"""Запускает несколько очередей сборщиков в одном процессе."""

from __future__ import annotations

import time
from typing import Sequence

from django.core.management.base import BaseCommand, CommandError

from core.services.worker import make_runner


class Command(BaseCommand):
    help = "Запускает Telegram- и веб-сборщики вместе"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--queues",
            nargs="+",
            default=["collector", "collector_web"],
            help="Список очередей для обработки (по умолчанию collector и collector_web)",
        )
        parser.add_argument(
            "--worker-prefix",
            dest="worker_prefix",
            default="collectors",
            help="Префикс для ID воркеров (добавляется имя очереди)",
        )
        parser.add_argument(
            "--batch-size",
            dest="batch_size",
            type=int,
            default=1,
            help="Количество задач, резервируемых за итерацию для каждой очереди",
        )
        parser.add_argument(
            "--sleep",
            dest="idle_sleep",
            type=float,
            default=1.0,
            help="Длительность паузы, когда ни одна очередь не произвела работы",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            dest="run_once",
            help="Обработать одну пачку для всех очередей и выйти",
        )
        parser.add_argument(
            "--iterations",
            dest="iterations",
            type=int,
            default=None,
            help="Опциональное ограничение на количество итераций цикла (полезно для тестов)",
        )

    def handle(self, *args, **options):
        queues = [queue.lower() for queue in options.get("queues", []) if queue]
        if not queues:
            raise CommandError("Specify at least one queue via --queues")
        batch_size = options.get("batch_size", 1)
        idle_sleep = options.get("idle_sleep", 1.0)
        run_once = options.get("run_once", False)
        iterations_limit = options.get("iterations")
        worker_prefix = options.get("worker_prefix") or None

        runners = self._build_runners(
            queues=queues,
            batch_size=batch_size,
            worker_prefix=worker_prefix,
        )

        if run_once:
            processed = sum(runner.run_once() for runner in runners)
            self.stdout.write(self.style.SUCCESS(f"Processed {processed} tasks"))
            return

        loop = 0
        while True:  # pragma: no branch - intentionally long-running
            loop += 1
            processed = 0
            for runner in runners:
                processed += runner.run_once()
            if iterations_limit and loop >= iterations_limit:
                break
            if processed == 0 and idle_sleep:
                time.sleep(idle_sleep)

    def _build_runners(
        self,
        *,
        queues: Sequence[str],
        batch_size: int,
        worker_prefix: str | None,
    ):
        runners = []
        for queue in queues:
            worker_id = f"{worker_prefix}-{queue}" if worker_prefix else None
            try:
                runner = make_runner(
                    queue=queue,
                    worker_id=worker_id,
                    batch_size=batch_size,
                    idle_sleep=0,
                )
            except LookupError as exc:
                raise CommandError(str(exc)) from exc
            runners.append(runner)
        return runners
