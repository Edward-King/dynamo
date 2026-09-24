"""
ExportService (services/export_service.py) + `dump-tree` CLI tests.

Covers build_portfolio_tree() over the real repo/DB (no mocks): full-tree
dump from root, subtree dump via an explicit root Port_UUID, the
nonexistent-root error path, cross-portfolio duplicate representation
(reusing the byte-identical-file pattern from
test_cross_portfolio_duplicates.py), virtual-portfolio sort_order/
alternate_name population (null for physical entries), and a round-trip
through the CLI command (json.loads on the written file + top-level shape).

The CLI is exercised in-process via manage.dump_tree(Namespace(...)); it is
a thin wrapper over the service, so this both proves the wiring and the
service-level structure in one path where useful.
"""
from __future__ import annotations

import argparse
import json
from uuid import UUID, uuid4

import pytest

from photoshare.cli import manage
from photoshare.identity.models import compute_port_uuid
from photoshare.services import export_service
from photoshare.services.metadata_repository import NotFoundError

SCHEMA_VERSION = 2
# Matches CacheConfig's default thumbnail_sizes. The two singular static-URL
# defaults are now explicit config values (both 320x240 here, matching the
# CacheConfig field defaults) rather than an implicit thumbnail_sizes[0].
THUMBNAIL_SIZES = [(320, 240), (800, 600)]
DEFAULT_STATIC_THUMBNAIL_SIZE = (320, 240)
DEFAULT_STATIC_ICON_SIZE = (320, 240)


def _find(node: dict, name: str) -> dict:
    """Depth-first search for the first child node with the given name."""
    if node["name"] == name:
        return node
    for child in node["children"]:
        hit = _find(child, name)
        if hit is not None:
            return hit
    return None


# --------------------------------------------------------------------------
# Service layer: build_portfolio_tree
# --------------------------------------------------------------------------
class TestBuildPortfolioTreeFullDump:
    def test_full_tree_structure_and_counts(
        self, library, metadata_service, repo, root_id
    ):
        library.add_jpeg("vacation/beach.jpg")
        library.add_jpeg("vacation/mountain.jpg")
        library.add_meta("vacation", {"description": "trip", "tags": ["v", "2026"]})
        library.add_jpeg("family/portrait.jpg")
        metadata_service.rescan(port_uuid=None, recursive=True)

        dump = export_service.build_portfolio_tree(repo, None, SCHEMA_VERSION, THUMBNAIL_SIZES, DEFAULT_STATIC_THUMBNAIL_SIZE, DEFAULT_STATIC_ICON_SIZE)

        assert set(dump.keys()) == {
            "generated_at",
            "root_port_uuid",
            "schema_version",
            "tree",
        }
        assert dump["schema_version"] == SCHEMA_VERSION

        tree = dump["tree"]
        assert dump["root_port_uuid"] == tree["id"]
        # Recursion: root has vacation + family as children.
        child_names = {c["name"] for c in tree["children"]}
        assert child_names == {"vacation", "family"}

        vacation = _find(tree, "vacation")
        assert vacation["description"] == "trip"
        assert set(vacation["tags"]) == {"v", "2026"}
        assert {i["name"] for i in vacation["images"]} == {"beach.jpg", "mountain.jpg"}
        assert vacation["direct_image_count"] == 2

        family = _find(tree, "family")
        assert {i["name"] for i in family["images"]} == {"portrait.jpg"}

        # summarize_tree counts 3 portfolios (root+vacation+family), 3 images.
        portfolios, images = export_service.summarize_tree(tree)
        assert portfolios == 3
        assert images == 3

    def test_portfolio_icon_urls_populated_and_none(
        self, library, metadata_service, repo, root_id
    ):
        """Dump-tree portfolio entries carry icon_thumbnail_url /
        icon_thumbnail_static_url: populated (matching the ImgOut thumbnail
        formats) when an icon is resolved, None when the portfolio has none."""
        library.add_jpeg("vacation/beach.jpg")
        library.add_jpeg("vacation/mountain.jpg")
        metadata_service.rescan(port_uuid=None, recursive=True)

        tree = export_service.build_portfolio_tree(repo, None, SCHEMA_VERSION, THUMBNAIL_SIZES, DEFAULT_STATIC_THUMBNAIL_SIZE, DEFAULT_STATIC_ICON_SIZE)["tree"]

        # vacation has direct images -> an icon is resolved.
        vacation = _find(tree, "vacation")
        icon_id = vacation["icon_image_id"]
        assert icon_id is not None
        assert vacation["icon_thumbnail_url"] == (
            f"/api/v2/images/{icon_id}/thumb?width=320&height=240"
        )
        assert vacation["icon_thumbnail_static_url"] == (
            f"/static-thumbs-320x240/{icon_id[:2]}/{icon_id}_320x240.jpg"
        )

        # root has no direct images -> no icon -> both fields None.
        assert tree["icon_image_id"] is None
        assert tree["icon_thumbnail_url"] is None
        assert tree["icon_thumbnail_static_url"] is None

    def test_image_entry_has_full_metadata_fields(
        self, library, metadata_service, repo
    ):
        library.add_jpeg("solo/only.jpg")
        metadata_service.rescan(port_uuid=None, recursive=True)

        tree = export_service.build_portfolio_tree(repo, None, SCHEMA_VERSION, THUMBNAIL_SIZES, DEFAULT_STATIC_THUMBNAIL_SIZE, DEFAULT_STATIC_ICON_SIZE)["tree"]
        entry = _find(tree, "solo")["images"][0]

        assert set(entry.keys()) == {
            "id",
            "name",
            "rel_path",
            "width",
            "height",
            "size_bytes",
            "mime_type",
            "content_hash",
            "file_modified_at",
            "taken_at",
            "is_symlink",
            "tags",
            "full_url",
            "thumbnail_url",
            "full_static_url",
            "thumbnail_static_url",
            "alternate_name",
            "sort_order",
        }
        assert entry["name"] == "only.jpg"
        assert entry["rel_path"] == "solo/only.jpg"
        img_id = entry["id"]
        shard = img_id[:2]
        assert entry["full_url"] == f"/api/v2/images/{img_id}/full"
        assert entry["thumbnail_url"] == (
            f"/api/v2/images/{img_id}/thumb?width=320&height=240"
        )
        assert entry["full_static_url"] == "/static-photos/solo/only.jpg"
        assert entry["thumbnail_static_url"] == (
            f"/static-thumbs-320x240/{shard}/{img_id}_320x240.jpg"
        )


