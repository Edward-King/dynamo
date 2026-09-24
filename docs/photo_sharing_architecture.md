# PhotoShare Architecture

**Date:** 2026-08-19  
**Stack:** Python 3, FastAPI, Uvicorn, SQLite, Pillow, and a POSIX-compatible filesystem

PhotoShare is a read-oriented photo-library service. It indexes a directory tree into SQLite, presents physical and virtual portfolios through a versioned HTTP API, streams original image files, generates cached thumbnails, and can optionally expose static file mounts for simple clients. The filesystem remains the authoritative library input; SQLite is a derived metadata and query cache.

## Executive Summary

PhotoShare separates image content from where that content appears in the library. An image record represents stable content identity and technical properties, while an image-location record represents a particular file placement in a portfolio. This lets the same bytes appear in more than one directory without duplicating content metadata or thumbnails.

A full or scoped rescan reads the configured photo root, interprets optional `meta.json` files, updates SQLite, records results, and writes a JSON Lines rescan log. API read paths use SQLite rather than walking the filesystem, so filesystem changes become visible after a rescan. Thumbnail creation reads the source file synchronously on a cache miss and stores a JPEG derivative beneath the configured cache root.

The service is configured from YAML with `PHOTOSHARE_` environment-variable overrides. It starts through an import-string application factory so Uvicorn can create an independent FastAPI application in each worker process. API requests require an API key except for the health endpoint; optional static mounts intentionally bypass this API authentication path.

---

# 1. System Architecture Overview

## 1.1 Layers and responsibilities

| Layer | Main modules | Responsibility |
|---|---|---|
| Process entry point | `__main__.py` | Parses `--config`, exports the absolute configuration path for worker processes, and launches Uvicorn with an import-string factory. |
| Configuration | `config/schema.py` | Validates YAML and nested `PHOTOSHARE_` overrides into a typed `Settings` object. |
| API | `api/main.py`, `api/routers/`, `api/models.py` | Creates FastAPI, installs optional CORS and static routes, exposes HTTP endpoints, and maps domain errors to JSON responses. |
| Authentication | `auth/` | Extracts a configured API-key header and validates a SHA-256 token hash in SQLite. |
| Application services | `services/` | Performs rescans, portfolio and image queries, thumbnail generation, searching, export, and viewer configuration. |
| Storage and cache | `storage/`, `cache/` | Safely resolves library paths, reads `meta.json`, stores metadata in SQLite, and maintains thumbnail files. |
| Identity | `identity/models.py` | Derives deterministic root, portfolio, and image identifiers. |
| URL construction | `utils/urls.py` | Produces shared dynamic and optional-static URLs from IDs, relative paths, and configured thumbnail sizes. |

## 1.2 Request and indexing flow

```text
photo root + meta.json
        |
        | MetadataService.rescan()
        v
FilesystemAdapter ----> identity functions ----> SQLite metadata cache
        |                                             |
        |                                             v
        +--------------------------------------> rescan history + JSONL log

HTTP client --> FastAPI router --> API-key dependency --> service --> SQLite/filesystem
                                                    |                |
                                                    |                +--> original file or thumbnail cache
                                                    +--> response models and shared URL helpers
```

The database is an index and query cache, not a replacement for the photo tree. `PortfolioService`, `ImageService`, `SearchService`, and `ExportService` query SQLite. They do not discover newly created files while answering a request. Run a rescan after filesystem changes, or enable startup rescanning for deployments where that cost is appropriate.

## 1.3 Application construction and workers

The normal process invocation is:

```bash
python -m photoshare --config /etc/photoshare/config.prod.yaml
```

The entry point loads and validates the file, sets `PHOTOSHARE_CONFIG_PATH` to its absolute path, and calls Uvicorn with `photoshare.api.main:app_factory` and `factory=True`. `app_factory()` reloads the settings from that environment variable and returns `create_app(settings)`.

The import-string, zero-argument factory is required for Uvicorn worker mode: a worker process imports the factory and constructs its own application rather than attempting to receive an already-created Python object. Each worker therefore has its own `Settings`, SQLite connection, service instances, and FastAPI lifespan. If `rescan.on_startup` is true, every worker performs its own startup rescan. SQLite WAL mode and the configured busy timeout support concurrent readers and writers, but operators should account for this duplicate startup work when choosing the worker count.

`create_app()` creates the application state during its lifespan:

1. create the cache-root installation record when needed;
2. open the SQLite cache and apply connection pragmas;
3. construct filesystem, metadata, thumbnail, search, export, portfolio, and image services;
4. run a full rescan when `rescan.on_startup` is enabled;
5. close the database connection during shutdown.

## 1.4 URL families

`utils/urls.py` is the one place that defines URL patterns used in service response models:

