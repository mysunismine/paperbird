"""Реестр и вспомогательные функции валидации для пресетов веб-сборщика."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - optional dependency guard
    from jsonschema import Draft202012Validator, ValidationError as JSONSchemaError  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    Draft202012Validator = None  # type: ignore[assignment]
    JSONSchemaError = Exception  # type: ignore[assignment]

from django.utils import timezone

from projects.models import Source, WebPreset
from projects.schemas import load_web_preset_schema

logger = logging.getLogger(__name__)


class PresetValidationError(RuntimeError):
    """Вызывается, когда полезная нагрузка пресета не является валидным JSON или нарушает схему."""


@dataclass(slots=True)
class PresetMetadata:
    """Нормализованные метаданные, извлеченные из полезной нагрузки пресета."""

    name: str
    version: str
    schema_version: int
    checksum: str


class WebPresetValidator:
    """Валидирует полезные нагрузки пресетов с использованием JSON-схемы."""

    def __init__(self) -> None:
        schema = load_web_preset_schema()
        if Draft202012Validator is None:  # pragma: no cover - defensive
            raise RuntimeError(
                "jsonschema не установлен. Выполните `pip install -r requirements.txt`."
            )
        self._validator = Draft202012Validator(schema)

    def validate(self, payload: dict[str, Any]) -> PresetMetadata:
        """Валидирует полезную нагрузку и возвращает нормализованные метаданные."""

        try:
            self._validator.validate(payload)
        except JSONSchemaError as exc:  # pragma: no cover - exercised via tests
            raise PresetValidationError(str(exc)) from exc
        checksum = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        schema_version = int(payload.get("schema_version") or 1)
        return PresetMetadata(
            name=payload["name"],
            version=payload["version"],
            schema_version=schema_version,
            checksum=checksum,
        )


class WebPresetRegistry:
    """Хранит пресеты, обеспечивает валидацию схемы и переключает статус."""

    def __init__(self, validator: WebPresetValidator | None = None) -> None:
        self.validator = validator or WebPresetValidator()

    def import_payload(
        self,
        payload: str | bytes,
        *,
        activate: bool = True,
    ) -> WebPreset:
        """Парсит JSON-полезную нагрузку, валидирует и сохраняет пресет."""

        data = self._parse(payload)
        meta = self.validator.validate(data)
        defaults = {
            "schema_version": meta.schema_version,
            "checksum": meta.checksum,
            "config": data,
            "title": data.get("title", ""),
            "description": data.get("description", ""),
        }
        status = WebPreset.Status.ACTIVE if activate else WebPreset.Status.DRAFT
        existing = (
            WebPreset.objects.filter(name=meta.name, version=meta.version)
            .only("checksum")
            .first()
        )
        preset, created = WebPreset.objects.update_or_create(
            name=meta.name,
            version=meta.version,
            defaults={**defaults, "status": status},
        )
        previous_checksum = existing.checksum if existing else None
        config_changed = created or (previous_checksum != meta.checksum)
        if config_changed and activate:
            self._refresh_source_snapshots(preset=preset, snapshot=data)
        return preset

    @staticmethod
    def _parse(payload: str | bytes) -> dict[str, Any]:
        """Парсит JSON-полезную нагрузку."""
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            return json.loads(payload)
        except json.JSONDecodeError as exc:  # pragma: no cover - raised in form tests
            raise PresetValidationError(f"Некорректный JSON: {exc}") from exc

    def _refresh_source_snapshots(self, *, preset: WebPreset, snapshot: dict[str, Any]) -> None:
        """Обновляет снимки для всех источников, связанных с пресетом."""

        sources = list(Source.objects.filter(web_preset=preset).only("pk"))
        if not sources:
            return
        now = timezone.now()
        for source in sources:
            Source.objects.filter(pk=source.pk).update(
                web_preset_snapshot=snapshot,
                updated_at=now,
            )
        logger.info(
            "web_preset_snapshots_refreshed preset=%s sources=%s",
            preset.pk,
            len(sources),
        )


__all__ = ["PresetValidationError", "PresetMetadata", "WebPresetRegistry", "WebPresetValidator"]
