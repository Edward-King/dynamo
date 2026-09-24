"""
FastAPI dependency wiring for auth (auth hooks design doc).

get_current_principal is applied at the router level (see api/main.py),
not per-endpoint, so it's impossible to forget on a new route. The
exception is /api/v2/admin/health, mounted on a router with no auth
dependency.

KNOWN INTERIM LIMITATION (surfaced explicitly, not hidden -- see
how_it_works_guide.md "Known interim limitations"): admin routes are not
yet scope-restricted from regular read-access keys. Until Principal gains
a `scopes` field and a require_scope("admin") dependency is added (see the
auth hooks doc's extension-points table), any valid API key can call
admin routes such as /admin/rescan. Acceptable for a single-operator
deployment; a real gap for anything multi-tenant.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from photoshare.auth.protocols import AuthScheme, CredentialStore, Principal


async def resolve_principal(
    request: Request, scheme: AuthScheme, store: CredentialStore
) -> Principal:
    raw = await scheme.extract(request)
    if raw is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_MISSING", "message": "No credential provided"},
        )
    principal = await store.validate(raw)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_INVALID", "message": "Invalid or expired credential"},
        )
    return principal


def build_get_current_principal(scheme: AuthScheme, store: CredentialStore):
    """
    Factory returning a FastAPI dependency closed over the configured
    AuthScheme/CredentialStore instances (themselves built once at app
    startup from Settings.auth -- see api/dependencies.py). This keeps
    the dependency itself free of any global state.
    """

    async def get_current_principal(request: Request) -> Principal:
        return await resolve_principal(request, scheme, store)

    return get_current_principal
