"""
SearchService (services/search_service.py) tag-search tests.

Covers both public methods -- search_portfolios_by_tag() and
search_images_by_tag() -- with zero / one / multiple matches and the
case-insensitive COLLATE NOCASE match, at the service layer directly
(constructed in-test, no HTTP).

There IS an API-level search router (photoshare/api/routers/search.py,
GET /api/v2/search/portfolios and GET /api/v2/search/images, wired into
create_app() in api/main.py) -- see tests/test_api_search.py for the
endpoint-level tests, including icon_thumbnail_url/icon_thumbnail_static_url
assertions on filtered PortfolioOut search results. This file's tests
don't duplicate those: this file exercises SearchService's SQL/matching
logic directly; test_api_search.py exercises the route wiring and response
model shape through the full app.

SearchService has no conftest fixture, so it is constructed in-test from the
existing db + portfolio_service + image_service fixtures (its real
collaborators). portfolio_tags is populated by rescan from meta.json tags;
image_tags is not written by any rescan path (it is a schema-defined table
read only by search), so the image-tag tests seed it directly -- the same
way production tagging would.
"""
from __future__ import annotations

from photoshare.services.search_service import SearchService


def _img_uuid_at(db, rel_path: str) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT img_uuid FROM image_locations WHERE rel_path = ?", (rel_path,))
        return cur.fetchone()["img_uuid"]


def _tag_image(db, img_uuid: str, tag: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO image_tags (img_uuid, tag) VALUES (?, ?)",
            (img_uuid, tag),
        )


class TestSearchPortfoliosByTag:
    def test_no_match_returns_empty_list(
        self, library, metadata_service, db, portfolio_service, image_service
    ):
        library.add_bytes("trip/a.jpg", b"trip-a")
        library.add_meta("trip", {"tags": ["summer"]})
        metadata_service.rescan(port_uuid=None, recursive=True)

        svc = SearchService(db, portfolio_service, image_service)
        assert svc.search_portfolios_by_tag("winter") == []

    def test_single_match_returns_that_portfolio(
        self, library, metadata_service, db, portfolio_service, image_service, root_id
    ):
        from photoshare.identity.models import compute_port_uuid

        library.add_bytes("trip/a.jpg", b"trip-a")
        library.add_meta("trip", {"tags": ["summer", "2026"]})
        metadata_service.rescan(port_uuid=None, recursive=True)

        svc = SearchService(db, portfolio_service, image_service)
        results = svc.search_portfolios_by_tag("2026")
        assert [str(p.id) for p in results] == [str(compute_port_uuid(root_id, "trip"))]
        assert results[0].name == "trip"

    def test_multiple_portfolios_share_a_tag(
        self, library, metadata_service, db, portfolio_service, image_service
    ):
        library.add_bytes("a/x.jpg", b"a-x")
        library.add_bytes("b/y.jpg", b"b-y")
        library.add_meta("a", {"tags": ["shared"]})
        library.add_meta("b", {"tags": ["shared"]})
        metadata_service.rescan(port_uuid=None, recursive=True)

        svc = SearchService(db, portfolio_service, image_service)
        results = svc.search_portfolios_by_tag("shared")
        assert {p.name for p in results} == {"a", "b"}

    def test_match_is_case_insensitive(
        self, library, metadata_service, db, portfolio_service, image_service
    ):
        """portfolio_tags query uses COLLATE NOCASE, so a differently-cased
        query still matches the stored tag."""
        library.add_bytes("trip/a.jpg", b"trip-a")
        library.add_meta("trip", {"tags": ["Beach"]})
        metadata_service.rescan(port_uuid=None, recursive=True)

        svc = SearchService(db, portfolio_service, image_service)
        assert [p.name for p in svc.search_portfolios_by_tag("beach")] == ["trip"]
        assert [p.name for p in svc.search_portfolios_by_tag("BEACH")] == ["trip"]


class TestSearchImagesByTag:
    def test_no_match_returns_empty_list(
        self, library, metadata_service, db, portfolio_service, image_service
    ):
        library.add_bytes("p/a.jpg", b"content-a")
        metadata_service.rescan(port_uuid=None, recursive=True)

        svc = SearchService(db, portfolio_service, image_service)
        assert svc.search_images_by_tag("nope") == []

    def test_single_match_returns_that_image(
        self, library, metadata_service, db, portfolio_service, image_service
    ):
        library.add_bytes("p/a.jpg", b"content-a")
        metadata_service.rescan(port_uuid=None, recursive=True)
        img_uuid = _img_uuid_at(db, "p/a.jpg")
        _tag_image(db, img_uuid, "sunset")

        svc = SearchService(db, portfolio_service, image_service)
        results = svc.search_images_by_tag("sunset")
        assert [str(i.id) for i in results] == [img_uuid]
        assert results[0].name == "a.jpg"

    def test_multiple_images_share_a_tag(
        self, library, metadata_service, db, portfolio_service, image_service
    ):
        library.add_bytes("p/a.jpg", b"content-a")
        library.add_bytes("p/b.jpg", b"content-b")
        metadata_service.rescan(port_uuid=None, recursive=True)
        a = _img_uuid_at(db, "p/a.jpg")
        b = _img_uuid_at(db, "p/b.jpg")
        _tag_image(db, a, "portrait")
        _tag_image(db, b, "portrait")

        svc = SearchService(db, portfolio_service, image_service)
        results = svc.search_images_by_tag("portrait")
        assert {str(i.id) for i in results} == {a, b}

    def test_match_is_case_insensitive(
        self, library, metadata_service, db, portfolio_service, image_service
    ):
        library.add_bytes("p/a.jpg", b"content-a")
        metadata_service.rescan(port_uuid=None, recursive=True)
        img_uuid = _img_uuid_at(db, "p/a.jpg")
        _tag_image(db, img_uuid, "Landscape")

        svc = SearchService(db, portfolio_service, image_service)
        assert [str(i.id) for i in svc.search_images_by_tag("landscape")] == [img_uuid]
        assert [str(i.id) for i in svc.search_images_by_tag("LANDSCAPE")] == [img_uuid]
