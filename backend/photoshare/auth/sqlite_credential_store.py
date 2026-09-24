"""
Reference CredentialStore implementation (auth hooks design doc,
"Reference (v1) implementation"): a `api_keys` table in metadata.db.

Keys are high-entropy random tokens (not user-chosen passwords), so a
straight SHA-256 hash + lookup is appropriate -- no per-request bcrypt cost
is needed. Raw keys are never stored; `issue()` returns the raw secret
exactly once and only the hash is persisted.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from photoshare.auth.protocols import Principal
from photoshare.cache.db import Database

_TOKEN_BYTES = 32  # 256 bits of entropy, urlsafe-base64 encoded by secrets.token_urlsafe


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class SqliteCredentialStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def validate(self, raw_credential: str) -> Optional[Principal]:
        key_hash = _hash_key(raw_credential)
        with self._db.cursor() as cur:
            cur.execute(
                """
                SELECT id, label, issued_at, expires_at
                FROM api_keys
                WHERE key_hash = ? AND revoked_at IS NULL
                """,
                (key_hash,),
            )
            row = cur.fetchone()
        if row is None:
            return None

        expires_at = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return None

        return Principal(
            id=row["id"],
            label=row["label"],
            issued_at=datetime.fromisoformat(row["issued_at"]),
            expires_at=expires_at,
        )

    async def revoke(self, principal_id: str) -> None:
        with self._db.cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), principal_id),
            )

    async def issue(
        self, label: str, expires_at: Optional[datetime] = None
    ) -> tuple[Principal, str]:
        raw_key = secrets.token_urlsafe(_TOKEN_BYTES)
        key_hash = _hash_key(raw_key)
        principal_id = str(uuid.uuid4())
        issued_at = datetime.now(timezone.utc)

        with self._db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_keys (id, key_hash, label, issued_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    principal_id,
                    key_hash,
                    label,
                    issued_at.isoformat(),
                    expires_at.isoformat() if expires_at else None,
                ),
            )

        principal = Principal(
            id=principal_id, label=label, issued_at=issued_at, expires_at=expires_at
        )
        return principal, raw_key
