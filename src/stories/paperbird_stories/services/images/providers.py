"""Providers for image generation."""

from __future__ import annotations

import base64
import binascii
import json
import os
import socket
import time
import urllib.error
import urllib.request

from django.conf import settings

from stories.paperbird_stories.services.helpers import (
    build_yandex_model_uri,
    normalize_image_quality,
    normalize_image_size,
)
from stories.paperbird_stories.services.images.placeholders import (
    GeneratedImage,
    _placeholder_image_bytes,
)

from ..exceptions import ImageGenerationFailed


class ImageGenerationProvider:
    """Интерфейс генератора изображений."""

    def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImage:  # pragma: no cover - protocol stub
        raise NotImplementedError


class OpenAIImageProvider:
    """Генерирует изображения через OpenAI Images API."""

    def __init__(
        self,
        *,
        api_url: str | None = None,
        model: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.api_url = api_url or getattr(
            settings,
            "OPENAI_IMAGE_URL",
            "https://api.openai.com/v1/images/generations",
        )
        self.model = model or settings.OPENAI_IMAGE_MODEL or "dall-e-3"
        self.request_timeout = timeout or getattr(settings, "OPENAI_IMAGE_TIMEOUT", 60)

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImage:
        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            data = _placeholder_image_bytes(prompt or "placeholder")
            return GeneratedImage(data=data, mime_type="image/png")

        use_model = model or self.model
        use_quality = self._normalize_quality(quality)
        payload = {
            "model": use_model,
            "prompt": prompt,
            "size": normalize_image_size(size),
            "quality": use_quality,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.api_url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {api_key}")

        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                raw_body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:  # pragma: no cover - требует живого API
            message = exc.read().decode("utf-8", "replace")
            raise ImageGenerationFailed(f"OpenAI HTTP {exc.code}: {message}") from exc
        except TimeoutError as exc:  # pragma: no cover - сетевой таймаут
            raise ImageGenerationFailed(
                "OpenAI не ответил вовремя. Повторите попытку через пару секунд — "
                "генерация иногда занимает дольше."
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            if isinstance(getattr(exc, "reason", None), socket.timeout):
                raise ImageGenerationFailed(
                    "OpenAI не ответил вовремя. Повторите попытку через пару секунд — "
                    "генерация иногда занимает дольше."
                ) from exc
            raise ImageGenerationFailed(str(exc)) from exc
        except OSError as exc:  # pragma: no cover
            raise ImageGenerationFailed(str(exc)) from exc

        try:
            data = json.loads(raw_body)
            content = data["data"][0]
            b64 = content.get("b64_json") or content.get("content")
            if not b64:
                raise ValueError("Ответ OpenAI не содержит данных изображения")
            decoded = base64.b64decode(b64, validate=True)
        except (KeyError, IndexError, ValueError, binascii.Error) as exc:
            raise ImageGenerationFailed("Некорректный ответ OpenAI") from exc

        mime_type = content.get("mime_type") or content.get("mimeType") or "image/png"
        return GeneratedImage(data=decoded, mime_type=mime_type)

    @staticmethod
    def _normalize_quality(value: str | None) -> str:
        normalized = normalize_image_quality(value)
        mapping = {
            "standard": "medium",
            "hd": "high",
        }
        if normalized in mapping:
            return mapping[normalized]
        if normalized in {"low", "medium", "high", "auto"}:
            return normalized
        return "auto"


class YandexArtProvider:
    """Генерирует изображения через YandexART."""

    api_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
    status_url = "https://llm.api.cloud.yandex.net:443/operations/"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        folder_id: str | None = None,
        model: str | None = None,
        poll_interval: int | None = None,
        poll_timeout: int | float | None = None,
    ) -> None:
        self.api_key = api_key or getattr(settings, "YANDEX_API_KEY", "")
        self.folder_id = folder_id or getattr(settings, "YANDEX_FOLDER_ID", "")
        self.model = (model or getattr(settings, "YANDEX_IMAGE_MODEL", "yandex-art")).strip()
        self.poll_interval = poll_interval or int(
            getattr(settings, "YANDEX_IMAGE_POLL_INTERVAL", 3)
        )
        self.poll_timeout = poll_timeout or float(
            getattr(settings, "YANDEX_IMAGE_POLL_TIMEOUT", 90)
        )
        if not self.api_key:
            raise ImageGenerationFailed("YANDEX_API_KEY не задан")
        if not self.folder_id:
            raise ImageGenerationFailed("YANDEX_FOLDER_ID не задан")
        self.model_uri = build_yandex_model_uri(
            self.model,
            folder_id=self.folder_id,
            scheme="art",
        )

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImage:
        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")

        use_model = model or self.model
        use_size = normalize_image_size(size)

        request_body = {
            "modelUri": build_yandex_model_uri(
                use_model,
                folder_id=self.folder_id,
                scheme="art",
            ),
            "messages": [
                {"role": "system", "text": "You are helpful image generation model."},
                {"role": "user", "text": prompt},
            ],
            "generationOptions": {
                "mimeType": "image/png",
                "size": use_size,
            },
        }
        body = json.dumps(request_body).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={
                "Authorization": f"Api-Key {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.poll_timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover
            message = exc.read().decode("utf-8", "replace")
            raise ImageGenerationFailed(f"YandexART HTTP {exc.code}: {message}") from exc
        except OSError as exc:  # pragma: no cover
            raise ImageGenerationFailed(str(exc)) from exc

        operation_id = data.get("id")
        if not operation_id:
            raise ImageGenerationFailed("YandexART не вернул идентификатор операции")

        deadline = time.time() + self.poll_timeout
        status_url = f"{self.status_url}{operation_id}"
        while time.time() < deadline:
            request = urllib.request.Request(
                status_url,
                headers={"Authorization": f"Api-Key {self.api_key}"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.poll_timeout) as response:
                    body = response.read().decode("utf-8")
                    status_data = json.loads(body)
            except urllib.error.HTTPError as exc:  # pragma: no cover
                message = exc.read().decode("utf-8", "replace")
                raise ImageGenerationFailed(f"YandexART status HTTP {exc.code}: {message}") from exc
            except OSError as exc:  # pragma: no cover
                raise ImageGenerationFailed(str(exc)) from exc

            if status_data.get("done"):
                response_data = status_data.get("response")
                if not response_data:
                    error_details = status_data.get("error")
                    message = (
                        error_details.get("message")
                        if error_details
                        else "Неизвестная ошибка"
                    )
                    raise ImageGenerationFailed(f"YandexART: {message}")

                image_b64 = response_data.get("image")
                if not image_b64:
                    raise ImageGenerationFailed("YandexART не вернул изображение")

                try:
                    image_bytes = base64.b64decode(image_b64, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ImageGenerationFailed(
                        "Некорректные данные изображения от YandexART"
                    ) from exc
                if not image_bytes:
                    raise ImageGenerationFailed("Пустой ответ от YandexART")
                return GeneratedImage(data=image_bytes, mime_type="image/png")
            time.sleep(self.poll_interval)

        raise ImageGenerationFailed("YandexART не успел завершить генерацию")


class GeminiImageProvider:
    """Генерирует изображения через Gemini."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.api_key = (api_key or getattr(settings, "GEMINI_API_KEY", "")).strip()
        self.model = (
            model
            or getattr(settings, "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
        ).strip()
        self.timeout = timeout or getattr(settings, "GEMINI_TIMEOUT", 30)
        if not self.api_key:
            raise ImageGenerationFailed("GEMINI_API_KEY не задан")

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImage:
        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")

        use_model = (model or self.model).strip()
        use_aspect_ratio = (
            (aspect_ratio or getattr(settings, "GEMINI_IMAGE_ASPECT_RATIO", ""))
            .strip()
            .lower()
        )
        use_image_size = (
            (image_size or getattr(settings, "GEMINI_IMAGE_SIZE", ""))
            .strip()
            .upper()
        )

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ImageGenerationFailed(
                "The 'google-genai' package is not installed. "
                "Please run 'pip install google-genai'."
            ) from exc

        client = genai.Client(api_key=self.api_key)
        try:
            image_config = {}
            if use_aspect_ratio:
                image_config["aspect_ratio"] = use_aspect_ratio
            if use_image_size:
                image_config["image_size"] = use_image_size

            image_config_cls = getattr(types, "ImageConfig", None)
            if image_config and image_config_cls is not None:
                image_config_obj = image_config_cls(**image_config)
                config = types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=image_config_obj,
                )
            else:
                config = types.GenerateContentConfig(response_modalities=["IMAGE"])
            response = client.models.generate_content(
                model=use_model,
                contents=[prompt],
                config=config,
            )
        except Exception as exc:  # pragma: no cover
            raise ImageGenerationFailed(str(exc)) from exc

        parts = []
        response_parts = getattr(response, "parts", None)
        if response_parts:
            parts.extend(response_parts)
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if content and getattr(content, "parts", None):
                parts.extend(content.parts)

        def _extract_inline(part: object) -> tuple[object | None, str | None]:
            inline = None
            if isinstance(part, dict):
                inline = part.get("inline_data") or part.get("inlineData")
            else:
                inline = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
            if not inline:
                return None, None
            if isinstance(inline, dict):
                data = inline.get("data")
                mime = inline.get("mime_type") or inline.get("mimeType")
                return data, mime
            data = getattr(inline, "data", None)
            mime = getattr(inline, "mime_type", None) or getattr(inline, "mimeType", None)
            return data, mime

        for part in parts:
            data, mime_type = _extract_inline(part)
            if data is None:
                continue
            if isinstance(data, str):
                try:
                    image_bytes = base64.b64decode(data, validate=True)
                except binascii.Error as exc:
                    raise ImageGenerationFailed(
                        "Некорректные данные изображения"
                    ) from exc
            else:
                image_bytes = bytes(data)
            if not image_bytes:
                continue
            mime_type = mime_type or "image/png"
            return GeneratedImage(data=image_bytes, mime_type=mime_type)

        raise ImageGenerationFailed("Gemini не вернул изображение")
