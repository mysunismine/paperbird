"""Сервис генерации рекомендованного промпта для изображений."""

from __future__ import annotations

from typing import Any

from projects.services.prompt_config import DEFAULT_IMAGE_PROMPT_TEMPLATE, ensure_prompt_config
from stories.paperbird_stories.models import Story
from stories.paperbird_stories.services import RewriteFailed, default_rewriter


class ImagePromptSuggestionFailed(RuntimeError):
    """Ошибка генерации рекомендованного промпта для изображения."""


def suggest_image_prompt(story: Story) -> str:
    """Запрашивает у модели рекомендованный промпт для изображения."""
    config = ensure_prompt_config(story.project)
    template = (config.image_prompt_template or "").strip() or DEFAULT_IMAGE_PROMPT_TEMPLATE
    prompt_text = _apply_replacements(template, _build_replacements(story))
    messages = [{"role": "system", "content": prompt_text}]
    try:
        provider = default_rewriter(project=story.project).provider
    except RewriteFailed as exc:
        raise ImagePromptSuggestionFailed(str(exc)) from exc
    try:
        response = provider.run(messages=messages)
    except RewriteFailed as exc:
        raise ImagePromptSuggestionFailed(str(exc)) from exc

    candidate = _extract_prompt(response.result)
    if not candidate:
        raise ImagePromptSuggestionFailed("Модель не вернула рекомендованный промпт.")
    return candidate


def _extract_prompt(result: Any) -> str:
    if isinstance(result, dict):
        value = result.get("prompt") or result.get("content") or ""
        return str(value).strip()
    if isinstance(result, str):
        return result.strip()
    return ""


def _apply_replacements(text: str, replacements: dict[str, str]) -> str:
    rendered = text or ""
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered.strip()


def _build_replacements(story: Story) -> dict[str, str]:
    project = story.project
    return {
        "{{PROJECT_NAME}}": project.name,
        "{{PROJECT_DESCRIPTION}}": (project.description or "").strip() or "Описание не указано.",
        "{{STORY_TITLE}}": (story.title or "").strip() or "Без названия",
        "{{STORY_SUMMARY}}": (story.summary or "").strip() or "Краткое описание отсутствует.",
        "{{STORY_BODY}}": (story.body or "").strip() or "Текст сюжета отсутствует.",
        "{{POSTS}}": _render_story_posts(story),
    }


def _render_story_posts(story: Story) -> str:
    posts = list(story.ordered_posts().select_related("source"))
    if not posts:
        return "Источники не найдены."
    blocks: list[str] = []
    for index, post in enumerate(posts, start=1):
        body = (post.message or "").strip() or "(пустой текст)"
        link = post.canonical_url or post.source_url or post.external_link or ""
        if link:
            blocks.append(f"НОВОСТЬ #{index}:\n{body}\nСсылка на источник: {link}")
        else:
            blocks.append(f"НОВОСТЬ #{index}:\n{body}")
    return "\n\n".join(blocks)
