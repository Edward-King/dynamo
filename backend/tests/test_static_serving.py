"""
Tests for the opt-in unauthenticated static file mounts
(enable_static_file_serving) and the four *_url fields on ImgOut.

Covers:
  * Flag OFF (default): the URL fields are present as populated strings on
    every ImgOut, but requesting any *_static_url returns 404 because NO
    StaticFiles mount is registered -- including every per-size thumbnail
    mount, not just the default.
  * Flag ON: /static-photos and every per-size /static-thumbs-{w}x{h} mount
    (one per cache.thumbnail_sizes entry) serve the correct bytes with NO
    X-API-Key header at all -- proving the security tradeoff (any file
    fetchable by URL, no auth) is real and intentional.
  * Flag ON: ImgOut.thumbnail_static_urls exposes one URL per configured size,
    each resolving after that size is preheated; the singular
    thumbnail_static_url stays pinned to the first configured size.
  * Flag ON: a static-thumb URL for a size that is NOT configured has no mount
    and 404s.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from photoshare.api.main import create_app
from photoshare.auth.sqlite_credential_store import SqliteCredentialStore
from photoshare.config.schema import Settings


def _make_settings(base_root, cache_root, tmp_path, *, static_serving: bool,
                   thumbnail_sizes: list[tuple[int, int]] | None = None,
                   default_static_thumbnail_size: tuple[int, int] | None = None,
                   default_static_icon_size: tuple[int, int] | None = None) -> Settings:
    cache: dict = {"cache_root": str(cache_root)}
    if thumbnail_sizes is not None:
        cache["thumbnail_sizes"] = thumbnail_sizes
    if default_static_thumbnail_size is not None:
        cache["default_static_thumbnail_size"] = default_static_thumbnail_size
    if default_static_icon_size is not None:
        cache["default_static_icon_size"] = default_static_icon_size
    return Settings(
        enable_static_file_serving=static_serving,
        server={"host": "127.0.0.1", "port": 0, "workers": 1, "cors_allowed_origins": []},
        storage={"base_root": str(base_root)},
        cache=cache,
        rescan={"on_startup": True, "watch_filesystem": False, "cycle_detection": True},
        logging={"level": "WARNING",
                 "rescan_log_path": str(tmp_path / "logs" / "rescan_history.jsonl")},
    )


def _preheat(client, headers, width: int, height: int) -> None:
    """Force-generate thumbnails at (width, height) via the admin endpoint so
    the corresponding static files exist on disk."""
    r = client.post(
        "/api/v2/admin/preheat-thumbnails",
        json={"width": width, "height": height},
        headers=headers,
    )
    assert r.status_code == 200


def _issue_key(client) -> dict[str, str]:
    store = SqliteCredentialStore(client.app.state.db)
    import asyncio

    _principal, raw = asyncio.run(
        store.issue("static-test-key",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=365))
    )
    return {"X-API-Key": raw}


def _vacation_portfolio(client, headers) -> dict:
    """The vacation portfolio has direct images, so it resolves an icon and
    thus a populated icon_thumbnail_static_url."""
    root = client.get("/api/v2/portfolios/root", headers=headers).json()
    children = client.get(
        f"/api/v2/portfolios/{root['id']}/children", headers=headers
    ).json()
    return next(c for c in children if c["name"] == "vacation")


def _first_image(client, headers) -> dict:
    vacation = _vacation_portfolio(client, headers)
    images = client.get(
        f"/api/v2/portfolios/{vacation['id']}/images", headers=headers
    ).json()
    return images["items"][0]


# --------------------------------------------------------------------------
# Flag OFF (default)
# --------------------------------------------------------------------------
class TestStaticServingDisabled:
    def test_url_fields_present_but_static_urls_404(
        self, sample_library, cache_root, tmp_path
    ):
        """With the flag off, ImgOut still carries all four URL strings, but
        the two *_static_url paths 404 (no mount registered)."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=False)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            # All four fields present and populated as strings.
            for field in ("full_url", "thumbnail_url",
                          "full_static_url", "thumbnail_static_url"):
                assert isinstance(img[field], str) and img[field]

            # The static URLs 404 -- mounts not registered when flag is off.
            assert client.get(img["full_static_url"]).status_code == 404
            assert client.get(img["thumbnail_static_url"]).status_code == 404

    def test_flag_off_disables_every_per_size_mount(
        self, sample_library, cache_root, tmp_path
    ):
        """The flag gates the WHOLE mount loop: every per-size thumbnail URL
        in thumbnail_static_urls 404s when static serving is off, not just the
        default size."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=False)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            # Config default has 2 sizes -> 2 per-size URLs, both 404 (no mount).
            assert set(img["thumbnail_static_urls"].keys()) == {"320x240", "800x600"}
            for url in img["thumbnail_static_urls"].values():
                assert client.get(url).status_code == 404


# --------------------------------------------------------------------------
# Flag ON
# --------------------------------------------------------------------------
class TestStaticServingEnabled:
    def test_full_static_url_serves_bytes_without_api_key(
        self, sample_library, cache_root, tmp_path
    ):
        """The base_root static mount serves the real image bytes with NO
        X-API-Key header -- the explicit, opt-in security tradeoff."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            # No auth header sent at all.
            r = client.get(img["full_static_url"])
            assert r.status_code == 200
            on_disk = (sample_library.base_root / img["rel_path"]).read_bytes()
            assert r.content == on_disk

    def test_thumbnail_static_url_serves_bytes_after_generation(
        self, sample_library, cache_root, tmp_path
    ):
        """The cache_root/thumbnails static mount serves a thumbnail's bytes
        with no API key, once the default-size thumbnail exists on disk
        (generated here via the dynamic /thumb route)."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            # Trigger lazy generation of the default 320x240 thumbnail so the
            # static file exists on disk.
            gen = client.get(
                f"/api/v2/images/{img['id']}/thumb?width=320&height=240",
                headers=headers,
            )
            assert gen.status_code == 200

            # Now fetch it statically, with NO API key.
            r = client.get(img["thumbnail_static_url"])
            assert r.status_code == 200
            assert r.content == gen.content

    def test_unconfigured_size_has_no_mount_and_404s(
        self, sample_library, cache_root, tmp_path
    ):
        """A size that is NOT in cache.thumbnail_sizes gets no static mount at
        all, so its would-be static path 404s even with the flag on (and it
        never appears in thumbnail_static_urls)."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            shard = img["id"][:2]
            unconfigured_url = f"/static-thumbs-640x480/{shard}/{img['id']}_640x480.jpg"
            assert "640x480" not in img["thumbnail_static_urls"]
            assert client.get(unconfigured_url).status_code == 404

    def test_all_configured_sizes_serve_after_preheat(
        self, sample_library, cache_root, tmp_path
    ):
        """End-to-end multi-size: preheat BOTH configured sizes (320x240 and
        800x600), then fetch each via its OWN per-size mount with NO API key
        and get 200 for both -- proving config-driven multi-size serving."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            _preheat(client, headers, 320, 240)
            _preheat(client, headers, 800, 600)

            urls = img["thumbnail_static_urls"]
            assert client.get(urls["320x240"]).status_code == 200
            assert client.get(urls["800x600"]).status_code == 200

    def test_thumbnail_static_urls_dict_keys_and_resolution(
        self, sample_library, cache_root, tmp_path
    ):
        """thumbnail_static_urls has exactly the configured-size keys, and each
        URL resolves to 200 once that size is preheated."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            assert set(img["thumbnail_static_urls"].keys()) == {"320x240", "800x600"}

            _preheat(client, headers, 320, 240)
            _preheat(client, headers, 800, 600)
            for url in img["thumbnail_static_urls"].values():
                assert client.get(url).status_code == 200

    def test_thumbnail_static_url_equals_default_static_thumbnail_size(
        self, sample_library, cache_root, tmp_path
    ):
        """The singular per-image thumbnail_static_url points at the
        explicitly-configured default_static_thumbnail_size (320x240 by
        default) -- i.e. equals that entry of the dict."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            assert img["thumbnail_static_url"] == img["thumbnail_static_urls"]["320x240"]
            assert img["thumbnail_static_url"].startswith("/static-thumbs-320x240/")

    def test_icon_thumbnail_static_url_equals_default_static_icon_size(
        self, sample_library, cache_root, tmp_path
    ):
        """The singular portfolio icon_thumbnail_static_url points at the
        explicitly-configured default_static_icon_size (320x240 by default)."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True)
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            vacation = _vacation_portfolio(client, headers)

            assert vacation["icon_thumbnail_static_url"] is not None
            assert vacation["icon_thumbnail_static_url"].startswith(
                "/static-thumbs-320x240/"
            )

    def test_default_static_thumbnail_and_icon_sizes_are_independent(
        self, sample_library, cache_root, tmp_path
    ):
        """MOST IMPORTANT independence regression: with
        default_static_thumbnail_size=(800,600) and
        default_static_icon_size=(320,240) deliberately different, in the SAME
        run the per-image thumbnail_static_url resolves to the 800x600 mount
        while the portfolio icon_thumbnail_static_url resolves to the 320x240
        mount -- proving the two singular defaults are genuinely decoupled and
        neither silently falls back to thumbnail_sizes[0]."""
        settings = _make_settings(
            sample_library.base_root, cache_root, tmp_path, static_serving=True,
            default_static_thumbnail_size=(800, 600),
            default_static_icon_size=(320, 240),
        )
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)
            vacation = _vacation_portfolio(client, headers)

            # Per-image singular field follows default_static_thumbnail_size.
            assert img["thumbnail_static_url"].startswith("/static-thumbs-800x600/")
            assert img["thumbnail_static_url"] == img["thumbnail_static_urls"]["800x600"]

            # Portfolio icon follows default_static_icon_size (the OTHER size).
            assert vacation["icon_thumbnail_static_url"].startswith(
                "/static-thumbs-320x240/"
            )

            # And they serve after preheating their respective sizes.
            _preheat(client, headers, 800, 600)
            _preheat(client, headers, 320, 240)
            assert client.get(img["thumbnail_static_url"]).status_code == 200
            assert client.get(vacation["icon_thumbnail_static_url"]).status_code == 200

    def test_single_size_config_yields_one_mount_and_key(
        self, sample_library, cache_root, tmp_path
    ):
        """With a custom single-size cache.thumbnail_sizes, exactly one static
        mount and one dict key exist -- nothing hardcodes two sizes."""
        settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  static_serving=True, thumbnail_sizes=[(320, 240)])
        with TestClient(create_app(settings)) as client:
            headers = _issue_key(client)
            img = _first_image(client, headers)

            assert set(img["thumbnail_static_urls"].keys()) == {"320x240"}
            assert img["thumbnail_static_url"] == img["thumbnail_static_urls"]["320x240"]

            _preheat(client, headers, 320, 240)
            assert client.get(img["thumbnail_static_urls"]["320x240"]).status_code == 200
            # The unconfigured 800x600 has no mount here.
            shard = img["id"][:2]
            assert client.get(
                f"/static-thumbs-800x600/{shard}/{img['id']}_800x600.jpg"
            ).status_code == 404
