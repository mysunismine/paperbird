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

    def test_library_filters_by_media_type(self) -> None:
        """Фильтр типа медиа ограничивает выдачу."""
        image_asset = self._upload("photo.jpg", title="Photo")
        video_asset = self._upload("clip.mp4", title="Clip")
        gif_asset = self._upload("loop.gif", title="Loop")

        response = self.client.get(
            reverse("media_library:library"),
            {"project": self.project.id, "type": "video"},
        )
        page = response.context["page_obj"].object_list
        self.assertIn(video_asset, page)
        self.assertNotIn(image_asset, page)
        self.assertNotIn(gif_asset, page)

        response = self.client.get(
            reverse("media_library:library"),
            {"project": self.project.id, "type": "gif"},
        )
        page = response.context["page_obj"].object_list
        self.assertIn(gif_asset, page)
        self.assertNotIn(image_asset, page)
        self.assertNotIn(video_asset, page)

    def test_assets_json_endpoint_filters_by_media_type(self) -> None:
        """JSON-эндпоинт учитывает фильтр типа медиа."""
        image_asset = self._upload("photo.jpg", title="Photo")
        video_asset = self._upload("clip.mp4", title="Clip")

        response = self.client.get(
            reverse("media_library:library_assets"),
            {"project": self.project.id, "type": "image"},
        )
        items = response.json()["items"]
        ids = {item["id"] for item in items}
        self.assertIn(image_asset.id, ids)
        self.assertNotIn(video_asset.id, ids)

    def test_bulk_update_assets(self) -> None:
        """Массовое обновление названий и тегов."""
        asset_one = self._upload("photo.jpg", title="Photo", tags=["one"])
        asset_two = self._upload("clip.mp4", title="Clip", tags=[])

        response = self.client.post(
            reverse("media_library:library"),
            {
                "action": "bulk_update",
                "project": self.project.id,
                "asset_ids": [asset_one.id, asset_two.id],
                "bulk_title": "Common",
                "bulk_tags": "alpha, beta",
                "append_tags": "1",
            },
        )
        self.assertEqual(response.status_code, 302)

        asset_one.refresh_from_db()
        asset_two.refresh_from_db()
        self.assertEqual(asset_one.title, "Common")
        self.assertEqual(asset_two.title, "Common")
        self.assertEqual(asset_one.tags, ["one", "alpha", "beta"])
        self.assertEqual(asset_two.tags, ["alpha", "beta"])

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
