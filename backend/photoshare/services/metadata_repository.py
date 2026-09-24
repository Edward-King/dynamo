"""
MetadataRepository (v3.0 §3.5), SQLite-backed.

get_portfolio_metadata()/get_image_metadata() are the only two metadata
read methods -- naming unified per §1.2 (no separate get_port_metadata).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from photoshare.cache.db import Database
from photoshare.services.metadata_models import DirMeta, ImageMeta

PORTFOLIO_NOT_FOUND = "PORTFOLIO_NOT_FOUND"
IMAGE_NOT_FOUND = "IMAGE_NOT_FOUND"


class NotFoundError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class MetadataRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- portfolios -----------------------------------------------------
    def get_portfolio_metadata(self, port_uuid: UUID) -> DirMeta:
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM portfolios WHERE id = ?", (str(port_uuid),))
            row = cur.fetchone()
        if row is None:
            raise NotFoundError(PORTFOLIO_NOT_FOUND, f"No portfolio found for id {port_uuid}")

        tags = self._get_portfolio_tags(port_uuid)
        return DirMeta(
            id=UUID(row["id"]),
            name=row["name"],
            virtual=bool(row["virtual"]),
            rel_path=row["rel_path"],
            real_path=row["rel_path"],  # real_path reconstructed by caller w/ base_root if needed
            tags=tags,
            description=row["description"] or "",
            icon_image_id=UUID(row["icon_image_id"]) if row["icon_image_id"] else None,
        )

    def get_portfolio_by_path(self, rel_path: str):
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM portfolios WHERE rel_path = ?", (rel_path,))
            return cur.fetchone()

    def get_root_portfolio_row(self):
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM portfolios WHERE parent_id IS NULL LIMIT 1")
            return cur.fetchone()

    def get_portfolio_row(self, port_uuid: UUID):
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM portfolios WHERE id = ?", (str(port_uuid),))
            return cur.fetchone()

    def list_children_rows(self, port_uuid: UUID):
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT * FROM portfolios WHERE parent_id = ? ORDER BY name COLLATE NOCASE",
                (str(port_uuid),),
            )
            return cur.fetchall()

    def portfolio_exists(self, port_uuid: UUID) -> bool:
        with self._db.cursor() as cur:
            cur.execute("SELECT 1 FROM portfolios WHERE id = ?", (str(port_uuid),))
            return cur.fetchone() is not None

    def _get_portfolio_tags(self, port_uuid: UUID) -> list[str]:
        with self._db.cursor() as cur:
            cur.execute("SELECT tag FROM portfolio_tags WHERE port_uuid = ?", (str(port_uuid),))
            return [r["tag"] for r in cur.fetchall()]

    def direct_image_count(self, port_uuid: UUID) -> int:
        with self._db.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM image_locations WHERE port_uuid = ?) +
                    (SELECT COUNT(*) FROM virtual_membership WHERE port_uuid = ?)
                    AS cnt
                """,
                (str(port_uuid), str(port_uuid)),
            )
            return cur.fetchone()["cnt"]

    def total_image_count(self, port_uuid: UUID) -> int:
        with self._db.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE descendant_portfolios(id) AS (
                    SELECT id FROM portfolios WHERE id = ?
                    UNION ALL
                    SELECT p.id
                    FROM portfolios p
                    JOIN descendant_portfolios dp ON p.parent_id = dp.id
                )
                SELECT
                    (SELECT COUNT(*) FROM image_locations WHERE port_uuid IN (SELECT id FROM descendant_portfolios))
                    +
                    (SELECT COUNT(*) FROM virtual_membership WHERE port_uuid IN (SELECT id FROM descendant_portfolios))
                    AS total
                """,
                (str(port_uuid),),
            )
            return cur.fetchone()["total"]

    def child_port_count(self, port_uuid: UUID) -> int:
        with self._db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM portfolios WHERE parent_id = ?", (str(port_uuid),))
            return cur.fetchone()["cnt"]

    # -- images -----------------------------------------------------------
    def get_image_metadata(self, img_uuid: UUID) -> ImageMeta:
        with self._db.cursor() as cur:
            # Content fields live on `images`; placement fields (name,
            # rel_path, file_modified_at) come from one image_locations row.
            # A duplicate appears in several portfolios, so any placement is
            # a valid answer for the image-scoped (not portfolio-scoped) view.
            cur.execute(
                """
                SELECT i.*, l.rel_path, l.name, l.is_symlink, l.file_modified_at
                FROM images i
                LEFT JOIN image_locations l ON l.img_uuid = i.id
                WHERE i.id = ?
                LIMIT 1
                """,
                (str(img_uuid),),
            )
            row = cur.fetchone()
        if row is None:
            raise NotFoundError(IMAGE_NOT_FOUND, f"No image found for id {img_uuid}")

        tags = self._get_image_tags(img_uuid)
        return ImageMeta(
            id=UUID(row["id"]),
            name=row["name"],
            rel_path=row["rel_path"],
            real_path=row["rel_path"],
            width=row["width"],
            height=row["height"],
            size_bytes=row["size_bytes"],
            mime_type=row["mime_type"],
            file_modified_at=datetime.fromisoformat(row["file_modified_at"]),
            taken_at=datetime.fromisoformat(row["taken_at"]),
            tags=tags,
        )

    def image_exists(self, img_uuid: UUID) -> bool:
        with self._db.cursor() as cur:
            cur.execute("SELECT 1 FROM images WHERE id = ?", (str(img_uuid),))
            return cur.fetchone() is not None

    def all_image_uuids(self) -> set[UUID]:
        """Every content-identity Img_UUID currently in the registry (the
        `images` content rows). Used by cleanup-thumbnails to compute the set
        of still-valid thumbnails; a content row is dropped only when its last
        placement is removed, so its presence here means the image is live."""
        with self._db.cursor() as cur:
            cur.execute("SELECT id FROM images")
            return {UUID(r["id"]) for r in cur.fetchall()}

    def get_image_row(self, img_uuid: UUID):
        with self._db.cursor() as cur:
            cur.execute(
                """
                SELECT i.*, l.rel_path, l.name, l.is_symlink, l.file_modified_at
                FROM images i
                LEFT JOIN image_locations l ON l.img_uuid = i.id
                WHERE i.id = ?
                LIMIT 1
                """,
                (str(img_uuid),),
            )
            return cur.fetchone()

    def _get_image_tags(self, img_uuid: UUID) -> list[str]:
        with self._db.cursor() as cur:
            cur.execute("SELECT tag FROM image_tags WHERE img_uuid = ?", (str(img_uuid),))
            return [r["tag"] for r in cur.fetchall()]

    def list_images_for_portfolio(self, port_uuid: UUID, is_virtual: bool):
        with self._db.cursor() as cur:
            if is_virtual:
                cur.execute(
                    """
                    SELECT i.*, l.rel_path, l.name, l.is_symlink, l.file_modified_at,
                           vm.alternate_name, vm.sort_order
                    FROM virtual_membership vm
                    JOIN images i ON i.id = vm.img_uuid
                    LEFT JOIN image_locations l ON l.img_uuid = i.id
                    WHERE vm.port_uuid = ?
                    GROUP BY vm.img_uuid
                    ORDER BY vm.sort_order ASC
                    """,
                    (str(port_uuid),),
                )
            else:
                cur.execute(
                    """
                    SELECT i.*, l.rel_path, l.name, l.is_symlink, l.file_modified_at
                    FROM image_locations l
                    JOIN images i ON i.id = l.img_uuid
                    WHERE l.port_uuid = ?
                    ORDER BY l.name COLLATE NOCASE
                    """,
                    (str(port_uuid),),
                )
            return cur.fetchall()

    # -- identity registry (§3.6) -----------------------------------------
    def find_image_by_content_hash(self, content_hash: str):
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM images WHERE content_hash = ? LIMIT 1", (content_hash,))
            return cur.fetchone()

    def resolve_path(self, uuid_: UUID) -> Optional[str]:
        """Identity Registry lookup (§3.5 Protocol)."""
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT normalized_rel_path FROM identity_registry WHERE uuid = ? AND tombstoned_at IS NULL",
                (str(uuid_),),
            )
            row = cur.fetchone()
        return row["normalized_rel_path"] if row else None

    # -- rescan history -----------------------------------------------------
    def insert_rescan_history(self, result, scope_port_uuid: Optional[UUID], recursive: bool) -> None:
        with self._db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rescan_history (
                    started_at, completed_at, scope_port_uuid, recursive,
                    portfolios_added, portfolios_moved, portfolios_removed,
                    images_added, images_moved, images_removed,
                    errors_json, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.started_at.isoformat(),
                    result.completed_at.isoformat(),
                    str(scope_port_uuid) if scope_port_uuid else None,
                    int(recursive),
                    result.portfolios_added,
                    result.portfolios_moved,
                    result.portfolios_removed,
                    result.images_added,
                    result.images_moved,
                    result.images_removed,
                    json.dumps(result.errors),
                    result.duration_ms,
                ),
            )
