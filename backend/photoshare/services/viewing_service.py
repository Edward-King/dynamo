"""
ViewingService (v3.0 §3.3).

Client-facing display hints (tile threshold, thumbnail sizes) and
previous/next navigation within a portfolio's image ordering, so viewer
clients never hardcode these values.
"""
from __future__ import annotations

from uuid import UUID

from photoshare.api.models import AdjacentImages, ViewerConfig
from photoshare.config.schema import CacheConfig
from photoshare.services.metadata_repository import (
    IMAGE_NOT_FOUND,
    MetadataRepository,
    NotFoundError,
)


class ViewingService:
    def __init__(self, repo: MetadataRepository, cache_config: CacheConfig) -> None:
        self._repo = repo
        self._cache_config = cache_config

    def viewer_config(self) -> ViewerConfig:
        return ViewerConfig(
            thumbnail_sizes=[list(size) for size in self._cache_config.thumbnail_sizes],
            tile_threshold_width=self._cache_config.tile_threshold_px.width,
            tile_threshold_height=self._cache_config.tile_threshold_px.height,
        )

    def adjacent_images(self, port_uuid: UUID, img_uuid: UUID, is_virtual: bool) -> AdjacentImages:
        """Uses the same ordering as list_images_for_portfolio (name sort
        for physical portfolios, sort_order for virtual ones) so adjacency
        matches what the client already sees when browsing."""
        rows = self._repo.list_images_for_portfolio(port_uuid, is_virtual)
        ids = [row["id"] for row in rows]
        target = str(img_uuid)
        if target not in ids:
            raise NotFoundError(IMAGE_NOT_FOUND, f"No image found for id {img_uuid} in portfolio {port_uuid}")
        idx = ids.index(target)
        prev_id = UUID(ids[idx - 1]) if idx > 0 else None
        next_id = UUID(ids[idx + 1]) if idx < len(ids) - 1 else None
        return AdjacentImages(previous=prev_id, next=next_id)
