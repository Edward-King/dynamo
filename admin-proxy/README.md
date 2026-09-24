# PhotoShare Admin Proxy

A small FastAPI service that sits between the browser-based admin UI
(`../photoshare-admin-frontend/`) and PhotoShare's `/api/v2/admin/*` routes
only. It holds a PhotoShare API key server-side, the same way the
data-plane proxy (`../photoshare-proxy/`) does for the gallery, but forwards
only to admin operations: rescan, preheat-thumbnails, cleanup-thumbnails,
and root change.

## Why a separate proxy from the gallery proxy

PhotoShare's backend does not currently scope API keys to read-only vs.
admin — any valid key can call both the data routes (portfolios/images/
search) and the admin routes, since key scopes aren't implemented yet (see
the backend's own `admin.py` docstring). Splitting the proxy and frontend
into a data plane and an admin plane doesn't fix that at the backend, but
it does mean:

- the admin UI and its key never share an origin, container, or codebase
  with the public-facing gallery
- the data-plane and admin-plane API keys can be issued, rotated, and
  revoked independently, even though the backend would currently still
  accept either key on either route group
- if the backend ever adds real key scopes, this proxy needs no changes —
  it would just start getting 403s back for a non-admin key

## Setup

```bash
cd photoshare-admin-proxy
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "photoshare_base_url": "http://127.0.0.1:8000",
  "photoshare_api_key": "<your real PhotoShare admin API key>",
  "proxy_host": "127.0.0.1",
  "proxy_port": 8200,
  "cors_allow_origins": ["http://127.0.0.1:5600", "http://localhost:5600"]
}
```

- `photoshare_base_url` — where your PhotoShare server is running.
- `photoshare_api_key` — created via `python -m photoshare.cli.manage --config config.dev.yaml create-api-key --label "admin-proxy"` on the PhotoShare side. Use a **different** key than the one issued to the data-plane proxy, so the two can be revoked independently.
- `cors_allow_origins` — the origin(s) the admin frontend will be served from.

`config.json` is gitignored — never commit your real API key.

## Running in a container (environment-variable config)

Like the data-plane proxy, `config.json` is optional in a container — every
field can be supplied via environment variables instead:

| Environment variable | Overrides | Required? |
|---|---|---|
| `ADMIN_PROXY_API_KEY` | `photoshare_api_key` | Yes (unless set in `config.json`) |
| `ADMIN_PROXY_BASE_URL` | `photoshare_base_url` | Yes (unless set in `config.json`) |
| `ADMIN_PROXY_HOST` | `proxy_host` | No (default `127.0.0.1`; use `0.0.0.0` in a container — the Dockerfile sets this) |
| `ADMIN_PROXY_PORT` | `proxy_port` | No (default `8200`) |
| `ADMIN_PROXY_CORS_ORIGINS` | `cors_allow_origins` | No (comma-separated) |

Deliberately a different env var prefix from the data-plane proxy's
`GALLERY_PROXY_*` and the backend's own `PHOTOSHARE_*`, so a `docker-compose.yml`
or `.env` file never confuses which service a variable belongs to.

## Running locally

1. Start PhotoShare itself first (in the PhotoShare project):
   ```bash
   python -m photoshare --config config.dev.yaml
   ```
2. Start this proxy (in this project, with its venv active):
   ```bash
   python main.py
   ```
   Listens on `http://127.0.0.1:8200` (or whatever `proxy_port` you set).
3. Serve the admin frontend (in `../photoshare-admin-frontend/`):
   ```bash
   cd ../photoshare-admin-frontend
   python3 -m http.server 5600
   ```
   Then open `http://127.0.0.1:5600` in your browser. Set `ADMIN_PROXY_BASE_URL`
   at the top of `admin.js` to `'http://127.0.0.1:8200'` for this local-only
   setup (it defaults to a relative URL for the Apache2-fronted deployment).

## Endpoints this proxy exposes

| Method | Path | Forwards to PhotoShare |
|---|---|---|
| POST | `/admin/rescan` | `POST /api/v2/admin/rescan` |
| POST | `/admin/preheat-thumbnails` | `POST /api/v2/admin/preheat-thumbnails` |
| POST | `/admin/cleanup-thumbnails` | `POST /api/v2/admin/cleanup-thumbnails` |
| POST | `/admin/root` | `POST /api/v2/admin/root` |
| GET | `/admin/backend-health` | `GET /api/v2/admin/health` |
| GET | `/admin-proxy-health` | (none — this proxy's own health check) |

Request bodies match the backend's own models exactly (`RescanRequest`,
`PreheatThumbnailsRequest`, `SetRootRequest` in `photoshare/api/models.py`)
— see `main.py`'s Pydantic model mirrors for the precise fields.

Rescan and preheat-thumbnails run synchronously on the backend and can take
a while on a large real photo library, so this proxy uses a longer
60-second upstream timeout than the data-plane proxy's 30 seconds, and
returns a distinct 504 with an explanatory message (rather than a bare
timeout) if the backend doesn't respond in time.

## Tested against

Manually verified end-to-end against a mock backend implementing the same
`/api/v2/admin/*` response shapes: all four operations (rescan, preheat,
cleanup, root change) round-trip correctly through the admin frontend →
this proxy → backend → back to the UI, with the backend-health badge
correctly reflecting reachability. Not yet tested against the real
PhotoShare backend or inside an actual Docker container — see the project
knowledge wiki's "known blockers" for outstanding validation work shared
with the data-plane proxy.
