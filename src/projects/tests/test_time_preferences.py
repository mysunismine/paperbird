from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from . import User
from core.constants import (
    IMAGE_DEFAULT_MODEL,
    IMAGE_DEFAULT_QUALITY,
    IMAGE_DEFAULT_SIZE,
    REWRITE_DEFAULT_MODEL,
)
from projects.forms import ProjectCreateForm
from projects.models import Project
from projects.services.prompt_config import render_prompt
from projects.services.time_preferences import build_project_datetime_context


class ProjectTimePreferenceFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("pref-owner", password="secret")

    def _make_data(self, **overrides):
        base = {
            "name": "Локальный проект",
            "description": "",
            "publish_target": "@channel",
            "locale": "ru_RU",
            "time_zone": "Europe/Moscow",
            "rewrite_model": REWRITE_DEFAULT_MODEL,
            "image_model": IMAGE_DEFAULT_MODEL,
            "image_size": IMAGE_DEFAULT_SIZE,
            "image_quality": IMAGE_DEFAULT_QUALITY,
            "retention_days": 30,
            "collector_telegram_interval": 300,
            "collector_web_interval": 300,
        }
        base.update(overrides)
        return base

    def test_invalid_timezone_rejected(self) -> None:
        form = ProjectCreateForm(data=self._make_data(time_zone="Mars/Phobos"), owner=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("time_zone", form.errors)

    def test_fixed_offset_timezone_allowed(self) -> None:
        form = ProjectCreateForm(data=self._make_data(time_zone="UTC+05:30"), owner=self.user)
        self.assertTrue(form.is_valid(), form.errors)


class ProjectTimePreferenceUtilsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("pref-utils", password="secret")

    def test_context_uses_locale_and_timezone(self) -> None:
        project = Project.objects.create(
            owner=self.user,
            name="Часовой пояс",
            locale="ru_RU",
            time_zone="UTC+02:30",
        )
        fixed_now = datetime(2024, 1, 1, 10, 0, tzinfo=dt_timezone.utc)
        with patch("projects.services.time_preferences.timezone.now", return_value=fixed_now):
            context = build_project_datetime_context(project)
        self.assertEqual(context["formatted"], "01.01.2024 12:30")
        self.assertEqual(context["offset"], "UTC+02:30")
        self.assertEqual(context["time_zone"], "UTC+02:30")
        self.assertTrue(context["iso"].startswith("2024-01-01T12:30:00"))


class PromptCurrentDatetimeInjectionTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("prompt-owner", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Новости",
            locale="en_US",
            time_zone="Europe/Berlin",
        )

    def test_render_prompt_appends_datetime_section(self) -> None:
        context = {
            "formatted": "2024-05-10 12:00",
            "offset": "UTC+02:00",
            "time_zone": "Europe/Berlin",
            "iso": "2024-05-10T12:00:00+02:00",
        }
        with patch(
            "projects.services.prompt_config.build_project_datetime_context",
            return_value=context,
        ):
            rendered = render_prompt(project=self.project, posts=[], title="Новости дня")
        self.assertIn("UTC+02:00", rendered.full_text)
        self.assertIn("Europe/Berlin", rendered.full_text)
        self.assertEqual(rendered.sections[-1][0], "current_datetime")
        self.assertIn("2024-05-10 12:00", rendered.full_text)
        self.assertIn("ISO: 2024-05-10T12:00:00+02:00", rendered.sections[-1][1])
