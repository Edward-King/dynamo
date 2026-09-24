# PhotoShare Cloud Deployment — Working Tree

This folder holds the editable source used to containerize PhotoShare's
backend + proxy for an Ubuntu 22 cloud server, with the frontend served by
the server's native Apache2. It is separate from the original snapshot
archives at the repo root (`PhotoShare Gallery Backend.zip` /
`Frontend.zip` / `Proxy.zip`), which remain as the historical reference
copies delivered at project start.

See the project knowledge wiki (`projects/photoshare-docker-deployment.md`)
for the full architecture, decisions, and rationale. This README only
tracks what's been done here and what's still pending.

## Status (2026-08-17) — Step 3 of 3: full integration validated locally in a sandbox

**Step 1: Data plane (done)**
- [x] Proxy: `GALLERY_PROXY_*` environment-variable config support added to
  `proxy/main.py` (`load_config()` now overlays env vars on top of an
  optional `config.json`; either source alone is sufficient). See
  `proxy/README.md` for the variable table.
- [x] Proxy: `proxy/Dockerfile` + `proxy/.dockerignore` authored (none
  existed before). Binds `GALLERY_PROXY_HOST=0.0.0.0` inside the container
  so the published port is actually reachable; does not copy `config.json`
  into the image (secrets come from the environment only).
- [x] Frontend: `frontend/common.js` — `PROXY_BASE_URL` changed from the
  hardcoded `http://127.0.0.1:8100` to `''` (relative/same-origin), so the
  gallery works through an Apache2 reverse proxy on any real domain without
  further changes. `common.js` was the only file with the hardcoded value;
  the full frontend (`index.html`, `about.html`, `script.js`, `style.css`,
  `common.js`) now lives in `frontend/` here, unchanged apart from that one
  line, for a self-contained deployable copy.
- [x] Validated end-to-end against a real Docker daemon as part of Step 3
  below (the backend's own standalone Dockerfile, used only as a build
  context here, is still untested in isolation — see `GETTING_STARTED.md`).

