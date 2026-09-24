"""
API-facing Pydantic response/request models (v3.0 §5).

These are the wire-format shapes returned by FastAPI routes. They are kept
separate from the internal dataclasses in `services/metadata_models.py` and
`storage/models.py` so that internal refactors never leak into the public
API contract, and vice versa.
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Uniform error envelope for every non-2xx response (v3.0 §5.3,
    How It Works Guide §10). `detail` is populated only for validation
    errors (e.g. normalized 422s)."""

    code: str
    message: str
    detail: Optional[object] = None


class PortfolioOut(BaseModel):
    id: UUID
    name: str
    virtual: bool
    rel_path: str
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    icon_image_id: Optional[UUID] = None
    # Both None exactly when icon_image_id is None; both populated whenever an
    # icon is resolved. icon_thumbnail_url is the dynamic authenticated route;
    # icon_thumbnail_static_url is the unauthenticated StaticFiles path (404s
    # unless enable_static_file_serving is on). A portfolio can genuinely have
    # no icon, so unlike ImgOut's always-populated URLs these are Optional.
    icon_thumbnail_url: Optional[str] = None
    icon_thumbnail_static_url: Optional[str] = None
    direct_image_count: int
    total_image_count: int
    children: list[UUID] = Field(default_factory=list)


class ImgOut(BaseModel):
    id: UUID
    name: str
    rel_path: str
    width: int
    height: int
    size_bytes: int
    mime_type: str
    file_modified_at: datetime
    taken_at: datetime
    tags: list[str] = Field(default_factory=list)
    # Four per-image access URLs (see utils/urls.py). full_url/thumbnail_url
    # are the dynamic, authenticated API routes (always work). The two
    # *_static_url fields are unauthenticated StaticFiles paths that only
    # resolve when enable_static_file_serving is on -- they are ALWAYS
    # populated strings regardless of that flag (a client can rely on the
    # field existing), but 404 when the mount isn't registered.
    full_url: str
    thumbnail_url: str
    full_static_url: str
    # Backward-compatible singular field: always the static URL for the FIRST
    # configured cache.thumbnail_sizes entry (currently 320x240).
    thumbnail_static_url: str
    # One static URL per configured size, keyed by "{width}x{height}" (e.g.
    # "320x240", "800x600"). Config-derived, so it grows automatically when a
    # size is added to cache.thumbnail_sizes. Equals thumbnail_static_url for
    # the first size.
    thumbnail_static_urls: dict[str, str] = Field(default_factory=dict)
    # Populated only when the image is returned as a member of a virtual
    # portfolio (§4.7) -- otherwise both are None.
    alternate_name: Optional[str] = None
    sort_order: Optional[int] = None


class PagedResult(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    has_next: bool


class ViewerConfig(BaseModel):
    """§3.3 ViewingService.viewer_config -- client-facing display hints
    (tile threshold, thumbnail sizes) so viewers don't hardcode them."""

    thumbnail_sizes: list[list[int]]
    tile_threshold_width: int
    tile_threshold_height: int


class AdjacentImages(BaseModel):
    previous: Optional[UUID] = None
    next: Optional[UUID] = None


class RescanRequest(BaseModel):
    port_uuid: Optional[UUID] = None
    recursive: bool = True


class RescanResultOut(BaseModel):
    started_at: datetime
    completed_at: datetime
    portfolios_added: int
    portfolios_moved: int
    portfolios_removed: int
    images_added: int
    images_moved: int
    images_removed: int
    errors: list[str] = Field(default_factory=list)
    duration_ms: int


class PreheatThumbnailsRequest(BaseModel):
    port_uuid: Optional[UUID] = None
    # Target thumbnail dimensions, matching get_image_thumb's width/height
    # query-param convention exactly (default 320x240, bounds [16, 4096]
    # per dimension). Using the same pair here means preheated files land at
    # the same {uuid}_{w}x{h}.jpg names the app's own thumbnail URLs request.
    width: int = Field(320, ge=16, le=4096)
    height: int = Field(240, ge=16, le=4096)
    recursive: bool = True


class PreheatResultOut(BaseModel):
    images_processed: int
    thumbnails_created: int
    duration_ms: int


class CleanupResultOut(BaseModel):
    thumbnails_removed: int
    duration_ms: int


class SetRootRequest(BaseModel):
    path: str


class SetRootResultOut(BaseModel):
    base_root: str
    rescan_triggered: bool
    duration_ms: int


class HealthOut(BaseModel):
    status: str = "ok"
    root_id: str
    schema_version: int
