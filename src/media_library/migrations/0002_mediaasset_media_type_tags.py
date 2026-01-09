from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("media_library", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaasset",
            name="media_type",
            field=models.CharField(
                choices=[("image", "Изображение"), ("video", "Видео"), ("gif", "GIF")],
                default="image",
                max_length=20,
                verbose_name="Тип медиа",
            ),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="tags",
            field=models.JSONField(blank=True, default=list, verbose_name="Теги"),
        ),
    ]
