"""Утилиты для работы с Telethon."""

from __future__ import annotations


def _strip_quotes(value: str) -> str:
    if (value.startswith("\"") and value.endswith("\"")) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1].strip()
    return value


def normalize_session_value(raw: str | None) -> str:
    """Приводит строку Telethon-сессии к чистому значению."""

    if not raw:
        return ""
    session = raw.strip()
    if not session:
        return ""

    session = _strip_quotes(session)

    if session.startswith("StringSession(") and session.endswith(")"):
        inner = session[len("StringSession(") : -1].strip()
        session = _strip_quotes(inner).strip()

    if session.startswith("session="):
        session = session.split("=", 1)[1].strip()
        session = _strip_quotes(session)

    return session


__all__ = ["normalize_session_value"]
