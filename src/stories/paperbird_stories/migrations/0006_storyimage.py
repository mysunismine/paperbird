from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("stories", "0005_publication_media_order"),
    ]

    operations = [
        migrations.CreateModel(
            name="StoryImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image_file", models.FileField(upload_to="story_images/", verbose_name="Изображение")),
                ("prompt", models.TextField(blank=True, default="", verbose_name="Промпт генерации")),
                (
                    "source_kind",
                    models.CharField(
                        choices=[
                            ("generated", "Сгенерировано"),
                            ("upload", "Загрузка"),
                            ("source", "Источник"),
                        ],
                        default="generated",
                        max_length=20,
                        verbose_name="Источник",
                    ),
                ),
                ("is_selected", models.BooleanField(default=True, verbose_name="Выбрано для публикации")),
                ("is_main", models.BooleanField(default=False, verbose_name="Основное")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "story",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="images",
                        to="stories.story",
                        verbose_name="Сюжет",
                    ),
                ),
            ],
            options={
                "verbose_name": "Изображение сюжета",
                "verbose_name_plural": "Изображения сюжетов",
                "ordering": ("-is_main", "-created_at"),
            },
        ),
    ]
