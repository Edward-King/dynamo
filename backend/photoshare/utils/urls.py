"""
Shared URL builders for the four per-image access modes exposed on ImgOut and
the dump-tree export (see thumbnail_url_full_url_analysis.md).

Two access modes, each in full-size and thumbnail form:

  * Dynamic (authenticated) API routes -- always work, run per-request Python
    (auth, lazy thumbnail generation, cache headers):
      - image_full_url        -> /api/v2/images/{id}/full
      - image_thumbnail_url   -> /api/v2/images/{id}/thumb?width=&height=

  * Static (unauthenticated) StaticFiles mounts -- only resolve when
    enable_static_file_serving is on (otherwise 404); no per-request Python,
    no API key required:
      - image_full_static_url      -> /static-photos/{rel_path}
      - image_thumbnail_static_url -> /static-thumbs-{w}x{h}/{shard}/{id}_{w}x{h}.jpg

There is one static-thumbnail mount PER configured size in
cache.thumbnail_sizes (see api/main.py::_mount_static_serving), so a static
thumbnail URL is only valid for a size that has a mount. The builder returns
None for any (width, height) not in that configured list.

Both construction sites (services/image_service.py and
services/export_service.py) build URLs exclusively through these helpers so
the shard convention and path formats never drift between them.
"""
from __future__ import annotations

from uuid import UUID

from photoshare.services.thumbnail_service import shard_for

# Static-mount URL prefixes -- must match the app.mount() prefixes in
# api/main.py.
STATIC_PHOTOS_PREFIX = "/static-photos"


def static_thumbs_prefix(width: int, height: int) -> str:
    """Per-size static-thumbnail mount prefix. MUST stay in sync with the
    app.mount() paths built in api/main.py::_mount_static_serving (one mount
    per configured cache.thumbnail_sizes entry)."""
    return f"/static-thumbs-{width}x{height}"

# Default query-param size for the DYNAMIC /thumb route only (image_thumbnail_url).
# NOTE: this is deliberately decoupled from the static singular-URL defaults
# (cache.default_static_thumbnail_size / default_static_icon_size), which are
# explicit config settings chosen from cache.thumbnail_sizes. Do not reuse
# these constants to resolve static URLs -- the two concerns are independent.
DEFAULT_THUMB_WIDTH = 320
DEFAULT_THUMB_HEIGHT = 240


def image_full_url(img_id: UUID | str) -> str:
    """Dynamic, authenticated full-image route."""
    return f"/api/v2/images/{img_id}/full"


def image_thumbnail_url(
    img_id: UUID | str,
    width: int = DEFAULT_THUMB_WIDTH,
    height: int = DEFAULT_THUMB_HEIGHT,
) -> str:
    """Dynamic, authenticated thumbnail route. The default size is embedded
    explicitly so clients get a directly-usable URL without knowing the
    server default."""
    return f"/api/v2/images/{img_id}/thumb?width={width}&height={height}"


def image_full_static_url(rel_path: str) -> str:
    """Static, unauthenticated full-image URL under the base_root mount.
    Resolves only when enable_static_file_serving is on; 404s otherwise."""
    return f"{STATIC_PHOTOS_PREFIX}/{rel_path}"


def image_thumbnail_static_url(
    img_id: UUID | str,
    width: int,
    height: int,
    thumbnail_sizes: list[tuple[int, int]],
) -> str | None:
    """Static, unauthenticated thumbnail URL under the size-specific
    cache_root/thumbnails mount for (width, height). Returns None if
    (width, height) is not one of the sizes in cache.thumbnail_sizes --
    only configured sizes get a static mount. Resolves only when
    enable_static_file_serving is on AND the thumbnail already exists on
    disk (static serving cannot lazily generate)."""
    if (width, height) not in thumbnail_sizes:
        return None
    shard = shard_for(img_id if isinstance(img_id, UUID) else UUID(str(img_id)))
    return f"{static_thumbs_prefix(width, height)}/{shard}/{img_id}_{width}x{height}.jpg"
