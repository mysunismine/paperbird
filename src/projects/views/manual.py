"""Views for manual post creation."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView, TemplateView

from projects.forms import ManualPostForm
from projects.models import Post, PostVersion, Project, Source


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
        context["is_edit"] = False
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
                title="Редакторский текст",
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
            created_by=self.request.user,
        )
        messages.success(self.request, "Редакторский текст добавлен в ленту.")
        return redirect(reverse("feed-detail", args=[self.project.id]))


class ManualPostUpdateView(LoginRequiredMixin, FormView):
    """Редактирование ручного текста."""

    template_name = "projects/manual_post_form.html"
    form_class = ManualPostForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.project = get_object_or_404(
            Project.objects.accessible_by(request.user),
            pk=kwargs["project_pk"],
        )
        self.manual_post = get_object_or_404(
            Post.objects.select_related("source"),
            pk=kwargs["post_pk"],
            project=self.project,
            origin_type=Post.Origin.MANUAL,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self) -> dict[str, str]:
        return {"title": self.manual_post.manual_title, "body": self.manual_post.manual_body}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        context["post"] = self.manual_post
        context["is_edit"] = True
        return context

    def form_valid(self, form):
        title = form.cleaned_data["title"].strip()
        body = form.cleaned_data["body"]
        self.manual_post.update_manual(
            title=title,
            body=body,
            updated_by=self.request.user,
        )
        messages.success(self.request, "Редакторский текст обновлён.")
        return redirect(
            reverse("feed-post-detail", args=[self.project.id, self.manual_post.id])
        )


class ManualPostVersionDetailView(LoginRequiredMixin, TemplateView):
    """Просмотр конкретной версии ручного текста."""

    template_name = "projects/manual_post_version.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.project = get_object_or_404(
            Project.objects.accessible_by(request.user),
            pk=kwargs["project_pk"],
        )
        self.manual_post = get_object_or_404(
            Post.objects.select_related("source"),
            pk=kwargs["post_pk"],
            project=self.project,
            origin_type=Post.Origin.MANUAL,
        )
        self.version = get_object_or_404(
            PostVersion.objects.select_related("created_by"),
            pk=kwargs["version_pk"],
            post=self.manual_post,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "project": self.project,
                "post": self.manual_post,
                "version": self.version,
            }
        )
        return context
