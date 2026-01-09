"""Тесты для медиабиблиотеки."""

from __future__ import annotations

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from media_library.models import MediaAsset
from projects.models import Project

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class MediaLibraryTests(TestCase):
    """Проверяет фильтры и API медиабиблиотеки."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(username="tester", password="password")
        self.project = Project.objects.create(owner=self.user, name="Test Project")
        self.client = Client()
        self.client.force_login(self.user)

    def _upload(self, filename: str, *, title: str, tags=None, source_kind=None) -> MediaAsset:
        tags = tags or []
        source_kind = source_kind or MediaAsset.SourceKind.UPLOAD
        content = SimpleUploadedFile(filename, b"file-bytes")
        return MediaAsset.objects.create(
            project=self.project,
            created_by=self.user,
            image_file=content,
            title=title,
            tags=tags,
            source_kind=source_kind,
        )

    def test_media_type_autodetects_gif_and_video(self) -> None:
        """Автоопределение типа медиа по имени файла."""
        gif_asset = self._upload("cat.gif", title="Cat")
        video_asset = self._upload("clip.mp4", title="Clip")
        image_asset = self._upload("photo.jpg", title="Photo")

        self.assertEqual(gif_asset.media_type, MediaAsset.MediaType.GIF)
        self.assertEqual(video_asset.media_type, MediaAsset.MediaType.VIDEO)
        self.assertEqual(image_asset.media_type, MediaAsset.MediaType.IMAGE)

    def test_library_filters_by_tags_and_query(self) -> None:
        """Фильтрация по тегам и строке поиска."""
        cat = self._upload("cat.jpg", title="Cat", tags=["cat", "cute"])
        dog = self._upload("dog.jpg", title="Dog", tags=["dog"])
        imported = self._upload(
            "imported.jpg",
            title="Imported",
            tags=["cat"],
            source_kind=MediaAsset.SourceKind.IMPORTED,
        )

        response = self.client.get(
            reverse("media_library:library"),
            {"project": self.project.id, "tags": "cat,cute"},
        )
        page = response.context["page_obj"].object_list
        self.assertIn(cat, page)
        self.assertNotIn(dog, page)
        self.assertNotIn(imported, page)

        response = self.client.get(
            reverse("media_library:library"),
            {"project": self.project.id, "q": "cat"},
        )
        page = response.context["page_obj"].object_list
        self.assertIn(cat, page)
        self.assertNotIn(dog, page)
        self.assertNotIn(imported, page)

    def test_assets_json_endpoint_paginates(self) -> None:
        """JSON-эндпоинт отдает пагинацию и флаги следующей страницы."""
        for index in range(25):
            self._upload(f"asset_{index}.jpg", title=f"Asset {index}")

        response = self.client.get(
            reverse("media_library:library_assets"),
            {"project": self.project.id, "page": 1},
        )
        payload = response.json()
        self.assertEqual(len(payload["items"]), 24)
        self.assertTrue(payload["has_next"])
        self.assertEqual(payload["next_page"], 2)

    def test_tag_suggestions_endpoint_excludes_selected(self) -> None:
        """Подсказки тегов исключают уже выбранные и фильтруются по префиксу."""
        self._upload("cat.jpg", title="Cat", tags=["cat", "camera", "car"])
        response = self.client.get(
            reverse("media_library:library_tags"),
            {"project": self.project.id, "tags": "cat", "suggest": "ca"},
        )
        items = response.json()["items"]
        self.assertIn("camera", items)
        self.assertIn("car", items)
        self.assertNotIn("cat", items)
