"""Константы, используемые в сервисах сюжетов."""

from __future__ import annotations

from core.constants import IMAGE_QUALITY_CHOICES, IMAGE_SIZE_CHOICES

ALLOWED_IMAGE_SIZES = {choice[0] for choice in IMAGE_SIZE_CHOICES}
ALLOWED_IMAGE_QUALITIES = {choice[0] for choice in IMAGE_QUALITY_CHOICES}
OPENAI_MODELS_WITH_FIXED_TEMPERATURE = {"gpt-5", "gpt-5.0", "gpt-5o", "gpt-5o-mini"}
