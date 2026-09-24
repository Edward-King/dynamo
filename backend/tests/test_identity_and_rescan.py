"""
Identity-registry & rescan move/rename-detection tests.

Verifies the content-hash identity model (identity/models.py §3.6) and the
move/rename detection + reconciliation implemented in
services/metadata_service.py. All expected UUIDs are computed with the same
helpers the production code uses, so the tests assert real identity math
rather than magic strings.
"""
from __future__ import annotations

from uuid import uuid4

from PIL import Image

from photoshare.identity.models import compute_img_uuid, compute_port_uuid


def _img_id(db, rel_path: str) -> str:
    """Img_UUID for the image physically placed at rel_path. rel_path now
    lives on image_locations (schema_version 2); the images row is
    content-only and no longer path-addressable."""
    with db.cursor() as cur:
        cur.execute("SELECT img_uuid FROM image_locations WHERE rel_path = ?", (rel_path,))
        row = cur.fetchone()
    return row["img_uuid"] if row else None


def _port_row(db, rel_path: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM portfolios WHERE rel_path = ?", (rel_path,))
        return cur.fetchone()


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
        return cur.fetchone()["c"]


# --------------------------------------------------------------------------
# Img_UUID stability across rename/move (§3.6 content-addressed identity)
# --------------------------------------------------------------------------
class TestImageIdentityStability:
    def test_img_uuid_stable_on_rename_within_same_portfolio(
        self, library, metadata_service, db, root_id
    ):
        """Renaming a file in place keeps its content hash -> same Img_UUID;
        rescan records a move, not a delete+add."""
        library.add_bytes("p/original.jpg", b"stable-content-1")
        metadata_service.rescan(port_uuid=None, recursive=True)
        original_id = _img_id(db, "p/original.jpg")
        assert original_id is not None

        (library.base_root / "p" / "original.jpg").rename(
            library.base_root / "p" / "renamed.jpg"
        )
        result = metadata_service.rescan(port_uuid=None, recursive=True)

        assert _img_id(db, "p/renamed.jpg") == original_id
        assert _img_id(db, "p/original.jpg") is None
        assert result.images_moved == 1
        assert result.images_added == 0
        assert result.images_removed == 0

    def test_img_uuid_stable_on_move_across_portfolios(
        self, library, metadata_service, db
    ):
        """Moving a file to a different directory keeps a stable Img_UUID
        (content identity + identity_registry survive). Under the
        location-based rule (schema_version 2), a cross-portfolio relocation
        is counted as add (new (img_uuid, dst_port) placement) + remove
        (stale (img_uuid, src_port) placement), NOT an in-portfolio move --
        `images_moved` is reserved for same-(img_uuid, port_uuid) rel_path
        changes."""
        library.add_bytes("src/photo.jpg", b"stable-content-2")
        library.add_dir("dst")
        metadata_service.rescan(port_uuid=None, recursive=True)
        moved_id = _img_id(db, "src/photo.jpg")

        (library.base_root / "src" / "photo.jpg").rename(
            library.base_root / "dst" / "photo.jpg"
        )
        result = metadata_service.rescan(port_uuid=None, recursive=True)

        # Identity is preserved even though it is counted as add+remove.
        assert _img_id(db, "dst/photo.jpg") == moved_id
        assert _img_id(db, "src/photo.jpg") is None
        assert result.images_added == 1
        assert result.images_removed == 1
        assert result.images_moved == 0
        # The shared content row was never dropped (a location still exists).
        assert _count(db, "images") == 1

    def test_identical_content_in_two_locations_collapses_to_one_uuid(
        self, library, metadata_service, db, root_id
    ):
        """Content-addressed identity: two byte-identical files map to the
        SAME Img_UUID (uuid5(root_id, content_hash)) and therefore share ONE
        content row in `images`. Under schema_version 2 the two physical
        placements no longer collide on the PK -- each gets its own
        image_locations row -- so both copies survive (this is the
        cross-portfolio duplicate fix)."""
        library.add_bytes("d1/one.jpg", b"IDENTICAL-BYTES")
        library.add_bytes("d2/two.jpg", b"IDENTICAL-BYTES")

        metadata_service.rescan(port_uuid=None, recursive=True)

        content_hash = metadata_service._storage.compute_content_hash("d1/one.jpg")
        expected_uuid = str(compute_img_uuid(root_id, content_hash))
        # One shared content row...
        assert _count(db, "images") == 1
        with db.cursor() as cur:
            cur.execute("SELECT id FROM images")
            assert cur.fetchone()["id"] == expected_uuid
        # ...but two distinct placements, both pointing at that content row.
        assert _count(db, "image_locations") == 2
        assert _img_id(db, "d1/one.jpg") == expected_uuid
        assert _img_id(db, "d2/two.jpg") == expected_uuid

    def test_different_content_yields_different_uuids(
        self, library, metadata_service, db
    ):
        library.add_bytes("d/a.jpg", b"content-A")
        library.add_bytes("d/b.jpg", b"content-B")
        metadata_service.rescan(port_uuid=None, recursive=True)
        assert _count(db, "images") == 2
        assert _img_id(db, "d/a.jpg") != _img_id(db, "d/b.jpg")


# --------------------------------------------------------------------------
# Port_UUID is path-derived (§3.6): a renamed dir is a new portfolio
# --------------------------------------------------------------------------
class TestPortfolioIdentity:
    def test_port_uuid_changes_when_directory_renamed(
        self, library, metadata_service, db, root_id
    ):
        """Port_UUID = uuid5(root_id, normalized_rel_path); renaming the
        directory changes the path, so the old portfolio is removed and a
        new one (new Port_UUID) is added."""
        library.add_bytes("orig/x.jpg", b"x")
        metadata_service.rescan(port_uuid=None, recursive=True)
        old_uuid = str(compute_port_uuid(root_id, "orig"))
        assert _port_row(db, "orig")["id"] == old_uuid

        (library.base_root / "orig").rename(library.base_root / "renamed")
        result = metadata_service.rescan(port_uuid=None, recursive=True)

        new_uuid = str(compute_port_uuid(root_id, "renamed"))
        assert _port_row(db, "renamed")["id"] == new_uuid
        assert new_uuid != old_uuid
        assert _port_row(db, "orig") is None
        assert result.portfolios_added == 1
        assert result.portfolios_removed == 1


# --------------------------------------------------------------------------
# icon_image_id FK-ordering regression (portfolio inserted before its icon)
# --------------------------------------------------------------------------
class TestIconBackfillFkOrdering:
    def test_same_dir_icon_backfilled_in_single_rescan(
        self, library, metadata_service, db, root_id
    ):
        """Regression: the portfolio row is inserted first with
        icon_image_id=NULL (so the images FK is never violated), then
        backfilled once its images exist. For a same-dir icon (first image
        alphabetically), one rescan suffices and no IntegrityError occurs."""
        library.add_bytes("p/m_second.jpg", b"second")
        library.add_bytes("p/a_first.jpg", b"first")

        metadata_service.rescan(port_uuid=None, recursive=True)

        row = _port_row(db, "p")
        first_hash = metadata_service._storage.compute_content_hash("p/a_first.jpg")
        assert row["icon_image_id"] == str(compute_img_uuid(root_id, first_hash))

    def test_cross_dir_icon_null_first_pass_backfilled_second_pass(
        self, library, metadata_service, db, root_id
    ):
        """Regression for the FK-ordering bug in
        _walk/_upsert_portfolio/_backfill_icon_image_id: when a portfolio's
        icon_dir points at an image in a directory not yet scanned, the
        portfolio is inserted with icon_image_id=NULL on the first pass
        (target image row doesn't exist -> backfill safely skipped), and a
        subsequent rescan backfills it once the target has been indexed."""
        library.add_bytes("zzz/pic.jpg", b"distinct-icon-target")
        library.add_bytes("aaa/self.jpg", b"aaa-local-distinct")
        library.add_meta("aaa", {"icon_dir": "zzz/pic.jpg"})

        metadata_service.rescan(port_uuid=None, recursive=True)
        assert _port_row(db, "aaa")["icon_image_id"] is None

        metadata_service.rescan(port_uuid=None, recursive=True)
        target_hash = metadata_service._storage.compute_content_hash("zzz/pic.jpg")
        assert _port_row(db, "aaa")["icon_image_id"] == str(
            compute_img_uuid(root_id, target_hash)
        )


# --------------------------------------------------------------------------
# Rescan result counts across a combined add/move/remove scenario
# --------------------------------------------------------------------------
class TestRescanResultCounts:
    def test_combined_add_move_remove_counts(self, library, metadata_service, db):
        """One rescan that simultaneously adds a new file, renames a file in
        place (a genuine in-portfolio move), and removes a file reports each
        count correctly and independently. `images_moved` counts only
        same-(img_uuid, port_uuid) rel_path changes; cross-portfolio
        relocations are add+remove (see the move-across-portfolios test)."""
        library.add_bytes("p1/a.jpg", b"content-a")
        library.add_bytes("p1/b.jpg", b"content-b")
        library.add_bytes("p2/c.jpg", b"content-c")

        first = metadata_service.rescan(port_uuid=None, recursive=True)
        assert (first.portfolios_added, first.images_added) == (3, 3)  # root+p1+p2

        # add d.jpg to p2; rename a.jpg within p1 (in-portfolio move); remove b.jpg
        library.add_bytes("p2/d.jpg", b"content-d")
        (library.base_root / "p1" / "a.jpg").rename(library.base_root / "p1" / "a2.jpg")
        (library.base_root / "p1" / "b.jpg").unlink()

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        assert result.images_added == 1     # d.jpg
        assert result.images_moved == 1     # a.jpg -> a2.jpg within p1
        assert result.images_removed == 1   # b.jpg
        assert result.portfolios_added == 0
        assert result.portfolios_moved == 0
        assert result.portfolios_removed == 0

    def test_removed_image_is_tombstoned_in_identity_registry(
        self, library, metadata_service, db
    ):
        """A removed image is deleted from images and tombstoned (not hard
        deleted) in identity_registry, so its UUID can never be silently
        reused (§3.6 registry semantics)."""
        library.add_bytes("p/gone.jpg", b"will-be-removed")
        metadata_service.rescan(port_uuid=None, recursive=True)
        gone_id = _img_id(db, "p/gone.jpg")

        (library.base_root / "p" / "gone.jpg").unlink()
        metadata_service.rescan(port_uuid=None, recursive=True)

        with db.cursor() as cur:
            cur.execute(
                "SELECT tombstoned_at FROM identity_registry WHERE uuid = ?", (gone_id,)
            )
            row = cur.fetchone()
        assert row is not None and row["tombstoned_at"] is not None


# --------------------------------------------------------------------------
# meta.json malformed handling surfaces as a rescan error, not a crash
# --------------------------------------------------------------------------
class TestMalformedMetaJson:
    def test_malformed_tags_surface_as_rescan_error(
        self, library, metadata_service, db
    ):
        """meta.json 'tags' as a comma-separated string (not a JSON array)
        is reported as a non-fatal rescan error and never silently coerced
        (§2.3); the rest of the scan still completes and the portfolio row
        is still created."""
        library.add_bytes("trip/photo.jpg", b"a-photo")
        library.add_meta("trip", '{"tags": "beach,2026", "description": "x"}')

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        assert any("tags" in e for e in result.errors), result.errors
        # not a crash: portfolio + image still indexed.
        assert _port_row(db, "trip") is not None
        assert _img_id(db, "trip/photo.jpg") is not None


# --------------------------------------------------------------------------
# Scoped rescan (port_uuid set): existing subtree + not-found scope
# --------------------------------------------------------------------------
class TestScopedRescan:
    def test_scoped_rescan_only_walks_the_named_subtree(
        self, library, metadata_service, db, root_id
    ):
        """rescan(port_uuid=<subdir>) sets start_rel_path to that portfolio's
        rel_path and walks only that subtree; a sibling subtree is untouched."""
        library.add_bytes("keep/a.jpg", b"keep-a")
        library.add_bytes("other/b.jpg", b"other-b")
        metadata_service.rescan(port_uuid=None, recursive=True)

        keep_uuid = compute_port_uuid(root_id, "keep")
        # Add a new file into the scoped subtree only.
        library.add_bytes("keep/c.jpg", b"keep-c")
        result = metadata_service.rescan(port_uuid=keep_uuid, recursive=True)

        assert result.images_added == 1  # only keep/c.jpg
        assert _img_id(db, "keep/c.jpg") is not None

    def test_scoped_rescan_removal_is_scoped_to_subtree(
        self, library, metadata_service, db, root_id
    ):
        """A file deleted inside the scoped subtree is reconciled away via the
        LIKE-prefix branch; nodes outside the scope are never inspected."""
        library.add_bytes("keep/a.jpg", b"keep-a")
        library.add_bytes("keep/gone.jpg", b"keep-gone")
        library.add_bytes("other/b.jpg", b"other-b")
        metadata_service.rescan(port_uuid=None, recursive=True)

        keep_uuid = compute_port_uuid(root_id, "keep")
        (library.base_root / "keep" / "gone.jpg").unlink()
        result = metadata_service.rescan(port_uuid=keep_uuid, recursive=True)

        assert result.images_removed == 1
        assert _img_id(db, "keep/gone.jpg") is None
        # Out-of-scope image untouched.
        assert _img_id(db, "other/b.jpg") is not None

    def test_rescan_with_unknown_scope_records_error_and_scans_nothing(
        self, library, metadata_service, db
    ):
        """An unknown scope port_uuid short-circuits: an error is recorded and
        nothing is walked (start_rel_path lookup returns None)."""
        library.add_bytes("p/a.jpg", b"a")
        metadata_service.rescan(port_uuid=None, recursive=True)
        before = _count(db, "image_locations")

        result = metadata_service.rescan(port_uuid=uuid4(), recursive=True)

        assert any("not found" in e for e in result.errors), result.errors
        assert result.images_added == 0 and result.portfolios_added == 0
        assert _count(db, "image_locations") == before


# --------------------------------------------------------------------------
# Virtual-membership resolution error branches (_upsert_virtual_membership)
# --------------------------------------------------------------------------
class TestVirtualMembershipErrors:
    def test_unresolvable_entry_records_error(self, library, metadata_service):
        """A virtual images[] entry whose rel_path cannot be content-hashed
        (file does not exist) is reported as an unresolvable-entry error and
        skipped, without aborting the rescan."""
        library.add_meta("v", {
            "virtual": True,
            "images": [{"rel_path": "does/not/exist.jpg"}],
        })
        result = metadata_service.rescan(port_uuid=None, recursive=True)
        assert any("unresolvable images[] entry" in e for e in result.errors), result.errors

    def test_not_yet_indexed_entry_records_error_then_resolves(
        self, library, metadata_service, db, root_id
    ):
        """When a virtual portfolio (walked earlier, alphabetically) references
        an image whose own directory is scanned later, the first pass records
        a 'not yet indexed' error; a second rescan resolves the membership."""
        # 'aaa' sorts before 'zzz', so the virtual dir is visited first.
        library.add_bytes("zzz/pic.jpg", b"virtual-target")
        library.add_meta("aaa", {
            "virtual": True,
            "images": [{"rel_path": "zzz/pic.jpg"}],
        })

        first = metadata_service.rescan(port_uuid=None, recursive=True)
        assert any("not yet indexed" in e for e in first.errors), first.errors

        metadata_service.rescan(port_uuid=None, recursive=True)
        aaa_uuid = compute_port_uuid(root_id, "aaa")
        with db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM virtual_membership WHERE port_uuid = ?",
                (str(aaa_uuid),),
            )
            assert cur.fetchone()["c"] == 1


