"""Регистрация моделей воркера в админ-панели."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from django import forms
from django.conf import settings
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from stories.paperbird_stories.services.exceptions import ImageGenerationFailed
from stories.paperbird_stories.services.images.providers import (
    GeminiImageProvider,
    OpenAIImageProvider,
    YandexArtProvider,
)
from core.constants import OPENAI_MODEL_ALIASES
from stories.paperbird_stories.services.rewrite import (
    GeminiChatProvider,
    OpenAIChatProvider,
    RewriteFailed,
    YandexGPTProvider,
)

from .models import WorkerTask, WorkerTaskAttempt


@admin.register(WorkerTask)
class WorkerTaskAdmin(admin.ModelAdmin):
    """Настройки админ-панели для фоновых задач."""

    list_display = (
        "id",
        "queue",
        "status",
        "priority",
        "attempts",
        "max_attempts",
        "available_at",
        "locked_by",
        "last_error_code",
    )
    list_filter = ("queue", "status")
    search_fields = ("id", "queue", "locked_by", "last_error_code")
    ordering = ("queue", "priority", "available_at")
    readonly_fields = (
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "locked_at",
        "payload",
        "result",
        "last_error_payload",
    )
    fieldsets = (
        (None, {"fields": ("queue", "status", "priority", "payload", "result")}),
        (
            "Execution",
            {
                "fields": (
                    "attempts",
                    "max_attempts",
                    "available_at",
                    "locked_by",
                    "locked_at",
                    "started_at",
                    "finished_at",
                )
            },
        ),
        (
            "Retry policy",
            {
                "fields": (
                    "base_retry_delay",
                    "max_retry_delay",
                )
            },
        ),
        (
            "Error",
            {
                "fields": (
                    "last_error_code",
                    "last_error_message",
                    "last_error_payload",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(WorkerTaskAttempt)
class WorkerTaskAttemptAdmin(admin.ModelAdmin):
    """Настройки админ-панели для попыток задач."""

    list_display = (
        "id",
        "task",
        "attempt_number",
        "status",
        "duration_ms",
        "will_retry",
        "available_at",
        "created_at",
    )
    list_filter = ("status", "will_retry")
    search_fields = ("task__id", "error_code", "error_message")
    ordering = ("-created_at",)
    readonly_fields = (
        "task",
        "attempt_number",
        "status",
        "error_code",
        "error_message",
        "error_payload",
        "duration_ms",
        "will_retry",
        "available_at",
        "created_at",
    )


@dataclass(frozen=True)
class ModelProvider:
    key: str
    label: str
    kind: str


MODEL_PROVIDERS = (
    ModelProvider("openai_chat", "OpenAI Chat", "llm"),
    ModelProvider("gemini_chat", "Gemini", "llm"),
    ModelProvider("yandex_gpt", "YandexGPT", "llm"),
    ModelProvider("openai_image", "OpenAI Images", "image"),
    ModelProvider("gemini_image", "Gemini Images", "image"),
    ModelProvider("yandex_art", "YandexART", "image"),
)

DEFAULT_PROMPTS = {
    "llm": 'Верни JSON {"ok": true}.',
    "image": "Белый квадрат на светлом фоне.",
}


class ModelSandboxForm(forms.Form):
    provider = forms.ChoiceField(
        choices=[(provider.key, provider.label) for provider in MODEL_PROVIDERS],
        label="Провайдер",
    )
    prompt = forms.CharField(
        label="Промпт",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "Если оставить пустым, будет использован "
            "тестовый промпт."
        ),
    )


def _mask_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "—"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _build_messages(prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": prompt},
    ]


def _format_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

def _normalize_openai_model(model: str) -> tuple[str, str | None]:
    cleaned = (model or "").strip()
    normalized = OPENAI_MODEL_ALIASES.get(cleaned, cleaned)
    if normalized != cleaned:
        return normalized, cleaned
    return cleaned, None


def _model_settings() -> list[dict[str, object]]:
    openai_model, openai_alias_from = _normalize_openai_model(
        getattr(settings, "OPENAI_MODEL", "")
    )
    openai_extra = getattr(settings, "OPENAI_BASE_URL", "") or "—"
    if openai_alias_from:
        if openai_extra == "—":
            openai_extra = f"alias from {openai_alias_from}"
        else:
            openai_extra = f"{openai_extra} (alias from {openai_alias_from})"
    return [
        {
            "section": "LLM",
            "rows": [
                {
                    "name": "OpenAI",
                    "model": openai_model,
                    "key": _mask_secret(getattr(settings, "OPENAI_API_KEY", "")),
                    "extra": openai_extra,
                },
                {
                    "name": "Gemini",
                    "model": getattr(settings, "GEMINI_MODEL", ""),
                    "key": _mask_secret(getattr(settings, "GEMINI_API_KEY", "")),
                    "extra": "—",
                },
                {
                    "name": "YandexGPT",
                    "model": "yandexgpt-lite",
                    "key": _mask_secret(getattr(settings, "YANDEX_API_KEY", "")),
                    "extra": getattr(settings, "YANDEX_FOLDER_ID", "") or "—",
                },
            ],
        },
        {
            "section": "Images",
            "rows": [
                {
                    "name": "OpenAI Images",
                    "model": getattr(settings, "OPENAI_IMAGE_MODEL", ""),
                    "key": _mask_secret(getattr(settings, "OPENAI_API_KEY", "")),
                    "extra": (
                        f"{getattr(settings, 'OPENAI_IMAGE_SIZE', '')} / "
                        f"{getattr(settings, 'OPENAI_IMAGE_QUALITY', '')}"
                    ),
                },
                {
                    "name": "Gemini Images",
                    "model": getattr(settings, "GEMINI_IMAGE_MODEL", ""),
                    "key": _mask_secret(getattr(settings, "GEMINI_API_KEY", "")),
                    "extra": (
                        f"{getattr(settings, 'GEMINI_IMAGE_ASPECT_RATIO', '') or '—'} / "
                        f"{getattr(settings, 'GEMINI_IMAGE_SIZE', '') or '—'}"
                    ),
                },
                {
                    "name": "YandexART",
                    "model": getattr(settings, "YANDEX_IMAGE_MODEL", ""),
                    "key": _mask_secret(getattr(settings, "YANDEX_API_KEY", "")),
                    "extra": (
                        f"{getattr(settings, 'YANDEX_IMAGE_SIZE', '')} / "
                        f"{getattr(settings, 'YANDEX_IMAGE_QUALITY', '')} / "
                        f"{getattr(settings, 'YANDEX_FOLDER_ID', '') or '—'}"
                    ),
                },
            ],
        },
    ]


def models_sandbox_view(request):
    form = ModelSandboxForm(request.POST or None)
    result = None
    error = None
    history = request.session.get("models_sandbox_history", [])

    if request.method == "POST" and form.is_valid():
        provider_key = form.cleaned_data["provider"]
        provider_lookup = {item.key: item for item in MODEL_PROVIDERS}
        provider = provider_lookup.get(provider_key)
        if not provider:
            error = "Неизвестный провайдер."
        else:
            prompt = form.cleaned_data["prompt"].strip() or DEFAULT_PROMPTS[provider.kind]
            started_at = time.monotonic()
            try:
                if provider.key == "openai_chat":
                    response = OpenAIChatProvider().run(messages=_build_messages(prompt))
                    payload = response.result
                    raw_payload = response.raw
                elif provider.key == "gemini_chat":
                    response = GeminiChatProvider().run(messages=_build_messages(prompt))
                    payload = response.result
                    raw_payload = response.raw
                elif provider.key == "yandex_gpt":
                    response = YandexGPTProvider().run(messages=_build_messages(prompt))
                    payload = response.result
                    raw_payload = response.raw
                elif provider.key == "openai_image":
                    if not getattr(settings, "OPENAI_API_KEY", "").strip():
                        raise ImageGenerationFailed("OPENAI_API_KEY не задан")
                    image = OpenAIImageProvider().generate(
                        prompt=prompt,
                        size=getattr(settings, "OPENAI_IMAGE_SIZE", None),
                        quality=getattr(settings, "OPENAI_IMAGE_QUALITY", None),
                    )
                    payload = {
                        "mime_type": image.mime_type,
                        "bytes": len(image.data),
                    }
                    raw_payload = payload
                elif provider.key == "gemini_image":
                    image = GeminiImageProvider().generate(prompt=prompt)
                    payload = {
                        "mime_type": image.mime_type,
                        "bytes": len(image.data),
                    }
                    raw_payload = payload
                elif provider.key == "yandex_art":
                    image = YandexArtProvider().generate(
                        prompt=prompt,
                        size=getattr(settings, "YANDEX_IMAGE_SIZE", None),
                        quality=getattr(settings, "YANDEX_IMAGE_QUALITY", None),
                    )
                    payload = {
                        "mime_type": image.mime_type,
                        "bytes": len(image.data),
                    }
                    raw_payload = payload
                else:
                    raise ValueError("Неизвестный провайдер.")

                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                image_preview = None
                if provider.kind == "image":
                    image_preview = (
                        base64.b64encode(image.data).decode("ascii")
                        if image.data
                        else None
                    )
                result = {
                    "label": provider.label,
                    "elapsed_ms": elapsed_ms,
                    "payload": _format_json(payload),
                    "raw": _format_json(raw_payload),
                    "image_preview": image_preview,
                    "image_mime": image.mime_type if provider.kind == "image" else None,
                }
                history_entry = {
                    "timestamp": timezone.localtime().strftime("%H:%M:%S"),
                    "provider": provider.label,
                    "status": "success",
                    "elapsed_ms": elapsed_ms,
                }
            except (RewriteFailed, ImageGenerationFailed, ValueError) as exc:
                error = str(exc)
                history_entry = {
                    "timestamp": timezone.localtime().strftime("%H:%M:%S"),
                    "provider": provider.label,
                    "status": "error",
                    "message": error,
                }
            history = [history_entry, *history][:5]
            request.session["models_sandbox_history"] = history

    context = {
        **admin.site.each_context(request),
        "title": "Модели",
        "form": form,
        "result": result,
        "error": error,
        "config_sections": _model_settings(),
        "providers": MODEL_PROVIDERS,
        "history": history,
    }
    return TemplateResponse(request, "admin/models_sandbox.html", context)


def _register_models_sandbox():
    original_get_urls = admin.site.get_urls
    original_get_app_list = admin.site.get_app_list

    def get_urls():
        urls = original_get_urls()
        custom_urls = [
            path(
                "models/",
                admin.site.admin_view(models_sandbox_view),
                name="models-sandbox",
            ),
        ]
        return custom_urls + urls

    def get_app_list(request, app_label=None):
        app_list = list(original_get_app_list(request, app_label))
        if app_label:
            return app_list
        sandbox_url = reverse("admin:models-sandbox")
        app_list.insert(
            0,
            {
                "name": "Models",
                "app_label": "models",
                "app_url": sandbox_url,
                "has_module_perms": True,
                "models": [
                    {
                        "name": "Модели",
                        "object_name": "ModelsSandbox",
                        "perms": {"view": True},
                        "admin_url": sandbox_url,
                        "add_url": None,
                        "view_only": False,
                    }
                ],
            },
        )
        return app_list

    admin.site.get_urls = get_urls
    admin.site.get_app_list = get_app_list


_register_models_sandbox()