| Purpose | Pattern |
|---|---|
| Dynamic full image | `/api/v2/images/{img_uuid}/full` |
| Dynamic thumbnail | `/api/v2/images/{img_uuid}/thumb?width={width}&height={height}` |
| Optional static full image | `/static-photos/{logical_relative_path}` |
| Optional static thumbnail | `/static-thumbs-{width}x{height}/{shard}/{img_uuid}.jpg` |

The static-thumbnail helper returns `null` when the requested dimensions are not in `cache.thumbnail_sizes`. This prevents API response models from advertising a mount that the application does not create.

---

# 2. Filesystem Design

## 2.1 Library root and physical portfolios

`storage.base_root` identifies the root of the source library. The root itself is a portfolio, and each non-hidden directory beneath it is a physical portfolio. Directory names and image file names remain filesystem-controlled; PhotoShare does not rename source files or write source-library metadata.

Only files whose suffix is in `storage.allowed_extensions` are indexed. The default list is:

```yaml
.jpg, .jpeg, .png, .gif, .webp, .heic
```

The adapter skips dot-prefixed directory entries and dot-prefixed files. It treats a directory whose name ends in an allowed image suffix as a directory, not as an image. Physical listing uses the adapter's current name-key sort; API listings of physical images use SQLite `COLLATE NOCASE` ordering.

## 2.2 Safe path resolution and symlinks

All logical paths are resolved through `FilesystemAdapter`. A supplied path may not begin with `/` or `\`, and may not contain a literal `..` component. The resolved path must remain under the resolved `base_root`; otherwise the operation raises `PathOutsideRootError`.

Directory and image symlinks are supported only when their resolved targets remain under the library root. During directory and image enumeration, symlinks that resolve outside the root are silently omitted rather than indexed. A direct request to resolve such a logical path is rejected as a path-outside-root error.

Recursive rescan has optional real-directory cycle detection. With `rescan.cycle_detection: true`, `MetadataService` tracks real paths visited in the traversal and records `SYMLINK_CYCLE_DETECTED` before skipping a repeated real directory. This also avoids indexing the same in-root directory twice through aliases. Disabling the setting removes that safeguard and can permit unbounded recursion through a cyclic symlink graph.

A symlinked image must retain an allowed image suffix in its visible directory entry. For example, `Japan_Tokyo_IMG_0007.heic -> ../shared/IMG_0007.heic` is eligible for indexing, while a symlink called `Japan_Tokyo_IMG_0007` is not.

## 2.3 Cache-root layout

`cache.cache_root` is separate from `storage.base_root`. Settings validation rejects a cache root equal to, or nested inside, the source root. The cache root contains service-owned state:

```text
<cache_root>/
├── installation.json
├── metadata.db
└── thumbnails/
    └── {width}x{height}/
        └── {shard}/
            └── {image_uuid}.jpg
