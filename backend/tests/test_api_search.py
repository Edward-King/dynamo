"""
Endpoint-level tests for the /api/v2/search/* routes (api/routers/search.py):
GET /search/portfolios?tag= and GET /search/images?tag=.

photoshare/services/search_service.py's own test file (test_search_service.py)
tests SearchService directly and its docstring used to claim "there is no
API-level search router in this codebase" -- that claim was incorrect and has
been corrected there. search.py defines real HTTP routes wired into
create_app() via app.include_router(search.router, prefix="/api/v2") in
api/main.py. This file covers those routes for both response models, on both
the empty-match and populated-match cases:

- /search/portfolios: PortfolioOut results carry icon_thumbnail_url /
  icon_thumbnail_static_url correctly on a populated (tag-matched) result,
  not just on a direct /portfolios/{id} fetch (test_api_portfolios.py).
- /search/images: ImgOut results carry all four full_url/thumbnail_url/
  full_static_url/thumbnail_static_url fields correctly on a populated
  (tag-matched) result, not just on a direct /images/{id} fetch
  (test_api_images.py's TestGetImageMetadataEndpoint).

Both confirm the search code path (SearchService's SQL join + PortfolioOut/
ImgOut construction) doesn't drop or bypass the same field-derivation logic
used by the direct-fetch and list routes -- a gap that existed here until
these tests were added, mirroring the same class of gap found and fixed on
the paged image-list endpoint (see test_api_images.py's
test_paged_items_carry_all_four_url_fields) and the single-portfolio-by-id
endpoint (see test_api_portfolios.py's
test_get_portfolio_by_id_endpoint_carries_icon_urls).

Uses the full app via the TestClient `client` fixture (real create_app
factory, temp fs/DB, startup rescan on the sample library). vacation has
direct images (tags "2026", "vacation" per conftest's sample_library), so it
resolves an icon and is a suitable case for asserting populated icon URLs in
a filtered search result. image_tags (read by search_images_by_tag) is not
populated by any rescan path, so the image-tag test seeds it directly via the
app's own db handle -- the same approach test_search_service.py uses at the
service layer.
"""
from __future__ import annotations


def test_search_portfolios_by_tag_includes_icon_urls(client, api_key_header):
    """A tag-filtered portfolio search result carries the same icon URL
    fields (populated) as a direct fetch -- the search code path must not
    drop or bypass PortfolioOut's icon derivation."""
    resp = client.get(
        "/api/v2/search/portfolios", params={"tag": "vacation"}, headers=api_key_header
    )
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    vacation = results[0]
    assert vacation["name"] == "vacation"

    icon_id = vacation["icon_image_id"]
    assert icon_id is not None
    assert vacation["icon_thumbnail_url"] == (
        f"/api/v2/images/{icon_id}/thumb?width=320&height=240"
    )
    shard = icon_id[:2]
    assert vacation["icon_thumbnail_static_url"] == (
        f"/static-thumbs-320x240/{shard}/{icon_id}_320x240.jpg"
    )


def test_search_portfolios_by_tag_no_match_returns_empty_list(client, api_key_header):
    """A tag with no matching portfolio returns an empty list -- no icon
    fields to check, but confirms the route itself behaves like the
    underlying service method for the zero-match case."""
    resp = client.get(
        "/api/v2/search/portfolios", params={"tag": "no-such-tag"}, headers=api_key_header
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_images_by_tag_shape_unaffected(client, api_key_header):
    """Sanity check that /search/images (ImgOut results, not PortfolioOut)
    still returns its own always-populated URL fields -- unrelated to the
    Optional icon fields, but confirms the search router's two routes are
    both live and correctly wired to their respective response models."""
    resp = client.get(
        "/api/v2/search/images", params={"tag": "no-such-tag"}, headers=api_key_header
    )
    assert resp.status_code == 200
    assert resp.json() == []


def _tag_first_vacation_image(client, api_key_header) -> str:
    """Seed image_tags directly for vacation/beach.jpg (search_images_by_tag
    reads image_tags, which no rescan path populates -- same seeding
    approach as test_search_service.py's _tag_image helper, but through the
    HTTP layer's own db handle since this file has no direct db fixture)."""
    root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
    children = client.get(
        f"/api/v2/portfolios/{root['id']}/children", headers=api_key_header
    ).json()
    vacation = next(c for c in children if c["name"] == "vacation")
    images = client.get(
        f"/api/v2/portfolios/{vacation['id']}/images", headers=api_key_header
    ).json()["items"]
    beach = next(i for i in images if i["name"] == "beach.jpg")

    with client.app.state.db.cursor() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO image_tags (img_uuid, tag) VALUES (?, ?)",
            (beach["id"], "sunset"),
        )
    return beach["id"]


def test_search_images_by_tag_includes_all_url_fields(client, api_key_header):
    """A tag-filtered image search result must carry the same four
    always-populated URL fields as a direct fetch (TestGetImageMetadataEndpoint
    in test_api_images.py) -- the previous test only checked the zero-match
    empty-list case, never a populated match, so the search code path's
    field-building was unverified."""
    img_id = _tag_first_vacation_image(client, api_key_header)

    resp = client.get(
        "/api/v2/search/images", params={"tag": "sunset"}, headers=api_key_header
    )
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    beach = results[0]
    assert beach["id"] == img_id

    assert beach["full_url"] == f"/api/v2/images/{img_id}/full"
    assert beach["thumbnail_url"] == (
        f"/api/v2/images/{img_id}/thumb?width=320&height=240"
    )
    assert beach["full_static_url"] == f"/static-photos/{beach['rel_path']}"
    shard = img_id[:2]
    assert beach["thumbnail_static_url"] == (
        f"/static-thumbs-320x240/{shard}/{img_id}_320x240.jpg"
    )
