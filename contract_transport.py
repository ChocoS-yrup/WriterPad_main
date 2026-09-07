"""Per-call HTTP evidence for contract RPCs; never log response bodies.

Keep the pinned SDK's request/auth/JSON conversion, but retain the status it
otherwise loses when converting a PostgREST JSON error into APIError. Copies
are private to this call; shared SDK sessions and other RPCs are not patched.
"""
from copy import copy

from postgrest._sync.request_builder import SyncRPCFilterRequestBuilder
from postgrest.exceptions import APIError

from sync_contract import SyncContractError


CONTRACT_REFUSALS = frozenset({
    "AUTH_REQUIRED", "AUTH_EXPIRED", "FORBIDDEN", "INVALID_ARGUMENT",
    "CONTRACT_NOT_ALLOWED", "CONTRACT_DIGEST_MISMATCH", "PROTOCOL_TOO_OLD",
    "CAPABILITY_MISMATCH", "STALE_MIGRATION_EPOCH", "PROJECT_MIGRATING",
    "MIGRATION_LOCKED", "PROJECT_TRASHED", "PROJECT_PURGED",
})


def execute_contract_rpc(call):
    if not isinstance(call, SyncRPCFilterRequestBuilder):
        # Local adapters and test doubles implement the same execute interface.
        return call.execute()
    local_call = copy(call)
    local_call.request = copy(call.request)
    # Only the lifecycle owns retries: every new send needs a new gate check.
    local_call.request.retry_enabled = False
    observed = {}
    send = local_call.request.send

    def send_once(headers):
        response = send(headers)
        observed["status"] = response.status_code
        return response

    local_call.request.send = send_once
    try:
        return local_call.execute()
    except APIError as error:
        status = observed.get("status")
        message = getattr(error, "message", "")
        code = getattr(error, "code", "")
        if message in CONTRACT_REFUSALS:
            raise SyncContractError(message) from error
        if code in {"PGRST301", "PGRST303"} or status == 401:
            raise SyncContractError("AUTH_EXPIRED") from error
        if code == "42501" or status == 403:
            raise SyncContractError("FORBIDDEN") from error
        # Preserve the original SDK error and its server code for existing
        # conflict handling. No request/response content is copied to logs.
        error.status_code = status
        raise
