from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from . import User
from projects.models import Project, Source
from projects.services.telethon_client import (
    TelethonClientFactory,
    TelethonCredentialsMissingError,
)
from projects.workers import refresh_source_metadata_task


class SourceMetadataWorkerTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("owner", password="secret")
        self.user.telethon_api_id = 123
        self.user.telethon_api_hash = "hash"
        self.user.telethon_session = "session"
        self.user.save(update_fields=["telethon_api_id", "telethon_api_hash", "telethon_session"])
        self.project = Project.objects.create(owner=self.user, name="Лента")
        self.source = Source.objects.create(project=self.project, username="technews")

    @patch("projects.workers.TelethonClientFactory")
    def test_refresh_updates_source(self, mock_factory) -> None:
        async def get_entity(target):
            return SimpleNamespace(title="Tech News", username="TechNewsRu", id=999)

        class DummyContext:
            async def __aenter__(self):
                return SimpleNamespace(get_entity=get_entity)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        mock_factory.return_value.connect.return_value = DummyContext()

        task = SimpleNamespace(payload={"source_id": self.source.pk})
        result = refresh_source_metadata_task(task)
        self.assertEqual(result["status"], "ok")
        mock_factory.assert_called_once_with(user=self.user)
        self.source.refresh_from_db()
        self.assertEqual(self.source.title, "Tech News")
        self.assertEqual(self.source.username, "technewsru")
        self.assertEqual(self.source.telegram_id, 999)

    def test_refresh_skips_without_credentials(self) -> None:
        self.user.telethon_api_id = None
        self.user.telethon_api_hash = ""
        self.user.telethon_session = ""
        self.user.save(update_fields=["telethon_api_id", "telethon_api_hash", "telethon_session"])
        task = SimpleNamespace(payload={"source_id": self.source.pk})
        result = refresh_source_metadata_task(task)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_credentials")


class TelethonClientFactoryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("collector", password="secret")
        self.user.telethon_api_id = 123456
        self.user.telethon_api_hash = "hash123"
        self.user.save(update_fields=["telethon_api_id", "telethon_api_hash"])

    def test_build_requires_credentials(self) -> None:
        factory = TelethonClientFactory(user=self.user)
        with self.assertRaisesMessage(
            TelethonCredentialsMissingError,
            "У пользователя не заполнены ключи Telethon",
        ):
            factory.build()

    def test_build_rejects_invalid_session(self) -> None:
        self.user.telethon_session = "broken"
        self.user.save(update_fields=["telethon_session"])
        factory = TelethonClientFactory(user=self.user)
        with self.assertRaisesMessage(
            TelethonCredentialsMissingError,
            "Строка Telethon-сессии повреждена. Сгенерируйте новую и сохраните её в профиле.",
        ):
            factory.build()

    @patch("projects.services.telethon_client.TelegramClient")
    @patch("projects.services.telethon_client.StringSession")
    def test_build_strips_wrappers(self, mock_string_session, mock_client) -> None:
        mock_string_session.return_value = MagicMock()
        mock_client.return_value = MagicMock()
        self.user.telethon_session = 'StringSession("1Aabc==")'
        self.user.save(update_fields=["telethon_session"])
        factory = TelethonClientFactory(user=self.user)
        factory.build()
        mock_string_session.assert_called_once_with("1Aabc==")
