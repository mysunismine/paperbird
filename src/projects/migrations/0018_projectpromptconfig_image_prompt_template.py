from django.db import migrations, models


DEFAULT_IMAGE_PROMPT_TEMPLATE = (
    "Ты помогаешь редактору подобрать промпт для генерации иллюстрации к сюжету.\n"
    "Используй контекст ниже и предложи короткий визуальный промпт для генерации "
    "изображения (1-2 предложения).\n"
    "Требования:\n"
    "- Без указаний по размерам, форматам, разрешению или стилям рендера.\n"
    "- Сфокусируйся на ключевом визуальном образе.\n"
    "- Избегай упоминания конкретных брендов, персональных данных и логотипов.\n"
    "- Ответ строго в JSON: {\"prompt\": \"...\"}\n"
    "\n"
    "Проект: {{PROJECT_NAME}}\n"
    "Описание проекта: {{PROJECT_DESCRIPTION}}\n"
    "Заголовок сюжета: {{STORY_TITLE}}\n"
    "Краткое описание: {{STORY_SUMMARY}}\n"
    "Текст сюжета: {{STORY_BODY}}\n"
    "Источники:\n"
    "{{POSTS}}\n"
)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0017_source_web_retry_base_delay_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectpromptconfig",
            name="image_prompt_template",
            field=models.TextField(
                blank=True,
                default=DEFAULT_IMAGE_PROMPT_TEMPLATE,
                help_text="Шаблон для запроса в модель при подборе идеи иллюстрации.",
                verbose_name="Промпт для генерации идеи изображения",
            ),
        ),
    ]
