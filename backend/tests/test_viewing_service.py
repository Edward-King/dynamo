"""
ViewingService (services/viewing_service.py) tests.

Covers viewer_config() (shape of the client display hints) and
adjacent_images() (previous/next navigation) for the first / middle / last
image, the NotFoundError path when the target image is not a member, and
both the physical (name-sorted) and virtual (sort_order) orderings.

ViewingService has no conftest fixture; it is constructed in-test from the
existing `repo` fixture plus a directly-built CacheConfig (viewer_config only
reads config values, so a standalone CacheConfig is the real collaborator).
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from photoshare.config.schema import CacheConfig, TileThresholdConfig
from photoshare.identity.models import compute_port_uuid
from photoshare.services.metadata_repository import NotFoundError
from photoshare.services.viewing_service import ViewingService


class TestViewerConfig:
    def test_viewer_config_reports_configured_hints(self, repo):
        cache_config = CacheConfig(
            cache_root="/unused/for/viewer/config",
            thumbnail_sizes=[(100, 120), (200, 240)],
            # Must be members of thumbnail_sizes (CacheConfig validator);
            # irrelevant to this viewer-config assertion but required to build.
            default_static_thumbnail_size=(100, 120),
            default_static_icon_size=(100, 120),
            tile_threshold_px=TileThresholdConfig(width=1000, height=800),
        )
        svc = ViewingService(repo, cache_config)

        cfg = svc.viewer_config()
        # tuples are flattened to JSON-friendly lists.
        assert cfg.thumbnail_sizes == [[100, 120], [200, 240]]
        assert cfg.tile_threshold_width == 1000
        assert cfg.tile_threshold_height == 800


def _cache_config():
    return CacheConfig(
        cache_root="/unused/for/adjacency",
        thumbnail_sizes=[(320, 240)],
        tile_threshold_px=TileThresholdConfig(width=4000, height=3000),
    )


class TestAdjacentImagesPhysical:
    def _setup(self, library, metadata_service, repo, root_id):
        # Names chosen so the name-sort order is a < b < c.
        library.add_bytes("p/a.jpg", b"phys-a")
        library.add_bytes("p/b.jpg", b"phys-b")
        library.add_bytes("p/c.jpg", b"phys-c")
        metadata_service.rescan(port_uuid=None, recursive=True)
        port_uuid = compute_port_uuid(root_id, "p")
        rows = repo.list_images_for_portfolio(port_uuid, False)
        ids = [UUID(r["id"]) for r in rows]
        assert len(ids) == 3, "expected a,b,c in name-sorted order"
        return ViewingService(repo, _cache_config()), port_uuid, ids

    def test_first_image_has_no_previous(self, library, metadata_service, repo, root_id):
        svc, port_uuid, ids = self._setup(library, metadata_service, repo, root_id)
        adj = svc.adjacent_images(port_uuid, ids[0], False)
        assert adj.previous is None
        assert adj.next == ids[1]

    def test_middle_image_has_both_neighbors(self, library, metadata_service, repo, root_id):
        svc, port_uuid, ids = self._setup(library, metadata_service, repo, root_id)
        adj = svc.adjacent_images(port_uuid, ids[1], False)
        assert adj.previous == ids[0]
        assert adj.next == ids[2]

    def test_last_image_has_no_next(self, library, metadata_service, repo, root_id):
        svc, port_uuid, ids = self._setup(library, metadata_service, repo, root_id)
        adj = svc.adjacent_images(port_uuid, ids[2], False)
        assert adj.previous == ids[1]
        assert adj.next is None

    def test_unknown_image_raises_not_found(self, library, metadata_service, repo, root_id):
        svc, port_uuid, _ids = self._setup(library, metadata_service, repo, root_id)
        with pytest.raises(NotFoundError):
            svc.adjacent_images(port_uuid, uuid4(), False)


class TestAdjacentImagesVirtual:
    def test_virtual_ordering_follows_sort_order(
        self, library, metadata_service, repo, root_id
    ):
        """A virtual portfolio orders by sort_order (the meta.json images[]
        sequence), independent of filename sort. Root-level images are
        indexed during the root walk before the virtual subdirectory is
        visited, so its content-hash references resolve on the first pass."""
        library.add_bytes("a.jpg", b"virt-a")
        library.add_bytes("b.jpg", b"virt-b")
        library.add_bytes("c.jpg", b"virt-c")
        library.add_meta("gallery", {
            "virtual": True,
            # deliberately not filename order: c then a.
            "images": [{"rel_path": "c.jpg"}, {"rel_path": "a.jpg"}],
        })
        result = metadata_service.rescan(port_uuid=None, recursive=True)
        assert [e for e in result.errors if "not yet indexed" in e] == [], result.errors

        gallery_uuid = compute_port_uuid(root_id, "gallery")
        rows = repo.list_images_for_portfolio(gallery_uuid, True)
        ids = [UUID(r["id"]) for r in rows]
        assert len(ids) == 2  # ordered [c, a] by sort_order

        svc = ViewingService(repo, _cache_config())
        first = svc.adjacent_images(gallery_uuid, ids[0], True)
        assert first.previous is None
        assert first.next == ids[1]

        last = svc.adjacent_images(gallery_uuid, ids[1], True)
        assert last.previous == ids[0]
        assert last.next is None
