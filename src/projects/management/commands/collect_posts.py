"""Команда для запуска сбора постов."""

from __future__ import annotations

import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from projects.services.collector import collect_for_user_sync
from projects.services.telethon_client import TelethonCredentialsMissingError

User = get_user_model()


class Command(BaseCommand):
    help = "Собирает посты из Telegram-источников для заданного пользователя"

    def add_arguments(self, parser):
        parser.add_argument("username", help="Имя пользователя (django username)")
        parser.add_argument(
            "--project",
            dest="project_id",
            type=int,
            help="ID проекта. Если не указан — собираются все проекты пользователя.",
        )
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            default=100,
            help="Сколько сообщений запрашивать у каждого источника",
        )
        parser.add_argument(
            "--follow",
            action="store_true",
            help="Включить непрерывный режим мониторинга (Ctrl+C для остановки)",
        )
        parser.add_argument(
            "--interval",
            dest="interval",
            type=int,
            default=60,
            help="Интервал между циклами в секундах в непрерывном режиме",
        )

    def handle(self, *args, **options):
        username = options["username"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:  # pragma: no cover - простая ветка
            raise CommandError(f"Пользователь {username!r} не найден") from exc

        if not user.has_telethon_credentials:
            raise CommandError(
                "У пользователя не настроены Telethon-ключи. Заполните профиль и повторите."
            )

        password_owner = user.username if user.username else getpass.getuser()
        mode = "непрерывный" if options["follow"] else "разовый"
        interval = options["interval"]
        note = f"interval={interval}s" if options["follow"] else f"limit={options['limit']}"
        self.stdout.write(
            self.style.NOTICE(
                f"Запуск {mode} сборщика для пользователя {password_owner} ({note})"
            )
        )
        try:
            collect_for_user_sync(
                user,
                project_id=options.get("project_id"),
                limit=options["limit"],
                continuous=options["follow"],
                interval=interval,
            )
        except TelethonCredentialsMissingError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Сбор завершён"))
