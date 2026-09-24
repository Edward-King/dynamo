"""
Reference AuthScheme implementation (auth hooks design doc, "Reference (v1)
implementation"): reads the raw API key out of a configurable header,
default X-API-Key (photoshare.config.schema.AuthConfig.api_key_header).
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request


class HeaderApiKeyScheme:
    def __init__(self, header_name: str = "X-API-Key") -> None:
        self._header_name = header_name

    async def extract(self, request: Request) -> Optional[str]:
        return request.headers.get(self._header_name)
