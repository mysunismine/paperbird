from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0005_project_image_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="collector_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Если включено, фоновый сборщик будет регулярно обновлять ленту проекта.",
                verbose_name="Сборщик активен",
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="collector_interval",
            field=models.PositiveIntegerField(
                default=300,
                help_text="Через какой промежуток времени запускать следующий цикл сбора.",
                verbose_name="Интервал сбора (сек)",
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="collector_last_run",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Последний запуск сборщика",
            ),
        ),
    ]
