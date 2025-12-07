"""Utility helpers for the collector."""

from __future__ import annotations

from datetime import date, datetime, time


def _normalize_raw(value):
    """Рекурсивно преобразует неподдерживаемые типы JSON (например, datetime) в строки."""

    if isinstance(value, dict):
        return {key: _normalize_raw(sub) for key, sub in value.items()}
    if isinstance(value, list):
        return [_normalize_raw(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_raw(item) for item in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value
