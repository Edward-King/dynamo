# How It Works — Developer Guide

This is an endpoint-by-endpoint behavioral reference for PhotoShare. It describes the current request paths, response shapes, validation behavior, cache behavior, and filesystem reconciliation rules.

PhotoShare treats the filesystem as the source library and SQLite as derived metadata. Image content identity is separate from filesystem placement: `images` stores one row per content ID, while `image_locations` records each physical occurrence in a portfolio. See `photo_sharing_architecture.md`, Sections 1–8 and Appendix A, for the architectural overview.

## 1. Startup sequence

1. The required `--config` file is loaded and validated. `PHOTOSHARE_` environment variables with `__` nesting override YAML values.
2. `cache.cache_root` is created as needed. `installation.json` is created if absent and stores the persistent UUID4 `root_id` and schema version.
3. SQLite opens at `cache_root/metadata.db`, where the schema is applied idempotently.
4. The app constructs storage, repository, service, authentication, and thumbnail components.
5. When `rescan.on_startup` is true, `MetadataService.rescan(port_uuid=None, recursive=True)` runs before the application accepts traffic.
6. Protected portfolio, image, search, and administration routers are mounted below `/api/v2`; the health router is mounted at `/api/v2/admin/health` without the authentication dependency.

A configuration whose cache root equals or falls under the photo root is rejected. The server entry point passes the validated config path to Uvicorn worker processes through `PHOTOSHARE_CONFIG_PATH`; each worker constructs its own application.

## 2. Authentication and the health endpoint

`GET /api/v2/admin/health` is the sole route without an API-key dependency. It returns:

```json
{
  "status": "ok",
  "root_id": "<uuid>",
  "schema_version": 2
}
```

Every other API route uses the configured key header, `X-API-Key` by default. Authentication outcomes are:

| Condition | HTTP status | Error code |
|---|---:|---|
| Header absent | 401 | `AUTH_MISSING` |
| Unknown, expired, or revoked key | 401 | `AUTH_INVALID` |

The SQLite credential store retains only a SHA-256 key hash. The management CLI returns a raw key when it issues one, and the raw value is not stored.

## 3. Portfolio endpoints

### `GET /api/v2/portfolios/root`

The repository selects a row with `parent_id IS NULL` and the portfolio service builds a `PortfolioOut`. Once a rescan has created the root portfolio, the endpoint returns `200 OK`. If no rescan has established a root row, it returns `404 PORTFOLIO_NOT_FOUND`.

### `GET /api/v2/portfolios/{port_uuid}`

The repository performs an exact primary-key lookup. A missing UUID returns `404 PORTFOLIO_NOT_FOUND`; a found row returns `200 OK` with `PortfolioOut`.

### `GET /api/v2/portfolios/{port_uuid}/children`

A missing parent returns `404 PORTFOLIO_NOT_FOUND`, which distinguishes it from a valid portfolio that simply has no children. A valid empty portfolio returns `200 OK` with `[]`. Child rows are ordered by `name COLLATE NOCASE`.

### `PortfolioOut`

A portfolio response contains:

| Field | Meaning |
|---|---|
| `id`, `name`, `virtual`, `rel_path` | Portfolio identity and presentation. |
| `tags`, `description` | Values from the directory's `meta.json`. |
| `icon_image_id` | Resolved icon image ID or `null`. |
| `icon_thumbnail_url` | Authenticated dynamic thumbnail URL for the icon or `null`. |
| `icon_thumbnail_static_url` | Static thumbnail URL at `cache.default_static_icon_size`, or `null`. |
| `direct_image_count` | Direct physical placements plus virtual memberships for the portfolio. |
| `total_image_count` | The direct count throughout the portfolio's recursive subtree. |
| `children` | Direct child portfolio UUIDs. |

## 4. `GET /api/v2/portfolios/{port_uuid}/images`

This authenticated endpoint returns `PagedResult[ImgOut]`.

- A missing portfolio returns `404 PORTFOLIO_NOT_FOUND`.
- `page` defaults to `1` and must be at least `1`.
- `page_size` defaults to `50` and must be from `1` through `200`.
- Invalid pagination fields return `422 VALIDATION_ERROR`.
- `has_next` is true when `(page * page_size) < total`.

