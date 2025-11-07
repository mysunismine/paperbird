from django.db import migrations, models

DEFAULT_SECTIONS = {
    "system_role": "Ты — редактор новостного Telegram-канала на тему: {{PROJECT_NAME}}.",
    "task_instruction": (
        "Твоя задача — переписать предоставленные новости в указанном стиле, сохраняя смысл и факты.\n"
        "Если есть несколько новостей, объедини их логично и последовательно. Используй только самые важные факты."
    ),
    "documents_intro": (
        "Тебе даны следующие источники:\n"
        "{{POSTS}}\n"
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


def create_default_configs(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Config = apps.get_model("projects", "ProjectPromptConfig")
    for project in Project.objects.all():
        Config.objects.get_or_create(
            project=project,
            defaults=DEFAULT_SECTIONS.copy(),
        )


def drop_configs(apps, schema_editor):
    Config = apps.get_model("projects", "ProjectPromptConfig")
    Config.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0008_project_rewrite_model"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectPromptConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("system_role", models.TextField(blank=True, verbose_name="Системная роль")),
                ("task_instruction", models.TextField(blank=True, verbose_name="Задание")),
                ("documents_intro", models.TextField(blank=True, verbose_name="Описание документов")),
                ("style_requirements", models.TextField(blank=True, verbose_name="Требования к стилю")),
                ("output_format", models.TextField(blank=True, verbose_name="Формат ответа")),
                ("output_example", models.TextField(blank=True, verbose_name="Пример вывода")),
                ("editor_comment_note", models.TextField(blank=True, verbose_name="Комментарий редактора")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                (
                    "project",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="prompt_config",
                        to="projects.project",
                        verbose_name="Проект",
                    ),
                ),
            ],
            options={
                "verbose_name": "Шаблон промтов проекта",
                "verbose_name_plural": "Шаблоны промтов проекта",
            },
        ),
        migrations.RunPython(create_default_configs, drop_configs),
    ]
