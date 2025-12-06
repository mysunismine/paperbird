import asyncio
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from . import User
from projects.services.collector import collect_for_all_users


class CollectForAllUsersTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user_with_creds = User.objects.create_user("collector1", password="secret")
        self.user_with_creds.telethon_api_id = 111
        self.user_with_creds.telethon_api_hash = "hash"
        self.user_with_creds.telethon_session = "session"
        self.user_with_creds.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )
        self.user_without_creds = User.objects.create_user("collector2", password="secret")

    @patch("projects.services.collector.collect_for_user", new_callable=AsyncMock)
    def test_collects_only_users_with_credentials(self, mock_collect) -> None:
        asyncio.run(collect_for_all_users(limit=77))
        mock_collect.assert_awaited_once()
        mock_collect.assert_awaited_with(
            self.user_with_creds,
            project_id=None,
            limit=77,
        )

    @patch("projects.services.collector.collect_for_user", new_callable=AsyncMock)
    def test_handles_collect_errors_per_user(self, mock_collect) -> None:
        mock_collect.side_effect = [RuntimeError("boom"), None]
        other = User.objects.create_user("collector3", password="secret")
        other.telethon_api_id = 222
        other.telethon_api_hash = "hash2"
        other.telethon_session = "session2"
        other.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )
        asyncio.run(collect_for_all_users(limit=10))
        self.assertEqual(mock_collect.await_count, 2)
