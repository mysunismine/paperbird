import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stories", "0002_publication"),
        ("projects", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RewritePreset",
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
                (
                    "name",
                    models.CharField(max_length=100, verbose_name="Название"),
                ),
                (
                    "description",
                    models.TextField(blank=True, verbose_name="Описание"),
                ),
                (
                    "style",
                    models.CharField(blank=True, max_length=255, verbose_name="Стиль"),
                ),
                (
                    "editor_comment",
                    models.TextField(blank=True, verbose_name="Комментарий редактора"),
                ),
                (
                    "max_length_tokens",
                    models.PositiveIntegerField(
                        default=1000,
                        verbose_name="Максимальное количество токенов",
                    ),
                ),
                (
                    "output_format",
                    models.JSONField(blank=True, default=dict, verbose_name="Формат вывода"),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Активен"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rewrite_presets",
                        to="projects.project",
                        verbose_name="Проект",
                    ),
                ),
            ],
            options={
                "verbose_name": "Пресет рерайта",
                "verbose_name_plural": "Пресеты рерайта",
                "ordering": ("name",),
                "unique_together": {("project", "name")},
            },
        ),
        migrations.AddField(
            model_name="rewritetask",
            name="preset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rewrite_tasks",
                to="stories.rewritepreset",
                verbose_name="Пресет",
            ),
        ),
        migrations.AddField(
            model_name="story",
            name="last_rewrite_preset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="stories",
                to="stories.rewritepreset",
                verbose_name="Последний пресет",
            ),
        ),
    ]
