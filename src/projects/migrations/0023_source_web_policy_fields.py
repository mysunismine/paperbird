from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0022_normalize_openai_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="source",
            name="web_request_interval_sec",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Пауза между запросами к сайту, чтобы не перегружать источник.",
                null=True,
                verbose_name="Минимальный интервал между запросами (сек)",
            ),
        ),
        migrations.AddField(
            model_name="source",
            name="web_request_jitter_sec",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Добавляется случайная задержка к каждому запросу.",
                null=True,
                verbose_name="Случайная пауза (сек)",
            ),
        ),
        migrations.AddField(
            model_name="source",
            name="web_max_items_per_run",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text=(
                    "Ограничивает число статей, которые парсер забирает за один запуск."
                ),
                null=True,
                verbose_name="Максимум статей за запуск",
            ),
        ),
        migrations.AddField(
            model_name="source",
            name="web_block_cooldown_sec",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Сколько ждать перед следующей попыткой, если сайт заблокировал доступ."
                ),
                null=True,
                verbose_name="Cooldown при блокировке (сек)",
            ),
        ),
        migrations.AddField(
            model_name="source",
            name="web_blocked_until",
            field=models.DateTimeField(
                blank=True,
                help_text="До этого времени источник не будет опрашиваться.",
                null=True,
                verbose_name="Блокировка до",
            ),
        ),
        migrations.AddField(
            model_name="source",
            name="web_block_reason",
            field=models.TextField(blank=True, verbose_name="Причина блокировки"),
        ),
    ]