```

`installation.json` stores the installation root ID and schema value. The current initializer creates schema value `2`. Rescan history logs are configured independently by `logging.rescan_log_path`; they are not implicitly placed under the cache root.

To rebuild derived state, stop the service, remove or replace the cache-root state that should be rebuilt, and run the service with a rescan. Preserve the source library. Treat `installation.json` deliberately: keeping it preserves the root namespace used for deterministic IDs, while replacing it creates a new namespace.

## 2.4 `meta.json` portfolio metadata

A directory may contain `meta.json`. The scanner parses it while indexing that directory; PhotoShare does not provide an API that writes or edits this file.

```json
{
  "portfolio": "Japan",
  "description": "Street photographs and travel images.",
  "tags": ["travel", "japan"],
  "icon_dir": "cover.jpg",
  "virtual": false
}
```

| Field | Meaning |
|---|---|
| `portfolio` | Optional display name. The directory name is used when absent. |
| `description` | Optional portfolio description. |
| `tags` | List of string tags stored in `portfolio_tags` and available to portfolio tag search. |
| `icon_dir` | The single field controlling the portfolio icon. A value containing a path separator is resolved relative to the library root; a bare filename is resolved relative to the current portfolio directory. There is no separate `icon` field. |
| `virtual` | Enables virtual-membership behavior described in Section 2.5. |
| `images` | Virtual-portfolio membership definitions when `virtual` is true. |

Malformed JSON or invalid top-level tags produces a rescan error for that directory and the directory is otherwise scanned without usable metadata.

### Icon selection

Icon resolution follows this order:

1. If `icon_dir` is present and contains a path separator, treat it as a path relative to the library root.
2. If `icon_dir` is present as a bare filename with no path separator, resolve it relative to the current portfolio directory.
3. If `icon_dir` is absent, or the resolved candidate cannot be converted to an indexed image, fall back to the first physical image in case-insensitive natural-name order.

The resolved candidate is converted to its deterministic image ID and stored as `icon_image_id` only when that image is already indexed. An icon target in another portfolio can remain unavailable during an initial traversal if its target has not yet been indexed; a later rescan can fill the reference. A virtual portfolio has no physical-image fallback icon; it can still resolve `icon_dir` explicitly.

## 2.5 Virtual portfolios

A virtual portfolio is a real directory with `virtual: true` in `meta.json`. Its physical image files are not listed as direct portfolio images. Instead, its `images` array selects already-indexed image content by relative path:

```json
{
  "portfolio": "Highlights",
  "virtual": true,
  "images": [
    {
      "rel_path": "Japan/Tokyo/IMG_0007.heic",
      "alternate_name": "Night crossing",
      "tags": ["night", "street"]
    }
  ]
}
```

Each valid entry must name `rel_path`. `alternate_name` and the entry order are retained in `virtual_membership` and affect virtual-list output and navigation. The membership table has one row per `(portfolio, image)` pair, so repeated references to the same content coalesce.

Virtual membership resolution requires the referenced content to have been indexed already. A target encountered later in the same traversal can generate a `not yet indexed` rescan error and become available on a subsequent rescan. The entry-level `tags` value is parsed and validated but is not currently persisted to `image_tags`; it does not make image-tag search work.

## 2.6 Source-image behavior

The source tree is read-only from PhotoShare's perspective. The application reads image files to fingerprint them, probe metadata, stream originals, and build thumbnails. It does not create `meta.json`, mutate image bytes, or maintain sidecar write-back files.

Pillow performs image probing and thumbnail conversion. The configured default extensions include HEIC, but successful probing depends on the installed Pillow build having a decoder for the particular image format. There is no ImageMagick, libvips, RAW, or TIFF processing path in this codebase.

---

# 3. Storage Abstraction Layer

## 3.1 Filesystem adapter

`storage/filesystem_adapter.py` supplies the concrete local-filesystem adapter used by the service. It centralizes containment checks, relative-path conversion, directory and image enumeration, source-file opening, and root switching. It keeps filesystem assumptions out of API routers and services.

The adapter exposes logical relative paths rather than accepting untrusted absolute paths. Service code can therefore request a known image location without duplicating traversal validation. The current implementation is a local `pathlib`/`os.scandir` adapter, not a runtime-selectable cloud-storage backend. Adding another backend requires a compatible interface for resolution, discovery, file access, and containment semantics.

The concrete adapter operations form the effective storage contract:

| Operation | Result and use |
|---|---|
| `set_root(base_root)` | Validates and changes the in-memory root used for future resolution. |
| `resolve(rel_path)` | Validates logical syntax and returns an in-root resolved `Path`. |
| `list_dirs(rel_path)` | Returns direct, visible child-directory `DirInfo` values with directory and direct-image counts. |
| `list_images(rel_path)` | Returns direct, visible allowed-image `ImgInfo` values. |
| `open_image(rel_path)` | Opens the validated source file in binary mode. |
| `resolve_symlink(rel_path)` | Returns a symlink target representation when applicable. |
| `compute_content_hash(rel_path)` | Calculates the first-4,096-bytes-plus-size fingerprint used by image identity. |

`DirInfo` includes name, relative path, symlink state, direct child-directory count, and direct image count. `ImgInfo` includes name, relative path, symlink state, file size, modification time, and MIME-type information used by indexing.

## 3.2 Content identity and placement identity

PhotoShare uses deterministic UUID version 5 values under an installation-specific root namespace.

| Entity | Derivation | Stability |
|---|---|---|
| Root ID | UUID version 4, created once in `installation.json` | Stable while the installation file is retained. |
| Portfolio ID | UUID5(root ID, normalized relative directory path) | Stable for the same root namespace and logical directory path. |
| Image ID | UUID5(root ID, fast content fingerprint) | Stable for the same root namespace and fingerprint. |

The image fingerprint is SHA-256 over the first 4,096 bytes of the file followed by its byte size. It is a fast indexing fingerprint, not a full-file cryptographic proof of byte-for-byte equality. A file change that leaves that prefix and size unchanged is not distinguished by this algorithm.

Content identity is deliberately separate from placement. A single `images` row represents an image ID and its technical metadata; `image_locations` represents each file location in a physical portfolio. This prevents duplicate thumbnails and permits the same content to occur in multiple directories.

A move inside the same portfolio can be recognized by a changed location path for the same content. A move between portfolios is represented as removal from one placement and addition to another while retaining the content ID. A directory rename changes the logical relative directory path and therefore creates a different portfolio ID.

The identity registry stores one current normalized path per image ID. When the same content appears at several locations, the registry's direct lookup path is whichever placement was most recently recorded. Direct image lookup and streaming by image ID can therefore choose a placement arbitrarily among duplicates; portfolio-scoped listings preserve the requested placement.

## 3.3 SQLite schema

The database uses foreign keys, WAL journal mode, `synchronous=NORMAL`, and the configured `db.busy_timeout_ms`. The settings model also includes `db.wal_autocheckpoint_pages`; that value is declared but is not applied by the current database connection code.

| Table | Columns and purpose |
|---|---|
| `installation` | `id`, `root_id`, `schema_version`, `created_at`. Installation metadata table; runtime root identity is read from `installation.json`. |
| `portfolios` | `id`, unique `rel_path`, `name`, `description`, `virtual`, `icon_image_id`, `parent_id`, `is_symlink`, `created_at`, `updated_at`. Represents hierarchy and presentation metadata. |
| `images` | `id`, `content_hash`, `width`, `height`, `size_bytes`, `mime_type`, `taken_at`, `created_at`, `updated_at`. Content-level technical metadata. |
| `image_locations` | `img_uuid`, `port_uuid`, `rel_path`, `name`, `is_symlink`, `file_modified_at`, `created_at`, `updated_at`. Primary key: `(img_uuid, port_uuid, rel_path)`; represents each placement. |
| `portfolio_tags` | `port_uuid`, `tag`, keyed by `(port_uuid, tag)`. Portfolio tag index. |
| `image_tags` | `img_uuid`, `tag`, keyed by `(img_uuid, tag)`. Image tag index; the current rescan path does not populate it. |
| `virtual_membership` | `port_uuid`, `img_uuid`, `alternate_name`, `sort_order`, keyed by `(port_uuid, img_uuid)`. Ordered virtual-portfolio image membership. |
| `identity_registry` | `uuid`, `kind`, `normalized_rel_path`, nullable `content_hash`, `last_seen_at`, nullable `tombstoned_at`. Deterministic identity lifecycle index. |
| `rescan_history` | timestamps, optional scope ID, recursive flag, added/moved/removed portfolio and image counts, `errors_json`, and `duration_ms`. Full and scoped rescan record. |
| `api_keys` | `id`, unique `key_hash`, `label`, `issued_at`, nullable `expires_at`, nullable `revoked_at`. API-key principals and credential state. |

Foreign keys cascade from portfolios and images to dependent placement, tag, and membership rows. `icon_image_id` is set to null if its image is deleted. The metadata repository currently sets the persisted `is_symlink` value for portfolio rows to `0`; directory symlink discovery is governed by filesystem traversal rather than a fully represented portfolio-symlink flag.

## 3.4 Rescan transactions and results

`MetadataService.rescan()` may process the complete root or a portfolio ID scope. It collects counts for directories, images added, images moved, images removed, virtual portfolio work, errors, and timing. It finalizes the result in `rescan_history` and appends structured JSON Lines to the configured rescan log path.

The scanner creates or updates portfolio, image, location, tag, membership, and identity records; removes stale locations; tombstones content identity when no locations remain; and preserves content-level rows while a remaining location exists. Direct image count is the number of stored physical locations plus virtual-membership rows for that portfolio. Recursive total count sums these values throughout the portfolio subtree.

## 3.5 Thumbnail storage

`ThumbnailService` stores one JPEG per content ID, width, and height. The shard is derived from the image ID and is also used by `utils.urls.py` for static-thumbnail URLs. Thumbnail generation:

1. validates that the requested size is configured or otherwise permitted by the caller;
2. reads the selected source image;
3. opens it with Pillow and converts it to RGB;
4. applies Pillow's bounded `thumbnail((width, height))` operation, preserving aspect ratio;
5. writes a JPEG at quality 85 into the shard path.

A `320x240` request is a bounding box, not a promise of a 320-by-240 cropped or padded output. The cache cleanup operation removes thumbnail files whose image IDs no longer have any content record.

---

# 4. Application Services

## 4.1 Portfolio service

`PortfolioService` queries SQLite for the root, a portfolio, its children, and images within a portfolio. It returns physical child portfolios based on their parent relationship and uses database ordering for physical images. It provides the `is_virtual()` decision used by adjacent-image navigation.

Portfolio responses contain ID, display name, relative path, virtual flag, description, tags, dynamic icon thumbnail URL, optional static icon thumbnail URL, direct count, recursive total count, and child count. The dynamic icon URL uses the dynamic thumbnail default of `320x240`; the static icon URL uses `cache.default_static_icon_size`.

## 4.2 Image service

`ImageService` returns image metadata, full-image files, and thumbnails. It resolves source data from indexed database rows and the filesystem adapter. The service does not add missing files to SQLite while responding to a read request.

An image response includes:

| Field | Meaning |
|---|---|
| `id`, `name`, `rel_path` | Content ID and selected placement information. |
| `width`, `height`, `size_bytes`, `mime_type`, `file_modified_at`, `taken_at` | Indexed technical and time metadata. |
| `tags` | Image tags from the image-tag index; scanning `meta.json` does not populate these today. |
| `full_url`, `thumbnail_url` | Dynamic endpoints. The thumbnail URL is `320x240`. |
| `full_static_url`, `thumbnail_static_url`, `thumbnail_static_urls` | Optional-static URL forms derived by `utils/urls.py`. |
| `alternate_name`, `sort_order` | Virtual-membership presentation fields when applicable. |

The singular static thumbnail uses `cache.default_static_thumbnail_size`. The static thumbnail map includes every configured thumbnail size. A response can contain static URLs even when static serving is disabled; those URLs only resolve when the corresponding optional mounts are active and their files exist.

## 4.3 Metadata service

`MetadataService` orchestrates traversal, `meta.json` parsing, deterministic identity, database upserts, stale-record cleanup, cycle detection, virtual membership, rescan history, and JSON Lines logging. It is the only normal route by which filesystem state becomes indexed metadata.

A malformed `meta.json`, an icon or virtual-member target that is not indexed, a thumbnail probe failure, or a traversal problem is recorded in rescan output rather than silently represented as a successful item. An API-scoped rescan validates the target portfolio before dispatching service work.

## 4.4 Thumbnail, viewer, search, and export services

`ThumbnailService` produces cache files used by the dynamic thumbnail endpoint and by preheating. Dynamic thumbnail dimensions are request query parameters and are limited to values between 16 and 4,096 in each dimension. The endpoint defaults to `320x240`.

`ViewerConfigService` exposes configured thumbnail sizes and the tile threshold dimensions. The tile threshold is configuration for clients; the server does not implement a tile or deep-zoom endpoint.

`SearchService` performs exact, case-insensitive single-tag searches. Portfolio tag search uses `portfolio_tags`. Image tag search uses `image_tags`, which is empty after a normal filesystem rescan unless another process has written image-tag rows.

`ExportService` builds the data used by the management CLI's `dump-tree` command. It reads the indexed database without first rescanning and emits a JSON hierarchy with portfolio metadata, image placement information, content hashes, and static URL fields.

---

# 5. API Layer

## 5.1 API conventions

The primary API prefix is `/api/v2`. Request and response models are defined in `api/models.py`; routers remain synchronous functions and FastAPI runs them in its standard synchronous execution path.

All API routes in the portfolio, image, search, and administration routers require a valid API key. `GET /api/v2/admin/health` is the sole unauthenticated application endpoint. The default FastAPI OpenAPI document is available at `/openapi.json`, with the normal interactive documentation routes provided by FastAPI unless disabled by an embedding deployment.

## 5.2 Response shapes

A portfolio object has the following conceptual shape:

```json
{
  "id": "portfolio UUID",
  "name": "Tokyo",
  "rel_path": "Japan/Tokyo",
  "is_virtual": false,
  "description": "...",
  "tags": ["travel"],
  "icon_thumbnail_url": "/api/v2/images/.../thumb?width=320&height=240",
  "icon_thumbnail_static_url": "/static-thumbs-320x240/...jpg",
  "direct_image_count": 12,
  "total_image_count": 48,
  "child_count": 3
}
```

An image object includes placement, content metadata, dynamic URLs, static URL variants, and virtual-membership presentation values as described in Section 4.2. Because content can have multiple locations, an unscoped image-by-ID response does not promise which placement supplies its `name` or `rel_path`.

## 5.3 Read endpoints

| Method and path | Parameters | Result |
|---|---|---|
| `GET /api/v2/admin/health` | none | `{status, root_id, schema_version}` from installation state. Mounted without the API-key dependency. |
| `GET /api/v2/portfolios/root` | none | Root portfolio. |
| `GET /api/v2/portfolios/{port_uuid}` | portfolio UUID | One portfolio. |
| `GET /api/v2/portfolios/{port_uuid}/children` | portfolio UUID | Direct child portfolios. |
| `GET /api/v2/portfolios/{port_uuid}/images` | `page` default 1; `page_size` default 50, maximum 200 | Paginated image items for the physical or virtual portfolio. |
| `GET /api/v2/portfolios/{port_uuid}/images/{img_uuid}/adjacent` | portfolio and image UUIDs | `{previous, next}` using physical name order or virtual `sort_order`. |
| `GET /api/v2/images/{img_uuid}` | image UUID | Image metadata. |
| `GET /api/v2/images/{img_uuid}/full` | image UUID | Streams the selected original file. |
| `GET /api/v2/images/{img_uuid}/thumb` | `width` default 320; `height` default 240 | Returns or generates a JPEG thumbnail. |
| `GET /api/v2/viewer/config` | none | Configured thumbnail-size pairs and tile threshold width and height. |
| `GET /api/v2/search/portfolios` | required non-empty `tag` | Exact case-insensitive portfolio-tag matches. |
| `GET /api/v2/search/images` | required non-empty `tag` | Exact case-insensitive image-tag matches. |

The adjacent endpoint rejects a nonexistent portfolio, even before image adjacency is evaluated. Physical adjacency follows the database name order; virtual adjacency follows membership `sort_order`.

## 5.4 Administrative endpoints

| Method and path | Request body | Result |
|---|---|---|
| `POST /api/v2/admin/rescan` | `port_uuid` nullable; `recursive` | Full or scoped rescan result. |
| `POST /api/v2/admin/preheat-thumbnails` | `port_uuid` nullable; `width` default 320; `height` default 240; `recursive` | Creates requested thumbnails and returns counts. |
| `POST /api/v2/admin/cleanup-thumbnails` | none | Removes stale thumbnail-cache entries. |
| `POST /api/v2/admin/root` | a filesystem root path | Validates a directory, switches the in-memory filesystem root, and runs a full rescan. |

The root-change endpoint does not rewrite the YAML configuration file. It retains the installation root namespace, so content IDs remain derived from that namespace and their fingerprints, while portfolio IDs are based on relative directory paths. If static mounts are enabled, they were bound when the app was created; restart the application after a root change to rebind static serving.

There is no separate administrator scope model. Any non-expired, non-revoked valid API key can invoke these endpoints, so deployments should protect key issuance and restrict network access accordingly.

## 5.5 HTTP caching and errors

Full-image responses use an ETag derived from the content fingerprint and a cache-control value of `public, max-age=604800, immutable`. Thumbnail ETags include the fingerprint and requested dimensions. Both response types expose the stored file-modification value as `Last-Modified`; it is an ISO-format value from the metadata store, not an HTTP-date formatter. A matching conditional request receives a bare `304` response.

The API converts domain errors into a common error object with `code`, `message`, and optional `detail`:

| Condition | Status | Code |
|---|---:|---|
| Missing API key | 401 | `AUTH_MISSING` |
| Invalid, expired, or revoked API key | 401 | `AUTH_INVALID` |
| Portfolio not found | 404 | `PORTFOLIO_NOT_FOUND` |
| Image not found | 404 | `IMAGE_NOT_FOUND` |
| Malformed path | 400 | `MALFORMED_PATH` |
| Path resolves outside library root | 403 | `PATH_OUTSIDE_ROOT` |
| Invalid root | 400 | `INVALID_ROOT_PATH` |
| Invalid thumbnail size raised by service | 400 | `INVALID_THUMBNAIL_SIZE` |
| Filesystem permission failure during a scan | 500 | `FILESYSTEM_PERMISSION_ERROR` |
| Pydantic request/query validation failure | 422 | `VALIDATION_ERROR` |
| Any other HTTP exception | its HTTP status | `HTTP_ERROR` |

There is no dedicated `INTERNAL_ERROR` code. An exception that is not one of the handled domain errors above falls through to FastAPI's default error handling rather than the application's JSON error envelope. For query dimensions, FastAPI validation normally catches values outside the endpoint bounds before the service is called, yielding the `VALIDATION_ERROR` form.

## 5.6 Optional static mounts

When `enable_static_file_serving` is true, application construction adds these unauthenticated mounts:

```text
/static-photos                 -> storage.base_root
/static-thumbs-{width}x{height} -> cache_root/thumbnails/{width}x{height}
```

One thumbnail mount is created for every configured `cache.thumbnail_sizes` pair. Static full-image URLs use the logical relative path, and static thumbnail URLs use the cache shard and image ID. Static delivery neither generates a missing thumbnail nor applies API-key checks, API cache headers, or route-level authorization. Disable static serving when originals and derivatives must remain behind the authenticated API.

---

# 6. Security Considerations

## 6.1 Filesystem boundary

The primary local-security boundary is the configured resolved library root. Logical paths reject absolute and parent-traversal forms, and resolved targets must remain inside that root. External symlinks are omitted during enumeration and rejected when directly resolved. The same boundary must be preserved by any future storage implementation.

The source tree and cache root should be owned and mounted so the service has only the access it needs. The supplied container mounts the source photo directory read-only and uses writable volumes for cache and logs.

## 6.2 API-key authentication

The active authentication implementation is SQLite-backed header API keys. The default header is `X-API-Key`, and `auth.header_name` makes it configurable. Raw tokens are generated with `secrets.token_urlsafe`, displayed only when created, and stored only as SHA-256 hashes. A credential is rejected when its principal is unknown, revoked, or expired.

The settings model declares `auth.scheme` with `api_key` and `jwt` values, but runtime wiring currently constructs the header API-key scheme only. JWT verification is not implemented. Similarly, `auth.key_expiry_days` is declared but API-key expiry is supplied by the management command's `--expires-days` option rather than automatically applied from that setting.

## 6.3 Public surfaces and transport

The health endpoint and any optional static mounts are intentionally unauthenticated. The dynamic API routes are key-protected, including the viewer configuration endpoint. Static mounts expose files directly and should be placed only behind an appropriate network boundary when used.

CORS middleware is installed only when `server.cors_allowed_origins` is non-empty. When installed, it allows credentials, all methods, and all headers for the configured origins. The application does not terminate TLS, issue HTTP Strict Transport Security headers, or provide an edge proxy configuration; deploy it behind a TLS-capable reverse proxy or load balancer when serving untrusted networks.

The settings model includes rate-limit fields, but the current application does not install a rate-limiting middleware or proxy policy. Enforce request limits at an ingress layer until an application mechanism is added.

## 6.4 Metadata and observability

`meta.json` is input from the source library. Anyone able to change it can affect portfolio names, descriptions, tags, virtual membership, and icon selection at the next rescan. Limit write access to trusted library maintainers.

Rescan history includes operational error details and the configured JSON Lines log path receives rescan records. Treat these logs and the SQLite cache as operational data: restrict access, rotate or retain logs according to local policy, and back up only what is appropriate for the deployment.

---

# 7. Extensibility

## 7.1 Storage backends

The current code uses one concrete local-filesystem adapter. Its clean separation from routers and indexing services makes it feasible to introduce a backend interface for object storage, network filesystems, or a media catalog, but no `STORAGE_BACKEND` setting or cloud backend is supplied today. A new backend must define safe relative-path behavior, directory discovery, file opening, root containment, modification metadata, and symlink or alias semantics.

## 7.2 Identity and metadata evolution

The content/placement split accommodates duplicate source files and future placement metadata without multiplying content rows. New image attributes belong with `images` when they describe bytes, and with `image_locations` when they describe a file occurrence. New virtual-membership presentation data belongs with `virtual_membership`.

Image tags have a schema table and search path but no scanner writer. Adding durable image tagging requires an explicit source of truth and code that populates `image_tags`; persisting virtual-entry tags is one possible design, but it should define whether tags apply to the content globally or only in one virtual portfolio.

## 7.3 Media processing

Thumbnail generation is centralized in `ThumbnailService`, which makes it the appropriate integration point for alternative encoders, color management, orientation policy, fixed-canvas crops, background processing, or additional derivatives. Any change must preserve cache keying by content ID and requested rendition parameters so static URL construction and cleanup remain correct.

## 7.4 API and operational extensions

The API layer has separate routers, response models, dependencies, and exception handlers, so new endpoints should be added through the corresponding router and service rather than embedding database logic in a handler. Potential extensions include privileged admin scopes, asynchronous rescan jobs, durable job status, webhooks, signed static delivery, and a real image-tagging workflow. These are extension directions, not active capabilities.

---

# 8. Technology Reference and Deployment

## 8.1 Runtime dependencies

| Component | Role |
|---|---|
| Python | Application runtime; the container image uses Python 3.12 slim. |
| FastAPI | HTTP routing, dependency injection, request validation, OpenAPI, and response modeling. |
| Uvicorn | ASGI server and multi-worker process manager. |
| Pydantic and pydantic-settings | Typed configuration and API models. |
| PyYAML | YAML configuration loading. |
| SQLite | Metadata index, API-key store, rescan history, and search queries. |
| Pillow | Image metadata probing and JPEG thumbnail production. |
| natsort | Case-insensitive natural ordering used for automatic icon fallback. |
| python-multipart | Multipart support required by the runtime dependency set. |

The runtime requirements do not include ImageMagick, libvips, a filesystem watcher, a JWT library, a reverse proxy, or a system service manager.

## 8.2 Configuration reference

Configuration is a YAML file loaded through `--config`. Environment variables prefixed with `PHOTOSHARE_` override nested values using double underscores, for example:

```bash
PHOTOSHARE_SERVER__WORKERS=2
PHOTOSHARE_CACHE__ROOT=/var/lib/photoshare/cache
```

Unknown configuration fields are rejected. The principal settings tree is:

| Section | Important settings and defaults | Runtime notes |
|---|---|---|
| Top level | `enable_static_file_serving: false` | Controls creation of unauthenticated static mounts. |
| `server` | `host: 0.0.0.0`, `port: 8000`, `workers: 4`, `cors_allowed_origins: []` | Workers are passed to Uvicorn; CORS middleware is absent when the origin list is empty. |
| `storage` | `base_root: /photos_root`; allowed extensions listed in Section 2.1 | The library input. |
| `cache` | `cache_root: /var/lib/photoshare/cache`; sizes `[[320,240],[800,600]]`; default static thumbnail and icon sizes `[320,240]`; tile threshold `4000x3000` | Each static default must be one of the configured thumbnail sizes. |
| `db` | `busy_timeout_ms: 5000`; `wal_autocheckpoint_pages: 1000` | Busy timeout is applied. The auto-checkpoint value is not currently consumed. |
| `auth` | `scheme: api_key`; `header_name: X-API-Key`; `key_expiry_days: 365` | Header API keys are active. Scheme selection and default expiry are not wired into runtime behavior. |
| `rate_limiting` | `requests_per_minute: 100`; `burst_size: 10` | Declared configuration; no active enforcement component. |
| `rescan` | `on_startup: true`; `watch_filesystem: false`; `cycle_detection: true` | Startup and cycle settings are used. There is no filesystem-watch implementation. |
| `logging` | `level: INFO`; `rescan_log_path: logs/rescan_history.jsonl` | Rescan JSON Lines destination. |

A development configuration uses a loopback host, one worker, `./sample_photos`, `./dev_cache`, and a local log path. The production configuration uses the container-oriented source, cache, and log locations with the public bind address and a four-worker default.

## 8.3 Container packaging

The Dockerfile:

1. starts from `python:3.12-slim`;
2. installs the runtime requirements;
3. copies the package and configuration files into the image;
4. creates a non-root `photoshare` user with UID 1000;
5. creates and assigns `/photos_root`, `/var/lib/photoshare/cache`, and `/var/log/photoshare`;
6. exposes port 8000; and
7. uses `python -m photoshare` as the entry point.

The supplied Compose configuration publishes port 8000, mounts `./sample_photos` at `/photos_root` read-only, uses named cache and log volumes, sets `PHOTOSHARE_SERVER__WORKERS=2`, and uses `restart: unless-stopped`.

A representative production invocation is:

```bash
docker compose up -d
```

Provide a production YAML file through the command or container configuration, mount the intended photo tree read-only, persist the cache and log locations, and place a TLS-capable ingress in front of the exposed service when needed.

## 8.4 Management CLI

The management commands use the same configuration model as the service:

```bash
python -m photoshare.cli.manage --config /etc/photoshare/config.prod.yaml create-api-key --label reader
python -m photoshare.cli.manage --config /etc/photoshare/config.prod.yaml create-api-key --label temporary --expires-days 7
python -m photoshare.cli.manage --config /etc/photoshare/config.prod.yaml revoke-api-key --principal-id <uuid>
python -m photoshare.cli.manage --config /etc/photoshare/config.prod.yaml dump-tree --output library.json
```

`create-api-key` prints a raw token once and stores only its hash. `revoke-api-key` is safe to repeat for the same principal. `dump-tree` exports current indexed metadata and does not trigger a rescan; use the optional root argument to export a particular indexed subtree.

---

# Appendix A. Worked Example

Consider this source library:

```text
/photos_root/
├── Japan/
│   ├── meta.json
│   ├── Tokyo/
│   │   ├── IMG_0007.heic
│   │   └── Japan_Tokyo_IMG_0007.heic -> ../../shared/IMG_0007.heic
│   └── Kyoto/
│       └── IMG_0102.jpg
├── Highlights/
│   └── meta.json
└── shared/
    └── IMG_0007.heic
