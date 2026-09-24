"""
/api/v2/portfolios/{port_uuid}/images and /api/v2/images/* routes
(v3.0 §5.2, How It Works Guide §5-7).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, Response

from photoshare.api.dependencies import (
    get_image_service,
    get_portfolio_service,
    get_thumbnail_service,
    get_viewing_service,
)
from photoshare.api.models import AdjacentImages, ImgOut, PagedResult, ViewerConfig
from photoshare.services.image_service import MAX_PAGE_SIZE, ImageService
from photoshare.services.portfolio_service import PortfolioService
from photoshare.services.thumbnail_service import ThumbnailService
from photoshare.services.viewing_service import ViewingService

router = APIRouter(tags=["images"])

CACHE_CONTROL_HEADER = "public, max-age=604800, immutable"


@router.get("/portfolios/{port_uuid}/images", response_model=PagedResult[ImgOut])
def list_images(
    port_uuid: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    image_service: ImageService = Depends(get_image_service),
) -> PagedResult[ImgOut]:
    """How It Works Guide §5. Parent existence checked first (404 if
    missing). page/page_size validated by FastAPI/Pydantic (ge=1 already
    covers the 400 case); page_size additionally clamped server-side."""
    if not portfolio_service.portfolio_exists(port_uuid):
        from photoshare.services.metadata_repository import PORTFOLIO_NOT_FOUND, NotFoundError

        raise NotFoundError(PORTFOLIO_NOT_FOUND, f"No portfolio found for id {port_uuid}")
    is_virtual = portfolio_service.is_virtual(port_uuid)
    return image_service.list_images(port_uuid, is_virtual, page, page_size)


@router.get("/images/{img_uuid}/full")
def get_image_full(
    img_uuid: UUID,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    image_service: ImageService = Depends(get_image_service),
):
    """How It Works Guide §6. Streams the original file with content-hash
    ETag, immutable Cache-Control, and Last-Modified. Honors conditional
    If-None-Match requests with a bare 304 -- a free consequence of
    content-addressed identity."""
    resolved = image_service.resolve_for_streaming(img_uuid)
    etag = f'"{resolved.content_hash}"'

    if if_none_match is not None and if_none_match == etag:
        return Response(status_code=304)

    return FileResponse(
        path=resolved.real_path,
        headers={
            "Cache-Control": CACHE_CONTROL_HEADER,
            "ETag": etag,
            "Last-Modified": resolved.file_modified_at,
        },
    )


@router.get("/images/{img_uuid}/thumb")
def get_image_thumb(
    img_uuid: UUID,
    width: int = Query(320, ge=16, le=4096),
    height: int = Query(240, ge=16, le=4096),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    image_service: ImageService = Depends(get_image_service),
    thumbnail_service: ThumbnailService = Depends(get_thumbnail_service),
):
    """How It Works Guide §7. width/height validated to [16, 4096] by
    Query() constraints (produces a 422 -> normalized ErrorResponse for
    out-of-range values, per the validation_exception_handler)."""
    resolved = image_service.resolve_for_streaming(img_uuid)
    thumb_path = thumbnail_service.get_or_create(img_uuid, resolved.real_path, width, height)
    etag = f'"{resolved.content_hash}_{width}x{height}"'

    if if_none_match is not None and if_none_match == etag:
        return Response(status_code=304)

    return FileResponse(
        path=thumb_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": CACHE_CONTROL_HEADER,
            "ETag": etag,
            "Last-Modified": resolved.file_modified_at,
        },
    )


@router.get("/images/{img_uuid}", response_model=ImgOut)
def get_image_metadata(
    img_uuid: UUID,
    image_service: ImageService = Depends(get_image_service),
) -> ImgOut:
    """How It Works Guide §5. Direct-by-id metadata lookup for a single
    image. Returns the same ImgOut shape as list_images/search_images;
    alternate_name/sort_order are null because this is not resolved through a
    virtual-portfolio placement. Raises NotFoundError (404 IMAGE_NOT_FOUND)
    if the id is unknown. Declared after the /full and /thumb routes so those
    more-specific paths are matched first."""
    return image_service.get_image_metadata(img_uuid)


@router.get("/viewer/config", response_model=ViewerConfig)
def get_viewer_config(viewing_service: ViewingService = Depends(get_viewing_service)) -> ViewerConfig:
    return viewing_service.viewer_config()


@router.get(
    "/portfolios/{port_uuid}/images/{img_uuid}/adjacent",
    response_model=AdjacentImages,
)
def get_adjacent_images(
    port_uuid: UUID,
    img_uuid: UUID,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    viewing_service: ViewingService = Depends(get_viewing_service),
) -> AdjacentImages:
    is_virtual = portfolio_service.is_virtual(port_uuid)
    return viewing_service.adjacent_images(port_uuid, img_uuid, is_virtual)