**Step 2: Admin plane (this step — done)**
- [x] New admin proxy service: `admin-proxy/main.py`, forwards only to
  `/api/v2/admin/*` (rescan, preheat-thumbnails, cleanup-thumbnails, root
  change, plus a backend-health passthrough and its own health check).
  Uses its own `ADMIN_PROXY_*` env var prefix (distinct from the
  data-plane proxy's `GALLERY_PROXY_*`) and its own API key, separate from
  the data-plane proxy's key. Own `Dockerfile` + `.dockerignore` (same
  conventions as the data-plane proxy: non-root uid 1000, binds
  `ADMIN_PROXY_HOST=0.0.0.0` in-container, no `config.json` baked into the
  image). Uses a 60s upstream timeout (vs. the data-plane proxy's 30s)
  since rescan/preheat run synchronously on the backend and can take a
  while on a large library.
- [x] New minimal admin frontend: `admin-frontend/index.html` +
  `admin.js`, standalone (no shared code with the gallery frontend) with
  one form per admin operation (rescan, preheat-thumbnails,
  cleanup-thumbnails, root change) plus a live backend-health badge.
  `ADMIN_PROXY_BASE_URL` defaults to `''` (relative/same-origin), matching
  the data-plane frontend's pattern for working through Apache2 without
  further changes.
- [x] Manually tested end-to-end against a mock backend implementing the
  admin route response shapes: backend-health badge and all four
  operations (rescan, preheat, cleanup, root change) verified to
  round-trip correctly through frontend → admin proxy → backend → back to
  the UI, with no console errors.
- [x] Validated end-to-end against the real PhotoShare backend and a real
  Docker daemon as part of Step 3 below.

**Step 3: Integration (done — validated end-to-end with a real Docker daemon)**
- [x] Three-service `docker-compose.yml` (`docker-compose.yml`): backend +
  data-proxy + admin-proxy, backend not published to the host by default
  (only the two proxies reach it, over the internal Compose network),
  `depends_on: condition: service_healthy` gating both proxies on the
  backend's own healthcheck (`GET /api/v2/admin/health`), each proxy's own
  healthcheck hitting its `/gallery/health` or `/admin-proxy-health`. API
  keys and CORS origins are required env vars (`${VAR:?...}` — compose
  refuses to start without them) sourced from a new `.env.example`
  (`GALLERY_PROXY_API_KEY`, `ADMIN_PROXY_API_KEY`, plus their respective
  `*_CORS_ORIGINS`, the `PHOTOSHARE_PHOTOS_ROOT` host path, and the
  `PHOTOSHARE_CACHE_ROOT` / `PHOTOSHARE_LOG_ROOT` host paths for the
  backend's persistent cache and logs — bind-mounted directly rather than
  Docker-managed named volumes, for easier inspection/backup). Validated as
  syntactically correct YAML with the right service/port/dependency
  topology, and later run end-to-end against a real Docker daemon (see
  below).
- [x] Two Apache2 vhost configs (`apache/photoshare-gallery.conf`,
  `apache/photoshare-admin.conf`): separate vhosts/document roots/upstream
  ports for the data plane (proxies `/gallery/*` to `127.0.0.1:8100`) and
  admin plane (proxies `/admin/*` + `/admin-proxy-health` to
  `127.0.0.1:8200`), each serving its own frontend statically and each
  same-origin with its own proxy so neither `common.js`'s nor `admin.js`'s
  relative base URLs need any CORS configuration in the default setup.
  **Verified with a real installed Apache2** in this sandbox: both configs
  pass `apache2ctl -t` syntax validation, and a live end-to-end test
  (name-based vhosts + `mod_proxy` + a running admin-proxy container against
  a mock backend) confirmed each vhost serves its own static frontend,
  correctly proxies its own routes end-to-end (`/admin-proxy-health` and
  `/admin/backend-health` both returned real backend data through the full
  chain), and that the two planes are mutually isolated — admin routes 404
  on the gallery vhost and vice versa. Test scaffolding (mock backend,
  Apache site enablement, test docroots) was torn down afterward; the repo
  only contains the two `.conf` files themselves.
- [x] **Full integration validated with a real Docker daemon.** The
  PhotoShare backend source is now copied into `backend/` here (from
  `PhotoShare Gallery Backend.zip`, unmodified) so `docker-compose.yml`'s
  build context resolves. All three images (`backend`, `data-proxy`,
  `admin-proxy`) were built and the full stack was brought up together —
  the backend passed its healthcheck, both proxies then started and passed
  theirs, exactly matching the `depends_on: condition: service_healthy`
  ordering in the compose file.
- [x] With a real backend-issued API key (via the backend's own
  `photoshare.cli.manage create-api-key` command), verified real data
  flowing through the full chain: the data-proxy returned the real
  portfolio tree (2 albums, 3 images) from the mounted sample photo
  library, and fetching `/gallery/image/{id}/full` and `/thumb` returned
  real JPEG bytes at the correct dimensions straight through proxy →
  backend → filesystem. On the admin plane, a real `POST /admin/rescan`
  completed successfully end-to-end, and client-side key enforcement was
  confirmed correct: gated operations (`rescan`, `cleanup-thumbnails`, etc.)
  return 401 with no key or the wrong key and succeed only with the right
  one, while the two health-passthrough routes (`/admin/backend-health`,
  `/admin-proxy-health`) are correctly unauthenticated by design.
- **Sandbox networking caveat:** this sandbox's kernel has no
  netfilter/nftables support at all, so Docker's default bridge network
  can't NAT container traffic (no internet access during image builds, no
  inter-container DNS at runtime). Images were built with
  `docker buildx build --network=host` and the stack was run with a
  sandbox-only compose override forcing `network_mode: host` on all three
  services — purely to work around this environment's limitation. Neither
  the override file nor any sandbox workaround is part of this repo; the
  real `docker-compose.yml` here is unmodified and expects normal bridge
  networking, which a standard Ubuntu 22 cloud host has. First `docker
  compose up` on the real server, DNS records, and TLS certificates for
  the real hostnames are still outstanding — those need the actual target
  server and domains, which this sandbox doesn't have.

## Layout

```
deployment/
  docker-compose.yml  three-service stack (step 3): backend, data-proxy,
                      admin-proxy
  .env.example        required env vars for docker-compose.yml (API keys,
                      CORS origins, photo-library/cache/log host paths) —
                      copy to .env and fill in real values, never commit
                      real .env
  apache/             the two Apache2 vhost configs (step 3): one for the
                      data plane, one for the admin plane, each with its
                      own document root and upstream proxy port
  backend/    full copy of the PhotoShare backend source (unmodified, from
              PhotoShare Gallery Backend.zip) — this is docker-compose.yml's
              build context for the backend service
  proxy/      full working copy of the proxy service, modified for
              containerization (this is the DATA-plane proxy)
  frontend/   full gallery frontend (index.html, about.html, script.js,
              style.css, common.js) — only common.js's PROXY_BASE_URL was
              changed for containerization, the rest is unmodified
  admin-proxy/    new admin-plane proxy service (step 2), forwards only
                  to /api/v2/admin/*, own Dockerfile + API key
  admin-frontend/ new minimal admin UI (step 2), standalone HTML/JS with
                  one form per admin operation plus a backend-health badge
```

To actually run this stack: copy `.env.example` to `.env`, fill in the
real host paths (`PHOTOSHARE_PHOTOS_ROOT`, `PHOTOSHARE_CACHE_ROOT`,
`PHOTOSHARE_LOG_ROOT`), then issue real API keys on the backend with
`python -m photoshare.cli.manage --config config.prod.yaml create-api-key
--label <name>` (run once, inside the backend container, after first
startup — the raw key is only shown once) before the proxies can
authenticate to it. See `CLOUD_DEPLOY.md` for the full ordered walkthrough,
including the circular dependency between starting the backend and issuing
those keys.
