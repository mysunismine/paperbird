"""Forms related to project prompt configuration."""

from __future__ import annotations

from django import forms

from projects.models import ProjectPromptConfig


class ProjectPromptConfigForm(forms.ModelForm):
    """Редактирование основного шаблона промтов проекта."""

    class Meta:
        model = ProjectPromptConfig
        fields = [
            "system_role",
            "task_instruction",
            "documents_intro",
            "style_requirements",
            "output_format",
            "output_example",
            "editor_comment_note",
            "image_prompt_template",
        ]
        widgets = {
            field: forms.Textarea(
                attrs={
                    "class": "form-control font-monospace",
                    "rows": 4
                    if field not in {"output_format", "output_example", "image_prompt_template"}
                    else 8,
                }
            )
            for field in fields
        }
        labels = {
            "system_role": "Системная роль",
            "task_instruction": "Задание",
            "documents_intro": "Источники / документы",
            "style_requirements": "Требования к стилю",
            "output_format": "Формат ответа (JSON)",
            "output_example": "Пример корректного вывода",
            "editor_comment_note": "Комментарий редактора",
            "image_prompt_template": "Промпт для идеи изображения",
        }
        help_texts = {
            "system_role": "Например: «Ты — редактор ... {{PROJECT_NAME}}».",
            "documents_intro": (
                "Вставьте {{POSTS}}, чтобы книга новостей появилась на месте шаблона."
            ),
            "editor_comment_note": (
                "Используйте {{EDITOR_COMMENT}}, чтобы подставить текст редактора."
            ),
            "image_prompt_template": (
                "Модель должна вернуть JSON с полем prompt. "
                "Можно использовать {{STORY_TITLE}}, {{STORY_SUMMARY}}, {{STORY_BODY}}."
            ),
        }