class TestBuildPortfolioTreeSubtree:
    def test_root_scopes_to_subtree(self, library, metadata_service, repo, root_id):
        library.add_jpeg("vacation/beach.jpg")
        library.add_jpeg("family/portrait.jpg")
        metadata_service.rescan(port_uuid=None, recursive=True)

        vacation_uuid = compute_port_uuid(root_id, "vacation")
        dump = export_service.build_portfolio_tree(
            repo, vacation_uuid, SCHEMA_VERSION, THUMBNAIL_SIZES,
            DEFAULT_STATIC_THUMBNAIL_SIZE, DEFAULT_STATIC_ICON_SIZE,
        )

        tree = dump["tree"]
        assert tree["name"] == "vacation"
        assert dump["root_port_uuid"] == str(vacation_uuid)
        # family is a sibling, not a descendant -> absent from the subtree.
        assert _find(tree, "family") is None

    def test_nonexistent_root_raises_not_found(self, repo):
        with pytest.raises(NotFoundError) as excinfo:
            export_service.build_portfolio_tree(repo, uuid4(), SCHEMA_VERSION, THUMBNAIL_SIZES, DEFAULT_STATIC_THUMBNAIL_SIZE, DEFAULT_STATIC_ICON_SIZE)
        assert excinfo.value.code == "PORTFOLIO_NOT_FOUND"


# --------------------------------------------------------------------------
# Cross-portfolio duplicate: full metadata repeated in both portfolios
# --------------------------------------------------------------------------
class TestDuplicateRepresentation:
    def test_duplicate_appears_in_both_portfolios_with_same_id(
        self, library, metadata_service, repo, root_id
    ):
        library.add_bytes("alpha/pic.jpg", b"DUP-BYTES")
        library.add_bytes("beta/copy.jpg", b"DUP-BYTES")
        metadata_service.rescan(port_uuid=None, recursive=True)

        tree = export_service.build_portfolio_tree(repo, None, SCHEMA_VERSION, THUMBNAIL_SIZES, DEFAULT_STATIC_THUMBNAIL_SIZE, DEFAULT_STATIC_ICON_SIZE)["tree"]
        alpha = _find(tree, "alpha")
        beta = _find(tree, "beta")

        assert len(alpha["images"]) == 1
        assert len(beta["images"]) == 1
        alpha_entry = alpha["images"][0]
        beta_entry = beta["images"][0]

        # Same content id (Img_UUID), independent placement metadata.
        assert alpha_entry["id"] == beta_entry["id"]
        assert alpha_entry["content_hash"] == beta_entry["content_hash"]
        assert alpha_entry["name"] == "pic.jpg"
        assert beta_entry["name"] == "copy.jpg"
        assert alpha_entry["rel_path"] == "alpha/pic.jpg"
        assert beta_entry["rel_path"] == "beta/copy.jpg"
        # Tags live on the content identity -> identical for both entries.
        assert alpha_entry["tags"] == beta_entry["tags"]


