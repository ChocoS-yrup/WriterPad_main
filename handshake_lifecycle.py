"""Contract authorization lifetimes. No credentials or manuscript diagnostics."""
from datetime import datetime, timezone
import threading
import time

from cloud_config import classify_cloud_error
from contract_transport import CONTRACT_REFUSALS, execute_contract_rpc
from runtime_profile import is_forced_offline
from sync_contract import (
    CONTRACT_VERSION, CANONICAL_CONTRACT_SHA256, SYNC_PROTOCOL_VERSION,
    CLIENT_CAPABILITIES, SyncContractError, read_handshake_compatibility, require_uuid,
)


class ContractDispatchPaused(SyncContractError):
    def __init__(self):
        super().__init__("CONTRACT_DISPATCH_PAUSED")


class HandshakeLifecycleMixin:
    def _init_handshake_lifecycle(self):
        self._contract_lock = threading.RLock()
        self._contract_epoch = 0
        self._contract_handshake_inflight = None
        self._contract_retry_key = None
        self._contract_retry_count = 0
        self._contract_retry_after = 0.0
        self._contract_sending = set()
        self._contract_preparing = set()
        self._contract_probe_pending = None
        self._contract_write_epoch = 0
        self._contract_project_state_context = None

    def _contract_context_key(self):
        context = self._v2_context or {}
        return (
            self._v2_context_generation, self._contract_epoch,
            id(self._v2_store), id(self.supabase),
            context.get("local_key"), context.get("project_id"),
            self._contract_identity(),
        )

    def _forget_contract_handshake(self):
        with self._contract_lock:
            self._contract_epoch += 1
            self._contract_handshake = None
            self._contract_handshake_attempt = None
            self._contract_handshake_error = ""
            self._contract_retry_key = None
            self._contract_retry_count = 0
            self._contract_retry_after = 0.0
            # An old network call still owns its slot until it returns. It is
            # invalidated by epoch, never killed or allowed to publish late.

    @staticmethod
    def _transient_handshake_error(error):
        seen = set()
        chain = []
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            chain.append(error)
            # Explicit contract/auth refusals must not be retried because an
            # older exception happened to be attached to them.
            if isinstance(error, SyncContractError) or str(error) in CONTRACT_REFUSALS:
                return False
            status = getattr(error, 'status_code', None) or getattr(error, 'status', None)
            if str(status) in {'400', '401', '403'}:
                return False
            error = getattr(error, '__cause__', None) or getattr(error, '__context__', None)
        return any(HandshakeLifecycleMixin._transient_transport_error(item) for item in chain)

    @staticmethod
    def _transient_transport_error(error):
        if isinstance(error, SyncContractError):
            return False
        if isinstance(error, (TimeoutError, ConnectionError)):
            return True
        status = getattr(error, "status_code", None) or getattr(error, "status", None)
        if str(status).isdigit() and (int(status) == 429 or 500 <= int(status) <= 599):
            return True
        if str(error) == "NETWORK_UNAVAILABLE":
            return True
        return (classify_cloud_error(error).kind in {"dns", "timeout"}
                or type(error).__name__ in {"ConnectError", "NetworkError", "ReadError", "WriteError", "RemoteProtocolError"})

    def perform_contract_handshake(self, *, require_connection=False, _automatic=False, _expected_key=None):
        # Only the short state transitions use the lock. Authentication and
        # network I/O run on the existing pull worker, never under this lock.
        with self._contract_lock:
            if not self.is_v2_enabled:
                raise RuntimeError("v2 project is not configured")
            if is_forced_offline() or not self.supabase:
                if require_connection:
                    raise RuntimeError("NETWORK_UNAVAILABLE")
                return None
            if self._contract_handshake_inflight is not None:
                return None
            key = self._contract_context_key()
            if _expected_key is not None and key != _expected_key:
                return None
            if _automatic and (self._contract_handshake_attempt == key
                               or time.monotonic() < self._contract_retry_after):
                return self._contract_handshake_reading()
            if not key[-1]:
                raise SyncContractError("AUTH_REQUIRED")
            ticket = object()
            self._contract_handshake_inflight = ticket
            self._contract_handshake = None
            client = self.supabase
            project_id = self._v2_context["project_id"]
        try:
            self.ensure_session_valid(client)
            with self._contract_lock:
                if key != self._contract_context_key():
                    return None
            response = execute_contract_rpc(client.rpc("get_sync_handshake", {
                "p_project_id": project_id,
                "p_contract_sha256": CANONICAL_CONTRACT_SHA256,
            }))
            handshake = self._response_data(response)
            with self._contract_lock:
                if key != self._contract_context_key():
                    return None
                if not isinstance(handshake, dict):
                    raise SyncContractError("INVALID_ARGUMENT")
                if not isinstance(handshake.get("project_id"), str):
                    raise SyncContractError("INVALID_ARGUMENT", "invalid project_id")
                answered = require_uuid(handshake["project_id"], "project_id")
                if answered != require_uuid(project_id, "project_id"):
                    raise SyncContractError("INVALID_ARGUMENT")
                reading = {
                    "generation": self._v2_context_generation,
                    "context_key": key,
                    "project_id": project_id, "identity": key[-1],
                    "contract_sha256": CANONICAL_CONTRACT_SHA256,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "outcome": "unsupported",
                }
                if handshake.get("supported") is True:
                    compatibility = read_handshake_compatibility(handshake)
                    project = self.activate_contract_project(**compatibility)
                    reading.update(
                        outcome="supported",
                        project_sync_mode=project["project_sync_mode"],
                        migration_epoch=int(project["migration_epoch"] or 0),
                    )
                self._contract_handshake = reading
                self._contract_handshake_error = ""
                self._contract_handshake_attempt = key
                self._contract_retry_count = 0
                self._contract_retry_after = 0.0
                return reading
        except Exception as error:
            with self._contract_lock:
                if key == self._contract_context_key():
                    code = self._stable_error_code(error) or "HANDSHAKE_FAILED"
                    self._contract_handshake_error = code
                    if code in {"AUTH_REQUIRED", "AUTH_EXPIRED"}:
                        self._mark_auth_required(error)
                    elif self._transient_handshake_error(error):
                        self._contract_handshake_attempt = None
                        self._contract_retry_key = key
                        self._contract_retry_count = min(self._contract_retry_count + 1, 6)
                        self._contract_retry_after = time.monotonic() + min(60, 2 ** self._contract_retry_count)
                    else:
                        self._contract_handshake_attempt = key
            raise
        finally:
            with self._contract_lock:
                if self._contract_handshake_inflight is ticket:
                    self._contract_handshake_inflight = None

    def _ensure_contract_handshake(self, expected_key=None):
        with self._contract_lock:
            if (not self.is_v2_enabled or is_forced_offline() or not self.supabase
                    or self._auth_retry_blocked or not self._contract_identity()):
                return None
            key = self._contract_context_key()
            if self._contract_retry_key != key:
                if expected_key is not None and key != expected_key:
                    return None
                self._contract_retry_key = key
                self._contract_retry_count = 0
                self._contract_retry_after = 0.0
            if (self._contract_handshake_attempt == key
                    or self._contract_handshake_inflight is not None
                    or time.monotonic() < self._contract_retry_after):
                return self._contract_handshake_reading()
        try:
            return self.perform_contract_handshake(_automatic=True, _expected_key=expected_key or key)
        except Exception:
            # perform records the outcome before releasing the in-flight slot.
            # Pull/legacy/local editing remain usable when the probe fails.
            return None

    def _contract_dispatch_context(self):
        with self._contract_lock:
            return (self._contract_context_key(), self._v2_store, self.supabase,
                    (self._contract_write_epoch, self._contract_queue_stamp()))

    def _contract_queue_stamp(self):
        reader = getattr(self._v2_store, "contract_queue_authority_stamp", None)
        if callable(reader) and self._v2_context:
            return reader(self._v2_context["local_key"])
        return None

    def _contract_project_context(self):
        return (self._v2_pull_identity(), id(self._v2_store),
                id(self.supabase), self._contract_identity())

    def _contract_authority_allows_dispatch(self):
        return self._contract_authority_observation()['allowed']

    def _contract_authority_observation(self):
        # An old/default state is not a current authorization. Unlike the
        # general legacy path, contract writes require an observed baseline.
        identity_matches = self._v2_structure_authority_identity == self._v2_pull_identity()
        accepted = self._v2_structure_authority in {'contract', 'legacy'}
        project_matches = self._contract_project_state_context == self._contract_project_context()
        active = self._current_project_server_state() == 'active'
        return {'identity_matches': identity_matches, 'accepted': accepted,
                'project_context_matches': project_matches, 'project_active': active,
                'allowed': identity_matches and accepted and project_matches and active}

    def request_contract_handshake_async(self):
        """A read-only probe is independent of the outbound/pull queue gate.

        The existing periodic/reconnect pull notifications drive this method;
        no network work or waiting runs on their UI caller.
        """
        if self._session_restore_waiting():
            return self.request_session_recovery_async()
        with self._contract_lock:
            if (not self.is_v2_enabled or not self.supabase or is_forced_offline()
                    or self._auth_retry_blocked or self._shutting_down
                    or not self._contract_identity()):
                return False
            key = self._contract_context_key()
            if (self._contract_probe_pending is not None
                    or self._contract_handshake_inflight is not None
                    or self._contract_handshake_attempt == key
                    or self._contract_handshake_reading() is not None
                    or (self._contract_retry_key == key
                        and time.monotonic() < self._contract_retry_after)):
                return False
            ticket = object()
            self._contract_probe_pending = ticket

        def probe():
            with self._contract_lock:
                if key != self._contract_context_key():
                    return None
            return self._ensure_contract_handshake(expected_key=key)

        def complete(*_):
            with self._contract_lock:
                if self._contract_probe_pending is ticket:
                    self._contract_probe_pending = None
                current = key == self._contract_context_key()
            if current:
                self._publish_sync_state()

        try:
            worker = self._start_server_action(probe, complete)
            if worker is None:
                complete()
                return False
            return True
        except Exception:
            complete()
            raise

    def _check_contract_dispatch(self, request, context):
        key, store, client, authority = context
        if (key != self._contract_context_key() or not key[-1]
                or self._auth_retry_blocked or self._shutting_down
                or is_forced_offline() or not client
                or authority[0] != self._contract_write_epoch
                or not self._contract_authority_allows_dispatch()
                or not self._uses_contract_structure()):
            raise ContractDispatchPaused()
        counts = self._v2_activity_counts(key[4])
        if counts.get("blocked", 0) or counts.get("conflict", 0):
            raise ContractDispatchPaused()
        if authority[1] is None or authority[1] != self._contract_queue_stamp():
            raise ContractDispatchPaused()
        project = store.get_project(key[4])
        reading = self._contract_handshake_reading() or {}
        batch = request.get("batch", {}) if request else {}
        if (not request or request.get("project_id") != key[5]
                or batch.get("contract_version") != CONTRACT_VERSION
                or batch.get("canonical_contract_sha256") != CANONICAL_CONTRACT_SHA256
                or batch.get("sync_protocol_version") != SYNC_PROTOCOL_VERSION
                or set(batch.get("client_capabilities") or ()) != set(CLIENT_CAPABILITIES)
                or batch.get("writer_device_id") != self._v2_device_id
                or reading.get("project_sync_mode") != project["project_sync_mode"]
                or reading.get("migration_epoch") != project["migration_epoch"]
                or request.get("project_sync_mode") != project["project_sync_mode"]
                or request.get("migration_epoch") != project["migration_epoch"]):
            raise ContractDispatchPaused()

    def _contract_request_ready(self, request):
        try:
            with self._contract_lock:
                self._check_contract_dispatch(request, self._contract_dispatch_context())
            return True
        except (ContractDispatchPaused, TypeError, ValueError):
            return False

    def _send_contract_request(self, rpc_name, request, context, *, reviewed=None):
        key, store, client, _write_epoch = context
        batch_id = request["batch"]["batch_id"]
        with self._contract_lock:
            self._check_contract_dispatch(request, context)
            if reviewed is not None:
                reviewed.check_local()
            if batch_id in self._contract_preparing or batch_id in self._contract_sending:
                raise ContractDispatchPaused()
            cached = reviewed.cached_response() if reviewed is not None else store.document_batch_response(batch_id)
            if cached is not None:
                return cached
            # Preparation includes a potentially slow read. Duplicate recovery
            # notifications must not start additional reads for this batch.
            self._contract_preparing.add(batch_id)
        try:
            self.ensure_session_valid(client)
            with self._contract_lock:
                self._check_contract_dispatch(request, context)
                if reviewed is not None:
                    reviewed.check_local()
            # A contract-only read: never use the legacy compatibility fallback
            # or a cached/default active value to approve this send.
            status = self._response_data(execute_contract_rpc(client.rpc(
                "get_project_status", {"p_project_id": key[5]}
            )))
            with self._contract_lock:
                self._check_contract_dispatch(request, context)
                if (not isinstance(status, dict)
                        or not isinstance(status.get("project_id"), str)
                        or require_uuid(status["project_id"], "project_id") != key[5]
                        or status.get("state") not in {"active", "trashed", "purged"}):
                    raise SyncContractError("INVALID_ARGUMENT", "invalid project status")
                if status["state"] != "active":
                    self.mark_project_server_state(key[5], status["state"])
                    raise ContractDispatchPaused()
            if reviewed is not None:
                reviewed.trace('second_remote_before')
                reviewed.check_remote()
                reviewed.trace('second_remote_after')
            call = client.rpc(rpc_name, {"p_request": request})
            with self._contract_lock:
                if reviewed is not None:
                    reviewed.trace('rpc_constructed')
                # The status read and RPC construction can both yield. The
                # captured queue history also rejects blocked -> resolved ABA.
                self._check_contract_dispatch(request, context)
                cached = reviewed.cached_response() if reviewed is not None else store.document_batch_response(batch_id)
                if cached is not None:
                    return cached
                if reviewed is not None:
                    reviewed.before_http()
                self._contract_sending.add(batch_id)
            response = self._response_data(execute_contract_rpc(call))
            # A receipt belongs to the captured store even if the UI moved on.
            if reviewed is not None:
                receipt = reviewed.record_response(response)
            elif rpc_name == "atomic_structure_commit":
                receipt = store.record_structure_batch_response(batch_id, response)
            else:
                receipt = store.record_document_batch_response(batch_id, response)
            with self._contract_lock:
                if key == self._contract_context_key() and not receipt.get("applied"):
                    self._forget_stale_contract_handshake(receipt.get("error", {}).get("code", ""))
            return receipt
        except Exception as error:
            with self._contract_lock:
                if key == self._contract_context_key():
                    self._forget_stale_contract_handshake(error)
            raise
        finally:
            with self._contract_lock:
                self._contract_sending.discard(batch_id)
                self._contract_preparing.discard(batch_id)
