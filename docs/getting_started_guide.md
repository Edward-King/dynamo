# PhotoShare — Getting Started Guide

This guide runs PhotoShare locally with the bundled `sample_photos/` library, creates an API key, and exercises the API.

PhotoShare indexes a filesystem library into SQLite. Image content is stored separately from physical placements, so byte-identical files can appear in more than one portfolio while sharing one image ID and thumbnail cache.

For implementation context, see `photo_sharing_architecture.md`, especially Sections 1–8 and Appendix A. This guide focuses on operating the service.

## 1. Prerequisites

- Python 3.10 or later.
- No separate database server. PhotoShare uses SQLite in `cache.cache_root`.
- Disk space for `metadata.db`, `installation.json`, logs, and lazily generated thumbnails outside the photo library.

## 2. Install dependencies

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` includes the runtime packages plus `pytest`, `pytest-cov`, and `httpx` for testing. The container image uses the runtime-only dependency list in `requirements-runtime.txt`.

## 3. Choose a configuration file

The `--config` option is required. PhotoShare does not select a configuration file implicitly.

- `config.dev.yaml` uses `./sample_photos` as `storage.base_root`, `./dev_cache` for cache data, one worker, and DEBUG logging.
- `config.prod.yaml` uses `/photos_root`, `/var/lib/photoshare/cache`, and `/var/log/photoshare/rescan_history.jsonl`; it sets four workers and INFO logging.

Configuration is validated before the server starts. The cache root must not equal or be nested inside the photo-library root. Environment variables prefixed with `PHOTOSHARE_` override YAML values; use `__` to express nesting:

```bash
export PHOTOSHARE_STORAGE__BASE_ROOT=/mnt/photos
export PHOTOSHARE_SERVER__PORT=9000
python -m photoshare --config config.prod.yaml
```

For example, `PHOTOSHARE_SERVER__WORKERS=2` overrides `server.workers`.

## 4. Start the server

```bash
python -m photoshare --config config.dev.yaml
```

Startup validates the configuration, creates or loads `installation.json`, opens `metadata.db`, applies the SQLite schema, and runs a full rescan when `rescan.on_startup` is true. The persistent `root_id` in `installation.json` is generated once per cache installation.

The development configuration indexes the three bundled sample images. The `vacation` directory contains `meta.json`, so its portfolio receives the declared description and tags.

`server.workers` controls Uvicorn worker processes. Each worker builds an app through `photoshare.api.main:app_factory`; with startup rescans enabled, each worker performs a rescan against the shared SQLite database. For a multi-worker deployment where redundant startup scans are undesirable, set `rescan.on_startup: false` and run one authenticated `POST /api/v2/admin/rescan` after deployment.

## 5. Create an API key

All API routes require the configured API-key header except the health route, `GET /api/v2/admin/health`. The default header is `X-API-Key`.

In a second terminal with the virtual environment active, create a key:

```bash
python -m photoshare.cli.manage --config config.dev.yaml create-api-key --label "my-first-key"
```

The command displays the raw key once. Store it securely; SQLite retains only its SHA-256 hash. Omit `--expires-days` for a non-expiring key, or supply a number of days:

```bash
python -m photoshare.cli.manage --config config.dev.yaml create-api-key \
  --label "temporary-key" --expires-days 30
```

To revoke a key, use its printed principal ID:

```bash
python -m photoshare.cli.manage --config config.dev.yaml revoke-api-key \
  --principal-id <principal-id>
```

## 6. Try the API with curl

Set the key in your shell:

```bash
export PHOTOSHARE_KEY="<raw-key-shown-by-create-api-key>"
```

**Health check — no key required:**

```bash
curl http://127.0.0.1:8000/api/v2/admin/health
```

The response includes `status`, `root_id`, and `schema_version`.

**Root portfolio:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/portfolios/root
```

