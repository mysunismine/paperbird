"""Project export helpers."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from projects.models import Project, Source
from projects.services.prompt_config import ensure_prompt_config


def build_project_export(project: Project) -> dict[str, Any]:
    """Собирает данные проекта для экспорта без постов."""
    prompt_config = ensure_prompt_config(project)
    sources = (
        Source.objects.filter(project=project)
        .select_related("web_preset")
        .order_by("id")
    )
    web_presets: dict[tuple[str, str], dict[str, Any]] = {}
    source_payloads: list[dict[str, Any]] = []
    for source in sources:
        preset = source.web_preset
        if preset:
            key = (preset.name, preset.version)
            if key not in web_presets:
                web_presets[key] = {
                    "name": preset.name,
                    "version": preset.version,
                    "title": preset.title,
                    "description": preset.description,
                    "schema_version": preset.schema_version,
                    "status": preset.status,
                    "checksum": preset.checksum,
                    "config": preset.config,
                }
        source_payloads.append(
            {
                "type": source.type,
                "title": source.title,
                "telegram_id": source.telegram_id,
                "username": source.username,
                "invite_link": source.invite_link,
                "web_preset": (
                    {
                        "name": preset.name,
                        "version": preset.version,
                    }
                    if preset
                    else None
                ),
                "web_preset_snapshot": source.web_preset_snapshot,
                "web_retry_max_attempts": source.web_retry_max_attempts,
                "web_retry_base_delay": source.web_retry_base_delay,
                "web_retry_max_delay": source.web_retry_max_delay,
                "include_keywords": source.include_keywords,
                "exclude_keywords": source.exclude_keywords,
                "deduplicate_text": source.deduplicate_text,
                "deduplicate_media": source.deduplicate_media,
                "retention_days": source.retention_days,
                "is_active": source.is_active,
            }
        )

    return {
        "schema_version": 1,
        "exported_at": timezone.now().isoformat(),
        "project": {
            "name": project.name,
            "description": project.description,
            "publish_target": project.publish_target,
            "locale": project.locale,
            "time_zone": project.time_zone,
            "rewrite_model": project.rewrite_model,
            "image_model": project.image_model,
            "image_size": project.image_size,
            "image_quality": project.image_quality,
            "retention_days": project.retention_days,
            "collector_enabled": project.collector_enabled,
            "collector_telegram_interval": project.collector_telegram_interval,
            "collector_web_interval": project.collector_web_interval,
            "is_active": project.is_active,
        },
        "prompt_config": {
            "system_role": prompt_config.system_role,
            "task_instruction": prompt_config.task_instruction,
            "documents_intro": prompt_config.documents_intro,
            "style_requirements": prompt_config.style_requirements,
            "output_format": prompt_config.output_format,
            "output_example": prompt_config.output_example,
            "editor_comment_note": prompt_config.editor_comment_note,
            "image_prompt_template": prompt_config.image_prompt_template,
        },
        "sources": source_payloads,
        "web_presets": list(web_presets.values()),
    }
