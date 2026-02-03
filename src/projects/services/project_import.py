"""Project import helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from projects.models import Project, ProjectPromptConfig, Source, WebPreset
from projects.services.prompt_config import ensure_prompt_config
from projects.services.web_preset_registry import PresetValidationError, WebPresetRegistry


class ProjectImportError(RuntimeError):
    """Raised when project import payload is invalid."""


@dataclass(slots=True)
class ImportResult:
    project: Project
    sources_created: int
    presets_imported: int


PROJECT_FIELDS = (
    "description",
    "publish_target",
    "locale",
    "time_zone",
    "rewrite_model",
    "image_prompt_model",
    "image_model",
    "image_size",
    "image_quality",
    "retention_days",
    "collector_enabled",
    "collector_telegram_interval",
    "collector_web_interval",
    "is_active",
)

PROMPT_FIELDS = (
    "system_role",
    "task_instruction",
    "documents_intro",
    "style_requirements",
    "output_format",
    "output_example",
    "editor_comment_note",
    "image_prompt_template",
)


def import_project_payload(*, owner, payload: dict[str, Any]) -> ImportResult:
    """Imports project data and returns the created project."""

    if not isinstance(payload, dict):
        raise ProjectImportError("Некорректный формат файла проекта.")
    project_data = payload.get("project")
    if not isinstance(project_data, dict):
        raise ProjectImportError("В файле отсутствует блок project.")
    name = (project_data.get("name") or "").strip()
    if not name:
        raise ProjectImportError("Название проекта не указано.")

    project_name = _unique_project_name(owner=owner, base=name)
    project_kwargs: dict[str, Any] = {"owner": owner, "name": project_name}
    for field in PROJECT_FIELDS:
        if field in project_data:
            project_kwargs[field] = project_data.get(field)

    prompt_data = payload.get("prompt_config", {})
    if not isinstance(prompt_data, dict):
        prompt_data = {}

    presets_payload = payload.get("web_presets", [])
    if presets_payload is None:
        presets_payload = []
    if not isinstance(presets_payload, list):
        raise ProjectImportError("Некорректный формат web_presets.")

    sources_payload = payload.get("sources", [])
    if sources_payload is None:
        sources_payload = []
    if not isinstance(sources_payload, list):
        raise ProjectImportError("Некорректный формат sources.")

    registry = WebPresetRegistry()
    preset_map: dict[tuple[str, str], WebPreset] = {}

    with transaction.atomic():
        project = Project.objects.create(**project_kwargs)

        config = ensure_prompt_config(project)
        updated_fields: list[str] = []
        for field in PROMPT_FIELDS:
            if field in prompt_data:
                setattr(config, field, prompt_data.get(field) or "")
                updated_fields.append(field)
        if updated_fields:
            config.save(update_fields=updated_fields + ["updated_at"])

        presets_imported = 0
        for preset_payload in presets_payload:
            if not isinstance(preset_payload, dict):
                raise ProjectImportError("Некорректный формат пресета.")
            preset_config = preset_payload.get("config")
            if not isinstance(preset_config, dict):
                raise ProjectImportError("В пресете отсутствует config.")
            try:
                preset = registry.import_payload(
                    json.dumps(preset_config, ensure_ascii=False),
                    activate=True,
                )
            except PresetValidationError as exc:
                raise ProjectImportError(str(exc)) from exc
            preset_map[(preset.name, preset.version)] = preset
            presets_imported += 1

        sources_created = 0
        for source_payload in sources_payload:
            if not isinstance(source_payload, dict):
                raise ProjectImportError("Некорректный формат источника.")
            source_type = source_payload.get("type")
            if source_type not in Source.Type.values:
                raise ProjectImportError("Некорректный тип источника.")

            web_preset = None
            web_preset_snapshot = source_payload.get("web_preset_snapshot")
            if source_type == Source.Type.WEB:
                preset_ref = source_payload.get("web_preset") or {}
                if not isinstance(preset_ref, dict):
                    raise ProjectImportError("Некорректный формат web_preset.")
                preset_key = (preset_ref.get("name"), preset_ref.get("version"))
                if not all(preset_key):
                    raise ProjectImportError("Для веб-источника нужен web_preset.")
                web_preset = preset_map.get(preset_key)
                if not web_preset:
                    raise ProjectImportError("Не найден пресет для веб-источника.")
                if not isinstance(web_preset_snapshot, dict):
                    web_preset_snapshot = web_preset.config

            include_keywords = source_payload.get("include_keywords")
            exclude_keywords = source_payload.get("exclude_keywords")
            if not isinstance(include_keywords, list):
                include_keywords = []
            if not isinstance(exclude_keywords, list):
                exclude_keywords = []

            telegram_kind = source_payload.get("telegram_kind", Source.TelegramKind.UNKNOWN)
            if telegram_kind not in Source.TelegramKind.values:
                telegram_kind = Source.TelegramKind.UNKNOWN
            Source.objects.create(
                project=project,
                type=source_type,
                telegram_kind=telegram_kind,
                title=source_payload.get("title") or "",
                telegram_id=source_payload.get("telegram_id"),
                username=source_payload.get("username") or "",
                invite_link=source_payload.get("invite_link") or "",
                web_preset=web_preset,
                web_preset_snapshot=web_preset_snapshot or {},
                web_retry_max_attempts=source_payload.get("web_retry_max_attempts"),
                web_retry_base_delay=source_payload.get("web_retry_base_delay"),
                web_retry_max_delay=source_payload.get("web_retry_max_delay"),
                web_request_interval_sec=source_payload.get("web_request_interval_sec"),
                web_request_jitter_sec=source_payload.get("web_request_jitter_sec"),
                web_max_items_per_run=source_payload.get("web_max_items_per_run"),
                web_block_cooldown_sec=source_payload.get("web_block_cooldown_sec"),
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
                deduplicate_text=source_payload.get("deduplicate_text", True),
                deduplicate_media=source_payload.get("deduplicate_media", True),
                retention_days=source_payload.get("retention_days") or 7,
                is_active=source_payload.get("is_active", True),
            )
            sources_created += 1

    return ImportResult(
        project=project,
        sources_created=sources_created,
        presets_imported=presets_imported,
    )


def _unique_project_name(*, owner, base: str) -> str:
    """Ensures project name is unique for the owner."""

    if not Project.objects.filter(owner=owner, name=base).exists():
        return base
    suffix = " (импорт)"
    candidate = f"{base}{suffix}"
    counter = 2
    while Project.objects.filter(owner=owner, name=candidate).exists():
        candidate = f"{base}{suffix} {counter}"
        counter += 1
    return candidate
