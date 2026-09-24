"""
PhotoShare Admin Proxy
=======================

A tiny FastAPI service that sits between the browser-based admin UI and a
real PhotoShare API server's ADMIN routes only.

Why a separate proxy from the gallery (data-plane) proxy: PhotoShare's
backend does not yet scope API keys to read-only vs. admin -- any valid
key can call both /api/v2/portfolios|images|search/* (data) and
/api/v2/admin/* (rescan, preheat-thumbnails, cleanup-thumbnails, root
change). Splitting the *proxy and frontend* layer into a data plane and an
admin plane (per Edward's 2026-08-17 request) at least means:

  - the admin UI/key never needs to live in the same origin, container, or
    codebase as the public gallery
  - the two API keys (data-plane vs admin-plane) can be issued, rotated,
    and revoked independently, even though the backend itself would
    currently still accept either key on either route group
  - a future backend change to add real key scopes slots in cleanly: this
    proxy would just start getting 403s back from the backend for a
    non-admin key, with no client-side change needed

This proxy is intentionally almost identical in shape to the data-plane
proxy (../photoshare-proxy/main.py) -- same env-var config pattern, same
error handling -- but forwards to a completely different route group and
has none of the image-streaming / URL-rewrite logic (admin responses are
plain JSON, no image bytes, no thumbnail/full URLs to rewrite).

Routes exposed to the admin frontend (all require no API key from callers
-- this proxy holds the PhotoShare admin key server-side):

  POST /admin/rescan               -> POST /api/v2/admin/rescan
  POST /admin/preheat-thumbnails   -> POST /api/v2/admin/preheat-thumbnails
  POST /admin/cleanup-thumbnails   -> POST /api/v2/admin/cleanup-thumbnails
  POST /admin/root                 -> POST /api/v2/admin/root
  GET  /admin/health               -> GET  /api/v2/admin/health (no key needed either side)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

CONFIG_PATH = Path(__file__).parent / "config.json"

# Deliberately a different prefix from the data-plane proxy's
# GALLERY_PROXY_* and the backend's own PHOTOSHARE_* -- this is a third,
# separate process/container and should never be confused with either at a
# glance in a docker-compose.yml or .env file.
ENV_PREFIX = "ADMIN_PROXY_"
ENV_KEY_MAP = {
    f"{ENV_PREFIX}API_KEY": "photoshare_api_key",
    f"{ENV_PREFIX}BASE_URL": "photoshare_base_url",
    f"{ENV_PREFIX}HOST": "proxy_host",
    f"{ENV_PREFIX}PORT": "proxy_port",
    f"{ENV_PREFIX}CORS_ORIGINS": "cors_allow_origins",
}


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay ADMIN_PROXY_* environment variables onto a config dict.

    Mirrors the data-plane proxy's _apply_env_overrides exactly (see
    ../photoshare-proxy/main.py) so the two proxies behave identically
    from an operator's point of view, differing only in the env var
    prefix and which upstream routes they forward to.
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
    """Load admin proxy config from config.json (if present) then apply
    ADMIN_PROXY_* environment overrides on top. config.json is optional --
    a container supplying ADMIN_PROXY_API_KEY and ADMIN_PROXY_BASE_URL via
    the environment needs no config.json at all.
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
    if cfg["photoshare_api_key"] == "PASTE_YOUR_PHOTOSHARE_ADMIN_API_KEY_HERE":
        raise RuntimeError("photoshare_api_key is still the placeholder value -- set a real key.")

    cfg.setdefault("proxy_host", "127.0.0.1")
    cfg.setdefault("proxy_port", 8200)
    cfg.setdefault("cors_allow_origins", ["*"])
    return cfg


CONFIG = load_config()
UPSTREAM_BASE = CONFIG["photoshare_base_url"].rstrip("/")
API_KEY = CONFIG["photoshare_api_key"]
AUTH_HEADERS = {"X-API-Key": API_KEY}

