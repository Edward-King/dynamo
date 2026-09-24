"""
SearchService (v3.0 §3.3).

Tag-based search across portfolios and images. Simple SQL LIKE/exact-match
queries against the tag junction tables -- no external search index in v1.
"""
from __future__ import annotations

from uuid import UUID

from photoshare.api.models import ImgOut, PortfolioOut
from photoshare.cache.db import Database


class SearchService:
    def __init__(self, db: Database, portfolio_service, image_service) -> None:
        self._db = db
        self._portfolio_service = portfolio_service
        self._image_service = image_service

    def search_portfolios_by_tag(self, tag: str) -> list[PortfolioOut]:
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT port_uuid FROM portfolio_tags WHERE tag = ? COLLATE NOCASE",
                (tag,),
            )
            port_uuids = [UUID(row["port_uuid"]) for row in cur.fetchall()]
        return [self._portfolio_service.get_portfolio(uid) for uid in port_uuids]

    def search_images_by_tag(self, tag: str) -> list[ImgOut]:
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT img_uuid FROM image_tags WHERE tag = ? COLLATE NOCASE",
                (tag,),
            )
            img_uuids = [UUID(row["img_uuid"]) for row in cur.fetchall()]
        return [self._image_service.get_image_metadata(uid) for uid in img_uuids]
