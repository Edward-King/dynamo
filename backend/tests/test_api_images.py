"""
Endpoint-level tests for the image read path (api/routers/images.py):
pagination clamping, thumbnail size validation, and ETag conditional GETs.

Uses the full app via the TestClient fixture (real create_app factory,
temp fs/DB, startup rescan on the sample library).
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from photoshare.services.image_service import MAX_PAGE_SIZE
from photoshare.services.metadata_repository import NotFoundError


def _first_image_id(client, headers) -> str:
    """Navigate root -> vacation child -> first image, returning its UUID."""
    root = client.get("/api/v2/portfolios/root", headers=headers).json()
    children = client.get(
        f"/api/v2/portfolios/{root['id']}/children", headers=headers
    ).json()
    vacation = next(c for c in children if c["name"] == "vacation")
    images = client.get(
        f"/api/v2/portfolios/{vacation['id']}/images", headers=headers
    ).json()
    return images["items"][0]["id"], vacation["id"]


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------
class TestPagination:
    def test_page_size_clamped_to_max_in_service(
        self, sample_library, metadata_service, image_service, repo
    ):
        """ImageService.list_images clamps page_size to MAX_PAGE_SIZE (200)
        server-side, regardless of the requested value (§how-it-works #5).
        Verified at the service layer because the route additionally caps the
        query param at le=200 (see test_page_size_over_max_rejected_by_api)."""
        metadata_service.rescan(port_uuid=None, recursive=True)
        vacation_row = repo.get_portfolio_by_path("vacation")

        result = image_service.list_images(
            __import__("uuid").UUID(vacation_row["id"]),
            is_virtual=False, page=1, page_size=1000,
        )
        assert result.page_size == MAX_PAGE_SIZE

    def test_page_size_over_max_rejected_by_api(self, client, api_key_header):
        """The route bounds page_size at le=MAX_PAGE_SIZE, so an over-large
        value is a 422 VALIDATION_ERROR (normalized envelope)."""
        root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
        r = client.get(
            f"/api/v2/portfolios/{root['id']}/images",
            params={"page_size": MAX_PAGE_SIZE + 1},
            headers=api_key_header,
        )
        assert r.status_code == 422
        assert r.json()["code"] == "VALIDATION_ERROR"

    def test_valid_pagination_returns_paged_shape(self, client, api_key_header):
        _img_id, vacation_id = _first_image_id(client, api_key_header)
        r = client.get(
            f"/api/v2/portfolios/{vacation_id}/images",
            params={"page": 1, "page_size": 1}, headers=api_key_header,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2 and len(body["items"]) == 1 and body["has_next"] is True

    def test_paged_items_carry_all_four_url_fields(self, client, api_key_header):
        """PagedResult[ImgOut] wraps items built the same way as the
        single-item GET /images/{id} endpoint (TestGetImageMetadataEndpoint),
        but that assertion was never made for the *paged list* code path --
        confirm each item in a full (unpaginated-within-portfolio) page
        carries its own correctly-derived full_url/thumbnail_url/
        full_static_url/thumbnail_static_url, not just the id/name fields
        already covered by test_valid_pagination_returns_paged_shape."""
        _img_id, vacation_id = _first_image_id(client, api_key_header)
        r = client.get(
            f"/api/v2/portfolios/{vacation_id}/images",
            params={"page": 1, "page_size": 10}, headers=api_key_header,
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 2  # vacation has beach.jpg + mountain.jpg

        seen_ids = set()
        for item in items:
            img_id = item["id"]
            seen_ids.add(img_id)
            assert item["full_url"] == f"/api/v2/images/{img_id}/full"
            assert item["thumbnail_url"] == (
                f"/api/v2/images/{img_id}/thumb?width=320&height=240"
            )
            assert item["full_static_url"] == f"/static-photos/{item['rel_path']}"
            shard = img_id[:2]
            assert item["thumbnail_static_url"] == (
                f"/static-thumbs-320x240/{shard}/{img_id}_320x240.jpg"
            )
        # Each item's URLs are genuinely per-item, not a copy of one shared value.
        assert len(seen_ids) == 2


# --------------------------------------------------------------------------
# Thumbnail size validation ([16, 4096])
# --------------------------------------------------------------------------
class TestThumbnailSizeValidation:
    @pytest.mark.parametrize("width,height", [(15, 240), (4097, 240), (320, 15), (320, 5000)])
    def test_out_of_range_thumb_size_rejected_by_api(
        self, client, api_key_header, width, height
    ):
        """width/height outside [16, 4096] are rejected by the Query bounds
        as a 422 VALIDATION_ERROR (api/routers/images.py get_image_thumb)."""
        img_id, _ = _first_image_id(client, api_key_header)
        r = client.get(
            f"/api/v2/images/{img_id}/thumb",
            params={"width": width, "height": height}, headers=api_key_header,
        )
        assert r.status_code == 422
        assert r.json()["code"] == "VALIDATION_ERROR"

    def test_in_range_thumb_size_succeeds(self, client, api_key_header):
        img_id, _ = _first_image_id(client, api_key_header)
        r = client.get(
            f"/api/v2/images/{img_id}/thumb",
            params={"width": 320, "height": 240}, headers=api_key_header,
        )
        assert r.status_code == 200
        assert r.headers.get("content-type") == "image/jpeg"

    def test_thumbnail_service_rejects_out_of_range_directly(
        self, thumbnail_service, sample_library
    ):
        """Unit-level: ThumbnailService raises InvalidThumbnailSizeError
        (-> 400 INVALID_THUMBNAIL_SIZE handler) for out-of-range dims."""
        from photoshare.services.thumbnail_service import InvalidThumbnailSizeError

        src = sample_library.base_root / "vacation" / "beach.jpg"
        with pytest.raises(InvalidThumbnailSizeError):
            thumbnail_service.get_or_create(
                __import__("uuid").uuid4(), src, width=9999, height=240
            )


# --------------------------------------------------------------------------
# ETag / conditional GET (content-addressed identity => free 304s)
# --------------------------------------------------------------------------
class TestConditionalGet:
    def test_full_image_returns_etag_then_304_on_match(self, client, api_key_header):
        """A repeated /full GET with If-None-Match equal to the returned
        ETag yields a bare 304 (api/routers/images.py get_image_full)."""
        img_id, _ = _first_image_id(client, api_key_header)

        first = client.get(f"/api/v2/images/{img_id}/full", headers=api_key_header)
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag is not None

        second = client.get(
            f"/api/v2/images/{img_id}/full",
            headers={**api_key_header, "If-None-Match": etag},
        )
        assert second.status_code == 304

    def test_full_image_non_matching_etag_returns_200(self, client, api_key_header):
        img_id, _ = _first_image_id(client, api_key_header)
        r = client.get(
            f"/api/v2/images/{img_id}/full",
            headers={**api_key_header, "If-None-Match": '"stale-etag"'},
        )
        assert r.status_code == 200


# --------------------------------------------------------------------------
# GET /api/v2/images/{img_uuid} -- direct-by-id metadata endpoint
# --------------------------------------------------------------------------
class TestGetImageMetadataEndpoint:
    def test_returns_imgout_shape_with_null_virtual_fields(self, client, api_key_header):
        """Happy path: direct-by-id lookup returns the ImgOut shape;
        alternate_name/sort_order are null because this is not resolved
        through a virtual-portfolio placement (matches search_images)."""
        img_id, _vac = _first_image_id(client, api_key_header)
        r = client.get(f"/api/v2/images/{img_id}", headers=api_key_header)
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {
            "id", "name", "rel_path", "width", "height", "size_bytes",
            "mime_type", "file_modified_at", "taken_at", "tags",
            "full_url", "thumbnail_url", "full_static_url", "thumbnail_static_url",
            "thumbnail_static_urls",
            "alternate_name", "sort_order",
        }
        assert body["id"] == img_id
        assert body["alternate_name"] is None
        assert body["sort_order"] is None
        # The four access URLs are always populated strings (§static-mount).
        assert body["full_url"] == f"/api/v2/images/{img_id}/full"
        assert body["thumbnail_url"] == (
            f"/api/v2/images/{img_id}/thumb?width=320&height=240"
        )
        assert body["full_static_url"] == f"/static-photos/{body['rel_path']}"
        shard = str(img_id)[:2]
        assert body["thumbnail_static_url"] == (
            f"/static-thumbs-320x240/{shard}/{img_id}_320x240.jpg"
        )

    def test_unknown_id_returns_404_image_not_found(self, client, api_key_header):
        r = client.get(f"/api/v2/images/{uuid4()}", headers=api_key_header)
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "IMAGE_NOT_FOUND"
        assert set(body.keys()) == {"code", "message", "detail"}
        assert body["detail"] is None

    def test_requires_auth(self, client):
        """No credential -> 401 AUTH_MISSING (same enforcement as every
        non-health route)."""
        r = client.get(f"/api/v2/images/{uuid4()}")
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_MISSING"

    def test_does_not_shadow_full_or_thumb_routes(self, client, api_key_header):
        """The catch-all /images/{img_uuid} must not swallow the more
        specific /full and /thumb sub-routes."""
        img_id, _vac = _first_image_id(client, api_key_header)
        full = client.get(f"/api/v2/images/{img_id}/full", headers=api_key_header)
        assert full.status_code == 200
        thumb = client.get(
            f"/api/v2/images/{img_id}/thumb",
            params={"width": 64, "height": 64}, headers=api_key_header,
        )
        assert thumb.status_code == 200
        assert thumb.headers.get("content-type") == "image/jpeg"


# --------------------------------------------------------------------------
# ImageService resolution paths (service-level, no HTTP)
# --------------------------------------------------------------------------
def _first_img_uuid(db) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT img_uuid, rel_path FROM image_locations ORDER BY rel_path LIMIT 1")
        row = cur.fetchone()
    return row["img_uuid"], row["rel_path"]


class TestImageServiceResolution:
    def test_get_image_metadata_returns_placement_fields(
        self, library, metadata_service, image_service, db
    ):
        library.add_bytes("p/a.jpg", b"content-a")
        metadata_service.rescan(port_uuid=None, recursive=True)
        img_uuid, _rel = _first_img_uuid(db)

        out = image_service.get_image_metadata(UUID(img_uuid))
        assert str(out.id) == img_uuid
        assert out.name == "a.jpg"
        assert out.rel_path == "p/a.jpg"

    def test_get_image_metadata_unknown_raises_not_found(self, image_service):
        with pytest.raises(NotFoundError):
            image_service.get_image_metadata(uuid4())

    def test_resolve_for_streaming_returns_real_path_and_hash(
        self, library, metadata_service, image_service, db
    ):
        library.add_bytes("p/a.jpg", b"stream-me")
        metadata_service.rescan(port_uuid=None, recursive=True)
        img_uuid, _rel = _first_img_uuid(db)

        resolved = image_service.resolve_for_streaming(UUID(img_uuid))
        assert resolved.real_path.is_file()
        assert resolved.content_hash
        with image_service.open_stream(resolved.real_path) as fh:
            assert fh.read() == b"stream-me"

    def test_resolve_for_streaming_unknown_uuid_raises_not_found(self, image_service):
        """No identity_registry entry -> resolve_path None -> 404."""
        with pytest.raises(NotFoundError):
            image_service.resolve_for_streaming(uuid4())

    def test_resolve_for_streaming_file_removed_from_disk_raises_not_found(
        self, library, metadata_service, image_service, db
    ):
        """Registry still resolves the rel_path, but the file is gone from
        disk (no rescan yet) -> is_file() False -> 404."""
        library.add_bytes("p/a.jpg", b"about-to-vanish")
        metadata_service.rescan(port_uuid=None, recursive=True)
        img_uuid, rel = _first_img_uuid(db)

        (library.base_root / rel).unlink()
        with pytest.raises(NotFoundError):
            image_service.resolve_for_streaming(UUID(img_uuid))

    def test_resolve_for_streaming_missing_content_row_raises_not_found(
        self, library, metadata_service, image_service, db
    ):
        """Synthetic inconsistent state: the identity_registry entry and the
        on-disk file both survive, but the `images` content row is gone (its
        image_locations rows cascade away). get_image_row then returns None,
        exercising the row-is-None 404 branch."""
        library.add_bytes("p/a.jpg", b"orphaned")
        metadata_service.rescan(port_uuid=None, recursive=True)
        img_uuid, _rel = _first_img_uuid(db)

        with db.cursor() as cur:
            cur.execute("DELETE FROM images WHERE id = ?", (img_uuid,))

        with pytest.raises(NotFoundError):
            image_service.resolve_for_streaming(UUID(img_uuid))
