"""
/api/v2/admin/* routes (v3.0 §6, How It Works Guide §8).

admin_no_auth_router carries only /health, mounted with NO auth dependency
(auth hooks design doc: "/api/v2/admin/health is mounted on a separate
router with no auth dependency"). admin_router carries the rest and is
mounted with the same auth dependency as every other router -- see the
KNOWN INTERIM LIMITATION note in auth/dependency.py: any valid API key can
currently call these, not just an "admin" key, since scopes don't exist yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from photoshare.api.dependencies import (
    get_db,
    get_image_service,
    get_metadata_service,
    get_portfolio_service,
    get_repo,
    get_settings,
    get_storage,
    get_thumbnail_service,
)
from photoshare.api.models import (
    CleanupResultOut,
    HealthOut,
    PreheatResultOut,
    PreheatThumbnailsRequest,
    RescanRequest,
    RescanResultOut,
    SetRootRequest,
    SetRootResultOut,
)
from photoshare.cache.db import Database
from photoshare.config.schema import Settings
from photoshare.identity.models import load_or_create_installation
from photoshare.services import export_service
from photoshare.services.image_service import ImageService
from photoshare.services.metadata_repository import (
    PORTFOLIO_NOT_FOUND,
    MetadataRepository,
    NotFoundError,
)
from photoshare.services.metadata_service import MetadataService
from photoshare.services.portfolio_service import PortfolioService
from photoshare.services.thumbnail_service import ThumbnailService
from photoshare.storage.errors import InvalidRootPathError
from photoshare.storage.filesystem_adapter import FilesystemStorageAdapter

admin_router = APIRouter(prefix="/admin", tags=["admin"])
admin_no_auth_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.post("/rescan", response_model=RescanResultOut)
def trigger_rescan(
    body: RescanRequest,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    metadata_service: MetadataService = Depends(get_metadata_service),
) -> RescanResultOut:
    """How It Works Guide §8. Runs synchronously within the request (v1
    simplest-correct behavior; flagged as a future scaling note, not built
    now). Every call appends to rescan_history regardless of outcome --
    only a hard exception (e.g. cache_root unwritable) prevents that row,
    surfacing as 500 FILESYSTEM_PERMISSION_ERROR via the exception handler."""
    if body.port_uuid is not None and not portfolio_service.portfolio_exists(body.port_uuid):
        raise NotFoundError(PORTFOLIO_NOT_FOUND, f"No portfolio found for id {body.port_uuid}")

    result = metadata_service.rescan(port_uuid=body.port_uuid, recursive=body.recursive)
    return RescanResultOut(
        started_at=result.started_at,
        completed_at=result.completed_at,
        portfolios_added=result.portfolios_added,
        portfolios_moved=result.portfolios_moved,
        portfolios_removed=result.portfolios_removed,
        images_added=result.images_added,
        images_moved=result.images_moved,
        images_removed=result.images_removed,
        errors=result.errors,
        duration_ms=result.duration_ms,
    )


@admin_router.post("/preheat-thumbnails", response_model=PreheatResultOut)
def preheat_thumbnails(
    body: PreheatThumbnailsRequest,
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    repo: MetadataRepository = Depends(get_repo),
    image_service: ImageService = Depends(get_image_service),
    thumbnail_service: ThumbnailService = Depends(get_thumbnail_service),
) -> PreheatResultOut:
    """v3.0 §5.2 / §4.4. Force-generates thumbnails ahead of time for every
    image in the target portfolio (its subtree too when recursive=True), at
    the requested `width`x`height` (same convention as GET /images/{id}/thumb;
    default 320x240, bounds [16, 4096] per dimension enforced by the request
    model, out-of-range -> 422). Using the same width/height pair the dynamic
    route uses means preheated files match the {uuid}_{w}x{h}.jpg names the
    app's own thumbnail URLs point at. Reuses the export_service tree walk to
    collect image ids and ThumbnailService's get_or_create per image. Images
    whose file has since disappeared are skipped (not counted).
    thumbnails_created counts only newly-generated thumbnails; already-cached
    ones are counted in images_processed only."""
    started_at = datetime.now(timezone.utc)
    if body.port_uuid is not None and not portfolio_service.portfolio_exists(body.port_uuid):
        raise NotFoundError(PORTFOLIO_NOT_FOUND, f"No portfolio found for id {body.port_uuid}")

    img_uuids = export_service.collect_image_uuids(repo, body.port_uuid, body.recursive)
    images_processed = 0
    thumbnails_created = 0
    for img_uuid in img_uuids:
        try:
            resolved = image_service.resolve_for_streaming(img_uuid)
        except NotFoundError:
            continue
        was_cached = thumbnail_service.is_cached(img_uuid, body.width, body.height)
        thumbnail_service.get_or_create(img_uuid, resolved.real_path, body.width, body.height)
        images_processed += 1
        if not was_cached:
            thumbnails_created += 1

    completed_at = datetime.now(timezone.utc)
    return PreheatResultOut(
        images_processed=images_processed,
        thumbnails_created=thumbnails_created,
        duration_ms=int((completed_at - started_at).total_seconds() * 1000),
    )


@admin_router.post("/cleanup-thumbnails", response_model=CleanupResultOut)
def cleanup_thumbnails(
    repo: MetadataRepository = Depends(get_repo),
    thumbnail_service: ThumbnailService = Depends(get_thumbnail_service),
) -> CleanupResultOut:
    """v3.0 §4.4. Removes cached thumbnails whose Img_UUID no longer has a
    live content row in the Identity Registry (e.g. the image was removed, or
    an orphaned thumbnail directory remains). No request body -- it always
    reconciles the full cache against the current registry. Returns the count
    of files deleted."""
    started_at = datetime.now(timezone.utc)
    valid_img_uuids = repo.all_image_uuids()
    thumbnails_removed = thumbnail_service.cleanup_stale(valid_img_uuids)
    completed_at = datetime.now(timezone.utc)
    return CleanupResultOut(
        thumbnails_removed=thumbnails_removed,
        duration_ms=int((completed_at - started_at).total_seconds() * 1000),
    )


@admin_router.post("/root", response_model=SetRootResultOut)
def set_root(
    body: SetRootRequest,
    settings: Settings = Depends(get_settings),
    storage: FilesystemStorageAdapter = Depends(get_storage),
    metadata_service: MetadataService = Depends(get_metadata_service),
) -> SetRootResultOut:
    """v3.0 §3.6. Sets base_root as a SAFE, RECONCILING operation: root_id
    (and therefore every Img_UUID/Port_UUID) is preserved -- changing the
    root path never re-mints identity. The new path is validated (must exist
    and be a directory) BEFORE any mutation, so a bad path is a no-op 400
    INVALID_ROOT_PATH. On success the shared storage adapter's root is
    updated (propagating to the metadata service, which shares it) and a full
    reconciling rescan is triggered to bring the registry in line with the
    new tree."""
    started_at = datetime.now(timezone.utc)
    candidate = Path(body.path)
    if not candidate.is_dir():
        raise InvalidRootPathError(
            f"Path does not exist or is not a directory: {body.path}"
        )

    storage.set_root(body.path)
    settings.storage.base_root = str(storage.base_root)
    metadata_service.rescan(port_uuid=None, recursive=True)

    completed_at = datetime.now(timezone.utc)
    return SetRootResultOut(
        base_root=str(storage.base_root),
        rescan_triggered=True,
        duration_ms=int((completed_at - started_at).total_seconds() * 1000),
    )


@admin_no_auth_router.get("/health", response_model=HealthOut)
def health(
    settings: Settings = Depends(get_settings),
) -> HealthOut:
    """No auth dependency -- suitable for load balancer / orchestrator
    health checks (auth hooks design doc)."""
    installation = load_or_create_installation(settings.cache.cache_root)
    return HealthOut(status="ok", root_id=installation.root_id, schema_version=installation.schema_version)
