"""Утилиты для работы с определением языка."""

from __future__ import annotations

import re

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
LATIN_RE = re.compile(r"[A-Za-z]")


def detect_language(text: str | None) -> str:
    """Возвращает ISO-код языка на основе простых эвристик."""

    if not text:
        return "unknown"
    cyrillic = len(CYRILLIC_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    if cyrillic and cyrillic >= latin:
        return "ru"
    if latin and latin > cyrillic:
        return "en"
    return "unknown"


__all__ = ["detect_language"]
