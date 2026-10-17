"""Concurrent, deterministic preview-image vetting for web search sources.

Fetches only the first 64KB of each candidate via an HTTP range request,
parses width/height straight from the file header (no imaging library
dependency), and keeps only images that look like a real photograph rather
than a logo or icon - a minimum size plus a bounded aspect ratio.
"""

import asyncio
import logging
import struct

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_HEAD_BYTES = 65536
MIN_WIDTH = 400
MIN_HEIGHT = 220
MIN_RATIO = 1.15
MAX_RATIO = 3.0


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _gif_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10 or data[:3] != b"GIF":
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return width, height


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    if data[12:16] == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if data[12:16] == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        if marker in (0xD8, 0xD9):
            return None
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        index += 2 + length
    return None


def _dimensions(data: bytes) -> tuple[int, int] | None:
    for parser in (_png_dimensions, _gif_dimensions, _webp_dimensions, _jpeg_dimensions):
        dimensions = parser(data)
        if dimensions is not None:
            return dimensions
    return None


def is_photograph(width: int, height: int) -> bool:
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return False
    ratio = max(width, height) / max(1, min(width, height))
    return MIN_RATIO <= ratio <= MAX_RATIO


async def _inspect(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(
            url,
            headers={"Range": f"bytes=0-{_HEAD_BYTES - 1}", "Accept": "image/*"},
        )
        if response.status_code not in (200, 206):
            return None
        dimensions = _dimensions(response.content)
        if dimensions is None:
            return None
        return url if is_photograph(*dimensions) else None
    except (httpx.HTTPError, struct.error, ValueError):
        return None


async def vet_preview_images(urls: list[str], *, limit: int = 2) -> list[str]:
    """Vet candidate image URLs concurrently; return up to `limit` that look
    like real photographs rather than logos or icons. Never raises."""
    candidates = [url for url in dict.fromkeys(urls) if url]
    if not candidates:
        return []
    timeout = httpx.Timeout(settings.WEB_SEARCH_IMAGE_PREVIEW_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            vetted = await asyncio.gather(
                *(_inspect(client, url) for url in candidates), return_exceptions=True
            )
    except Exception:
        logger.warning("web_search.image_preview_unavailable")
        return []
    return [url for url in vetted if isinstance(url, str)][:limit]
