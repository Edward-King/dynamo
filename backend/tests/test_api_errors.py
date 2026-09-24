"""
Endpoint-level error-taxonomy tests (api/errors.py, §2.7/§5.3).

Verifies the uniform ErrorResponse envelope and the documented status/code
mapping for not-found resources, and that a malformed meta.json surfaces via
the rescan endpoint's `errors` array rather than crashing the request.
"""
from __future__ import annotations

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestNotFoundTaxonomy:
    def test_nonexistent_portfolio_returns_404_portfolio_not_found(
        self, client, api_key_header
    ):
        r = client.get(f"/api/v2/portfolios/{NONEXISTENT_UUID}", headers=api_key_header)
        assert r.status_code == 404
        assert r.json()["code"] == "PORTFOLIO_NOT_FOUND"

    def test_nonexistent_portfolio_children_returns_404(self, client, api_key_header):
        r = client.get(
            f"/api/v2/portfolios/{NONEXISTENT_UUID}/children", headers=api_key_header
        )
        assert r.status_code == 404
        assert r.json()["code"] == "PORTFOLIO_NOT_FOUND"

    def test_nonexistent_image_returns_404_image_not_found(self, client, api_key_header):
        r = client.get(f"/api/v2/images/{NONEXISTENT_UUID}/full", headers=api_key_header)
        assert r.status_code == 404
        assert r.json()["code"] == "IMAGE_NOT_FOUND"

    def test_error_envelope_shape(self, client, api_key_header):
        """Every non-2xx body is {code, message, detail} (api/models
        .ErrorResponse); detail is null for a plain not-found."""
        body = client.get(
            f"/api/v2/portfolios/{NONEXISTENT_UUID}", headers=api_key_header
        ).json()
        assert set(body.keys()) == {"code", "message", "detail"}
        assert body["detail"] is None

    def test_malformed_uuid_is_validation_error(self, client, api_key_header):
        """A path param that isn't a UUID is a 422 VALIDATION_ERROR, distinct
        from a well-formed-but-absent UUID's 404."""
        r = client.get("/api/v2/portfolios/not-a-uuid", headers=api_key_header)
        assert r.status_code == 422
        assert r.json()["code"] == "VALIDATION_ERROR"


class TestRescanSurfacesMetaErrors:
    def test_malformed_meta_json_reported_in_rescan_errors_not_crash(
        self, client, api_key_header
    ):
        """Injecting a malformed meta.json into the live library and
        triggering /admin/rescan returns 200 with the parse error captured
        in the `errors` array -- never a 500 or a silent drop (§2.3)."""
        base_root = client.app.state.storage.base_root
        bad_dir = base_root / "vacation"
        (bad_dir / "meta.json").write_text('{"tags": "a,b,c"}', encoding="utf-8")

        r = client.post(
            "/api/v2/admin/rescan", json={"port_uuid": None, "recursive": True},
            headers=api_key_header,
        )
        assert r.status_code == 200
        body = r.json()
        assert any("tags" in e for e in body["errors"]), body["errors"]
