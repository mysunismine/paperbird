"""Тесты приложения accounts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.forms import UserProfileForm
from accounts.services.telethon_setup import (
    TelethonLoginState,
    TelethonPasswordRequiredError,
    TelethonSessionError,
    request_login_code,
)
from accounts.views import TELETHON_SETUP_SESSION_KEY
from core.utils.telethon import normalize_session_value

User = get_user_model()


class TelethonEventLoopPolicyTests(TestCase):
    def test_windows_policy_applied_when_not_selector(self) -> None:
        from accounts.services import telethon_setup

        class DummyPolicy:  # noqa: D401 - simple stub for isinstance checks
            """Stub policy used to simulate Windows selector policy."""

        with (
            patch("accounts.services.telethon_setup.sys.platform", "win32"),
            patch(
                "accounts.services.telethon_setup.asyncio.WindowsSelectorEventLoopPolicy",
                new=DummyPolicy,
                create=True,
            ),
            patch(
                "accounts.services.telethon_setup.asyncio.get_event_loop_policy",
                return_value=object(),
            ),
            patch(
                "accounts.services.telethon_setup.asyncio.set_event_loop_policy"
            ) as mock_set_policy,
        ):
            telethon_setup._ensure_windows_event_loop_policy()
            mock_set_policy.assert_called_once()
            policy_arg = mock_set_policy.call_args.args[0]
            self.assertIsInstance(policy_arg, DummyPolicy)

    def test_windows_policy_noop_when_already_set(self) -> None:
        from accounts.services import telethon_setup

        class DummyPolicy:
            pass

        selector_instance = DummyPolicy()

        with (
            patch("accounts.services.telethon_setup.sys.platform", "win32"),
            patch(
                "accounts.services.telethon_setup.asyncio.WindowsSelectorEventLoopPolicy",
                new=DummyPolicy,
                create=True,
            ),
            patch(
                "accounts.services.telethon_setup.asyncio.get_event_loop_policy",
                return_value=selector_instance,
            ),
            patch(
                "accounts.services.telethon_setup.asyncio.set_event_loop_policy"
            ) as mock_set_policy,
        ):
            telethon_setup._ensure_windows_event_loop_policy()
            mock_set_policy.assert_not_called()


class LogoutViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("logout-user", password="secret")

    def test_get_logout_redirects_and_clears_session(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class TelethonSetupServiceTests(TestCase):
    @patch("accounts.services.telethon_setup.TelegramClient")
    def test_force_sms_requests_resend_code(self, mock_client_cls) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.connect = AsyncMock()
                self.disconnect = AsyncMock()
                self.send_code_request = AsyncMock(
                    return_value=SimpleNamespace(phone_code_hash="hash1")
                )
                self.session = SimpleNamespace(save=Mock(return_value="session"))
                self.resend_mock = AsyncMock(
                    return_value=SimpleNamespace(phone_code_hash="hash2")
                )

            async def __call__(self, request):
                return await self.resend_mock(request)

        client = FakeClient()
        mock_client_cls.return_value = client

        state = request_login_code(
            api_id=123,
            api_hash="hash",
            phone="+79990000000",
            force_sms=True,
        )

        client.connect.assert_awaited_once()
        client.send_code_request.assert_awaited_once()
        client.resend_mock.assert_awaited_once()
        self.assertEqual(state.phone_code_hash, "hash2")

    @patch("accounts.services.telethon_setup.TelegramClient")
    def test_force_sms_skipped_when_hash_missing(self, mock_client_cls) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.connect = AsyncMock()
                self.disconnect = AsyncMock()
                self.send_code_request = AsyncMock(
                    return_value=SimpleNamespace(phone_code_hash="")
                )
                self.session = SimpleNamespace(save=Mock(return_value="session"))
                self.resend_mock = AsyncMock()

            async def __call__(self, request):
                return await self.resend_mock(request)

        client = FakeClient()
        mock_client_cls.return_value = client

        state = request_login_code(
            api_id=123,
            api_hash="hash",
            phone="+79990000000",
            force_sms=True,
        )

        client.resend_mock.assert_not_called()
        self.assertEqual(state.phone_code_hash, "")

    @patch("accounts.services.telethon_setup.TelegramClient")
    def test_force_sms_unavailable_error(self, mock_client_cls) -> None:
        from accounts.services import telethon_setup

        class FakeClient:
            def __init__(self) -> None:
                self.connect = AsyncMock()
                self.disconnect = AsyncMock()
                self.send_code_request = AsyncMock(
                    return_value=SimpleNamespace(phone_code_hash="hash1")
                )
                self.session = SimpleNamespace(save=Mock(return_value="session"))
                self.resend_mock = AsyncMock(
                    side_effect=telethon_setup.SendCodeUnavailableError(Mock())
                )

            async def __call__(self, request):
                return await self.resend_mock(request)

        client = FakeClient()
        mock_client_cls.return_value = client

        with self.assertRaises(TelethonSessionError) as ctx:
            request_login_code(
                api_id=123,
                api_hash="hash",
                phone="+79990000000",
                force_sms=True,
            )

        self.assertIn("Telegram временно не может отправить SMS", str(ctx.exception))


class TelethonUtilsTests(TestCase):
    def test_normalize_session_value_strips_wrappers(self) -> None:
        raw = "StringSession(\"ABCD==\")"
        self.assertEqual(normalize_session_value(raw), "ABCD==")

    def test_normalize_session_value_handles_prefix(self) -> None:
        raw = "session=  'XYZ123'  "
        self.assertEqual(normalize_session_value(raw), "XYZ123")


class UserProfileFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="secret")

    def test_form_strips_session_wrappers_on_save(self) -> None:
        form = UserProfileForm(
            data={
                "first_name": "",
                "last_name": "",
                "email": "",
                "telethon_api_id": 12345,
                "telethon_api_hash": " hash ",
                "telethon_session": 'StringSession("ABCD==")',
            },
            instance=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.telethon_api_hash, "hash")
        self.assertEqual(saved.telethon_session, "ABCD==")

    def test_form_requires_matching_credentials_for_session(self) -> None:
        form = UserProfileForm(
            data={
                "first_name": "",
                "last_name": "",
                "email": "",
                "telethon_api_id": "",
                "telethon_api_hash": "",
                "telethon_session": "value",
            },
            instance=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Для сохранения сессии заполните Telethon API ID и API hash.",
            form.errors["__all__"],
        )


class TelethonSessionSetupViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("owner", password="secret")
        self.url = reverse("accounts:telethon-setup")

    def test_redirects_when_credentials_missing(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("accounts:profile"))

    def _prepare_user_with_credentials(self) -> None:
        self.user.telethon_api_id = 123456
        self.user.telethon_api_hash = "hash123"
        self.user.save(update_fields=["telethon_api_id", "telethon_api_hash"])

    @patch("accounts.views.request_login_code")
    def test_start_step_stores_state(self, mock_request_login_code) -> None:
        self._prepare_user_with_credentials()
        mock_request_login_code.return_value = TelethonLoginState(
            session="temp-session",
            phone_code_hash="hash",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"step": "start", "phone": "+79991234567"},
        )
        self.assertRedirects(response, self.url)
        state = self.client.session.get(TELETHON_SETUP_SESSION_KEY)
        self.assertIsNotNone(state)
        self.assertEqual(state["phone"], "+79991234567")
        mock_request_login_code.assert_called_once()

    @patch("accounts.views.complete_login")
    def test_code_step_saves_session(self, mock_complete_login) -> None:
        self._prepare_user_with_credentials()
        mock_complete_login.return_value = "final-session"
        session = self.client.session
        session[TELETHON_SETUP_SESSION_KEY] = {
            "phone": "+79991234567",
            "session": "temp-session",
            "phone_code_hash": "hash",
        }
        session.save()
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"step": "code", "code": "12345"},
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.telethon_session, "final-session")
        self.assertNotIn(TELETHON_SETUP_SESSION_KEY, self.client.session)

    @patch("accounts.views.complete_login")
    def test_code_step_requires_password(self, mock_complete_login) -> None:
        self._prepare_user_with_credentials()
        mock_complete_login.side_effect = TelethonPasswordRequiredError("password needed")
        session = self.client.session
        session[TELETHON_SETUP_SESSION_KEY] = {
            "phone": "+79991234567",
            "session": "temp-session",
            "phone_code_hash": "hash",
        }
        session.save()
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"step": "code", "code": "12345"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "password needed")
        self.assertIn(TELETHON_SETUP_SESSION_KEY, self.client.session)

    @patch("accounts.views.request_login_code")
    def test_start_step_handles_service_error(self, mock_request_login_code) -> None:
        self._prepare_user_with_credentials()
        mock_request_login_code.side_effect = TelethonSessionError("Телефон указан неверно.")
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"step": "start", "phone": "+79991234567"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Телефон указан неверно.")
