"""
Regression tests for the PhotoShare Gallery Proxy (main.py).

Covers every route currently exposed to the frontend:

  GET /gallery/portfolios/root
  GET /gallery/portfolios/{port_uuid}
  GET /gallery/portfolios/{port_uuid}/children
  GET /gallery/portfolios/{port_uuid}/images
  GET /gallery/image/{img_uuid}/thumb
  GET /gallery/image/{img_uuid}/full
  GET /gallery/image/{img_uuid}/full?download=1
  GET /gallery/share?p={port_uuid}[&i={img_uuid}]
  GET /gallery/health

All upstream (PhotoShare) calls are intercepted with respx so the suite
never needs a live PhotoShare server, and runs fast/offline in CI.

Special attention is paid to the two URL-rewrite helpers
(_rewrite_image_urls / _rewrite_portfolio_urls) since they are the site of
a real bug that was fixed in this project (icon thumbnail URLs pointing at
PhotoShare directly instead of back at the proxy) -- these tests exist to
make sure that regression can never silently come back.
"""

from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Response


@respx.mock
def test_root_portfolio_rewrites_icon_url(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/root").mock(
        return_value=Response(
            200,
            json={
                "id": "root-uuid",
                "name": "Library",
                "icon_image_id": "img-1",
                "icon_thumbnail_url": "/api/v2/images/img-1/thumb?width=320&height=240",
                "children": [],
            },
        )
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/root")

    assert resp.status_code == 200
    data = resp.json()
    # The bug this guards against: icon_thumbnail_url must point back at
    # *this* proxy's own streaming route, never at PhotoShare directly.
    assert data["icon_thumbnail_url"] == "/gallery/image/img-1/thumb"
    assert "photoshare.test" not in data["icon_thumbnail_url"]


@respx.mock
def test_root_portfolio_without_icon_leaves_icon_url_none(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/root").mock(
        return_value=Response(
            200,
            json={
                "id": "root-uuid",
                "name": "Library",
                "icon_image_id": None,
                "icon_thumbnail_url": None,
                "children": [],
            },
        )
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/root")

    assert resp.status_code == 200
    assert resp.json()["icon_thumbnail_url"] is None


@respx.mock
def test_get_portfolio_by_id(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/abc-123").mock(
        return_value=Response(
            200,
            json={"id": "abc-123", "name": "Trip", "icon_image_id": None, "icon_thumbnail_url": None},
        )
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/abc-123")

    assert resp.status_code == 200
    assert resp.json()["id"] == "abc-123"


@respx.mock
def test_get_portfolio_not_found_passes_through_status(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/missing").mock(
        return_value=Response(404, json={"code": "NOT_FOUND", "message": "no such portfolio"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/missing")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


@respx.mock
def test_get_children_rewrites_each_childs_icon_url(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/root/children").mock(
        return_value=Response(
            200,
            json=[
                {"id": "c1", "name": "2024", "icon_image_id": "img-9", "icon_thumbnail_url": "/api/v2/images/img-9/thumb"},
                {"id": "c2", "name": "2025", "icon_image_id": None, "icon_thumbnail_url": None},
            ],
        )
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/root/children")

    assert resp.status_code == 200
    children = resp.json()
    assert len(children) == 2
    assert children[0]["icon_thumbnail_url"] == "/gallery/image/img-9/thumb"
    assert children[1]["icon_thumbnail_url"] is None


@respx.mock
def test_get_images_rewrites_thumbnail_and_full_urls(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/root/images").mock(
        return_value=Response(
            200,
            json={
                "items": [
                    {
                        "id": "img-1",
                        "name": "sunset.jpg",
                        "thumbnail_url": "/api/v2/images/img-1/thumb?width=320&height=240",
                        "full_url": "/api/v2/images/img-1/full",
                    }
                ],
                "page": 1,
                "page_size": 100,
                "total": 1,
                "has_next": False,
            },
        )
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/root/images")

    assert resp.status_code == 200
    data = resp.json()
    item = data["items"][0]
    assert item["thumbnail_url"] == "/gallery/image/img-1/thumb"
    assert item["full_url"] == "/gallery/image/img-1/full"
    assert data["total"] == 1


@respx.mock
def test_get_images_passes_pagination_params_upstream(app, upstream_base):
    route = respx.get(f"{upstream_base}/api/v2/portfolios/root/images").mock(
        return_value=Response(
            200, json={"items": [], "page": 2, "page_size": 50, "total": 0, "has_next": False}
        )
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/root/images", params={"page": 2, "page_size": 50})

    assert resp.status_code == 200
    sent_request = route.calls.last.request
    assert sent_request.url.params["page"] == "2"
    assert sent_request.url.params["page_size"] == "50"


def test_get_images_rejects_invalid_page_size(app):
    # page_size has le=200 in the route signature -- FastAPI should 422
    # before ever reaching upstream.
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/root/images", params={"page_size": 999})
    assert resp.status_code == 422


@respx.mock
def test_stream_thumbnail_returns_bytes_and_content_type(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/img-1/thumb").mock(
        return_value=Response(
            200,
            content=b"FAKEJPEGBYTES",
            headers={"content-type": "image/jpeg", "cache-control": "max-age=3600"},
        )
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/thumb")

    assert resp.status_code == 200
    assert resp.content == b"FAKEJPEGBYTES"
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers.get("cache-control") == "max-age=3600"


@respx.mock
def test_stream_thumbnail_forwards_width_height_params(app, upstream_base):
    route = respx.get(f"{upstream_base}/api/v2/images/img-1/thumb").mock(
        return_value=Response(200, content=b"x", headers={"content-type": "image/jpeg"})
    )
    client = TestClient(app)
    client.get("/gallery/image/img-1/thumb", params={"width": 640, "height": 480})

    sent_request = route.calls.last.request
    assert sent_request.url.params["width"] == "640"
    assert sent_request.url.params["height"] == "480"


@respx.mock
def test_stream_thumbnail_upstream_error_propagates_status(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/missing/thumb").mock(
        return_value=Response(404, text="not found")
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/missing/thumb")

    assert resp.status_code == 404


@respx.mock
def test_stream_full_image_returns_bytes(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"FAKEFULLBYTES", headers={"content-type": "image/jpeg"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full")

    assert resp.status_code == 200
    assert resp.content == b"FAKEFULLBYTES"


# ---------------------------------------------------------------------------
# Download (?download=1) on the full-image route
# ---------------------------------------------------------------------------


@respx.mock
def test_full_image_without_download_is_unchanged(app, upstream_base):
    # Guards the "optional, additive" promise: no attachment header and no
    # extra metadata call unless the caller asks for a download.
    full = respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"FULL", headers={"content-type": "image/jpeg"})
    )
    meta = respx.get(f"{upstream_base}/api/v2/images/img-1").mock(
        return_value=Response(200, json={"id": "img-1", "name": "sunset.jpg"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full")

    assert resp.status_code == 200
    assert "content-disposition" not in resp.headers
    assert full.called
    assert not meta.called


@respx.mock
def test_download_sets_attachment_with_real_filename(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"FULL", headers={"content-type": "image/jpeg"})
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(
        return_value=Response(200, json={"id": "img-1", "name": "sunset.jpg"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    assert resp.status_code == 200
    assert resp.content == b"FULL"
    assert resp.headers["content-type"] == "image/jpeg"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert 'filename="sunset.jpg"' in disposition
    assert "filename*=UTF-8''sunset.jpg" in disposition


@respx.mock
def test_download_keeps_cache_headers(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(
            200,
            content=b"FULL",
            headers={
                "content-type": "image/jpeg",
                "cache-control": "public, max-age=31536000, immutable",
                "etag": '"abc123"',
            },
        )
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(
        return_value=Response(200, json={"id": "img-1", "name": "sunset.jpg"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full", params={"download": "true"})

    assert resp.headers.get("cache-control") == "public, max-age=31536000, immutable"
    assert resp.headers.get("etag") == '"abc123"'


@respx.mock
def test_download_non_ascii_filename_is_encoded(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"FULL", headers={"content-type": "image/jpeg"})
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(
        return_value=Response(200, json={"id": "img-1", "name": "Café façade.jpg"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    disposition = resp.headers["content-disposition"]
    # ASCII fallback for old clients, exact UTF-8 name for current browsers.
    assert 'filename="Caf_ fa_ade.jpg"' in disposition
    assert "filename*=UTF-8''Caf%C3%A9%20fa%C3%A7ade.jpg" in disposition


@respx.mock
def test_download_sanitizes_unsafe_filename_characters(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"FULL", headers={"content-type": "image/jpeg"})
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(
        return_value=Response(200, json={"id": "img-1", "name": '../trip/beach: "day 1"?.jpg'})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    disposition = resp.headers["content-disposition"]
    # Path parts dropped; characters invalid on Windows replaced; no raw
    # quote can break out of the header's quoted filename.
    assert 'filename="beach_ _day 1__.jpg"' in disposition
    assert ".." not in disposition
    assert "/" not in disposition.split("filename*=")[0]


@respx.mock
def test_download_falls_back_to_uuid_name_when_metadata_unavailable(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"PNG", headers={"content-type": "image/png"})
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(
        return_value=Response(404, json={"code": "IMAGE_NOT_FOUND"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    # A metadata failure must not block the download itself.
    assert resp.status_code == 200
    assert resp.content == b"PNG"
    assert 'filename="img-1.png"' in resp.headers["content-disposition"]


@respx.mock
def test_download_falls_back_when_metadata_lookup_times_out(app, upstream_base):
    # Regression guard (PR #1 review, bug 1): a timeout on the filename
    # lookup used to escape as a 500 even though the image bytes were fine.
    import httpx

    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"FULL", headers={"content-type": "image/jpeg"})
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(side_effect=httpx.ReadTimeout("slow"))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    assert resp.status_code == 200
    assert resp.content == b"FULL"
    assert 'filename="img-1.jpg"' in resp.headers["content-disposition"]


@respx.mock
def test_download_falls_back_when_metadata_connection_drops(app, upstream_base):
    # Same guard for a transport error other than a timeout or refused
    # connection (e.g. PhotoShare closing the connection mid-response).
    import httpx

    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(200, content=b"FULL", headers={"content-type": "image/jpeg"})
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(side_effect=httpx.ReadError("reset"))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    assert resp.status_code == 200
    assert 'filename="img-1.jpg"' in resp.headers["content-disposition"]


@respx.mock
def test_download_fetches_image_and_filename_concurrently(app, upstream_base):
    # PR #1 review, item 3: the image bytes and the filename lookup used to
    # be fetched one after the other. Each mocked upstream route below waits
    # until the *other* one has started, so the test only passes if both
    # requests are in flight at the same time (sequential code would hit
    # the timeout and fail).
    import asyncio

    full_started = asyncio.Event()
    meta_started = asyncio.Event()

    async def full_side_effect(request):
        full_started.set()
        await asyncio.wait_for(meta_started.wait(), timeout=2)
        return Response(200, content=b"FULL", headers={"content-type": "image/jpeg"})

    async def meta_side_effect(request):
        meta_started.set()
        await asyncio.wait_for(full_started.wait(), timeout=2)
        return Response(200, json={"id": "img-1", "name": "sunset.jpg"})

    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(side_effect=full_side_effect)
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(side_effect=meta_side_effect)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    assert resp.status_code == 200
    assert 'filename="sunset.jpg"' in resp.headers["content-disposition"]


@respx.mock
def test_download_image_error_still_propagates_with_concurrent_lookup(app, upstream_base):
    # With the lookup running alongside, an image failure must still come
    # back as the image's own error status -- not be masked by the lookup.
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(503, text="unavailable")
    )
    respx.get(f"{upstream_base}/api/v2/images/img-1").mock(
        return_value=Response(200, json={"id": "img-1", "name": "sunset.jpg"})
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full", params={"download": 1})

    assert resp.status_code == 503
    assert "content-disposition" not in resp.headers


@respx.mock
def test_download_upstream_image_error_propagates_status(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/images/missing/full").mock(
        return_value=Response(404, text="not found")
    )
    client = TestClient(app)
    resp = client.get("/gallery/image/missing/full", params={"download": 1})

    assert resp.status_code == 404
    assert "content-disposition" not in resp.headers


@respx.mock
def test_full_image_varies_on_origin(app, upstream_base):
    # Regression guard: without Vary: Origin, a browser that first loads
    # the image without CORS (page background) caches a copy with no
    # Access-Control-Allow-Origin header, then reuses it for the lightbox's
    # CORS request in share mode and blocks it -- the photo fails to load
    # and Save fails on phones.
    respx.get(f"{upstream_base}/api/v2/images/img-1/full").mock(
        return_value=Response(
            200,
            content=b"FULL",
            headers={"content-type": "image/jpeg", "cache-control": "public, max-age=31536000, immutable"},
        )
    )
    client = TestClient(app)

    no_cors = client.get("/gallery/image/img-1/full")
    with_cors = client.get("/gallery/image/img-1/full", headers={"Origin": "http://gallery.test"})

    for resp in (no_cors, with_cors):
        vary = [v.strip().lower() for v in resp.headers.get("vary", "").split(",")]
        assert "origin" in vary
    assert with_cors.headers.get("access-control-allow-origin") in ("*", "http://gallery.test")


def test_download_rejects_invalid_flag_value(app):
    client = TestClient(app)
    resp = client.get("/gallery/image/img-1/full", params={"download": "banana"})

    assert resp.status_code == 422


@respx.mock
def test_upstream_connection_failure_returns_502(app, upstream_base):
    import httpx

    respx.get(f"{upstream_base}/api/v2/portfolios/root").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    client = TestClient(app)
    resp = client.get("/gallery/portfolios/root")

    assert resp.status_code == 502
    assert "Could not reach PhotoShare" in resp.json()["detail"]


def test_health_does_not_call_upstream(app):
    # No respx mock is registered for this test at all -- if health() ever
    # started reaching out to PhotoShare, this test would fail with a
    # respx "no route found" style error instead of a clean 200.
    client = TestClient(app)
    resp = client.get("/gallery/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "upstream" in body


def test_cors_headers_present_for_frontend_origin(app):
    client = TestClient(app)
    resp = client.options(
        "/gallery/health",
        headers={
            "Origin": "http://127.0.0.1:5500",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") in ("*", "http://127.0.0.1:5500")


# ---------------------------------------------------------------------------
# /gallery/share -- link-preview page for shared albums and photos
# ---------------------------------------------------------------------------

import re  # noqa: E402

import httpx  # noqa: E402

ALBUM = "0b4f9c3e-1d2a-5e6f-8a9b-0c1d2e3f4a5b"
PHOTO = "7c8d9e0f-1a2b-5c3d-9e4f-5a6b7c8d9e0f"
COVER = "1a2b3c4d-5e6f-5a7b-8c9d-0e1f2a3b4c5d"


def _meta(body: str, prop: str) -> str | None:
    """Content of <meta property=prop> or <meta name=prop>, unescaped."""
    import html as _html

    m = re.search(r'<meta (?:property|name)="' + re.escape(prop) + r'" content="([^"]*)">', body)
    return _html.unescape(m.group(1)) if m else None


def _mock_album(upstream_base, **fields):
    data = {
        "id": ALBUM,
        "name": "Kyoto",
        "icon_image_id": COVER,
        "total_image_count": 24,
    }
    data.update(fields)
    return respx.get(f"{upstream_base}/api/v2/portfolios/{ALBUM}").mock(
        return_value=Response(200, json=data)
    )


def _mock_photo(upstream_base, **fields):
    data = {"id": PHOTO, "name": "temple.jpg", "width": 4000, "height": 3000}
    data.update(fields)
    return respx.get(f"{upstream_base}/api/v2/images/{PHOTO}").mock(
        return_value=Response(200, json=data)
    )


def _share(app, headers=None, **params):
    client = TestClient(app, raise_server_exceptions=False)
    base = {"host": "gallery.example.com", "x-forwarded-proto": "https"}
    base.update(headers or {})
    return client.get("/gallery/share", params=params, headers=base)


@respx.mock
def test_share_album_has_preview_tags(app, upstream_base):
    _mock_album(upstream_base)
    resp = _share(app, p=ALBUM)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert _meta(body, "og:title") == "Kyoto"
    assert _meta(body, "og:description") == "Album \u00b7 24 photos"
    assert _meta(body, "og:type") == "website"
    assert _meta(body, "og:site_name") == "My Dynamic Gallery"
    assert _meta(body, "og:url") == f"https://gallery.example.com/gallery/share?p={ALBUM}"
    # The album's cover, as an absolute URL crawlers can fetch.
    assert _meta(body, "og:image") == (
        f"https://gallery.example.com/gallery/image/{COVER}/thumb?width=800&height=600"
    )
    assert _meta(body, "twitter:card") == "summary_large_image"
    # The cover's size isn't looked up, so no size tags are claimed.
    assert _meta(body, "og:image:width") is None


@respx.mock
def test_share_photo_uses_photo_and_album_name(app, upstream_base):
    _mock_album(upstream_base)
    _mock_photo(upstream_base)
    resp = _share(app, p=ALBUM, i=PHOTO)

    assert resp.status_code == 200
    body = resp.text
    assert _meta(body, "og:title") == "temple.jpg"
    assert _meta(body, "og:description") == "Photo from Kyoto"
    assert _meta(body, "og:url") == f"https://gallery.example.com/gallery/share?p={ALBUM}&i={PHOTO}"
    assert _meta(body, "og:image") == (
        f"https://gallery.example.com/gallery/image/{PHOTO}/thumb?width=800&height=600"
    )
    # 4000x3000 shrunk to fit 800x600.
    assert _meta(body, "og:image:width") == "800"
    assert _meta(body, "og:image:height") == "600"


@respx.mock
def test_share_photo_preview_size_keeps_shape_and_never_enlarges(app, upstream_base):
    _mock_album(upstream_base)
    _mock_photo(upstream_base, width=3000, height=4000)  # portrait
    body = _share(app, p=ALBUM, i=PHOTO).text
    assert (_meta(body, "og:image:width"), _meta(body, "og:image:height")) == ("450", "600")

    respx.reset()
    _mock_album(upstream_base)
    _mock_photo(upstream_base, width=640, height=480)  # already small
    body = _share(app, p=ALBUM, i=PHOTO).text
    assert (_meta(body, "og:image:width"), _meta(body, "og:image:height")) == ("640", "480")


@respx.mock
def test_share_photo_lookups_run_concurrently(app, upstream_base):
    import asyncio

    album_started = asyncio.Event()
    photo_started = asyncio.Event()

    async def album_side_effect(request):
        album_started.set()
        await asyncio.wait_for(photo_started.wait(), timeout=2)
        return Response(200, json={"id": ALBUM, "name": "Kyoto"})

    async def photo_side_effect(request):
        photo_started.set()
        await asyncio.wait_for(album_started.wait(), timeout=2)
        return Response(200, json={"id": PHOTO, "name": "temple.jpg"})

    respx.get(f"{upstream_base}/api/v2/portfolios/{ALBUM}").mock(side_effect=album_side_effect)
    respx.get(f"{upstream_base}/api/v2/images/{PHOTO}").mock(side_effect=photo_side_effect)
    resp = _share(app, p=ALBUM, i=PHOTO)

    assert resp.status_code == 200
    assert _meta(resp.text, "og:title") == "temple.jpg"


@respx.mock
def test_share_album_without_cover_omits_image(app, upstream_base):
    _mock_album(upstream_base, icon_image_id=None, total_image_count=0)
    body = _share(app, p=ALBUM).text

    assert _meta(body, "og:image") is None
    assert _meta(body, "twitter:card") == "summary"
    assert _meta(body, "og:description") == "Album"


@respx.mock
def test_share_single_photo_album_says_photo(app, upstream_base):
    _mock_album(upstream_base, total_image_count=1)
    assert _meta(_share(app, p=ALBUM).text, "og:description") == "Album \u00b7 1 photo"


@respx.mock
def test_share_sends_visitors_to_gallery(app, upstream_base):
    _mock_album(upstream_base)
    _mock_photo(upstream_base)
    body = _share(app, p=ALBUM, i=PHOTO).text
    target = f"../?p={ALBUM}&i={PHOTO}"

    # A 200 page with a script hop, not an HTTP redirect, so crawlers keep
    # the tags. The visible link covers browsers that block scripts.
    assert f'<script>location.replace("{target}");</script>' in body
    assert f'href="{target.replace("&", "&amp;")}"' in body
    assert "http-equiv" not in body


@respx.mock
def test_share_album_link_targets_album_only(app, upstream_base):
    _mock_album(upstream_base)
    body = _share(app, p=ALBUM).text
    assert f'location.replace("../?p={ALBUM}")' in body


@respx.mock
def test_share_escapes_names(app, upstream_base):
    _mock_album(upstream_base, name='Tom & "Jerry" <script>alert(1)</script>')
    _mock_photo(upstream_base, name="<img src=x onerror=alert(1)>.jpg")
    body = _share(app, p=ALBUM, i=PHOTO).text

    assert "<script>alert(1)" not in body
    assert "<img src=x" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;.jpg" in body
    assert _meta(body, "og:description") == 'Photo from Tom & "Jerry" <script>alert(1)</script>'
    # Only the gallery's own script hop is on the page.
    assert body.count("<script>") == 1


def test_share_rejects_malformed_ids_without_calling_upstream(app):
    # No respx routes: any upstream call would fail the test.
    with respx.mock(assert_all_called=False) as mock:
        for params in (
            {},
            {"p": "not-a-uuid"},
            {"p": "../../admin"},
            {"p": ALBUM, "i": "x"},
            {"p": ALBUM, "i": ""},
        ):
            resp = _share(app, **params)
            assert resp.status_code == 404, params
            assert "This link isn&#x27;t valid." in resp.text
            assert "location.replace" not in resp.text
            assert 'href="../"' in resp.text
        assert not mock.calls


@respx.mock
def test_share_unknown_album_is_friendly_404(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/{ALBUM}").mock(
        return_value=Response(404, json={"error": "not found"})
    )
    resp = _share(app, p=ALBUM)

    assert resp.status_code == 404
    assert "This album isn&#x27;t available any more." in resp.text
    assert "location.replace" not in resp.text
    assert resp.headers["cache-control"] == "public, max-age=300"


@respx.mock
def test_share_unknown_photo_links_to_album(app, upstream_base):
    _mock_album(upstream_base)
    respx.get(f"{upstream_base}/api/v2/images/{PHOTO}").mock(
        return_value=Response(404, json={"error": "not found"})
    )
    resp = _share(app, p=ALBUM, i=PHOTO)

    assert resp.status_code == 404
    assert "This photo isn&#x27;t available any more." in resp.text
    assert f'href="../?p={ALBUM}"' in resp.text


@respx.mock
def test_share_uses_http_when_visitor_used_http(app, upstream_base):
    _mock_album(upstream_base)
    body = _share(app, headers={"host": "gallery.local", "x-forwarded-proto": "http"}, p=ALBUM).text
    assert _meta(body, "og:image").startswith("http://gallery.local/gallery/image/")
    assert _meta(body, "og:url").startswith("http://gallery.local/gallery/share")


@respx.mock
def test_share_keeps_host_port_and_ignores_unknown_proto(app, upstream_base):
    _mock_album(upstream_base)
    body = _share(
        app, headers={"host": "192.168.1.20:8080", "x-forwarded-proto": "javascript"}, p=ALBUM
    ).text
    # Falls back to the scheme the proxy itself was reached on.
    assert _meta(body, "og:url") == f"http://192.168.1.20:8080/gallery/share?p={ALBUM}"


@respx.mock
def test_share_bad_host_header_drops_absolute_urls(app, upstream_base):
    _mock_album(upstream_base)
    resp = _share(app, headers={"host": 'evil.example"><script>x</script>'}, p=ALBUM)

    assert resp.status_code == 200
    assert _meta(resp.text, "og:url") is None
    assert _meta(resp.text, "og:image") is None
    assert "evil.example" not in resp.text
    # Still sends visitors on.
    assert f'location.replace("../?p={ALBUM}")' in resp.text


@respx.mock
def test_share_slow_backend_still_sends_visitors_on(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/{ALBUM}").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    resp = _share(app, p=ALBUM)

    assert resp.status_code == 200
    assert _meta(resp.text, "og:title") == "My Dynamic Gallery"
    assert f'location.replace("../?p={ALBUM}")' in resp.text
    # Not cached, so the next preview attempt can get the real one.
    assert resp.headers["cache-control"] == "no-store"


@respx.mock
def test_share_backend_down_or_erroring_still_sends_visitors_on(app, upstream_base):
    respx.get(f"{upstream_base}/api/v2/portfolios/{ALBUM}").mock(
        side_effect=httpx.ConnectError("refused")
    )
    respx.get(f"{upstream_base}/api/v2/images/{PHOTO}").mock(return_value=Response(500))
    resp = _share(app, p=ALBUM, i=PHOTO)

    assert resp.status_code == 200
    assert f'location.replace("../?p={ALBUM}&i={PHOTO}")' in resp.text
    assert resp.headers["cache-control"] == "no-store"


@respx.mock
def test_share_success_is_cacheable_for_five_minutes(app, upstream_base):
    _mock_album(upstream_base)
    resp = _share(app, p=ALBUM)
    assert resp.headers["cache-control"] == "public, max-age=300"


@respx.mock
def test_share_accepts_uppercase_ids(app, upstream_base):
    _mock_album(upstream_base)
    resp = _share(app, p=ALBUM.upper())
    assert resp.status_code == 200
    assert f"p={ALBUM}" in _meta(resp.text, "og:url")


def test_site_name_env_override(monkeypatch):
    import main

    monkeypatch.setenv("GALLERY_PROXY_SITE_NAME", "Edward's Photos")
    cfg = main._apply_env_overrides({"site_name": "My Dynamic Gallery"})
    assert cfg["site_name"] == "Edward's Photos"
