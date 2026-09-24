"""
Identity Model: root_id + content hash (v3.0 §3.6).

Replaces the old path-derived UUID scheme. Every installation has a
permanent root_id (UUID4, generated once at first startup). Image identity
is derived from file content; portfolio identity is derived from a
normalized relative path -- both namespaced under root_id via uuid5.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

CONTENT_HASH_PREFIX_BYTES = 4096


def compute_content_hash(file_path: str | Path) -> str:
    """sha256(first_4KB_of_file_bytes + str(file_size_bytes)) -- §3.6."""
    path = Path(file_path)
    size = path.stat().st_size
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        hasher.update(f.read(CONTENT_HASH_PREFIX_BYTES))
    hasher.update(str(size).encode("utf-8"))
    return hasher.hexdigest()


def normalize_rel_path(rel_path: str) -> str:
    """POSIX-normalized rel_path relative to base_root, case-preserved (§3.6)."""
    normalized = PurePosixPath(rel_path.replace("\\", "/"))
    # Collapse any "." segments and redundant slashes; case is preserved.
    parts = [p for p in normalized.parts if p not in (".", "")]
    return "/".join(parts)


def compute_img_uuid(root_id: str, content_hash: str) -> uuid.UUID:
    """Img_UUID = uuid5(root_id, content_hash) -- §3.6."""
    namespace = uuid.UUID(root_id)
    return uuid.uuid5(namespace, content_hash)


def compute_port_uuid(root_id: str, normalized_rel_path: str) -> uuid.UUID:
    """Port_UUID = uuid5(root_id, normalized_rel_path) -- §3.6."""
    namespace = uuid.UUID(root_id)
    return uuid.uuid5(namespace, normalized_rel_path)


@dataclass
class Installation:
    root_id: str
    schema_version: int
    created_at: str


def load_or_create_installation(cache_root: str | Path) -> Installation:
    """
    Reads cache_root/installation.json; if absent, generates a new root_id
    (UUID4) and writes it. This happens exactly once per installation, ever
    (v3.0 §3.6, How It Works Guide §1).
    """
    cache_root_path = Path(cache_root)
    cache_root_path.mkdir(parents=True, exist_ok=True)
    installation_path = cache_root_path / "installation.json"

    if installation_path.is_file():
        data = json.loads(installation_path.read_text(encoding="utf-8"))
        return Installation(
            root_id=data["root_id"],
            schema_version=data["schema_version"],
            created_at=data["created_at"],
        )

    # schema_version 2: images split into content-only `images` +
    # placement `image_locations` (cross-portfolio duplicate fix). No
    # in-place migration is performed; upgrading from an older metadata.db
    # requires deleting metadata.db and running a full rescan.
    installation = Installation(
        root_id=str(uuid.uuid4()),
        schema_version=2,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    installation_path.write_text(
        json.dumps(
            {
                "root_id": installation.root_id,
                "schema_version": installation.schema_version,
                "created_at": installation.created_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return installation
