"""
/api/v2/portfolios/* routes (v3.0 §5.2, How It Works Guide §2-4).
Auth is applied at router-inclusion time in api/main.py, not per-route.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from photoshare.api.dependencies import get_portfolio_service
from photoshare.api.models import PortfolioOut
from photoshare.services.portfolio_service import PortfolioService

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


@router.get("/root", response_model=PortfolioOut)
def get_root_portfolio(service: PortfolioService = Depends(get_portfolio_service)) -> PortfolioOut:
    """How It Works Guide §2. Should never 404 once startup has completed
    a first rescan -- the root row always exists."""
    return service.get_root_portfolio()


@router.get("/{port_uuid}", response_model=PortfolioOut)
def get_portfolio(
    port_uuid: UUID, service: PortfolioService = Depends(get_portfolio_service)
) -> PortfolioOut:
    """How It Works Guide §3. Raises NotFoundError -> 404 PORTFOLIO_NOT_FOUND
    via the registered exception handler if port_uuid doesn't exist."""
    return service.get_portfolio(port_uuid)


@router.get("/{port_uuid}/children", response_model=list[PortfolioOut])
def list_children(
    port_uuid: UUID, service: PortfolioService = Depends(get_portfolio_service)
) -> list[PortfolioOut]:
    """How It Works Guide §4. Parent-not-found -> 404; parent-with-no-children
    -> 200 with []. Both cases return distinguishably per the spec."""
    return service.list_children(port_uuid)
