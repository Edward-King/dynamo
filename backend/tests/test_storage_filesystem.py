"""
Storage-layer & symlink-cycle-detection tests.

Verifies the filesystem adapter's path-containment rules (§2.1/§2.7) and the
rescan walk's symlink-cycle protection (§2.1, implemented in
MetadataService._walk via the visited_real_dirs set). Behaviors here were
confirmed against the actual implementation rather than assumed.
"""
from __future__ import annotations

import pytest

from photoshare.storage.errors import MalformedPathError, PathOutsideRootError


# --------------------------------------------------------------------------
# Path validation / containment (FilesystemStorageAdapter.resolve, §2.7)
# --------------------------------------------------------------------------
class TestPathContainment:
    def test_resolve_rejects_parent_traversal(self, storage):
        """'..' segments are rejected syntactically before resolution (§2.7
        MALFORMED_PATH)."""
        with pytest.raises(MalformedPathError):
            storage.resolve("../etc/passwd")

    def test_resolve_rejects_absolute_path(self, storage):
        with pytest.raises(MalformedPathError):
            storage.resolve("/etc/passwd")

    def test_resolve_accepts_in_root_relative_path(self, library, storage):
        library.add_jpeg("dir/a.jpg")
        resolved = storage.resolve("dir/a.jpg")
        assert resolved == (library.base_root / "dir" / "a.jpg").resolve()

    def test_symlink_escaping_root_resolves_outside(self, library, storage, tmp_path):
        """A symlinked *file* whose target resolves outside base_root trips
        the containment check -> PathOutsideRootError (§2.1 security rule)."""
        external = tmp_path / "outside"
        external.mkdir()
        (external / "secret.jpg").write_bytes(b"secret")
        library.add_symlink("escape.jpg", external / "secret.jpg", target_is_absolute=True)
        with pytest.raises(PathOutsideRootError):
            storage.resolve("escape.jpg")


# --------------------------------------------------------------------------
# list_dirs symlink containment (§2.1)
# --------------------------------------------------------------------------
class TestListDirsSymlinkContainment:
    def test_external_symlinked_dir_is_excluded(self, library, storage, tmp_path):
        """A directory symlink whose real target is outside base_root is
        silently excluded from listings (§2.1) -- never surfaced upward."""
        external = tmp_path / "external_lib"
        external.mkdir()
        library.add_jpeg("real/keep.jpg")
        library.add_symlink("extlink", external, target_is_absolute=True)

        names = [d.name for d in storage.list_dirs("")]
        assert "real" in names
        assert "extlink" not in names

    def test_dot_directories_are_invisible(self, library, storage):
        """Dot-directories are invisible to every layer above storage (§2.6)."""
        library.add_dir(".hidden")
        library.add_dir("visible")
        names = [d.name for d in storage.list_dirs("")]
        assert names == ["visible"]


# --------------------------------------------------------------------------
# Symlink cycle detection during rescan (§2.1)
# --------------------------------------------------------------------------
class TestSymlinkCycleDetection:
    def test_self_cycle_symlink_to_ancestor_is_detected_and_skipped(
        self, library, metadata_service
    ):
        """A symlink pointing back to an ancestor directory forms a cycle;
        rescan must detect it, record SYMLINK_CYCLE_DETECTED, skip the
        subtree, and still index the rest -- no infinite recursion (§2.1)."""
        library.add_jpeg("a/x.jpg")
        library.add_symlink("a/loop", library.base_root)  # -> ancestor (root)

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        cycle_errors = [e for e in result.errors if e.startswith("SYMLINK_CYCLE_DETECTED")]
        assert cycle_errors, f"expected a cycle error, got {result.errors}"
        # root + 'a' indexed; the loop subtree skipped, real image still found.
        assert result.portfolios_added == 2
        assert result.images_added == 1

    def test_mutual_two_symlink_cycle_is_detected(self, library, metadata_service):
        """Two directory symlinks pointing at each other (a/loop -> b,
        b/loop -> a) form a cycle that must be detected, not looped."""
        library.add_jpeg("a/x.jpg")
        library.add_jpeg("b/y.jpg", color=(5, 5, 5))
        library.add_symlink("a/loop", "b")
        library.add_symlink("b/loop", "a")

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        cycle_errors = [e for e in result.errors if e.startswith("SYMLINK_CYCLE_DETECTED")]
        assert cycle_errors, f"expected cycle detection, got {result.errors}"

    def test_sibling_symlink_target_indexed_once_not_looped(
        self, library, metadata_service
    ):
        """A non-cyclic in-root symlink to a sibling dir is not an infinite
        loop: the real target dir is walked exactly once and the second
        encounter is deduped via the same visited-real-dir guard (recorded
        as a cycle error). Documents actual implemented behavior: the target
        is reachable both directly and via the link, so its real path repeats
        and the repeat is skipped rather than double-indexed."""
        library.add_jpeg("real2/z.jpg")
        library.add_dir("real1")
        library.add_symlink("real1/link", "real2")

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        # z.jpg indexed exactly once despite being reachable via two paths.
        assert result.images_added == 1
        assert result.images_removed == 0

    def test_normal_nested_dirs_have_no_cycle_errors(self, library, metadata_service):
        """Plain (non-symlink) nested directories are unaffected by
        cycle-detection logic -- everything is indexed, no cycle errors."""
        library.add_jpeg("top/mid/deep/photo.jpg")
        library.add_jpeg("top/mid/other.jpg", color=(9, 9, 9))

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        assert [e for e in result.errors if "CYCLE" in e] == []
        # root + top + top/mid + top/mid/deep = 4 portfolios.
        assert result.portfolios_added == 4
        assert result.images_added == 2

    def test_cycle_detection_toggle_off_removes_protection(
        self, library, metadata_service_factory
    ):
        """rescan.cycle_detection is a configurable, documented accepted-risk
        toggle (config.schema.RescanConfig): with it DISABLED, the
        visited-real-dir guard no longer short-circuits, so a real symlink
        cycle recurses unbounded until Python's recursion limit trips.

        RISK (documented as accepted in the YAML config proposal): production
        configs MUST keep cycle_detection=true; this test exists precisely to
        prove that turning it off genuinely removes the protection."""
        library.add_jpeg("a/x.jpg")
        library.add_symlink("a/loop", library.base_root)

        service = metadata_service_factory(cycle_detection_enabled=False)
        with pytest.raises(RecursionError):
            service.rescan(port_uuid=None, recursive=True)


