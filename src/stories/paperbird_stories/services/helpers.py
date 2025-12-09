"""Утилиты для сервисов сюжетов."""

from __future__ import annotations

import base64
from datetime import date, datetime

from core.constants import (
    IMAGE_DEFAULT_QUALITY,
    IMAGE_DEFAULT_SIZE,
    OPENAI_DEFAULT_TEMPERATURE,
)

from .constants import (
    ALLOWED_IMAGE_QUALITIES,
    ALLOWED_IMAGE_SIZES,
    OPENAI_MODELS_WITH_FIXED_TEMPERATURE,
)


def _json_safe(value):
    """Преобразует произвольные объекты в JSON-совместимый вид."""

    if isinstance(value, dict):
        return {str(key): _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        return base64.b64encode(value).decode("ascii")
    return value


def normalize_image_size(value: str | None) -> str:
    """Возвращает безопасный размер изображения."""

    if not value:
        return IMAGE_DEFAULT_SIZE
    cleaned = str(value).strip().lower()
    legacy_map = {
        "512x512": "1024x1024",
        "256x256": "1024x1024",
    }
    cleaned = legacy_map.get(cleaned, cleaned)
    if cleaned in ALLOWED_IMAGE_SIZES:
        return cleaned
    return IMAGE_DEFAULT_SIZE


def normalize_image_quality(value: str | None) -> str:
    """Возвращает допустимое качество изображения."""

    if not value:
        return IMAGE_DEFAULT_QUALITY
    cleaned = str(value).strip().lower()
    legacy_map = {
        "standard": "medium",
        "hd": "high",
    }
    cleaned = legacy_map.get(cleaned, cleaned)
    if cleaned in ALLOWED_IMAGE_QUALITIES:
        return cleaned
    return IMAGE_DEFAULT_QUALITY


def build_yandex_model_uri(model: str, *, folder_id: str, scheme: str = "gpt") -> str:
    """Формирует URI модели YandexGPT/ART."""

    cleaned = (model or "").strip()
    if cleaned.startswith(("gpt://", "art://")):
        return cleaned
    base, _, version = cleaned.partition("/")
    version = version or "latest"
    return f"{scheme}://{folder_id}/{base}/{version}"


def _looks_like_yandex_text_model(model: str) -> bool:
    """Проверяет, похожа ли модель на текстовую модель Yandex."""
    lowered = (model or "").lower()
    prefixes = ("yandex", "qwen", "gpt-oss", "gemma", "gpt://")
    return any(lowered.startswith(prefix) for prefix in prefixes)


def _looks_like_gemini_model(model: str) -> bool:
    """Проверяет, похожа ли модель на модель Gemini."""
    lowered = (model or "").lower()
    return lowered.startswith("gemini")


def _looks_like_yandex_art_model(model: str) -> bool:
    """Проверяет, похожа ли модель на модель YandexART."""
    lowered = (model or "").lower()
    return lowered.startswith("yandex") or lowered.startswith("art://")


def _strip_code_fence(text: str) -> str:
    """Удаляет ограничители кода."""
    if not text:
        return text
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        body = stripped.strip("`")
        first_newline = body.find("\n")
        if first_newline != -1:
            language = body[:first_newline].strip().lower()
            if language in {"json", "yaml", "markdown", "text"}:
                return body[first_newline + 1 :].strip()
        return body.strip()
    return stripped


def _openai_temperature_for_model(model: str) -> float:
    """Возвращает температуру OpenAI для модели."""

    lowered = (model or "").lower()
    if any(lowered.startswith(prefix) for prefix in ("gpt-5", "gpt-5o")):
        return 1.0
    if model in OPENAI_MODELS_WITH_FIXED_TEMPERATURE:
        return 1.0
    return OPENAI_DEFAULT_TEMPERATURE
