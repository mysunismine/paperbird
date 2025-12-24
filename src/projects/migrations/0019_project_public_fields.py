from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0018_projectpromptconfig_image_prompt_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="public_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Показывать публичную витрину опубликованных материалов.",
                verbose_name="Публичные страницы",
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="public_noindex",
            field=models.BooleanField(
                default=True,
                help_text="Добавлять noindex для публичных страниц проекта.",
                verbose_name="Закрыть от индексации",
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="public_title",
            field=models.CharField(
                blank=True,
                help_text="Отображается на публичной витрине вместо названия проекта.",
                max_length=200,
                verbose_name="Публичное название",
            ),
        ),
    ]
