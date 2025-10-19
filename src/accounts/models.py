"""Модели пользователей и профиля.

Определяет кастомного пользователя с данными Telethon.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Пользователь со встроенными полями для Telethon."""

    telethon_api_id = models.PositiveBigIntegerField(
        blank=True,
        null=True,
        verbose_name="Telethon API ID",
        help_text="Идентификатор приложения на my.telegram.org",
    )
    telethon_api_hash = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Telethon API hash",
        help_text="Секретный ключ приложения на my.telegram.org",
    )
    telethon_session = models.TextField(
        blank=True,
        verbose_name="Telethon session",
        help_text="Строковая сессия Telethon. Используйте StringSession для хранения.",
    )

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    @property
    def has_telethon_credentials(self) -> bool:
        """Признак наличия всех данных для Telethon."""

        return bool(self.telethon_api_id and self.telethon_api_hash and self.telethon_session)
