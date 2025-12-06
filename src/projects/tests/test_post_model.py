from django.test import TestCase
from django.utils import timezone

from projects.models import Post, Project, Source

from . import User


class PostDisplayMessageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("reader", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Лента")
        self.web_source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Website",
        )
        self.telegram_source = Source.objects.create(
            project=self.project,
            type=Source.Type.TELEGRAM,
            telegram_id=555,
            title="Канал",
        )

    def test_web_post_combines_title_and_body(self) -> None:
        post = Post.objects.create(
            project=self.project,
            source=self.web_source,
            origin_type=Post.Origin.WEB,
            message="Первый абзац статьи",
            external_metadata={"title": "Заголовок материала"},
            posted_at=timezone.now(),
        )
        self.assertEqual(post.display_message, "Заголовок материала\n\nПервый абзац статьи")

    def test_web_post_does_not_duplicate_existing_title(self) -> None:
        original_text = "Заголовок материала\n\nОсновной текст"
        post = Post.objects.create(
            project=self.project,
            source=self.web_source,
            origin_type=Post.Origin.WEB,
            message=original_text,
            external_metadata={"title": "Заголовок материала"},
            posted_at=timezone.now(),
        )
        self.assertEqual(post.display_message, original_text)

    def test_telegram_post_returns_original_text(self) -> None:
        post = Post.objects.create(
            project=self.project,
            source=self.telegram_source,
            origin_type=Post.Origin.TELEGRAM,
            message="Новость из канала",
            posted_at=timezone.now(),
        )
        self.assertEqual(post.display_message, "Новость из канала")
