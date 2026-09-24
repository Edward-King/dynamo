"""
Shared pytest fixtures for the PhotoShare Gallery Proxy test suite.

main.py reads config.json and builds its httpx.AsyncClient at *import
time* (module-level `CONFIG = load_config()` / `_client = httpx.AsyncClient(...)`).
To keep the test suite fully hermetic (no real config.json required, no
real PhotoShare server required), we:

  1. Write a throwaway config.json alongside main.py before it is ever
     imported, pointing at a fake upstream URL that is never actually
     contacted (respx intercepts all outbound httpx traffic).
  2. Import main fresh for the test session.
  3. Use respx to mock the upstream (PhotoShare) responses per-test.

This does not touch any real config.json a developer may have in place --
if one already exists, it is backed up and restored around the test run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROXY_DIR = Path(__file__).parent
CONFIG_PATH = PROXY_DIR / "config.json"
BACKUP_PATH = PROXY_DIR / "config.json.pytest-backup"

TEST_CONFIG = {
    "photoshare_base_url": "http://photoshare.test",
    "photoshare_api_key": "test-api-key",
    "proxy_host": "127.0.0.1",
    "proxy_port": 8100,
    "cors_allow_origins": ["*"],
}


def _install_test_config() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.rename(BACKUP_PATH)
    CONFIG_PATH.write_text(json.dumps(TEST_CONFIG))


def _restore_real_config() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
    if BACKUP_PATH.exists():
        BACKUP_PATH.rename(CONFIG_PATH)


@pytest.fixture(scope="session", autouse=True)
def _proxy_test_config():
    """Ensure main.py sees a valid, predictable config.json for the whole run."""
    _install_test_config()
    try:
        yield
    finally:
        _restore_real_config()


@pytest.fixture(scope="session")
def app(_proxy_test_config):
    """Import the FastAPI app once config.json is guaranteed to exist."""
    sys.path.insert(0, str(PROXY_DIR))
    import main  # noqa: E402  (import must happen after config is written)

    return main.app


@pytest.fixture()
def upstream_base():
    return TEST_CONFIG["photoshare_base_url"]
