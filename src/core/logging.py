"""Инфраструктура структурированного логирования для Paperbird."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Final
from uuid import uuid4

LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})
REQUIRED_FIELDS: Final = ("correlation_id", "user_id", "project_id", "story_id")


def generate_correlation_id() -> str:
    """Возвращает новый идентификатор корреляции для цепочки событий."""

    return uuid4().hex


def _current_context() -> dict[str, Any]:
    """Копирует текущий контекст логирования из `ContextVar`."""

    return dict(LOG_CONTEXT.get())


def current_correlation_id() -> str | None:
    """Возвращает активный correlation_id, если он уже установлен."""

    return _current_context().get("correlation_id")


def bind_context(**values: Any) -> None:
    """Обновляет контекст логирования новыми значениями."""

    context = _current_context()
    for key, value in values.items():
        if value is None:
            context.pop(key, None)
        else:
            context[key] = value
    LOG_CONTEXT.set(context)


@contextmanager
def logging_context(**values: Any) -> Iterator[None]:
    """Временная привязка значений к контексту логирования."""

    previous = LOG_CONTEXT.set(
        {**_current_context(), **{k: v for k, v in values.items() if v is not None}}
    )
    try:
        yield
    finally:
        LOG_CONTEXT.reset(previous)


class ContextInjector(logging.Filter):
    """Добавляет значения контекста к каждому лог-записи."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 - стандартное API logging
        context = _current_context()
        for key in REQUIRED_FIELDS:
            value = context.get(key)
            if value is not None:
                setattr(record, key, value)
        return True


class StructuredFormatter(logging.Formatter):
    """Форматирует записи логов в JSON-вид с обязательными полями."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 - стандартное API logging
        payload: dict[str, Any]
        structured_payload = getattr(record, "structured_payload", None)
        if isinstance(structured_payload, dict):
            payload = dict(structured_payload)
        else:
            payload = {"message": record.getMessage()}

        payload.setdefault("level", record.levelname)
        payload.setdefault("logger", record.name)
        if record.exc_info:
            payload.setdefault(
                "exception",
                "".join(traceback.format_exception(*record.exc_info)).strip(),
            )

        for field in REQUIRED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload.setdefault(field, value)

        return json.dumps(payload, ensure_ascii=False, default=str)


class EventLogger:
    """Утилита для логирования структурированных событий."""

    def __init__(self, *, logger: logging.Logger) -> None:
        self._logger = logger

    def info(self, event: str, **fields: Any) -> None:
        """Логирует событие уровня INFO."""

        self._log(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        """Логирует событие уровня WARNING."""

        self._log(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        """Логирует событие уровня ERROR."""

        self._log(logging.ERROR, event, fields)

    def _log(self, level: int, event: str, fields: dict[str, Any]) -> None:
        context = _current_context()
        payload: dict[str, Any] = {
            key: value for key, value in context.items() if value is not None
        }
        payload.update({key: value for key, value in fields.items() if value is not None})
        payload["event"] = event
        if "correlation_id" not in payload:
            payload["correlation_id"] = generate_correlation_id()
            bind_context(correlation_id=payload["correlation_id"])
        self._logger.log(level, "", extra={"structured_payload": payload})


def event_logger(name: str) -> EventLogger:
    """Создает `EventLogger` с заданным именем логгера."""

    logger = logging.getLogger(name)
    return EventLogger(logger=logger)
