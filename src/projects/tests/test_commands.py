import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from projects.models import Project, Source, WebPreset
from projects.services.telethon_client import TelethonCredentialsMissingError

from . import User, make_preset_payload


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
        with self.assertRaisesMessage(
            CommandError,
            "Укажите username или используйте флаг --all-users.",
        ):
            call_command("collect_posts")

    def test_all_users_conflicts_with_username(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "Нельзя указывать username вместе с флагом --all-users.",
        ):
            call_command("collect_posts", self.user.username, "--all-users")

    def test_all_users_conflicts_with_project(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "Флаг --project несовместим с режимом --all-users.",
        ):
            call_command("collect_posts", "--all-users", "--project", "1")


class ExportPrivateArtifactsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("owner", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Private News")
        preset_payload = make_preset_payload("private_feed")
        self.preset = WebPreset.objects.create(
            name=preset_payload["name"],
            version=preset_payload["version"],
            title="Private Feed",
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum="123",
            config=preset_payload,
        )
        Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            web_preset=self.preset,
            web_preset_snapshot=preset_payload,
            title="Source",
        )

    def test_exports_project_artifacts_to_private_dir(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with override_settings(PAPERBIRD_PRIVATE_ARTIFACTS_DIR=Path(tmpdir)):
                call_command(
                    "export_private_artifacts",
                    self.user.username,
                    "--project",
                    str(self.project.pk),
                )
            exported_root = Path(tmpdir) / "owner"
            exported_dirs = list(exported_root.iterdir())
            self.assertEqual(len(exported_dirs), 1)
            project_dir = exported_dirs[0]
            self.assertTrue((project_dir / "project-export.json").exists())
            self.assertTrue((project_dir / "prompt.txt").exists())
            preset_files = list((project_dir / "web-presets").glob("*.json"))
            self.assertEqual(len(preset_files), 1)

    def test_raises_for_unknown_project(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            f"Проект с id=999 для пользователя {self.user.username!r} не найден.",
        ):
            call_command(
                "export_private_artifacts",
                self.user.username,
                "--project",
                "999",
            )


class ImportPrivateArtifactsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("importer", password="secret")

    def _payload(self, *, name: str) -> dict:
        return {
            "schema_version": 1,
            "project": {
                "name": name,
                "description": "Imported",
                "publish_target": "",
                "locale": "ru_RU",
                "time_zone": "UTC",
                "rewrite_model": "gpt-4o-mini",
                "image_prompt_model": "gpt-4o-mini",
                "image_model": "gpt-image-1",
                "image_size": "1024x1024",
                "image_quality": "medium",
                "retention_days": 30,
                "collector_enabled": False,
                "collector_telegram_interval": 300,
                "collector_web_interval": 300,
                "is_active": True,
            },
            "prompt_config": {},
            "sources": [],
            "web_presets": [],
        }

    def test_imports_all_projects_from_private_dir(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "importer"
            alpha_dir = root / "alpha-1"
            beta_dir = root / "beta-2"
            alpha_dir.mkdir(parents=True, exist_ok=True)
            beta_dir.mkdir(parents=True, exist_ok=True)
            (alpha_dir / "project-export.json").write_text(
                json.dumps(self._payload(name="Alpha"), ensure_ascii=False),
                encoding="utf-8",
            )
            (beta_dir / "project-export.json").write_text(
                json.dumps(self._payload(name="Beta"), ensure_ascii=False),
                encoding="utf-8",
            )

            with override_settings(PAPERBIRD_PRIVATE_ARTIFACTS_DIR=Path(tmpdir)):
                call_command("import_private_artifacts", self.user.username)

        self.assertTrue(Project.objects.filter(owner=self.user, name="Alpha").exists())
        self.assertTrue(Project.objects.filter(owner=self.user, name="Beta").exists())

    def test_imports_single_project_by_dir(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "importer"
            alpha_dir = root / "alpha-1"
            alpha_dir.mkdir(parents=True, exist_ok=True)
            (alpha_dir / "project-export.json").write_text(
                json.dumps(self._payload(name="Alpha"), ensure_ascii=False),
                encoding="utf-8",
            )

            with override_settings(PAPERBIRD_PRIVATE_ARTIFACTS_DIR=Path(tmpdir)):
                call_command(
                    "import_private_artifacts",
                    self.user.username,
                    "--project-dir",
                    "alpha-1",
                )

        self.assertTrue(Project.objects.filter(owner=self.user, name="Alpha").exists())

    def test_raises_for_missing_project_dir(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with override_settings(PAPERBIRD_PRIVATE_ARTIFACTS_DIR=Path(tmpdir)):
                with self.assertRaisesMessage(CommandError, "Папка проекта не найдена:"):
                    call_command(
                        "import_private_artifacts",
                        self.user.username,
                        "--project-dir",
                        "missing",
                    )
