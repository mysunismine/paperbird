import hashlib
import json
from http import HTTPStatus
from unittest.mock import ANY, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import (
    IMAGE_DEFAULT_MODEL,
    IMAGE_DEFAULT_QUALITY,
    IMAGE_DEFAULT_SIZE,
    IMAGE_MODEL_CHOICES,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
    REWRITE_DEFAULT_MODEL,
    REWRITE_MODEL_CHOICES,
)
from core.models import WorkerTask
from projects.models import Post, Project, ProjectPromptConfig, Source, WebPreset
from projects.services.prompt_config import ensure_prompt_config
from stories.paperbird_stories.services import StoryFactory

from . import User, make_preset_payload


class ProjectListViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("manager", password="secret")
        self.client.force_login(self.user)
        self.project_main = Project.objects.create(owner=self.user, name="Основной")
        self.project_extra = Project.objects.create(owner=self.user, name="Резерв")
        source = Source.objects.create(project=self.project_main, telegram_id=10)
        post = Post.objects.create(
            project=self.project_main,
            source=source,
            telegram_id=1,
            message="Новость",
            posted_at=timezone.now(),
        )
        story = StoryFactory(project=self.project_main).create(post_ids=[post.id])
        story.apply_rewrite(
            title="Заголовок",
            summary="",
            body="Текст",
            hashtags=[],
            sources=[],
            payload={},
        )

    def test_project_list_page(self) -> None:
        response = self.client.get(reverse("projects:list"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Основной")
        self.assertContains(response, "Лента постов")
        self.assertContains(response, "Источники")
        self.assertContains(response, "Настройки")
        self.assertContains(response, "Создать проект")
        self.assertNotContains(response, "Создать сюжет")


class ProjectCreateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("owner", password="secret")
        self.client.force_login(self.user)

    def test_get_create_page(self) -> None:
        response = self.client.get(reverse("projects:create"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Новый проект")
        self.assertContains(response, "Сохранить проект")

    def test_post_creates_project_and_redirects(self) -> None:
        alt_model = IMAGE_MODEL_CHOICES[1][0]
        alt_size = IMAGE_SIZE_CHOICES[1][0]
        alt_quality = IMAGE_QUALITY_CHOICES[2][0]
        rewrite_choice = REWRITE_MODEL_CHOICES[1][0]
        response = self.client.post(
            reverse("projects:create"),
            data={
                "name": "Мониторинг",
                "description": "Telegram-лента",
                "publish_target": "@paperbird",
                "locale": "ru_RU",
                "time_zone": "Europe/Moscow",
                "rewrite_model": rewrite_choice,
                "image_model": alt_model,
                "image_size": alt_size,
                "image_quality": alt_quality,
                "retention_days": 45,
                "collector_telegram_interval": 60,
                "collector_web_interval": 300,
            },
            follow=True,
        )
        self.assertContains(response, "Проект «Мониторинг» создан.")
        project = Project.objects.get(owner=self.user, name="Мониторинг")
        self.assertEqual(project.publish_target, "@paperbird")
        self.assertEqual(project.retention_days, 45)
        self.assertEqual(project.rewrite_model, rewrite_choice)
        self.assertEqual(project.image_model, alt_model)
        self.assertEqual(project.image_size, alt_size)
        self.assertEqual(project.image_quality, alt_quality)

    def test_duplicate_name_validation(self) -> None:
        Project.objects.create(owner=self.user, name="Мониторинг")
        response = self.client.post(
            reverse("projects:create"),
            data={
                "name": "Мониторинг",
                "description": "",
                "publish_target": "",
                "locale": "ru_RU",
                "time_zone": "UTC",
                "rewrite_model": REWRITE_DEFAULT_MODEL,
                "image_model": IMAGE_DEFAULT_MODEL,
                "image_size": IMAGE_DEFAULT_SIZE,
                "image_quality": IMAGE_DEFAULT_QUALITY,
                "retention_days": 90,
                "collector_telegram_interval": 60,
                "collector_web_interval": 300,
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form = response.context["form"]
        self.assertFormError(
            form,
            "name",
            "У вас уже есть проект с таким названием. Выберите другое.",
        )


class ProjectSettingsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="secret")
        self.other = User.objects.create_user("viewer", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Новости",
            publish_target="@old",
            retention_days=30,
        )

    def test_get_settings_page(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("projects:settings", args=[self.project.pk])
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Настройки проекта")
        self.assertContains(response, "@old")
        self.assertContains(response, "Перейти к промтам")

    def test_post_updates_settings(self) -> None:
        self.client.force_login(self.user)
        new_model = IMAGE_MODEL_CHOICES[1][0]
        new_size = IMAGE_SIZE_CHOICES[-1][0]
        new_quality = IMAGE_QUALITY_CHOICES[2][0]
        new_rewrite = REWRITE_MODEL_CHOICES[-1][0]
        response = self.client.post(
            reverse("projects:settings", args=[self.project.pk]),
            data={
                "name": "Новости",
                "description": "Обновлённое описание",
                "publish_target": "@fresh",
                "locale": "ru_RU",
                "time_zone": "Europe/Moscow",
                "rewrite_model": new_rewrite,
                "image_model": new_model,
                "image_size": new_size,
                "image_quality": new_quality,
                "retention_days": 60,
                "collector_telegram_interval": 90,
                "collector_web_interval": 240,
            },
            follow=True,
        )
        self.assertContains(response, "Настройки проекта «Новости» обновлены.")
        self.project.refresh_from_db()
        self.assertEqual(self.project.publish_target, "@fresh")
        self.assertEqual(self.project.retention_days, 60)
        self.assertEqual(self.project.description, "Обновлённое описание")
        self.assertEqual(self.project.rewrite_model, new_rewrite)
        self.assertEqual(self.project.image_model, new_model)
        self.assertEqual(self.project.image_size, new_size)
        self.assertEqual(self.project.image_quality, new_quality)

    def test_other_user_cannot_access(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(
            reverse("projects:settings", args=[self.project.pk])
        )
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)


class ProjectPromptsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("prompts", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(
            owner=self.user,
            name="Редакция",
            description="Новости технологий",
        )
        ensure_prompt_config(self.project)

    def _form_payload(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        config = self.project.prompt_config
        data = {
            "system_role": config.system_role,
            "task_instruction": config.task_instruction,
            "documents_intro": config.documents_intro,
            "style_requirements": config.style_requirements,
            "output_format": config.output_format,
            "output_example": config.output_example,
            "editor_comment_note": config.editor_comment_note,
            "image_prompt_template": config.image_prompt_template,
        }
        if overrides:
            data.update(overrides)
        return data

    def test_prompts_page_lists_sections(self) -> None:
        response = self.client.get(reverse("projects:prompts", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "1. [СИСТЕМНАЯ РОЛЬ]")
        self.assertContains(response, "{{PROJECT_NAME}}")
        self.assertContains(response, "Доступные плейсхолдеры")

    def test_prompt_update_persists(self) -> None:
        url = reverse("projects:prompts", args=[self.project.id])
        response = self.client.post(
            url,
            data=self._form_payload(
                {"system_role": "Ты — редактор {{PROJECT_NAME}} и ведёшь канал."}
            ),
            follow=True,
        )
        self.assertContains(response, "Промт проекта «Редакция» сохранён.")
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.prompt_config.system_role,
            "Ты — редактор {{PROJECT_NAME}} и ведёшь канал.",
        )

    def test_default_config_created_when_missing(self) -> None:
        ProjectPromptConfig.objects.filter(project=self.project).delete()
        self.project = Project.objects.get(pk=self.project.pk)
        response = self.client.get(reverse("projects:prompts", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project.refresh_from_db()
        self.assertTrue(hasattr(self.project, "prompt_config"))
        self.assertIn(
            "{{PROJECT_NAME}}",
            self.project.prompt_config.system_role,
        )

    def test_export_contains_sections_in_order(self) -> None:
        url = reverse("projects:prompts-export", args=[self.project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("1. [СИСТЕМНАЯ РОЛЬ]"))
        self.assertIn("5. [ФОРМАТ ОТВЕТА — JSON]", body)

    def test_import_updates_prompt_config(self) -> None:
        url = reverse("projects:prompts-import", args=[self.project.id])
        payload = {
            "prompt_config": {
                "system_role": "Новый системный промпт",
                "task_instruction": "Новая инструкция",
                "documents_intro": "Документы",
                "style_requirements": "Стиль",
                "output_format": "Формат",
                "output_example": "Пример",
                "editor_comment_note": "Комментарий",
                "image_prompt_template": "Шаблон картинки",
            }
        }
        upload = SimpleUploadedFile(
            "prompt.json",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
        response = self.client.post(url, data={"prompt_file": upload}, follow=True)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.prompt_config.system_role, "Новый системный промпт")
        self.assertEqual(self.project.prompt_config.image_prompt_template, "Шаблон картинки")


class ProjectExportViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("exporter", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(
            owner=self.user,
            name="Экспорт",
            publish_target="@export",
        )
        preset_data = make_preset_payload("site_feed")
        checksum = hashlib.sha256(
            json.dumps(preset_data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.preset = WebPreset.objects.create(
            name=preset_data["name"],
            version=preset_data["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=preset_data,
        )
        Source.objects.create(
            project=self.project,
            type=Source.Type.TELEGRAM,
            title="Telegram",
            username="news",
            retention_days=5,
            is_active=True,
        )
        Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Web",
            web_preset=self.preset,
            web_preset_snapshot=preset_data,
            retention_days=7,
            is_active=False,
        )

    def test_export_returns_json_payload(self) -> None:
        url = reverse("projects:export", args=[self.project.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        payload = json.loads(response.content)
        self.assertEqual(payload["project"]["name"], "Экспорт")
        self.assertEqual(payload["project"]["publish_target"], "@export")
        self.assertIn("image_prompt_template", payload["prompt_config"])
        self.assertEqual(len(payload["sources"]), 2)
        snapshot = payload["sources"][1]["web_preset_snapshot"]
        self.assertEqual(snapshot["name"], "site_feed")
        self.assertEqual(payload["web_presets"][0]["name"], "site_feed")

    def test_export_returns_yaml_payload(self) -> None:
        url = reverse("projects:export", args=[self.project.pk])
        response = self.client.get(f"{url}?format=yaml")
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response["Content-Type"], "text/yaml; charset=utf-8")
        import yaml

        payload = yaml.safe_load(response.content)
        self.assertEqual(payload["project"]["name"], "Экспорт")


class ProjectSourcesViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("curator", password="secret")
        self.other = User.objects.create_user("reader", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Мониторинг")

    def test_get_sources_page(self) -> None:
        response = self.client.get(reverse("projects:sources", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Источники проекта")
        self.assertContains(response, "Добавить источник")

    def test_delete_source(self) -> None:
        source = Source.objects.create(project=self.project, title="Temp", username="temp")
        response = self.client.post(
            reverse("projects:sources", args=[self.project.pk]),
            data={
                "action": "delete",
                "source_id": source.pk,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertFalse(Source.objects.filter(pk=source.pk).exists())

    def test_other_user_cannot_access(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(reverse("projects:sources", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)


class ProjectSourceCreateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("curator", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Мониторинг")

    def test_get_create_page(self) -> None:
        response = self.client.get(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk})
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Добавить источник")

    @patch("projects.forms.enqueue_source_refresh")
    def test_post_creates_source(self, mock_refresh) -> None:
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
                "title": "Tech",
                "telegram_id": "",
                "username": "https://t.me/technews",
                "invite_link": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 15,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        source = Source.objects.get(project=self.project, username="technews")
        self.assertIsNone(source.telegram_id)
        mock_refresh.assert_called_once_with(source)

    @patch("projects.forms.enqueue_source_refresh")
    def test_username_from_s_path_normalized(self, mock_refresh) -> None:
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
                "title": "News",
                "telegram_id": "",
                "username": "https://t.me/s/bazabazon",
                "invite_link": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 10,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        source = Source.objects.get(project=self.project)
        self.assertEqual(source.username, "bazabazon")
        mock_refresh.assert_called_once()

    @patch("projects.forms.enqueue_source_refresh")
    def test_invite_link_detection_from_username_field(self, mock_refresh) -> None:
        self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
                "title": "Private",
                "telegram_id": "",
                "username": "https://t.me/+abcdef",
                "invite_link": "",
                "retention_days": 7,
            },
            follow=True,
        )
        source = Source.objects.get(project=self.project, title="Private")
        self.assertEqual(source.invite_link, "https://t.me/+abcdef")
        mock_refresh.assert_called_once()

    @patch("projects.forms.enqueue_source_refresh")
    def test_create_source_autofills_title(self, mock_refresh) -> None:
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
                "title": "",
                "telegram_id": "",
                "username": "https://t.me/techsource",
                "invite_link": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 12,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        created = Source.objects.get(project=self.project, username="techsource")
        self.assertEqual(created.title, "@techsource")
        mock_refresh.assert_called_once_with(created)

    @patch("projects.views.feed.enqueue_task")
    def test_web_source_schedules_collection(self, mock_enqueue) -> None:
        payload = json.dumps(make_preset_payload("site_feed"))
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.WEB,
                "title": "Сайт",
                "preset_payload": payload,
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 30,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        created = Source.objects.get(project=self.project)
        self.assertEqual(created.type, Source.Type.WEB)
        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        self.assertEqual(args[0], WorkerTask.Queue.COLLECTOR_WEB)
        payload_sent = kwargs["payload"]
        self.assertEqual(payload_sent["project_id"], self.project.pk)
        self.assertEqual(payload_sent["source_id"], created.pk)

    @patch("projects.views.feed.enqueue_task", side_effect=RuntimeError("boom"))
    def test_web_source_enqueue_failure_shows_message(self, mock_enqueue) -> None:
        payload = json.dumps(make_preset_payload("site_feed"))
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.WEB,
                "title": "Сайт",
                "preset_payload": payload,
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 30,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "не удалось запустить парсер")
        mock_enqueue.assert_called_once()


class ProjectSourceUpdateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="secret")
        self.other = User.objects.create_user("outsider", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Апдейты")
        self.source = Source.objects.create(
            project=self.project,
            title="Новости",
            username="news",
            retention_days=5,
        )

    def test_get_edit_page(self) -> None:
        url = reverse("projects:source-edit", args=[self.project.pk, self.source.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Редактирование источника")
        self.assertContains(response, "Новости")

    @patch("projects.forms.enqueue_source_refresh")
    def test_post_updates_source(self, mock_refresh) -> None:
        url = reverse("projects:source-edit", args=[self.project.pk, self.source.pk])
        response = self.client.post(
            url,
            data={
                "type": Source.Type.TELEGRAM,
                "title": "",
                "username": "@updated",
                "invite_link": "",
                "telegram_id": "",
                "deduplicate_text": "on",
                "deduplicate_media": "",
                "retention_days": 12,
            },
        )
        self.assertRedirects(response, reverse("projects:sources", args=[self.project.pk]))
        self.source.refresh_from_db()
        self.assertEqual(self.source.title, "@updated")
        self.assertEqual(self.source.username, "updated")
        self.assertEqual(self.source.retention_days, 12)
        mock_refresh.assert_called_once_with(self.source)

    def test_other_user_cannot_edit(self) -> None:
        self.client.force_login(self.other)
        url = reverse("projects:source-edit", args=[self.project.pk, self.source.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)


class ProjectCollectorQueueViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("ops", password="secret")
        self.other = User.objects.create_user("guest", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Мониторинг")
        self.payload = {"project_id": self.project.pk}

    def _make_task(self, **overrides):
        defaults = {
            "queue": WorkerTask.Queue.COLLECTOR,
            "payload": self.payload,
            "status": WorkerTask.Status.QUEUED,
        }
        defaults.update(overrides)
        return WorkerTask.objects.create(**defaults)

    def test_queue_view_lists_tasks(self) -> None:
        self._make_task()
        self._make_task(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            status=WorkerTask.Status.RUNNING,
        )
        response = self.client.get(reverse("projects:queue", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Очередь коллектора проекта")
        self.assertContains(response, "Telegram")
        self.assertContains(response, "Web")

    def test_other_user_cannot_view_queue(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(reverse("projects:queue", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_cancel_task_via_ui(self) -> None:
        task = self._make_task()
        response = self.client.post(
            reverse("projects:queue", args=[self.project.pk]),
            data={"action": "cancel_task", "task_id": str(task.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.CANCELLED)

    @patch("projects.views.feed.enqueue_task")
    def test_retry_task_enqueues_new(self, mock_enqueue) -> None:
        task = self._make_task(status=WorkerTask.Status.SUCCEEDED)
        response = self.client.post(
            reverse("projects:queue", args=[self.project.pk]),
            data={"action": "retry_task", "task_id": str(task.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        mock_enqueue.assert_called_once_with(
            task.queue,
            payload=task.payload,
            scheduled_for=ANY,
        )
