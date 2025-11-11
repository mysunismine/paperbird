from django.db import migrations, models


def migrate_image_quality(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    mapping = {
        "standard": "medium",
        "hd": "high",
    }
    for old, new in mapping.items():
        Project.objects.filter(image_quality=old).update(image_quality=new)
    Project.objects.exclude(image_quality__in={"low", "medium", "high", "auto"}).update(image_quality="medium")


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0013_alter_post_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="image_quality",
            field=models.CharField(
                "Качество изображения",
                max_length=20,
                choices=[
                    ("low", "low"),
                    ("medium", "medium"),
                    ("high", "high"),
                    ("auto", "auto"),
                ],
                default="medium",
            ),
        ),
        migrations.RunPython(migrate_image_quality, migrations.RunPython.noop),
    ]
