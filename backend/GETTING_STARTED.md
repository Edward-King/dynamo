# PhotoShare — Getting Started Guide

This guide gets the PhotoShare v3.1 reference implementation running locally in a few minutes, using the bundled `sample_photos/` library so you can see real results immediately.

**v3.1 note:** this revision fixes a cross-portfolio identity collision bug (see `photo_sharing_architecture_v3.md` §9.1) by splitting image content identity from filesystem placement (`images` + new `image_locations` table). No commands or endpoints in this guide changed as a result — duplicate photos placed in more than one portfolio now simply show up correctly in every portfolio that contains them.

This codebase implements the design finalized across the project's architecture documents: `photo_sharing_architecture_v3.md` (core spec), `design_notes_sql_ddl.md` (schema), `design_notes_auth_hooks.md` (auth interfaces), `design_notes_yaml_config.md` (configuration), and `how_it_works_guide.md` (endpoint-by-endpoint behavior). Read those for *why* things work this way; this guide is just *how to run it*.

## 1. Prerequisites

- **Python 3.10+** (tested on 3.11–3.14)
- No external services required — PhotoShare uses SQLite, not a separate database server
- **Optional but recommended for production:** enough disk space at your configured `cache.cache_root` for thumbnails, since they're generated lazily and cached on disk

## 2. Install dependencies

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` pins minimum versions (FastAPI, Uvicorn, Pydantic v2, pydantic-settings, PyYAML, Pillow, natsort, python-multipart) rather than exact versions, so `pip` can resolve compatible builds for your Python version and platform.

## 3. Understand the `--config` flag (required, no default)

PhotoShare **requires** a `--config` flag pointing at a YAML file — there is no implicit default config, by deliberate design (see the YAML Configuration Proposal). Two example files are included:

- `config.dev.yaml` — points `storage.base_root` at the bundled `./sample_photos`, uses `./dev_cache` for cache data, and sets `logging.level: DEBUG`. Use this to try things out.
- `config.prod.yaml` — a production-shaped template with placeholder absolute paths (`/photos_root`, `/var/lib/photoshare/cache`, `/var/log/photoshare/...`). Edit these paths before deploying, or override them with environment variables (next section).

Missing or unreadable `--config` paths fail startup immediately with a clear error — this is intentional, not a bug.

## 4. Run the server (first time)

```bash
python -m photoshare --config config.dev.yaml
```

On first run you'll see log output like:

```
INFO photoshare: Running full rescan on startup (rescan.on_startup=true)...
INFO photoshare: Startup rescan complete: +3/-0 images, +3/-0 portfolios (Nms)
INFO:     Uvicorn running on http://127.0.0.1:8000
```

What just happened, per the startup sequence in `how_it_works_guide.md` §1:

1. `config.dev.yaml` was loaded and validated (fails fast on bad config).
2. `./dev_cache/installation.json` was created with a new, permanent `root_id` (UUID4) — generated exactly once, ever, for this installation.
3. `./dev_cache/metadata.db` was created and the full schema applied.
4. Because `rescan.on_startup: true`, a full scan of `sample_photos/` ran before the server started accepting traffic — indexing the 3 bundled sample images and the `vacation` folder's `meta.json` tags/description.
5. Routes were mounted under `/api/v2` with the auth dependency applied — except `/api/v2/admin/health`, which is intentionally open.

Leave this running in one terminal for the next steps.

**Multiple worker processes.** `server.workers` (default `1` in `config.dev.yaml`, `4` in `config.prod.yaml`) controls how many Uvicorn worker processes handle requests. As of this revision it's genuinely wired up: PhotoShare starts Uvicorn from an import-string factory (`photoshare.api.main:app_factory`) rather than a pre-built app object, which is what lets Uvicorn fork real worker subprocesses. Each worker independently runs the startup rescan (if `rescan.on_startup: true`) against the same SQLite `metadata.db` — safe (WAL mode + a configurable busy-timeout absorb the concurrent writes) but wasteful at high worker counts, since you get N redundant full rescans on every restart. For `workers > 1` deployments where that matters, set `rescan.on_startup: false` and trigger a single `POST /api/v2/admin/rescan` as part of your deploy step instead.

## 5. Create your first API key

Every route except `/api/v2/admin/health` requires an `X-API-Key` header (configurable via `auth.api_key_header`). In a **second terminal** (with the virtualenv activated):

```bash
python -m photoshare.cli.manage --config config.dev.yaml create-api-key --label "my-first-key"
```

Output looks like:

```
API key created. This raw key is shown ONLY ONCE -- store it securely now:

  OI2iAC4vtKJRDuiM6wnUgBhlxjAvoA7YlOIQF_MYIH4

  principal id : 0ae9fe66-8c84-4f05-b2c1-2cc40a06db20
  label        : my-first-key
  issued_at    : 2026-07-04T02:16:49+00:00
  expires_at   : 2027-07-04T02:16:49+00:00
