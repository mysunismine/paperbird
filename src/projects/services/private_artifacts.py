"""Helpers for storing project artifacts in a private checkout."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils.text import slugify

from projects.models import Project
from projects.services.project_export import build_project_export
from projects.services.project_import import (
    ImportResult,
    ProjectImportError,
    import_project_payload,
)
from projects.services.prompt_config import render_prompt

try:  # pragma: no cover - optional dependency
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


@dataclass(slots=True)
class PrivateProjectArtifactExport:
    project_dir: Path
    project_export_file: Path
    prompt_export_file: Path
    preset_files: list[Path]


@dataclass(slots=True)
class PrivateProjectArtifactImport:
    source_dir: Path
    source_file: Path
    result: ImportResult


def export_project_artifacts_to_private_dir(
    *,
    project: Project,
    username: str,
    fmt: str = "json",
) -> PrivateProjectArtifactExport:
    """Export project payload, prompt text and web presets to private artifact directory."""
    payload = build_project_export(project)
    project_dir = _project_dir_for(username=username, project=project)
    project_dir.mkdir(parents=True, exist_ok=True)

    project_export_file = _write_project_export(
        project_dir=project_dir,
        payload=payload,
        fmt=fmt,
    )
    prompt_export_file = _write_prompt_export(project_dir=project_dir, project=project)
    preset_files = _write_preset_files(project_dir=project_dir, payload=payload)

    return PrivateProjectArtifactExport(
        project_dir=project_dir,
        project_export_file=project_export_file,
        prompt_export_file=prompt_export_file,
        preset_files=preset_files,
    )


def import_project_artifacts_from_private_dir(
    *,
    owner,
    source_dir: Path,
    source_file: str | None = None,
) -> PrivateProjectArtifactImport:
    """Import one project from a private artifact directory."""
    resolved_dir = source_dir.expanduser().resolve()
    if not resolved_dir.exists() or not resolved_dir.is_dir():
        raise ProjectImportError(f"Директория артефактов не найдена: {resolved_dir}")

    export_file = _resolve_project_export_file(source_dir=resolved_dir, source_file=source_file)
    payload = _parse_project_export_file(export_file)
    if not isinstance(payload, dict):
        raise ProjectImportError("Некорректный формат файла проекта.")

    result = import_project_payload(owner=owner, payload=payload)
    return PrivateProjectArtifactImport(
        source_dir=resolved_dir,
        source_file=export_file,
        result=result,
    )


def _project_dir_for(*, username: str, project: Project) -> Path:
    base_dir = Path(settings.PAPERBIRD_PRIVATE_ARTIFACTS_DIR)
    user_part = slugify(username) or "user"
    project_part = slugify(project.name) or f"project-{project.pk}"
    return base_dir / user_part / f"{project_part}-{project.pk}"


def private_user_artifacts_dir(*, username: str) -> Path:
    base_dir = Path(settings.PAPERBIRD_PRIVATE_ARTIFACTS_DIR)
    return base_dir / (slugify(username) or "user")


def _write_project_export(*, project_dir: Path, payload: dict[str, Any], fmt: str) -> Path:
    fmt_normalized = fmt.lower()
    if fmt_normalized in {"yaml", "yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML не установлен: экспорт в YAML недоступен.")
        target = project_dir / "project-export.yaml"
        content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)  # type: ignore[arg-type]
        target.write_text(content, encoding="utf-8")
        return target

    target = project_dir / "project-export.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    target.write_text(content, encoding="utf-8")
    return target


def _write_prompt_export(*, project_dir: Path, project: Project) -> Path:
    prompt = render_prompt(
        project=project,
        posts=[],
        preview_mode=True,
        editor_comment="",
    )
    target = project_dir / "prompt.txt"
    target.write_text(prompt.full_text, encoding="utf-8")
    return target


def _write_preset_files(*, project_dir: Path, payload: dict[str, Any]) -> list[Path]:
    presets_dir = project_dir / "web-presets"
    preset_files: list[Path] = []
    for preset in payload.get("web_presets", []):
        if not isinstance(preset, dict):
            continue
        name = slugify(str(preset.get("name") or "")) or "preset"
        version = _slug_version(str(preset.get("version") or ""))
        target = presets_dir / f"{name}-{version}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(preset.get("config", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        preset_files.append(target)
    return preset_files


def _slug_version(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "-", value).strip("-").lower()
    return normalized or "version"


def _resolve_project_export_file(*, source_dir: Path, source_file: str | None) -> Path:
    if source_file:
        candidate = (source_dir / source_file).resolve()
        if not candidate.exists() or not candidate.is_file():
            raise ProjectImportError(f"Файл экспорта не найден: {candidate}")
        return candidate

    json_file = source_dir / "project-export.json"
    if json_file.exists() and json_file.is_file():
        return json_file

    yaml_file = source_dir / "project-export.yaml"
    if yaml_file.exists() and yaml_file.is_file():
        return yaml_file

    yml_file = source_dir / "project-export.yml"
    if yml_file.exists() and yml_file.is_file():
        return yml_file

    raise ProjectImportError(
        "Не найден project-export.json/yaml в директории артефактов."
    )


def _parse_project_export_file(file_path: Path) -> dict[str, Any] | None:
    payload = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() == ".json":
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProjectImportError(f"Некорректный JSON: {exc}") from exc

    if file_path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise ProjectImportError("PyYAML не установлен: импорт YAML недоступен.")
        try:
            return yaml.safe_load(payload)  # type: ignore[no-any-return]
        except Exception as exc:  # pragma: no cover - library specific
            raise ProjectImportError(f"Некорректный YAML: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        if yaml is None:
            raise ProjectImportError("Формат файла не поддерживается без PyYAML.") from exc
        try:
            return yaml.safe_load(payload)  # type: ignore[no-any-return]
        except Exception as yaml_exc:  # pragma: no cover - library specific
            raise ProjectImportError(f"Некорректный файл экспорта: {exc}") from yaml_exc
