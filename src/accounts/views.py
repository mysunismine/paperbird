"""Представления для управления профилем и аутентификацией."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordChangeDoneView, PasswordChangeView
from django.urls import reverse_lazy
from django.views.generic import FormView

from accounts.forms import UserProfileForm


class SignInView(LoginView):
    """Страница входа в систему."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class ProfileView(LoginRequiredMixin, FormView):
    """Форма управления профилем пользователя."""

    template_name = "accounts/profile.html"
    form_class = UserProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Профиль обновлён.")
        return super().form_valid(form)


class PasswordUpdateView(LoginRequiredMixin, PasswordChangeView):
    """Обновление пароля текущего пользователя."""

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            existing_classes = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing_classes} form-control".strip()
        return form


class PasswordUpdateDoneView(LoginRequiredMixin, PasswordChangeDoneView):
    """Экран успешной смены пароля."""

    template_name = "accounts/password_change_done.html"
