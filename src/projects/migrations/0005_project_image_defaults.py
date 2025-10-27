from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0004_alter_source_telegram_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="image_model",
            field=models.CharField(
                choices=[("gpt-image-1", "gpt-image-1"), ("dall-e-3", "dall-e-3")],
                default="gpt-image-1",
                max_length=100,
                verbose_name="Модель генерации изображений",
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="image_quality",
            field=models.CharField(
                choices=[("standard", "standard"), ("hd", "hd")],
                default="standard",
                max_length=20,
                verbose_name="Качество изображения",
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="image_size",
            field=models.CharField(
                choices=[
                    ("1024x1024", "1024x1024"),
                    ("512x512", "512x512"),
                    ("1792x1024", "1792x1024"),
                    ("1024x1792", "1024x1792"),
                ],
                default="1024x1024",
                max_length=20,
                verbose_name="Размер изображения",
            ),
        ),
    ]
