"""Функции подготовки промптов для рерайта."""

from __future__ import annotations

from collections.abc import Sequence

from projects.models import Post
from projects.services.prompt_config import render_prompt
from stories.paperbird_stories.models import RewritePreset, Story

from .exceptions import RewriteFailed


def build_prompt(
    *,
    posts: Sequence[Post],
    editor_comment: str,
    title: str,
    preset_instruction: str = "",
) -> list[dict[str, str]]:
    """Формирует сообщения для LLM, используя шаблон проекта."""

    if not posts:
        raise ValueError("Невозможно сформировать промт без постов")
    project = posts[0].project
    rendered = render_prompt(
        project=project,
        posts=posts,
        title=title,
        editor_comment=editor_comment,
        preset_instruction=preset_instruction,
    )
    return [
        {"role": "system", "content": rendered.system_message},
        {"role": "user", "content": rendered.user_message},
    ]


def make_prompt_messages(
    story: Story,
    *,
    editor_comment: str | None = None,
    preset: RewritePreset | None = None,
) -> tuple[list[dict[str, str]], str]:
    """Собирает промпт для сюжета и возвращает сообщения и комментарий пользователя."""

    posts = list(story.ordered_posts())
    if not posts:
        raise RewriteFailed("Сюжет не содержит постов для рерайта")

    user_comment = editor_comment if editor_comment is not None else story.editor_comment
    user_comment = user_comment.strip() if user_comment else ""
    preset_comment = preset.editor_comment.strip() if preset and preset.editor_comment else ""
    if preset_comment and user_comment:
        combined_comment = (
            f"{preset_comment}\n\n"
            "Дополнительные указания редактора:\n"
            f"{user_comment}"
        )
    else:
        combined_comment = preset_comment or user_comment

    messages = build_prompt(
        posts=posts,
        editor_comment=combined_comment,
        title=story.title,
        preset_instruction=preset.instruction_block() if preset else "",
    )
    return messages, user_comment
