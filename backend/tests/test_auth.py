"""
Auth-hook validation tests (auth hooks design doc; implemented in
photoshare/auth/*). Covers the CredentialStore contract (validate/issue/
revoke, hashed-at-rest storage) and the route-level 401 taxonomy
(AUTH_MISSING vs AUTH_INVALID) enforced by the get_current_principal
dependency.

A protected route (/api/v2/portfolios/root) and the unprotected exception
(/api/v2/admin/health) are used to exercise the enforcement boundary.
"""
from __future__ import annotations

import asyncio

import pytest

from photoshare.auth.sqlite_credential_store import _hash_key as hash_key

PROTECTED_ROUTE = "/api/v2/portfolios/root"
HEALTH_ROUTE = "/api/v2/admin/health"
RESCAN_ROUTE = "/api/v2/admin/rescan"


# --------------------------------------------------------------------------
# Route-level enforcement via the TestClient
# --------------------------------------------------------------------------
class TestRouteAuthEnforcement:
    def test_valid_key_succeeds_on_protected_route(self, client, api_key_header):
        r = client.get(PROTECTED_ROUTE, headers=api_key_header)
        assert r.status_code == 200

    def test_missing_key_returns_401_auth_missing(self, client):
        """No credential -> 401 AUTH_MISSING (distinct from an invalid one)."""
        r = client.get(PROTECTED_ROUTE)
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_MISSING"

    def test_garbage_key_returns_401_auth_invalid(self, client):
        r = client.get(PROTECTED_ROUTE, headers={"X-API-Key": "not-a-real-key"})
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_INVALID"

    def test_error_envelope_shape_for_both_401_cases(self, client):
        """Both 401 bodies use the uniform {code, message, detail} envelope
        (api/models.ErrorResponse) and are distinguishable by `code`."""
        missing = client.get(PROTECTED_ROUTE).json()
        invalid = client.get(PROTECTED_ROUTE, headers={"X-API-Key": "x"}).json()
        for body in (missing, invalid):
            assert set(body.keys()) == {"code", "message", "detail"}
        assert missing["code"] == "AUTH_MISSING"
        assert invalid["code"] == "AUTH_INVALID"

    def test_health_endpoint_reachable_without_auth(self, client):
        """/admin/health is the documented no-auth exception (mounted on the
        no-auth router in api/main.py)."""
        r = client.get(HEALTH_ROUTE)
        assert r.status_code == 200
        assert "root_id" in r.json()

    def test_admin_rescan_requires_auth(self, client):
        """Every admin route *except* /health requires a credential."""
        r = client.post(RESCAN_ROUTE, json={"port_uuid": None, "recursive": True})
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_MISSING"

    def test_admin_rescan_succeeds_with_valid_key(self, client, api_key_header):
        r = client.post(RESCAN_ROUTE, json={"port_uuid": None, "recursive": True},
                        headers=api_key_header)
        assert r.status_code == 200


# --------------------------------------------------------------------------
# Rejected-credential modes, exercised through the real route
# --------------------------------------------------------------------------
class TestRejectedKeyModes:
    def _mint(self, client, expires_at=None, revoke=False):
        from datetime import datetime, timezone

        from photoshare.auth.sqlite_credential_store import SqliteCredentialStore

        store = SqliteCredentialStore(client.app.state.db)
        principal, raw = asyncio.run(store.issue("mode-key", expires_at=expires_at))
        if revoke:
            asyncio.run(store.revoke(principal.id))
        return raw

    def test_expired_key_is_rejected(self, client):
        from datetime import datetime, timedelta, timezone

        raw = self._mint(client, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        r = client.get(PROTECTED_ROUTE, headers={"X-API-Key": raw})
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_INVALID"

    def test_revoked_key_is_rejected(self, client):
        raw = self._mint(client, revoke=True)
        r = client.get(PROTECTED_ROUTE, headers={"X-API-Key": raw})
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_INVALID"


# --------------------------------------------------------------------------
# SqliteCredentialStore.validate() unit behavior (parametrized failure modes)
# --------------------------------------------------------------------------
class TestCredentialStoreValidate:
    def test_valid_key_validates_to_principal(self, credential_store, valid_key):
        principal = asyncio.run(credential_store.validate(valid_key))
        assert principal is not None
        assert principal.label == "valid-key"

    @pytest.mark.parametrize(
        "key_fixture, reason",
        [
            ("garbage", "unknown key hash"),
            ("expired_key", "expires_at in the past"),
            ("revoked_key", "revoked_at set"),
        ],
    )
    def test_invalid_keys_validate_to_none(self, request, credential_store,
                                           key_fixture, reason):
        """validate() must return None (never raise) for every rejected
        mode: unknown, expired, and revoked keys."""
        raw = ("this-is-not-a-key" if key_fixture == "garbage"
               else request.getfixturevalue(key_fixture))
        assert asyncio.run(credential_store.validate(raw)) is None, reason


# --------------------------------------------------------------------------
# Secret-at-rest: raw key never recoverable from storage (§ auth doc)
# --------------------------------------------------------------------------
class TestSecretAtRest:
    def test_only_sha256_hash_is_stored_never_plaintext(
        self, credential_store, valid_key, db
    ):
        """The api_keys row stores SHA-256(raw_key), and the raw key appears
        nowhere in the table -- issue()'s one-time-secret contract."""
        with db.cursor() as cur:
            cur.execute("SELECT id, key_hash, label FROM api_keys")
            rows = cur.fetchall()

        assert len(rows) == 1
        row = rows[0]
        assert row["key_hash"] == hash_key(valid_key)
        assert row["key_hash"] != valid_key
        # The raw secret must not be persisted verbatim in any column.
        assert valid_key not in {row["id"], row["key_hash"], row["label"]}

    def test_revoke_is_idempotent_and_scoped(self, credential_store):
        """revoke() only affects the targeted principal and is safe to call
        on an unknown id (no raise)."""
        raw_a = _issue_sync(credential_store, "key-a")
        raw_b = _issue_sync(credential_store, "key-b")
        principal_a = asyncio.run(credential_store.validate(raw_a))

        asyncio.run(credential_store.revoke(principal_a.id))
        asyncio.run(credential_store.revoke("00000000-0000-0000-0000-000000000000"))

        assert asyncio.run(credential_store.validate(raw_a)) is None
        assert asyncio.run(credential_store.validate(raw_b)) is not None


def _issue_sync(store, label):
    _p, raw = asyncio.run(store.issue(label))
    return raw
