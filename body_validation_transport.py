"""Final synchronous HTTP boundary for the dedicated staging validation UI."""
import hashlib
import json
import threading
import time
from uuid import uuid4

import httpx

from bidirectional_sync_scope import ENDPOINT, PROJECT_ID, ScopeDenied


class ForegroundTicket:
    def __init__(self, *, clock=time.monotonic):
        self.run_id = str(uuid4())
        self._clock = clock
        self._deadline = clock() + 300
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def check(self):
        if self._cancelled.is_set() or self._clock() >= self._deadline:
            raise ScopeDenied("FOREGROUND_GRANT_ENDED")


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ScopeDenied("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ScopeDenied("INVALID_JSON_NUMBER")))


class BodyHTTPTransport(httpx.BaseTransport):
    """No HTTP retry, redirect, implicit proxy, broad query or unbound write.

    Budgets are consumed at handle_request, including SDK resends. Auth token
    rotation may finish after cancellation so the existing client can persist
    the rotated token; callers recheck the grant before applying any result.
    """
    TABLES = frozenset({"projects", "project_sync_settings", "documents", "folders", "tree_orders"})

    def __init__(self, ticket, *, inner=None):
        self.ticket = ticket
        self.inner = inner if inner is not None else httpx.HTTPTransport(retries=0, trust_env=False)
        self.scope = None
        self.account_id = None
        self._bearer_sha = None
        self._counts = {}
        self._lock = threading.RLock()
        self.events = []
        self.persist_attempt = None
        self.validate_local_rpc = None

    def bind_verified_session(self, account_id, access_token):
        from uuid import UUID
        UUID(account_id)
        with self._lock:
            self.ticket.check()
            if self.account_id is not None or not access_token:
                raise ScopeDenied("SESSION_ALREADY_BOUND_OR_MISSING")
            self.account_id = account_id
            self._bearer_sha = hashlib.sha256(("Bearer " + access_token).encode()).digest()

    def _consume(self, key, limit):
        if self._counts.get(key, 0) >= limit:
            raise ScopeDenied("HTTP_ATTEMPT_BUDGET_EXHAUSTED")
        self._counts[key] = self._counts.get(key, 0) + 1

    def handle_request(self, request):
        with self._lock:
            self.ticket.check()
            url = request.url
            if (url.scheme != "https" or url.host != httpx.URL(ENDPOINT).host
                    or url.port not in (None, 443) or url.userinfo or url.fragment):
                raise ScopeDenied("HTTP_ENDPOINT_REFUSED")
            path, method = url.path, request.method
            params = list(url.params.multi_items())
            # Encoded/alternate paths must not be normalized into this allowlist.
            raw_path = url.raw_path.split(b"?", 1)[0]
            if raw_path != path.encode("ascii"):
                raise ScopeDenied("HTTP_PATH_REFUSED")
            if any(request.headers.get(k, "public") != "public" for k in ("accept-profile", "content-profile")):
                raise ScopeDenied("HTTP_SCHEMA_REFUSED")
            if not self.account_id:
                if method == "GET" and path == "/auth/v1/user" and not params:
                    self._consume("auth_user", 4)
                elif method == "POST" and path == "/auth/v1/token" and params == [("grant_type", "refresh_token")]:
                    payload = strict_json(request.content)
                    if set(payload) != {"refresh_token"} or not isinstance(payload["refresh_token"], str):
                        raise ScopeDenied("AUTH_REFRESH_PAYLOAD_REFUSED")
                    self._consume("auth_refresh", 1)
                else:
                    raise ScopeDenied("AUTH_ONLY_UNTIL_VERIFIED")
            else:
                bearer = hashlib.sha256(request.headers.get("authorization", "").encode()).digest()
                if bearer != self._bearer_sha:
                    raise ScopeDenied("HTTP_SESSION_CHANGED")
                table = path.removeprefix("/rest/v1/")
                if method == "GET" and table in self.TABLES:
                    if sorted(params) != sorted([("select", "*"), ("project_id", "eq." + PROJECT_ID)]):
                        raise ScopeDenied("HTTP_QUERY_REFUSED")
                    if any(h in request.headers for h in ("range", "range-unit")):
                        raise ScopeDenied("HTTP_PARTIAL_SNAPSHOT_REFUSED")
                    self._consume("get_" + table, 2)
                elif method == "POST" and path.startswith("/rest/v1/rpc/") and not params and self.scope:
                    name = path.removeprefix("/rest/v1/rpc/")
                    if self.validate_local_rpc:
                        self.validate_local_rpc(name)
                    self.scope.consume_rpc(name, strict_json(request.content),
                                           endpoint=ENDPOINT, account_id=self.account_id)
                else:
                    raise ScopeDenied("HTTP_OUTSIDE_BODY_SCOPE")
            event = {"method": method, "path": path, "run_id": self.ticket.run_id,
                     "attempt": len(self.events) + 1}
            self.events.append(event)
            if self.persist_attempt:
                self.persist_attempt("http_attempt", event)
            # This is the dispatch linearization point. Cancellation after this
            # point treats a request as potentially in flight, never retryable.
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()
