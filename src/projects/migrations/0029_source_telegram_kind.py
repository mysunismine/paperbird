from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0028_postversion"),
    ]

    operations = [
        migrations.AddField(
            model_name="source",
            name="telegram_kind",
            field=models.CharField(
                choices=[
                    ("unknown", "Не определён"),
                    ("channel", "Канал"),
                    ("chat", "Чат"),
                ],
                default="unknown",
                max_length=20,
                verbose_name="Тип Telegram-источника",
            ),
        ),
    ]
