"""Helper utilities for web collector parsing."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from django.utils import timezone

try:  # pragma: no cover - optional dependency guard
    from dateutil import parser as date_parser  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    date_parser = None  # type: ignore[assignment]


def collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


DATETIME_DELIMITERS = ("|", "•", "·", " / ", " — ", " – ", "—", "−", "―")
DATETIME_PATTERN = re.compile(
    r"\d{1,2}[\./-]\d{1,2}[\./-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
)


def _datetime_candidates(value: str | None) -> list[str]:
    if not value:
        return []
    raw = value.strip()
    normalized = collapse_whitespace(raw.replace("\xa0", " "))
    candidates: list[str] = []
    for candidate in (raw, normalized):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for delimiter in DATETIME_DELIMITERS:
        if delimiter in normalized:
            trimmed = normalized.split(delimiter, 1)[0].strip()
            if trimmed and trimmed not in candidates:
                candidates.append(trimmed)
    match = DATETIME_PATTERN.search(normalized)
    if match:
        snippet = match.group(0).strip()
        if snippet and snippet not in candidates:
            candidates.append(snippet)
    return candidates


def parse_datetime(value: str | None) -> datetime | None:
    for candidate in _datetime_candidates(value):
        try:
            if date_parser is None:
                parsed = datetime.fromisoformat(candidate)
            else:
                parsed = date_parser.parse(candidate)
        except (ValueError, TypeError):  # pragma: no cover - defensive
            continue
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
        return parsed
    return None


def normalize_url(base: str, url: str | None) -> str:
    if not url:
        return ""
    return urljoin(base, url)


def strip_tracking_params(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    new_query = urlencode(pairs)
    return urlunparse(parsed._replace(query=new_query))
