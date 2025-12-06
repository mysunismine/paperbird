"""Утилиты для обработки настроек локали и часового пояса проекта."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

UTC_PATTERN = re.compile(
    r"^UTC(?P<sign>[+-])(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?$",
    re.IGNORECASE,
)


def _parse_fixed_offset(label: str):
    match = UTC_PATTERN.match(label.strip())
    if not match:
        return None
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes") or 0)
    if hours > 23 or minutes > 59:
        return None
    delta = timedelta(hours=hours, minutes=minutes)
    if match.group("sign") == "-":
        delta = -delta
    normalized = _format_offset(delta)
    return dt_timezone(delta, name=normalized)


def _format_offset(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    absolute = abs(total_minutes)
    hours, minutes = divmod(absolute, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def resolve_timezone(value: str | None):
    """Возвращает объект tzinfo для заданной метки или вызывает ValueError."""

    label = (value or "").strip() or "UTC"
    try:
        return ZoneInfo(label)
    except ZoneInfoNotFoundError:
        fixed = _parse_fixed_offset(label.upper())
        if fixed:
            return fixed
    raise ValueError(f"Unknown timezone: {label}")


def is_timezone_valid(value: str | None) -> bool:
    """Проверяет, является ли часовой пояс корректным."""
    try:
        resolve_timezone(value)
        return True
    except ValueError:
        return False


def format_datetime_for_locale(moment: datetime, locale: str | None) -> str:
    """Форматирует дату и время в соответствии с локалью."""
    locale = (locale or "").lower()
    if locale.startswith("ru"):
        return moment.strftime("%d.%m.%Y %H:%M")
    return moment.strftime("%Y-%m-%d %H:%M")


def build_project_datetime_context(project) -> dict[str, Any]:
    """Возвращает словарь с локализованными строками даты/времени для проекта."""

    tz_label = project.time_zone or "UTC"
    try:
        tzinfo = resolve_timezone(tz_label)
    except ValueError:
        tzinfo = timezone.get_default_timezone()
        tz_label = "UTC"
    localized_now = timezone.now().astimezone(tzinfo)
    offset = localized_now.utcoffset() or timedelta()
    return {
        "formatted": format_datetime_for_locale(localized_now, project.locale),
        "iso": localized_now.isoformat(),
        "offset": _format_offset(offset),
        "time_zone": tz_label,
    }


__all__ = [
    "build_project_datetime_context",
    "format_datetime_for_locale",
    "is_timezone_valid",
    "resolve_timezone",
]
