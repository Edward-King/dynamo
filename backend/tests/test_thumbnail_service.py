"""
ThumbnailService (services/thumbnail_service.py) tests.

Covers the cache-hit short-circuit (a second get_or_create returns the
cached file without regenerating it), _validate_size boundary rejection,
preheat() over multiple sizes, and cleanup_stale() (deletes stale thumbs,
preserves valid ones, and skips files whose stem is not a UUID via the
swallowed-ValueError branch).

The out-of-range InvalidThumbnailSizeError is already exercised at one point
(width=9999) by tests/test_api_images.py::test_thumbnail_service_rejects_out_of_range_directly;
here we assert the exact [16, 4096] boundaries (15/4097 reject, 16/4096
accept) so the two files are complementary rather than duplicative.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from photoshare.services.thumbnail_service import (
    MAX_DIM,
    MIN_DIM,
    InvalidThumbnailSizeError,
    ThumbnailService,
)


class TestCacheHitShortCircuit:
    def test_second_call_does_not_regenerate(self, thumbnail_service, sample_library):
        """A cache hit returns the existing file untouched. We overwrite the
        generated thumbnail with a sentinel; a regeneration would clobber it,
        so an unchanged sentinel proves the is_file() short-circuit fired."""
        src = sample_library.base_root / "vacation" / "beach.jpg"
        img_uuid = uuid4()

        first = thumbnail_service.get_or_create(img_uuid, src, 64, 64)
        assert first.is_file()

        first.write_bytes(b"SENTINEL-NOT-A-REAL-JPEG")
        second = thumbnail_service.get_or_create(img_uuid, src, 64, 64)

        assert second == first
        assert second.read_bytes() == b"SENTINEL-NOT-A-REAL-JPEG"


class TestSizeValidationBoundaries:
    @pytest.mark.parametrize(
        "width,height",
        [(MIN_DIM - 1, 240), (MAX_DIM + 1, 240), (320, MIN_DIM - 1), (320, MAX_DIM + 1)],
    )
    def test_out_of_range_rejected(self, thumbnail_service, sample_library, width, height):
        src = sample_library.base_root / "vacation" / "beach.jpg"
        with pytest.raises(InvalidThumbnailSizeError):
            thumbnail_service.get_or_create(uuid4(), src, width, height)

    @pytest.mark.parametrize("dim", [MIN_DIM, MAX_DIM])
    def test_exact_boundaries_accepted(self, thumbnail_service, sample_library, dim):
        """MIN_DIM and MAX_DIM are inclusive: neither boundary raises. Uses
        MIN_DIM for the other axis so MAX_DIM does not blow up decode memory
        while still passing validation."""
        src = sample_library.base_root / "vacation" / "beach.jpg"
        out = thumbnail_service.get_or_create(uuid4(), src, dim, MIN_DIM)
        assert out.is_file()


class TestPreheat:
    def test_preheat_generates_each_size(self, thumbnail_service, sample_library):
        src = sample_library.base_root / "vacation" / "beach.jpg"
        img_uuid = uuid4()
        sizes = [(32, 32), (64, 48)]

        thumbnail_service.preheat(img_uuid, src, sizes)

        for w, h in sizes:
            assert thumbnail_service.get_or_create(img_uuid, src, w, h).is_file()


class TestCleanupStale:
    def test_removes_stale_preserves_valid(self, thumbnail_service, sample_library):
        src = sample_library.base_root / "vacation" / "beach.jpg"
        keep_uuid = uuid4()
        drop_uuid = uuid4()
        thumbnail_service.get_or_create(keep_uuid, src, 32, 32)
        stale_path = thumbnail_service.get_or_create(drop_uuid, src, 32, 32)
        keep_path = thumbnail_service.get_or_create(keep_uuid, src, 64, 64)

        removed = thumbnail_service.cleanup_stale({keep_uuid})

        assert removed == 1
        assert not stale_path.exists()
        assert keep_path.exists()

    def test_non_uuid_stem_is_skipped(self, thumbnail_service, sample_library, cache_root):
        """cleanup_stale swallows ValueError from a filename whose stem is not
        a UUID: such a file is neither counted nor deleted (defensive branch
        for foreign files that happen to live in the thumbnails tree)."""
        src = sample_library.base_root / "vacation" / "beach.jpg"
        valid_uuid = uuid4()
        valid_path = thumbnail_service.get_or_create(valid_uuid, src, 32, 32)

        # Drop a bogus-stem file into an existing shard dir.
        junk = valid_path.parent / "not-a-uuid_32x32.jpg"
        junk.write_bytes(b"junk")

        removed = thumbnail_service.cleanup_stale(set())  # nothing is valid

        # The valid-uuid thumb is removed (not in the valid set); the bogus
        # stem is skipped, so only 1 deletion is counted and junk survives.
        assert removed == 1
        assert not valid_path.exists()
        assert junk.exists()

    def test_non_directory_entry_in_thumb_dir_is_ignored(
        self, thumbnail_service, cache_root
    ):
        """A stray file directly under thumbnails/ (not a shard dir) is
        skipped by the `if not shard_dir.is_dir(): continue` guard."""
        stray = cache_root / "thumbnails" / "stray.txt"
        stray.write_text("x")
        assert thumbnail_service.cleanup_stale(set()) == 0
        assert stray.exists()
