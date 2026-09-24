"""
ThumbnailService (v3.0 §3.4/§4.4).

Lazy thumbnail generation with on-disk caching under
cache_root/thumbnails/<shard>/<uuid>_<w>x<h>.jpg. Cache hits stream
immediately; cache misses generate synchronously (explicit, expected
latency on first request per How-It-Works-Guide §7 -- not a bug).
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PIL import Image

MIN_DIM = 16
MAX_DIM = 4096


def shard_for(img_uuid: UUID) -> str:
    """Two-char shard prefix from the UUID, keeping any single cache
    directory from accumulating unbounded entries on large libraries. This
    is the single source of truth for the shard convention: the on-disk
    cache layout (ThumbnailService._cache_path) and the static-thumb URL
    builder (utils/urls.py) both derive their path from it."""
    return str(img_uuid)[:2]


class InvalidThumbnailSizeError(Exception):
    """width/height outside [MIN_DIM, MAX_DIM] -- router maps to 400."""


class ThumbnailService:
    def __init__(self, cache_root: str | Path) -> None:
        self._cache_root = Path(cache_root)
        self._thumb_dir = self._cache_root / "thumbnails"
        self._thumb_dir.mkdir(parents=True, exist_ok=True)

    def _validate_size(self, width: int, height: int) -> None:
        if not (MIN_DIM <= width <= MAX_DIM) or not (MIN_DIM <= height <= MAX_DIM):
            raise InvalidThumbnailSizeError(
                f"width/height must each be in [{MIN_DIM}, {MAX_DIM}], got {width}x{height}"
            )

    def _shard_for(self, img_uuid: UUID) -> str:
        return shard_for(img_uuid)

    def _cache_path(self, img_uuid: UUID, width: int, height: int) -> Path:
        shard = self._shard_for(img_uuid)
        shard_dir = self._thumb_dir / shard
        shard_dir.mkdir(parents=True, exist_ok=True)
        return shard_dir / f"{img_uuid}_{width}x{height}.jpg"

    def is_cached(self, img_uuid: UUID, width: int, height: int) -> bool:
        """True if a thumbnail for this img/size already exists on disk.
        Used by preheat to distinguish newly-generated thumbnails from
        cache hits without regenerating them."""
        return self._cache_path(img_uuid, width, height).is_file()

    def get_or_create(self, img_uuid: UUID, source_path: Path, width: int, height: int) -> Path:
        self._validate_size(width, height)
        cache_path = self._cache_path(img_uuid, width, height)
        if cache_path.is_file():
            return cache_path

        with Image.open(source_path) as im:
            im = im.convert("RGB")
            im.thumbnail((width, height), Image.LANCZOS)
            im.save(cache_path, "JPEG", quality=85)
        return cache_path

    def preheat(self, img_uuid: UUID, source_path: Path, sizes: list[tuple[int, int]]) -> None:
        """Proactively generate thumbnails for the configured cache.thumbnail_sizes."""
        for width, height in sizes:
            self.get_or_create(img_uuid, source_path, width, height)

    def cleanup_stale(self, valid_img_uuids: set[UUID]) -> int:
        """Removes cached thumbnails whose img_uuid no longer exists in the
        current library (called after a rescan removes images). Returns the
        count of files deleted."""
        removed = 0
        for shard_dir in self._thumb_dir.glob("*"):
            if not shard_dir.is_dir():
                continue
            for thumb_file in shard_dir.glob("*.jpg"):
                stem_uuid = thumb_file.stem.split("_")[0]
                try:
                    if UUID(stem_uuid) not in valid_img_uuids:
                        thumb_file.unlink()
                        removed += 1
                except ValueError:
                    continue
        return removed