```

The raw key is shown **exactly once** and only its SHA-256 hash is stored — copy it now. Add `--expires-days N` to set an expiry, or omit it for a non-expiring key. To revoke a key later: `create-api-key`'s companion command is `revoke-api-key --principal-id <id>`.

## Dumping the portfolio tree to JSON

Export a full offline snapshot of every portfolio and image's metadata (for backup or inspection) without running the server. This runs standalone against the metadata DB, exactly like `create-api-key`:

```bash
python -m photoshare.cli.manage --config config.dev.yaml dump-tree --output tree.json
```

Dump only a subtree by passing a portfolio's UUID:

```bash
python -m photoshare.cli.manage --config config.dev.yaml dump-tree --output sub.json --root <port_uuid>
```

`--output` is required; `--root` defaults to the library root. On success it prints a one-line summary (portfolio count, image-placement count, path). A nonexistent `--root` fails with a clear message and a non-zero exit code without writing a partial file.

## 6. Try it out with curl

Set your key as a shell variable for convenience:

```bash
export PHOTOSHARE_KEY="OI2iAC4vtKJRDuiM6wnUgBhlxjAvoA7YlOIQF_MYIH4"
```

**Health check (no key needed):**
```bash
curl http://127.0.0.1:8000/api/v2/admin/health
```

**Get the root portfolio:**
```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" http://127.0.0.1:8000/api/v2/portfolios/root
```

**List its children** (use the `id` from the previous response):
```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/portfolios/<root-id>/children
```

**List images in the "vacation" portfolio** (use its `id` from the children list):
```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/portfolios/<vacation-id>/images
```

**Get a single image's metadata** (use an image `id` from the list above):
```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/images/<img-id>
```
Returns the same `ImgOut` shape as the portfolio image listing. Since this is a direct by-id lookup rather than a portfolio placement, `alternate_name` and `sort_order` are always `null` here. An unknown id returns `404 IMAGE_NOT_FOUND`.

**Download a full image** (use an image `id` from the list above):
```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/images/<img-id>/full -o photo.jpg
```

**Get a thumbnail** (first request generates and caches it; subsequent requests are fast):
```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  "http://127.0.0.1:8000/api/v2/images/<img-id>/thumb?width=320&height=240" -o thumb.jpg
```

**Search by tag** (the bundled `vacation/meta.json` sets tags `["vacation", "2026"]`):
```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  "http://127.0.0.1:8000/api/v2/search/portfolios?tag=vacation"
```

**Trigger a manual rescan** (e.g. after adding new photos to `sample_photos/`):
```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" -H "Content-Type: application/json" \
  -d '{"port_uuid": null, "recursive": true}' \
  http://127.0.0.1:8000/api/v2/admin/rescan
```

**Pre-generate thumbnails for a portfolio** (all fields optional — `port_uuid` defaults to the library root; `width`/`height` are independent values, defaulting to `320`/`240` — the same convention used by `GET /images/{img_uuid}/thumb`, so the default call matches the `thumbnail_static_url` every image already returns):
```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" -H "Content-Type: application/json" \
  -d '{"port_uuid": "<port-id>", "width": 320, "height": 240, "recursive": true}' \
  http://127.0.0.1:8000/api/v2/admin/preheat-thumbnails
```
Returns `{"images_processed", "thumbnails_created", "duration_ms"}`. An unknown `port_uuid` returns `404 PORTFOLIO_NOT_FOUND`; a `width` or `height` outside `16–4096` returns `422`.

> **Note:** earlier versions of this endpoint took a single `size` field applied to both dimensions, so the default call produced a `320x320` square file that never matched `thumbnail_static_url`'s `320x240` default — static thumbnails would 404 even after preheating. If you're on an older build, upgrade first; passing explicit `width`/`height` on this new version always produces the file its matching URL expects.

**Remove stale thumbnails** (no request body):
```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/admin/cleanup-thumbnails
```
Deletes any cached thumbnail whose image is no longer in the registry. Returns `{"thumbnails_removed", "duration_ms"}`.

**Change the library root** (a safe, reconciling operation — see the architecture spec §3.6):
```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" -H "Content-Type: application/json" \
  -d '{"path": "/mnt/photos"}' \
  http://127.0.0.1:8000/api/v2/admin/root
