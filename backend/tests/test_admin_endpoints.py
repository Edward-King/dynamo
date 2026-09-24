"""
Endpoint-level tests for the admin write path added in the spec/impl
gap-closure (api/routers/admin.py):

  * POST /api/v2/admin/preheat-thumbnails
  * POST /api/v2/admin/cleanup-thumbnails
  * POST /api/v2/admin/root

Uses the full app via the TestClient fixture (real create_app factory, temp
fs/DB, startup rescan on the sample library), mirroring test_api_images.py /
test_auth.py conventions. Auth is exercised the same way test_auth.py does
for /admin/rescan (missing key -> 401 AUTH_MISSING).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from photoshare.api.main import create_app
from photoshare.auth.sqlite_credential_store import SqliteCredentialStore
from photoshare.config.schema import Settings

PREHEAT_ROUTE = "/api/v2/admin/preheat-thumbnails"
CLEANUP_ROUTE = "/api/v2/admin/cleanup-thumbnails"
ROOT_ROUTE = "/api/v2/admin/root"


def _make_static_settings(base_root, cache_root, tmp_path) -> Settings:
    """Mirror of test_static_serving._make_settings with static serving on."""
    return Settings(
        enable_static_file_serving=True,
        server={"host": "127.0.0.1", "port": 0, "workers": 1, "cors_allowed_origins": []},
        storage={"base_root": str(base_root)},
        cache={"cache_root": str(cache_root)},
        rescan={"on_startup": True, "watch_filesystem": False, "cycle_detection": True},
        logging={"level": "WARNING",
                 "rescan_log_path": str(tmp_path / "logs" / "rescan_history.jsonl")},
    )


def _issue_key(client) -> dict[str, str]:
    store = SqliteCredentialStore(client.app.state.db)
    _principal, raw = asyncio.run(
        store.issue("preheat-test-key",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=365))
    )
    return {"X-API-Key": raw}


def _first_image(client, headers) -> dict:
    root = client.get("/api/v2/portfolios/root", headers=headers).json()
    children = client.get(
        f"/api/v2/portfolios/{root['id']}/children", headers=headers
    ).json()
    vacation = next(c for c in children if c["name"] == "vacation")
    images = client.get(
        f"/api/v2/portfolios/{vacation['id']}/images", headers=headers
    ).json()
    return images["items"][0]


# --------------------------------------------------------------------------
# POST /admin/preheat-thumbnails
# --------------------------------------------------------------------------
class TestPreheatThumbnails:
    def test_happy_path_generates_thumbnails_for_whole_tree(
        self, client, api_key_header, cache_root
    ):
        """Default body (root, recursive) pre-generates a thumbnail for every
        image in the sample library (3: beach, mountain, portrait). First run
        creates all of them; images_processed == thumbnails_created."""
        r = client.post(
            PREHEAT_ROUTE, json={"width": 64, "height": 64}, headers=api_key_header
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {
            "images_processed", "thumbnails_created", "duration_ms"
        }
        assert body["images_processed"] == 3
        assert body["thumbnails_created"] == 3
        assert body["duration_ms"] >= 0

        thumbs = list((cache_root / "thumbnails").glob("*/*_64x64.jpg"))
        assert len(thumbs) == 3

    def test_second_run_is_cache_hit(self, client, api_key_header):
        """A repeated preheat at the same size regenerates nothing:
        images_processed stays 3, thumbnails_created drops to 0."""
        first = client.post(
            PREHEAT_ROUTE, json={"width": 64, "height": 64}, headers=api_key_header
        )
        assert first.status_code == 200
        second = client.post(
            PREHEAT_ROUTE, json={"width": 64, "height": 64}, headers=api_key_header
        )
        assert second.status_code == 200
        body = second.json()
        assert body["images_processed"] == 3
        assert body["thumbnails_created"] == 0

    def test_default_width_height_when_omitted(self, client, api_key_header, cache_root):
        """width/height default to 320x240 (matching the dynamic /thumb route
        and the static thumbnail URL builder) when omitted -- NOT a 320x320
        square. Preheated files must land at *_320x240.jpg so the app's own
        thumbnail URLs resolve."""
        r = client.post(PREHEAT_ROUTE, json={}, headers=api_key_header)
        assert r.status_code == 200
        assert r.json()["images_processed"] == 3
        assert list((cache_root / "thumbnails").glob("*/*_320x240.jpg"))
        assert not list((cache_root / "thumbnails").glob("*/*_320x320.jpg"))

    def test_unknown_portfolio_returns_404(self, client, api_key_header):
        r = client.post(
            PREHEAT_ROUTE,
            json={"port_uuid": str(uuid4()), "width": 64, "height": 64},
            headers=api_key_header,
        )
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "PORTFOLIO_NOT_FOUND"
        assert set(body.keys()) == {"code", "message", "detail"}

    def test_scoped_to_subtree_when_port_uuid_given(self, client, api_key_header):
        """Passing a leaf portfolio's id limits processing to that portfolio
        (family has exactly 1 image)."""
        root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
        children = client.get(
            f"/api/v2/portfolios/{root['id']}/children", headers=api_key_header
        ).json()
        family = next(c for c in children if c["name"] == "family")
        r = client.post(
            PREHEAT_ROUTE,
            json={"port_uuid": family["id"], "width": 64, "height": 64, "recursive": False},
            headers=api_key_header,
        )
        assert r.status_code == 200
        assert r.json()["images_processed"] == 1

    def test_out_of_range_width_or_height_rejected(self, client, api_key_header):
        """Each dimension is bounded independently to [16, 4096]; a bad width
        OR a bad height (low or high) is rejected with 422 VALIDATION_ERROR."""
        bad_bodies = [
            {"width": 15, "height": 240},
            {"width": 4097, "height": 240},
            {"width": 320, "height": 15},
            {"width": 320, "height": 4097},
        ]
        for body in bad_bodies:
            r = client.post(PREHEAT_ROUTE, json=body, headers=api_key_header)
            assert r.status_code == 422
            assert r.json()["code"] == "VALIDATION_ERROR"

    def test_requires_auth(self, client):
        r = client.post(PREHEAT_ROUTE, json={"width": 64, "height": 64})
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_MISSING"

    def test_default_preheat_output_matches_static_thumbnail_url(
        self, sample_library, cache_root, tmp_path
    ):
        """Regression for the square-thumbnail bug: a default preheat (no body)
        must generate the exact file that an image's thumbnail_static_url points
        at, so the unauthenticated static mount serves it with NO API key. Before
        the fix, preheat produced 320x320 while the URL points at 320x240 -> 404."""
        settings = _make_static_settings(sample_library.base_root, cache_root, tmp_path)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            r = client.post(PREHEAT_ROUTE, json={}, headers=headers)
            assert r.status_code == 200

            # No auth header: proves the preheated file matches the URL.
            served = client.get(img["thumbnail_static_url"])
            assert served.status_code == 200

    def test_preheat_nondefault_dimensions_match_dynamic_thumb_convention(
        self, client, api_key_header, cache_root
    ):
        """Non-default (width, height) preheat lands at {uuid}_{w}x{h}.jpg,
        matching the dynamic /thumb route's naming (here 800x600, a configured
        thumbnail size)."""
        r = client.post(
            PREHEAT_ROUTE, json={"width": 800, "height": 600}, headers=api_key_header
        )
        assert r.status_code == 200
        assert r.json()["images_processed"] == 3
        assert len(list((cache_root / "thumbnails").glob("*/*_800x600.jpg"))) == 3


# --------------------------------------------------------------------------
# POST /admin/cleanup-thumbnails
# --------------------------------------------------------------------------
class TestCleanupThumbnails:
    def test_no_stale_thumbnails_removes_nothing(self, client, api_key_header):
        """A freshly preheated cache has only valid thumbnails, so cleanup
        removes zero."""
        client.post(PREHEAT_ROUTE, json={"width": 64, "height": 64}, headers=api_key_header)
        r = client.post(CLEANUP_ROUTE, headers=api_key_header)
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"thumbnails_removed", "duration_ms"}
        assert body["thumbnails_removed"] == 0

    def test_removes_stale_keeps_valid(self, client, api_key_header, cache_root):
        """A thumbnail whose Img_UUID is not in the registry is stale and
        removed; valid thumbnails for live images are kept."""
        client.post(PREHEAT_ROUTE, json={"width": 64, "height": 64}, headers=api_key_header)
        valid_before = list((cache_root / "thumbnails").glob("*/*_64x64.jpg"))
        assert len(valid_before) == 3

        stale_uuid = uuid4()
        shard = (cache_root / "thumbnails" / str(stale_uuid)[:2])
        shard.mkdir(parents=True, exist_ok=True)
        stale_file = shard / f"{stale_uuid}_64x64.jpg"
        Image.new("RGB", (64, 64), (0, 0, 0)).save(stale_file, "JPEG")
        assert stale_file.is_file()

        r = client.post(CLEANUP_ROUTE, headers=api_key_header)
        assert r.status_code == 200
        assert r.json()["thumbnails_removed"] == 1
        assert not stale_file.exists()
        # The 3 valid thumbnails survive.
        assert len(list((cache_root / "thumbnails").glob("*/*_64x64.jpg"))) == 3

    def test_requires_auth(self, client):
        r = client.post(CLEANUP_ROUTE)
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_MISSING"


# --------------------------------------------------------------------------
# POST /admin/root
# --------------------------------------------------------------------------
class TestSetRoot:
    def _new_library(self, tmp_path: Path) -> Path:
        new_root = tmp_path / "photos2"
        (new_root / "trip").mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (10, 20, 30)).save(
            new_root / "trip" / "one.jpg", "JPEG"
        )
        return new_root

    def test_happy_path_sets_root_and_reconciles(
        self, client, api_key_header, tmp_path
    ):
        """A valid directory becomes the new base_root, a reconciling rescan
        is triggered, and the new tree is served afterward. root_id (identity)
        is unchanged -- a safe reconciling op (§3.6)."""
        health_before = client.get("/api/v2/admin/health").json()
        new_root = self._new_library(tmp_path)

        r = client.post(
            ROOT_ROUTE, json={"path": str(new_root)}, headers=api_key_header
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"base_root", "rescan_triggered", "duration_ms"}
        assert body["base_root"] == str(new_root.resolve())
        assert body["rescan_triggered"] is True
        assert body["duration_ms"] >= 0

        # Identity is preserved across the root change.
        health_after = client.get("/api/v2/admin/health").json()
        assert health_after["root_id"] == health_before["root_id"]

        # The new tree is now served: root has a single 'trip' child.
        root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
        children = client.get(
            f"/api/v2/portfolios/{root['id']}/children", headers=api_key_header
        ).json()
        assert {c["name"] for c in children} == {"trip"}

    def test_invalid_path_returns_400_no_change(
        self, client, api_key_header, tmp_path
    ):
        """A nonexistent path is rejected with 400 INVALID_ROOT_PATH before
        any mutation, so the existing tree is untouched."""
        bogus = tmp_path / "does" / "not" / "exist"
        r = client.post(
            ROOT_ROUTE, json={"path": str(bogus)}, headers=api_key_header
        )
        assert r.status_code == 400
        body = r.json()
        assert body["code"] == "INVALID_ROOT_PATH"
        assert set(body.keys()) == {"code", "message", "detail"}

        # Original sample library is still served (vacation + family).
        root = client.get("/api/v2/portfolios/root", headers=api_key_header).json()
        children = client.get(
            f"/api/v2/portfolios/{root['id']}/children", headers=api_key_header
        ).json()
        assert {c["name"] for c in children} == {"vacation", "family"}

    def test_path_pointing_at_a_file_is_rejected(
        self, client, api_key_header, tmp_path
    ):
        """A path that exists but is a file (not a directory) is invalid."""
        a_file = tmp_path / "not_a_dir.txt"
        a_file.write_text("x", encoding="utf-8")
        r = client.post(
            ROOT_ROUTE, json={"path": str(a_file)}, headers=api_key_header
        )
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_ROOT_PATH"

    def test_requires_auth(self, client, tmp_path):
        r = client.post(ROOT_ROUTE, json={"path": str(tmp_path)})
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_MISSING"