```

`Japan/meta.json` can set a display name, tags, description, and a cover image. `Highlights/meta.json` can declare a virtual portfolio:

```json
{
  "portfolio": "Highlights",
  "virtual": true,
  "images": [
    {
      "rel_path": "Japan/Tokyo/IMG_0007.heic",
      "alternate_name": "Night crossing"
    },
    {
      "rel_path": "Japan/Kyoto/IMG_0102.jpg",
      "alternate_name": "Temple courtyard"
    }
  ]
}
```

During a full rescan, PhotoShare assigns a portfolio ID to `Japan/Tokyo`, calculates fast fingerprints for the images, and inserts content and placement records. If `IMG_0007.heic`, the in-root symlink target, and another library file share the same fingerprint, they share one image ID but retain separate `image_locations` rows. The virtual portfolio stores ordered membership for the content IDs after referenced images are available in the index.

A client can retrieve the root and descend through physical portfolios:

```text
GET /api/v2/portfolios/root
GET /api/v2/portfolios/{japan_uuid}/children
GET /api/v2/portfolios/{tokyo_uuid}/images?page=1&page_size=50
```

It can request the original or a generated derivative using the content ID:

```text
GET /api/v2/images/{img_uuid}/full
GET /api/v2/images/{img_uuid}/thumb?width=800&height=600
```

When static serving is enabled and the derivative exists, the same image can be addressed through the static paths emitted by the response model. Otherwise the authenticated dynamic endpoints remain the authoritative delivery path.
