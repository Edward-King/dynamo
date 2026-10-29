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
      ?download=1                                   (optional) same bytes, sent as a
                                                    file download with the image's
                                                    real filename
  GET /gallery/share?p={port_uuid}[&i={img_uuid}] -> small HTML page with link-preview
                                                    tags (title, photo) that sends
                                                    people on to the gallery

Every route above except /gallery/share forwards to the equivalent PhotoShare /api/v2 route,
attaching X-API-Key from server-side config. Responses are passed through
largely as-is, except that PhotoShare's own `full_url`/`thumbnail_url`
fields (which point *at PhotoShare directly* and would require the browser
to have the key) are rewritten to point back at this proxy's streaming
routes instead, so the frontend never needs to know PhotoShare's address
or key at all.
"""

from __future__ import annotations

import asyncio
import html
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

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
    f"{ENV_PREFIX}SITE_NAME": "site_name",
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
    # Shown in link previews (og:site_name). Matches the gallery's header.
    cfg.setdefault("site_name", "My Dynamic Gallery")
    return cfg


CONFIG = load_config()
UPSTREAM_BASE = CONFIG["photoshare_base_url"].rstrip("/")
API_KEY = CONFIG["photoshare_api_key"]
AUTH_HEADERS = {"X-API-Key": API_KEY}
SITE_NAME = str(CONFIG["site_name"])

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


# Characters that are unsafe or meaningless in a downloaded filename on at
# least one major OS (Windows is the strictest: <>:"/\|?* plus control
# characters). Replaced rather than dropped so names stay recognizable.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def _safe_download_filename(name: str | None, img_uuid: str, content_type: str) -> str:
    """Pick the filename a browser should save a downloaded image as.

    Prefers the image's real name from PhotoShare's metadata (e.g.
    "sunset.jpg"). Falls back to "<uuid><ext>" -- extension guessed from
    the upstream Content-Type -- when no usable name is available. The name
    always comes from PhotoShare, never from the request, so a caller can't
    choose what the file is called.
    """
    cleaned = ""
    if name:
        # Keep only the last path component (rel_path-style names shouldn't
        # reach us, but be defensive), then neutralize unsafe characters.
        cleaned = name.replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = _UNSAFE_FILENAME_CHARS.sub("_", cleaned).strip(" .")
    if not cleaned:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".jpg"
        # mimetypes maps image/jpeg to ".jpg" on 3.12+, but older tables
        # return ".jpe"; normalize to the extension people expect.
        if ext == ".jpe":
            ext = ".jpg"
        cleaned = f"{img_uuid}{ext}"
    return cleaned


def _content_disposition_attachment(filename: str) -> str:
    """Build an RFC 6266 attachment header that works across browsers.

    `filename=` carries an ASCII-only fallback (non-ASCII replaced with
    "_") for older clients; `filename*=` carries the exact UTF-8 name,
    which every current browser (Chrome, Firefox, Safari, Edge, mobile)
    prefers when present.
    """
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


async def _lookup_image_name(img_uuid: str) -> str | None:
    """Fetch the image's real filename from PhotoShare's metadata route.

    Best-effort: any failure (unknown id, upstream error, timeout, dropped
    connection, malformed body) returns None and the caller falls back to a
    UUID-based filename, so a metadata hiccup never blocks the download
    itself.
    """
    try:
        resp = await _upstream_get(f"/api/v2/images/{img_uuid}")
    except HTTPException:
        # _upstream_get turns connection failures into a 502 HTTPException.
        return None
    except httpx.HTTPError:
        # Everything _upstream_get doesn't convert: timeouts, read/protocol
        # errors, etc. Without this, a slow metadata lookup turned an
        # otherwise successful download into a 500.
        return None
    if resp.status_code >= 400:
        return None
    try:
        name = resp.json().get("name")
    except (ValueError, AttributeError):
        return None
    return name if isinstance(name, str) else None


@app.get("/gallery/image/{img_uuid}/full")
async def stream_full_image(img_uuid: str, download: bool = Query(False)):
    """Stream the full-resolution original.

    With `?download=1` the same bytes are sent with a
    `Content-Disposition: attachment` header so the browser saves the file
    (using the image's real name) instead of displaying it. This is needed
    because the gallery frontend and this proxy are on different origins,
    and browsers ignore the HTML `download` attribute on cross-origin links.
    Without the parameter the response is unchanged.

    For downloads, the filename lookup runs at the same time as the image
    fetch rather than after it, so it adds no extra wait. If the image
    fetch fails, the lookup is cancelled.
    """
    name_task: asyncio.Task[str | None] | None = None
    if download:
        name_task = asyncio.create_task(_lookup_image_name(img_uuid))
    try:
        resp = await _upstream_get(f"/api/v2/images/{img_uuid}/full")
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except BaseException:
        if name_task is not None:
            name_task.cancel()
        raise
    content_type = resp.headers.get("content-type", "image/jpeg")
    headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() in ("cache-control", "etag", "last-modified")
    }
    # The same full image is requested both without CORS (the page's dimmed
    # background, plain <img> loads) and with CORS (the lightbox in share
    # mode, which reads the bytes to hand to the share sheet). With
    # cors_allow_origins = ["*"] the CORS middleware only adds
    # Access-Control-Allow-Origin when the request carries an Origin header,
    # and doesn't add Vary. Combined with PhotoShare's immutable
    # Cache-Control, a browser would reuse the cached non-CORS copy for the
    # CORS request and block it. Vary: Origin tells caches to keep the two
    # apart. (Starlette already adds Vary: Origin when a specific origin list
    # is configured; this makes the "*" case behave the same.)
    headers["Vary"] = "Origin"
    if name_task is not None:
        name = await name_task
        filename = _safe_download_filename(name, img_uuid, content_type)
        headers["Content-Disposition"] = _content_disposition_attachment(filename)
    return StreamingResponse(
        iter([resp.content]),
        media_type=content_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Share links with previews
#
# The gallery's share buttons hand out /gallery/share?p=<album>[&i=<photo>]
# links. Chat and social apps (iMessage, WhatsApp, Slack, Discord, ...) read
# a link's Open Graph tags to draw a preview card, but they don't run
# JavaScript, so they can't see anything the gallery itself renders. This
# route answers with a tiny HTML page that carries those tags (album or photo
# name, plus a photo) and then sends real visitors on to the gallery page
# (/?p=...&i=...) with one line of JavaScript. Crawlers ignore the script and
# keep the tags; people barely see the page.
#
# It's a 200 page rather than an HTTP redirect on purpose: most preview
# crawlers follow redirects and would then read the gallery's own page,
# which has no tags.
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# A hostname or IPv4 address, or a bracketed IPv6 address, with an optional
# port. Anything else in the Host header isn't used to build links.
_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?$"
)
# Preview crawlers give up after a few seconds, so lookups here use a much
# shorter timeout than the rest of the proxy.
SHARE_LOOKUP_TIMEOUT = 5.0
SHARE_CACHE_CONTROL = "public, max-age=300"
# One of the thumbnail sizes PhotoShare pre-generates (cache.thumbnail_sizes
# in config.prod.yaml), so previews are fast once thumbnails are preheated.
PREVIEW_WIDTH = 800
PREVIEW_HEIGHT = 600


def _valid_uuid(value: str | None) -> str | None:
    if value and _UUID_RE.match(value):
        return value.lower()
    return None


def _public_origin(request: Request) -> str | None:
    """The scheme://host this request came in on, for absolute preview URLs.

    Apache keeps the visitor's Host header (ProxyPreserveHost On) and says
    whether the visitor used HTTPS in X-Forwarded-Proto. Returns None when
    the Host header doesn't look like a host name; the page then leaves out
    the tags that need an absolute URL instead of building one from it.
    """
    host = request.headers.get("host", "")
    if not _HOST_RE.match(host):
        return None
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        proto = request.url.scheme
    return f"{proto}://{host}"


async def _share_lookup(path: str) -> tuple[str, dict[str, Any] | None]:
    """Fetch one metadata record for the share page.

    Returns ("ok", data), ("missing", None) when PhotoShare doesn't know the
    ID, or ("error", None) when PhotoShare couldn't be asked (down, slow,
    unexpected reply). Never raises.
    """
    try:
        resp = await _client.get(path, headers=AUTH_HEADERS, timeout=SHARE_LOOKUP_TIMEOUT)
    except httpx.HTTPError:
        return "error", None
    if resp.status_code in (400, 404, 422):
        return "missing", None
    if resp.status_code >= 400:
        return "error", None
    try:
        data = resp.json()
    except ValueError:
        return "error", None
    if not isinstance(data, dict):
        return "error", None
    return "ok", data


def _preview_size(image: dict[str, Any] | None) -> tuple[int, int] | None:
    """Size of the 800x600 preview thumbnail, from the original's size.

    PhotoShare shrinks images to fit inside the box, keeping the shape, and
    never enlarges them. Returns None if the original's size is unknown.
    """
    if not image:
        return None
    width, height = image.get("width"), image.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None
    scale = min(PREVIEW_WIDTH / width, PREVIEW_HEIGHT / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _photo_count_text(album: dict[str, Any]) -> str:
    count = album.get("total_image_count")
    if not isinstance(count, int) or count <= 0:
        return "Album"
    return f"Album \u00b7 {count} photo" + ("" if count == 1 else "s")


def _share_html(
    *,
    title: str,
    description: str,
    target: str | None,
    page_url: str | None,
    image_url: str | None,
    image_size: tuple[int, int] | None,
    message: str,
    link_text: str,
    link_href: str,
) -> str:
    """Render the share page. Every value is HTML-escaped here."""
    esc = html.escape
    meta = [
        f'<meta property="og:site_name" content="{esc(SITE_NAME)}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        '<meta property="og:type" content="website">',
        f'<meta name="description" content="{esc(description)}">',
    ]
    if page_url:
        meta.append(f'<meta property="og:url" content="{esc(page_url)}">')
    if image_url:
        meta.append(f'<meta property="og:image" content="{esc(image_url)}">')
        if image_size:
            meta.append(f'<meta property="og:image:width" content="{image_size[0]}">')
            meta.append(f'<meta property="og:image:height" content="{image_size[1]}">')
        meta.append(f'<meta property="og:image:alt" content="{esc(title)}">')
        meta.append('<meta name="twitter:card" content="summary_large_image">')
    else:
        meta.append('<meta name="twitter:card" content="summary">')
    script = ""
    if target:
        # target is built only from validated UUIDs, so it needs no escaping
        # inside the script; json.dumps adds the quotes.
        script = f"<script>location.replace({json.dumps(target)});</script>"
    page_title = title if title == SITE_NAME else f"{title} \u2013 {SITE_NAME}"
    head = "\n".join(meta)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(page_title)}</title>
{head}
<style>
body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: #23262b; color: #f2f2f2; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
main {{ text-align: center; padding: 24px; }}
a {{ display: inline-block; margin-top: 12px; padding: 12px 20px; border-radius: 22px;
  background: rgba(255, 255, 255, 0.14); color: #fff; text-decoration: none; font-weight: 600; }}
</style>
</head>
<body>
<main>
<p>{esc(message)}</p>
<a href="{esc(link_href)}">{esc(link_text)}</a>
</main>
{script}
</body>
</html>
"""