app = FastAPI(title="PhotoShare Admin Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG["cors_allow_origins"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_client = httpx.AsyncClient(base_url=UPSTREAM_BASE, timeout=60.0)
# 60s timeout (vs. the data-plane proxy's 30s): rescan and preheat-thumbnails
# run synchronously in the backend and can take much longer than a single
# image/portfolio fetch on a large real photo library (see the backend's own
# admin.py docstring -- rescan is "v1 simplest-correct behavior," not async).


# --- Request/response models -------------------------------------------
# Mirrored from photoshare/api/models.py so this proxy validates the same
# shape the backend expects before forwarding, and so FastAPI's automatic
# /docs reflects the real admin API surface for whoever builds the admin
# frontend against this proxy.

class RescanRequest(BaseModel):
    port_uuid: Optional[UUID] = None
    recursive: bool = True


class PreheatThumbnailsRequest(BaseModel):
    port_uuid: Optional[UUID] = None
    width: int = Field(320, ge=16, le=4096)
    height: int = Field(240, ge=16, le=4096)
    recursive: bool = True


class SetRootRequest(BaseModel):
    path: str


# --- Helpers -------------------------------------------------------------

async def _upstream_post(path: str, json_body: dict[str, Any] | None = None) -> httpx.Response:
    try:
        resp = await _client.post(path, headers=AUTH_HEADERS, json=json_body or {})
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach PhotoShare server at {UPSTREAM_BASE}. Is it running?",
        ) from exc
    except httpx.ReadTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"PhotoShare server at {UPSTREAM_BASE} did not respond in time. "
                "Admin operations like rescan/preheat run synchronously and can "
                "take a while on a large photo library -- it may still be running "
                "server-side even though this request timed out."
            ),
        ) from exc
    return resp


async def _upstream_get(path: str) -> httpx.Response:
    try:
        resp = await _client.get(path, headers=AUTH_HEADERS)
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach PhotoShare server at {UPSTREAM_BASE}. Is it running?",
        ) from exc
    return resp


def _passthrough_or_raise(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


# --- Routes ----------------------------------------------------------------

@app.post("/admin/rescan")
async def rescan(body: RescanRequest):
    payload = body.model_dump(mode="json")
    resp = await _upstream_post("/api/v2/admin/rescan", payload)
    return _passthrough_or_raise(resp)


@app.post("/admin/preheat-thumbnails")
async def preheat_thumbnails(body: PreheatThumbnailsRequest):
    payload = body.model_dump(mode="json")
    resp = await _upstream_post("/api/v2/admin/preheat-thumbnails", payload)
    return _passthrough_or_raise(resp)


@app.post("/admin/cleanup-thumbnails")
async def cleanup_thumbnails():
    resp = await _upstream_post("/api/v2/admin/cleanup-thumbnails", {})
    return _passthrough_or_raise(resp)


@app.post("/admin/root")
async def set_root(body: SetRootRequest):
    payload = body.model_dump(mode="json")
    resp = await _upstream_post("/api/v2/admin/root", payload)
    return _passthrough_or_raise(resp)


@app.get("/admin/backend-health")
async def backend_health():
    """Forwards to PhotoShare's own unauthenticated /admin/health, so the
    admin UI can show whether the backend itself is reachable, distinct
    from this proxy's own /admin-proxy-health below."""
    resp = await _upstream_get("/api/v2/admin/health")
    return _passthrough_or_raise(resp)


@app.get("/admin-proxy-health")
async def proxy_health():
    """This proxy's own health check -- does not require reaching
    PhotoShare. Named distinctly from /admin/backend-health and from the
    data-plane proxy's /gallery/health so log lines and monitoring configs
    are unambiguous about which service answered."""
    return {"status": "ok", "upstream": UPSTREAM_BASE}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=CONFIG["proxy_host"], port=CONFIG["proxy_port"])