For a physical portfolio, the query joins `image_locations` to `images`, filters on `image_locations.port_uuid`, and sorts by placement name with `COLLATE NOCASE`. Content properties come from `images`; `name`, `rel_path`, `is_symlink`, and `file_modified_at` come from the requested placement.

For a virtual portfolio, the query joins `virtual_membership` to `images` and reads rows in `sort_order ASC`. The virtual portfolio's `meta.json.images` array establishes order: its first entry is stored with `sort_order: 0`. Reorder the array and rescan to change the display order. Virtual entries may have `alternate_name`; physical entries return `alternate_name: null` and `sort_order: null`.

The same content ID may have physical placements in multiple portfolios. Each scoped listing returns its own placement name and path while sharing the same image ID, content metadata, tags, and thumbnail cache.

The paginated shape is:

```json
{
  "items": ["ImgOut", "..."],
  "page": 1,
  "page_size": 50,
  "total": 2,
  "has_next": false
}
```

## 5. Image metadata, navigation, and search

### `GET /api/v2/images/{img_uuid}`

This direct image-ID lookup returns `200 OK` with `ImgOut` or `404 IMAGE_NOT_FOUND`. Because the request is not scoped to a portfolio, the repository selects one available physical placement for `name`, `rel_path`, and file timestamps when content has duplicates. `alternate_name` and `sort_order` are `null`.

### `GET /api/v2/portfolios/{port_uuid}/images/{img_uuid}/adjacent`

This endpoint returns:

```json
{"previous": "<uuid-or-null>", "next": "<uuid-or-null>"}
```

It applies the same physical-name or virtual-membership ordering used by the portfolio image listing. If the requested image is absent from that portfolio's ordered set, it returns `404 IMAGE_NOT_FOUND`. The route checks virtual status through the portfolio repository; an unknown portfolio therefore returns `404 PORTFOLIO_NOT_FOUND`.

### `GET /api/v2/viewer/config`

This returns the configured display hints:

```json
{
  "thumbnail_sizes": [[320, 240], [800, 600]],
  "tile_threshold_width": 4000,
  "tile_threshold_height": 3000
}
```

The values reflect `cache.thumbnail_sizes` and `cache.tile_threshold_px`.

### `GET /api/v2/search/portfolios?tag={tag}` and `GET /api/v2/search/images?tag={tag}`

Both endpoints require a non-empty `tag`; omitting it or passing an empty value returns `422 VALIDATION_ERROR`. Searches use exact, case-insensitive tag matches. The portfolio endpoint returns `list[PortfolioOut]`; the image endpoint returns `list[ImgOut]`.

## 6. `GET /api/v2/images/{img_uuid}/full`

The image service resolves the image UUID through `identity_registry` to a current normalized relative path, then resolves that path through the storage adapter.

- An absent or tombstoned registry entry returns `404 IMAGE_NOT_FOUND`.
- A registry path whose file no longer exists returns `404 IMAGE_NOT_FOUND`.
- A defensive containment failure returns `403 PATH_OUTSIDE_ROOT`.
- A successful request streams the original with `Cache-Control: public, max-age=604800, immutable`, an ETag containing the content hash, and a `Last-Modified` header from stored placement metadata.
- An `If-None-Match` value exactly equal to the quoted ETag returns `304 Not Modified` with no body.

For duplicate content, the identity registry holds a current path for the content ID. An unscoped full-image request can therefore stream one of its physical placements; portfolio-scoped listings retain the requested placement details.

## 7. `GET /api/v2/images/{img_uuid}/thumb`

This endpoint performs the same identity resolution as the full-image endpoint, then calls `ThumbnailService.get_or_create`.

- `width` defaults to `320`; `height` defaults to `240`.
- Each dimension must be in the inclusive range `16`–`4096`; invalid query values return `422 VALIDATION_ERROR` before thumbnail generation.
- A cache hit streams `cache_root/thumbnails/{shard}/{img_uuid}_{width}x{height}.jpg`.
- A cache miss generates a JPEG synchronously, fitting the source inside the requested bounding box, saves it at that location, then streams it.
- The response uses `Cache-Control: public, max-age=604800, immutable`, an ETag containing the content hash and requested dimensions, and the stored `Last-Modified` value.
- An exact matching `If-None-Match` returns `304 Not Modified`.

