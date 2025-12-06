from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from unittest.mock import patch

from . import User
from projects.services.telethon_client import TelethonCredentialsMissingError


class CollectPostsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("runner", password="secret")
        self.user.telethon_api_id = 123456
        self.user.telethon_api_hash = "hash123"
        self.user.telethon_session = "stub-session"
        self.user.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )

    @patch("projects.management.commands.collect_posts.collect_for_user_sync")
    def test_command_wraps_telethon_errors(self, mock_collect) -> None:
        mock_collect.side_effect = TelethonCredentialsMissingError("Сессия недействительна")
        with self.assertRaisesMessage(CommandError, "Сессия недействительна"):
            call_command("collect_posts", self.user.username)
        mock_collect.assert_called_once()

    @patch("projects.management.commands.collect_posts.collect_for_user_sync")
    def test_command_passes_follow_arguments(self, mock_collect) -> None:
        call_command(
            "collect_posts",
            self.user.username,
            "--project",
            "7",
            "--limit",
            "25",
            "--interval",
            "30",
            "--follow",
        )
        mock_collect.assert_called_once_with(
            self.user,
            project_id=7,
            limit=25,
            continuous=True,
            interval=30,
        )

    @patch("projects.management.commands.collect_posts.collect_for_all_users_sync")
    def test_all_users_flag_runs_collector(self, mock_all_users) -> None:
        call_command(
            "collect_posts",
            "--all-users",
            "--limit",
            "10",
            "--interval",
            "15",
            "--follow",
        )
        mock_all_users.assert_called_once_with(
            project_id=None,
            limit=10,
            continuous=True,
            interval=15,
        )

    def test_username_required_without_flag(self) -> None:
        with self.assertRaisesMessage(CommandError, "Укажите username или используйте флаг --all-users."):
            call_command("collect_posts")

    def test_all_users_conflicts_with_username(self) -> None:
        with self.assertRaisesMessage(CommandError, "Нельзя указывать username вместе с флагом --all-users."):
            call_command("collect_posts", self.user.username, "--all-users")

    def test_all_users_conflicts_with_project(self) -> None:
        with self.assertRaisesMessage(CommandError, "Флаг --project несовместим с режимом --all-users."):
            call_command("collect_posts", "--all-users", "--project", "1")
