import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from projects.models import Project, Source
from projects.services.telethon_client import (
    TelethonClientFactory,
    TelethonCredentialsMissingError,
    extract_invite_hash,
    resolve_telegram_target,
)
from projects.workers import refresh_source_metadata_task

from . import User


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


class TelegramInviteResolveTests(TestCase):
    def test_extract_invite_hash_supports_plus_and_joinchat(self) -> None:
        self.assertEqual(extract_invite_hash("https://t.me/+Ps_kUDH31H5kZTdi"), "Ps_kUDH31H5kZTdi")
        self.assertEqual(extract_invite_hash("https://t.me/joinchat/AbCdEf123"), "AbCdEf123")

    def test_resolve_telegram_target_uses_import_for_invite_link(self) -> None:
        entity = SimpleNamespace(id=321, title="Private channel", username=None)

        class FakeClient:
            async def __call__(self, request):
                name = request.__class__.__name__
                if name == "CheckChatInviteRequest":
                    return SimpleNamespace()
                if name == "ImportChatInviteRequest":
                    return SimpleNamespace(chats=[entity])
                raise AssertionError(name)

            async def get_entity(self, target):
                raise AssertionError(f"Unexpected fallback get_entity({target})")

        resolved = asyncio.run(resolve_telegram_target(FakeClient(), "https://t.me/+abcdef"))
        self.assertEqual(resolved.id, 321)

    def test_resolve_telegram_target_fallbacks_to_get_entity(self) -> None:
        expected = SimpleNamespace(id=1)

        class FakeClient:
            async def __call__(self, request):
                raise AssertionError(request)

            async def get_entity(self, target):
                return expected

        resolved = asyncio.run(resolve_telegram_target(FakeClient(), "technews"))
        self.assertIs(resolved, expected)
