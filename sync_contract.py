"""Released WriterPad sync-contract 0.2.0 client primitives.

This module intentionally contains no Supabase credentials and never accepts
document bodies in diagnostics.  Contract-native writes fail closed unless the
server metadata, protocol, capabilities and digest all match the released pin.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass

from storage_name_tables import is_assigned_baseline, is_excluded_scalar
from unicode15_casefold import frozen_casefold


CONTRACT_VERSION = "0.2.0"
CONTRACT_GIT_COMMIT = "fcd99b7098b9a04bd93c585d89b16588aa482530"
CONTRACT_CONTENT_COMMIT = "7bcb5d25c5376b02469666df7318b90b456ffee6"
CANONICAL_CONTRACT_BYTES = 23256
CANONICAL_CONTRACT_SHA256 = (
    "416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670"
)
SYNC_PROTOCOL_VERSION = 3
STORAGE_NAME_ALGORITHM = "storage-name-v1"
STORAGE_NAME_UNICODE_VERSION = "15.0.0"
CLIENT_BUILD_ID = os.environ.get(
    "WRITERPAD_BUILD_ID", "writerpad-windows-stage8-contract-0.2.0"
)

CLIENT_CAPABILITIES = (
    "folders_authoritative",
    "tree_order_ids",
    "tombstones",
    "immutable_batch_contract_metadata",
    "operation_attempt_history",
    "operation_state_events",
    "storage_name_v1",
    "document_commit_v1",
)
SERVER_CAPABILITIES = (
    "atomic_structure_commit",
    "contract_allowlist_validation",
    "project_mode_migration_lock",
    "folder_tombstones",
    "id_tree_validation",
    "legacy_epoch_zero_adapter",
    "storage_name_v1",
    "document_commit_v1",
)

PROJECT_MODES = ("LEGACY", "MIGRATING", "ID_BASED")
ENTITY_KINDS = ("project", "folder", "document", "tree_order", "trash_purge")
INTENT_KINDS = (
    "ensure", "create", "update", "rename", "move", "delete", "restore",
    "reorder", "migrate",
)
TERMINAL_STATES = ("completed", "cancelled")
EVENT_STATE = {
    "enqueued": "pending",
    "dispatch_started": "inflight",
    "retry_scheduled": "retry_wait",
    "blocked": "blocked",
    "conflict_detected": "conflict",
    "committed": "completed",
    "replayed": "completed",
    "cancel_requested": "cancelled",
    "superseded": "cancelled",
}

_ASCII_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_RESERVED_BASENAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class SyncContractError(RuntimeError):
    """Stable fail-closed error carrying a contract error code."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class StorageName:
    normalized: str
    utf8: bytes

    @property
    def utf8_hex(self) -> str:
        return self.utf8.hex()


def _unicode15_module():
    try:
        import unicodedata2 as unicode_data
    except ImportError:
        import unicodedata as unicode_data
    if unicode_data.unidata_version != STORAGE_NAME_UNICODE_VERSION:
        raise SyncContractError(
            "UNICODE_VERSION_MISMATCH",
            f"Unicode {STORAGE_NAME_UNICODE_VERSION} required; "
            f"runtime provides {unicode_data.unidata_version}",
        )
    return unicode_data


def normalize_storage_name(value: str) -> StorageName:
    """Return the normative Unicode-15 storage-name collision key."""
    if not isinstance(value, str):
        raise SyncContractError("STORAGE_NAME_INVALID")
    for character in value:
        codepoint = ord(character)
        if character in "/\\" or codepoint <= 31 or codepoint == 127:
            raise SyncContractError("STORAGE_NAME_INVALID")
    unicode_data = _unicode15_module()
    normalized = unicode_data.normalize("NFKC", value)
    normalized = frozen_casefold(normalized)
    normalized = unicode_data.normalize("NFKC", normalized).rstrip(" .")
    if normalized in {"", ".", ".."}:
        raise SyncContractError("STORAGE_NAME_INVALID")
    if normalized.split(".", 1)[0] in _RESERVED_BASENAMES:
        raise SyncContractError("STORAGE_NAME_RESERVED")
    return StorageName(normalized=normalized, utf8=normalized.encode("utf-8"))


