"""
ExportService: offline portfolio-tree dump (used by the `dump-tree` CLI).

Walks the portfolio tree (root, or an optional subtree) and returns a single
JSON-able dict with full portfolio metadata and full, per-portfolio image
metadata. This is a data-export/backup view, so it deliberately includes a
few raw fields the live `ImgOut` API contract omits (e.g. content_hash,
file_modified_at, is_symlink).

Cross-portfolio duplicates are NOT normalized: a byte-identical image placed
in two portfolios appears as a full, independently-nested entry in each
portfolio's `images` array -- both entries share the same `id` (Img_UUID) but
carry their own placement-specific name/rel_path. Tags live on the content
identity (image_tags is keyed by img_uuid), so both entries report the same
tags.

The module is DB-only (no filesystem, no FastAPI), reusing existing
MetadataRepository methods, so an API endpoint could later call
build_portfolio_tree() unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from photoshare.services.metadata_repository import (
    PORTFOLIO_NOT_FOUND,
    MetadataRepository,
    NotFoundError,
)
from photoshare.utils.urls import (
    image_full_static_url,
    image_full_url,
    image_thumbnail_static_url,
    image_thumbnail_url,
)


def build_portfolio_tree(
    repo: MetadataRepository,
    root_port_uuid: Optional[UUID],
    schema_version: int,
    thumbnail_sizes: list[tuple[int, int]],
    default_static_thumbnail_size: tuple[int, int],
    default_static_icon_size: tuple[int, int],
) -> dict:
    """Return the top-level dump dict rooted at root_port_uuid (or the tree
    root if None). Raises NotFoundError(PORTFOLIO_NOT_FOUND) if an explicit
    root_port_uuid does not exist -- callers should surface this as a clear
    error and a non-zero exit rather than writing a partial file."""
    if root_port_uuid is None:
        root_row = repo.get_root_portfolio_row()
        if root_row is None:
            raise NotFoundError(
                PORTFOLIO_NOT_FOUND,
                "No root portfolio exists yet -- has an initial rescan run?",
            )
    else:
        root_row = repo.get_portfolio_row(root_port_uuid)
        if root_row is None:
            raise NotFoundError(
                PORTFOLIO_NOT_FOUND, f"No portfolio found for id {root_port_uuid}"
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root_port_uuid": root_row["id"],
        "schema_version": schema_version,
        "tree": _build_node(
            repo,
            root_row,
            thumbnail_sizes,
            default_static_thumbnail_size,
            default_static_icon_size,
        ),
    }


def _build_node(
    repo: MetadataRepository,
    row,
    thumbnail_sizes: list[tuple[int, int]],
    default_static_thumbnail_size: tuple[int, int],
    default_static_icon_size: tuple[int, int],
) -> dict:
    port_uuid = UUID(row["id"])
    is_virtual = bool(row["virtual"])

    images = [
        _image_entry(repo, r, thumbnail_sizes, default_static_thumbnail_size)
        for r in repo.list_images_for_portfolio(port_uuid, is_virtual)
    ]
    children = [
        _build_node(
            repo,
            child_row,
            thumbnail_sizes,
            default_static_thumbnail_size,
            default_static_icon_size,
        )
        for child_row in repo.list_children_rows(port_uuid)
    ]

    icon_id = UUID(row["icon_image_id"]) if row["icon_image_id"] else None
    # Icon uses its own configured default size, independent of the per-image
    # default threaded down to _image_entry.
    first_w, first_h = default_static_icon_size
    return {
        "id": row["id"],
        "name": row["name"],
        "rel_path": row["rel_path"],
        "description": row["description"] or "",
        "virtual": is_virtual,
        "tags": repo._get_portfolio_tags(port_uuid),
        "icon_image_id": row["icon_image_id"],
        "icon_thumbnail_url": image_thumbnail_url(icon_id) if icon_id else None,
        "icon_thumbnail_static_url": (
            image_thumbnail_static_url(icon_id, first_w, first_h, thumbnail_sizes)
            if icon_id
            else None
        ),
        "direct_image_count": repo.direct_image_count(port_uuid),
        "total_image_count": repo.total_image_count(port_uuid),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "images": images,
        "children": children,
    }


def _image_entry(
    repo: MetadataRepository,
    row,
    thumbnail_sizes: list[tuple[int, int]],
    default_static_thumbnail_size: tuple[int, int],
) -> dict:
    """Full per-placement image metadata. alternate_name/sort_order are only
    present on virtual-membership rows (same rule as ImgOut._row_to_out), so
    they are read defensively and default to None for physical placements."""
    keys = row.keys()
    img_id = UUID(row["id"])
    first_w, first_h = default_static_thumbnail_size
    return {
        "id": row["id"],
        "name": row["name"],
        "rel_path": row["rel_path"],
        "width": row["width"],
        "height": row["height"],
        "size_bytes": row["size_bytes"],
        "mime_type": row["mime_type"],
        "content_hash": row["content_hash"],
        "file_modified_at": row["file_modified_at"],
        "taken_at": row["taken_at"],
        "is_symlink": bool(row["is_symlink"]),
        "tags": repo._get_image_tags(img_id),
        "full_url": image_full_url(img_id),
        "thumbnail_url": image_thumbnail_url(img_id),
        "full_static_url": image_full_static_url(row["rel_path"]),
        "thumbnail_static_url": image_thumbnail_static_url(
            img_id, first_w, first_h, thumbnail_sizes
        ),
        "alternate_name": row["alternate_name"] if "alternate_name" in keys else None,
        "sort_order": row["sort_order"] if "sort_order" in keys else None,
    }


def collect_image_uuids(
    repo: MetadataRepository,
    root_port_uuid: Optional[UUID],
    recursive: bool = True,
) -> set[UUID]:
    """Return the set of unique Img_UUIDs placed in the portfolio subtree
    rooted at root_port_uuid (or the tree root if None). Reuses the same
    list_images_for_portfolio / list_children_rows walk as build_portfolio_tree
    so tree-traversal logic lives in one place. Raises
    NotFoundError(PORTFOLIO_NOT_FOUND) if an explicit root does not exist."""
    if root_port_uuid is None:
        root_row = repo.get_root_portfolio_row()
        if root_row is None:
            raise NotFoundError(
                PORTFOLIO_NOT_FOUND,
                "No root portfolio exists yet -- has an initial rescan run?",
            )
    else:
        root_row = repo.get_portfolio_row(root_port_uuid)
        if root_row is None:
            raise NotFoundError(
                PORTFOLIO_NOT_FOUND, f"No portfolio found for id {root_port_uuid}"
            )

    uuids: set[UUID] = set()
    _collect_uuids(repo, root_row, recursive, uuids)
    return uuids


def _collect_uuids(
    repo: MetadataRepository, row, recursive: bool, uuids: set[UUID]
) -> None:
    port_uuid = UUID(row["id"])
    is_virtual = bool(row["virtual"])
    for r in repo.list_images_for_portfolio(port_uuid, is_virtual):
        uuids.add(UUID(r["id"]))
    if recursive:
        for child_row in repo.list_children_rows(port_uuid):
            _collect_uuids(repo, child_row, recursive, uuids)


def summarize_tree(node: dict) -> tuple[int, int]:
    """Count (portfolios, images) in a built tree node, recursively. Images
    are counted per-placement (duplicates counted in each portfolio), matching
    how they appear in the dump."""
    portfolios = 1
    images = len(node["images"])
    for child in node["children"]:
        c_portfolios, c_images = summarize_tree(child)
        portfolios += c_portfolios
        images += c_images
    return portfolios, images
