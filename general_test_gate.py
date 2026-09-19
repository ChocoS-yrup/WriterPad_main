"""Preparation-only gate controller for one named Staging test project.

The durable send hold precedes every probe and survives activation/restart.
Release is a separate explicit action after another fresh handshake and pull.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import json
import tempfile

from general_test_gate_target import PROJECT_ID, PROJECT_NAME, STAGING_URL, EXPECTED_FOLDERS, EXPECTED_ORDERS
from project_creation_v1 import audit
from project_identity_v1 import read_identity
from sync_contract import CONTRACT_VERSION, CANONICAL_CONTRACT_SHA256, SYNC_PROTOCOL_VERSION


class PreparationError(RuntimeError):
    pass


def _hold_path(store):
    return Path(store.db_path).absolute().parent / (".general-test-send-hold-" + PROJECT_ID)


def writes_held(manager, context=None):
    """No credentials/DB mutation; safe at both dispatcher and HTTP boundaries."""
    if context is None:
        project_id = (getattr(manager, "_v2_context", None) or {}).get("project_id")
        store = getattr(manager, "_v2_store", None)
    else:
        project_id, store = context[0][5], context[1]
    if project_id != PROJECT_ID:
        return False
    try:
        path = _hold_path(store)
        path.lstat()
        try:
            if path.stat().st_size > 4096:
                return True
            state = json.loads(path.read_text(encoding="utf-8"))
            return not (isinstance(state, dict) and state.get("state") == "released"
                        and state.get("project_id") == PROJECT_ID and state.get("format") == 1)
        except (ValueError, UnicodeError):
            return True
    except FileNotFoundError:
        return False
    except (OSError, AttributeError, TypeError):
        return True


def _target(manager):
    context = manager._v2_context or {}
    if (not manager.is_v2_enabled or context.get("project_id") != PROJECT_ID
            or context.get("project_name") != PROJECT_NAME):
        raise PreparationError("지정된 일반 동기화 시험 작품을 먼저 여세요.")
    configured = getattr(getattr(manager, "_cloud_config", None), "url", "")
    connected = getattr(manager.supabase, "supabase_url", "")
    if str(configured).rstrip("/") != STAGING_URL or str(connected).rstrip("/") != STAGING_URL:
        raise PreparationError("현재 연결이 지정된 Staging과 일치하지 않습니다.")
    if not manager._contract_identity():
        raise PreparationError("현재 앱의 로그인이 필요합니다.")
    root = getattr(getattr(manager, "_v2_wpm", None), "writing_root_path", None)
    if not root or manager._v2_store.local_key_for(root) != context.get("local_key"):
        raise PreparationError("집필 화면의 로컬 작품 연결이 변경됐습니다.")


def _idle(manager, *, allow_pull=False):
    if (manager._auth_retry_blocked or manager._shutting_down
            or getattr(manager, "_review_execution_busy", False)
            or manager._active_server_syncs or manager._v2_worker is not None
            or manager._v2_structure_worker is not None
            or getattr(manager, "_active_backups", 0)
            or manager._contract_sending or manager._contract_preparing
            or getattr(manager, "_retry_active_key", None) is not None
            or getattr(manager, "_server_action_workers", ())):
        raise PreparationError("다른 동기화 작업이 끝난 뒤 다시 확인하세요.")
    counts = manager._v2_store.counts(manager._v2_context["local_key"])
    if counts["total"] or manager._retry_queue:
        raise PreparationError("미완료·차단·충돌 작업이 남아 있어 준비를 진행하지 않았습니다.")
    coordinator = manager._current_pull_coordinator()
    if not allow_pull and (coordinator["pulling"] or manager._v2_pull_worker is not None):
        raise PreparationError("진행 중인 수신이 끝난 뒤 다시 확인하세요.")


def _gates(manager):
    with manager._v2_store._reader() as connection:
        return {row["local_key"]: bool(row["contract_path_enabled"]) for row in
                connection.execute("SELECT local_key, contract_path_enabled FROM sync_projects")}


def begin(manager):
    """Owner-thread local reservation. No handshake, pull or gate activation."""
    with manager._contract_lock:
        _target(manager)
        _idle(manager)
        if manager._contract_handshake_inflight is not None:
            raise PreparationError("현재 계약 확인이 끝난 뒤 다시 시도하세요.")
        if manager.contract_path_enabled() and not writes_held(manager):
            raise PreparationError("송신 보류 없이 이미 열린 관문은 이 준비 경로에서 변경하지 않습니다.")
        path = _hold_path(manager._v2_store)
        # Exclusive creation, no overwrite. Existing or malformed holds remain
        # fail closed and are never auto-cleared after errors or cancellation.
        if not writes_held(manager):
            with path.open("x", encoding="ascii") as handle:
                handle.write("preparation-only; no outbound release\n")
                handle.flush()
                os.fsync(handle.fileno())
        ticket = {
            "key": manager._contract_context_key(), "gates": _gates(manager),
            "queue_stamp": manager._contract_queue_stamp(),
            "pull_sequence": None, "ready": False,
            "checkpoint": _checkpoint(manager),
        }
        manager._general_test_ticket = ticket
        return ticket


def _validate(manager, ticket):
    _target(manager)
    if ticket.get("view_guard") is not None:
        ticket["view_guard"]()
    if (ticket is not getattr(manager, "_general_test_ticket", None)
            or ticket["key"] != manager._contract_context_key()
            or not writes_held(manager)):
        raise PreparationError("작품·계정·연결이 변경됐습니다. 준비 확인을 다시 시작하세요.")
    if ticket["queue_stamp"] != manager._contract_queue_stamp():
        raise PreparationError("준비 중 로컬 작업이 변경됐습니다. 송신 보류를 유지합니다.")


def handshake(manager, ticket):
    with manager._contract_lock:
        _validate(manager, ticket)
        _idle(manager)
    manager.perform_contract_handshake(require_connection=True,
        _checkpoint_acceptor=lambda compatibility, reading: _accept_checkpoint(manager, ticket, compatibility, reading))
    with manager._contract_lock:
        _validate(manager, ticket)
        _pins(manager)


def _checkpoint(manager):
    row = manager._v2_store.get_project(manager._v2_context["local_key"])
    return row["project_sync_mode"], row["migration_epoch"]


def _accept_checkpoint(manager, ticket, compatibility, reading):
    from server_checkpoint import accept, source_record
    def guard():
        _validate(manager, ticket)
        _idle(manager)
        if manager.contract_path_enabled() or _checkpoint(manager) != ticket["checkpoint"]:
            raise PreparationError("로컬 전환 기록 또는 관문이 변경됐습니다. 송신 보류를 유지합니다.")
    _validate(manager, ticket)
    _idle(manager)
    if (compatibility["project_sync_mode"], compatibility["migration_epoch"]) != ("ID_BASED", 1):
        raise PreparationError("서버 상태가 지정 시험의 ID_BASED/1과 다릅니다.")
    if ticket["checkpoint"] == ("ID_BASED", 1):
        if _checkpoint(manager) != ticket["checkpoint"]:
            raise PreparationError("로컬 전환 기록이 변경됐습니다.")
        return manager.activate_contract_project(**compatibility)
    context = manager._v2_context
    row, changed = accept(manager._v2_store,
        binding=(context["local_key"], PROJECT_ID, PROJECT_NAME), expected_checkpoint=ticket["checkpoint"],
        compatibility=compatibility, source=source_record(reading), guard=guard)
    manager._v2_context.update(project_sync_mode=row["project_sync_mode"], migration_epoch=row["migration_epoch"])
    ticket["checkpoint"] = (row["project_sync_mode"], row["migration_epoch"])
    if changed:
        manager._begin_structure_authority_selection()
        coordinator = manager._current_pull_coordinator()
        coordinator.update(baseline_validated=False, applied_snapshot_fingerprint=None, applied_index_fingerprint=None)
    return row


def _pins(manager):
    row = manager._v2_store.get_project(manager._v2_context["local_key"])
    if (not manager.contract_handshake_is_fresh()
            or row["project_sync_mode"] != "ID_BASED" or row["migration_epoch"] != 1
            or row["active_contract_sha256"] != CANONICAL_CONTRACT_SHA256
            or row["server_protocol_version"] != SYNC_PROTOCOL_VERSION
            or CONTRACT_VERSION != "0.2.0" or SYNC_PROTOCOL_VERSION != 3):
        raise PreparationError("새 핸드셰이크가 시험 작품의 계약·mode·epoch와 맞지 않습니다.")


def start_pull(manager, ticket):
    """Owner thread: reuse the existing pull/coordinator/apply, never a new manager."""
    with manager._contract_lock:
        _validate(manager, ticket)
        _idle(manager)
        _pins(manager)
        coordinator = manager._current_pull_coordinator()
        ticket["pull_sequence"] = coordinator.get("completed_baseline_sequence", 0) + 1
        coordinator["applied_snapshot_fingerprint"] = None
        coordinator["applied_index_fingerprint"] = None
    if not manager.pull_remote_changes_async(manual=True, retry_pending_after_pull=False, reason="baseline"):
        raise PreparationError("새 수신을 시작하지 못했습니다. 송신 보류를 유지합니다.")


def _baseline(manager, ticket):
    coordinator = manager._current_pull_coordinator()
    if (ticket["pull_sequence"] is None
            or coordinator.get("completed_baseline_sequence", 0) < ticket["pull_sequence"]
            or not coordinator["baseline_validated"] or coordinator["pulling"]
            or manager._v2_pull_worker is not None
            or manager._v2_structure_authority != "contract"
            or not manager._contract_authority_observation()["allowed"]):
        raise PreparationError("새 UUID 구조 수신 또는 C9 준비 확인이 완료되지 않았습니다.")
    key = manager._v2_context["local_key"]
    with manager._v2_store._reader() as connection:
        folders = [dict(row) for row in connection.execute(
            "SELECT folder_id, parent_folder_id, name, revision, is_deleted FROM sync_folders WHERE local_key = ?", (key,))]
        orders = [dict(row) for row in connection.execute(
            "SELECT tree_order_id, parent_folder_id, children_json, revision FROM sync_tree_orders WHERE local_key = ?", (key,))]
    import json
    actual_folders = {row["folder_id"]: (row["parent_folder_id"], row["name"], row["revision"], bool(row["is_deleted"])) for row in folders}
    expected_folders = {row["id"]: (row["parent"], row["name"], row["revision"], row["deleted"]) for row in EXPECTED_FOLDERS}
    actual_orders = {row["tree_order_id"]: (row["parent_folder_id"], json.loads(row["children_json"]), row["revision"]) for row in orders}
    expected_orders = {row["id"]: (row["parent"], row["children"], row["revision"]) for row in EXPECTED_ORDERS}
    if actual_folders != expected_folders or actual_orders != expected_orders:
        raise PreparationError("수신한 폴더·정렬표가 고정 시험 기준과 다릅니다.")
    root = str(Path(manager._v2_wpm.writing_root_path).parent)
    identity = read_identity(root)
    local = {node["uuid"]: (node["parent_uuid"], node["title"]) for node in identity["nodes"] if node["kind"] == "folder"}
    if (identity["project"]["uuid"] != PROJECT_ID or any(audit(root).values())
            or local != {row["id"]: (row["parent"], row["name"]) for row in EXPECTED_FOLDERS}
            or any(node["kind"] == "document" for node in identity["nodes"])):
        raise PreparationError("실제 로컬 작품 구조가 시험 기준과 다릅니다.")
    return len(orders)


def _ready(manager, ticket):
    _validate(manager, ticket)
    _idle(manager)
    _pins(manager)
    from contract_readiness_diagnostics import observe_readiness
    readiness = observe_readiness(manager, ticket["key"], executing=True)
    if not readiness["all_conditions_met"]:
        raise PreparationError("현재 송신 조정기(C9)의 준비 조건이 충족되지 않았습니다.")
    return _baseline(manager, ticket)


def observation(manager, ticket):
    """Only succeeds for a fresh full apply on this manager and exact baseline."""
    with manager._contract_lock:
        count = _ready(manager, ticket)
        gates = _gates(manager)
        key = manager._v2_context["local_key"]
        others_unchanged = {k:v for k,v in gates.items() if k != key} == {k:v for k,v in ticket["gates"].items() if k != key}
        if not others_unchanged:
            raise PreparationError("다른 작품의 관문 상태가 달라져 완료 판정을 중단했습니다.")
        ticket["ready"] = True
        from contract_readiness_diagnostics import observe_readiness
        return {
            "project_id": PROJECT_ID, "observed_at": datetime.now(timezone.utc).isoformat(),
            "mode": "ID_BASED", "epoch": 1, "contract_version": CONTRACT_VERSION,
            "contract_sha256": CANONICAL_CONTRACT_SHA256, "protocol": SYNC_PROTOCOL_VERSION,
            "handshake_fresh": True, "gate_open": manager.contract_path_enabled(),
            "contract_path_selected": manager._uses_contract_structure(),
            "received_uuid_orders": count, "baseline_ready": True, "pending_operations": 0,
            "other_project_gates_unchanged": others_unchanged, "outbound_send_held": True,
            "c9": observe_readiness(manager, ticket["key"], executing=True),
            "installed_build_verified": None,
            "installed_build_note": "runtime binding is verified; installed artifact identity requires release verification",
            "actual_app_runtime_evidence": {"pid": os.getpid(), "frozen": bool(getattr(sys, "frozen", False)),
                "controller": "general-test-gate-20260910", "manager": "bound application instance"},
            "outbound_test_requests": None,
            "outbound_count_scope": "not an HTTP traffic counter; dispatch is durably held for this project",
        }


def activate(manager, ticket):
    with manager._contract_lock:
        if not ticket.get("ready"):
            raise PreparationError("먼저 준비 확인·수신을 완료하세요.")
        _ready(manager, ticket)
    # The existing product activation always takes a NEW handshake. Its guard
    # rechecks the captured context/C9/queue immediately before changing the gate.
    manager.enable_contract_path(_before_open=lambda: _ready(manager, ticket))
    return observation(manager, ticket)


def _write_control(manager, state):
    """Atomic replacement: a failed write leaves the previous durable hold."""
    path = _hold_path(manager._v2_store)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + "-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"format": 1, "project_id": PROJECT_ID, "state": state,
                       "recorded_at": datetime.now(timezone.utc).isoformat()}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_release(manager, ticket):
    with manager._contract_lock:
        _ready(manager, ticket)
        if not manager.contract_path_enabled():
            raise PreparationError("시험 관문을 먼저 확인하세요.")
        ticket["release_pull_sequence"] = manager._current_pull_coordinator().get("completed_baseline_sequence", 0) + 1
    # The UI starts a fresh full pull after this worker finishes.
    manager.perform_contract_handshake(require_connection=True)
    with manager._contract_lock:
        _ready(manager, ticket)


def release(manager, ticket):
    """Explicit opt-in after a NEW baseline. No queue creation or dispatch."""
    with manager._contract_lock:
        _ready(manager, ticket)
        if (not manager.contract_path_enabled() or not ticket.get("release_pull_sequence")
                or ticket["pull_sequence"] < ticket["release_pull_sequence"]):
            raise PreparationError("보류 해제용 새 수신과 열린 관문 확인이 필요합니다.")
    manager.perform_contract_handshake(require_connection=True)
    with manager._contract_lock:
        report = observation(manager, ticket)
        if not manager.contract_path_enabled():
            raise PreparationError("관문이 닫혀 있습니다. 송신 보류를 유지합니다.")
        # All potentially failing checks finish before atomic publication.
        # Old captured dispatch contexts lose authority before release.
        manager._contract_write_epoch += 1
        manager._v2_retry_context = None
        _write_control(manager, "released")
        ticket["released"] = True
        report["outbound_send_held"] = False
        report["outbound_count_scope"] = "release issues no writes; runtime traffic is not measured"
        return report


def dispatch_generation_current(manager, context):
    return context[0][5] != PROJECT_ID or context[3][0] == manager._contract_write_epoch


def current_observation(manager, ticket):
    if not ticket.get("released"):
        return observation(manager, ticket)
    # After the first edit, the fixed EMPTY fixture must no longer police
    # general sync. Only current ordinary contract/C9/queue conditions apply.
    from contract_readiness_diagnostics import observe_readiness
    with manager._contract_lock:
        _target(manager)
        if ticket["key"] != manager._contract_context_key() or writes_held(manager):
            raise PreparationError("문맥 또는 보류 상태가 변경됐습니다.")
        _pins(manager)
        return {"project_id": PROJECT_ID, "observed_at": datetime.now(timezone.utc).isoformat(),
                "mode": "ID_BASED", "epoch": 1, "contract_version": CONTRACT_VERSION,
                "protocol": SYNC_PROTOCOL_VERSION, "contract_sha256": CANONICAL_CONTRACT_SHA256,
                "handshake_fresh": True,
                "gate_open": manager.contract_path_enabled(), "outbound_send_held": False,
                "contract_path_selected": manager._uses_contract_structure(),
                "pending_operations": manager._v2_store.counts(manager._v2_context["local_key"])["total"],
                "c9": observe_readiness(manager, ticket["key"], executing=True),
                "outbound_test_requests": None, "installed_build_verified": None,
                "actual_app_runtime_evidence": {"pid": os.getpid(), "frozen": bool(getattr(sys, "frozen", False)),
                    "controller": "general-test-checkpoint-release-20260910", "manager": "bound application instance"},
                "scope": "current general sync after explicit release; not the fixed empty baseline"}


def reprepare(manager):
    """Local explicit stop before another experiment. Never reset user content."""
    with manager._contract_lock:
        _target(manager)
        _idle(manager)
        _write_control(manager, "held")
        manager.disable_contract_path()
        manager._begin_structure_authority_selection()
        manager._current_pull_coordinator()["baseline_validated"] = False
        return begin(manager)
