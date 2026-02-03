from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0026_alter_project_image_prompt_model_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="source",
            name="type",
            field=models.CharField(
                "Тип источника",
                max_length=20,
                choices=[
                    ("telegram", "Telegram"),
                    ("web", "Web"),
                    ("manual", "Свой текст"),
                ],
                default="telegram",
            ),
        ),
        migrations.AlterField(
            model_name="post",
            name="origin_type",
            field=models.CharField(
                "Тип источника",
                max_length=20,
                choices=[
                    ("telegram", "Telegram"),
                    ("web", "Web"),
                    ("manual", "Свой текст"),
                ],
                default="telegram",
            ),
        ),
    ]
