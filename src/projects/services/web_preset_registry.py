"""Registry and validation helpers for web collector presets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - optional dependency guard
    from jsonschema import Draft202012Validator, ValidationError as JSONSchemaError  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    Draft202012Validator = None  # type: ignore[assignment]
    JSONSchemaError = Exception  # type: ignore[assignment]

from projects.models import WebPreset
from projects.schemas import load_web_preset_schema


class PresetValidationError(RuntimeError):
    """Raised when preset payload is not valid JSON or violates schema."""


@dataclass(slots=True)
class PresetMetadata:
    """Normalized metadata extracted from a preset payload."""

    name: str
    version: str
    schema_version: int
    checksum: str


class WebPresetValidator:
    """Validates preset payloads using JSON Schema."""

    def __init__(self) -> None:
        schema = load_web_preset_schema()
        if Draft202012Validator is None:  # pragma: no cover - defensive
            raise RuntimeError("jsonschema не установлен. Выполните `pip install -r requirements.txt`.")
        self._validator = Draft202012Validator(schema)

    def validate(self, payload: dict[str, Any]) -> PresetMetadata:
        """Validate payload and return normalized metadata."""

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
    """Stores presets, enforces schema validation, and toggles status."""

    def __init__(self, validator: WebPresetValidator | None = None) -> None:
        self.validator = validator or WebPresetValidator()

    def import_payload(
        self,
        payload: str | bytes,
        *,
        activate: bool = True,
    ) -> WebPreset:
        """Parse JSON payload, validate, and persist preset."""

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
        preset, created = WebPreset.objects.update_or_create(
            name=meta.name,
            version=meta.version,
            defaults={**defaults, "status": status},
        )
        if not created:
            needs_status_update = activate and preset.status != WebPreset.Status.ACTIVE
            changes = []
            for field, value in defaults.items():
                if getattr(preset, field) != value:
                    setattr(preset, field, value)
                    changes.append(field)
            if needs_status_update:
                preset.status = WebPreset.Status.ACTIVE
                changes.append("status")
            if changes:
                preset.save(update_fields=[*changes, "updated_at"])
        return preset

    @staticmethod
    def _parse(payload: str | bytes) -> dict[str, Any]:
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            return json.loads(payload)
        except json.JSONDecodeError as exc:  # pragma: no cover - raised in form tests
            raise PresetValidationError(f"Некорректный JSON: {exc}") from exc


__all__ = ["PresetValidationError", "PresetMetadata", "WebPresetRegistry", "WebPresetValidator"]
