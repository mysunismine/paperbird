"""Представления для управления профилем и аутентификацией."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordChangeDoneView, PasswordChangeView
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView, TemplateView

from accounts.forms import (
    TelethonSessionCodeForm,
    TelethonSessionStartForm,
    UserProfileForm,
)
from accounts.services.telethon_setup import (
    TelethonPasswordRequiredError,
    TelethonSessionError,
    complete_login,
    request_login_code,
)


TELETHON_SETUP_SESSION_KEY = "telethon_session_setup"


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


class TelethonSessionSetupView(LoginRequiredMixin, TemplateView):
    """Мастер генерации строковой сессии Telethon."""

    template_name = "accounts/telethon_session.html"

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not (user.telethon_api_id and user.telethon_api_hash):
            messages.error(
                request,
                "Сначала укажите TELETHON API ID и API hash в профиле, затем повторите попытку.",
            )
            return redirect("accounts:profile")
        return super().dispatch(request, *args, **kwargs)

    # --- Helpers ---------------------------------------------------------

    def _get_state(self) -> dict | None:
        return self.request.session.get(TELETHON_SETUP_SESSION_KEY)

    def _save_state(self, *, phone: str, session: str, phone_code_hash: str) -> None:
        self.request.session[TELETHON_SETUP_SESSION_KEY] = {
            "phone": phone,
            "session": session,
            "phone_code_hash": phone_code_hash,
        }
        self.request.session.modified = True

    def _clear_state(self) -> None:
        if TELETHON_SETUP_SESSION_KEY in self.request.session:
            del self.request.session[TELETHON_SETUP_SESSION_KEY]
            self.request.session.modified = True

    # --- Rendering -------------------------------------------------------

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        state = self._get_state()
        step = "code" if state else "start"
        context["step"] = step
        if step == "start":
            context["start_form"] = kwargs.get("start_form") or TelethonSessionStartForm()
        else:
            context["code_form"] = kwargs.get("code_form") or TelethonSessionCodeForm()
            context["phone"] = state["phone"] if state else None
        return context

    # --- HTTP verbs ------------------------------------------------------

    def post(self, request, *args, **kwargs):
        if "cancel" in request.POST:
            self._clear_state()
            messages.info(request, "Мастер генерации сессии был сброшен.")
            return redirect(request.path)
        action = request.POST.get("step")
        if action == "start":
            return self._handle_start()
        if action == "code":
            return self._handle_code()
        return redirect(request.path)

    def _handle_start(self):
        form = TelethonSessionStartForm(self.request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(start_form=form))

        user = self.request.user
        try:
            login_state = request_login_code(
                api_id=int(user.telethon_api_id),
                api_hash=str(user.telethon_api_hash),
                phone=form.cleaned_data["phone"],
                force_sms=form.cleaned_data.get("force_sms", False),
            )
        except TelethonSessionError as exc:
            form.add_error(None, str(exc))
            return self.render_to_response(self.get_context_data(start_form=form))

        self._save_state(
            phone=form.cleaned_data["phone"],
            session=login_state.session,
            phone_code_hash=login_state.phone_code_hash,
        )
        messages.info(
            self.request,
            "Код отправлен. Проверьте Telegram или SMS и введите код ниже.",
        )
        return redirect(self.request.path)

    def _handle_code(self):
        state = self._get_state()
        if not state:
            messages.error(
                self.request,
                "Сначала запросите код подтверждения.",
            )
            return redirect(self.request.path)

        form = TelethonSessionCodeForm(self.request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(code_form=form))

        user = self.request.user
        try:
            session_string = complete_login(
                api_id=int(user.telethon_api_id),
                api_hash=str(user.telethon_api_hash),
                phone=state["phone"],
                session=state["session"],
                phone_code_hash=state["phone_code_hash"],
                code=form.cleaned_data["code"],
                password=form.cleaned_data.get("password") or None,
            )
        except TelethonPasswordRequiredError as exc:
            form.add_error("password", str(exc))
            return self.render_to_response(self.get_context_data(code_form=form))
        except TelethonSessionError as exc:
            form.add_error(None, str(exc))
            return self.render_to_response(self.get_context_data(code_form=form))

        user.telethon_session = session_string
        user.save(update_fields=["telethon_session"])
        self._clear_state()
        messages.success(
            self.request,
            "Telethon-сессия успешно создана и сохранена.",
        )
        return redirect(reverse("accounts:profile"))


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