The service-level thumbnail API also validates dimensions and can raise `INVALID_THUMBNAIL_SIZE` with status 400 when called outside the route. HTTP callers of this route receive the route-level 422 validation response for invalid query values.

## 8. `POST /api/v2/admin/rescan`

This authenticated endpoint accepts:

```json
{
  "port_uuid": null,
  "recursive": true
}
```

`port_uuid` is optional; `null` starts at the library root. `recursive` defaults to `true`. A supplied UUID that is not a portfolio returns `404 PORTFOLIO_NOT_FOUND`.

The request calls `MetadataService.rescan()` synchronously and returns `200 OK` with:

```json
{
  "started_at": "<ISO8601>",
  "completed_at": "<ISO8601>",
  "portfolios_added": 0,
  "portfolios_moved": 0,
  "portfolios_removed": 0,
  "images_added": 0,
  "images_moved": 0,
  "images_removed": 0,
  "errors": [],
  "duration_ms": 0
}
```

The scanner records a rescan-history row and appends a JSON Lines record to `logging.rescan_log_path` as part of normal completion. A filesystem permission problem encountered during the scan is captured in the returned `errors` list; failures while persisting operational records can still abort the request.

## 9. Rescan internals: placement add, rename, and removal

The scanner walks the configured root through `FilesystemStorageAdapter`, excludes dot-prefixed names, enforces path containment, and optionally detects repeated resolved directories as symlink cycles.

1. For each visited directory, it computes `Port_UUID = uuid5(root_id, normalized_rel_path)` and upserts a `portfolios` row plus an `identity_registry` entry. The root uses the normalized path `.`.
2. It reads `meta.json`. Invalid JSON or invalid tag structures become non-fatal entries in `RescanResult.errors`; the directory is processed with default metadata.
3. For a physical image, it computes a content fingerprint and derives `Img_UUID = uuid5(root_id, content_hash)`.
4. It inserts or updates the `images` content row with content hash, dimensions, byte size, MIME type, capture time, and timestamps.
5. It inserts or updates an `image_locations` row with `(img_uuid, port_uuid, rel_path)`, name, symlink flag, and file modification time.
   - A placement at an already-recorded path is updated without an image counter change.
   - A new `(img_uuid, port_uuid)` placement is counted as `images_added`, including a byte-identical file in another portfolio.
   - If the same image/portfolio pair has an earlier placement path that has not been seen during this scan, the location is updated in place and `images_moved` increments. This is an in-portfolio rename.
   - If another occurrence of the same content is encountered in the same portfolio after a prior placement was already seen, an additional location row is inserted and counted as added.
6. A move to a different portfolio becomes one new placement (`images_added`) and one stale-placement removal (`images_removed`), rather than an `images_moved` event. The content ID remains the same.
7. After traversal, unvisited physical location rows within the scan scope are deleted and counted in `images_removed`. When no location rows remain for a content ID, its `images` row is deleted and the identity-registry entry is tombstoned.
8. Unvisited non-root portfolio rows in the scan scope are removed and their identity entries are tombstoned. A renamed directory derives a different path-based portfolio ID, so it is represented by removal and addition rather than a preserved portfolio move.

Virtual portfolios do not index direct images from their own directory. Their existing `virtual_membership` rows are replaced from `meta.json.images`. Each referenced path is fingerprinted to derive an image ID; entries that do not yet have an `images` row generate an error and are skipped for that pass. Valid members receive their array position as `sort_order`.

## 10. Administration maintenance endpoints

### `POST /api/v2/admin/preheat-thumbnails`

Request body fields are `port_uuid` (optional), `width` (default `320`), `height` (default `240`), and `recursive` (default `true`). Width and height are each constrained to `16`–`4096`; bad values return `422 VALIDATION_ERROR`. An unknown selected portfolio returns `404 PORTFOLIO_NOT_FOUND`.