def _reject_supplementary_adjacency(value: str) -> None:
    """Reject a supplementary scalar followed by a combining mark or dakuten.

    Platform NFKC is where the two clients could still diverge, and it does so
    exactly around these sequences. The contract therefore rejects them before
    normalization runs rather than trusting the host Unicode version.
    """
    for index in range(len(value) - 1):
        if ord(value[index]) < 0x10000:
            continue
        following = value[index + 1]
        if unicodedata.combining(following) != 0 or following in "ﾞﾟ":
            raise SyncContractError("STORAGE_NAME_INVALID")


def normalize_storage_name_v2(value: str) -> StorageName:
    """Return the storage-name-v2 collision key from the frozen contract tables.

    Assignment, exclusion and case folding never consult the host Unicode
    database. Platform NFKC is still used, but it is bounded on both sides: the
    pre-normalization adjacency rejection above, and the defensive baseline
    re-check below.
    """
    if not isinstance(value, str):
        raise SyncContractError("STORAGE_NAME_INVALID")

    for character in value:
        codepoint = ord(character)
        if not is_assigned_baseline(codepoint):
            raise SyncContractError("STORAGE_NAME_UNASSIGNED")
        if is_excluded_scalar(codepoint):
            raise SyncContractError("STORAGE_NAME_UNSUPPORTED_SCALAR")

    _reject_supplementary_adjacency(value)

    normalized = unicodedata.normalize("NFKC", value)
    normalized = frozen_casefold(normalized)
    normalized = unicodedata.normalize("NFKC", normalized)

    # Separators are rejected, never rewritten, and the check runs after
    # normalization so scalars that normalize into a separator are caught too.
    for character in normalized:
        codepoint = ord(character)
        if character in "/\\" or codepoint <= 31 or codepoint == 127:
            raise SyncContractError("STORAGE_NAME_INVALID")
        if not is_assigned_baseline(codepoint):
            raise SyncContractError("STORAGE_NAME_UNASSIGNED")

    normalized = normalized.rstrip(" .")
    if normalized in {"", ".", ".."}:
        raise SyncContractError("STORAGE_NAME_INVALID")
    if normalized.split(".", 1)[0] in _RESERVED_BASENAMES:
        raise SyncContractError("STORAGE_NAME_RESERVED")
    return StorageName(normalized=normalized, utf8=normalized.encode("utf-8"))


def _validate_canonical_value(value):
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise SyncContractError("INVALID_ARGUMENT", "surrogate JSON strings are forbidden")
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, list):
        for item in value:
            _validate_canonical_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not _ASCII_KEY.fullmatch(key):
                raise SyncContractError("INVALID_ARGUMENT", "non-contract JSON key")
            _validate_canonical_value(item)
        return
    raise SyncContractError("INVALID_ARGUMENT", "contract JSON permits integers only")


def canonical_json(value) -> str:
    """Canonicalize the Stage-7 structural JSON subset of RFC 8785."""
    _validate_canonical_value(value)
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    )


