"""API routers mounted under /api/v2."""
from photoshare.api.routers import admin, images, portfolios, search

__all__ = ["admin", "images", "portfolios", "search"]