# --------------------------------------------------------------------------
# Root management (set_root, §3.2) -- changing the root never touches UUIDs
# --------------------------------------------------------------------------
class TestSetRoot:
    def test_set_root_updates_base_root_resolved(self, storage, tmp_path):
        new_root = tmp_path / "new_photos"
        new_root.mkdir()
        storage.set_root(str(new_root))
        assert storage.base_root == new_root.resolve()


# --------------------------------------------------------------------------
# Non-directory targets return [] rather than raising (list_dirs/list_images)
# --------------------------------------------------------------------------
class TestNonDirectoryListing:
    def test_list_dirs_on_a_file_returns_empty(self, library, storage):
        library.add_jpeg("solo.jpg")
        assert storage.list_dirs("solo.jpg") == []

    def test_list_images_on_a_file_returns_empty(self, library, storage):
        library.add_jpeg("solo.jpg")
        assert storage.list_images("solo.jpg") == []


# --------------------------------------------------------------------------
# Listing filters: dotfiles, dot-images, and dirs that look like images
# --------------------------------------------------------------------------
class TestListingFilters:
    def test_count_direct_skips_dotfiles(self, library, storage):
        """_count_direct ignores dot-prefixed entries when tallying a child
        directory's direct_image_count (§2.6)."""
        library.add_jpeg("child/real.jpg")
        library.add_bytes("child/.hidden.jpg", b"hidden")
        library.add_dir("child/sub")

        child = next(d for d in storage.list_dirs("") if d.name == "child")
        assert child.direct_image_count == 1  # .hidden.jpg not counted
        assert child.child_port_count == 1

    def test_list_images_skips_dot_images(self, library, storage):
        library.add_jpeg("d/real.jpg")
        library.add_bytes("d/.secret.jpg", b"secret")
        names = [i.name for i in storage.list_images("d")]
        assert names == ["real.jpg"]

    def test_directory_named_like_an_image_is_not_listed_as_image(self, library, storage):
        """A subdirectory whose name carries an image extension is not
        file-like, so list_images skips it (the `if not is_file_like`
        branch)."""
        library.add_dir("d/photos.jpg")  # a DIRECTORY named like an image
        library.add_jpeg("d/real.jpg")
        names = [i.name for i in storage.list_images("d")]
        assert names == ["real.jpg"]

    def test_symlinked_image_pointing_outside_root_is_excluded(
        self, library, storage, tmp_path
    ):
        """An image-extension symlink whose target resolves outside base_root
        is dropped from list_images (relative_to ValueError branch), even
        though the target is a real file."""
        external = tmp_path / "outside"
        external.mkdir()
        (external / "secret.jpg").write_bytes(b"secret-bytes")
        library.add_jpeg("d/real.jpg")
        library.add_symlink("d/ext.jpg", external / "secret.jpg", target_is_absolute=True)

        names = [i.name for i in storage.list_images("d")]
        assert names == ["real.jpg"]  # ext.jpg excluded


# --------------------------------------------------------------------------
# open_image and resolve_symlink
# --------------------------------------------------------------------------
class TestOpenAndResolveSymlink:
    def test_open_image_returns_readable_stream(self, library, storage):
        p = library.add_jpeg("d/pic.jpg")
        with storage.open_image("d/pic.jpg") as fh:
            data = fh.read()
        assert data == p.read_bytes()
        assert len(data) > 0

    def test_resolve_symlink_returns_none_for_regular_file(self, library, storage):
        library.add_jpeg("d/pic.jpg")
        assert storage.resolve_symlink("d/pic.jpg") is None

    def test_resolve_symlink_returns_target_for_symlink(self, library, storage):
        target = library.add_jpeg("d/real.jpg")
        library.add_symlink("d/link.jpg", "d/real.jpg")
        assert storage.resolve_symlink("d/link.jpg") == str(target.resolve())
