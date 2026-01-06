from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0023_source_web_policy_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="source",
            name="web_last_seen_url",
            field=models.URLField(
                blank=True,
                help_text="Используется для инкрементального сбора веб-источников.",
                verbose_name="Последний обработанный URL",
            ),
        ),
        migrations.AddField(
            model_name="source",
            name="web_last_seen_published_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Используется для инкрементального сбора веб-источников.",
                null=True,
                verbose_name="Последняя дата публикации",
            ),
        ),
        migrations.CreateModel(
            name="WebFetchCache",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("url", models.URLField(max_length=1000, verbose_name="URL")),
                ("etag", models.CharField(blank=True, max_length=255, verbose_name="ETag")),
                (
                    "last_modified",
                    models.CharField(blank=True, max_length=255, verbose_name="Last-Modified"),
                ),
                ("last_status_code", models.PositiveSmallIntegerField(default=0, verbose_name="Последний статус")),
                ("last_checked_at", models.DateTimeField(blank=True, null=True, verbose_name="Последняя проверка")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создан")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлён")),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="web_cache_entries",
                        to="projects.source",
                        verbose_name="Источник",
                    ),
                ),
            ],
            options={
                "verbose_name": "Кеш веб-запросов",
                "verbose_name_plural": "Кеш веб-запросов",
                "unique_together": {("source", "url")},
            },
        ),
    ]
