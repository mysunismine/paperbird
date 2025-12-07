"""Views for managing story publications."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView

from projects.models import Project
from stories.paperbird_stories.forms import PublicationManageForm
from stories.paperbird_stories.models import Publication


class PublicationListView(LoginRequiredMixin, ListView):
    """Отображает публикации пользователя."""

    model = Publication
    template_name = "stories/publication_list.html"
    context_object_name = "publications"
    paginate_by = 25
    _page_override: str | None = None

    def get_queryset(self):
        return (
            Publication.objects.filter(story__project__owner=self.request.user)
            .select_related("story", "story__project")
            .order_by("-created_at")
        )

    def paginate_queryset(self, queryset, page_size):
        paginator = self.get_paginator(
            queryset,
            page_size,
            allow_empty_first_page=self.get_allow_empty(),
        )
        page_number = (
            self.kwargs.get(self.page_kwarg)
            or self.request.GET.get(self.page_kwarg)
            or self._page_override
        )
        page_obj = paginator.get_page(page_number)
        self._page_override = None
        return paginator, page_obj, page_obj.object_list, page_obj.has_other_pages()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        bound_form = kwargs.pop("bound_form", None)
        context = super().get_context_data(**kwargs)
        publications = context.get("publications", [])
        context["publication_forms"] = self._build_forms(publications, bound_form=bound_form)
        context["projects"] = (
            Project.objects.filter(owner=self.request.user)
            .order_by("name")
        )
        return context

    def post(self, request, *args, **kwargs):
        publication = self._get_publication(request.POST.get("publication_id"))
        page = request.POST.get("page") or ""
        submit_action = request.POST.get("submit_action", "save")
        if submit_action == "delete":
            title = publication.story.title or f"Сюжет #{publication.story_id}"
            publication.delete()
            messages.success(request, f"Публикация сюжета «{title}» удалена.")
            return self._redirect_to_page(page)

        form = PublicationManageForm(
            request.POST,
            instance=publication,
            prefix=self._form_prefix(publication),
        )
        if form.is_valid():
            updated = form.save()
            display_title = updated.story.title or f"Сюжет #{updated.story_id}"
            messages.success(
                request,
                f"Настройки публикации для сюжета «{display_title}» сохранены.",
            )
            return self._redirect_to_page(page)

        messages.error(request, "Исправьте ошибки в форме публикации.")
        self.object_list = self.get_queryset()
        self._page_override = page or None
        context = self.get_context_data(bound_form=form)
        return self.render_to_response(context)

    def _form_prefix(self, publication: Publication) -> str:
        return f"publication-{publication.pk}"

    def _build_forms(
        self,
        publications: Sequence[Publication],
        *,
        bound_form: PublicationManageForm | None = None,
    ) -> list[tuple[Publication, PublicationManageForm]]:
        forms: list[tuple[Publication, PublicationManageForm]] = []
        bound_pk = bound_form.instance.pk if bound_form is not None else None
        for publication in publications:
            if bound_pk == publication.pk and bound_form is not None:
                forms.append((publication, bound_form))
            else:
                forms.append(
                    (
                        publication,
                        PublicationManageForm(
                            instance=publication,
                            prefix=self._form_prefix(publication),
                        ),
                    )
                )
        return forms

    def _get_publication(self, identifier: str | None) -> Publication:
        if not identifier or not str(identifier).isdigit():
            raise Http404("Публикация не найдена")
        return get_object_or_404(
            Publication.objects.select_related("story", "story__project"),
            pk=int(identifier),
            story__project__owner=self.request.user,
        )

    def _redirect_to_page(self, page: str | None):
        url = self.request.path
        if page and page not in {"", "1"}:
            return redirect(f"{url}?{self.page_kwarg}={page}")
        return redirect(url)
