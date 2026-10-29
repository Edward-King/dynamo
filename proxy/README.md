# PhotoShare Gallery Proxy

A small FastAPI service that sits between the browser-based gallery frontend
(`../photoshare-frontend/`) and a real PhotoShare API server. It holds your
PhotoShare API key server-side so the key is never exposed to the browser.

## Setup

```bash
cd photoshare-proxy
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "photoshare_base_url": "http://127.0.0.1:8000",
  "photoshare_api_key": "<your real PhotoShare API key>",
  "proxy_host": "127.0.0.1",
  "proxy_port": 8100,
  "cors_allow_origins": ["http://127.0.0.1:5500", "http://localhost:5500"]
}
```

- `photoshare_base_url` — where your PhotoShare server is running.
- `photoshare_api_key` — created via `python -m photoshare.cli.manage --config config.dev.yaml create-api-key --label "gallery-proxy"` on the PhotoShare side.
- `cors_allow_origins` — the origin(s) your frontend will be served from. If you're opening `index.html` directly with a "Live Server"-style extension or a simple `python -m http.server`, add that origin here (e.g. `http://127.0.0.1:5500` for VS Code's Live Server, or `http://127.0.0.1:8000`/whatever port you use).

`config.json` is gitignored — never commit your real API key.

## Running in a container (environment-variable config)

For Docker/Compose deployments, `config.json` is optional — every field can
be supplied via environment variables instead, which is the recommended
approach so the API key never ends up baked into an image layer or mounted
from a file:

| Environment variable | Overrides | Required? |
|---|---|---|
| `GALLERY_PROXY_API_KEY` | `photoshare_api_key` | Yes (unless set in `config.json`) |
| `GALLERY_PROXY_BASE_URL` | `photoshare_base_url` | Yes (unless set in `config.json`) |
| `GALLERY_PROXY_HOST` | `proxy_host` | No (default `127.0.0.1`; use `0.0.0.0` in a container) |
| `GALLERY_PROXY_PORT` | `proxy_port` | No (default `8100`) |
| `GALLERY_PROXY_CORS_ORIGINS` | `cors_allow_origins` | No (comma-separated, e.g. `https://example.com,https://a.example.com`) |
| `GALLERY_PROXY_SITE_NAME` | `site_name` | No (default `My Dynamic Gallery`; shown in share-link previews) |

Environment variables always win over `config.json` when both are set, so
you can still keep a `config.json` for local dev and override just the key
at deploy time. See the accompanying `Dockerfile`, which deliberately does
**not** copy `config.json` into the image for this reason.

## Running

1. Start PhotoShare itself first (in the PhotoShare project):
   ```bash
   python -m photoshare --config config.dev.yaml
   ```
2. Start this proxy (in this project, with its venv active):
   ```bash
   python main.py
   ```
   You should see it listening on `http://127.0.0.1:8100` (or whatever `proxy_port` you set).
3. Serve the frontend (in `../photoshare-frontend/`), e.g.:
   ```bash
   cd ../photoshare-frontend
   python3 -m http.server 5500
   ```
   Then open `http://127.0.0.1:5500` in your browser.

If the frontend's status area shows a "could not reach the gallery proxy" message, check that step 2's server is running and that `PROXY_BASE_URL` at the top of `script.js` matches your `proxy_port`.

## Share-link previews

`GET /gallery/share?p=<album id>[&i=<photo id>]` is the address the
gallery's **Copy link** / **Share album** buttons hand out. It answers
with a small HTML page carrying Open Graph tags (album or photo name, a
description, and an 800x600 thumbnail as `og:image`) so chat and social
apps can draw a preview card, then sends visitors on to the gallery
(`../?p=...&i=...`) with one line of JavaScript. It's a `200` page rather
than a redirect because preview crawlers follow redirects and would then
only see the gallery's untagged page.

- Absolute URLs in the tags are built from the request's `Host` header and
  `X-Forwarded-Proto`, which the Apache2 vhost provides
  (`ProxyPreserveHost On`). A `Host` that isn't a plain host name (with an
  optional port) is ignored and those tags are left out.
- IDs must be UUIDs; anything else, or an album or photo PhotoShare doesn't
  know, gets a friendly "isn't available" page with status `404`.
- If PhotoShare is down or slow (lookups give up after 5 seconds), the page
  still sends visitors on, with a plain preview and `Cache-Control:
  no-store`. Normal pages are cached for 5 minutes (`public, max-age=300`).
- Names are HTML-escaped. The page has no API key or PhotoShare address in
  it.

## Why a proxy at all?

PhotoShare requires an `X-API-Key` header on every route except `/admin/health`.
A pure client-side page (just HTML/CSS/JS with no server of its own) has nowhere
safe to store that key — anything shipped to the browser is visible via dev tools,
and this key can also call every `/admin/*` route (PhotoShare doesn't yet scope
keys to read-only — see its own docs' "known interim limitations"). This proxy
holds the key in `config.json` on your machine, and the browser only ever talks
to the proxy over plain, keyless routes.

## Running the tests

The test suite (`test_main.py` / `conftest.py`) covers every route above,
including the portfolio/image URL-rewrite logic, pagination params, error
pass-through, the `?download=1` file-download option (filename choice,
safe/encoded names, fallbacks, concurrent filename lookup), and the
`/gallery/health` check. It mocks the upstream PhotoShare server with
[respx](https://lundberg.github.io/respx/), so it runs fully offline --
no real `config.json` or running PhotoShare server is needed (a throwaway
one is generated for the test run and any existing `config.json` is
safely restored afterwards).

The same suite runs automatically on GitHub (Python 3.12 and 3.14) for
every pull request and push to `main` that touches `proxy/` -- see
`.github/workflows/proxy-tests.yml`.

```bash
pip install -r requirements-dev.txt
pytest -v
```

## Endpoints this proxy exposes

| Method | Path | Forwards to PhotoShare |
|---|---|---|
| GET | `/gallery/portfolios/root` | `GET /api/v2/portfolios/root` |
| GET | `/gallery/portfolios/{id}` | `GET /api/v2/portfolios/{id}` |
| GET | `/gallery/portfolios/{id}/children` | `GET /api/v2/portfolios/{id}/children` |
| GET | `/gallery/portfolios/{id}/images` | `GET /api/v2/portfolios/{id}/images` |
| GET | `/gallery/image/{id}/thumb` | `GET /api/v2/images/{id}/thumb` |
| GET | `/gallery/image/{id}/full` | `GET /api/v2/images/{id}/full` |
| GET | `/gallery/image/{id}/full?download=1` | `GET /api/v2/images/{id}/full` + `GET /api/v2/images/{id}` (for the filename) |
| GET | `/gallery/health` | (none — proxy's own health check) |

With `?download=1`, the full image is sent with
`Content-Disposition: attachment` and the image's real filename, so the
browser saves it instead of displaying it. The gallery's lightbox
Download button uses this; without the parameter the response is
unchanged. Full-image responses also carry `Vary: Origin` so browsers
cache CORS and non-CORS copies separately (see the comment in
`stream_full_image`).
