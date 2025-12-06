"""Вспомогательные утилиты для настройки и рендеринга промтов проекта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from projects.models import Post, Project, ProjectPromptConfig
from projects.services.time_preferences import build_project_datetime_context

PROMPT_SECTION_ORDER: list[tuple[str, str]] = [
    ("system_role", "1. [СИСТЕМНАЯ РОЛЬ]"),
    ("task_instruction", "2. [ЗАДАНИЕ]"),
    ("documents_intro", "3. [ИСТОЧНИКИ / ДОКУМЕНТЫ]"),
    ("style_requirements", "4. [ТРЕБОВАНИЯ К СТИЛЮ]"),
    ("output_format", "5. [ФОРМАТ ОТВЕТА — JSON]"),
    ("output_example", "6. [ПРИМЕР КОРРЕКТНОГО ВЫВОДА]"),
    ("editor_comment_note", "7. [КОММЕНТАРИЙ РЕДАКТОРА]"),
]

PROMPT_SECTION_HINTS: dict[str, str] = {
    "system_role": "Опишите роль модели. Используйте {{PROJECT_NAME}} для подпстановки названия.",
    "task_instruction": "Формулируйте задачу и ожидаемые действия модели.",
    "documents_intro": "Расскажите, как работать с источниками. Токен {{POSTS}} заменится на список новостей.",
    "style_requirements": "Дайте тон, требования к языку и форматированию.",
    "output_format": "Опишите JSON схему. Можно добавить блоки ``` для удобства.",
    "output_example": "Приведите пример корректного JSON ответа.",
    "editor_comment_note": "Используйте {{EDITOR_COMMENT}} для подстановки комментария редактора.",
}

PROMPT_TEMPLATE_TOKENS: dict[str, str] = {
    "{{PROJECT_NAME}}": "Название проекта",
    "{{PROJECT_DESCRIPTION}}": "Описание проекта",
    "{{POSTS}}": "Список новостей вида «НОВОСТЬ #1: ...»",
    "{{TITLE}}": "Заголовок сюжета",
    "{{EDITOR_COMMENT}}": "Комментарий редактора (или заглушка, если он пустой)",
    "{{CURRENT_DATETIME}}": "Текущая дата и время проекта в локальной зоне",
    "{{CURRENT_UTC_OFFSET}}": "Смещение относительно UTC (например, UTC+03:00)",
    "{{CURRENT_TIMEZONE}}": "Название выбранного часового пояса",
    "{{CURRENT_ISO_DATETIME}}": "Дата и время в ISO 8601 (UTC с учётом смещения)",
}

DEFAULT_PROMPT_SECTIONS: dict[str, str] = {
    "system_role": "Ты — редактор новостного Telegram-канала на тему: {{PROJECT_NAME}}.",
    "task_instruction": (
        "Твоя задача — переписать предоставленные новости в указанном стиле, сохраняя смысл и факты.\n"
        "Если есть несколько новостей, объедини их логично и последовательно. Используй только самые важные факты.\n"
        "Текущий заголовок: {{TITLE}}."
    ),
    "documents_intro": (
        "Тебе даны следующие источники:\n"
        "{{POSTS}}\n"
        "Текущая дата проекта: {{CURRENT_DATETIME}} ({{CURRENT_UTC_OFFSET}} / {{CURRENT_TIMEZONE}}).\n"
        "Если какой‑то источник пустой или повторяется, просто пропусти его."
    ),
    "style_requirements": (
        "- Формат для Telegram: короткие абзацы и простые фразы.\n"
        "- Возможен лёгкий юмор или ирония, если это уместно.\n"
        "- Делай заголовки выразительными.\n"
        "- При необходимости добавь контекст.\n"
        "- Используй эмодзи только если редактор это допускает."
    ),
    "output_format": (
        "Ответ строго в формате JSON:\n"
        "```json\n"
        "{\n"
        '  "title": "Краткий, выразительный заголовок",\n'
        '  "summary": "Короткое резюме одной фразой (до 150 символов)",\n'
        '  "content": "Основной текст для публикации в Telegram",\n'
        '  "hashtags": "#пример #новости #технологии",\n'
        '  "sources": ["https://t.me/source/123", "https://t.me/source/456"]\n'
        "}\n"
        "```\n"
        "Если невозможно соблюсти формат — всё равно верни JSON с пустыми строками вместо отсутствующих полей.\n"
        "Не добавляй ничего за пределами JSON."
    ),
    "output_example": (
        "```json\n"
        "{\n"
        '  "title": "Учёные нашли способ обучать ИИ быстрее",\n'
        '  "summary": "Исследователи предложили новый метод обучения, ускоряющий обработку данных.",\n'
        '  "content": "Инженеры из MIT разработали алгоритм, который сокращает время обучения моделей на 40%. ...",\n'
        '  "hashtags": "#ИИ #наука #технологии",\n'
        '  "sources": ["https://t.me/source/123"]\n'
        "}\n"
        "```"
    ),
    "editor_comment_note": (
        "{{EDITOR_COMMENT}}\n"
        "Если редактор ничего не указал — продолжай без дополнительных замечаний."
    ),
}


@dataclass(slots=True)
class RenderedPrompt:
    """Результат рендеринга фрагментов промта."""

    sections: list[tuple[str, str]]

    @property
    def system_message(self) -> str:
        return self.sections[0][1]

    @property
    def user_message(self) -> str:
        user_sections = [text for _, text in self.sections[1:]]
        return "\n\n".join(user_sections)

    @property
    def full_text(self) -> str:
        return "\n\n".join(text for _, text in self.sections)


def default_prompt_payload() -> dict[str, str]:
    """Возвращает копию секций промта по умолчанию."""

    return DEFAULT_PROMPT_SECTIONS.copy()


def ensure_prompt_config(project: Project) -> ProjectPromptConfig:
    """Убеждается, что конфигурация промта существует для проекта."""

    config, _ = ProjectPromptConfig.objects.get_or_create(
        project=project,
        defaults=default_prompt_payload(),
    )
    return config


def render_prompt(
    *,
    project: Project,
    posts: Sequence[Post] | None,
    title: str = "",
    editor_comment: str = "",
    preset_instruction: str = "",
    preview_mode: bool = False,
) -> RenderedPrompt:
    """Рендерит секции промта с заменами."""

    config = ensure_prompt_config(project)
    datetime_context = build_project_datetime_context(project)
    replacements = _build_replacements(
        project=project,
        posts=posts or [],
        title=title,
        editor_comment=editor_comment,
        preset_instruction=preset_instruction,
        preview_mode=preview_mode,
        datetime_context=datetime_context,
    )
    sections: list[tuple[str, str]] = []
    for field, heading in PROMPT_SECTION_ORDER:
        raw = getattr(config, field)
        text = heading + "\n" + _apply_replacements(raw, replacements)
        sections.append((field, text.strip()))
    sections.append(("current_datetime", _render_current_datetime_section(datetime_context)))
    return RenderedPrompt(sections=sections)


def _build_replacements(
    *,
    project: Project,
    posts: Sequence[Post],
    title: str,
    editor_comment: str,
    preset_instruction: str,
    preview_mode: bool,
    datetime_context: dict[str, str],
) -> dict[str, str]:
    documents = _render_documents(posts, preview_mode=preview_mode)
    comment_block = _render_editor_comment(
        editor_comment=editor_comment,
        preset_instruction=preset_instruction,
    )
    project_description = project.description.strip() if project.description else ""
    replacements = {
        "{{PROJECT_NAME}}": project.name,
        "{{PROJECT_DESCRIPTION}}": project_description or "Описание не указано.",
        "{{POSTS}}": documents,
        "{{TITLE}}": title.strip() or "Без названия",
        "{{EDITOR_COMMENT}}": comment_block,
        "{{CURRENT_DATETIME}}": datetime_context["formatted"],
        "{{CURRENT_UTC_OFFSET}}": datetime_context["offset"],
        "{{CURRENT_TIMEZONE}}": datetime_context["time_zone"],
        "{{CURRENT_ISO_DATETIME}}": datetime_context["iso"],
    }
    return replacements


def _render_current_datetime_section(context: dict[str, str]) -> str:
    return (
        "[ТЕКУЩАЯ ДАТА]\n"
        f"Дата и время пользователя: {context['formatted']} "
        f"({context['offset']} / {context['time_zone']})\n"
        f"ISO: {context['iso']}"
    )


def _apply_replacements(text: str, replacements: dict[str, str]) -> str:
    rendered = text or ""
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered.strip()


def _render_documents(
    posts: Sequence[Post],
    *,
    preview_mode: bool,
) -> str:
    if not posts:
        return "{{POSTS}}" if preview_mode else "Источники не найдены."

    blocks: list[str] = []
    for index, post in enumerate(posts, start=1):
        body = (post.message or "").strip() or "(пустой текст)"
        link = _preferred_link(post)
        if link:
            blocks.append(f"НОВОСТЬ #{index}:\n{body}\nСсылка на источник: {link}")
        else:
            blocks.append(f"НОВОСТЬ #{index}:\n{body}")
    return "\n\n".join(blocks)


def _preferred_link(post: Post) -> str:
    if post.canonical_url:
        return post.canonical_url
    if post.source_url:
        return post.source_url
    link = getattr(post, "external_link", None)
    if isinstance(link, str) and link.strip():
        return link.strip()
    return ""


def _render_editor_comment(
    *,
    editor_comment: str,
    preset_instruction: str,
) -> str:
    parts: list[str] = []
    preset_instruction = preset_instruction.strip()
    editor_comment = editor_comment.strip()
    if preset_instruction:
        parts.append(preset_instruction)
    if editor_comment:
        parts.append(editor_comment)
    if not parts:
        return "Комментарий редактора не указан."
    return "\n\n".join(parts)


def tokens_help() -> list[tuple[str, str]]:
    """Возвращает доступные токены шаблона и их значения."""

    return list(PROMPT_TEMPLATE_TOKENS.items())
