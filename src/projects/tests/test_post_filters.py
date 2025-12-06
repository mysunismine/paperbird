from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import User
from projects.models import Post, Project, Source
from projects.services.post_filters import (
    PostFilterOptions,
    apply_post_filters,
    collect_keyword_hits,
    summarize_keyword_hits,
)


class PostFilterServiceTests(TestCase):
    """Проверяет расширенные фильтры постов и ключевых слов."""

    def setUp(self) -> None:
        self.user = User.objects.create_user("analyst", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Новости")
        self.source_primary = Source.objects.create(
            project=self.project,
            telegram_id=101,
            title="Технологические новости",
            username="technews",
        )
        self.source_secondary = Source.objects.create(
            project=self.project,
            telegram_id=202,
            title="Политика",
            username="politics",
        )
        now = timezone.now()
        self.post_new = Post.objects.create(
            project=self.project,
            source=self.source_primary,
            telegram_id=1,
            message="Apple представила новую серию устройств на презентации",
            posted_at=now - timedelta(hours=1),
            status=Post.Status.NEW,
            has_media=True,
            raw={"media": []},
        )
        self.post_used = Post.objects.create(
            project=self.project,
            source=self.source_primary,
            telegram_id=2,
            message="Google объявила о запуске сервиса на территории России",
            posted_at=now - timedelta(days=1),
            status=Post.Status.USED,
            has_media=False,
            raw={},
        )
        self.post_other_source = Post.objects.create(
            project=self.project,
            source=self.source_secondary,
            telegram_id=3,
            message="Парламент обсудил новые меры поддержки экономики",
            posted_at=now - timedelta(days=2),
            status=Post.Status.NEW,
            has_media=False,
            raw={},
        )

    def test_filter_by_status_and_media(self) -> None:
        options = PostFilterOptions(
            statuses={Post.Status.NEW},
            has_media=True,
        )
        queryset = apply_post_filters(
            Post.objects.filter(project=self.project).select_related("source"),
            options,
        )
        self.assertEqual(list(queryset), [self.post_new])

    def test_filter_by_search_terms(self) -> None:
        options = PostFilterOptions(search="Apple презентации")
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertEqual(list(queryset), [self.post_new])

    def test_filter_by_keywords_include_and_exclude(self) -> None:
        options = PostFilterOptions(
            include_keywords={"запуск"},
            exclude_keywords={"России"},
        )
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertEqual(list(queryset), [])

        options = PostFilterOptions(include_keywords={"Парламент", "запуск"})
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertCountEqual(list(queryset), [self.post_used, self.post_other_source])

    def test_filter_by_date_interval_and_source(self) -> None:
        options = PostFilterOptions(
            date_from=timezone.now() - timedelta(days=1, hours=12),
            date_to=timezone.now(),
            source_ids={self.source_primary.id},
        )
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertCountEqual(list(queryset), [self.post_new, self.post_used])

    def test_keyword_hits_summary(self) -> None:
        options = PostFilterOptions(include_keywords={"презентации", "поддержки"})
        queryset = apply_post_filters(
            Post.objects.filter(project=self.project).select_related("source"),
            options,
        )
        posts = list(queryset)
        hits = collect_keyword_hits(posts, options.highlight_keywords)
        summary = summarize_keyword_hits(posts, options.highlight_keywords)

        self.assertIn(self.post_new.id, hits)
        self.assertEqual(hits[self.post_new.id], ["презентации"])
        self.assertEqual(summary, {"презентации": 1, "поддержки": 1})

    def test_invalid_status_raises_error(self) -> None:
        options = PostFilterOptions(statuses={"unknown"})
        with self.assertRaisesMessage(ValueError, "Неизвестные статусы постов"):
            apply_post_filters(Post.objects.filter(project=self.project), options)
