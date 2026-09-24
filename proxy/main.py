"""
PhotoShare Gallery Proxy
========================

A tiny FastAPI service that sits between the browser-based gallery
frontend and a real PhotoShare API server.

Why this exists: PhotoShare's own API requires an `X-API-Key` header on
every route except /admin/health (see GETTING_STARTED.md / how_it_works_guide.md
in the PhotoShare docs). A pure client-side page has nowhere safe to keep
that key -- anything shipped to the browser is visible in dev tools. This
proxy holds the key server-side, in config.json (gitignored), and the
frontend only ever talks to *this* service, over relative/same-origin
URLs, with no key of its own.

Routes exposed to the frontend (all safe, no API key required from callers):

  GET /gallery/portfolios/root                  -> root portfolio node
  GET /gallery/portfolios/{port_uuid}            -> one portfolio node
  GET /gallery/portfolios/{port_uuid}/children   -> sub-portfolios (folders)
  GET /gallery/portfolios/{port_uuid}/images     -> images in a portfolio (paginated)
  GET /gallery/image/{img_uuid}/thumb            -> streamed thumbnail bytes
  GET /gallery/image/{img_uuid}/full             -> streamed full-resolution bytes

Every route above forwards to the equivalent PhotoShare /api/v2 route,
attaching X-API-Key from server-side config. Responses are passed through
largely as-is, except that PhotoShare's own `full_url`/`thumbnail_url`
fields (which point *at PhotoShare directly* and would require the browser
to have the key) are rewritten to point back at this proxy's streaming
routes instead, so the frontend never needs to know PhotoShare's address
or key at all.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

CONFIG_PATH = Path(__file__).parent / "config.json"

# Environment-variable overrides, applied on top of config.json. This lets a
# containerized deployment inject the API key (and, if needed, the upstream
# URL) via `docker run -e` / Compose `environment:` without baking secrets
# into the image or requiring a config.json file to exist in the container at
# all. Env vars win over config.json when both are present so a deploy-time
# override always takes effect. Prefixed GALLERY_PROXY_ to avoid confusion
# with the backend's own PHOTOSHARE_ prefix (this proxy is a separate
# process/container from the PhotoShare backend).
ENV_PREFIX = "GALLERY_PROXY_"
ENV_KEY_MAP = {
    f"{ENV_PREFIX}API_KEY": "photoshare_api_key",
    f"{ENV_PREFIX}BASE_URL": "photoshare_base_url",
    f"{ENV_PREFIX}HOST": "proxy_host",
    f"{ENV_PREFIX}PORT": "proxy_port",
    f"{ENV_PREFIX}CORS_ORIGINS": "cors_allow_origins",
}


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay GALLERY_PROXY_* environment variables onto a config dict.

    CORS origins is the one list-valued field; when set via env var it's a
    comma-separated string (e.g. \"https://example.com,https://a.example.com\"),
    matching how most container platforms pass list-like config. proxy_port
    is coerced to int since env vars are always strings.
    """
    for env_name, cfg_key in ENV_KEY_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        if cfg_key == "cors_allow_origins":
            cfg[cfg_key] = [origin.strip() for origin in raw.split(",") if origin.strip()]
        elif cfg_key == "proxy_port":
            cfg[cfg_key] = int(raw)
        else:
            cfg[cfg_key] = raw
    return cfg


def load_config() -> dict[str, Any]:
    """Load proxy config from config.json (if present) then apply
    GALLERY_PROXY_* environment overrides on top.

    config.json is now OPTIONAL: a container that supplies
    GALLERY_PROXY_API_KEY and GALLERY_PROXY_BASE_URL via the environment
    needs no config.json at all. Running config.json-only (local dev, no
    env vars set) continues to work exactly as before -- this is purely
    additive.
    """
    cfg: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)

    cfg = _apply_env_overrides(cfg)

    required = ["photoshare_base_url", "photoshare_api_key"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required config field(s): {missing}. Provide them via "
            f"config.json (copy config.example.json) or via environment "
            f"variables ({', '.join(k for k, v in ENV_KEY_MAP.items() if v in missing)})."
        )
    if cfg["photoshare_api_key"] == "PASTE_YOUR_PHOTOSHARE_API_KEY_HERE":
        raise RuntimeError("photoshare_api_key is still the placeholder value -- set a real key.")

    cfg.setdefault("proxy_host", "127.0.0.1")
    cfg.setdefault("proxy_port", 8100)
    cfg.setdefault("cors_allow_origins", ["*"])
    return cfg


CONFIG = load_config()
UPSTREAM_BASE = CONFIG["photoshare_base_url"].rstrip("/")
API_KEY = CONFIG["photoshare_api_key"]
AUTH_HEADERS = {"X-API-Key": API_KEY}

