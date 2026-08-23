"""Preflight for the contract write path. Three modes, in widening scope.

  (no flag)                the client pin, plus the local gate and the server
                           state stored beside it, read out of the live
                           database with mode=ro. No network.

  --rpc-handshake          a read-only RPC preflight. Calls get_sync_handshake
                           once per project and validates the reply the way the
                           client validates it, entirely in memory. Proves what
                           the server answers. Proves nothing about the client
                           wiring, because it does not use it.

  --application-handshake  the end-to-end check. Runs the real
                           SyncManager.perform_contract_handshake() against a
                           throwaway copy of the database, lets it record what
                           it normally records, and then proves the gate held:
                           still 0, still LEGACY at epoch 0, no contract batch
                           and no protocol 3 operation created, and a document
                           write that still comes out legacy. Exits non-zero if
                           any of that is false.

Nothing here opens the gate or promotes a project, and nothing writes to the
live database: the application mode works on a copy. No project names, no
paths, no document content and no credentials are printed; project ids and
metadata only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sync_contract import (  # noqa: E402
    CANONICAL_CONTRACT_BYTES,
    CANONICAL_CONTRACT_SHA256,
    CONTRACT_CONTENT_COMMIT,
    CONTRACT_GIT_COMMIT,
    CONTRACT_VERSION,
    SERVER_CAPABILITIES,
    SYNC_PROTOCOL_VERSION,
    SyncContractError,
    read_handshake_compatibility,
    require_server_compatibility,
)

CONTRACT_COLUMNS = (
    "contract_path_enabled",
    "contract_path_enabled_at",
    "project_sync_mode",
    "migration_epoch",
    "server_protocol_version",
    "active_contract_sha256",
    "server_capabilities_json",
    "contract_validated_at",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_database() -> Path:
    from runtime_profile import app_data_dir

    return Path(app_data_dir()) / "sync_v2.sqlite3"


def show(key, value):
    if value is None:
        value = ""
    if isinstance(value, bool):
        value = "true" if value else "false"
    print(f"{key}={value}")


def print_client_pin():
    print("[client pin]")
    show("contract_version", CONTRACT_VERSION)
    show("canonical_contract_sha256", CANONICAL_CONTRACT_SHA256)
    show("canonical_contract_bytes", CANONICAL_CONTRACT_BYTES)
    show("contract_git_commit", CONTRACT_GIT_COMMIT)
    show("contract_content_commit", CONTRACT_CONTENT_COMMIT)
    show("sync_protocol_version", SYNC_PROTOCOL_VERSION)
    for index, capability in enumerate(SERVER_CAPABILITIES, 1):
        show(f"required_server_capability.{index}", capability)
    print()


def read_projects(database: Path):
    """Open the live database read-only and take the contract columns out."""
    # mode=ro and not immutable=1: immutable tells SQLite the file cannot
    # change and to skip the write-ahead log entirely. A gate that was closed
    # in a WAL frame would read as whatever the main file still said, which is
    # the one thing this must not get wrong.
    uri = database.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        present = {
            row[1] for row in connection.execute("PRAGMA table_info(sync_projects)")
        }
        columns = ["project_id"] + [
            column for column in CONTRACT_COLUMNS if column in present
        ]
        rows = connection.execute(
            f"SELECT {', '.join(columns)} FROM sync_projects ORDER BY project_id"
        ).fetchall()
        return (
            int(connection.execute("PRAGMA user_version").fetchone()[0]),
            present,
            [dict(row) for row in rows],
        )
    finally:
        connection.close()


def stored_compatibility(project):
    """What require_server_compatibility says about the stored server state."""
    if not project.get("active_contract_sha256"):
        # No handshake has ever written here. Saying PROTOCOL_TOO_OLD about an
        # empty row would read as a server that answered badly.
        return "NOT RECORDED"
    try:
        require_server_compatibility(
            project_sync_mode=project.get("project_sync_mode") or "LEGACY",
            migration_epoch=int(project.get("migration_epoch") or 0),
            server_protocol_version=int(project.get("server_protocol_version") or 0),
            server_contract_sha256=project.get("active_contract_sha256") or "",
            server_capabilities=json.loads(
                project.get("server_capabilities_json") or "[]"
            ),
        )
    except (SyncContractError, TypeError, ValueError) as error:
        return getattr(error, "code", type(error).__name__)
    return "PASS"


def print_projects(user_version, present, projects, gate_column=True):
    print("[local database]")
    show("user_version", user_version)
    show("projects", len(projects))
    if not gate_column:
        show(
            "gate_column",
            "ABSENT -- this database has not been opened by a build that has "
            "the gate; it is added, closed, on first open",
        )
    else:
        show(
            "projects_with_gate_open",
            sum(1 for row in projects if row.get("contract_path_enabled")),
        )
    print()

    for index, project in enumerate(projects, 1):
        print(f"[project {index}]")
        show("project_id", project["project_id"])
        gate_open = bool(project.get("contract_path_enabled"))
        # An absent column is not a column reading false. Saying false would
        # claim this database has a gate and it is shut, which is a stronger
        # thing than is known.
        show(
            "contract_path_enabled",
            gate_open if gate_column else "ABSENT (treated as closed)",
        )
        show(
            "contract_path_enabled_at",
            project.get("contract_path_enabled_at") if gate_column else "ABSENT",
        )
        show("project_sync_mode", project.get("project_sync_mode"))
        show("migration_epoch", project.get("migration_epoch"))
        show("server_protocol_version", project.get("server_protocol_version"))
        show("active_contract_sha256", project.get("active_contract_sha256"))
        capabilities = json.loads(project.get("server_capabilities_json") or "[]")
        show("server_capabilities_count", len(capabilities))
        show("contract_validated_at", project.get("contract_validated_at"))
        compatibility = stored_compatibility(project)
        show("stored_server_compatibility", compatibility)
        # The third condition lives in the running process and cannot be read
        # from here, so this is the ceiling rather than the answer.
        show("would_use_contract_path", gate_open and compatibility == "PASS")
        print()


QUEUE_COUNTERS = (
    ("operations_total", "sync_operations", "1 = 1"),
    ("operations_protocol_3", "sync_operations", "sync_protocol_version >= 3"),
    ("operations_with_batch_id", "sync_operations", "batch_id IS NOT NULL"),
    (
        "operations_contract_batch",
        "sync_operations",
        "provenance_kind = 'CONTRACT_BATCH'",
    ),
    ("contract_batches", "sync_contract_batches", "1 = 1"),
    ("documents", "sync_documents", "1 = 1"),
)


def call_handshake(client, project_id):
    response = client.rpc("get_sync_handshake", {
        "p_project_id": project_id,
        "p_contract_sha256": CANONICAL_CONTRACT_SHA256,
    }).execute()
    data = getattr(response, "data", response)
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    return data


def print_handshake(project_id, reply):
    if not isinstance(reply, dict):
        show("reply", "NOT A JSON OBJECT")
        show("verdict", "STOP -- unreadable reply")
        return

    show("supported", reply.get("supported") is True)
    show("project_sync_mode", reply.get("project_sync_mode"))
    show("migration_epoch", reply.get("migration_epoch"))
    show("contract_version", reply.get("contract_version"))
    show("server_protocol_version", reply.get("server_protocol_version"))
    show(
        "supported_protocol_versions",
        json.dumps(reply.get("supported_protocol_versions")),
    )
    show("server_contract_sha256", reply.get("server_contract_sha256"))
    show("canonical_contract_sha256", reply.get("canonical_contract_sha256"))
    capabilities = reply.get("server_capabilities")
    show(
        "server_capabilities_count",
        len(capabilities) if isinstance(capabilities, list) else "",
    )
    show(
        "reply_sha256",
        hashlib.sha256(
            json.dumps(reply, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )

    if reply.get("project_id") not in (None, project_id):
        show("verdict", "STOP -- the reply is about another project")
        return
    if reply.get("supported") is not True:
        show("compatibility", "n/a")
        show("verdict", "server does not support this client; nothing to open")
        return

    try:
        compatibility = read_handshake_compatibility(reply)
        require_server_compatibility(**compatibility)
    except SyncContractError as error:
        show("compatibility", error.code)
        show("verdict", f"STOP -- {error.code}")
        return

    show("compatibility", "PASS")
    mode = reply.get("project_sync_mode")
    epoch = reply.get("migration_epoch")
    if (mode, epoch) != ("LEGACY", 0):
        # An allowlisted contract does not promote a project. Anything else
        # means somebody ran a promotion, which is a separate approval.
        show(
            "verdict",
            f"STOP -- expected LEGACY/0, the server answered {mode}/{epoch}",
        )
        return
    show("verdict", "supported, LEGACY/0, compatible -- gate may be considered")


def run_handshakes(projects, only_project):
    """One fresh handshake per project. Nothing here is written down."""
    from sync_manager import SyncManager

    client = SyncManager.create_supabase_client()
    if client is None:
        print("[handshake]")
        show("client", "UNAVAILABLE -- cloud config is not ready")
        print()
        return
    if not getattr(client, "_antigravity_authenticated", False):
        print("[handshake]")
        show("client", "NOT AUTHENTICATED -- sign in from the app first")
        print()
        return

    for index, project in enumerate(projects, 1):
        project_id = project["project_id"]
        if only_project and project_id != only_project:
            continue
        print(f"[handshake project {index}]")
        show("project_id", project_id)
        try:
            reply = call_handshake(client, project_id)
        except Exception as error:
            # Deliberately not the exception text: a transport error can carry
            # a URL or a token fragment.
            show("call", f"FAILED ({type(error).__name__})")
            show("verdict", "STOP -- the handshake did not complete")
            print()
            continue
        show("call", "OK")
        print_handshake(project_id, reply)
        print()



def copy_database(live: Path, destination: Path):
    """Take a consistent copy of the live database without writing to it.

    A file copy of a database with a live write-ahead log can catch it
    mid-transaction. The backup API reads a committed snapshot, and the source
    connection is opened read-only so this cannot touch the original.
    """
    source = sqlite3.connect(live.as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def queue_snapshot(database: Path):
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counters = {}
        for name, table, where in QUEUE_COUNTERS:
            counters[name] = (
                int(connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {where}"
                ).fetchone()[0])
                if table in tables else 0
            )
        return counters
    finally:
        connection.close()


def project_snapshot(database: Path):
    _, _, rows = read_projects(database)
    return {row["project_id"]: row for row in rows}


def diff_rows(before, after, keys):
    return [
        (key, before.get(key), after.get(key))
        for key in keys
        if before.get(key) != after.get(key)
    ]


class Checks:
    """Every invariant the application run has to hold, and whether it did."""

    def __init__(self):
        self.results = []

    def require(self, name, condition, detail=""):
        self.results.append((name, bool(condition), detail))
        return bool(condition)

    @property
    def failed(self):
        return [item for item in self.results if not item[1]]

    def report(self):
        print("[checks]")
        for name, passed, detail in self.results:
            show(name, "PASS" if passed else f"FAIL {detail}".strip())
        print()


def build_manager(store, project, device_id):
    """A manager attached to the copy, holding one project, and nothing else."""
    from sync_manager import SyncManager

    manager = SyncManager()
    manager.release_v2()
    manager._v2_store = store
    manager._v2_context = {
        "local_key": project["local_key"],
        "project_id": project["project_id"],
        "writer_device_id": device_id,
    }
    manager._v2_device_id = device_id
    manager._forget_contract_handshake()
    return manager


def run_application_handshake(live: Path, only_project: str):
    """Drive the real client path against a copy and prove the gate held."""
    from sync_manager import SyncManager
    from sync_v2_store import SyncV2Store

    checks = Checks()
    workspace = Path(tempfile.mkdtemp(prefix="contract-preflight-"))
    copy = workspace / "sync_v2.sqlite3"
    device_id = str(uuid.uuid4())
    attached = []

    try:
        copy_database(live, copy)
        print("[application handshake]")
        show("live_database_written", False)
        show("working_copy", "a throwaway snapshot; the live file is not opened for write")
        print()

        before_projects = project_snapshot(copy)
        before_queue = queue_snapshot(copy)

        store = SyncV2Store(str(copy))
        rows = [
            row for row in store.list_projects()
            if not only_project or row["project_id"] == only_project
        ]
        if not rows:
            show("projects", "NONE MATCHED")
            return 1

        client = SyncManager.create_supabase_client()
        if client is None or not getattr(client, "_antigravity_authenticated", False):
            print("[application handshake]")
            show("client", "NOT AUTHENTICATED - sign in from the app first")
            print()
            return 1

        readings = {}
        for index, project in enumerate(rows, 1):
            project_id = project["project_id"]
            manager = build_manager(store, project, device_id)
            attached.append(manager)
            manager.supabase = client
            print(f"[application handshake project {index}]")
            show("project_id", project_id)
            show("gate_before", bool(before_projects[project_id]["contract_path_enabled"]))
            try:
                reading = manager.perform_contract_handshake(require_connection=True)
            except Exception as error:
                # Never the exception text: it can carry a URL or a token
                # fragment from the transport layer.
                code = getattr(error, "code", "") or type(error).__name__
                show("perform_contract_handshake", f"RAISED {code}")
                checks.require(f"handshake_completed[{index}]", False, code)
                print()
                continue
            show("perform_contract_handshake", "RETURNED")
            show("outcome", (reading or {}).get("outcome"))
            show("observed_project_sync_mode", (reading or {}).get("project_sync_mode"))
            show("observed_migration_epoch", (reading or {}).get("migration_epoch"))
            show("handshake_is_fresh", manager.contract_handshake_is_fresh())
            uses_contract = manager._uses_contract_structure()
            show("uses_contract_structure", uses_contract)
            readings[project_id] = (reading or {}, uses_contract, manager)
            checks.require(f"handshake_completed[{index}]", True)
            # The gate is the whole arrangement: a fresh, supported, compatible
            # reading is in hand and the write path is still shut.
            checks.require(
                f"contract_path_stayed_shut[{index}]", not uses_contract,
                "the contract path opened without the gate",
            )
            print()

        after_projects = project_snapshot(copy)
        after_queue = queue_snapshot(copy)

        print("[project fields, before -> after]")
        tracked = [column for column in CONTRACT_COLUMNS]
        for index, project in enumerate(rows, 1):
            project_id = project["project_id"]
            changed = diff_rows(
                before_projects[project_id], after_projects[project_id], tracked
            )
            show(f"project.{index}.id", project_id)
            if not changed:
                show(f"project.{index}.changed", "(nothing)")
            for column, was, now in changed:
                show(f"project.{index}.{column}", f"{was!r} -> {now!r}")
            row = after_projects[project_id]
            checks.require(
                f"gate_still_closed[{index}]",
                not row["contract_path_enabled"],
                "contract_path_enabled moved",
            )
            checks.require(
                f"gate_timestamp_unset[{index}]",
                not row["contract_path_enabled_at"],
                "contract_path_enabled_at was written",
            )
            checks.require(
                f"observed_mode_is_legacy_epoch_0[{index}]",
                (row["project_sync_mode"], int(row["migration_epoch"] or 0))
                == ("LEGACY", 0),
                f'{row["project_sync_mode"]}/{row["migration_epoch"]}',
            )
        print()

        print("[queue, before -> after handshake]")
        for name, _table, _where in QUEUE_COUNTERS:
            show(name, f"{before_queue[name]} -> {after_queue[name]}")
        print()
        for name in (
            "operations_total", "operations_protocol_3",
            "operations_with_batch_id", "operations_contract_batch",
            "contract_batches",
        ):
            checks.require(
                f"handshake_queued_nothing.{name}",
                before_queue[name] == after_queue[name],
                f"{before_queue[name]} -> {after_queue[name]}",
            )

        # A handshake that queues nothing proves only that it queued nothing.
        # Asking the queue for a write is what proves the stored server state
        # cannot open the contract path on its own.
        print("[gate probe: one document write per project]")
        probe_before = queue_snapshot(copy)
        for index, project in enumerate(rows, 1):
            context = {
                "local_key": project["local_key"],
                "project_id": project["project_id"],
                "writer_device_id": device_id,
            }
            probe_path = f"__contract_gate_probe_{index}__.txt"
            try:
                operation = store.enqueue(
                    context, probe_path, "preflight probe",
                    relative_path=probe_path,
                )
            except Exception as error:
                code = getattr(error, "code", "") or type(error).__name__
                show(f"probe.{index}", f"RAISED {code}")
                checks.require(f"probe_enqueued[{index}]", False, code)
                continue
            show(f"probe.{index}.provenance_kind", operation["provenance_kind"])
            show(f"probe.{index}.sync_protocol_version", operation["sync_protocol_version"])
            show(f"probe.{index}.batch_id", operation["batch_id"] or "(none)")
            show(f"probe.{index}.contract_version", operation["contract_version"] or "(none)")
            checks.require(f"probe_enqueued[{index}]", True)
            checks.require(
                f"probe_write_is_legacy[{index}]",
                operation["provenance_kind"] == "LEGACY_EPOCH_0"
                and int(operation["sync_protocol_version"] or 0) < 3
                and not operation["batch_id"]
                and not operation["contract_version"],
                "a contract-native write went out through a closed gate",
            )
        probe_after = queue_snapshot(copy)
        print()
        print("[queue, before -> after probe]")
        for name, _table, _where in QUEUE_COUNTERS:
            show(name, f"{probe_before[name]} -> {probe_after[name]}")
        print()
        checks.require(
            "probe_created_no_contract_batch",
            probe_before["contract_batches"] == probe_after["contract_batches"],
            f'{probe_before["contract_batches"]} -> {probe_after["contract_batches"]}',
        )
        checks.require(
            "probe_created_no_protocol_3_operation",
            probe_before["operations_protocol_3"] == probe_after["operations_protocol_3"],
            f'{probe_before["operations_protocol_3"]} -> '
            f'{probe_after["operations_protocol_3"]}',
        )

        supported = [
            project_id for project_id, (reading, _uses, _m) in readings.items()
            if reading.get("outcome") == "supported"
        ]
        print("[summary]")
        show("projects_checked", len(rows))
        show("projects_the_server_supports", len(supported))
        checks.report()
        if checks.failed:
            show("verdict", "STOP - " + ", ".join(name for name, _p, _d in checks.failed))
            return 1
        if not supported:
            show(
                "verdict",
                "wiring held; the server supports no checked project, so there "
                "is nothing to activate",
            )
            return 0
        show(
            "verdict",
            "wiring held: supported, recorded as LEGACY/0, gate still 0, "
            "writes still legacy",
        )
        return 0
    finally:
        # One manager serves the whole process. Leaving it holding a store on a
        # directory that is about to be deleted is how the next thing to ask it
        # for a project gets a database that is no longer there.
        for manager in attached:
            try:
                manager.release_v2()
                manager.supabase = None
                manager._v2_store = None
                manager._v2_device_id = None
            except Exception:
                pass
        shutil.rmtree(workspace, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, default=None,
        help="sync database to read; defaults to the live profile database",
    )
    parser.add_argument(
        "--rpc-handshake", action="store_true",
        help="read-only RPC preflight: call get_sync_handshake and validate "
             "the reply in memory, without using the client wiring",
    )
    parser.add_argument(
        "--application-handshake", action="store_true",
        help="end-to-end check: run the real perform_contract_handshake() "
             "against a throwaway copy and prove the gate held",
    )
    parser.add_argument(
        "--project", default="",
        help="limit the handshake modes to one project id",
    )
    args = parser.parse_args()

    database = (args.database or default_database()).resolve(strict=True)
    before = file_sha256(database)
    only_project = args.project.strip()

    print_client_pin()
    user_version, present, projects = read_projects(database)
    print("[database file]")
    show("path", database)
    show("sha256_before", before)
    print()
    print_projects(
        user_version, present, projects,
        gate_column="contract_path_enabled" in present,
    )

    status = 0
    if args.rpc_handshake:
        run_handshakes(projects, only_project)
    if args.application_handshake:
        status = run_application_handshake(database, only_project)

    after = file_sha256(database)
    print("[database file]")
    show("sha256_after", after)
    show("unchanged", after == before)
    if after != before:
        show("verdict", "STOP - the live database changed during a preflight")
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
