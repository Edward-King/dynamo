"""
Endpoint-level tests for PortfolioOut's icon-URL shape (api/routers/portfolios.py):
the two new icon_thumbnail_url / icon_thumbnail_static_url fields.

Both are None exactly when icon_image_id is None, and both are populated
(mirroring the ImgOut thumbnail URL formats) whenever an icon is resolved.

Uses the full app via the TestClient fixture (real create_app factory, temp
fs/DB, startup rescan on the sample library). The flag off/on behavior tests
mirror the structure in tests/test_static_serving.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from photoshare.api.main import create_app
from photoshare.auth.sqlite_credential_store import SqliteCredentialStore
from photoshare.config.schema import Settings


def _make_settings(base_root, cache_root, tmp_path, *, static_serving: bool) -> Settings:
    return Settings(
        enable_static_file_serving=static_serving,
        server={"host": "127.0.0.1", "port": 0, "workers": 1, "cors_allowed_origins": []},
        storage={"base_root": str(base_root)},
        cache={"cache_root": str(cache_root)},
        rescan={"on_startup": True, "watch_filesystem": False, "cycle_detection": True},
        logging={"level": "WARNING",
                 "rescan_log_path": str(tmp_path / "logs" / "rescan_history.jsonl")},
    )


def _issue_key(client) -> dict[str, str]:
    import asyncio

    store = SqliteCredentialStore(client.app.state.db)
    _principal, raw = asyncio.run(
        store.issue("portfolio-test-key",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=365))
    )
    return {"X-API-Key": raw}


def _vacation_child(client, headers) -> dict:
    """The vacation portfolio has direct images, so it resolves an icon."""
    root = client.get("/api/v2/portfolios/root", headers=headers).json()
    children = client.get(
        f"/api/v2/portfolios/{root['id']}/children", headers=headers
    ).json()
    return next(c for c in children if c["name"] == "vacation")


# --------------------------------------------------------------------------
# Shape: icon URLs present when an icon is resolved, None when not
# --------------------------------------------------------------------------
class TestPortfolioIconUrlShape:
    def test_icon_urls_populated_when_icon_resolved(self, client, api_key_header):
        """vacation has direct images -> an icon is resolved, so both new
        fields are populated strings matching the ImgOut thumbnail formats."""
        vacation = _vacation_child(client, api_key_header)
        icon_id = vacation["icon_image_id"]
        assert icon_id is not None

        assert vacation["icon_thumbnail_url"] == (
            f"/api/v2/images/{icon_id}/thumb?width=320&height=240"
        )
        shard = icon_id[:2]
        assert vacation["icon_thumbnail_static_url"] == (
            f"/static-thumbs-320x240/{shard}/{icon_id}_320x240.jpg"
        )

    def test_icon_urls_none_when_no_icon(self, client, api_key_header):
        """The root portfolio has no direct images, so no icon is resolved:
        icon_image_id is None and both new URL fields are None (not populated
        strings) -- portfolios can genuinely have no icon."""
        root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
        assert root["icon_image_id"] is None
        assert root["icon_thumbnail_url"] is None
        assert root["icon_thumbnail_static_url"] is None

    def test_get_portfolio_by_id_endpoint_carries_icon_urls(self, client, api_key_header):
        """GET /portfolios/{port_uuid} (get_portfolio, a distinct route/
        service-method pair from both /root's get_root_portfolio and
        /children's list_children) was never directly exercised for icon
        fields -- only /root and /children were. Confirm the third
        PortfolioOut-returning route builds the same correct icon URLs."""
        vacation = _vacation_child(client, api_key_header)
        direct = client.get(
            f"/api/v2/portfolios/{vacation['id']}", headers=api_key_header
        ).json()
        assert direct["id"] == vacation["id"]
        icon_id = direct["icon_image_id"]
        assert icon_id is not None
        assert direct["icon_thumbnail_url"] == (
            f"/api/v2/images/{icon_id}/thumb?width=320&height=240"
        )
        assert direct["icon_thumbnail_static_url"] == (
            f"/static-thumbs-320x240/{icon_id[:2]}/{icon_id}_320x240.jpg"
        )

    def test_children_list_endpoint_carries_correct_icon_state_per_item(
        self, client, api_key_header
    ):
        """GET /portfolios/{id}/children (a *list* endpoint, list[PortfolioOut])
        must derive icon fields independently per item: vacation has direct
        images (icon resolved, both URL fields populated) while family --
        also a child of root, listed in the same response -- has its own
        direct image but no meta.json icon override, so this also confirms
        the list endpoint doesn't leak one item's icon state onto another."""
        root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
        children = client.get(
            f"/api/v2/portfolios/{root['id']}/children", headers=api_key_header
        ).json()
        by_name = {c["name"]: c for c in children}
        assert set(by_name) == {"vacation", "family"}

        vacation = by_name["vacation"]
        v_icon_id = vacation["icon_image_id"]
        assert v_icon_id is not None
        assert vacation["icon_thumbnail_url"] == (
            f"/api/v2/images/{v_icon_id}/thumb?width=320&height=240"
        )
        assert vacation["icon_thumbnail_static_url"] == (
            f"/static-thumbs-320x240/{v_icon_id[:2]}/{v_icon_id}_320x240.jpg"
        )

        family = by_name["family"]
        f_icon_id = family["icon_image_id"]
        assert f_icon_id is not None
        assert f_icon_id != v_icon_id
        assert family["icon_thumbnail_url"] == (
            f"/api/v2/images/{f_icon_id}/thumb?width=320&height=240"
        )
        assert family["icon_thumbnail_static_url"] == (
            f"/static-thumbs-320x240/{f_icon_id[:2]}/{f_icon_id}_320x240.jpg"
        )


# --------------------------------------------------------------------------
# Static-serving flag off/on for icon_thumbnail_static_url
# (mirrors tests/test_static_serving.py for ImgOut.thumbnail_static_url)
# --------------------------------------------------------------------------
class TestPortfolioIconStaticServing:
    def test_flag_off_field_present_but_static_url_404s(
        self, sample_library, cache_root, tmp_path
    ):
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=False)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            vacation = _vacation_child(client, headers)

            assert isinstance(vacation["icon_thumbnail_static_url"], str)
            assert vacation["icon_thumbnail_static_url"]
            # Mount not registered when the flag is off -> 404.
            assert client.get(vacation["icon_thumbnail_static_url"]).status_code == 404

    def test_flag_on_serves_icon_thumbnail_without_api_key(
        self, sample_library, cache_root, tmp_path
    ):
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            vacation = _vacation_child(client, headers)
            icon_id = vacation["icon_image_id"]

            # Generate the default 320x240 thumbnail for the icon image via the
            # dynamic route so the static file exists on disk.
            gen = client.get(
                f"/api/v2/images/{icon_id}/thumb?width=320&height=240",
                headers=headers,
            )
            assert gen.status_code == 200

            # Fetch it statically with NO API key -- the security tradeoff.
            r = client.get(vacation["icon_thumbnail_static_url"])
            assert r.status_code == 200
            assert r.content == gen.content
