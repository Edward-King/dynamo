"""
Dependency-injection providers for the FastAPI app.

Everything here is built once at app startup (see api/main.py's lifespan)
and stashed on `app.state`; these functions just read it back out for
route handlers via FastAPI's Depends().
"""
from __future__ import annotations

from fastapi import Request

from photoshare.auth.protocols import Principal
from photoshare.cache.db import Database
from photoshare.config.schema import Settings
from photoshare.services.image_service import ImageService
from photoshare.services.metadata_repository import MetadataRepository
from photoshare.services.metadata_service import MetadataService
from photoshare.services.portfolio_service import PortfolioService
from photoshare.services.search_service import SearchService
from photoshare.services.thumbnail_service import ThumbnailService
from photoshare.services.viewing_service import ViewingService
from photoshare.storage.filesystem_adapter import FilesystemStorageAdapter


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_storage(request: Request) -> FilesystemStorageAdapter:
    return request.app.state.storage


def get_repo(request: Request) -> MetadataRepository:
    return request.app.state.repo


def get_metadata_service(request: Request) -> MetadataService:
    return request.app.state.metadata_service


def get_portfolio_service(request: Request) -> PortfolioService:
    return request.app.state.portfolio_service


def get_image_service(request: Request) -> ImageService:
    return request.app.state.image_service


def get_viewing_service(request: Request) -> ViewingService:
    return request.app.state.viewing_service


def get_thumbnail_service(request: Request) -> ThumbnailService:
    return request.app.state.thumbnail_service


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


async def get_current_principal(request: Request) -> Principal:
    """Delegates to the closure built at startup in api/main.py from the
    configured AuthScheme + CredentialStore (auth hooks design doc)."""
    return await request.app.state.get_current_principal(request)
