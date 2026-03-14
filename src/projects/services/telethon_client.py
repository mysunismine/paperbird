"""Сервисы для подключения к Telethon."""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import (
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    RPCError,
    UserAlreadyParticipantError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import ChatInviteAlready

from accounts.models import User
from core.utils.telethon import normalize_session_value


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

        session_data = normalize_session_value(self.user.telethon_session)
        if not session_data:
            raise TelethonCredentialsMissingError("Телеграм-сессия отсутствует. Обновите профиль.")

        try:
            session = StringSession(session_data)
        except ValueError as exc:
            raise TelethonCredentialsMissingError(
                "Строка Telethon-сессии повреждена. Сгенерируйте новую и сохраните её в профиле."
            ) from exc

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


def extract_invite_hash(value: str | None) -> str:
    """Extract Telegram invite hash from known invite-link formats."""
    if not value:
        return ""
    raw = value.strip()
    if not raw:
        return ""

    for pattern in (
        r"(?:https?://)?t\.me/\+([A-Za-z0-9_-]+)",
        r"(?:https?://)?t\.me/joinchat/([A-Za-z0-9_-]+)",
    ):
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    if raw.startswith("+"):
        return raw[1:]
    return ""


async def resolve_telegram_target(client: TelegramClient, target: str | int):
    """Resolve username/id/invite link into Telethon entity.

    For private invite links (`t.me/+...`) attempts invite check/import before `get_entity`.
    """
    invite_hash = extract_invite_hash(str(target) if target is not None else "")
    if invite_hash:
        try:
            invite_state = await client(CheckChatInviteRequest(invite_hash))
            if isinstance(invite_state, ChatInviteAlready):
                return invite_state.chat
        except UserAlreadyParticipantError:
            pass
        except (InviteHashInvalidError, InviteHashExpiredError) as exc:
            raise ValueError("Инвайт-ссылка Telegram недействительна или устарела.") from exc

        try:
            updates = await client(ImportChatInviteRequest(invite_hash))
        except UserAlreadyParticipantError:
            pass
        except InviteRequestSentError as exc:
            raise ValueError(
                "Запрос на вступление отправлен. Дождитесь одобрения и повторите обновление."
            ) from exc
        else:
            chats = getattr(updates, "chats", None) or []
            if chats:
                return chats[0]

    return await client.get_entity(target)
