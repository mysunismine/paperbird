"""URL-конфигурация для тестирования обработчика ошибок."""

from __future__ import annotations

from django.http import HttpRequest
from django.urls import include, path


def crash_view(request: HttpRequest):
    """Искусственно выбрасывает ошибку для проверки страницы 500."""

    raise RuntimeError("Тестовая ошибка")


urlpatterns = [
    path("accounts/", include("accounts.urls")),
    path("projects/", include("projects.urls")),
    path("stories/", include("stories.paperbird_stories.urls")),
    path("", include("core.urls")),
    path("boom/", crash_view),
]


handler500 = "core.views.server_error"
