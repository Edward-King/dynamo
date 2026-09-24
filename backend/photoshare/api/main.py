"""
FastAPI application factory and startup sequence (v3.0 §5,
How It Works Guide §1).

Startup sequence:
  1. Load configuration from the required --config path (fails fast).
  2. Ensure cache_root exists; load-or-create installation.json (root_id
     generated exactly once, ever).
  3. Open/create metadata.db; apply the DDL idempotently.
  4. If rescan.on_startup is true, run a full MetadataService.rescan()
     before accepting traffic.
  5. Mount routers under /api/v2 with the auth dependency applied at the
     router level (except /admin/health).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from photoshare.api.dependencies import get_current_principal
from photoshare.api.errors import register_exception_handlers
from photoshare.api.routers import admin, images, portfolios, search
from photoshare.auth.dependency import build_get_current_principal
from photoshare.auth.header_api_key import HeaderApiKeyScheme
from photoshare.auth.sqlite_credential_store import SqliteCredentialStore
from photoshare.cache.db import Database
from photoshare.config.schema import Settings, load_settings
from photoshare.identity.models import load_or_create_installation
from photoshare.services.image_service import ImageService
from photoshare.services.metadata_repository import MetadataRepository
from photoshare.services.metadata_service import MetadataService
from photoshare.services.portfolio_service import PortfolioService
from photoshare.services.search_service import SearchService
from photoshare.services.thumbnail_service import ThumbnailService
from photoshare.services.viewing_service import ViewingService
from photoshare.storage.filesystem_adapter import FilesystemStorageAdapter
from photoshare.utils.urls import static_thumbs_prefix

logger = logging.getLogger("photoshare")

# Used by ``python -m photoshare`` to pass its required --config value to
# Uvicorn worker subprocesses.  It is intentionally separate from the normal
# Pydantic settings overrides, which use the PHOTOSHARE_ prefix and ``__`` for
# nesting.
CONFIG_PATH_ENV_VAR = "PHOTOSHARE_CONFIG_PATH"
APP_FACTORY_TARGET = "photoshare.api.main:app_factory"


def app_factory() -> FastAPI:
    """Create an app for Uvicorn's import-string worker factory mode.

    Uvicorn requires an import string, rather than an already-created ASGI
    object, when ``workers > 1``.  The parent CLI validates --config before
    launching Uvicorn and sets this environment variable so each spawned
    worker builds its own app from precisely the same configuration.
    """
    config_path = os.environ.get(CONFIG_PATH_ENV_VAR)
    if not config_path:
        raise RuntimeError(
            f"{CONFIG_PATH_ENV_VAR} is required when using the Uvicorn app factory. "
            "Start the service with `python -m photoshare --config <path>`."
        )
    return create_app(load_settings(config_path))


def _build_app_state(app: FastAPI, settings: Settings) -> None:
    """Step 2-3 of the startup sequence, plus wiring every service/repo
    the routers depend on onto app.state."""
    cache_root = Path(settings.cache.cache_root)
    installation = load_or_create_installation(cache_root)

    db_path = cache_root / "metadata.db"
    db = Database(db_path, busy_timeout_ms=settings.database.busy_timeout_ms)

    storage = FilesystemStorageAdapter(
        base_root=settings.storage.base_root,
        allowed_extensions=settings.storage.allowed_extensions,
    )

    repo = MetadataRepository(db)

    rescan_log_path = Path(settings.logging.rescan_log_path)
    metadata_service = MetadataService(
        db=db,
        storage=storage,
        root_id=installation.root_id,
        cache_root=cache_root,
        rescan_log_path=rescan_log_path,
        cycle_detection_enabled=settings.rescan.cycle_detection,
    )

    portfolio_service = PortfolioService(
        repo, settings.cache.thumbnail_sizes, settings.cache.default_static_icon_size
    )
    image_service = ImageService(
        repo,
        storage,
        settings.cache.thumbnail_sizes,
        settings.cache.default_static_thumbnail_size,
    )
    viewing_service = ViewingService(repo, settings.cache)
    thumbnail_service = ThumbnailService(cache_root)
    search_service = SearchService(db, portfolio_service, image_service)

    scheme = HeaderApiKeyScheme(header_name=settings.auth.api_key_header)
    credential_store = SqliteCredentialStore(db)

    app.state.settings = settings
    app.state.installation = installation
    app.state.db = db
    app.state.storage = storage
    app.state.repo = repo
    app.state.metadata_service = metadata_service
    app.state.portfolio_service = portfolio_service
    app.state.image_service = image_service
    app.state.viewing_service = viewing_service
    app.state.thumbnail_service = thumbnail_service
    app.state.search_service = search_service
    app.state.get_current_principal = build_get_current_principal(scheme, credential_store)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _build_app_state(app, settings)

        if settings.rescan.on_startup:
            logger.info("Running full rescan on startup (rescan.on_startup=true)...")
            result = app.state.metadata_service.rescan(port_uuid=None, recursive=True)
            app.state.repo.insert_rescan_history(result, scope_port_uuid=None, recursive=True)
            logger.info(
                "Startup rescan complete: +%d/-%d images, +%d/-%d portfolios (%dms)",
                result.images_added,
                result.images_removed,
                result.portfolios_added,
                result.portfolios_removed,
                result.duration_ms,
            )

        yield

        app.state.db.close()

    app = FastAPI(
        title="PhotoShare API",
        version="3.0.0",
        lifespan=lifespan,
    )

    if settings.server.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)

    # Auth dependency applied at router-inclusion level (auth hooks design
    # doc) -- impossible to forget on a new route added to these routers.
    app.include_router(
        portfolios.router,
        prefix="/api/v2",
        dependencies=[Depends(get_current_principal)],
    )
    app.include_router(
        images.router,
        prefix="/api/v2",
        dependencies=[Depends(get_current_principal)],
    )
    app.include_router(
        search.router,
        prefix="/api/v2",
        dependencies=[Depends(get_current_principal)],
    )
    app.include_router(
        admin.admin_router,
        prefix="/api/v2",
        dependencies=[Depends(get_current_principal)],
    )
    # /api/v2/admin/health -- no auth dependency, per auth hooks design doc.
    app.include_router(admin.admin_no_auth_router, prefix="/api/v2")

    _mount_static_serving(app, settings)

    return app


def _mount_static_serving(app: FastAPI, settings: Settings) -> None:
    """Opt-in, UNAUTHENTICATED static file mounts (§2.2, security tradeoff).

    When enable_static_file_serving is on, base_root is exposed at
    /static-photos, and cache_root/thumbnails is exposed once PER configured
    size in cache.thumbnail_sizes at /static-thumbs-{width}x{height} (e.g.
    /static-thumbs-320x240, /static-thumbs-800x600). Every thumbnail mount
    points at the SAME thumbnails directory -- they differ only in which
    {uuid}_{w}x{h}.jpg filename the client requests under each. The mount set
    is therefore fully config-derived: adding a size to cache.thumbnail_sizes
    exposes a new static mount at the next startup with no code change. These
    run NO per-request Python: no auth check, no X-API-Key, no cache-header
    logic. Enabling this flag makes every image and thumbnail fetchable by
    anyone with the URL -- an explicit, opt-in departure from the
    all-endpoints-require-auth model (§5.1). Left off by default (which
    disables EVERY derived mount, not just the default size); the *_url fields
    on ImgOut are still populated but the *_static_url ones 404 until this is
    turned on.

    KNOWN LIMITATION: a StaticFiles mount binds its directory at mount time
    (here, at app-factory/startup). Changing base_root at runtime via
    POST /api/v2/admin/root updates the DYNAMIC API routes immediately (via
    the rescan/reconciliation path), but the static mount continues to serve
    the base_root captured at startup. A server restart is required for
    /static-photos to follow a root change. Live remounting is intentionally
    not implemented (Starlette does not support it without a restart)."""
    if not settings.enable_static_file_serving:
        return

    base_root = Path(settings.storage.base_root)
    thumbs_dir = Path(settings.cache.cache_root) / "thumbnails"
    # ThumbnailService only creates this at lifespan time; StaticFiles with
    # check_dir=True raises if the directory is missing at mount time, so
    # ensure it exists here in the synchronous factory.
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    app.mount(
        "/static-photos",
        StaticFiles(directory=base_root),
        name="static-photos",
    )
    for width, height in settings.cache.thumbnail_sizes:
        app.mount(
            static_thumbs_prefix(width, height),
            StaticFiles(directory=thumbs_dir),
            name=f"static-thumbs-{width}x{height}",
        )