**Children of a portfolio:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/portfolios/<root-id>/children
```

**Paginated images in a portfolio:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  "http://127.0.0.1:8000/api/v2/portfolios/<vacation-id>/images?page=1&page_size=50"
```

`page` defaults to `1`; `page_size` defaults to `50` and accepts values through `200`.

**One image's metadata:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/images/<img-id>
```

**Original image:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/images/<img-id>/full -o photo.jpg
```

**Thumbnail:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  "http://127.0.0.1:8000/api/v2/images/<img-id>/thumb?width=320&height=240" \
  -o thumb.jpg
```

The first request for a size creates a JPEG thumbnail; later requests use the disk cache. Width and height each default to `320` and `240` and must be in the inclusive range `16`–`4096`.

**Portfolio-tag search:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  "http://127.0.0.1:8000/api/v2/search/portfolios?tag=vacation"
```

The `tag` parameter is required and matches case-insensitively.

**Image-tag search:**

```bash
curl -H "X-API-Key: $PHOTOSHARE_KEY" \
  "http://127.0.0.1:8000/api/v2/search/images?tag=vacation"
```

**Manual full rescan:**

```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" -H "Content-Type: application/json" \
  -d '{"port_uuid": null, "recursive": true}' \
  http://127.0.0.1:8000/api/v2/admin/rescan
```

**Preheat thumbnails:**

```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" -H "Content-Type: application/json" \
  -d '{"port_uuid": "<port-id>", "width": 320, "height": 240, "recursive": true}' \
  http://127.0.0.1:8000/api/v2/admin/preheat-thumbnails
```

All preheat fields are optional. `port_uuid` defaults to the library root, `recursive` defaults to `true`, and `width`/`height` default to `320`/`240`. The response has `images_processed`, `thumbnails_created`, and `duration_ms`.

**Remove stale thumbnails:**

```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" \
  http://127.0.0.1:8000/api/v2/admin/cleanup-thumbnails
```

This body-less request returns `thumbnails_removed` and `duration_ms`.

**Change the active library root:**

```bash
curl -X POST -H "X-API-Key: $PHOTOSHARE_KEY" -H "Content-Type: application/json" \
  -d '{"path": "/mnt/photos"}' \
  http://127.0.0.1:8000/api/v2/admin/root
```

The path must exist and be a directory. A valid request updates the in-memory root and runs a full rescan; it returns `base_root`, `rescan_triggered`, and `duration_ms`. The endpoint does not rewrite the YAML file.

## 7. Understand common responses

Successful list calls return normal JSON responses. Paginated image lists have this shape:

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 0,
  "has_next": false
}
```

Non-success responses use this envelope:

```json
{
  "code": "IMAGE_NOT_FOUND",
  "message": "No image found for id <img-id>",
  "detail": null
}
```

Missing credentials return `401 AUTH_MISSING`; invalid, expired, or revoked credentials return `401 AUTH_INVALID`. Unknown portfolios and images return `404 PORTFOLIO_NOT_FOUND` and `404 IMAGE_NOT_FOUND`, respectively. Invalid request fields return `422 VALIDATION_ERROR` with field details in `detail`.

## 8. Organize a library with `meta.json`

Place an optional `meta.json` in any directory to control the portfolio representation:

```json
{
  "description": "A short description of this portfolio",
  "tags": ["family", "2026"],
  "icon_dir": "cover.jpg"
}
```

- `tags` must be a JSON array of strings. A malformed file is reported in the rescan result's `errors` list and does not stop the scan.
- `icon_dir` is the sole icon setting. A bare filename is resolved relative to the current directory; a value containing `/` or `\\` is resolved relative to `storage.base_root`.
- Without a resolved `icon_dir`, PhotoShare selects the first direct image by case-insensitive natural sort. A portfolio with no usable image has no icon.
- Names beginning with `.` are excluded from indexing.

### Virtual portfolios

A virtual portfolio holds ordered references to images elsewhere in the library:

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

Each `rel_path` resolves against the library root. The referenced image must have an indexed content row before it can be added to virtual membership; otherwise the rescan records an error and a later rescan can resolve it. Array order supplies the returned `sort_order` values, starting at zero.

## 9. Dump an offline portfolio tree

`dump-tree` reads the metadata database without starting the server or running a rescan:

```bash
python -m photoshare.cli.manage --config config.dev.yaml dump-tree --output tree.json
```

To export one subtree:

```bash
python -m photoshare.cli.manage --config config.dev.yaml dump-tree \
  --output sub.json --root <port-id>