# --------------------------------------------------------------------------
# Virtual portfolio: sort_order / alternate_name populated; null for physical
# --------------------------------------------------------------------------
class TestVirtualPortfolio:
    def test_virtual_entries_have_sort_order_physical_are_null(
        self, library, metadata_service, repo, root_id
    ):
        library.add_bytes("a.jpg", b"virt-a")
        library.add_bytes("b.jpg", b"virt-b")
        # A physical portfolio holding one of the images too.
        library.add_bytes("phys/a.jpg", b"virt-a")
        library.add_meta("gallery", {
            "virtual": True,
            "images": [
                {"rel_path": "b.jpg", "alternate_name": "Bee"},
                {"rel_path": "a.jpg"},
            ],
        })
        result = metadata_service.rescan(port_uuid=None, recursive=True)
        assert [e for e in result.errors if "not yet indexed" in e] == [], result.errors

        tree = export_service.build_portfolio_tree(repo, None, SCHEMA_VERSION, THUMBNAIL_SIZES, DEFAULT_STATIC_THUMBNAIL_SIZE, DEFAULT_STATIC_ICON_SIZE)["tree"]

        gallery = _find(tree, "gallery")
        assert gallery["virtual"] is True
        # sort_order follows the meta.json images[] sequence: b then a.
        assert [e["name"] for e in gallery["images"]] == ["b.jpg", "a.jpg"]
        assert [e["sort_order"] for e in gallery["images"]] == [0, 1]
        assert gallery["images"][0]["alternate_name"] == "Bee"

        # Physical portfolio entries carry null sort_order/alternate_name.
        phys = _find(tree, "phys")
        assert phys["virtual"] is False
        assert phys["images"][0]["sort_order"] is None
        assert phys["images"][0]["alternate_name"] is None


# --------------------------------------------------------------------------
# CLI: dump-tree round-trip + error handling
# --------------------------------------------------------------------------
def _write_config(tmp_path, library, cache_root):
    """A minimal YAML config pointing at the temp fs/DB, matching the shape
    load_settings() expects (mirrors conftest._make_settings)."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "server:",
                "  host: 127.0.0.1",
                "  port: 0",
                "  workers: 1",
                "  cors_allowed_origins: []",
                "storage:",
                f"  base_root: {library.base_root}",
                "cache:",
                f"  cache_root: {cache_root}",
                "rescan:",
                "  on_startup: false",
                "  watch_filesystem: false",
                "  cycle_detection: true",
                "logging:",
                "  level: WARNING",
                f"  rescan_log_path: {tmp_path / 'logs' / 'rescan_history.jsonl'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


class TestDumpTreeCli:
    def test_cli_round_trip_writes_valid_json(
        self, tmp_path, library, cache_root, metadata_service, installation
    ):
        library.add_jpeg("vacation/beach.jpg")
        library.add_jpeg("family/portrait.jpg")
        metadata_service.rescan(port_uuid=None, recursive=True)

        config_path = _write_config(tmp_path, library, cache_root)
        output_path = tmp_path / "tree.json"
        manage.dump_tree(
            argparse.Namespace(
                config=str(config_path), output=str(output_path), root=None
            )
        )

        assert output_path.is_file()
        dump = json.loads(output_path.read_text(encoding="utf-8"))
        assert set(dump.keys()) == {
            "generated_at",
            "root_port_uuid",
            "schema_version",
            "tree",
        }
        assert dump["schema_version"] == installation.schema_version
        assert {c["name"] for c in dump["tree"]["children"]} == {
            "vacation",
            "family",
        }

    def test_cli_subtree_via_root_flag(
        self, tmp_path, library, cache_root, metadata_service, root_id
    ):
        library.add_jpeg("vacation/beach.jpg")
        library.add_jpeg("family/portrait.jpg")
        metadata_service.rescan(port_uuid=None, recursive=True)

        config_path = _write_config(tmp_path, library, cache_root)
        output_path = tmp_path / "subtree.json"
        vacation_uuid = compute_port_uuid(root_id, "vacation")
        manage.dump_tree(
            argparse.Namespace(
                config=str(config_path),
                output=str(output_path),
                root=str(vacation_uuid),
            )
        )

        dump = json.loads(output_path.read_text(encoding="utf-8"))
        assert dump["tree"]["name"] == "vacation"
        assert dump["root_port_uuid"] == str(vacation_uuid)

    def test_cli_nonexistent_root_exits_nonzero_no_file(
        self, tmp_path, library, cache_root, metadata_service
    ):
        library.add_jpeg("vacation/beach.jpg")
        metadata_service.rescan(port_uuid=None, recursive=True)

        config_path = _write_config(tmp_path, library, cache_root)
        output_path = tmp_path / "should_not_exist.json"
        with pytest.raises(SystemExit) as excinfo:
            manage.dump_tree(
                argparse.Namespace(
                    config=str(config_path),
                    output=str(output_path),
                    root=str(uuid4()),
                )
            )
        assert excinfo.value.code == 1
        assert not output_path.exists()
