# PhotoShare test suite

The formal, professional-grade pytest suite is now the **source of truth**
for PhotoShare's behavior. It replaces the previously-deferred harness note
that used to live here.

`smoke_test.py` (project root) remains only as a **legacy quick-check** — a
lightweight end-to-end sanity script with no fixtures and no coverage. It is
*not* collected by pytest (see `pyproject.toml`) and is not a substitute for
the suite below. Run it ad-hoc with `python smoke_test.py`.

## Layout

| File | Covers |
| --- | --- |
| `conftest.py` | Shared fixtures (temp photo library, temp SQLite DB, real service objects, credential store + key helpers, FastAPI `TestClient`). |
| `test_storage_filesystem.py` | Path containment (§2.7), symlink cycle detection (§2.1), the `rescan.cycle_detection` toggle. |
| `test_identity_and_rescan.py` | Content-hash `Img_UUID` stability across rename/move, path-derived `Port_UUID`, `icon_image_id` FK-ordering regression, rescan add/move/remove counts, malformed `meta.json` handling. |
| `test_auth.py` | `AUTH_MISSING`/`AUTH_INVALID` taxonomy, expired/revoked keys, hashed-at-rest storage, the `/admin/health` no-auth exception. |
| `test_api_images.py` | Pagination clamping, thumbnail size validation, ETag conditional (`304`) GETs, and the direct-by-id `GET /images/{img_uuid}` metadata endpoint (ImgOut shape with null virtual fields, `404 IMAGE_NOT_FOUND`, auth-required, and non-shadowing of `/full` and `/thumb`). |
| `test_admin_endpoints.py` | The admin write endpoints on `admin_router`: `POST /admin/preheat-thumbnails` (whole-tree + subtree generation, cache-hit re-run, default size, `404 PORTFOLIO_NOT_FOUND`, out-of-range size `422`, auth), `POST /admin/cleanup-thumbnails` (no-op when nothing stale, removes a stale thumbnail while keeping valid ones, auth), and `POST /admin/root` (valid dir sets `base_root` + reconciling rescan with `root_id` preserved, `400 INVALID_ROOT_PATH` for a missing path / a file, auth). |
| `test_api_errors.py` | `404` not-found taxonomy, error-envelope shape, rescan surfacing of `meta.json` errors. |
| `test_cross_portfolio_duplicates.py` | `image_locations` join (schema_version 2): byte-identical files in different portfolios share one `images` content row but get distinct placements, are listed in BOTH portfolios (service + HTTP), and the content row is dropped/tombstoned only when the last placement is removed. |
| `test_search_service.py` | `SearchService.search_portfolios_by_tag` / `search_images_by_tag`: zero/one/multiple matches and the `COLLATE NOCASE` case-insensitive match (`portfolio_tags` seeded via meta.json rescan; `image_tags` seeded directly, as no rescan path writes it). |
| `test_viewing_service.py` | `ViewingService.viewer_config` (thumbnail-size/tile-threshold shape) and `adjacent_images` first/middle/last neighbors, `NotFoundError` for a non-member image, and both physical (name-sorted) and virtual (`sort_order`) orderings. |
| `test_thumbnail_service.py` | `ThumbnailService` cache-hit short-circuit (2nd `get_or_create` returns the cached file un-regenerated), inclusive `[16, 4096]` size boundaries, `preheat` over multiple sizes, and `cleanup_stale` (deletes stale / keeps valid / skips non-UUID stems and non-shard files). |
| `test_export_service.py` | `export_service.build_portfolio_tree` + the `dump-tree` CLI: full-tree dump (recursion + portfolio/image counts), subtree via an explicit root Port_UUID, nonexistent-root `NotFoundError`, cross-portfolio duplicate images repeated in both portfolios' `images` arrays (same `id`, per-placement `name`/`rel_path`), virtual-portfolio `sort_order`/`alternate_name` population (null for physical entries), and a CLI round-trip (valid JSON on disk + top-level shape, nonexistent-root exits non-zero writing no file). |
| `test_static_serving.py` | The opt-in `enable_static_file_serving` mounts and the four `*_url` fields on `ImgOut`: flag OFF (all four URL fields present as populated strings, but the two `*_static_url` paths `404` since no mount is registered); flag ON (`/static-photos` and `/static-thumbs` serve the correct bytes with **no** `X-API-Key` header — the explicit security tradeoff); and a non-default-size static-thumb URL predictably `404`ing because static serving can't lazily generate. |
| `test_api_portfolios.py` | `PortfolioOut`'s two icon-URL fields (`icon_thumbnail_url`, `icon_thumbnail_static_url`): populated (matching the `ImgOut` thumbnail formats) when an icon is resolved, both `None` when the portfolio has none (root portfolio); plus flag OFF (field present but `404`) / flag ON (icon thumbnail bytes served with no `X-API-Key`) mirroring `test_static_serving.py`. |

