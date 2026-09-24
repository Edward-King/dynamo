"""
Cross-portfolio duplicate tests (schema_version 2, image_locations join).

Before the fix, `images.id` (= Img_UUID = uuid5(root_id, content_hash)) was
the sole primary key and carried the placement columns (rel_path,
parent_port_uuid, ...). Two byte-identical files in different portfolio
directories computed the SAME Img_UUID and collided on that PK: the
last-written copy overwrote the first, so one portfolio silently lost the
image and the surviving placement was unstable across rescans.

After the fix, `images` is content-only and every physical placement lives
in `image_locations` keyed by (img_uuid, port_uuid, rel_path). One shared
content row can now back many placements, so a duplicate is listed in BOTH
portfolios and neither copy is lost.
"""
from __future__ import annotations

from uuid import UUID

from photoshare.identity.models import compute_img_uuid, compute_port_uuid


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
        return cur.fetchone()["c"]


def _locations_for(db, img_uuid: str):
    with db.cursor() as cur:
        cur.execute(
            "SELECT port_uuid, rel_path FROM image_locations WHERE img_uuid = ? "
            "ORDER BY rel_path",
            (img_uuid,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------
# Service / repository layer
# --------------------------------------------------------------------------
class TestDuplicatePlacementsCoexist:
    def test_byte_identical_files_produce_one_content_row_two_locations(
        self, library, metadata_service, db, root_id
    ):
        library.add_bytes("alpha/pic.jpg", b"SAME-BYTES-HERE")
        library.add_bytes("beta/copy.jpg", b"SAME-BYTES-HERE")

        result = metadata_service.rescan(port_uuid=None, recursive=True)

        content_hash = metadata_service._storage.compute_content_hash("alpha/pic.jpg")
        img_uuid = str(compute_img_uuid(root_id, content_hash))

        # One content row, two placements -> nothing was overwritten.
        assert _count(db, "images") == 1
        locs = _locations_for(db, img_uuid)
        assert len(locs) == 2
        alpha_uuid = str(compute_port_uuid(root_id, "alpha"))
        beta_uuid = str(compute_port_uuid(root_id, "beta"))
        assert {r["port_uuid"] for r in locs} == {alpha_uuid, beta_uuid}
        # Both placements counted as adds (new (img_uuid, port_uuid) pairs).
        assert result.images_added == 2

    def test_duplicate_listed_in_both_portfolios(
        self, library, metadata_service, repo, image_service, db, root_id
    ):
        library.add_bytes("alpha/pic.jpg", b"DUPLICATE-CONTENT")
        library.add_bytes("beta/copy.jpg", b"DUPLICATE-CONTENT")
        metadata_service.rescan(port_uuid=None, recursive=True)

        content_hash = metadata_service._storage.compute_content_hash("alpha/pic.jpg")
        img_uuid = str(compute_img_uuid(root_id, content_hash))
        alpha_uuid = UUID(str(compute_port_uuid(root_id, "alpha")))
        beta_uuid = UUID(str(compute_port_uuid(root_id, "beta")))

        alpha_imgs = image_service.list_images(alpha_uuid, False, page=1, page_size=50)
        beta_imgs = image_service.list_images(beta_uuid, False, page=1, page_size=50)

        assert [str(i.id) for i in alpha_imgs.items] == [img_uuid]
        assert [str(i.id) for i in beta_imgs.items] == [img_uuid]
        # Each portfolio reports the placement in its direct count.
        assert repo.direct_image_count(alpha_uuid) == 1
        assert repo.direct_image_count(beta_uuid) == 1

    def test_removing_one_copy_keeps_the_other_and_content_row(
        self, library, metadata_service, db, root_id
    ):
        library.add_bytes("alpha/pic.jpg", b"KEEP-ONE")
        library.add_bytes("beta/copy.jpg", b"KEEP-ONE")
        metadata_service.rescan(port_uuid=None, recursive=True)

        content_hash = metadata_service._storage.compute_content_hash("alpha/pic.jpg")
        img_uuid = str(compute_img_uuid(root_id, content_hash))

        # Delete only the beta copy.
        (library.base_root / "beta" / "copy.jpg").unlink()
        result = metadata_service.rescan(port_uuid=None, recursive=True)

        assert result.images_removed == 1
        # Content row survives because alpha still references it.
        assert _count(db, "images") == 1
        locs = _locations_for(db, img_uuid)
        assert [r["rel_path"] for r in locs] == ["alpha/pic.jpg"]
        # Identity NOT tombstoned -- a placement still exists.
        with db.cursor() as cur:
            cur.execute(
                "SELECT tombstoned_at FROM identity_registry WHERE uuid = ?", (img_uuid,)
            )
            assert cur.fetchone()["tombstoned_at"] is None

    def test_removing_last_copy_drops_content_row_and_tombstones(
        self, library, metadata_service, db, root_id
    ):
        library.add_bytes("alpha/pic.jpg", b"DROP-ALL")
        library.add_bytes("beta/copy.jpg", b"DROP-ALL")
        metadata_service.rescan(port_uuid=None, recursive=True)

        content_hash = metadata_service._storage.compute_content_hash("alpha/pic.jpg")
        img_uuid = str(compute_img_uuid(root_id, content_hash))

        (library.base_root / "alpha" / "pic.jpg").unlink()
        (library.base_root / "beta" / "copy.jpg").unlink()
        result = metadata_service.rescan(port_uuid=None, recursive=True)

        assert result.images_removed == 2
        assert _count(db, "images") == 0
        assert _locations_for(db, img_uuid) == []
        with db.cursor() as cur:
            cur.execute(
                "SELECT tombstoned_at FROM identity_registry WHERE uuid = ?", (img_uuid,)
            )
            assert cur.fetchone()["tombstoned_at"] is not None


# --------------------------------------------------------------------------
# HTTP layer: a duplicate appears in BOTH portfolios' /images listings
# --------------------------------------------------------------------------
class TestDuplicateOverHttp:
    def test_duplicate_visible_in_both_portfolios_over_http(
        self, client, api_key_header, assert_json
    ):
        """Inject a byte-identical copy of vacation/beach.jpg into the family
        portfolio, rescan, and confirm GET /portfolios/{id}/images returns
        the shared Img_UUID in BOTH vacation and family (pre-fix, one of the
        two would have been missing)."""
        base_root = client.app.state.storage.base_root

        root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
        children = client.get(
            f"/api/v2/portfolios/{root['id']}/children", headers=api_key_header
        ).json()
        vacation = next(c for c in children if c["name"] == "vacation")
        family = next(c for c in children if c["name"] == "family")

        vac_imgs = client.get(
            f"/api/v2/portfolios/{vacation['id']}/images", headers=api_key_header
        ).json()
        beach = next(i for i in vac_imgs["items"] if i["name"] == "beach.jpg")

        # Copy the exact bytes into the family directory -> identical Img_UUID.
        src_bytes = (base_root / "vacation" / "beach.jpg").read_bytes()
        (base_root / "family" / "beach_dupe.jpg").write_bytes(src_bytes)

        r = client.post(
            "/api/v2/admin/rescan", json={"port_uuid": None, "recursive": True},
            headers=api_key_header,
        )
        assert r.status_code == 200

        vac_after = client.get(
            f"/api/v2/portfolios/{vacation['id']}/images", headers=api_key_header
        ).json()
        fam_after = client.get(
            f"/api/v2/portfolios/{family['id']}/images", headers=api_key_header
        ).json()

        vac_ids = {i["id"] for i in vac_after["items"]}
        fam_ids = {i["id"] for i in fam_after["items"]}

        # The shared content id is present in BOTH portfolios.
        assert beach["id"] in vac_ids
        assert beach["id"] in fam_ids
        # The family listing shows the copy under its own filename. Uses
        # assert_json (see tests/conftest.py) rather than a plain `==` so a
        # failure here prints the full placement dict as real JSON text
        # (null/true/false) instead of Python's None/True/False repr --
        # this is the one assertion in the suite comparing a whole
        # response-shaped dict, where that readability difference matters
        # most.
        dupe = next(i for i in fam_after["items"] if i["id"] == beach["id"])
        assert_json(
            dupe,
            {
                "id": beach["id"],
                "name": "beach_dupe.jpg",
                "rel_path": "family/beach_dupe.jpg",
                "width": beach["width"],
                "height": beach["height"],
                "size_bytes": beach["size_bytes"],
                "mime_type": beach["mime_type"],
                "file_modified_at": dupe["file_modified_at"],
                "taken_at": dupe["taken_at"],
                "tags": [],
                "full_url": f"/api/v2/images/{beach['id']}/full",
                "thumbnail_url": (
                    f"/api/v2/images/{beach['id']}/thumb?width=320&height=240"
                ),
                "full_static_url": "/static-photos/family/beach_dupe.jpg",
                "thumbnail_static_url": (
                    f"/static-thumbs-320x240/{beach['id'][:2]}/{beach['id']}_320x240.jpg"
                ),
                "thumbnail_static_urls": {
                    "320x240": (
                        f"/static-thumbs-320x240/{beach['id'][:2]}/{beach['id']}_320x240.jpg"
                    ),
                    "800x600": (
                        f"/static-thumbs-800x600/{beach['id'][:2]}/{beach['id']}_800x600.jpg"
                    ),
                },
                "alternate_name": None,
                "sort_order": None,
            },
            msg="family placement of the duplicated beach.jpg did not match expected shape",
        )
