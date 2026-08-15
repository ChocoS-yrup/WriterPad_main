import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cloud_config import (
    CLOUD_AUTH_MESSAGE,
    CLOUD_DISABLED_MESSAGE,
    CLOUD_DNS_MESSAGE,
    CLOUD_SERVER_REJECTION_MESSAGE,
    CLOUD_TIMEOUT_MESSAGE,
    RELEASE_CLOUD_CONFIG_FILENAME,
    assert_release_config_buildable,
    classify_cloud_error,
    load_cloud_client_config,
    validate_cloud_client_config,
)
from mode_assistant import AssistantModeWidget
from sync_manager import SyncManager


VALID_URL = "https://writerpad-public.supabase.co"
VALID_KEY = "sb_publishable_" + ("a" * 32)


class CloudConfigValidationTestCase(unittest.TestCase):
    def test_empty_public_config_is_explicitly_disabled(self):
        config = validate_cloud_client_config({
            "supabase_url": "",
            "supabase_publishable_key": "",
        })

        self.assertEqual(config.state, "disabled")
        self.assertEqual(config.user_message, CLOUD_DISABLED_MESSAGE)
        self.assertFalse(config.is_ready)

    def test_valid_public_config_accepts_only_url_and_publishable_key(self):
        config = validate_cloud_client_config({
            "supabase_url": VALID_URL + "/",
            "supabase_publishable_key": VALID_KEY,
        })

        self.assertTrue(config.is_ready)
        self.assertEqual(config.url, VALID_URL)
        self.assertNotIn(VALID_KEY, repr(config))

    def test_placeholder_url_and_key_are_rejected(self):
        placeholder_url = validate_cloud_client_config({
            "supabase_url": "https://your-project.supabase.co",
            "supabase_publishable_key": VALID_KEY,
        })
        placeholder_key = validate_cloud_client_config({
            "supabase_url": VALID_URL,
            "supabase_publishable_key": "sb_publishable_placeholder_value",
        })

        self.assertEqual(placeholder_url.reason, "placeholder_hostname")
        self.assertEqual(placeholder_key.reason, "placeholder_key")

    def test_missing_scheme_and_invalid_hostname_are_rejected(self):
        missing_scheme = validate_cloud_client_config({
            "supabase_url": "writerpad-public.supabase.co",
            "supabase_publishable_key": VALID_KEY,
        })
        invalid_hostname = validate_cloud_client_config({
            "supabase_url": "https://bad_host",
            "supabase_publishable_key": VALID_KEY,
        })

        self.assertEqual(missing_scheme.reason, "invalid_url_scheme")
        self.assertEqual(invalid_hostname.reason, "invalid_hostname")

        invalid_port = validate_cloud_client_config({
            "supabase_url": "https://writerpad-public.supabase.co:not-a-port",
            "supabase_publishable_key": VALID_KEY,
        })
        self.assertEqual(invalid_port.reason, "invalid_url_port")

    def test_secret_service_role_and_jwt_keys_are_rejected(self):
        rejected = (
            "sb_secret_" + ("b" * 32),
            "service_role_key_material",
            "header.payload.signature",
        )

        for key in rejected:
            with self.subTest(kind=key.split("_", 1)[0]):
                config = validate_cloud_client_config({
                    "supabase_url": VALID_URL,
                    "supabase_publishable_key": key,
                })
                self.assertEqual(config.state, "invalid")

    def test_authentication_or_token_fields_are_not_release_config_fields(self):
        forbidden_fields = (
            "email",
            "password",
            "jwt",
            "refresh_token",
            "service_role_key",
        )

        for field in forbidden_fields:
            with self.subTest(field=field):
                config = validate_cloud_client_config({
                    "supabase_url": VALID_URL,
                    "supabase_publishable_key": VALID_KEY,
                    field: "must-not-be-packaged",
                })
                self.assertEqual(config.reason, "unexpected_fields")

    def test_build_config_ignores_env_file_even_when_one_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            Path(root, ".env").write_text(
                "FORBIDDEN_BUILD_SENTINEL=must-not-be-read",
                encoding="utf-8",
            )
            Path(root, RELEASE_CLOUD_CONFIG_FILENAME).write_text(
                json.dumps({
                    "supabase_url": "",
                    "supabase_publishable_key": "",
                }),
                encoding="utf-8",
            )

            self.assertEqual(
                assert_release_config_buildable(
                    Path(root, RELEASE_CLOUD_CONFIG_FILENAME)
                ),
                "disabled",
            )

    def test_missing_release_config_is_cloud_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_cloud_client_config(temp_dir)

        self.assertEqual(config.state, "disabled")


