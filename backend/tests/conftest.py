"""
Shared pytest fixtures for the PhotoShare v3.0 formal test suite.

Everything here is isolated per-test and per-tmp_path so the suite is safe
to run under pytest-xdist (`pytest -n auto`): no fixed shared paths, no
network ports, no reliance on smoke_test.py or on test ordering.

Design goals (mirrors the wiring in photoshare/api/main.py::_build_app_state,
so tests exercise the *real* production wiring rather than hand-rolled
copies):

  * Temp photo library built under tmp_path via a small LibraryBuilder that
    can create real JPEGs, raw-byte files, nested dirs, and arbitrary
    symlinks (including cycles) -- never touching disk outside tmp_path.
  * Temp SQLite DB created through the real cache.db.Database class, so the
    schema is applied via the same code path production uses (schema.sql +
    apply_schema), not a hand-copied DDL string.
  * Real service objects (FilesystemStorageAdapter, MetadataRepository,
    MetadataService, PortfolioService, ImageService, ...) wired against the
    temp fs + temp DB. Nothing is mocked -- SQLite and a temp dir are both
    real and cheap.
  * A SqliteCredentialStore bound to the temp DB, plus helpers minting
    valid / expired / revoked API keys.
  * A FastAPI TestClient built via the real create_app() factory against a
    Settings object pointed at the temp fs/DB (this is how production wires
    it; strictly stronger than a Depends() override), yielding the client
    plus a valid X-API-Key header dict.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pytest
from PIL import Image

# Ensure the project root (which contains the `photoshare` package) is
# importable regardless of the directory pytest is invoked from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from photoshare.auth.sqlite_credential_store import SqliteCredentialStore  # noqa: E402
from photoshare.cache.db import Database  # noqa: E402
from photoshare.config.schema import Settings  # noqa: E402
from photoshare.identity.models import (  # noqa: E402
    Installation,
    load_or_create_installation,
)
from photoshare.services.image_service import ImageService  # noqa: E402
from photoshare.services.metadata_repository import MetadataRepository  # noqa: E402
from photoshare.services.metadata_service import MetadataService  # noqa: E402
from photoshare.services.portfolio_service import PortfolioService  # noqa: E402
from photoshare.services.thumbnail_service import ThumbnailService  # noqa: E402
from photoshare.storage.filesystem_adapter import FilesystemStorageAdapter  # noqa: E402


# --------------------------------------------------------------------------
# Temp photo-library builder
# --------------------------------------------------------------------------
class LibraryBuilder:
    """Constructs an arbitrary photo-library tree under a temp base_root.

    All paths are relative to base_root; every method returns the absolute
    Path it created so tests can chain/assert. Symlink helpers make it easy
    to construct the layouts the storage/rescan cycle-detection code must
    survive (self-cycles, mutual cycles, external targets, siblings).
    """

    def __init__(self, base_root: Path) -> None:
        self.base_root = base_root
        base_root.mkdir(parents=True, exist_ok=True)

    def add_dir(self, rel_path: str) -> Path:
        d = self.base_root / rel_path
        d.mkdir(parents=True, exist_ok=True)
        return d

    def add_jpeg(self, rel_path: str, color: tuple[int, int, int] = (200, 30, 30),
                 size: tuple[int, int] = (32, 24)) -> Path:
        """Write a *real* decodable JPEG so thumbnail/EXIF paths work."""
        p = self.base_root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, color).save(p, "JPEG")
        return p

    def add_bytes(self, rel_path: str, data: bytes) -> Path:
        """Write raw bytes under an image extension. Used to control the
        content hash exactly (e.g. two byte-identical files). PIL will fail
        to decode these, which the rescan handles gracefully (w/h -> 0)."""
        p = self.base_root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def add_meta(self, dir_rel_path: str, meta: dict | str) -> Path:
        """Write a meta.json. Pass a dict for well-formed JSON, or a raw
        string to inject malformed content (e.g. tags as a CSV string)."""
        d = self.base_root / dir_rel_path
        d.mkdir(parents=True, exist_ok=True)
        p = d / "meta.json"
        p.write_text(meta if isinstance(meta, str) else json.dumps(meta), encoding="utf-8")
        return p

    def add_symlink(self, link_rel_path: str, target: str | Path,
                    target_is_absolute: bool = False) -> Path:
        """Create a symlink at link_rel_path.

        target: if target_is_absolute, an absolute Path/str used verbatim;
        otherwise a path relative to base_root (resolved to absolute before
        the link is created, keeping the temp tree self-contained)."""
        link = self.base_root / link_rel_path
        link.parent.mkdir(parents=True, exist_ok=True)
        target_path = Path(target) if target_is_absolute else (self.base_root / target)
        link.symlink_to(target_path)
        return link


@pytest.fixture
def library(tmp_path: Path) -> LibraryBuilder:
    """Empty temp photo library rooted at tmp_path/photos."""
    return LibraryBuilder(tmp_path / "photos")


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    """Isolated cache_root, a *sibling* of the photo library so it satisfies
    the §2.2 base_root/cache_root separation invariant."""
    d = tmp_path / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def sample_library(library: LibraryBuilder) -> LibraryBuilder:
    """A small populated library mirroring sample_photos/: a root with two
    child portfolios (vacation w/ meta.json + 2 images, family w/ 1 image).
    Used by the API/TestClient tests."""
    library.add_jpeg("vacation/beach.jpg", color=(20, 80, 200))
    library.add_jpeg("vacation/mountain.jpg", color=(80, 160, 80))
    library.add_meta("vacation", {
        "description": "Summer vacation photos",
        "tags": ["vacation", "2026"],
    })
    library.add_jpeg("family/portrait.jpg", color=(200, 180, 120))
    return library


# --------------------------------------------------------------------------
# Settings / DB / installation
# --------------------------------------------------------------------------
def _make_settings(base_root: Path, cache_root: Path, tmp_path: Path,
                   *, on_startup: bool, cycle_detection: bool = True) -> Settings:
    """Build a real, validated Settings object (same schema production uses)
    pointed at the temp fs/DB. Constructed via the Pydantic model directly
    so the §2.2 root-separation validator actually runs."""
    return Settings(
        server={"host": "127.0.0.1", "port": 0, "workers": 1, "cors_allowed_origins": []},
        storage={"base_root": str(base_root)},
        cache={"cache_root": str(cache_root)},
        rescan={"on_startup": on_startup, "watch_filesystem": False,
                "cycle_detection": cycle_detection},
        logging={"level": "WARNING",
                 "rescan_log_path": str(tmp_path / "logs" / "rescan_history.jsonl")},
    )


@pytest.fixture
def settings(library: LibraryBuilder, cache_root: Path, tmp_path: Path) -> Settings:
    """Settings for service-level tests: startup rescan OFF (tests drive
    rescan explicitly), cycle detection ON."""
    return _make_settings(library.base_root, cache_root, tmp_path, on_startup=False)


@pytest.fixture
def installation(cache_root: Path) -> Installation:
    """Real installation.json (root_id minted once) under the temp cache."""
    return load_or_create_installation(cache_root)


@pytest.fixture
def root_id(installation: Installation) -> str:
    return installation.root_id


@pytest.fixture
def db(cache_root: Path) -> Database:
    """Freshly-schema'd, isolated SQLite DB via the real Database class
    (applies schema.sql through apply_schema -- not a hand-copied schema)."""
    database = Database(cache_root / "metadata.db")
    yield database
    database.close()


# --------------------------------------------------------------------------
# Real service objects wired against temp fs + temp DB
# --------------------------------------------------------------------------
@pytest.fixture
def storage(library: LibraryBuilder) -> FilesystemStorageAdapter:
    return FilesystemStorageAdapter(base_root=str(library.base_root))


@pytest.fixture
def repo(db: Database) -> MetadataRepository:
    return MetadataRepository(db)


@pytest.fixture
def metadata_service(db: Database, storage: FilesystemStorageAdapter, root_id: str,
                     cache_root: Path, tmp_path: Path) -> MetadataService:
    return MetadataService(
        db=db,
        storage=storage,
        root_id=root_id,
        cache_root=cache_root,
        rescan_log_path=tmp_path / "logs" / "rescan_history.jsonl",
        cycle_detection_enabled=True,
    )


@pytest.fixture
def metadata_service_factory(db: Database, storage: FilesystemStorageAdapter,
                             root_id: str, cache_root: Path,
                             tmp_path: Path) -> Callable[..., MetadataService]:
    """Factory so a test can build a MetadataService with a specific
    cycle_detection_enabled setting (used by the toggle test)."""
    def _make(*, cycle_detection_enabled: bool = True) -> MetadataService:
        return MetadataService(
            db=db,
            storage=storage,
            root_id=root_id,
            cache_root=cache_root,
            rescan_log_path=tmp_path / "logs" / "rescan_history.jsonl",
            cycle_detection_enabled=cycle_detection_enabled,
        )
    return _make


@pytest.fixture
def portfolio_service(repo: MetadataRepository, settings: Settings) -> PortfolioService:
    return PortfolioService(
        repo, settings.cache.thumbnail_sizes, settings.cache.default_static_icon_size
    )


@pytest.fixture
def image_service(repo: MetadataRepository,
                  storage: FilesystemStorageAdapter,
                  settings: Settings) -> ImageService:
    return ImageService(
        repo,
        storage,
        settings.cache.thumbnail_sizes,
        settings.cache.default_static_thumbnail_size,
    )


@pytest.fixture
def thumbnail_service(cache_root: Path) -> ThumbnailService:
    return ThumbnailService(cache_root)


# --------------------------------------------------------------------------
# Auth: credential store + key-minting helpers
# --------------------------------------------------------------------------
@pytest.fixture
def credential_store(db: Database) -> SqliteCredentialStore:
    return SqliteCredentialStore(db)


def _issue(store: SqliteCredentialStore, label: str,
           expires_at: Optional[datetime] = None) -> str:
    """Synchronously issue a key against `store`, returning the raw secret."""
    _principal, raw_key = asyncio.run(store.issue(label, expires_at=expires_at))
    return raw_key


@pytest.fixture
def valid_key(credential_store: SqliteCredentialStore) -> str:
    return _issue(credential_store, "valid-key",
                  expires_at=datetime.now(timezone.utc) + timedelta(days=365))


@pytest.fixture
def expired_key(credential_store: SqliteCredentialStore) -> str:
    """A key whose expires_at is already in the past."""
    return _issue(credential_store, "expired-key",
                  expires_at=datetime.now(timezone.utc) - timedelta(days=1))


@pytest.fixture
def revoked_key(credential_store: SqliteCredentialStore) -> str:
    raw = _issue(credential_store, "revoked-key")
    principal = asyncio.run(credential_store.validate(raw))
    assert principal is not None
    asyncio.run(credential_store.revoke(principal.id))
    return raw


# --------------------------------------------------------------------------
# FastAPI TestClient against the real app factory + temp fs/DB
# --------------------------------------------------------------------------
@pytest.fixture
def client(sample_library: LibraryBuilder, cache_root: Path, tmp_path: Path):
    """Full app via create_app(), startup rescan ON so the DB is populated
    from the temp sample library. Entering the context manager triggers the
    lifespan (rescan). Yields the ready TestClient."""
    from fastapi.testclient import TestClient

    from photoshare.api.main import create_app

    app_settings = _make_settings(sample_library.base_root, cache_root, tmp_path,
                                  on_startup=True)
    with TestClient(create_app(app_settings)) as test_client:
        yield test_client


@pytest.fixture
def api_key_header(client) -> dict[str, str]:
    """A valid X-API-Key header dict, minted against the running app's own
    DB (app.state.db) after startup -- mirrors smoke_test.py."""
    store = SqliteCredentialStore(client.app.state.db)
    raw = _issue(store, "test-suite-key",
                 expires_at=datetime.now(timezone.utc) + timedelta(days=365))
    return {"X-API-Key": raw}


# --------------------------------------------------------------------------
# Readable JSON assertion diffs
# --------------------------------------------------------------------------
# Plain `assert response_body == expected` still works fine and is what most
# tests use -- pytest's assertion rewriting prints a real diff on failure.
# The one caveat: because `response_body` is the *deserialized* Python dict
# (from `r.json()`), that diff renders Python's `None`/`True`/`False`, not
# JSON's `null`/`true`/`false` -- even though the actual HTTP response bytes
# are fully spec-compliant JSON. That's just Python's repr(), not a claim
# about wire format.
#
# `assert_json_equal` is an opt-in helper for tests/spots where a failure's
# JSON text (matching exactly what a client would see on the wire) is more
# useful to read than Python's repr -- e.g. when comparing a whole response
# body. It's not required or auto-applied anywhere; use plain `assert` for
# everything else.
def assert_json_equal(actual, expected, msg: str = "") -> None:
    """Assert two JSON-able values are equal, raising a diff rendered as
    actual JSON text (null/true/false, sorted keys) instead of Python's
    repr (None/True/False) when they don't match."""
    if actual != expected:
        actual_text = json.dumps(actual, indent=2, sort_keys=True)
        expected_text = json.dumps(expected, indent=2, sort_keys=True)
        raise AssertionError(
            f"{msg}\n--- actual (as JSON) ---\n{actual_text}\n"
            f"--- expected (as JSON) ---\n{expected_text}"
        )


@pytest.fixture
def assert_json():
    """Fixture form of assert_json_equal, for tests that prefer fixtures
    over a module-level import."""
    return assert_json_equal
