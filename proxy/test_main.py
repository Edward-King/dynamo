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