app = FastAPI(title="PhotoShare Gallery Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG["cors_allow_origins"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Reused across requests; httpx.AsyncClient is safe for concurrent use.
_client = httpx.AsyncClient(base_url=UPSTREAM_BASE, timeout=30.0)


def _rewrite_image_urls(image: dict[str, Any]) -> dict[str, Any]:
    """
    Replace PhotoShare's own (authenticated) full_url/thumbnail_url with
    this proxy's equivalent streaming routes, so the browser never talks
    to PhotoShare -- or needs its API key -- directly.

    Leaves every other field (name, tags, dimensions, static_url fields,
    etc.) untouched; we only care about the two dynamic-route fields here.
    """
    img_id = image.get("id")
    if img_id:
        image["thumbnail_url"] = f"/gallery/image/{img_id}/thumb"
        image["full_url"] = f"/gallery/image/{img_id}/full"
    return image


def _rewrite_portfolio_urls(portfolio: dict[str, Any]) -> dict[str, Any]:
    """
    Same idea as _rewrite_image_urls, but for PortfolioOut's icon field.

    PhotoShare's PortfolioOut carries icon_thumbnail_url (authenticated,
    dynamic -- see photo_sharing_architecture_v3.md Sec 4.1) whenever
    icon_image_id is set. Left un-rewritten, this field points at
    PhotoShare's own /api/v2/images/{id}/thumb route, which the browser
    can't reach (no key, wrong origin, and it isn't a route this proxy
    exposes). This is why portfolio icon thumbnails don't render:
    the frontend does `${PROXY_BASE_URL}${icon_thumbnail_url}`, producing
    a URL that 404s against the proxy instead of resolving to a real
    image.

    icon_image_id can be None (e.g. an empty portfolio) -- in that case
    PhotoShare's icon_thumbnail_url is also None and there is nothing to
    rewrite; the frontend's placeholder-icon fallback handles that case.
    """
    icon_id = portfolio.get("icon_image_id")
    if icon_id:
        portfolio["icon_thumbnail_url"] = f"/gallery/image/{icon_id}/thumb"
    else:
        portfolio["icon_thumbnail_url"] = None
    return portfolio


async def _upstream_get(path: str, params: dict[str, Any] | None = None) -> httpx.Response:
    try:
        resp = await _client.get(path, headers=AUTH_HEADERS, params=params)
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach PhotoShare server at {UPSTREAM_BASE}. Is it running?",
        ) from exc
    return resp


def _passthrough_or_raise(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        # Forward PhotoShare's own ErrorResponse body/status rather than masking it.
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


@app.get("/gallery/portfolios/root")
async def get_root_portfolio():
    resp = await _upstream_get("/api/v2/portfolios/root")
    data = _passthrough_or_raise(resp)
    return _rewrite_portfolio_urls(data)


@app.get("/gallery/portfolios/{port_uuid}")
async def get_portfolio(port_uuid: str):
    resp = await _upstream_get(f"/api/v2/portfolios/{port_uuid}")
    data = _passthrough_or_raise(resp)
    return _rewrite_portfolio_urls(data)


@app.get("/gallery/portfolios/{port_uuid}/children")
async def get_children(port_uuid: str):
    resp = await _upstream_get(f"/api/v2/portfolios/{port_uuid}/children")
    data = _passthrough_or_raise(resp)
    return [_rewrite_portfolio_urls(child) for child in data]


@app.get("/gallery/portfolios/{port_uuid}/images")
async def get_images(
    port_uuid: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
):
    resp = await _upstream_get(
        f"/api/v2/portfolios/{port_uuid}/images",
        params={"page": page, "page_size": page_size},
    )
    data = _passthrough_or_raise(resp)
    data["items"] = [_rewrite_image_urls(item) for item in data.get("items", [])]
    return data


@app.get("/gallery/image/{img_uuid}/thumb")
async def stream_thumbnail(img_uuid: str, width: int = 320, height: int = 240):
    resp = await _upstream_get(
        f"/api/v2/images/{img_uuid}/thumb",
        params={"width": width, "height": height},
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return StreamingResponse(
        iter([resp.content]),
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={
            k: v
            for k, v in resp.headers.items()
            if k.lower() in ("cache-control", "etag", "last-modified")
        },
    )


@app.get("/gallery/image/{img_uuid}/full")
async def stream_full_image(img_uuid: str):
    resp = await _upstream_get(f"/api/v2/images/{img_uuid}/full")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return StreamingResponse(
        iter([resp.content]),
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={
            k: v
            for k, v in resp.headers.items()
            if k.lower() in ("cache-control", "etag", "last-modified")
        },
    )


@app.get("/gallery/health")
async def health():
    """Own health check -- does not require reaching PhotoShare."""
    return {"status": "ok", "upstream": UPSTREAM_BASE}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=CONFIG["proxy_host"], port=CONFIG["proxy_port"])