```
Identities are preserved and a full rescan reconciles the registry against the new location. Returns `{"base_root", "rescan_triggered", "duration_ms"}`. A path that doesn't exist or isn't a directory returns `400 INVALID_ROOT_PATH` and makes no change.

## 7. Point it at your own photos

Edit `config.dev.yaml` (or copy it to a new file) and change:

```yaml
storage:
  base_root: "/path/to/your/photos"
cache:
  cache_root: "/path/to/a/separate/cache/directory"
```

`base_root` and `cache_root` must never be the same directory or nested inside one another — the app validates this at startup and refuses to start otherwise (this is what keeps PhotoShare's own database and thumbnails from ever polluting your photo library). Restart the server; the startup rescan will index your real library.

### Environment variable overrides

Any setting can be overridden without editing YAML, using `PHOTOSHARE_`-prefixed environment variables with `__` (double underscore) as the nesting separator:

```bash
export PHOTOSHARE_STORAGE__BASE_ROOT=/mnt/photos
export PHOTOSHARE_SERVER__PORT=9000
python -m photoshare --config config.prod.yaml
```

Environment variables always take precedence over the YAML file.

## 8. Organizing your photos (meta.json)

Drop an optional `meta.json` file in any directory to customize how it appears:

```json
{
  "description": "A short description of this portfolio",
  "tags": ["family", "2026"],
  "icon_dir": "cover.jpg"
}
```

- `tags` **must** be a JSON array of strings — a comma-separated string is rejected as malformed and reported in the rescan result's `errors`, never silently accepted.
- `icon_dir` can be a bare filename (an image in the same directory) or a path relative to `base_root` (if it contains a `/`). If omitted, the first image alphabetically (natural sort) is used automatically.
- Directories and files starting with `.` are always invisible to PhotoShare — use dot-prefixed names for anything you don't want indexed.

### Virtual portfolios

Set `"virtual": true` and provide an `images` array to create a portfolio that's really just a curated pointer-list of images living elsewhere in your library (e.g. a "Best Of" collection):

```json
{
  "virtual": true,
  "portfolio": "Best Of 2026",
  "images": [
    { "rel_path": "vacation/beach.jpg", "alternate_name": "Sunset at the beach" },
    { "rel_path": "family/portrait.jpg" }
  ]
}
```

Each `rel_path` must point at an image that has already been indexed elsewhere (or will be, on a subsequent rescan) — a virtual portfolio never duplicates the underlying file.

## 9. Running the test suite

A professional-grade pytest suite lives in `tests/` — 58 tests, 81% coverage of the `photoshare` package, covering symlink cycle detection, identity stability across moves/renames, the `icon_image_id` FK-ordering regression, the full auth/error taxonomy, and (new in v3.1) cross-portfolio duplicate-placement handling (`tests/test_cross_portfolio_duplicates.py`). See `tests/README.md` for the full layout and fixture details.

```bash
pip install -r requirements.txt          # includes pytest, pytest-cov, httpx
python -m pytest tests/ -v --tb=short
```

`smoke_test.py` in the project root remains as a lightweight legacy end-to-end quick-check (`python smoke_test.py`) but the pytest suite is now the source of truth.

## 10. Running with Docker

A `Dockerfile`, `.dockerignore`, and `docker-compose.yml` are included at the project root for containerized deployment.

**Build and run with Compose (recommended):**

```bash
docker compose up --build
```

This builds the image from `Dockerfile`, mounts the bundled `./sample_photos` read-only at `/photos_root` inside the container, and creates two named volumes: `photoshare_cache` (holds `metadata.db`, `installation.json`, and generated thumbnails at `/var/lib/photoshare/cache`) and `photoshare_logs` (holds `rescan_history.jsonl` at `/var/log/photoshare`). Both volumes persist across `docker compose down` / `up` cycles, so your index and thumbnail cache survive restarts. Edit the `volumes:` bind mount in `docker-compose.yml` to point at your real photo library instead of `./sample_photos` before deploying for real.

**Build and run with plain Docker:**

```bash
docker build -t photoshare .
docker run -d \
  -p 8000:8000 \
  -v /path/to/your/photos:/photos_root:ro \
  -v photoshare_cache:/var/lib/photoshare/cache \
  -v photoshare_logs:/var/log/photoshare \
  --name photoshare \
  photoshare
