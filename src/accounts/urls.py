"""Маршруты управления аутентификацией и профилем."""

from django.contrib.auth.views import LogoutView
from django.urls import path

from accounts.views import (
    PasswordUpdateDoneView,
    PasswordUpdateView,
    ProfileView,
    SignInView,
    SignOutView,
    TelethonSessionSetupView,
)

app_name = "accounts"

urlpatterns = [
    path("login/", SignInView.as_view(), name="login"),
    path("logout/", SignOutView.as_view(), name="logout"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path(
        "profile/telethon/",
        TelethonSessionSetupView.as_view(),
        name="telethon-setup",
    ),
    path("password/change/", PasswordUpdateView.as_view(), name="password_change"),
    path(
        "password/change/done/",
        PasswordUpdateDoneView.as_view(),
        name="password_change_done",
    ),
]