# --------------------------------------------------------------------------
# EXIF taken_at extraction (_extract_taken_at)
# --------------------------------------------------------------------------
class TestExifTakenAt:
    def _write_jpeg_with_exif(self, path, exif_pairs):
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (16, 16), (10, 20, 30))
        exif = img.getexif()
        for tag_id, value in exif_pairs.items():
            exif[tag_id] = value
        img.save(path, "JPEG", exif=exif)

    def test_datetimeoriginal_is_parsed_as_taken_at(self, library, metadata_service, db):
        """A DateTimeOriginal EXIF tag (36867) is parsed into taken_at
        (interpreted as UTC)."""
        p = library.base_root / "e" / "shot.jpg"
        self._write_jpeg_with_exif(p, {36867: "2020:01:02 03:04:05"})

        taken = metadata_service._extract_taken_at(str(p))
        assert taken is not None and taken.startswith("2020-01-02T03:04:05")

    def test_exif_without_datetimeoriginal_returns_none(self, library, metadata_service):
        """EXIF present but with no DateTimeOriginal tag -> None (falls back to
        file mtime at the call site)."""
        p = library.base_root / "e" / "make.jpg"
        self._write_jpeg_with_exif(p, {271: "SomeCameraMake"})  # 271 = Make

        assert metadata_service._extract_taken_at(str(p)) is None

    def test_no_exif_returns_none(self, library, metadata_service):
        p = library.add_jpeg("e/plain.jpg")
        assert metadata_service._extract_taken_at(str(p)) is None


# --------------------------------------------------------------------------
# Icon resolution: a candidate whose content hash cannot be computed
# --------------------------------------------------------------------------
class TestIconResolutionFailure:
    def test_unhashable_icon_candidate_leaves_icon_null(
        self, library, metadata_service, db
    ):
        """When meta.icon_dir points at a (path-shaped) target that does not
        exist, resolve_icon still returns it as a candidate but
        compute_content_hash raises; the except-branch returns None so the
        portfolio is stored with icon_image_id NULL and the rescan does not
        crash."""
        library.add_bytes("p/real.jpg", b"real")
        library.add_meta("p", {"icon_dir": "ghostdir/missing.jpg"})

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        row = _port_row(db, "p")
        assert row is not None
        assert row["icon_image_id"] is None
        # Still indexed the real image; no fatal error.
        assert _img_id(db, "p/real.jpg") is not None
