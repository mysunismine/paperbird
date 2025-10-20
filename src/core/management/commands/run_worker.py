"""Management command to run a background worker for a queue."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from core.services.worker import make_runner


class Command(BaseCommand):
    help = "Run background worker for the specified queue"

    def add_arguments(self, parser) -> None:
        parser.add_argument("queue", help="Queue name to process")
        parser.add_argument(
            "--worker-id",
            dest="worker_id",
            help="Explicit worker identifier (defaults to hostname/pid)",
        )
        parser.add_argument(
            "--handler",
            dest="handler",
            help="Dotted path to handler callable (optional if queue registered)",
        )
        parser.add_argument(
            "--batch-size",
            dest="batch_size",
            type=int,
            default=1,
            help="Number of tasks to reserve per iteration",
        )
        parser.add_argument(
            "--sleep",
            dest="idle_sleep",
            type=float,
            default=1.0,
            help="Sleep duration in seconds when queue is empty",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            dest="run_once",
            help="Process a single batch and exit",
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
