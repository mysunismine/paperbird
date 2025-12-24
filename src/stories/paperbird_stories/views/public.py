"""Public views for published stories."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Q
from django.http import Http404
from django.views.generic import DetailView, ListView

from projects.models import Project
from stories.paperbird_stories.models import Publication, StoryImage


class PublicIndexView(ListView):
    """Public directory of projects with published content."""

    model = Project
    template_name = "public/index.html"
    context_object_name = "projects"
    paginate_by = 18

    def get_queryset(self):
        return (
            Project.objects.filter(public_enabled=True)
            .annotate(
                published_count=Count(
                    "stories__publications",
                    filter=Q(stories__publications__status=Publication.Status.PUBLISHED),
                    distinct=True,
                )
            )
            .order_by("name")
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["public_noindex"] = True
        return context


class PublicProjectView(ListView):
    """Public list of published stories for a project."""

    model = Publication
    template_name = "public/project.html"
    context_object_name = "publications"
    paginate_by = 12

    def dispatch(self, request, *args, **kwargs):
        self.project = self._get_project()
        return super().dispatch(request, *args, **kwargs)

    def _get_project(self) -> Project:
        project_id = self.kwargs.get("project_id")
        try:
            return Project.objects.get(pk=project_id, public_enabled=True)
        except Project.DoesNotExist as exc:
            raise Http404("Проект не найден") from exc

    def get_queryset(self):
        return (
            Publication.objects.select_related("story", "story__project")
            .prefetch_related("story__images")
            .filter(
                story__project_id=self.project.pk,
                status=Publication.Status.PUBLISHED,
            )
            .order_by("-published_at", "-created_at")
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        context["public_noindex"] = self.project.public_noindex
        context["public_title"] = self.project.public_title or self.project.name
        context["og_description"] = self._excerpt(self.project.description or "", limit=160)
        publication_cards = []
        og_image = None
        for publication in context.get("publications", []):
            image = self._preview_image(publication)
            if image and og_image is None:
                og_image = image.image_file.url
            publication_cards.append(
                {
                    "publication": publication,
                    "title": self._publication_title(publication),
                    "text": self._publication_text(publication),
                    "excerpt": self._excerpt(self._publication_text(publication)),
                    "image": image,
                }
            )
        context["publication_cards"] = publication_cards
        context["og_image"] = og_image
        if og_image:
            context["og_image_url"] = self.request.build_absolute_uri(og_image)
        return context

    @staticmethod
    def _publication_title(publication: Publication) -> str:
        return publication.story.title or f"Сюжет #{publication.story_id}"

    @staticmethod
    def _publication_text(publication: Publication) -> str:
        text = (publication.result_text or "").strip()
        if text:
            return text
        return (publication.story.body or "").strip()

    @staticmethod
    def _excerpt(text: str, *, limit: int = 220) -> str:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return "Без текста"
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[:limit].rstrip()}…"

    @staticmethod
    def _preview_image(publication: Publication) -> StoryImage | None:
        story = publication.story
        return story.images.filter(is_selected=True).first() or story.images.first()


class PublicPublicationView(DetailView):
    """Public detail page for a published story."""

    model = Publication
    template_name = "public/publication.html"
    context_object_name = "publication"
    pk_url_kwarg = "publication_id"

    def get_queryset(self):
        project_id = self.kwargs.get("project_id")
        return (
            Publication.objects.select_related("story", "story__project")
            .prefetch_related("story__images")
            .filter(
                story__project_id=project_id,
                story__project__public_enabled=True,
                status=Publication.Status.PUBLISHED,
            )
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        publication: Publication = context["publication"]
        story = publication.story
        context["project"] = story.project
        context["title"] = story.title or f"Сюжет #{story.pk}"
        context["text"] = self._publication_text(publication)
        context["images"] = story.images.filter(is_selected=True)
        context["public_noindex"] = story.project.public_noindex
        context["public_title"] = story.project.public_title or story.project.name
        context["message_url"] = publication.message_url()
        context["og_description"] = PublicProjectView._excerpt(context["text"], limit=160)
        context["og_image"] = self._preview_image(story)
        if context["og_image"]:
            context["og_image_url"] = self.request.build_absolute_uri(
                context["og_image"].image_file.url
            )
        return context

    @staticmethod
    def _publication_text(publication: Publication) -> str:
        text = (publication.result_text or "").strip()
        if text:
            return text
        return (publication.story.body or "").strip()

    @staticmethod
    def _preview_image(story) -> StoryImage | None:
        return story.images.filter(is_selected=True).first() or story.images.first()
