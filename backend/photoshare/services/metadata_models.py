"""MetadataRepository dataclasses (v3.0 §3.5) and RescanResult (§4.5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass
class DirMeta:
    id: UUID
    name: str
    virtual: bool
    rel_path: str
    real_path: str
    tags: list[str]
    description: str
    icon_image_id: Optional[UUID]


@dataclass
class ImageMeta:
    id: UUID
    name: str
    rel_path: str
    real_path: str
    width: int
    height: int
    size_bytes: int
    mime_type: str
    file_modified_at: datetime
    taken_at: datetime
    tags: list[str]


@dataclass
class RescanResult:
    started_at: datetime
    completed_at: datetime
    portfolios_added: int = 0
    portfolios_moved: int = 0
    portfolios_removed: int = 0
    images_added: int = 0
    images_moved: int = 0
    images_removed: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
