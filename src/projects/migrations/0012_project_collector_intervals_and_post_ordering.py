from django.db import migrations, models


def copy_intervals_to_web(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    for project in Project.objects.all():
        project.collector_web_interval = project.collector_telegram_interval
        project.save(update_fields=["collector_web_interval"])


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0011_project_locale_project_time_zone"),
    ]

    operations = [
        migrations.RenameField(
            model_name="project",
            old_name="collector_interval",
            new_name="collector_telegram_interval",
        ),
        migrations.AddField(
            model_name="project",
            name="collector_web_interval",
            field=models.PositiveIntegerField(
                default=300,
                help_text="Как часто запускать веб-парсер (не менее 60 секунд).",
                verbose_name="Интервал веб-парсера (сек)",
            ),
        ),
        migrations.RunPython(copy_intervals_to_web, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="post",
            index=models.Index(
                fields=("project", "collected_at"),
                name="post_project_collected_idx",
            ),
        ),
    ]
