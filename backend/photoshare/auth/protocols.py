"""
Core auth interfaces (auth hooks design doc §6a).

Nothing in the service layer (PortfolioService, ImageService,
MetadataService, etc.) imports or knows about auth. Auth is a FastAPI
dependency layered in front of routes; services stay identity-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class Principal:
    """Represents the authenticated caller. Minimal now; extensible later
    (e.g., add `scopes: list[str]` when multi-tenant/RBAC is needed)."""

    id: str
    label: str
    issued_at: datetime
    expires_at: Optional[datetime]


class CredentialStore(Protocol):
    """Hook point #1: where credentials live and how they're validated.
    Swap this to move from flat-file keys -> DB-backed keys -> external
    identity provider, with zero route-layer changes."""

    async def validate(self, raw_credential: str) -> Optional[Principal]:
        """Return a Principal if raw_credential is valid and unexpired,
        else None. Must not raise on invalid input -- only on infra failure."""
        ...

    async def revoke(self, principal_id: str) -> None: ...

    async def issue(
        self, label: str, expires_at: Optional[datetime] = None
    ) -> tuple[Principal, str]:
        """Returns (Principal, raw_secret). raw_secret is shown to the
        caller exactly once and is never persisted in recoverable form."""
        ...


class AuthScheme(Protocol):
    """Hook point #2: how the credential is extracted from the request
    (header name/format). Lets API-key-in-header and future
    Bearer-JWT-in-header coexist or be swapped without touching
    CredentialStore or route handlers."""

    async def extract(self, request) -> Optional[str]:
        """Pull the raw credential string out of the request, or None if
        absent (triggers 401, not extraction failure)."""
        ...
