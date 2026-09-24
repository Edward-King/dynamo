"""
MetadataService (v3.0 §4.5) -- sole owner of rescan(). Implements the
move/rename detection algorithm walked through step-by-step in the
"How It Works" developer guide, §9.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from photoshare.cache.db import Database
from photoshare.identity.models import (
    compute_img_uuid,
    compute_port_uuid,
    normalize_rel_path,
)
from photoshare.services.meta_json import MetaJsonParseError, parse_meta_json, resolve_icon
from photoshare.services.metadata_models import RescanResult
from photoshare.storage.errors import FilesystemPermissionError
from photoshare.storage.filesystem_adapter import FilesystemStorageAdapter

logger = logging.getLogger("photoshare.rescan")


class MetadataService:
    def __init__(
        self,
        db: Database,
        storage: FilesystemStorageAdapter,
        root_id: str,
        cache_root: Path,
        rescan_log_path: Path,
        cycle_detection_enabled: bool = True,
    ) -> None:
        self._db = db
        self._storage = storage
        self._root_id = root_id
        self._cache_root = cache_root
        self._rescan_log_path = rescan_log_path
        self._cycle_detection_enabled = cycle_detection_enabled

    def rescan(self, port_uuid: Optional[UUID] = None, recursive: bool = True) -> RescanResult:
        """
        Walks the filesystem via StorageAdapter starting at port_uuid's
        rel_path (or base_root if omitted -- full rescan). Cycle detection
        (§2.1) applies. See "How It Works" guide §9 for the move/rename
        detection trace this implements.
        """
        started_at = datetime.now(timezone.utc)
        result = RescanResult(started_at=started_at, completed_at=started_at)

        start_rel_path = ""
        if port_uuid is not None:
            with self._db.cursor() as cur:
                cur.execute("SELECT rel_path FROM portfolios WHERE id = ?", (str(port_uuid),))
                row = cur.fetchone()
            if row is None:
                result.errors.append(f"rescan scope port_uuid {port_uuid} not found; nothing scanned")
                result.completed_at = datetime.now(timezone.utc)
                self._finalize(result, port_uuid, recursive)
                return result
            start_rel_path = row["rel_path"]

        visited_real_dirs: set[str] = set()
        seen_portfolio_rel_paths: set[str] = set()
        seen_image_rel_paths: set[str] = set()

        try:
            self._walk(
                rel_path=start_rel_path,
                parent_id=self._find_parent_id_for(start_rel_path),
                recursive=recursive,
                visited_real_dirs=visited_real_dirs,
                seen_portfolio_rel_paths=seen_portfolio_rel_paths,
                seen_image_rel_paths=seen_image_rel_paths,
                result=result,
            )
            self._reconcile_removed(
                start_rel_path, seen_portfolio_rel_paths, seen_image_rel_paths, result
            )
        except FilesystemPermissionError as exc:
            result.errors.append(f"Filesystem permission error during rescan: {exc}")

        result.completed_at = datetime.now(timezone.utc)
        result.duration_ms = int((result.completed_at - result.started_at).total_seconds() * 1000)
        self._finalize(result, port_uuid, recursive)
        return result

    # -- internal walk ----------------------------------------------------
    def _find_parent_id_for(self, rel_path: str) -> Optional[str]:
        if rel_path == "":
            return None
        with self._db.cursor() as cur:
            cur.execute("SELECT id FROM portfolios WHERE rel_path = ?", (rel_path,))
            row = cur.fetchone()
        return row["id"] if row else None

    def _walk(
        self,
        rel_path: str,
        parent_id: Optional[str],
        recursive: bool,
        visited_real_dirs: set[str],
        seen_portfolio_rel_paths: set[str],
        seen_image_rel_paths: set[str],
        result: RescanResult,
    ) -> None:
        real_dir = self._storage.resolve(rel_path) if rel_path else self._storage.base_root
        real_dir_str = str(real_dir)

        if real_dir_str in visited_real_dirs:
            if self._cycle_detection_enabled:
                logger.warning("SYMLINK_CYCLE_DETECTED at %s", real_dir_str)
                result.errors.append(f"SYMLINK_CYCLE_DETECTED: {rel_path}")
                return
        visited_real_dirs.add(real_dir_str)

        normalized_path = normalize_rel_path(rel_path)
        port_uuid = compute_port_uuid(self._root_id, normalized_path or ".")
        seen_portfolio_rel_paths.add(rel_path)

        try:
            meta = parse_meta_json(real_dir)
        except MetaJsonParseError as exc:
            result.errors.append(str(exc))
            meta = None

        name = (meta.portfolio if meta and meta.portfolio else None) or (real_dir.name or "root")
        description = meta.description if meta else ""
        is_virtual = bool(meta.virtual) if meta else False
        tags = meta.tags if meta else []

        dirs = self._storage.list_dirs(rel_path)
        images = [] if is_virtual else self._storage.list_images(rel_path)

        # icon_image_id is resolved to a candidate Img_UUID here, but that
        # image row may not exist yet (it's inserted below, or may live in
        # a not-yet-visited directory). Insert/update the portfolio row
        # first with icon_image_id=NULL to satisfy the FK constraint, then
        # backfill icon_image_id once the referenced image is known to
        # exist -- avoids an insert-order dependency between portfolios
        # and images during the walk.
        icon_image_id = self._resolve_and_upsert_icon(rel_path, real_dir, meta, images)

        self._upsert_portfolio(
            port_uuid=port_uuid,
            rel_path=rel_path,
            name=name,
            description=description,
            is_virtual=is_virtual,
            parent_id=parent_id,
            icon_image_id=None,
            tags=tags,
            result=result,
        )
        self._upsert_identity_registry(port_uuid, "portfolio", normalized_path or ".", None)

        if is_virtual and meta:
            self._upsert_virtual_membership(port_uuid, meta, rel_path, seen_image_rel_paths, result)
        else:
            for img in images:
                self._upsert_image(port_uuid, img, seen_image_rel_paths, result)

        if icon_image_id is not None:
            self._backfill_icon_image_id(port_uuid, icon_image_id)

        if recursive:
            for d in dirs:
                self._walk(
                    rel_path=d.rel_path,
                    parent_id=str(port_uuid),
                    recursive=True,
                    visited_real_dirs=visited_real_dirs,
                    seen_portfolio_rel_paths=seen_portfolio_rel_paths,
                    seen_image_rel_paths=seen_image_rel_paths,
                    result=result,
                )

    def _resolve_and_upsert_icon(self, rel_path, real_dir, meta, images) -> Optional[str]:
        candidate_names = [img.name for img in images]
        icon_rel_path = resolve_icon(rel_path, real_dir, meta, candidate_names)
        if icon_rel_path is None:
            return None
        try:
            content_hash = self._storage.compute_content_hash(icon_rel_path)
        except Exception:
            return None
        img_uuid = compute_img_uuid(self._root_id, content_hash)
        return str(img_uuid)

    def _upsert_portfolio(
        self, port_uuid, rel_path, name, description, is_virtual, parent_id, icon_image_id, tags, result
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._db.cursor() as cur:
            cur.execute("SELECT id, rel_path FROM portfolios WHERE id = ?", (str(port_uuid),))
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO portfolios (id, rel_path, name, description, virtual,
                        icon_image_id, parent_id, is_symlink, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (str(port_uuid), rel_path, name, description, int(is_virtual),
                     icon_image_id, parent_id, now, now),
                )
                result.portfolios_added += 1
            else:
                moved = existing["rel_path"] != rel_path
                cur.execute(
                    """
                    UPDATE portfolios SET rel_path = ?, name = ?, description = ?, virtual = ?,
                        icon_image_id = ?, parent_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (rel_path, name, description, int(is_virtual), icon_image_id,
                     parent_id, now, str(port_uuid)),
                )
                if moved:
                    result.portfolios_moved += 1

            cur.execute("DELETE FROM portfolio_tags WHERE port_uuid = ?", (str(port_uuid),))
            for tag in tags:
                cur.execute(
                    "INSERT OR IGNORE INTO portfolio_tags (port_uuid, tag) VALUES (?, ?)",
                    (str(port_uuid), tag),
                )

    def _backfill_icon_image_id(self, port_uuid, icon_image_id: str) -> None:
        """Sets portfolios.icon_image_id only if the referenced image row
        already exists (satisfies the FK constraint); otherwise leaves it
        NULL for this pass -- a subsequent rescan backfills it once the
        target image has been indexed (e.g. cross-directory icon_dir)."""
        with self._db.cursor() as cur:
            cur.execute("SELECT 1 FROM images WHERE id = ?", (icon_image_id,))
            if cur.fetchone() is None:
                return
            cur.execute(
                "UPDATE portfolios SET icon_image_id = ? WHERE id = ?",
                (icon_image_id, str(port_uuid)),
            )

    def _upsert_image(self, parent_port_uuid, img, seen_image_rel_paths, result) -> None:
        from PIL import Image as PILImage
        import mimetypes

        img_uuid = compute_img_uuid(self._root_id, img.content_hash)
        seen_image_rel_paths.add(img.rel_path)

        stat = Path(img.real_path).stat()
        file_modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        taken_at = self._extract_taken_at(img.real_path) or file_modified_at
        mime_type = mimetypes.guess_type(img.real_path)[0] or "application/octet-stream"

        try:
            with PILImage.open(img.real_path) as pil_img:
                width, height = pil_img.size
        except Exception:
            width, height = 0, 0

        now = datetime.now(timezone.utc).isoformat()
        with self._db.cursor() as cur:
            # (a) Content-only row in `images`, keyed by Img_UUID. Shared by
            # every physical placement of these exact bytes.
            cur.execute("SELECT 1 FROM images WHERE id = ?", (str(img_uuid),))
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO images (id, content_hash, width, height, size_bytes,
                        mime_type, taken_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(img_uuid), img.content_hash, width, height, stat.st_size,
                     mime_type, taken_at, now, now),
                )
            else:
                cur.execute(
                    """
                    UPDATE images SET content_hash = ?, width = ?, height = ?,
                        size_bytes = ?, mime_type = ?, taken_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (img.content_hash, width, height, stat.st_size, mime_type,
                     taken_at, now, str(img_uuid)),
                )

            # (b) Placement row in `image_locations`, keyed by
            # (img_uuid, port_uuid, rel_path). Move vs add is decided per
            # (img_uuid, port_uuid): a pre-existing placement for this pair
            # whose rel_path was NOT seen this pass (i.e. it is about to be
            # reconciled away) is treated as an in-portfolio move; a brand
            # new (img_uuid, port_uuid) pair -- or an additional identical
            # copy alongside one already seen this pass -- is an add. A new
            # pair counts as an add even when the same img_uuid already
            # exists in another portfolio (that is the cross-portfolio
            # duplicate case this table exists to support).
            cur.execute(
                "SELECT rel_path FROM image_locations WHERE img_uuid = ? AND port_uuid = ?",
                (str(img_uuid), str(parent_port_uuid)),
            )
            existing_locs = [r["rel_path"] for r in cur.fetchall()]
            if img.rel_path in existing_locs:
                cur.execute(
                    """
                    UPDATE image_locations SET name = ?, is_symlink = ?,
                        file_modified_at = ?, updated_at = ?
                    WHERE img_uuid = ? AND port_uuid = ? AND rel_path = ?
                    """,
                    (img.name, int(img.is_symlink), file_modified_at, now,
                     str(img_uuid), str(parent_port_uuid), img.rel_path),
                )
            else:
                stale = [rp for rp in existing_locs if rp not in seen_image_rel_paths]
                if stale:
                    cur.execute(
                        """
                        UPDATE image_locations SET rel_path = ?, name = ?, is_symlink = ?,
                            file_modified_at = ?, updated_at = ?
                        WHERE img_uuid = ? AND port_uuid = ? AND rel_path = ?
                        """,
                        (img.rel_path, img.name, int(img.is_symlink), file_modified_at,
                         now, str(img_uuid), str(parent_port_uuid), stale[0]),
                    )
                    result.images_moved += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO image_locations (img_uuid, port_uuid, rel_path, name,
                            is_symlink, file_modified_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (str(img_uuid), str(parent_port_uuid), img.rel_path, img.name,
                         int(img.is_symlink), file_modified_at, now, now),
                    )
                    result.images_added += 1

        self._upsert_identity_registry(img_uuid, "image", normalize_rel_path(img.rel_path), img.content_hash)

    def _extract_taken_at(self, real_path: str) -> Optional[str]:
        try:
            from PIL import Image as PILImage
            from PIL.ExifTags import TAGS

            with PILImage.open(real_path) as pil_img:
                exif = pil_img.getexif()
                if not exif:
                    return None
                for tag_id, value in exif.items():
                    if TAGS.get(tag_id) == "DateTimeOriginal":
                        dt = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                        return dt.replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            return None
        return None

    def _upsert_virtual_membership(self, port_uuid, meta, rel_path, seen_image_rel_paths, result) -> None:
        with self._db.cursor() as cur:
            cur.execute("DELETE FROM virtual_membership WHERE port_uuid = ?", (str(port_uuid),))
        for idx, entry in enumerate(meta.images):
            try:
                content_hash = self._storage.compute_content_hash(entry.rel_path)
            except Exception:
                result.errors.append(
                    f"Virtual portfolio {rel_path}: unresolvable images[] entry '{entry.rel_path}'"
                )
                continue
            img_uuid = compute_img_uuid(self._root_id, content_hash)
            with self._db.cursor() as cur:
                cur.execute("SELECT 1 FROM images WHERE id = ?", (str(img_uuid),))
                if cur.fetchone() is None:
                    result.errors.append(
                        f"Virtual portfolio {rel_path}: images[] entry '{entry.rel_path}' "
                        f"not yet indexed (will resolve once its own directory is scanned)"
                    )
                    continue
                cur.execute(
                    """
                    INSERT OR REPLACE INTO virtual_membership (port_uuid, img_uuid, alternate_name, sort_order)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(port_uuid), str(img_uuid), entry.alternate_name, idx),
                )

    def _upsert_identity_registry(self, uuid_, kind, normalized_rel_path, content_hash) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO identity_registry (uuid, kind, normalized_rel_path, content_hash, last_seen_at, tombstoned_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(uuid) DO UPDATE SET
                    normalized_rel_path = excluded.normalized_rel_path,
                    content_hash = excluded.content_hash,
                    last_seen_at = excluded.last_seen_at,
                    tombstoned_at = NULL
                """,
                (str(uuid_), kind, normalized_rel_path, content_hash, now),
            )

    def _reconcile_removed(self, start_rel_path, seen_portfolio_rel_paths, seen_image_rel_paths, result) -> None:
        """Anything under start_rel_path not visited this pass is considered removed."""
        now = datetime.now(timezone.utc).isoformat()
        with self._db.cursor() as cur:
            prefix = f"{start_rel_path.rstrip('/')}/%" if start_rel_path else "%"
            # Removals are now scoped to placements (image_locations): a file
            # that vanished from a directory removes only that location. The
            # shared content row in `images` (and the identity_registry
            # tombstone) is dropped only once NO location anywhere still
            # references that Img_UUID -- so a byte-identical copy surviving
            # in another portfolio keeps the image alive.
            if start_rel_path:
                cur.execute(
                    "SELECT img_uuid, rel_path FROM image_locations WHERE rel_path LIKE ? OR rel_path = ?",
                    (prefix, start_rel_path),
                )
            else:
                cur.execute("SELECT img_uuid, rel_path FROM image_locations")
            affected_img_uuids: set[str] = set()
            for row in cur.fetchall():
                if row["rel_path"] not in seen_image_rel_paths:
                    cur.execute(
                        "DELETE FROM image_locations WHERE img_uuid = ? AND rel_path = ?",
                        (row["img_uuid"], row["rel_path"]),
                    )
                    affected_img_uuids.add(row["img_uuid"])
                    result.images_removed += 1

            for img_uuid in affected_img_uuids:
                cur.execute(
                    "SELECT 1 FROM image_locations WHERE img_uuid = ? LIMIT 1",
                    (img_uuid,),
                )
                if cur.fetchone() is None:
                    cur.execute("DELETE FROM images WHERE id = ?", (img_uuid,))
                    cur.execute(
                        "UPDATE identity_registry SET tombstoned_at = ? WHERE uuid = ?",
                        (now, img_uuid),
                    )

            if start_rel_path:
                cur.execute(
                    "SELECT id, rel_path FROM portfolios WHERE (rel_path LIKE ? OR rel_path = ?) AND rel_path != ?",
                    (prefix, start_rel_path, start_rel_path),
                )
            else:
                cur.execute("SELECT id, rel_path FROM portfolios WHERE rel_path != ''")
            for row in cur.fetchall():
                if row["rel_path"] not in seen_portfolio_rel_paths:
                    cur.execute("DELETE FROM portfolios WHERE id = ?", (row["id"],))
                    cur.execute(
                        "UPDATE identity_registry SET tombstoned_at = ? WHERE uuid = ?",
                        (now, row["id"]),
                    )
                    result.portfolios_removed += 1

    def _finalize(self, result: RescanResult, scope_port_uuid: Optional[UUID], recursive: bool) -> None:
        with self._db.cursor() as cur:
            pass  # ensure any pending cursor context is closed before repository call
        from photoshare.services.metadata_repository import MetadataRepository

        MetadataRepository(self._db).insert_rescan_history(result, scope_port_uuid, recursive)
        self._append_jsonl(result, scope_port_uuid, recursive)

    def _append_jsonl(self, result: RescanResult, scope_port_uuid: Optional[UUID], recursive: bool) -> None:
        self._rescan_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat(),
            "scope_port_uuid": str(scope_port_uuid) if scope_port_uuid else None,
            "recursive": recursive,
            "portfolios_added": result.portfolios_added,
            "portfolios_moved": result.portfolios_moved,
            "portfolios_removed": result.portfolios_removed,
            "images_added": result.images_added,
            "images_moved": result.images_moved,
            "images_removed": result.images_removed,
            "errors": result.errors,
            "duration_ms": result.duration_ms,
        }
        with open(self._rescan_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
