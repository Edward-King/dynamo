"""/api/v2/search/* routes (v3.0 §3.3 SearchService)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from photoshare.api.dependencies import get_search_service
from photoshare.api.models import ImgOut, PortfolioOut
from photoshare.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/portfolios", response_model=list[PortfolioOut])
def search_portfolios(
    tag: str = Query(..., min_length=1),
    service: SearchService = Depends(get_search_service),
) -> list[PortfolioOut]:
    return service.search_portfolios_by_tag(tag)


@router.get("/images", response_model=list[ImgOut])
def search_images(
    tag: str = Query(..., min_length=1),
    service: SearchService = Depends(get_search_service),
) -> list[ImgOut]:
    return service.search_images_by_tag(tag)
