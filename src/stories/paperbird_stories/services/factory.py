"""Создание сюжетов из выбранных постов."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from projects.models import Post, Project
from projects.services.media_downloader import ensure_post_media_local
from stories.paperbird_stories.models import Story

from .exceptions import StoryCreationError


@dataclass(slots=True)
class StoryFactory:
    """Создаёт сюжет на основе выбранных постов."""

    project: Project

    def create(
        self,
        *,
        post_ids: Sequence[int],
        title: str = "",
        editor_comment: str = "",
    ) -> Story:
        """Создает сюжет и прикрепляет к нему посты."""
        if not post_ids:
            raise StoryCreationError("Список постов пуст")
        order_map = {post_id: index for index, post_id in enumerate(post_ids)}
        posts = list(
            Post.objects.filter(project=self.project, id__in=post_ids).order_by("id")
        )
        if len(order_map) != len(post_ids):
            raise StoryCreationError("Список постов содержит повторяющиеся значения")
        if len(posts) != len(post_ids):
            missing = set(post_ids) - {post.id for post in posts}
            raise StoryCreationError(
                f"Посты не найдены или не принадлежат проекту: {sorted(missing)}"
            )
        posts.sort(key=lambda post: order_map[post.id])
        for post in posts:
            ensure_post_media_local(post)

        story = Story.objects.create(
            project=self.project,
            title=title.strip(),
            editor_comment=editor_comment.strip(),
        )
        story.attach_posts(posts)
        return story
