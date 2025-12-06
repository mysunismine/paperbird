"""Пакет сервисов для сюжетов."""

from core.services.worker import enqueue_task

from .constants import (
    ALLOWED_IMAGE_QUALITIES,
    ALLOWED_IMAGE_SIZES,
    OPENAI_MODELS_WITH_FIXED_TEMPERATURE,
)
from .exceptions import (
    ImageGenerationFailed,
    PublicationFailed,
    RewriteFailed,
    StoryCreationError,
)
from .factory import StoryFactory
from .helpers import (
    _json_safe,
    _looks_like_yandex_art_model,
    _looks_like_yandex_text_model,
    _openai_temperature_for_model,
    _strip_code_fence,
    build_yandex_model_uri,
    normalize_image_quality,
    normalize_image_size,
)
from .images import (
    GeneratedImage,
    ImageGenerationProvider,
    OpenAIImageProvider,
    StoryImageGenerator,
    YandexArtProvider,
    default_image_generator,
)
from .prompts import build_prompt, make_prompt_messages
from .publisher import (
    PublisherBackend,
    PublishResult,
    StoryPublisher,
    TelethonPublisherBackend,
    default_publisher_for_story,
)
from .rewrite import (
    OpenAIChatProvider,
    ProviderResponse,
    RewriteProvider,
    StoryRewriter,
    YandexGPTProvider,
    default_rewriter,
)

__all__ = [
    "ALLOWED_IMAGE_QUALITIES",
    "ALLOWED_IMAGE_SIZES",
    "OPENAI_MODELS_WITH_FIXED_TEMPERATURE",
    "ImageGenerationFailed",
    "PublicationFailed",
    "RewriteFailed",
    "StoryCreationError",
    "StoryFactory",
    "_json_safe",
    "_looks_like_yandex_art_model",
    "_looks_like_yandex_text_model",
    "_openai_temperature_for_model",
    "_strip_code_fence",
    "build_yandex_model_uri",
    "normalize_image_quality",
    "normalize_image_size",
    "enqueue_task",
    "GeneratedImage",
    "ImageGenerationProvider",
    "OpenAIImageProvider",
    "StoryImageGenerator",
    "YandexArtProvider",
    "default_image_generator",
    "build_prompt",
    "make_prompt_messages",
    "PublishResult",
    "PublisherBackend",
    "StoryPublisher",
    "TelethonPublisherBackend",
    "default_publisher_for_story",
    "OpenAIChatProvider",
    "ProviderResponse",
    "RewriteProvider",
    "StoryRewriter",
    "YandexGPTProvider",
    "default_rewriter",
]
