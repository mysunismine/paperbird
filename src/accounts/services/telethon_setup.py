"""Helpers for interactive Telethon session setup."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from telethon import TelegramClient, functions
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    RPCError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession


class TelethonSessionError(RuntimeError):
    """Base error for Telethon session setup failures."""


class TelethonPasswordRequiredError(TelethonSessionError):
    """Raised when a 2FA password is required but not provided."""


@dataclass(slots=True)
class TelethonLoginState:
    """Returned after requesting a login code."""

    session: str
    phone_code_hash: str


def _ensure_windows_event_loop_policy() -> None:
    """Switches to the selector event loop policy on Windows if needed."""

    if sys.platform != "win32":
        return

    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:
        return

    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, policy_cls):
        asyncio.set_event_loop_policy(policy_cls())


async def _request_login_code_async(
    *, api_id: int, api_hash: str, phone: str, force_sms: bool = False
) -> TelethonLoginState:
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        if force_sms:
            phone_code_hash = getattr(sent, "phone_code_hash", "")
            if phone_code_hash:
                try:
                    resend = await client(
                        functions.auth.ResendCodeRequest(phone, phone_code_hash)
                    )
                except PhoneCodeExpiredError as exc:
                    raise TelethonSessionError(
                        "Срок действия кода истёк. Отправьте код повторно."
                    ) from exc
                except SendCodeUnavailableError as exc:
                    raise TelethonSessionError(
                        "Telegram временно не может отправить SMS на этот номер. "
                        "Попробуйте через несколько минут или без опции SMS."
                    ) from exc
                except RPCError as exc:
                    raise TelethonSessionError(str(exc)) from exc
                if getattr(resend, "phone_code_hash", ""):
                    sent = resend
        session_str = client.session.save()
        return TelethonLoginState(session=session_str, phone_code_hash=sent.phone_code_hash)
    finally:
        await client.disconnect()


def request_login_code(
    *, api_id: int, api_hash: str, phone: str, force_sms: bool = False
) -> TelethonLoginState:
    """Requests a login code for the provided phone number."""

    _ensure_windows_event_loop_policy()

    try:
        return asyncio.run(
            _request_login_code_async(
                api_id=api_id,
                api_hash=api_hash,
                phone=phone,
                force_sms=force_sms,
            )
        )
    except PhoneNumberInvalidError as exc:
        raise TelethonSessionError("Телефон указан неверно.") from exc
    except PhoneNumberBannedError as exc:
        raise TelethonSessionError(
            "Номер заблокирован Telegram. Зайдите через официальный клиент и попробуйте снова."
        ) from exc
    except PhoneNumberUnoccupiedError as exc:
        raise TelethonSessionError(
            "Номер не привязан к Telegram-аккаунту. Проверьте, что указали актуальный телефон."
        ) from exc


async def _complete_login_async(
    *,
    api_id: int,
    api_hash: str,
    phone: str,
    session: str,
    phone_code_hash: str,
    code: str,
    password: str | None,
) -> str:
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise TelethonPasswordRequiredError(
                    "Для входа необходим пароль двухфакторной аутентификации."
                )
            await client.sign_in(password=password)
        new_session = client.session.save()
        return new_session
    finally:
        await client.disconnect()


def complete_login(
    *,
    api_id: int,
    api_hash: str,
    phone: str,
    session: str,
    phone_code_hash: str,
    code: str,
    password: str | None,
) -> str:
    """Completes login with the confirmation code and returns a usable session string."""

    _ensure_windows_event_loop_policy()

    try:
        return asyncio.run(
            _complete_login_async(
                api_id=api_id,
                api_hash=api_hash,
                phone=phone,
                session=session,
                phone_code_hash=phone_code_hash,
                code=code,
                password=password,
            )
        )
    except TelethonPasswordRequiredError:
        raise
    except PhoneCodeInvalidError as exc:
        raise TelethonSessionError("Код подтверждения указан неверно.") from exc
    except PhoneCodeExpiredError as exc:
        raise TelethonSessionError("Срок действия кода истёк. Отправьте код повторно.") from exc


__all__ = [
    "TelethonSessionError",
    "TelethonPasswordRequiredError",
    "TelethonLoginState",
    "request_login_code",
    "complete_login",
]
