"""Placeholder image helpers."""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass


@dataclass(slots=True)
class GeneratedImage:
    """Результат генерации изображения."""

    data: bytes
    mime_type: str = "image/png"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """Создает PNG-чанк."""
    return (
        struct.pack("!I", len(data))
        + tag
        + data
        + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _placeholder_image_bytes(prompt: str) -> bytes:
    """Генерирует байты изображения-заглушки."""
    width = height = 320
    digest = hashlib.sha256(prompt.encode("utf-8", "ignore")).digest()
    color = digest[0], digest[8], digest[16]
    pixel = bytes([color[0], color[1], color[2], 255])
    rows = []
    for _ in range(height):
        rows.append(b"\x00" + pixel * width)
    raw = b"".join(rows)
    header = struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0)
    compressed = zlib.compress(raw, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
