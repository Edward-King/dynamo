#!/usr/bin/env python3
"""Add content-based version tags to the gallery's CSS/JS links.

Run this on the *deployed* copy of the frontend, after copying the files
into the web root:

    sudo python3 stamp_assets.py /var/www/photoshare-gallery

For every ``.html`` file in that folder, each local stylesheet or script
link such as ``href="style.css"`` or ``src="script.js"`` becomes
``href="style.css?v=3f9a1c2b7d4e"``, where the tag is the start of the
SHA-256 hash of that file as deployed. A tag changes only when the file's
contents change, so browsers fetch a fresh copy after a deploy that
changed it and can keep using their cached copy otherwise. The Apache
vhost (apache/photoshare-gallery.conf) lets browsers cache tagged CSS/JS
for a long time, and makes them check back on every visit for untagged
files and HTML.

Safe to run repeatedly: existing tags are replaced, not stacked. Links to
other sites, absolute paths, and files that don't exist in the folder are
left alone. The files in the repo stay untagged, so opening them locally
or serving them with a plain static server still works.

Standard library only (Python 3.8+).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

TAG_LENGTH = 12

# href="..." / src="..." (single or double quotes) pointing at a .css or .js
# file, optionally already carrying a ?v= tag from an earlier run.
_ASSET_ATTR = re.compile(
    r"""(?P<attr>\b(?:href|src)\s*=\s*)(?P<quote>["'])"""
    r"""(?P<path>[^"'?#\s]+\.(?:css|js))"""
    r"""(?:\?v=[0-9A-Za-z]*)?"""
    r"""(?P=quote)""",
    re.IGNORECASE,
)


def file_tag(path: Path) -> str:
    """Return the version tag for a file: the start of its SHA-256 hash."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:TAG_LENGTH]


def _is_local_relative(ref: str) -> bool:
    # Leave alone: other sites (https://, //cdn...), data:/javascript: URLs,
    # and absolute paths (which may be served by something other than this
    # folder, e.g. the proxy).
    return not (ref.startswith(("/", "\\")) or ":" in ref)


def stamp_html(html: str, base_dir: Path, root: Path) -> tuple[str, list[str]]:
    """Return (new_html, list of "ref -> ref?v=tag" changes) for one page."""
    changes: list[str] = []
    tags: dict[Path, str] = {}

    def replace(match: re.Match[str]) -> str:
        ref = match.group("path")
        if not _is_local_relative(ref):
            return match.group(0)
        target = (base_dir / ref).resolve()
        # Only files that exist and live inside the folder being stamped.
        if not target.is_file() or root not in target.parents:
            return match.group(0)
        if target not in tags:
            tags[target] = file_tag(target)
        new_ref = f"{ref}?v={tags[target]}"
        new = f"{match.group('attr')}{match.group('quote')}{new_ref}{match.group('quote')}"
        if new != match.group(0):
            changes.append(f"{ref} -> {new_ref}")
        return new

    return _ASSET_ATTR.sub(replace, html), changes


def stamp_folder(folder: Path, dry_run: bool = False) -> dict[str, list[str]]:
    """Stamp every .html file directly inside ``folder``.

    Returns a mapping of HTML file name to the changes made in it (pages
    with nothing to change are omitted).
    """
    root = folder.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")
    report: dict[str, list[str]] = {}
    for page in sorted(root.glob("*.html")):
        original = page.read_text(encoding="utf-8")
        updated, changes = stamp_html(original, page.parent, root)
        if changes:
            report[page.name] = changes
            if not dry_run:
                # Write in place so the file keeps its owner and permissions.
                with page.open("w", encoding="utf-8", newline="") as fh:
                    fh.write(updated)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add content-based ?v= tags to CSS/JS links in the gallery's HTML files.",
    )
    parser.add_argument("folder", type=Path, help="deployed frontend folder, e.g. /var/www/photoshare-gallery")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    args = parser.parse_args(argv)

    try:
        report = stamp_folder(args.folder, dry_run=args.dry_run)
    except (NotADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not report:
        print("Nothing to change: all CSS/JS links are already up to date.")
        return 0
    verb = "Would update" if args.dry_run else "Updated"
    for page, changes in report.items():
        print(f"{verb} {page}:")
        for change in changes:
            print(f"  {change}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
