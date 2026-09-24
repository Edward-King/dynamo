"""Storage-layer dataclasses (v3.0 §3.1)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DirInfo:
    name: str
    rel_path: str
    real_path: str
    is_symlink: bool
    direct_image_count: int
    child_port_count: int


@dataclass
class ImgInfo:
    name: str
    rel_path: str
    real_path: str
    is_symlink: bool
    content_hash: str
