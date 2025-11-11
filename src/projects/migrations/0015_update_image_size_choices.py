from django.db import migrations, models


def migrate_image_size(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    legacy_map = {
        "512x512": "1024x1024",
        "256x256": "1024x1024",
    }
    for old, new in legacy_map.items():
        Project.objects.filter(image_size=old).update(image_size=new)
    Project.objects.exclude(image_size__in={"1024x1024", "1024x1536", "1536x1024", "auto"}).update(
        image_size="1024x1024"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0014_update_image_quality_choices"),
    ]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="image_size",
            field=models.CharField(
                "Размер изображения",
                max_length=20,
                choices=[
                    ("1024x1024", "1024x1024"),
                    ("1024x1536", "1024x1536"),
                    ("1536x1024", "1536x1024"),
                    ("auto", "auto"),
                ],
                default="1024x1024",
            ),
        ),
        migrations.RunPython(migrate_image_size, migrations.RunPython.noop),
    ]
