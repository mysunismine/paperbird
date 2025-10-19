"""Сервисы для подключения к Telethon."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.sessions import StringSession

from accounts.models import User


class TelethonCredentialsMissingError(RuntimeError):
    """Выбрасывается, если у пользователя нет ключей Telethon."""


@dataclass
class TelethonClientFactory:
    """Создаёт Telethon клиент из данных пользователя."""

    user: User
    session_name: str | None = None

    def build(self) -> TelegramClient:
        if not self.user.has_telethon_credentials:
            raise TelethonCredentialsMissingError("У пользователя не заполнены ключи Telethon")
        session = StringSession(self.user.telethon_session)
        client = TelegramClient(
            session,
            api_id=self.user.telethon_api_id,
            api_hash=self.user.telethon_api_hash,
            device_model="Paperbird",
            system_version="Paperbird 0.1",
            app_version="0.1",
        )
        return client

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncIterator[TelegramClient]:
        client = self.build()
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelethonCredentialsMissingError(
                    "Сессия Telethon недействительна или требует входа"
                )
            yield client
        except RPCError as exc:  # pragma: no cover - требует реального API
            raise TelethonCredentialsMissingError(str(exc)) from exc
        finally:
            await client.disconnect()
