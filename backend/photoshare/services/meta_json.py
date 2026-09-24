"""
meta.json sidecar parsing (v3.0 §2.3) and icon resolution (§2.4).

Malformed meta.json (e.g. tags as a comma-separated string instead of a
JSON array) is never silently coerced -- it is reported as a non-fatal
error in RescanResult.errors, per §2.3.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from natsort import natsorted

META_JSON_FILENAME = "meta.json"


@dataclass
class VirtualImageEntry:
    rel_path: str
    alternate_name: Optional[str] = None
    tags: list[str] = field(default_factory=list)


@dataclass
class MetaJson:
    portfolio: Optional[str] = None
    description: str = ""
    icon_dir: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    virtual: bool = False
    images: list[VirtualImageEntry] = field(default_factory=list)


class MetaJsonParseError(Exception):
    """Raised when meta.json is malformed. Caught by the rescan loop and
    recorded in RescanResult.errors rather than aborting the scan (§2.3/§2.5)."""


def parse_meta_json(dir_path: Path) -> Optional[MetaJson]:
    """Returns None if no meta.json exists in dir_path. Raises MetaJsonParseError
    on malformed content (e.g. tags not a JSON array)."""
    meta_path = dir_path / META_JSON_FILENAME
    if not meta_path.is_file():
        return None

    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MetaJsonParseError(f"{meta_path}: invalid JSON ({exc})") from exc

    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise MetaJsonParseError(
            f"{meta_path}: 'tags' must be a JSON array of strings, got {tags!r} "
            f"(comma-separated strings are not accepted -- §2.3)."
        )

    images_raw = raw.get("images", [])
    images: list[VirtualImageEntry] = []
    if raw.get("virtual", False):
        for entry in images_raw:
            if "rel_path" not in entry:
                raise MetaJsonParseError(
                    f"{meta_path}: virtual portfolio image entry missing required 'rel_path': {entry!r}"
                )
            entry_tags = entry.get("tags", [])
            if not isinstance(entry_tags, list) or not all(isinstance(t, str) for t in entry_tags):
                raise MetaJsonParseError(
                    f"{meta_path}: image entry 'tags' must be a JSON array of strings: {entry!r}"
                )
            images.append(
                VirtualImageEntry(
                    rel_path=entry["rel_path"],
                    alternate_name=entry.get("alternate_name"),
                    tags=entry_tags,
                )
            )

    return MetaJson(
        portfolio=raw.get("portfolio"),
        description=raw.get("description", ""),
        icon_dir=raw.get("icon_dir"),
        tags=tags,
        virtual=bool(raw.get("virtual", False)),
        images=images,
    )


def resolve_icon(
    dir_rel_path: str,
    dir_real_path: Path,
    meta: Optional[MetaJson],
    candidate_image_names_in_dir: list[str],
) -> Optional[str]:
    """
    Single unambiguous icon resolution algorithm (v3.0 §2.4). Returns a
    rel_path (relative to base_root) pointing at the resolved icon image,
    or None if no resolution is possible (e.g. empty portfolio).

    Steps:
      1. icon_dir present + contains a path separator -> path relative to base_root.
      2. icon_dir present, bare filename -> relative to current directory.
      3. Absent or unresolvable -> first image alphabetically (natural sort).
    """
    icon_dir = meta.icon_dir if meta else None

    if icon_dir:
        if "/" in icon_dir or "\\" in icon_dir:
            # Step 1: path relative to base_root. Resolution/existence is the
            # caller's responsibility (it has access to the StorageAdapter);
            # this function returns the *candidate* path for the caller to verify.
            return icon_dir.replace("\\", "/")
        else:
            # Step 2: bare filename, relative to current directory.
            return f"{dir_rel_path.rstrip('/')}/{icon_dir}".lstrip("/")

    # Step 3: fall back to first image alphabetically, case-insensitive natural sort.
    if not candidate_image_names_in_dir:
        return None
    sorted_names = natsorted(candidate_image_names_in_dir, key=lambda n: n.lower())
    first_name = sorted_names[0]
    return f"{dir_rel_path.rstrip('/')}/{first_name}".lstrip("/")
