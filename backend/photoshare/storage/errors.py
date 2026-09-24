"""Error taxonomy (v3.0 §2.7). Distinct exception types replace v2.1's
overloaded PermissionError so each maps to an unambiguous HTTP status."""
from __future__ import annotations


class PathOutsideRootError(Exception):
    """A resolved real path (direct or via symlink) falls outside base_root.
    Maps to HTTP 403, error code PATH_OUTSIDE_ROOT."""


class FilesystemPermissionError(Exception):
    """The OS denies read access to an in-bounds path. Maps to HTTP 500,
    error code FILESYSTEM_PERMISSION_ERROR (server misconfiguration)."""


class MalformedPathError(Exception):
    """Input rel_path contains '..', is absolute, or otherwise fails
    syntactic validation before resolution is attempted. Maps to HTTP 400,
    error code MALFORMED_PATH."""


class InvalidRootPathError(Exception):
    """A requested base_root (e.g. via POST /admin/root) does not exist or is
    not a directory. Maps to HTTP 400, error code INVALID_ROOT_PATH. Raised
    before any config/root mutation is committed, so a bad path is a no-op."""


class SymlinkCycleDetected(Exception):
    """Raised internally during traversal when a cycle is detected; callers
    (rescan/preheat) catch this to log a warning and skip the subtree,
    per §2.1. Not intended to propagate to the API layer."""
