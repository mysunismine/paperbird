from django.db import migrations, models


NEW_SIZE_CHOICES = [
    ("512x512", "512x512"),
    ("256x256", "256x256"),
]


def reduce_image_sizes(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Project.objects.filter(
        image_size__in=["1024x1024", "1792x1024", "1024x1792"]
    ).update(image_size="512x512")


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0006_project_collector_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="image_size",
            field=models.CharField(
                choices=NEW_SIZE_CHOICES,
                default="512x512",
                max_length=20,
                verbose_name="Размер изображения",
            ),
        ),
        migrations.RunPython(reduce_image_sizes, migrations.RunPython.noop),
    ]
