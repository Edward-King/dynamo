"""
PortfolioService (v3.0 §3.3).

Read-only, stateless service that assembles PortfolioOut view models from
the MetadataRepository. Never touches the filesystem directly -- all
filesystem access happens during rescan(), not on the read path (§4.7
rationale: portfolio reads must be fast and DB-only).
"""
from __future__ import annotations

from uuid import UUID

from photoshare.api.models import PortfolioOut
from photoshare.services.metadata_repository import (
    PORTFOLIO_NOT_FOUND,
    MetadataRepository,
    NotFoundError,
)
from photoshare.utils.urls import image_thumbnail_static_url, image_thumbnail_url


class PortfolioService:
    def __init__(
        self,
        repo: MetadataRepository,
        thumbnail_sizes: list[tuple[int, int]],
        default_static_icon_size: tuple[int, int],
    ) -> None:
        self._repo = repo
        self._thumbnail_sizes = thumbnail_sizes
        self._default_static_icon_size = default_static_icon_size

    def _row_to_out(self, row) -> PortfolioOut:
        port_uuid = UUID(row["id"])
        children_rows = self._repo.list_children_rows(port_uuid)
        icon_id = UUID(row["icon_image_id"]) if row["icon_image_id"] else None
        # The icon's static URL resolves to the explicitly-configured
        # default_static_icon_size (independent of the per-image default).
        first_w, first_h = self._default_static_icon_size
        return PortfolioOut(
            id=port_uuid,
            name=row["name"],
            virtual=bool(row["virtual"]),
            rel_path=row["rel_path"],
            tags=self._repo._get_portfolio_tags(port_uuid),
            description=row["description"] or "",
            icon_image_id=icon_id,
            icon_thumbnail_url=image_thumbnail_url(icon_id) if icon_id else None,
            icon_thumbnail_static_url=(
                image_thumbnail_static_url(
                    icon_id, first_w, first_h, self._thumbnail_sizes
                )
                if icon_id
                else None
            ),
            direct_image_count=self._repo.direct_image_count(port_uuid),
            total_image_count=self._repo.total_image_count(port_uuid),
            children=[UUID(r["id"]) for r in children_rows],
        )

    def get_root_portfolio(self) -> PortfolioOut:
        """§How-It-Works-Guide #2. The root portfolio always exists once the
        first rescan has run -- rescan always creates a row for base_root."""
        row = self._repo.get_root_portfolio_row()
        if row is None:
            raise NotFoundError(
                PORTFOLIO_NOT_FOUND,
                "No root portfolio exists yet -- has an initial rescan run?",
            )
        return self._row_to_out(row)

    def get_portfolio(self, port_uuid: UUID) -> PortfolioOut:
        """§How-It-Works-Guide #3. Raises NotFoundError(PORTFOLIO_NOT_FOUND)
        if port_uuid doesn't exist -- caller (router) maps this to 404."""
        meta = self._repo.get_portfolio_metadata(port_uuid)  # raises if absent
        return self._row_to_out(
            {
                "id": str(meta.id),
                "name": meta.name,
                "virtual": int(meta.virtual),
                "rel_path": meta.rel_path,
                "description": meta.description,
                "icon_image_id": str(meta.icon_image_id) if meta.icon_image_id else None,
            }
        )

    def list_children(self, port_uuid: UUID) -> list[PortfolioOut]:
        """§How-It-Works-Guide #4. Parent existence is checked first so a
        missing parent 404s instead of silently returning []."""
        if not self._repo.portfolio_exists(port_uuid):
            raise NotFoundError(PORTFOLIO_NOT_FOUND, f"No portfolio found for id {port_uuid}")
        rows = self._repo.list_children_rows(port_uuid)
        return [self._row_to_out(row) for row in rows]

    def portfolio_exists(self, port_uuid: UUID) -> bool:
        return self._repo.portfolio_exists(port_uuid)

    def is_virtual(self, port_uuid: UUID) -> bool:
        meta = self._repo.get_portfolio_metadata(port_uuid)
        return meta.virtual
