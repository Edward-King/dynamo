"""
Ad-hoc smoke test (NOT the deferred formal test harness -- that remains a
separate future task per Edward's instruction). This just verifies the
app boots, a rescan populates the DB, and a handful of endpoints respond
with the expected status codes end-to-end via FastAPI's TestClient.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from photoshare.api.main import create_app  # noqa: E402
from photoshare.auth.sqlite_credential_store import SqliteCredentialStore  # noqa: E402
from photoshare.config.schema import load_settings  # noqa: E402

CACHE_ROOT = PROJECT_ROOT / "dev_cache"
LOGS_DIR = PROJECT_ROOT / "logs"


def reset_state() -> None:
    if CACHE_ROOT.exists():
        shutil.rmtree(CACHE_ROOT)
    if LOGS_DIR.exists():
        shutil.rmtree(LOGS_DIR)


def main() -> None:
    reset_state()
    settings = load_settings(str(PROJECT_ROOT / "config.dev.yaml"))

    failures: list[str] = []

    def check(label: str, condition: bool, extra: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {label} {extra}")
        if not condition:
            failures.append(label)

    def as_json(obj) -> str:
        """Render diagnostic values as actual JSON text (null/true/false), not
        Python's repr (None/True/False), so failure output matches what the
        API really sends over the wire."""
        return json.dumps(obj)

    with TestClient(create_app(settings)) as client:
        # --- startup / rescan sanity -------------------------------------
        db = client.app.state.db
        repo = client.app.state.repo
        root_row = repo.get_root_portfolio_row()
        check("root portfolio row exists after startup rescan", root_row is not None)

        # --- health (no auth) ---------------------------------------------
        r = client.get("/api/v2/admin/health")
        check("GET /admin/health -> 200", r.status_code == 200, str(r.status_code))
        check("health payload has root_id", "root_id" in r.json())

        # --- auth enforcement ------------------------------------------------
        r = client.get("/api/v2/portfolios/root")
        check("GET /portfolios/root with no key -> 401 AUTH_MISSING", r.status_code == 401 and r.json().get("code") == "AUTH_MISSING", as_json(r.json()))

        r = client.get("/api/v2/portfolios/root", headers={"X-API-Key": "bogus"})
        check("GET /portfolios/root with bad key -> 401 AUTH_INVALID", r.status_code == 401 and r.json().get("code") == "AUTH_INVALID", as_json(r.json()))

        # --- issue a real API key via the credential store directly --------
        import asyncio

        store = SqliteCredentialStore(db)
        principal, raw_key = asyncio.run(store.issue("smoke-test-key"))
        headers = {"X-API-Key": raw_key}

        r = client.get("/api/v2/portfolios/root", headers=headers)
        check("GET /portfolios/root with valid key -> 200", r.status_code == 200, str(r.status_code))
        root = r.json()
        check("root has direct_image_count/total_image_count", "direct_image_count" in root and "total_image_count" in root)
        check("root total_image_count == 3 (3 sample images)", root["total_image_count"] == 3, str(root["total_image_count"]))

        r = client.get(f"/api/v2/portfolios/{root['id']}/children", headers=headers)
        check("GET children of root -> 200", r.status_code == 200, str(r.status_code))
        children = r.json()
        check("root has 2 children (vacation, family)", len(children) == 2, as_json(len(children)))

        vacation = next((c for c in children if c["name"] == "vacation"), None)
        check("vacation child found", vacation is not None)
        if vacation:
            check("vacation has tags from meta.json", sorted(vacation["tags"]) == sorted(["vacation", "2026"]), as_json(vacation["tags"]))
            check("vacation description from meta.json", vacation["description"] == "Summer vacation photos", vacation["description"])

            r = client.get(f"/api/v2/portfolios/{vacation['id']}/images", headers=headers)
            check("GET vacation images -> 200", r.status_code == 200, str(r.status_code))
            paged = r.json()
            check("vacation has 2 images, paged result shape ok", paged["total"] == 2 and len(paged["items"]) == 2, as_json(paged))

            if paged["items"]:
                img = paged["items"][0]
                img_uuid = img["id"]

                check(
                    "image has all four access-URL fields",
                    all(isinstance(img.get(f), str) and img.get(f) for f in (
                        "full_url", "thumbnail_url",
                        "full_static_url", "thumbnail_static_url")),
                    as_json({k: img.get(k) for k in (
                        "full_url", "thumbnail_url",
                        "full_static_url", "thumbnail_static_url")}),
                )

                r = client.get(f"/api/v2/images/{img_uuid}/full", headers=headers)
                check("GET image full -> 200", r.status_code == 200, str(r.status_code))
                etag = r.headers.get("etag")
                check("full response has ETag header", etag is not None)

                r2 = client.get(
                    f"/api/v2/images/{img_uuid}/full",
                    headers={**headers, "If-None-Match": etag},
                )
                check("conditional GET with matching ETag -> 304", r2.status_code == 304, str(r2.status_code))

                r3 = client.get(f"/api/v2/images/{img_uuid}/thumb", headers=headers)
                check("GET thumbnail -> 200", r3.status_code == 200, str(r3.status_code))
                check("thumbnail content-type is jpeg", r3.headers.get("content-type") == "image/jpeg", r3.headers.get("content-type"))

        # --- 404 behavior --------------------------------------------------
        r = client.get("/api/v2/portfolios/00000000-0000-0000-0000-000000000000", headers=headers)
        check("GET nonexistent portfolio -> 404 PORTFOLIO_NOT_FOUND", r.status_code == 404 and r.json().get("code") == "PORTFOLIO_NOT_FOUND", as_json(r.json()))

        r = client.get("/api/v2/images/00000000-0000-0000-0000-000000000000/full", headers=headers)
        check("GET nonexistent image -> 404 IMAGE_NOT_FOUND", r.status_code == 404 and r.json().get("code") == "IMAGE_NOT_FOUND", as_json(r.json()))

        # --- search ----------------------------------------------------------
        r = client.get("/api/v2/search/portfolios", params={"tag": "vacation"}, headers=headers)
        check("search portfolios by tag -> 200, 1 match", r.status_code == 200 and len(r.json()) == 1, as_json(r.json()))

        # --- admin rescan ----------------------------------------------------
        r = client.post("/api/v2/admin/rescan", json={"port_uuid": None, "recursive": True}, headers=headers)
        check("POST admin/rescan -> 200", r.status_code == 200, str(r.status_code))
        rescan_body = r.json()
        check("rescan result has expected fields", "images_added" in rescan_body and "duration_ms" in rescan_body, as_json(rescan_body))

        # --- viewer config -----------------------------------------------
        r = client.get("/api/v2/viewer/config", headers=headers)
        check("GET viewer/config -> 200", r.status_code == 200, str(r.status_code))

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED: {failures}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
