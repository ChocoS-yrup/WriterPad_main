"""Validated public cloud client configuration for packaged WriterPad builds."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit


RELEASE_CLOUD_CONFIG_FILENAME = "release_cloud_config.json"
ALLOWED_RELEASE_CONFIG_KEYS = frozenset({
    "supabase_url",
    "supabase_publishable_key",
})

CLOUD_DISABLED_MESSAGE = "이 빌드는 클라우드 동기화가 구성되지 않았습니다."
CLOUD_INVALID_MESSAGE = "클라우드 서버 설정을 확인할 수 없습니다."
CLOUD_DNS_MESSAGE = "클라우드 서버 설정을 확인할 수 없습니다."
CLOUD_TIMEOUT_MESSAGE = "클라우드 서버 응답 시간이 초과되었습니다."
CLOUD_AUTH_MESSAGE = "이메일 또는 비밀번호를 확인해주세요."
CLOUD_SERVER_REJECTION_MESSAGE = "클라우드 서버가 로그인 요청을 거부했습니다."
CLOUD_UNKNOWN_MESSAGE = "클라우드 로그인 중 오류가 발생했습니다."

_PUBLISHABLE_KEY_PATTERN = re.compile(r"^sb_publishable_[A-Za-z0-9_-]{16,}$")
_HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_PLACEHOLDER_HOSTS = {
    "dummy.supabase.co",
    "example.supabase.co",
    "your-project.supabase.co",
}
_PLACEHOLDER_KEY_MARKERS = (
    "change-me",
    "dummy",
    "example",
    "placeholder",
    "publishable-key",
    "your-key",
)
_SECRET_KEY_MARKERS = (
    "service_role",
    "service-role",
    "sb_secret_",
)


class CloudClientConfig:
    """Configuration result whose representation never exposes the API key."""

    __slots__ = ("state", "url", "publishable_key", "reason")

    def __init__(self, state, url="", publishable_key="", reason=""):
        self.state = str(state)
        self.url = str(url)
        self.publishable_key = str(publishable_key)
        self.reason = str(reason)

    @property
    def is_ready(self):
        return self.state == "ready"

    @property
    def is_disabled(self):
        return self.state == "disabled"

    @property
    def user_message(self):
        return CLOUD_DISABLED_MESSAGE if self.is_disabled else CLOUD_INVALID_MESSAGE

    def __repr__(self):
        return f"CloudClientConfig(state={self.state!r}, reason={self.reason!r})"


class CloudError:
    __slots__ = ("kind", "message")

    def __init__(self, kind, message):
        self.kind = str(kind)
        self.message = str(message)

    def __repr__(self):
        return f"CloudError(kind={self.kind!r}, message={self.message!r})"


def _invalid(reason):
    return CloudClientConfig("invalid", reason=reason)


def _valid_hostname(hostname):
    if not hostname or len(hostname) > 253 or "." not in hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
        return False
    except ValueError:
        pass
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    return all(_HOST_LABEL_PATTERN.fullmatch(label) for label in ascii_hostname.split("."))


def validate_cloud_client_config(values):
    """Accept only an HTTPS URL and a new-style Supabase publishable key."""
    if not isinstance(values, dict):
        return _invalid("config_not_object")
    if set(values) - ALLOWED_RELEASE_CONFIG_KEYS:
        return _invalid("unexpected_fields")

    url = values.get("supabase_url", "")
    key = values.get("supabase_publishable_key", "")
    if not isinstance(url, str) or not isinstance(key, str):
        return _invalid("non_string_value")
    url = url.strip()
    key = key.strip()

    if not url and not key:
        return CloudClientConfig("disabled")
    if not url or not key:
        return _invalid("partial_config")

    lowered_key = key.lower()
    if any(marker in lowered_key for marker in _SECRET_KEY_MARKERS):
        return _invalid("secret_key_not_allowed")
    if key.count(".") == 2:
        return _invalid("jwt_key_not_allowed")
    if any(marker in lowered_key for marker in _PLACEHOLDER_KEY_MARKERS):
        return _invalid("placeholder_key")
    if not _PUBLISHABLE_KEY_PATTERN.fullmatch(key):
        return _invalid("invalid_publishable_key")

    try:
        parsed = urlsplit(url)
    except ValueError:
        return _invalid("invalid_url")
    if parsed.scheme.lower() != "https":
        return _invalid("invalid_url_scheme")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return _invalid("invalid_url_components")
    if parsed.path not in {"", "/"}:
        return _invalid("invalid_url_path")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if hostname in _PLACEHOLDER_HOSTS or "your-project" in hostname:
        return _invalid("placeholder_hostname")
    if not _valid_hostname(hostname):
        return _invalid("invalid_hostname")

    normalized_url = f"https://{hostname}"
    try:
        port = parsed.port
    except ValueError:
        return _invalid("invalid_url_port")
    if port:
        normalized_url += f":{port}"
    return CloudClientConfig("ready", normalized_url, key)


def load_cloud_client_config(config_dir):
    path = Path(config_dir, RELEASE_CLOUD_CONFIG_FILENAME)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CloudClientConfig("disabled", reason="config_missing")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _invalid("config_unreadable")
    return validate_cloud_client_config(values)


def assert_release_config_buildable(config_path):
    """Fail a package build safely when public release config is malformed."""
    path = Path(config_path)
    config = load_cloud_client_config(path.parent)
    if path.name != RELEASE_CLOUD_CONFIG_FILENAME:
        raise RuntimeError("Unexpected release cloud config filename")
    if config.state == "invalid":
        raise RuntimeError(f"Invalid release cloud config: {config.reason}")
    return config.state


def _exception_chain(error):
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )


def classify_cloud_error(error):
    """Map transport/auth/server failures without returning sensitive details."""
    chain = list(_exception_chain(error))
    text = " ".join(str(item).lower() for item in chain)

    if any(
        isinstance(item, socket.gaierror)
        or getattr(item, "winerror", None) == 11001
        for item in chain
    ) or any(marker in text for marker in ("getaddrinfo", "name resolution", "nodename nor servname")):
        return CloudError("dns", CLOUD_DNS_MESSAGE)

    if any(
        isinstance(item, TimeoutError)
        or "timeout" in type(item).__name__.lower()
        for item in chain
    ) or "timed out" in text:
        return CloudError("timeout", CLOUD_TIMEOUT_MESSAGE)

    # A retryable error names itself one. It carries a status of 0 rather than
    # anything the server said, so it has to be read before the status below or
    # it lands in the wrong bucket entirely.
    if any("retryable" in type(item).__name__.lower() for item in chain):
        return CloudError("timeout", CLOUD_TIMEOUT_MESSAGE)

    # supabase-auth puts the HTTP status on `status`; httpx and requests-shaped
    # errors put it on `status_code`. Reading only one of them left every
    # refusal this library raises falling through to "unknown".
    status_code = next(
        (
            status
            for item in chain
            for attribute in ("status_code", "status")
            for status in [getattr(item, attribute, None)]
            if isinstance(status, int) and not isinstance(status, bool) and status
        ),
        None,
    )
    auth_markers = (
        "invalid login credentials",
        "invalid credentials",
        "email not confirmed",
        "user not found",
        "authentication failed",
        # How a refused refresh token actually reads. None of the phrases above
        # appear in it, so without these a revoked session looked unrecognized.
        "invalid refresh token",
        "refresh_token_not_found",
        "refresh token not found",
        "already used",
        "session missing",
        "session_not_found",
    )
    if any(marker in text for marker in auth_markers) or status_code in {400, 401, 403}:
        return CloudError("authentication", CLOUD_AUTH_MESSAGE)
    if status_code is not None or any(
        marker in text for marker in ("server error", "bad gateway", "service unavailable")
    ):
        return CloudError("server_rejection", CLOUD_SERVER_REJECTION_MESSAGE)
    return CloudError("unknown", CLOUD_UNKNOWN_MESSAGE)