def json_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_uuid(value, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SyncContractError("INVALID_ARGUMENT", f"invalid {field}") from exc


def _valid_project_mode_epoch(project_sync_mode, migration_epoch) -> bool:
    try:
        migration_epoch = int(migration_epoch)
    except (TypeError, ValueError):
        return False
    return (
        project_sync_mode == "LEGACY" and migration_epoch == 0
    ) or (
        project_sync_mode in {"MIGRATING", "ID_BASED"}
        and migration_epoch >= 1
    )


def _require_int(value, field):
    """Read one integer the server sent without letting int() raise its own error.

    Every other bad argument in this module leaves as a SyncContractError, so a
    null or a string arriving here has to do the same. A bare int() would raise
    TypeError or ValueError instead, and those escape callers that only expect
    the contract error.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SyncContractError("INVALID_ARGUMENT", f"invalid {field}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SyncContractError("INVALID_ARGUMENT", f"invalid {field}") from exc


def require_server_compatibility(
    *,
    project_sync_mode: str,
    migration_epoch: int,
    server_protocol_version: int,
    server_contract_sha256: str,
    server_capabilities,
):
    if not _valid_project_mode_epoch(project_sync_mode, migration_epoch):
        raise SyncContractError("STALE_MIGRATION_EPOCH")
    if _require_int(server_protocol_version, "server_protocol_version") < SYNC_PROTOCOL_VERSION:
        raise SyncContractError("PROTOCOL_TOO_OLD")
    if server_contract_sha256 != CANONICAL_CONTRACT_SHA256:
        raise SyncContractError("CONTRACT_DIGEST_MISMATCH")
    if isinstance(server_capabilities, (str, bytes)):
        # A bare string would iterate into single characters and read as a
        # capability set that happens to be missing everything.
        raise SyncContractError("INVALID_ARGUMENT", "invalid server_capabilities")
    try:
        available = set(server_capabilities or ())
    except TypeError as exc:
        raise SyncContractError(
            "INVALID_ARGUMENT", "invalid server_capabilities"
        ) from exc
    if not set(SERVER_CAPABILITIES).issubset(available):
        raise SyncContractError("CAPABILITY_MISMATCH")


HANDSHAKE_COMPATIBILITY_FIELDS = (
    "project_sync_mode",
    "migration_epoch",
    "server_protocol_version",
    "server_contract_sha256",
    "server_capabilities",
)


def read_handshake_compatibility(handshake):
    """Read a get_sync_handshake reply into the five compatibility arguments.

    The reply crosses the network, so nothing about its shape is settled until
    it is checked here. A server with no allowlist row answers in nulls and
    empty arrays, and those must not reach ``require_server_compatibility`` as
    if they were a real server state. Every shape problem leaves as
    INVALID_ARGUMENT so the caller has one fail-closed code to handle rather
    than a mix of contract errors and interpreter errors.

    The reply also carries three fields that ``require_server_compatibility``
    never sees, and each one can contradict the five that it does. A reply that
    disagrees with itself is checked here, because this is the only place that
    holds the whole answer at once.
    """
    if not isinstance(handshake, dict):
        raise SyncContractError("INVALID_ARGUMENT", "invalid handshake")

    if not isinstance(handshake.get("project_id"), str):
        raise SyncContractError("INVALID_ARGUMENT", "invalid project_id")
    require_uuid(handshake["project_id"], "project_id")

    mode = handshake.get("project_sync_mode")
    if not isinstance(mode, str) or mode not in PROJECT_MODES:
        raise SyncContractError("INVALID_ARGUMENT", "invalid project_sync_mode")

    epoch = handshake.get("migration_epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise SyncContractError("INVALID_ARGUMENT", "invalid migration_epoch")

    protocol_version = handshake.get("server_protocol_version")
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version < 0
    ):
        raise SyncContractError(
            "INVALID_ARGUMENT", "invalid server_protocol_version"
        )

    digest = handshake.get("server_contract_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise SyncContractError(
            "INVALID_ARGUMENT", "invalid server_contract_sha256"
        )

    capabilities = handshake.get("server_capabilities")
    if not isinstance(capabilities, (list, tuple)) or not all(
        isinstance(item, str) and item for item in capabilities
    ):
        raise SyncContractError(
            "INVALID_ARGUMENT", "invalid server_capabilities"
        )
    if len(set(capabilities)) != len(capabilities):
        raise SyncContractError("INVALID_ARGUMENT", "duplicate server_capabilities")

    _require_coherent_handshake(handshake, digest, protocol_version)

    return {
        "project_sync_mode": mode,
        "migration_epoch": epoch,
        "server_protocol_version": protocol_version,
        "server_contract_sha256": digest,
        "server_capabilities": list(capabilities),
    }


def _require_coherent_handshake(handshake, digest, protocol_version):
    """Refuse a reply whose extra fields contradict the ones acted on."""
    if "supported_protocol_versions" not in handshake:
        raise SyncContractError(
            "INVALID_ARGUMENT", "missing supported_protocol_versions"
        )
    supported_versions = handshake["supported_protocol_versions"]
    if not isinstance(supported_versions, (list, tuple)) or not supported_versions or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in supported_versions
    ):
        raise SyncContractError(
            "INVALID_ARGUMENT", "invalid supported_protocol_versions"
        )
    if len(set(supported_versions)) != len(supported_versions):
        raise SyncContractError("INVALID_ARGUMENT", "duplicate supported_protocol_versions")
    if SYNC_PROTOCOL_VERSION not in supported_versions:
        # server_protocol_version on its own is only a ceiling, so a server
        # that has dropped protocol 3 and answers 4 clears the >= check while
        # refusing everything this client can actually say. The set is the
        # field that says which versions are still accepted.
        raise SyncContractError("PROTOCOL_TOO_OLD")
    if protocol_version not in supported_versions:
        raise SyncContractError(
            "INVALID_ARGUMENT", "server_protocol_version outside its own set"
        )

    if "canonical_contract_sha256" not in handshake:
        raise SyncContractError(
            "INVALID_ARGUMENT", "missing canonical_contract_sha256"
        )
    canonical = handshake["canonical_contract_sha256"]
    if canonical != digest:
        # The digest the server allowlists and the digest it reports serving
        # are one identity. A server that gives two different answers has not
        # told this client which contract it is on.
        raise SyncContractError("CONTRACT_DIGEST_MISMATCH")

    if "contract_version" not in handshake:
        raise SyncContractError("INVALID_ARGUMENT", "missing contract_version")
    contract_version = handshake["contract_version"]
    if contract_version != CONTRACT_VERSION:
        # A digest that matches the pin while the version does not means the
        # allowlist row disagrees with itself about what 0.2.0 is.
        raise SyncContractError("CONTRACT_DIGEST_MISMATCH")


def build_atomic_structure_request(
    *, project_id, project_sync_mode, migration_epoch, writer_device_id,
    ordered_intents, batch_id=None, client_build_id=CLIENT_BUILD_ID,
):
    project_id = require_uuid(project_id, "project_id")
    writer_device_id = require_uuid(writer_device_id, "writer_device_id")
    batch_id = require_uuid(batch_id or uuid.uuid4(), "batch_id")
    if not _valid_project_mode_epoch(project_sync_mode, migration_epoch):
        raise SyncContractError("INVALID_ARGUMENT")
    if not isinstance(ordered_intents, list) or not ordered_intents:
        raise SyncContractError("INVALID_ARGUMENT")

    intents = []
    for sequence, source in enumerate(ordered_intents, 1):
        if not isinstance(source, dict):
            raise SyncContractError("INVALID_ARGUMENT")
        entity_kind = source.get("entity_kind")
        intent_kind = source.get("intent_kind")
        if entity_kind not in ENTITY_KINDS or intent_kind not in INTENT_KINDS:
            raise SyncContractError("INVALID_ARGUMENT")
        payload = source.get("payload")
        if not isinstance(payload, dict):
            raise SyncContractError("INVALID_ARGUMENT")
        if "name" in payload:
            normalize_storage_name(payload["name"])
        intent = {
            "sequence": sequence,
            "operation_id": require_uuid(source.get("operation_id") or uuid.uuid4(), "operation_id"),
            "batch_id": batch_id,
            "entity_kind": entity_kind,
            "entity_id": require_uuid(source.get("entity_id"), "entity_id"),
            "intent_kind": intent_kind,
            "base_revision": int(source.get("base_revision", 0)),
            "payload_sha256": json_sha256(payload),
            "payload": payload,
        }
        if intent["base_revision"] < 0:
            raise SyncContractError("INVALID_ARGUMENT")
        if source.get("supersedes_operation_id"):
            intent["supersedes_operation_id"] = require_uuid(
                source["supersedes_operation_id"], "supersedes_operation_id"
            )
        intents.append(intent)

    batch_payload_sha256 = json_sha256(intents)
    return {
        "kind": "atomic_structure_commit_request",
        "project_id": project_id,
        "project_sync_mode": project_sync_mode,
        "migration_epoch": int(migration_epoch),
        "batch": {
            "batch_id": batch_id,
            "writer_device_id": writer_device_id,
            "client_build_id": str(client_build_id),
            "sync_protocol_version": SYNC_PROTOCOL_VERSION,
            "contract_version": CONTRACT_VERSION,
            "canonical_contract_sha256": CANONICAL_CONTRACT_SHA256,
            "client_capabilities": list(CLIENT_CAPABILITIES),
            "batch_payload_sha256": batch_payload_sha256,
        },
        "ordered_intents": intents,
    }


def build_document_commit_request(
    *, project_id, project_sync_mode, migration_epoch, writer_device_id,
    document_id, intent_kind, base_revision, parent_folder_id, name, content,
    is_deleted, structure_revision, operation_id=None, batch_id=None,
    supersedes_operation_id=None, client_build_id=CLIENT_BUILD_ID,
):
    """Build the normative protocol-3 immutable document commit request."""
    project_id = require_uuid(project_id, "project_id")
    writer_device_id = require_uuid(writer_device_id, "writer_device_id")
    document_id = require_uuid(document_id, "document_id")
    operation_id = require_uuid(operation_id or uuid.uuid4(), "operation_id")
    batch_id = require_uuid(batch_id or uuid.uuid4(), "batch_id")
    if not _valid_project_mode_epoch(project_sync_mode, migration_epoch):
        raise SyncContractError("INVALID_ARGUMENT")
    if intent_kind not in {"create", "update", "delete", "restore"}:
        raise SyncContractError("INVALID_ARGUMENT")
    if not isinstance(content, str) or len(content.encode("utf-8")) > 10_485_760:
        raise SyncContractError("INVALID_ARGUMENT")
    base_revision = int(base_revision)
    structure_revision = int(structure_revision)
    if base_revision < 0 or structure_revision < 1:
        raise SyncContractError("INVALID_ARGUMENT")
    if (
        (intent_kind == "create" and base_revision != 0)
        or (intent_kind != "create" and base_revision < 1)
        or (intent_kind == "delete") != bool(is_deleted)
    ):
        raise SyncContractError("INVALID_ARGUMENT")
    normalized_name = str(name)
    normalize_storage_name(normalized_name)
    parent_folder_id = (
        require_uuid(parent_folder_id, "parent_folder_id")
        if parent_folder_id is not None else None
    )
    body = content.encode("utf-8")
    payload = {
        "parent_folder_id": parent_folder_id,
        "name": normalized_name,
        "content": content,
        "content_sha256": hashlib.sha256(body).hexdigest(),
        "content_byte_count": len(body),
        "is_deleted": bool(is_deleted),
        "structure_revision": structure_revision,
    }
    intent = {
        "sequence": 1,
        "operation_id": operation_id,
        "batch_id": batch_id,
        "entity_kind": "document",
        "document_id": document_id,
        "intent_kind": intent_kind,
        "base_revision": base_revision,
        "payload_sha256": json_sha256(payload),
        "payload": payload,
    }
    if supersedes_operation_id:
        intent["supersedes_operation_id"] = require_uuid(
            supersedes_operation_id, "supersedes_operation_id"
        )
    intents = [intent]
    return {
        "kind": "document_commit_request",
        "project_id": project_id,
        "project_sync_mode": project_sync_mode,
        "migration_epoch": int(migration_epoch),
        "batch": {
            "batch_id": batch_id,
            "writer_device_id": writer_device_id,
            "client_build_id": str(client_build_id),
            "sync_protocol_version": SYNC_PROTOCOL_VERSION,
            "contract_version": CONTRACT_VERSION,
            "canonical_contract_sha256": CANONICAL_CONTRACT_SHA256,
            "client_capabilities": list(CLIENT_CAPABILITIES),
            "batch_payload_sha256": json_sha256(intents),
        },
        "ordered_intents": intents,
    }


def validate_atomic_structure_response(request, response):
    """Reject partial, mismatched or capability-incompatible wire responses."""
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise SyncContractError("INVALID_ATOMIC_RESPONSE")
    batch = request.get("batch") or {}
    if response.get("batch_id") != batch.get("batch_id"):
        raise SyncContractError("INVALID_ATOMIC_RESPONSE", "batch_id mismatch")
    if response.get("batch_payload_sha256") != batch.get("batch_payload_sha256"):
        raise SyncContractError("INVALID_ATOMIC_RESPONSE", "batch digest mismatch")

    intents = request.get("ordered_intents") or []
    results = response.get("results")
    if response.get("kind") == "atomic_structure_commit_success":
        if set(response) != {
            "kind", "batch_id", "batch_payload_sha256", "status", "applied", "results"
        }:
            raise SyncContractError("INVALID_ATOMIC_RESPONSE")
        if response.get("status") not in {"committed", "replayed"} or response.get("applied") is not True:
            raise SyncContractError("INVALID_ATOMIC_RESPONSE")
        if not isinstance(results, list) or len(results) != len(intents):
            raise SyncContractError("PARTIAL_BATCH_RESPONSE")
        for intent, result in zip(intents, results):
            if not isinstance(result, dict) or set(result) != {
                "sequence", "operation_id", "entity_id", "result_revision"
            }:
                raise SyncContractError("INVALID_ATOMIC_RESPONSE")
            if (
                result.get("sequence") != intent.get("sequence")
                or result.get("operation_id") != intent.get("operation_id")
                or result.get("entity_id") != intent.get("entity_id")
                or not isinstance(result.get("result_revision"), int)
                or result["result_revision"] < 1
            ):
                raise SyncContractError("PARTIAL_BATCH_RESPONSE")
        return response

    if response.get("kind") == "atomic_structure_commit_failure":
        if set(response) != {
            "kind", "batch_id", "batch_payload_sha256", "status", "applied", "error", "results"
        }:
            raise SyncContractError("INVALID_ATOMIC_RESPONSE")
        error = response.get("error")
        if (
            response.get("status") != "rejected"
            or response.get("applied") is not False
            or results != []
            or not isinstance(error, dict)
            or set(error) != {"code", "message", "failed_sequence"}
            or not _ERROR_CODE.fullmatch(str(error.get("code") or ""))
            or not isinstance(error.get("message"), str)
            or not error["message"]
        ):
            raise SyncContractError("INVALID_ATOMIC_RESPONSE")
        failed = error.get("failed_sequence")
        if failed is not None and (not isinstance(failed, int) or not 1 <= failed <= len(intents)):
            raise SyncContractError("INVALID_ATOMIC_RESPONSE")
        return response

    raise SyncContractError("INVALID_ATOMIC_RESPONSE")


def validate_document_commit_response(request, response):
    """Reject partial or mismatched document responses before local apply."""
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise SyncContractError("INVALID_DOCUMENT_RESPONSE")
    batch = request.get("batch") or {}
    intents = request.get("ordered_intents") or []
    if (
        request.get("kind") != "document_commit_request"
        or len(intents) != 1
        or response.get("batch_id") != batch.get("batch_id")
        or response.get("batch_payload_sha256") != batch.get("batch_payload_sha256")
    ):
        raise SyncContractError("INVALID_DOCUMENT_RESPONSE")
    results = response.get("results")
    if response.get("kind") == "document_commit_success":
        if set(response) != {
            "kind", "batch_id", "batch_payload_sha256", "status", "applied", "results"
        } or response.get("status") not in {"committed", "replayed"} or response.get("applied") is not True:
            raise SyncContractError("INVALID_DOCUMENT_RESPONSE")
        if not isinstance(results, list) or len(results) != 1:
            raise SyncContractError("PARTIAL_BATCH_RESPONSE")
        result = results[0]
        intent = intents[0]
        payload = intent.get("payload") or {}
        expected_keys = {
            "sequence", "operation_id", "document_id", "result_revision",
            "structure_revision", "parent_folder_id", "name", "content_sha256",
            "content_byte_count", "is_deleted",
        }
        if not isinstance(result, dict) or set(result) != expected_keys:
            raise SyncContractError("INVALID_DOCUMENT_RESPONSE")
        if (
            result.get("sequence") != 1
            or result.get("operation_id") != intent.get("operation_id")
            or result.get("document_id") != intent.get("document_id")
            or not isinstance(result.get("result_revision"), int)
            or result["result_revision"] < 1
            or result.get("structure_revision") != payload.get("structure_revision")
            or result.get("parent_folder_id") != payload.get("parent_folder_id")
            or result.get("name") != payload.get("name")
            or result.get("content_sha256") != payload.get("content_sha256")
            or result.get("content_byte_count") != payload.get("content_byte_count")
            or result.get("is_deleted") is not payload.get("is_deleted")
        ):
            raise SyncContractError("PARTIAL_BATCH_RESPONSE")
        return response
    if response.get("kind") == "document_commit_failure":
        error = response.get("error")
        if (
            set(response) != {
                "kind", "batch_id", "batch_payload_sha256", "status",
                "applied", "error", "results",
            }
            or response.get("status") != "rejected"
            or response.get("applied") is not False
            or results != []
            or not isinstance(error, dict)
            or set(error) != {"code", "message", "failed_sequence"}
            or not _ERROR_CODE.fullmatch(str(error.get("code") or ""))
            or not isinstance(error.get("message"), str)
            or not error["message"]
            or error.get("failed_sequence") not in {None, 1}
        ):
            raise SyncContractError("INVALID_DOCUMENT_RESPONSE")
        return response
    raise SyncContractError("INVALID_DOCUMENT_RESPONSE")


_TRACE_KEYS = {
    "batch_id", "operation_id", "event_id", "attempt_id", "project_id",
    "entity_id", "sequence", "state", "outcome", "error_code",
    "request_sha256", "response_sha256", "payload_sha256", "protocol_version",
    "contract_sha256", "migration_epoch", "project_sync_mode", "rpc_name",
}


def safe_trace(event: str, **metadata) -> dict:
    """Return metadata-only diagnostics; bodies, tokens and URLs are discarded."""
    trace = {"event": str(event)[:80]}
    for key, value in metadata.items():
        if key not in _TRACE_KEYS or value is None:
            continue
        rendered = str(value)
        trace[key] = rendered[:160]
    return trace
