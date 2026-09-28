"""Auth first, then a service-owned exact sequence. No background client."""
import threading
import httpx

from body_validation_transport import BodyHTTPTransport
from general_validation_boundary import GeneralHTTPBoundary, GeneralValidationDenied


class GeneralTransport(httpx.BaseTransport):
    def __init__(self, ticket, *, inner=None):
        self.ticket = ticket
        self.inner = inner if inner is not None else httpx.HTTPTransport(retries=0, trust_env=False)
        # Reuse only the already restricted Auth phase; never bind its LEGACY scope.
        self.auth = BodyHTTPTransport(ticket, inner=self.inner)
        self.boundary = None
        self.verified = False
        self.stopped = False
        self.labels = set()
        self.lock = threading.RLock()

    def bind_verified(self):
        with self.lock:
            self.ticket.check()
            if self.verified or self.stopped:
                raise GeneralValidationDenied('AUTH_ALREADY_ENDED')
            self.verified = True

    def arm(self, label, *, authority, requests, check_local, persist_attempt):
        with self.lock:
            self.ticket.check()
            if (not self.verified or self.stopped or label in self.labels or
                    (self.boundary and self.boundary.consumed != len(self.boundary._requests))):
                raise GeneralValidationDenied('PHASE_ALREADY_USED_OR_INCOMPLETE')
            self.labels.add(label)
            self.boundary = GeneralHTTPBoundary(authority=authority, requests=requests,
                check_local=check_local, persist_attempt=persist_attempt, inner=self.inner)

    def handle_request(self, request):
        with self.lock:
            try:
                if self.stopped:
                    raise GeneralValidationDenied('SESSION_STOPPED')
                if not self.verified:
                    return self.auth.handle_request(request)
                if self.boundary is None:
                    raise GeneralValidationDenied('EXECUTION_NOT_ARMED')
                return self.boundary.handle_request(request)
            except Exception:
                self.stopped = True
                raise

    def close(self):
        with self.lock:
            self.stopped = True
            self.inner.close()