## Fixtures (`conftest.py`)

All fixtures are isolated **per test** and rooted under pytest's `tmp_path`,
so nothing touches real disk outside the temp tree and the suite is safe
under parallel execution (`pytest -n auto`, if `pytest-xdist` is installed —
not a required dependency). Highlights:

- **`library`** — a `LibraryBuilder` over a temp `photos/` dir with helpers
  to add real JPEGs, raw-byte files (for exact content-hash control), nested
  dirs, `meta.json` (well-formed or malformed), and arbitrary symlinks
  (including cycles). **`sample_library`** is a pre-populated variant.
- **`db`** — an isolated, freshly-schema'd SQLite DB built through the real
  `cache.db.Database` class, i.e. the same `schema.sql` + `apply_schema`
  code path used at production startup (no hand-copied DDL).
- **service fixtures** — `storage`, `repo`, `metadata_service`
  (+ `metadata_service_factory`), `portfolio_service`, `image_service`,
  `thumbnail_service`, all wired against the temp fs + temp DB. Real logic,
  no mocks.
- **`credential_store`** plus **`valid_key` / `expired_key` / `revoked_key`**
  minting helpers, bound to the temp DB.
- **`client`** — the full app via the real `create_app()` factory against a
  `Settings` object pointed at the temp fs/DB (startup rescan populates the
  sample library). This is exactly how production wires the app — strictly
  stronger than a `Depends()` override. **`api_key_header`** yields a valid
  `X-API-Key` header dict minted against the running app's DB.
- **`assert_json`** — opt-in fixture for comparing a whole response-shaped
  dict. Plain `assert body == expected` is correct and is what nearly every
  test uses; pytest's assertion rewriting prints a real diff on failure.
  The one caveat: since `body` is the *deserialized* Python dict (from
  `r.json()`), that diff renders Python's `None`/`True`/`False`, not JSON's
  `null`/`true`/`false` — that's just Python's `repr()`, not a claim about
  the wire format (the actual HTTP response bytes are always spec-compliant
  JSON; confirm with `r.text` + `json.loads()` if ever in doubt). Use
  `assert_json(actual, expected, msg=...)` instead of `assert actual ==
  expected` when a mismatch's JSON-formatted diff would be materially
  easier to read than the default Python-repr diff — e.g. comparing a full
  paged-result or placement dict. See `test_cross_portfolio_duplicates.py`
  for an example. Also importable directly as `assert_json_equal` from
  `conftest.py` if a test prefers a plain function over a fixture.

## Test dependencies

Test-only deps are listed in the root **`requirements.txt`** under the
`# test dependencies` section:

- `pytest`, `pytest-cov` — runner + coverage.
- `httpx` — required by FastAPI's `TestClient` (starlette), not pulled in by
  `fastapi` itself.

## Running

```bash
pip install -r requirements.txt          # includes the test deps
python -m pytest tests/ -v --tb=short    # run the suite
python -m pytest tests/ --cov=photoshare --cov-report=term-missing   # with coverage
python smoke_test.py                     # optional legacy end-to-end quick-check
```

Pytest configuration (testpaths, discovery patterns) lives in the root
`pyproject.toml` under `[tool.pytest.ini_options]`.
