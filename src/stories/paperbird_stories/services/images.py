"""Сервисы для генерации и прикрепления изображений сюжета."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings

from core.constants import (
    IMAGE_DEFAULT_MODEL,
    IMAGE_DEFAULT_QUALITY,
    IMAGE_DEFAULT_SIZE,
)

from .exceptions import ImageGenerationFailed
from .helpers import (
    _looks_like_yandex_art_model,
    build_yandex_model_uri,
    normalize_image_quality,
    normalize_image_size,
)


@dataclass(slots=True)
class GeneratedImage:
    """Результат генерации изображения."""

    data: bytes
    mime_type: str = "image/png"


class ImageGenerationProvider(Protocol):
    """Интерфейс генератора изображений."""

    def generate(self, *, prompt: str) -> GeneratedImage:  # pragma: no cover - protocol
        ...


class OpenAIImageProvider:
    """Генерирует изображения через OpenAI Images API."""

    def __init__(
        self,
        *,
        api_url: str | None = None,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        response_format: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.api_url = api_url or os.getenv(
            "OPENAI_IMAGE_URL", "https://api.openai.com/v1/images/generations"
        )
        self.model = model or os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
        self.size = normalize_image_size(
            size or os.getenv("OPENAI_IMAGE_SIZE", IMAGE_DEFAULT_SIZE)
        )
        self.quality = normalize_image_quality(
            quality or os.getenv("OPENAI_IMAGE_QUALITY", IMAGE_DEFAULT_QUALITY)
        )
        if response_format is None:
            response_format = os.getenv("OPENAI_IMAGE_RESPONSE_FORMAT", "b64_json")
        self.response_format = (response_format or "").strip()
        self.request_timeout = timeout or getattr(settings, "OPENAI_IMAGE_TIMEOUT", 60)

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        _allow_without_format: bool = False,
    ) -> GeneratedImage:
        """Генерирует изображение."""
        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            data = _placeholder_image_bytes(prompt or "placeholder")
            return GeneratedImage(data=data, mime_type="image/png")

        use_model = model or self.model
        use_size = normalize_image_size(size or self.size)
        use_quality = normalize_image_quality(quality or self.quality)
        payload = {
            "model": use_model,
            "prompt": prompt,
            "size": use_size,
            "quality": use_quality,
        }
        if self.response_format and not _allow_without_format:
            payload["response_format"] = self.response_format
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.api_url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {api_key}")

        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                raw_body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:  # pragma: no cover - требует живого API
            message = exc.read().decode("utf-8", "replace")
            if (
                not _allow_without_format
                and self.response_format
                and exc.code == 400
                and "response_format" in message.lower()
            ):
                return self.generate(
                    prompt=prompt,
                    model=model,
                    size=size,
                    quality=quality,
                    _allow_without_format=True,
                )
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


class YandexArtProvider:
    """Генерирует изображения через YandexART."""

    api_url = "https://llm.api.cloud.yandex.net/foundationModels/v2/imageGenerationAsync"
    status_url = "https://llm.api.cloud.yandex.net:443/operations/"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        folder_id: str | None = None,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        poll_interval: int = 3,
        poll_timeout: int = 60,
    ) -> None:
        self.api_key = api_key or getattr(settings, "YANDEX_API_KEY", "")
        self.folder_id = folder_id or getattr(settings, "YANDEX_FOLDER_ID", "")
        self.model = (
            model or getattr(settings, "OPENAI_IMAGE_MODEL", IMAGE_DEFAULT_MODEL)
        ).strip()
        self.size = normalize_image_size(
            size or getattr(settings, "OPENAI_IMAGE_SIZE", IMAGE_DEFAULT_SIZE)
        )
        self.quality = normalize_image_quality(
            quality or getattr(settings, "OPENAI_IMAGE_QUALITY", IMAGE_DEFAULT_QUALITY)
        )
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
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
        _allow_without_format: bool = False,
    ) -> GeneratedImage:
        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")

        use_model = model or self.model
        use_size = normalize_image_size(size or self.size)
        use_quality = normalize_image_quality(quality or self.quality)

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
                "quality": use_quality,
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
                results = (
                    status_data.get("response", {})
                    .get("image")
                    .get("images")
                    or []
                )
                if not results:
                    raise ImageGenerationFailed("YandexART не вернул изображение")
                image_info = results[0].get("image") or results[0]
                encoded = (
                    image_info.get("imageBase64")
                    or image_info.get("base64")
                    or image_info.get("data")
                )
                if not encoded:
                    raise ImageGenerationFailed("Некорректный ответ YandexART")
                mime_type = image_info.get("mimeType", "image/png") or "image/png"
                try:
                    image_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ImageGenerationFailed(
                        "Некорректные данные изображения от YandexART"
                    ) from exc
                if not image_bytes:
                    raise ImageGenerationFailed("Пустой ответ от YandexART")
                return GeneratedImage(data=image_bytes, mime_type=mime_type)
            time.sleep(self.poll_interval)

        raise ImageGenerationFailed("YandexART не успел завершить генерацию")

    @staticmethod
    def _aspect_ratio(size: str) -> float:
        width, height = size.split("x")
        width_value = int(width)
        height_value = int(height)
        return width_value / height_value


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


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """Создает PNG-чанк."""
    return (
        struct.pack("!I", len(data))
        + tag
        + data
        + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _placeholder_image_bytes(prompt: str) -> bytes:
    """Генерирует байты изображения-заглушки."""
    width = height = 320
    digest = hashlib.sha256(prompt.encode("utf-8", "ignore")).digest()
    color = digest[0], digest[8], digest[16]
    pixel = bytes([color[0], color[1], color[2], 255])
    rows = []
    for _ in range(height):
        rows.append(b"\x00" + pixel * width)
    raw = b"".join(rows)
    header = struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0)
    compressed = zlib.compress(raw, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