```

`--output` is required. A missing `--root` portfolio causes a non-zero exit and does not write a partial output file. The dump includes a tree of portfolio metadata, per-placement image metadata, content hashes, file timestamps, symlink indicators, and generated API/static URL fields; it does not include image bytes or thumbnail files.

## 10. Run the test suite

The current suite contains **167 tests**. The verified run is:

```bash
python -m pytest tests/ -q
```

It reports `167 passed` (with one environment warning in the verified run). The tests cover authentication, error envelopes, configuration, filesystem containment and cycles, image and portfolio endpoints, rescans and identity, duplicate placements, static serving, exports, thumbnail behavior, and the server entry point.

`smoke_test.py` is also available for a lightweight manual check:

```bash
python smoke_test.py
```

## 11. Run with Docker

The project includes a `Dockerfile` and `docker-compose.yml`.

**Compose:**

```bash
docker compose up --build
```

The compose configuration mounts `./sample_photos` read-only at `/photos_root`, stores cache data in the `photoshare_cache` named volume, stores logs in `photoshare_logs`, and sets `PHOTOSHARE_SERVER__WORKERS=2`. Replace the source bind mount with the production photo-library path when deploying.

**Plain Docker:**

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

The image uses `python:3.12-slim`, installs `requirements-runtime.txt`, runs as the non-root `photoshare` user (UID 1000), exposes port 8000, and starts with `python -m photoshare --config config.prod.yaml`.

## 12. Optional static file serving

Every image response contains authenticated dynamic URLs and static URL forms:

- `full_url`: `/api/v2/images/{id}/full`
- `thumbnail_url`: `/api/v2/images/{id}/thumb?width=320&height=240`
- `full_static_url`: `/static-photos/{rel_path}`
- `thumbnail_static_url`: one configured default static thumbnail URL
- `thumbnail_static_urls`: a map with an entry for every configured thumbnail size

Dynamic routes require an API key. To add unauthenticated static mounts, set:

```yaml
enable_static_file_serving: true
```

After a restart, PhotoShare serves originals under `/static-photos` and exposes one thumbnail mount per `cache.thumbnail_sizes` entry, such as `/static-thumbs-320x240` and `/static-thumbs-800x600`. Static delivery never creates a missing thumbnail; use the dynamic thumbnail route or preheating first. Static URLs remain present in API responses even when the mounts are disabled, but they return 404 until static serving is enabled.

The default static URL sizes are independently configurable:

```yaml
cache:
  thumbnail_sizes:
    - [320, 240]
    - [800, 600]
  default_static_thumbnail_size: [320, 240]
  default_static_icon_size: [320, 240]
```

Both defaults must appear in `thumbnail_sizes`. `thumbnail_static_url` uses `default_static_thumbnail_size`; the optional `icon_thumbnail_static_url` on portfolio responses uses `default_static_icon_size`.

Enabling static serving makes files available to anyone who knows the URL. Leave it disabled unless unauthenticated embedding is appropriate. Static mount directories are set when the app is built, so restart after changing the library root to refresh static serving.

## 13. Current limitations

- Any valid API key can call the authenticated administration routes; keys do not carry scopes.
- Rescans run synchronously in the request that starts them.
- Filesystem state and `meta.json` are authoritative; PhotoShare does not provide undo or soft deletion for those changes.