The endpoint collects unique image IDs in the selected portfolio tree, resolves available files, and calls the same thumbnail service used by the dynamic thumbnail route. It returns `images_processed`, `thumbnails_created`, and `duration_ms`. `thumbnails_created` excludes cache hits.

### `POST /api/v2/admin/cleanup-thumbnails`

This body-less authenticated request builds the set of live IDs from `images` and deletes thumbnail files whose file-name ID is no longer live. It returns `thumbnails_removed` and `duration_ms`.

### `POST /api/v2/admin/root`

The request body is:

```json
{"path": "/new/photo/root"}
```

The path must already exist and be a directory. Otherwise it returns `400 INVALID_ROOT_PATH` before mutating the storage adapter. On success, the storage adapter and in-memory settings use the resolved root; a full rescan runs and the result is:

```json
{
  "base_root": "/new/photo/root",
  "rescan_triggered": true,
  "duration_ms": 0
}
```

The persistent `root_id` remains unchanged, and the YAML configuration file is not modified. If static serving is enabled, restart the application after a root change because static mount directories are bound during application construction.

## 11. Error response shape and taxonomy

Mapped errors use the common envelope:

```json
{
  "code": "IMAGE_NOT_FOUND",
  "message": "No image found for id <img-id>",
  "detail": null
}
```

`detail` contains FastAPI/Pydantic field errors for validation failures. The implemented mappings are:

| Condition | HTTP status | Code |
|---|---:|---|
| Missing portfolio | 404 | `PORTFOLIO_NOT_FOUND` |
| Missing image | 404 | `IMAGE_NOT_FOUND` |
| Path resolves outside the library root | 403 | `PATH_OUTSIDE_ROOT` |
| Malformed relative path | 400 | `MALFORMED_PATH` |
| Storage permission failure | 500 | `FILESYSTEM_PERMISSION_ERROR` |
| Invalid root request | 400 | `INVALID_ROOT_PATH` |
| Direct service thumbnail size failure | 400 | `INVALID_THUMBNAIL_SIZE` |
| Invalid request/query/body data | 422 | `VALIDATION_ERROR` |
| Other raised HTTP exception | its status | `HTTP_ERROR` |
| Missing key | 401 | `AUTH_MISSING` |
| Invalid, expired, or revoked key | 401 | `AUTH_INVALID` |

There is no implemented `UNAUTHORIZED`, `INVALID_PATH`, `INVALID_ROOT`, or `INTERNAL_ERROR` code in the current exception handlers.

## 12. Image URL fields and optional static delivery

Every `ImgOut` contains these URL fields:

| Field | Example | Authentication | Resolution behavior |
|---|---|---|---|
| `full_url` | `/api/v2/images/{id}/full` | Required | Dynamic route streams the original. |
| `thumbnail_url` | `/api/v2/images/{id}/thumb?width=320&height=240` | Required | Dynamic route creates the thumbnail if absent. |
| `full_static_url` | `/static-photos/{rel_path}` | None | Requires static serving to be enabled. |
| `thumbnail_static_url` | `/static-thumbs-320x240/{shard}/{id}_320x240.jpg` | None | Requires static serving and a pre-existing file. |
| `thumbnail_static_urls` | `{"320x240": "...", "800x600": "..."}` | None | One potential URL per configured size. |

`enable_static_file_serving` defaults to `false`. With the flag enabled, the app mounts `storage.base_root` at `/static-photos` and mounts `cache_root/thumbnails` once for each configured `cache.thumbnail_sizes` pair at `/static-thumbs-{width}x{height}`. Static paths do not authenticate callers, generate thumbnails, or supply dynamic route headers.

The singular thumbnail URL uses `cache.default_static_thumbnail_size`; a portfolio icon's optional static URL uses `cache.default_static_icon_size`. Each must be a member of `cache.thumbnail_sizes`, enforced during configuration validation. Static URL fields remain in API responses while static serving is disabled, but those paths return 404 because the mounts do not exist.

## Known Limitations

- Administration routes require a valid key but do not implement separate key scopes.
- Rescans and thumbnail preheating run synchronously in request handling.
- Static mounts do not follow a changed library root until the application restarts.
- PhotoShare does not retain undo history or soft-deleted filesystem metadata; rescans reflect the current filesystem and sidecar files.
