from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("projects", "0025_project_members"),
        ("stories", "0006_storyimage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaAsset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image_file", models.FileField(upload_to="media_library/", verbose_name="Изображение")),
                ("title", models.CharField(blank=True, max_length=200, verbose_name="Заголовок")),
                ("prompt", models.TextField(blank=True, verbose_name="Промпт")),
                (
                    "source_kind",
                    models.CharField(
                        choices=[("generated", "Сгенерировано"), ("upload", "Загружено"), ("imported", "Из поста")],
                        default="upload",
                        max_length=20,
                        verbose_name="Источник",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="media_assets",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Автор",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="media_assets",
                        to="projects.project",
                        verbose_name="Проект",
                    ),
                ),
                (
                    "story",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="media_assets",
                        to="stories.story",
                        verbose_name="Сюжет",
                    ),
                ),
            ],
            options={
                "verbose_name": "Медиа библиотеки",
                "verbose_name_plural": "Медиа библиотека",
                "ordering": ("-created_at",),
            },
        ),
    ]