@app.get("/gallery/share", response_class=HTMLResponse)
async def share_page(request: Request, p: str | None = None, i: str | None = None):
    """Link-preview page for a shared album (?p=) or photo (?p=&i=).

    Unknown or malformed IDs get a friendly "isn't available" page (404) with
    a link to the gallery. If PhotoShare can't be reached, the page still
    sends people on to the gallery, just with a plain preview, and isn't
    cached so the next preview attempt can pick up the real one.
    """
    album_id = _valid_uuid(p)
    image_id = _valid_uuid(i) if i is not None else None
    origin = _public_origin(request)
    # Relative to /gallery/share, "../" is the gallery page, so this also
    # works when the site lives under a path prefix.
    library = "../"

    def not_found(message: str, link_text: str = "Open the gallery", link_href: str = library):
        body = _share_html(
            title=SITE_NAME,
            description=message,
            target=None,
            page_url=None,
            image_url=None,
            image_size=None,
            message=message,
            link_text=link_text,
            link_href=link_href,
        )
        return HTMLResponse(body, status_code=404, headers={"Cache-Control": SHARE_CACHE_CONTROL})

    if album_id is None or (i is not None and image_id is None):
        return not_found("This link isn't valid.")

    if image_id:
        (album_state, album), (image_state, image) = await asyncio.gather(
            _share_lookup(f"/api/v2/portfolios/{album_id}"),
            _share_lookup(f"/api/v2/images/{image_id}"),
        )
    else:
        album_state, album = await _share_lookup(f"/api/v2/portfolios/{album_id}")
        image_state, image = "ok", None

    if album_state == "missing":
        return not_found("This album isn't available any more.")
    album_target = f"{library}?p={album_id}"
    if image_id and image_state == "missing":
        return not_found("This photo isn't available any more.", "Open the album", album_target)

    target = album_target + (f"&i={image_id}" if image_id else "")
    share_path = f"/gallery/share?p={album_id}" + (f"&i={image_id}" if image_id else "")
    page_url = f"{origin}{share_path}" if origin else None

    if album_state == "error" or image_state == "error":
        # Can't describe the album or photo right now; still pass people on.
        body = _share_html(
            title=SITE_NAME,
            description="Photos",
            target=target,
            page_url=page_url,
            image_url=None,
            image_size=None,
            message="Opening the gallery\u2026",
            link_text="Open the gallery",
            link_href=target,
        )
        return HTMLResponse(body, headers={"Cache-Control": "no-store"})

    album_name = str(album.get("name") or "Album")
    preview_id: str | None
    if image:
        title = str(image.get("name") or image.get("alternate_name") or "Photo")
        description = f"Photo from {album_name}"
        preview_id = image_id
        preview_size = _preview_size(image)
        link_text = "View the photo"
    else:
        title = album_name
        description = _photo_count_text(album)
        icon = album.get("icon_image_id")
        preview_id = str(icon) if icon and _valid_uuid(str(icon)) else None
        # The album's cover isn't looked up separately, so its size isn't known.
        preview_size = None
        link_text = "View the album"

    image_url = None
    if preview_id and origin:
        image_url = (
            f"{origin}/gallery/image/{preview_id}/thumb"
            f"?width={PREVIEW_WIDTH}&height={PREVIEW_HEIGHT}"
        )

    body = _share_html(
        title=title,
        description=description,
        target=target,
        page_url=page_url,
        image_url=image_url,
        image_size=preview_size if image_url else None,
        message=f"Opening {title}\u2026",
        link_text=link_text,
        link_href=target,
    )
    return HTMLResponse(body, headers={"Cache-Control": SHARE_CACHE_CONTROL})


@app.get("/gallery/health")
async def health():
    """Own health check -- does not require reaching PhotoShare."""
    return {"status": "ok", "upstream": UPSTREAM_BASE}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=CONFIG["proxy_host"], port=CONFIG["proxy_port"])
