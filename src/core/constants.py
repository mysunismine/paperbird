"""Общие константы, используемые в проекте Paperbird."""

from __future__ import annotations

DEFAULT_COLLECT_LIMIT = 100
REWRITE_MAX_ATTEMPTS = 3
OPENAI_DEFAULT_TEMPERATURE = 0.2
OPENAI_RESPONSE_FORMAT = {"type": "json_object"}

COLLECTOR_MAX_ATTEMPTS = 5
COLLECTOR_BASE_RETRY_DELAY = 30
COLLECTOR_MAX_RETRY_DELAY = 900
COLLECTOR_STALE_TIMEOUT = 600

PUBLISH_MAX_ATTEMPTS = 4
PUBLISH_BASE_RETRY_DELAY = 45
PUBLISH_MAX_RETRY_DELAY = 1800

IMAGE_MAX_ATTEMPTS = 3
IMAGE_BASE_RETRY_DELAY = 30
IMAGE_MAX_RETRY_DELAY = 900

REWRITE_BASE_RETRY_DELAY = 20
REWRITE_MAX_RETRY_DELAY = 600

REWRITE_DEFAULT_MAX_TOKENS = 1000

DEFAULT_QUEUE_MAX_ATTEMPTS = 5
DEFAULT_QUEUE_BASE_RETRY_DELAY = 10
DEFAULT_QUEUE_MAX_RETRY_DELAY = 3600
DEFAULT_QUEUE_STALE_TIMEOUT = 600

MAINTENANCE_MAX_ATTEMPTS = 3
MAINTENANCE_BASE_RETRY_DELAY = 60
MAINTENANCE_MAX_RETRY_DELAY = 3600

SOURCE_MAX_ATTEMPTS = 3
SOURCE_BASE_RETRY_DELAY = 120
SOURCE_MAX_RETRY_DELAY = 3600
COLLECTOR_WEB_STALE_TIMEOUT = 300

IMAGE_MODEL_CHOICES = (
    ("dall-e-3", "DALL-E 3"),
    ("gemini-2.5-flash-image", "Gemini 2.5 Flash Image"),
    ("gemini-3-pro-image-preview", "Gemini 3 Pro Image Preview"),
    ("yandex-art", "YandexART"),
)

GEMINI_IMAGE_ASPECT_RATIOS = (
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
)

GEMINI_IMAGE_SIZES = ("1K", "2K", "4K")

IMAGE_PROVIDER_SETTINGS = {
    "dall-e-3": {
        "sizes": ("1024x1024", "1792x1024", "1024x1792"),
        "qualities": ("low", "medium", "high", "auto"),
        "default_size": "1024x1024",
        "default_quality": "medium",
        "aspect_ratios": (),
        "image_sizes": (),
        "default_aspect_ratio": "",
        "default_image_size": "",
        "kind": "standard",
    },
    "gemini-2.5-flash-image": {
        "sizes": ("1024x1024",),
        "qualities": ("standard",),
        "default_size": "1024x1024",
        "default_quality": "standard",
        "aspect_ratios": GEMINI_IMAGE_ASPECT_RATIOS,
        "image_sizes": (),
        "default_aspect_ratio": "1:1",
        "default_image_size": "",
        "kind": "gemini",
    },
    "gemini-3-pro-image-preview": {
        "sizes": ("1024x1024",),
        "qualities": ("standard",),
        "default_size": "1024x1024",
        "default_quality": "standard",
        "aspect_ratios": GEMINI_IMAGE_ASPECT_RATIOS,
        "image_sizes": GEMINI_IMAGE_SIZES,
        "default_aspect_ratio": "1:1",
        "default_image_size": "1K",
        "kind": "gemini",
    },
    "yandex-art": {
        "sizes": ("1024x1024",),
        "qualities": ("standard",),
        "default_size": "1024x1024",
        "default_quality": "standard",
        "aspect_ratios": (),
        "image_sizes": (),
        "default_aspect_ratio": "",
        "default_image_size": "",
        "kind": "standard",
    },
    "default": {
        "sizes": ("1024x1024",),
        "qualities": ("standard",),
        "default_size": "1024x1024",
        "default_quality": "standard",
        "aspect_ratios": (),
        "image_sizes": (),
        "default_aspect_ratio": "",
        "default_image_size": "",
        "kind": "standard",
    },
}

# Generate flat choices for form validation, ensuring all options are present.
ALL_IMAGE_SIZES = sorted(list(set(
    size for config in IMAGE_PROVIDER_SETTINGS.values() for size in config["sizes"]
)))
ALL_IMAGE_QUALITIES = sorted(list(set(
    quality for config in IMAGE_PROVIDER_SETTINGS.values() for quality in config["qualities"]
)))

IMAGE_SIZE_CHOICES = tuple((s, s) for s in ALL_IMAGE_SIZES)
IMAGE_QUALITY_CHOICES = tuple((q, q) for q in ALL_IMAGE_QUALITIES)

IMAGE_DEFAULT_MODEL = "dall-e-3"
IMAGE_DEFAULT_SIZE = "1024x1024"
IMAGE_DEFAULT_QUALITY = "medium"

REWRITE_MODEL_CHOICES = (
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o", "gpt-4o"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
    ("gpt-5", "gpt-5"),
    ("gpt-5o", "gpt-5o"),
    ("gemini-1.5-flash", "gemini-1.5-flash"),
    ("yandexgpt-lite", "yandexgpt-lite"),
    ("yandexgpt", "yandexgpt"),
)
REWRITE_DEFAULT_MODEL = REWRITE_MODEL_CHOICES[0][0]
