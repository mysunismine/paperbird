"""Management-команда для запуска фонового воркера для очереди."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from core.services.worker import make_runner


class Command(BaseCommand):
    help = "Запускает фоновый воркер для указанной очереди"

    def add_arguments(self, parser) -> None:
        parser.add_argument("queue", help="Имя очереди для обработки")
        parser.add_argument(
            "--worker-id",
            dest="worker_id",
            help="Явный идентификатор воркера (по умолчанию hostname/pid)",
        )
        parser.add_argument(
            "--handler",
            dest="handler",
            help="Путь к обработчику (опционально, если очередь зарегистрирована)",
        )
        parser.add_argument(
            "--batch-size",
            dest="batch_size",
            type=int,
            default=1,
            help="Количество задач для резервирования за итерацию",
        )
        parser.add_argument(
            "--sleep",
            dest="idle_sleep",
            type=float,
            default=1.0,
            help="Длительность паузы в секундах, когда очередь пуста",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            dest="run_once",
            help="Обработать одну пачку и выйти",
        )

    def handle(self, *args, **options):
        queue = options["queue"].lower()
        worker_id = options.get("worker_id")
        handler_path = options.get("handler")
        batch_size = options.get("batch_size", 1)
        idle_sleep = options.get("idle_sleep", 1.0)
        run_once = options.get("run_once", False)

        handler = None
        if handler_path:
            try:
                handler = import_string(handler_path)
            except ImportError as exc:  # pragma: no cover - defensive branch
                raise CommandError(f"Cannot import handler '{handler_path}': {exc}") from exc

        try:
            runner = make_runner(
                queue=queue,
                handler=handler,
                worker_id=worker_id,
                batch_size=batch_size,
                idle_sleep=idle_sleep,
            )
        except LookupError as exc:
            raise CommandError(str(exc)) from exc

        if run_once:
            processed = runner.run_once()
            self.stdout.write(self.style.SUCCESS(f"Processed {processed} tasks"))
            return
        runner.run_forever()
