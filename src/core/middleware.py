"""Промежуточные слои для настройки контекста логов и обработки ошибок."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from core.logging import event_logger, generate_correlation_id, logging_context


class RequestContextMiddleware:
    """Добавляет correlation_id и user_id в контекст логирования каждого запроса."""

    header_name = "HTTP_X_CORRELATION_ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.logger = event_logger("paperbird.request")
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        correlation_id = request.META.get(self.header_name) or generate_correlation_id()
        request.correlation_id = correlation_id
        user = getattr(request, "user", None)
        user_id: int | None = None
        if user is not None and getattr(user, "is_authenticated", False):
            user_id = user.pk

        with logging_context(correlation_id=correlation_id, user_id=user_id):
            try:
                response = self.get_response(request)
            except Exception as exc:  # pragma: no cover - защитное логирование
                self.logger.error(
                    "unhandled_error",
                    path=request.path,
                    method=request.method,
                    error=str(exc),
                    exception=exc.__class__.__name__,
                )
                raise

        response["X-Correlation-ID"] = correlation_id
        return response