```

**What's inside the image:**

- Base: `python:3.12-slim`.
- Dependencies installed from `requirements-runtime.txt` — a runtime-only subset of `requirements.txt` (drops `pytest`, `pytest-cov`, `httpx`) so the image doesn't carry test tooling.
- Runs as a non-root user (`photoshare`, uid 1000), not root.
- Uses `config.prod.yaml` (container-friendly absolute paths: `/photos_root`, `/var/lib/photoshare/cache`, `/var/log/photoshare/...`) baked into the image as the default config. Override any value with `PHOTOSHARE_<SECTION>__<FIELD>` environment variables (see §7's "Environment variable overrides") — `docker-compose.yml` demonstrates this with `PHOTOSHARE_SERVER__WORKERS=2`.
- Exposes port 8000.

**Multiple workers in a container** work the same way described in §4 above — set `server.workers` in `config.prod.yaml` or override with `PHOTOSHARE_SERVER__WORKERS`. The container's entry point (`python -m photoshare --config config.prod.yaml`) handles propagating that config to each worker subprocess automatically; you don't need to do anything extra for Docker specifically.

**Note:** this guide's Docker instructions were written and the packaging files were authored and tested against a running Python process (confirming two real worker processes start and serve requests), but the actual `docker build`/`docker run` steps have not been executed in this environment, since no Docker daemon is available here. Run the build yourself the first time to confirm it works on your machine, and open an issue if it doesn't.

## 11. What's not included (by design)

- **Admin-scope restriction.** Any valid API key can currently call `/api/v2/admin/*` routes, not just a dedicated "admin" key — see `design_notes_auth_hooks.md`'s extension points table for how per-key scopes would be added later. Fine for a single-operator deployment; a real gap for anything multi-tenant.
- **JWT / browser-session auth.** The current reference implementation is API-key-only (`HeaderApiKeyScheme` + `SqliteCredentialStore`). The auth hooks are already shaped to support swapping in JWT later without touching any route handler — see `design_notes_auth_hooks.md`.
- **Background/async rescans.** `POST /admin/rescan` runs synchronously within the request. Fine for small-to-medium libraries; a known scaling limit at very large ones (flagged, not built, in `how_it_works_guide.md` §8).

## 12. Serving images statically (optional)

Every image in the API carries five URL-bearing fields:

- `full_url` / `thumbnail_url` — the authenticated dynamic routes (always work, require your API key).
- `full_static_url` / `thumbnail_static_url` / `thumbnail_static_urls` — direct static file paths, usable in a plain `<img src>` with **no API key** — but only when static serving is enabled. `thumbnail_static_urls` is a dict with one entry per configured thumbnail size; `thumbnail_static_url` is a single convenience URL for whichever size you've configured as the default (see below).

**Dynamic route (authenticated):**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  "http://localhost:8000/api/v2/images/<img-uuid>/thumb?width=320&height=240" \
  --output thumb.jpg
```

**Static embedding (unauthenticated).** First enable the flag in `config.dev.yaml`:

```yaml
enable_static_file_serving: true
```

Restart the server, then embed directly — no API key, no server code per request:

```html
<img src="http://localhost:8000/static-thumbs-320x240/3f/3f8a...c1_320x240.jpg">
<img src="http://localhost:8000/static-thumbs-800x600/3f/3f8a...c1_800x600.jpg">
<img src="http://localhost:8000/static-photos/vacation/beach.jpg">
```

The exact strings to use are the `thumbnail_static_url` / `thumbnail_static_urls` / `full_static_url` fields returned on each image — don't hand-construct these paths. There is one static mount **per size configured in `cache.thumbnail_sizes`** (default `[(320, 240), (800, 600)]`), at `/static-thumbs-{width}x{height}`. `thumbnail_static_urls` is a dict with one URL per configured size, e.g. `{"320x240": "...", "800x600": "..."}`. A size that isn't in `cache.thumbnail_sizes` has no mount at all and always 404s, even with the flag on. Any configured size must still be pre-generated via `POST /api/v2/admin/preheat-thumbnails` with matching `width`/`height` (see above) before its static URL will resolve. Enabling this flag makes those files fetchable by anyone with the URL, with no API key — see the security note in `photo_sharing_architecture_v3.md` §2.2.1 and `how_it_works_guide.md` §16.

**Choosing which size the singular fields point at.** `thumbnail_static_url` (per image) and `icon_thumbnail_static_url` (per portfolio icon) each resolve to one specific size, controlled by two independent `cache` config settings:

```yaml
cache:
  thumbnail_sizes:
    - [320, 240]
    - [800, 600]
  default_static_thumbnail_size: [320, 240]   # thumbnail_static_url uses this size
  default_static_icon_size: [320, 240]        # icon_thumbnail_static_url uses this size
```

Both default to `[320, 240]` if you omit them, so existing configs keep working unchanged. Set them independently to point each field at a different size, e.g. `default_static_thumbnail_size: [800, 600]` for larger per-image thumbnails while keeping `default_static_icon_size: [320, 240]` for smaller portfolio icons. The server validates both at startup and refuses to start if either value isn't one of the sizes listed in `thumbnail_sizes` — fix the mismatch in your YAML file and restart. Remember that whichever size you configure still needs its own preheat: e.g. if you set `default_static_thumbnail_size: [800, 600]`, run `POST /api/v2/admin/preheat-thumbnails` with `width=800&height=600` (or wait for a dynamic `/thumb?width=800&height=600` request) before `thumbnail_static_url` will resolve instead of 404ing.

## 13. Project layout reference

```
photoshare/
  __main__.py              # `python -m photoshare --config ...` entry point; builds Uvicorn run kwargs, propagates --config to worker subprocesses via env var
  config/schema.py         # Pydantic Settings + load_settings()
  cache/schema.sql          # SQLite DDL (idempotent CREATE TABLE IF NOT EXISTS)
  cache/db.py               # Database connection wrapper + pragmas
  identity/models.py        # root_id, content-hash-based Img_UUID, path-based Port_UUID
  storage/filesystem_adapter.py  # all filesystem access goes through here
  utils/urls.py             # shared URL builders for full_url/thumbnail_url/full_static_url/thumbnail_static_url
  services/
    metadata_service.py     # rescan() -- the placement add/move/remove engine (v3.1: content/placement split)
    metadata_repository.py  # SQL read layer (v3.1: queries join through image_locations)
    portfolio_service.py, image_service.py, viewing_service.py,
    thumbnail_service.py, search_service.py
  auth/
    protocols.py             # CredentialStore / AuthScheme interfaces
    header_api_key.py, sqlite_credential_store.py  # reference implementation
    dependency.py             # FastAPI auth dependency wiring
  api/
    main.py                  # FastAPI app factory + startup sequence; also hosts app_factory(), the import-string target Uvicorn uses to start multiple workers
    routers/                 # portfolios, images, search, admin
    models.py                 # API response/request Pydantic models
    errors.py                  # exception -> ErrorResponse mapping
  cli/manage.py              # create-api-key / revoke-api-key / dump-tree
config.dev.yaml, config.prod.yaml
requirements.txt             # full dev/test dependency set
requirements-runtime.txt     # runtime-only subset used by the Docker image
Dockerfile, .dockerignore, docker-compose.yml   # container packaging
pyproject.toml               # pytest configuration
smoke_test.py                # legacy ad-hoc quick-check
tests/                        # formal pytest suite (source of truth)
  conftest.py                 # shared fixtures (temp fs/db, real services, auth keys)
  test_storage_filesystem.py  # symlink cycle detection, path containment
  test_identity_and_rescan.py # Img_UUID/Port_UUID stability, FK-ordering regression
  test_cross_portfolio_duplicates.py  # v3.1: image_locations placement fix (cross-portfolio duplicates)
  test_auth.py                 # auth taxonomy, expired/revoked keys
  test_api_images.py, test_api_errors.py  # endpoint + error taxonomy coverage
  test_server_entrypoint.py    # Uvicorn workers/app-factory startup wiring
sample_photos/               # bundled sample library for first-run testing
```

For the full behavioral contract of each endpoint — status codes, edge cases, and the move/rename detection algorithm's exact trace — see `how_it_works_guide.md`.
