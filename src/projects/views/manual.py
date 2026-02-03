"""Views for manual post creation."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView

from projects.forms import ManualPostForm
from projects.models import Post, Project, Source


class ManualPostCreateView(LoginRequiredMixin, FormView):
    """Создание поста из вручную добавленного текста."""

    template_name = "projects/manual_post_form.html"
    form_class = ManualPostForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.project = get_object_or_404(
            Project.objects.accessible_by(request.user),
            pk=kwargs["project_pk"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return context

    def form_valid(self, form):
        title = form.cleaned_data["title"].strip()
        body = form.cleaned_data["body"]
        source = (
            Source.objects.filter(project=self.project, type=Source.Type.MANUAL)
            .order_by("id")
            .first()
        )
        if not source:
            source = Source.objects.create(
                project=self.project,
                type=Source.Type.MANUAL,
                title="Свой текст",
            )
        merged_message = Post.merge_title_and_body(title, body).strip()
        text_hash = Post.make_hash(merged_message) if merged_message else ""
        if source.deduplicate_text and text_hash:
            if Post.objects.filter(source=source, text_hash=text_hash).exists():
                messages.error(
                    self.request,
                    "Такой текст уже есть в ленте этого проекта.",
                )
                return self.form_invalid(form)

        Post.create_manual(
            project=self.project,
            source=source,
            title=title,
            body=body,
        )
        messages.success(self.request, "Текст добавлен в ленту.")
        return redirect(reverse("feed-detail", args=[self.project.id]))
