from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stories", "0004_story_image_file_story_image_prompt"),
    ]

    operations = [
        migrations.AddField(
            model_name="publication",
            name="media_order",
            field=models.CharField(
                choices=[("before", "Перед текстом"), ("after", "После текста")],
                default="after",
                max_length=10,
                verbose_name="Порядок медиа",
            ),
        ),
    ]
