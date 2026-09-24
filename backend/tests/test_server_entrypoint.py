"""Regression coverage for the Uvicorn worker startup path."""
from __future__ import annotations

import os
import sys

from photoshare.__main__ import build_uvicorn_run_kwargs
from photoshare.api import main as api_main
from photoshare.config.schema import Settings


def _settings(*, workers: int) -> Settings:
    return Settings(
        server={"host": "127.0.0.1", "port": 8765, "workers": workers},
        storage={"base_root": "/photos"},
        cache={"cache_root": "/cache"},
    )


def test_uvicorn_kwargs_pass_configured_worker_count_to_factory_target():
    kwargs = build_uvicorn_run_kwargs(_settings(workers=2))

    assert kwargs == {
        "host": "127.0.0.1",
        "port": 8765,
        "workers": 2,
        "factory": True,
    }


def test_main_uses_import_string_factory_and_propagates_config_path(monkeypatch, tmp_path):
    """The parsed --config path reaches spawned Uvicorn worker processes."""
    from photoshare import __main__ as entrypoint

    config_path = tmp_path / "photoshare.yaml"
    calls = []

    monkeypatch.setattr(entrypoint, "load_settings", lambda path: _settings(workers=3))
    monkeypatch.setattr(
        entrypoint.uvicorn,
        "run",
        lambda app_target, **kwargs: calls.append((app_target, kwargs)),
    )
    monkeypatch.setattr(sys, "argv", ["photoshare", "--config", str(config_path)])
    monkeypatch.delenv(api_main.CONFIG_PATH_ENV_VAR, raising=False)

    entrypoint.main()

    assert os.environ[api_main.CONFIG_PATH_ENV_VAR] == str(config_path.resolve())
    assert calls == [
        (
            api_main.APP_FACTORY_TARGET,
            {
                "host": "127.0.0.1",
                "port": 8765,
                "workers": 3,
                "factory": True,
            },
        )
    ]
def test_app_factory_loads_the_config_path_provided_by_entrypoint(monkeypatch, tmp_path):
    expected = object()
    config_path = tmp_path / "photoshare.yaml"
    observed = {}

    monkeypatch.setenv(api_main.CONFIG_PATH_ENV_VAR, str(config_path))
    monkeypatch.setattr(api_main, "load_settings", lambda path: observed.setdefault("path", path))
    monkeypatch.setattr(api_main, "create_app", lambda settings: expected)

    assert api_main.app_factory() is expected
    assert observed["path"] == str(config_path)