class CloudErrorClassificationTestCase(unittest.TestCase):
    class _ResponseError(RuntimeError):
        def __init__(self, message, status_code):
            super().__init__(message)
            self.status_code = status_code

    def test_dns_timeout_auth_and_server_rejection_are_distinct(self):
        dns = classify_cloud_error(socket.gaierror(11001, "getaddrinfo failed"))
        timeout = classify_cloud_error(TimeoutError("request timed out"))
        auth = classify_cloud_error(
            self._ResponseError("invalid login credentials", 400)
        )
        server = classify_cloud_error(
            self._ResponseError("service unavailable", 503)
        )

        self.assertEqual((dns.kind, dns.message), ("dns", CLOUD_DNS_MESSAGE))
        self.assertEqual(
            (timeout.kind, timeout.message), ("timeout", CLOUD_TIMEOUT_MESSAGE)
        )
        self.assertEqual(
            (auth.kind, auth.message), ("authentication", CLOUD_AUTH_MESSAGE)
        )
        self.assertEqual(
            (server.kind, server.message),
            ("server_rejection", CLOUD_SERVER_REJECTION_MESSAGE),
        )
        self.assertNotIn("11001", dns.message)
        self.assertNotIn("getaddrinfo", dns.message)

    def test_sign_in_never_returns_raw_credentials_or_keys(self):
        sentinel = "credential-and-key-sentinel"
        target = SimpleNamespace(
            cloud_network_enabled=True,
            supabase=SimpleNamespace(
                auth=SimpleNamespace(
                    sign_in_with_password=MagicMock(
                        side_effect=RuntimeError(sentinel)
                    )
                )
            ),
            _last_cloud_error_kind="",
        )
        output = io.StringIO()

        with redirect_stdout(output), redirect_stderr(output):
            success, message = SyncManager.sign_in(
                target,
                "account@example.invalid",
                sentinel,
            )

        self.assertFalse(success)
        self.assertNotIn(sentinel, message)
        self.assertNotIn(sentinel, output.getvalue())


class CloudDisabledRuntimeTestCase(unittest.TestCase):
    def test_cloud_disabled_does_not_create_network_client(self):
        disabled = validate_cloud_client_config({
            "supabase_url": "",
            "supabase_publishable_key": "",
        })
        target = SimpleNamespace(supabase=None)

        with patch(
            "sync_manager.load_cloud_client_config", return_value=disabled
        ), patch.object(SyncManager, "create_supabase_client") as create_client:
            SyncManager.init_supabase(target)

        create_client.assert_not_called()
        self.assertEqual(target.cloud_config_state, "disabled")
        self.assertIsNone(target.supabase)

    def test_cloud_disabled_does_not_start_shutdown_remote_flush(self):
        store = MagicMock()
        target = SimpleNamespace(
            cloud_network_enabled=False,
            is_v2_enabled=True,
            _v2_store=store,
        )

        self.assertTrue(SyncManager.flush_pending_syncs(target))
        store.counts.assert_not_called()

        flush = MagicMock()
        panel = SimpleNamespace(
            writing_mode=SimpleNamespace(
                sync_manager=SimpleNamespace(
                    cloud_network_enabled=False,
                    flush_pending_syncs=flush,
                )
            )
        )
        self.assertTrue(AssistantModeWidget._flush_writing_sync_before_close(panel))
        flush.assert_not_called()

    def test_environment_credentials_never_trigger_automatic_login(self):
        config = validate_cloud_client_config({
            "supabase_url": VALID_URL,
            "supabase_publishable_key": VALID_KEY,
        })
        auth = SimpleNamespace(
            set_session=MagicMock(),
            sign_in_with_password=MagicMock(),
            on_auth_state_change=MagicMock(),
        )
        client = SimpleNamespace(auth=auth)
        http_client = MagicMock()
        previous_email = os.environ.get("SUPABASE_EMAIL")
        previous_password = os.environ.get("SUPABASE_PASSWORD")
        os.environ["SUPABASE_EMAIL"] = "environment@example.invalid"
        os.environ["SUPABASE_PASSWORD"] = "environment-password"
        try:
            with patch("supabase.create_client", return_value=client), patch(
                "supabase.ClientOptions", return_value=object()
            ), patch("httpx.Client", return_value=http_client), patch(
                "security_manager.SecurityManager.get_supabase_session",
                return_value=("", ""),
            ):
                created = SyncManager.create_supabase_client(config)
        finally:
            if previous_email is None:
                os.environ.pop("SUPABASE_EMAIL", None)
            else:
                os.environ["SUPABASE_EMAIL"] = previous_email
            if previous_password is None:
                os.environ.pop("SUPABASE_PASSWORD", None)
            else:
                os.environ["SUPABASE_PASSWORD"] = previous_password

        self.assertIs(created, client)
        auth.sign_in_with_password.assert_not_called()
        auth.set_session.assert_not_called()

    def test_windows_credential_manager_session_restore_is_preserved(self):
        config = validate_cloud_client_config({
            "supabase_url": VALID_URL,
            "supabase_publishable_key": VALID_KEY,
        })
        session = SimpleNamespace(
            access_token="session-access-token",
            refresh_token="session-refresh-token",
            user=SimpleNamespace(email="account@example.invalid"),
        )
        auth = SimpleNamespace(
            set_session=MagicMock(return_value=SimpleNamespace(session=session)),
            sign_in_with_password=MagicMock(),
            on_auth_state_change=MagicMock(),
        )
        client = SimpleNamespace(auth=auth)

        with patch("supabase.create_client", return_value=client), patch(
            "supabase.ClientOptions", return_value=object()
        ), patch("httpx.Client", return_value=MagicMock()), patch(
            "security_manager.SecurityManager.get_supabase_session",
            return_value=(session.access_token, session.refresh_token),
        ), patch.object(SyncManager, "_persist_supabase_session", return_value=True):
            created = SyncManager.create_supabase_client(config)

        self.assertIs(created, client)
        auth.set_session.assert_called_once_with(
            session.access_token,
            session.refresh_token,
        )
        auth.sign_in_with_password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
