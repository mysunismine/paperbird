"""Service wrapper for selecting image providers."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from core.constants import IMAGE_DEFAULT_MODEL
from stories.paperbird_stories.services.helpers import _looks_like_yandex_art_model

from .providers import (
    GeneratedImage,
    ImageGenerationProvider,
    OpenAIImageProvider,
    YandexArtProvider,
)


@dataclass(slots=True)
class StoryImageGenerator:
    """Обёртка вокруг провайдера генерации изображений."""

    provider: ImageGenerationProvider

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
    ) -> GeneratedImage:
        """Генерирует изображение."""
        return self.provider.generate(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
        )


def default_image_generator(*, model: str | None = None) -> StoryImageGenerator:
    """Возвращает генератор изображений по умолчанию."""

    selected_model = (model or getattr(settings, "OPENAI_IMAGE_MODEL", IMAGE_DEFAULT_MODEL)).strip()
    if _looks_like_yandex_art_model(selected_model):
        provider = YandexArtProvider(model=selected_model)
    else:
        provider = OpenAIImageProvider(model=selected_model)
    return StoryImageGenerator(provider=provider)
