"""Настройки Django admin для пользователей."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Админка с дополнительными полями Telethon."""

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            _("Telethon"),
            {
                "fields": (
                    "telethon_api_id",
                    "telethon_api_hash",
                    "telethon_session",
                )
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            _("Telethon"),
            {
                "fields": (
                    "telethon_api_id",
                    "telethon_api_hash",
                    "telethon_session",
                )
            },
        ),
    )
    list_display = BaseUserAdmin.list_display + ("telethon_connected",)

    @admin.display(boolean=True, description="Telethon настроен")
    def telethon_connected(self, obj: User) -> bool:
        return obj.has_telethon_credentials
