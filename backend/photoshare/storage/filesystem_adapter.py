"""
FilesystemStorageAdapter (v3.0 §3.1/§3.2).

Read-only filesystem access, symlink resolution, and content hashing.
No service above this layer touches os/pathlib directly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, Optional

from natsort import natsorted

from photoshare.identity.models import compute_content_hash
from photoshare.storage.errors import (
    FilesystemPermissionError,
    MalformedPathError,
    PathOutsideRootError,
)
from photoshare.storage.models import DirInfo, ImgInfo

ALLOWED_EXTENSIONS_DEFAULT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}


class FilesystemStorageAdapter:
    def __init__(self, base_root: str, allowed_extensions: Optional[list[str]] = None) -> None:
        self._base_root = Path(base_root).resolve()
        self._allowed_extensions = {
            ext.lower() for ext in (allowed_extensions or list(ALLOWED_EXTENSIONS_DEFAULT))
        }

    # -- root management -----------------------------------------------
    def set_root(self, base_root: str) -> None:
        """§3.2 -- changing the root never affects any UUID (see identity module)."""
        self._base_root = Path(base_root).resolve()

    @property
    def base_root(self) -> Path:
        return self._base_root

    # -- path validation / resolution -----------------------------------
    def _validate_syntax(self, rel_path: str) -> None:
        if rel_path.startswith("/") or rel_path.startswith("\\"):
            raise MalformedPathError(f"rel_path must be relative, got absolute path: {rel_path}")
        parts = rel_path.replace("\\", "/").split("/")
        if ".." in parts:
            raise MalformedPathError(f"rel_path must not contain '..': {rel_path}")

    def resolve(self, rel_path: str) -> Path:
        """Validates syntax, resolves to a real path, and enforces root containment (§2.7)."""
        self._validate_syntax(rel_path)
        candidate = (self._base_root / rel_path).resolve()
        try:
            candidate.relative_to(self._base_root)
        except ValueError:
            raise PathOutsideRootError(
                f"Resolved path {candidate} falls outside base_root {self._base_root}"
            )
        return candidate

    # -- directory operations --------------------------------------------
    def list_dirs(self, rel_path: str) -> list[DirInfo]:
        real_dir = self.resolve(rel_path)
        if not real_dir.is_dir():
            return []

        results: list[DirInfo] = []
        try:
            entries = list(os.scandir(real_dir))
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc

        for entry in entries:
            name = entry.name
            if name.startswith("."):
                # §2.6 -- dot-directories are invisible to every layer above storage.
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if not is_dir:
                continue

            is_symlink = entry.is_symlink()
            child_rel_path = f"{rel_path.rstrip('/')}/{name}".lstrip("/")
            try:
                real_target = Path(entry.path).resolve()
                real_target.relative_to(self._base_root)
            except ValueError:
                # Symlinked directory resolves outside base_root -- excluded (§2.1 security rule).
                continue
            except OSError:
                continue

            direct_count, child_count = self._count_direct(real_target)
            results.append(
                DirInfo(
                    name=name,
                    rel_path=child_rel_path,
                    real_path=str(real_target),
                    is_symlink=is_symlink,
                    direct_image_count=direct_count,
                    child_port_count=child_count,
                )
            )

        results.sort(key=lambda d: natsorted([d.name])[0])
        return results

    def _count_direct(self, real_dir: Path) -> tuple[int, int]:
        image_count = 0
        dir_count = 0
        try:
            for entry in os.scandir(real_dir):
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        dir_count += 1
                    elif entry.is_file() or entry.is_symlink():
                        if Path(entry.name).suffix.lower() in self._allowed_extensions:
                            image_count += 1
                except OSError:
                    continue
        except (PermissionError, FileNotFoundError):
            pass
        return image_count, dir_count

    # -- image operations --------------------------------------------------
    def list_images(self, rel_path: str) -> list[ImgInfo]:
        real_dir = self.resolve(rel_path)
        if not real_dir.is_dir():
            return []

        results: list[ImgInfo] = []
        try:
            entries = list(os.scandir(real_dir))
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc

        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() not in self._allowed_extensions:
                continue
            try:
                is_file_like = entry.is_file() or (entry.is_symlink() and Path(entry.path).is_file())
            except OSError:
                continue
            if not is_file_like:
                continue

            try:
                real_target = Path(entry.path).resolve()
                real_target.relative_to(self._base_root)
            except ValueError:
                continue
            except OSError:
                continue

            child_rel_path = f"{rel_path.rstrip('/')}/{name}".lstrip("/")
            try:
                content_hash = compute_content_hash(real_target)
            except OSError as exc:
                raise FilesystemPermissionError(str(exc)) from exc

            results.append(
                ImgInfo(
                    name=name,
                    rel_path=child_rel_path,
                    real_path=str(real_target),
                    is_symlink=entry.is_symlink(),
                    content_hash=content_hash,
                )
            )

        results.sort(key=lambda i: natsorted([i.name])[0])
        return results

    def open_image(self, rel_path: str) -> BinaryIO:
        real_path = self.resolve(rel_path)
        try:
            return open(real_path, "rb")
        except PermissionError as exc:
            raise FilesystemPermissionError(str(exc)) from exc

    # -- symlink operations --------------------------------------------------
    def resolve_symlink(self, rel_path: str) -> Optional[str]:
        candidate = self._base_root / rel_path
        if not candidate.is_symlink():
            return None
        return str(candidate.resolve())

    # -- identity support --------------------------------------------------
    def compute_content_hash(self, rel_path: str) -> str:
        real_path = self.resolve(rel_path)
        return compute_content_hash(real_path)
