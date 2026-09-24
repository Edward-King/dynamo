"""
ImageService (v3.0 §3.3).

Handles paginated image listing and streaming lookups. Identity resolution
(img_uuid -> current rel_path) always goes through the Identity Registry,
never a stored/cached path on the images row directly, per §3.6.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional
from uuid import UUID

from photoshare.api.models import ImgOut, PagedResult
from photoshare.services.metadata_repository import (
    IMAGE_NOT_FOUND,
    MetadataRepository,
    NotFoundError,
)
from photoshare.storage.errors import PathOutsideRootError
from photoshare.storage.filesystem_adapter import FilesystemStorageAdapter
from photoshare.utils.urls import (
    image_full_static_url,
    image_full_url,
    image_thumbnail_static_url,
    image_thumbnail_url,
)

MAX_PAGE_SIZE = 200


@dataclass
class ResolvedImage:
    real_path: Path
    content_hash: str
    file_modified_at: str


class ImageService:
    def __init__(
        self,
        repo: MetadataRepository,
        storage: FilesystemStorageAdapter,
        thumbnail_sizes: list[tuple[int, int]],
        default_static_thumbnail_size: tuple[int, int],
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._thumbnail_sizes = thumbnail_sizes
        self._default_static_thumbnail_size = default_static_thumbnail_size

    def _row_to_out(self, row) -> ImgOut:
        img_id = UUID(row["id"])
        # The singular field resolves to the explicitly-configured
        # default_static_thumbnail_size; the dict carries every configured
        # size (all guaranteed to have a mount).
        first_w, first_h = self._default_static_thumbnail_size
        thumbnail_static_urls = {
            f"{w}x{h}": url
            for (w, h) in self._thumbnail_sizes
            if (url := image_thumbnail_static_url(img_id, w, h, self._thumbnail_sizes))
            is not None
        }
        return ImgOut(
            id=img_id,
            name=row["name"],
            rel_path=row["rel_path"],
            width=row["width"],
            height=row["height"],
            size_bytes=row["size_bytes"],
            mime_type=row["mime_type"],
            file_modified_at=row["file_modified_at"],
            taken_at=row["taken_at"],
            tags=self._repo._get_image_tags(img_id),
            full_url=image_full_url(img_id),
            thumbnail_url=image_thumbnail_url(img_id),
            full_static_url=image_full_static_url(row["rel_path"]),
            thumbnail_static_url=image_thumbnail_static_url(
                img_id, first_w, first_h, self._thumbnail_sizes
            ),
            thumbnail_static_urls=thumbnail_static_urls,
            alternate_name=row["alternate_name"] if "alternate_name" in row.keys() else None,
            sort_order=row["sort_order"] if "sort_order" in row.keys() else None,
        )

    def list_images(
        self, port_uuid: UUID, is_virtual: bool, page: int, page_size: int
    ) -> PagedResult[ImgOut]:
        """§How-It-Works-Guide #5. page_size is clamped server-side to
        MAX_PAGE_SIZE (200) regardless of what the caller requests."""
        page_size = min(page_size, MAX_PAGE_SIZE)
        all_rows = self._repo.list_images_for_portfolio(port_uuid, is_virtual)
        total = len(all_rows)
        start = (page - 1) * page_size
        end = start + page_size
        page_rows = all_rows[start:end]
        items = [self._row_to_out(row) for row in page_rows]
        return PagedResult[ImgOut](
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            has_next=(page * page_size) < total,
        )

    def get_image_metadata(self, img_uuid: UUID) -> ImgOut:
        meta = self._repo.get_image_metadata(img_uuid)  # raises NotFoundError if absent
        row = self._repo.get_image_row(img_uuid)
        return self._row_to_out(row)

    def resolve_for_streaming(self, img_uuid: UUID) -> ResolvedImage:
        """
        §How-It-Works-Guide #6. Resolution order:
          1. Identity Registry lookup by img_uuid -> normalized_rel_path.
             No entry (deleted, or stale/bad client UUID) -> 404 IMAGE_NOT_FOUND.
          2. Resolve that rel_path against the storage adapter. If the real
             path no longer exists on disk -> 404 IMAGE_NOT_FOUND (same code;
             internal cause differs, logged server-side only).
          3. If the resolved path structurally falls outside base_root
             (defensive check only) -> PathOutsideRootError, mapped to 403
             by the router.
        """
        rel_path = self._repo.resolve_path(img_uuid)
        if rel_path is None:
            raise NotFoundError(IMAGE_NOT_FOUND, f"No image found for id {img_uuid}")

        try:
            real_path = self._storage.resolve(rel_path)
        except PathOutsideRootError:
            raise  # router maps this to 403 PATH_OUTSIDE_ROOT

        if not real_path.is_file():
            raise NotFoundError(IMAGE_NOT_FOUND, f"Image file missing on disk: {rel_path}")

        row = self._repo.get_image_row(img_uuid)
        if row is None:
            raise NotFoundError(IMAGE_NOT_FOUND, f"No image found for id {img_uuid}")

        return ResolvedImage(
            real_path=real_path,
            content_hash=row["content_hash"],
            file_modified_at=row["file_modified_at"],
        )

    def open_stream(self, real_path: Path) -> BinaryIO:
        return open(real_path, "rb")
