"""
Configuration schema for the PhotoShare service.

Implements the YAML configuration system defined in the project's
"YAML Configuration Proposal" design document:

- Per-environment YAML files (config.dev.yaml / config.prod.yaml), selected
  via a REQUIRED --config CLI flag. There is no implicit default file.
- Environment variables prefixed PHOTOSHARE_, using "__" as the nesting
  separator, take precedence over the loaded YAML file.
- Validation happens once at startup via Pydantic; invalid config fails
  fast with a clear error instead of surfacing as a runtime error mid-request.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    cors_allowed_origins: list[str] = Field(default_factory=list)


class StorageConfig(BaseModel):
    base_root: str = "/photos_root"
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"]
    )


class TileThresholdConfig(BaseModel):
    width: int = 4000
    height: int = 3000


class CacheConfig(BaseModel):
    cache_root: str = "/var/lib/photoshare/cache"
    thumbnail_sizes: list[tuple[int, int]] = Field(
        default_factory=lambda: [(320, 240), (800, 600)]
    )
    # The size the singular static-URL fields resolve to, chosen explicitly
    # rather than implicitly reusing thumbnail_sizes[0]. Both must be members
    # of thumbnail_sizes (validated below) so the URL always has a mount.
    #   - default_static_thumbnail_size -> ImgOut.thumbnail_static_url (per image)
    #   - default_static_icon_size      -> PortfolioOut.icon_thumbnail_static_url
    default_static_thumbnail_size: tuple[int, int] = (320, 240)
    default_static_icon_size: tuple[int, int] = (320, 240)
    tile_threshold_px: TileThresholdConfig = Field(default_factory=TileThresholdConfig)

    @model_validator(mode="after")
    def _validate_default_static_sizes(self) -> "CacheConfig":
        """Both singular-URL default sizes must appear in thumbnail_sizes --
        otherwise the singular static URL would point at a size with no
        StaticFiles mount and always 404. Accumulate ALL failures so an error
        naming both offending fields is raised when both are invalid, rather
        than short-circuiting on the first."""
        failures = []
        for field_name in ("default_static_thumbnail_size", "default_static_icon_size"):
            value = getattr(self, field_name)
            if value not in self.thumbnail_sizes:
                failures.append((field_name, value))
        if failures:
            named = ", ".join(f"{name}={value}" for name, value in failures)
            raise ValueError(
                f"cache.{named}: each must be one of the configured "
                f"thumbnail_sizes {self.thumbnail_sizes}."
            )
        return self


class DatabaseConfig(BaseModel):
    busy_timeout_ms: int = 5000
    wal_autocheckpoint_pages: int = 1000


class AuthConfig(BaseModel):
    scheme: Literal["api_key", "jwt"] = "api_key"
    api_key_header: str = "X-API-Key"
    key_expiry_days: Optional[int] = 365


class RateLimitingConfig(BaseModel):
    read_requests_per_minute: int = 100
    admin_requests_per_minute: int = 10


class RescanConfig(BaseModel):
    on_startup: bool = True
    watch_filesystem: bool = False
    # §2.1 states cycle detection is mandatory. This remains a configurable
    # setting by deliberate, accepted-risk decision (see YAML config
    # proposal, "Decisions" section) for test-harness convenience only.
    # Production configuration files MUST keep this true.
    cycle_detection: bool = True


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    rescan_log_path: str = "logs/rescan_history.jsonl"


class Settings(BaseSettings):
    """
    Root configuration object. Loaded once at startup from:
      1. PHOTOSHARE_-prefixed environment variables (highest precedence)
      2. The YAML file passed via --config (required, no default)
      3. Hardcoded field defaults above (lowest precedence)
    """

    model_config = SettingsConfigDict(
        env_prefix="PHOTOSHARE_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    # When true, base_root and cache_root/thumbnails are additionally exposed
    # via unauthenticated StaticFiles mounts (/static-photos, /static-thumbs)
    # for direct <img src> embedding. Default false: enabling it makes any
    # image/thumbnail fetchable with no X-API-Key -- an explicit, opt-in
    # security tradeoff (see main.py static-mount block and §2.2).
    enable_static_file_serving: bool = False

    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    rate_limiting: RateLimitingConfig = Field(default_factory=RateLimitingConfig)
    rescan: RescanConfig = Field(default_factory=RescanConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def _validate_root_separation(self) -> "Settings":
        """
        Enforces the §2.2/§2.6 invariant: cache_root must never be the same
        as, or nested inside, base_root. This check has no equivalent in the
        prose spec; it is a direct, automatic consequence of making
        configuration explicit and validated (see YAML config proposal).
        """
        base_root = Path(self.storage.base_root).resolve()
        cache_root = Path(self.cache.cache_root).resolve()

        if base_root == cache_root:
            raise ValueError(
                f"storage.base_root and cache.cache_root must not be the same "
                f"path (both resolve to {base_root})."
            )

        try:
            cache_root.relative_to(base_root)
            raise ValueError(
                f"cache.cache_root ({cache_root}) must not be nested inside "
                f"storage.base_root ({base_root}). System-managed cache data "
                f"must never live inside the user's photo tree (§2.2)."
            )
        except ValueError as exc:
            # relative_to() raises ValueError when NOT a subpath -- that's
            # the success case. Re-raise only if it's our own message above.
            if "must not be nested inside" in str(exc):
                raise
        return self


def load_settings(config_path: str | Path) -> Settings:
    """
    Loads and validates a Settings object from a required YAML file path,
    then layers PHOTOSHARE_-prefixed environment variable overrides on top
    (handled automatically by pydantic-settings via env_prefix).

    Raises FileNotFoundError with a clear message if config_path does not
    exist -- startup must fail immediately, not fall back to a guessed file.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {path}. The --config flag is required "
            f"and must point to an existing YAML file (e.g. config.dev.yaml "
            f"or config.prod.yaml)."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Pydantic-settings applies env var overrides on top of constructor
    # kwargs automatically, honoring the precedence order in the design doc.
    return Settings(**raw)
