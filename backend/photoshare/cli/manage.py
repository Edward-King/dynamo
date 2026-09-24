"""
Admin CLI: `python -m photoshare.cli.manage create-api-key --config config.dev.yaml --label "..."`

Prints the raw API key exactly once, consistent with CredentialStore.issue()'s
one-time-secret contract (auth hooks design doc).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photoshare.cache.db import Database
from photoshare.config.schema import load_settings
from photoshare.identity.models import load_or_create_installation


def _get_db(config_path: str) -> Database:
    settings = load_settings(config_path)
    cache_root = Path(settings.cache.cache_root)
    load_or_create_installation(cache_root)  # ensures cache_root + installation.json exist
    db_path = cache_root / "metadata.db"
    return Database(db_path, busy_timeout_ms=settings.database.busy_timeout_ms)


def create_api_key(args: argparse.Namespace) -> None:
    import asyncio
    from datetime import datetime, timedelta, timezone

    from photoshare.auth.sqlite_credential_store import SqliteCredentialStore

    db = _get_db(args.config)
    store = SqliteCredentialStore(db)

    expires_at = None
    if args.expires_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=args.expires_days)

    principal, raw_key = asyncio.run(store.issue(args.label, expires_at=expires_at))

    print("API key created. This raw key is shown ONLY ONCE -- store it securely now:")
    print()
    print(f"  {raw_key}")
    print()
    print(f"  principal id : {principal.id}")
    print(f"  label        : {principal.label}")
    print(f"  issued_at    : {principal.issued_at.isoformat()}")
    print(f"  expires_at   : {principal.expires_at.isoformat() if principal.expires_at else 'never'}")
    db.close()


def revoke_api_key(args: argparse.Namespace) -> None:
    import asyncio

    from photoshare.auth.sqlite_credential_store import SqliteCredentialStore

    db = _get_db(args.config)
    store = SqliteCredentialStore(db)
    asyncio.run(store.revoke(args.principal_id))
    print(f"Revoked API key for principal id {args.principal_id} (if it existed and was active).")
    db.close()


def dump_tree(args: argparse.Namespace) -> None:
    import json
    from uuid import UUID

    from photoshare.services import export_service
    from photoshare.services.metadata_repository import MetadataRepository, NotFoundError

    settings = load_settings(args.config)
    cache_root = Path(settings.cache.cache_root)
    installation = load_or_create_installation(cache_root)
    db_path = cache_root / "metadata.db"
    db = Database(db_path, busy_timeout_ms=settings.database.busy_timeout_ms)
    try:
        repo = MetadataRepository(db)
        root_uuid = UUID(args.root) if args.root else None
        try:
            tree = export_service.build_portfolio_tree(
                repo, root_uuid, installation.schema_version,
                settings.cache.thumbnail_sizes,
                settings.cache.default_static_thumbnail_size,
                settings.cache.default_static_icon_size,
            )
        except NotFoundError as exc:
            print(f"FATAL: {exc.message}", file=sys.stderr)
            sys.exit(1)

        Path(args.output).write_text(
            json.dumps(tree, indent=2, default=str), encoding="utf-8"
        )

        portfolios, images = export_service.summarize_tree(tree["tree"])
        print(
            f"Wrote portfolio-tree dump to {args.output} "
            f"({portfolios} portfolios, {images} image placements)."
        )
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="manage.py")
    parser.add_argument("--config", required=True, help="Path to a YAML config file (required).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-api-key", help="Issue a new API key.")
    create_parser.add_argument("--label", required=True, help="Human-readable label for audit logs.")
    create_parser.add_argument(
        "--expires-days",
        type=int,
        default=None,
        help="Days until expiry. Omit for a non-expiring key.",
    )
    create_parser.set_defaults(func=create_api_key)

    revoke_parser = subparsers.add_parser("revoke-api-key", help="Revoke an existing API key.")
    revoke_parser.add_argument("--principal-id", required=True, help="Principal id (uuid) to revoke.")
    revoke_parser.set_defaults(func=revoke_api_key)

    dump_parser = subparsers.add_parser(
        "dump-tree",
        help="Dump the portfolio tree (or a subtree) to a JSON file.",
    )
    dump_parser.add_argument(
        "--output", required=True, help="Path to write the JSON dump to."
    )
    dump_parser.add_argument(
        "--root",
        default=None,
        help="Optional Port_UUID to dump as the subtree root. Defaults to the tree root.",
    )
    dump_parser.set_defaults(func=dump_tree)

    args = parser.parse_args()
    try:
        args.func(args)
    except FileNotFoundError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
