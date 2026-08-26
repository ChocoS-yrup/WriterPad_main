"""Preflight for the contract write path. Six modes, in widening scope.

  (no flag)                the client pin, plus the local gate and the server
                           state stored beside it, read out of the live
                           database with mode=ro. No network.

  --rpc-handshake          a read-only RPC preflight. Calls get_sync_handshake
                           once per project and validates the reply the way the
                           client validates it, entirely in memory. Proves what
                           the server answers. Proves nothing about the client
                           wiring, because it does not use it.

  --credential-lock-check  asks one question: can this process take the
                           credential lock for the profile it is running under.
                           Reports and exits. No client is built, no handshake
                           is attempted, and no request can leave, so it is the
                           mode to use when what is being tested is the locking
                           itself.

  --activate-contract-path opens the local gate for one named project, and
                           only after everything that would make that a mistake
                           has been ruled out. Reports and changes nothing
                           unless --apply is given as well. This is the one mode
                           that writes to the live database, and it writes one
                           column of one row.

  --application-handshake  the end-to-end check. Runs the real
                           SyncManager.perform_contract_handshake() against a
                           throwaway copy of the database, lets it record what
                           it normally records, and then proves the gate held:
                           still 0, still LEGACY at epoch 0, no contract batch
                           and no protocol 3 operation created, and a document
                           write that still comes out legacy. Exits non-zero if
                           any of that is false.

  --structure-write        the other half of that question, once a gate is
                           open. Makes one folder in one project whose gate is
                           open and follows it all the way out: builds the
                           contract batch through the real client path, checks
                           it field by field against the pin, then dispatches
                           it twice -- once with the network refused, because a
                           batch that cannot be sent has to wait rather than
                           tear anything up, and once for real, to find out
                           whether the server takes it. Without --apply it
                           works on a throwaway copy of the database and a
                           throwaway writing root, and sends nothing at all.

Nothing here opens the gate or promotes a project. Two modes write to the live
database, and only with --apply: --activate-contract-path, which writes one
column of one row, and --structure-write, which makes one folder in one
project and queues the batch that carries it. Every other mode works on a copy.
No project names, no document paths, no document content and no credentials
are printed; project ids, row metadata, and the folder this run was told to
make.

Read-only does not extend to the stored session. Both handshake modes build a
real client, which spends the saved refresh token and is issued a new one, and
that new pair is written to the credential store the same way the application
writes it. So before either of them touches the network this takes the same
lock the application takes before it exchanges a session, and holds it until
the run is over. Anything else already holding it -- the application, another
copy of this -- means stopping, not waiting.

The lock is per Windows account and per profile, because the stored session is:
security_manager.service_name() appends ANTIGRAVITY_PROFILE to the keyring
service, so a run under one profile can neither read nor retire another's
token. Both sides build the name from runtime_profile.credential_lease_name(),
and the digest printed under [credential lock] is that name -- two runs
contend only when the digest matches.

Verifying the lock offline, without touching the real login:

  1. Pick a profile that has no stored session, and use it on both sides:
     set ANTIGRAVITY_PROFILE=locktest for the application and for this tool.
  2. Start the application under that profile. It claims the lock at startup.
  3. Run this with --credential-lock-check under the same profile. It must
     report acquired=false and STOP, and its digest must equal the one the
     failed run prints.
  4. Close the application and run it again. It must report acquired=true.

Running the application under one profile and checking from another proves
nothing now: they are different locks by design, and both sides will report
the lock free. The profile has to match, and the printed digest is what says
whether it does.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import unicodedata
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace


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
    json_sha256,
    normalize_storage_name,
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


# Control, format, and line or paragraph separator. Everything that can end
# a line, start a control sequence, or reorder what is already on one.
_UNPRINTABLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


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
    print(f"{key}={_one_line(value)}")


def _one_line(value):
    """One field, one line, whatever the field came from.

    Some of what gets printed here arrives from the environment, and this output
    is kept as the record of what a run found. A value carrying a line break
    would not merely look wrong: it would add lines that read exactly like
    results, and somebody reading the record afterwards would see a verdict that
    was never reached.

    Escaping only the C0 controls is not enough. NEL, U+2028 and U+2029 all
    split a line under str.splitlines(), which is how any reader of this record
    will parse it, and the C1 range carries its own control sequences. Nor is a
    line break the whole risk: a bidi override reorders what is on the line, so
    a reader sees text that is not what was written.

    So the rule is by category rather than by list -- anything that is a
    control, a format character, or a line or paragraph separator becomes a
    visible escape. Everything legible survives, Korean paths included, because
    an unreadable record is its own kind of useless.

    A character this console cannot encode is escaped too. Some of what is
    printed here is named on the command line, and a name the output codepage
    has no room for would otherwise raise partway through a line and end the
    run holding a record that stops mid-sentence. An escape says what the
    character was; a half-written record says nothing at all.

    Only a stream that declares an encoding is held to one. A stream that does
    not -- anything capturing this in memory rather than writing it to a
    console -- has no codepage to exceed, and escaping against a guessed one
    would mangle the Korean paths this is meant to keep readable.
    """
    encoding = getattr(sys.stdout, "encoding", None)

    def unrenderable(character):
        if not encoding:
            return False
        try:
            character.encode(encoding)
        except (UnicodeError, LookupError):
            return True
        return False

    escaped = []
    for character in str(value):
        if (
            unicodedata.category(character) in _UNPRINTABLE_CATEGORIES
            or unrenderable(character)
        ):
            point = ord(character)
            escaped.append(
                "\\x%02x" % point if point < 0x100 else "\\u%04x" % point
            )
        else:
            escaped.append(character)
    return "".join(escaped)


def show_lease_scope():
    """Which credential lock this run would contend for.

    The profile is printed because it is what a verification run has to match,
    and the name because matching it is the proof that two runs are contending
    at all. Neither discloses anything: the name reaches print as a digest, so
    the account it is scoped to does not.
    """
    from runtime_profile import credential_lease_name, profile_name

    show("profile", profile_name() or "(default)")
    try:
        show("lock", credential_lease_name())
    except Exception:
        show("lock", "unavailable on this machine")


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


def build_manager(store, project, device_id, *, allow_client=True):
    """A manager attached to the copy, holding one project, and nothing else."""
    from sync_manager import SyncManager

    if allow_client:
        manager = SyncManager()
    else:
        # SyncManager.__init__ normally restores the stored session. A dry run
        # must not even build that client, so suppress initialization around
        # construction rather than creating a client and discarding it later.
        original_init_supabase = SyncManager.init_supabase
        SyncManager.init_supabase = lambda _manager: None
        try:
            manager = SyncManager()
        finally:
            SyncManager.init_supabase = original_init_supabase
        old_client = getattr(manager, "supabase", None)
        manager.supabase = None
        if old_client is not None:
            SyncManager._close_supabase_client(old_client)
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


def gate_snapshot(database: Path):
    """Every gate on the machine, so the ones not being opened can be shown shut."""
    _version, _present, rows = read_projects(database)
    return {
        row["project_id"]: (
            bool(row.get("contract_path_enabled")),
            row.get("contract_path_enabled_at") or "",
        )
        for row in rows
    }


def project_work_counts(database: Path, local_key: str):
    """What is still owed on one project. Opening a gate over unfinished work
    would change the shape of writes that were queued under the old one."""
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        def count(table, where, parameters):
            if table not in tables:
                return 0
            return int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where}", parameters
            ).fetchone()[0])

        return {
            "queued_operations": count(
                "sync_operations", "local_key = ?", (local_key,)
            ),
            "operations_with_errors": count(
                "sync_operations", "local_key = ? AND last_error != ''", (local_key,)
            ),
            "documents_in_conflict": count(
                "sync_documents",
                "local_key = ? AND (conflict_local IS NOT NULL"
                " OR conflict_remote IS NOT NULL)",
                (local_key,),
            ),
            "contract_batches": count(
                "sync_contract_batches", "local_key = ?", (local_key,)
            ),
        }
    finally:
        connection.close()


def run_activation(live: Path, project_id: str, confirm_id: str, apply_change: bool):
    """Open one project's gate, after ruling out everything that would make it
    a mistake. Without --apply nothing is written and the checks still run."""
    from sync_manager import SyncManager
    from sync_v2_store import SyncV2Store
    from sync_contract import (
        CANONICAL_CONTRACT_SHA256, SERVER_CAPABILITIES, SYNC_PROTOCOL_VERSION,
    )

    checks = Checks()
    print("[activation]")
    show("mode", "apply" if apply_change else "dry run - nothing will be written")
    show("target_project_id", project_id)
    # Typing it twice is the whole point: fifteen other projects are one
    # character away, and the gate is not something to open on a typo.
    checks.require(
        "the project id was given twice and matched",
        bool(project_id) and project_id == confirm_id,
        "--project-id and --confirm-project-id differ",
    )
    if checks.failed:
        checks.report()
        show("verdict", "STOP - the two project ids do not match")
        return 1

    digest_before = file_sha256(live)
    gates_before = gate_snapshot(live)
    _version, _present, rows_before = read_projects(live)
    row_before = next(
        (row for row in rows_before if row["project_id"] == project_id), {}
    )
    checks.require(
        "the project is one row in this database",
        list(gates_before).count(project_id) == 1,
        f"{list(gates_before).count(project_id)} rows match",
    )
    if project_id not in gates_before:
        checks.report()
        show("verdict", "STOP - no such project here")
        return 1
    show("other_projects", len(gates_before) - 1)
    checks.require(
        "its gate is shut to begin with",
        gates_before[project_id][0] is False,
        "it is already open",
    )

    store = SyncV2Store(str(live))
    project = store.get_project_by_id(project_id)
    local_key = project["local_key"]
    work = project_work_counts(live, local_key)
    for name, value in work.items():
        show(name, value)
    checks.require(
        "nothing is still owed on it",
        not any(work.values()),
        "queued work, errors, conflicts or batches are present",
    )

    device_id = project.get("writer_device_id") or str(uuid.uuid4())
    manager = SyncManager()
    manager.release_v2()
    manager._v2_store = store
    manager._v2_context = {
        "local_key": local_key,
        "project_id": project_id,
        "writer_device_id": device_id,
    }
    manager._v2_device_id = device_id
    manager._forget_contract_handshake()

    client = SyncManager.create_supabase_client()
    if client is None or not getattr(client, "_antigravity_authenticated", False):
        show(
            "client",
            "RESTORE FAILED - the stored session was kept"
            if client is not None else "NO CLIENT",
        )
        show(
            "restore_error_kind",
            getattr(client, "_antigravity_restore_error_kind", "") or "(none)",
        )
        checks.require("a signed-in client is available", False, "not authenticated")
        checks.report()
        show("verdict", "STOP - sign in from the application first")
        return 1
    manager.supabase = client
    checks.require("a signed-in client is available", True)
    print()

    try:
        print("[fresh handshake]")
        try:
            reading = manager.perform_contract_handshake(require_connection=True)
        except Exception as error:
            code = getattr(error, "code", "") or type(error).__name__
            show("perform_contract_handshake", f"RAISED {code}")
            checks.require("the handshake completed", False, code)
            reading = None
        if reading is not None:
            show("outcome", reading.get("outcome"))
            show("observed_project_sync_mode", reading.get("project_sync_mode"))
            show("observed_migration_epoch", reading.get("migration_epoch"))
            show("handshake_is_fresh", manager.contract_handshake_is_fresh())
            checks.require("the handshake completed", True)
            checks.require(
                "the server supports this client",
                reading.get("outcome") == "supported",
                str(reading.get("outcome")),
            )
            checks.require(
                "it answered LEGACY at epoch 0",
                (reading.get("project_sync_mode"), reading.get("migration_epoch"))
                == ("LEGACY", 0),
                f'{reading.get("project_sync_mode")}/{reading.get("migration_epoch")}',
            )

        stored = store.get_project_by_id(project_id)
        show("recorded_server_protocol_version", stored["server_protocol_version"])
        show("recorded_contract_sha256", stored["active_contract_sha256"])
        capabilities = json.loads(stored["server_capabilities_json"] or "[]")
        show("recorded_capabilities", len(capabilities))
        checks.require(
            "the digest is the one this client is pinned to",
            stored["active_contract_sha256"] == CANONICAL_CONTRACT_SHA256,
            "digest differs from the pin",
        )
        checks.require(
            "the protocol is one this client speaks",
            int(stored["server_protocol_version"] or 0) >= SYNC_PROTOCOL_VERSION,
            f'protocol {stored["server_protocol_version"]}',
        )
        missing = sorted(set(SERVER_CAPABILITIES) - set(capabilities))
        checks.require(
            "every capability this client needs is offered",
            not missing,
            f"missing {len(missing)}",
        )
        print()

        if checks.failed:
            checks.report()
            show(
                "verdict",
                "STOP - " + ", ".join(name for name, _p, _d in checks.failed),
            )
            return 1

        if not apply_change:
            checks.report()
            show("gate_after", gate_snapshot(live)[project_id][0])
            show(
                "verdict",
                "every check passed; re-run with --apply to open the gate",
            )
            return 0

        gates_mid = gate_snapshot(live)
        work_mid = project_work_counts(live, local_key)
        store.set_contract_path_enabled(local_key, True)
        gates_after = gate_snapshot(live)
        work_after = project_work_counts(live, local_key)

        print("[what changed]")
        moved = [
            other for other in gates_after
            if gates_after[other] != gates_mid.get(other)
        ]
        show("gate_rows_changed", len(moved))
        show("gate_after", gates_after[project_id][0])
        show("gate_opened_at", gates_after[project_id][1])
        checks.require(
            "exactly one gate moved", moved == [project_id],
            f"{len(moved)} moved",
        )
        checks.require(
            "the other gates are where they were",
            all(
                gates_after[other] == gates_before[other]
                for other in gates_after if other != project_id
            ),
            "another project's gate moved",
        )
        checks.require(
            "opening it queued nothing",
            work_after == work_mid,
            "the queue or the batch table moved",
        )
        stored_after = store.get_project_by_id(project_id)
        checks.require(
            "mode and epoch are untouched",
            (stored_after["project_sync_mode"],
             int(stored_after["migration_epoch"] or 0)) == ("LEGACY", 0),
            f'{stored_after["project_sync_mode"]}/{stored_after["migration_epoch"]}',
        )
        print()
        checks.report()
        if checks.failed:
            show(
                "verdict",
                "STOP - " + ", ".join(name for name, _p, _d in checks.failed),
            )
            return 1
        show("verdict", "the gate is open for this project and no other")
        return 0
    finally:
        try:
            manager.release_v2()
            manager.supabase = None
            manager._v2_store = None
            manager._v2_device_id = None
        except Exception:
            pass
        print()
        # A handshake records what the server said, so this file is expected to
        # change even on a dry run. Reporting a digest that moved and stopping
        # there would say nothing about whether it moved for a good reason, so
        # what moved is named instead.
        print("[target row, before -> after]")
        _v, _p, rows_after = read_projects(live)
        row_after = next(
            (row for row in rows_after if row["project_id"] == project_id), {}
        )
        moved_fields = [
            column for column in CONTRACT_COLUMNS
            if row_before.get(column) != row_after.get(column)
        ]
        if not moved_fields:
            show("changed", "(nothing)")
        for column in moved_fields:
            show(column, f"{row_before.get(column)!r} -> {row_after.get(column)!r}")
        untouched_gates = [
            other for other in gates_before
            if other != project_id
            and gate_snapshot(live).get(other) != gates_before[other]
        ]
        show("other_project_gates_moved", len(untouched_gates))
        print()
        print("[database file]")
        show("sha256_before", digest_before)
        show("sha256_after", file_sha256(live))
        show(
            "note",
            "a handshake records the server's answer, so this file moves even "
            "on a dry run; the fields above say what moved",
        )


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

        stored_session = False
        try:
            from security_manager import SecurityManager
            access, refresh = SecurityManager.get_supabase_session()
            stored_session = bool(access and refresh)
        except Exception:
            stored_session = False

        client = SyncManager.create_supabase_client()
        if client is None or not getattr(client, "_antigravity_authenticated", False):
            print("[application handshake]")
            show("stored_session_present", stored_session)
            # Two different situations wear the same "not authenticated" face:
            # nobody has signed in, or a signed-in session could not be restored
            # this time. Only the second one has a reason worth reading.
            show(
                "restore_error_kind",
                getattr(client, "_antigravity_restore_error_kind", "") or "(none)",
            )
            show(
                "client",
                "RESTORE FAILED - the stored session was kept"
                if stored_session else
                "NO STORED SESSION - sign in from the app first",
            )
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


def writing_root_fingerprint(root: Path):
    """The manuscript as it stands on disk: every folder, every .txt byte.

    A dispatch that fails has to leave this exactly as it found it, and a
    successful one has to change it by exactly the folder that was made and
    nothing else. Counting files would not show one that was rewritten in
    place, so each is hashed rather than tallied.
    """
    folders = []
    documents = {}
    unreadable = []
    if not root.is_dir():
        return {
            "folders": folders,
            "documents": documents,
            "unreadable": unreadable,
        }
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root)).replace("\\", "/")
        if path.is_dir():
            folders.append(relative)
        elif path.suffix == ".txt":
            try:
                documents[relative] = file_sha256(path)
            except OSError:
                # A fixed placeholder would compare equal to itself and let a
                # changed manuscript pass as untouched. Keep the failure
                # explicit so every caller can fail closed instead.
                unreadable.append(relative)
    return {
        "folders": folders,
        "documents": documents,
        "unreadable": unreadable,
    }


def manuscript_bytes_unchanged(before, after):
    """True only when every manuscript was readable and every byte matches."""
    return (
        not before.get("unreadable")
        and not after.get("unreadable")
        and before.get("documents") == after.get("documents")
    )


def manuscript_evidence_available(snapshot):
    """Whether a snapshot can prove something about real manuscript bytes."""
    return bool(snapshot.get("documents")) and not snapshot.get("unreadable")


def structure_target_directory(root: Path, parent_path: str, folder_name: str):
    """Resolve one safe child directory and prove it stays under ``root``.

    The name is operator input and reaches mkdir before the application sees
    the logical path. It therefore has to satisfy the contract name rule and
    the host path rule here, before any filesystem call. Resolving both sides
    also catches an existing junction beneath the writing root. A different
    drive or UNC share raises ValueError from relative_to and is simply outside.
    """
    try:
        normalize_storage_name(folder_name)
    except SyncContractError as error:
        raise ValueError(getattr(error, "code", "STORAGE_NAME_INVALID")) from error

    candidate = Path(folder_name)
    if (
        not folder_name
        or candidate.anchor
        or candidate.drive
        or candidate.parts != (folder_name,)
        or "/" in folder_name
        or "\\" in folder_name
    ):
        raise ValueError("FOLDER_NAME_NOT_ONE_SEGMENT")

    logical_parent = str(parent_path or "").replace("\\", "/").strip("/")
    parent_parts = logical_parent.split("/") if logical_parent else []
    if (
        not parent_parts
        or "\\" in str(parent_path or "")
        or str(parent_path or "") != logical_parent
        or any(part in {"", ".", ".."} for part in parent_parts)
    ):
        raise ValueError("PARENT_PATH_INVALID")

    root_resolved = root.resolve(strict=False)
    target_resolved = root.joinpath(*parent_parts, folder_name).resolve(
        strict=False
    )
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as error:
        raise ValueError("TARGET_OUTSIDE_WRITING_ROOT") from error
    return target_resolved


def arm_manager_from_stored_contract(manager, project):
    """Arm a throwaway manager without a client, credential, or RPC."""
    require_server_compatibility(
        project_sync_mode=project["project_sync_mode"],
        migration_epoch=int(project["migration_epoch"] or 0),
        server_protocol_version=int(project["server_protocol_version"] or 0),
        server_contract_sha256=project["active_contract_sha256"] or "",
        server_capabilities=json.loads(project["server_capabilities_json"] or "[]"),
    )
    # This manager exists only inside an offline dry run. Give it a token-shaped
    # holder with a plainly synthetic subject instead of relying on the
    # runtime's old empty-marker equality. It has no auth or RPC methods and no
    # real credential, so it cannot send or authorize anything.
    payload = base64.urlsafe_b64encode(json.dumps(
        {"sub": "offline-stored-contract-dry-run"},
        separators=(",", ":"),
    ).encode("utf-8")).decode("ascii").rstrip("=")
    manager.supabase = SimpleNamespace(
        _antigravity_access_token=f"header.{payload}.signature"
    )
    offline_identity = manager._contract_identity()
    reading = {
        "generation": manager._v2_context_generation,
        "project_id": manager._v2_context["project_id"],
        "identity": offline_identity,
        "contract_sha256": CANONICAL_CONTRACT_SHA256,
        "observed_at": project.get("contract_validated_at") or "",
        "outcome": "supported",
        "project_sync_mode": project["project_sync_mode"],
        "migration_epoch": int(project["migration_epoch"] or 0),
    }
    manager._contract_handshake_error = ""
    manager._contract_handshake = reading
    return reading


def project_structure_snapshot(database: Path, local_key: str):
    """Every row on one project that a contract batch can move."""
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        def rows(table, where):
            if table not in tables:
                return []
            return [
                dict(row) for row in connection.execute(
                    f"SELECT * FROM {table} WHERE {where}", (local_key,)
                )
            ]

        return {
            "folders": rows("sync_folders", "local_key = ?"),
            "documents": rows("sync_documents", "local_key = ?"),
            "tree_orders": rows("sync_tree_orders", "local_key = ?"),
            "operations": rows("sync_operations", "local_key = ?"),
            "batches": rows("sync_contract_batches", "local_key = ?"),
            "structure_operations": rows(
                "sync_structure_operations", "local_key = ?"
            ),
            "results": rows(
                "sync_contract_batch_results",
                "batch_id IN (SELECT batch_id FROM sync_contract_batches "
                "WHERE local_key = ?)",
            ),
        }
    finally:
        connection.close()


def unanswered_contract_batches(database: Path, local_key: str):
    """Batches this project has queued and never had an answer for."""
    snapshot = project_structure_snapshot(database, local_key)
    answered = {row["batch_id"] for row in snapshot["results"]}
    return sum(
        1 for row in snapshot["batches"] if row["batch_id"] not in answered
    )


def unanswered_batch_for_folder(database: Path, local_key: str, folder_id: str):
    """The batch queued to create one folder that was never answered.

    A batch the server never answered is not litter to clear away. The
    application resends it under its own id when it next runs, because a
    second id for the same work is the one thing the contract forbids. So a
    run that finds one resumes it rather than building another.
    """
    snapshot = project_structure_snapshot(database, local_key)
    answered = {row["batch_id"] for row in snapshot["results"]}
    for row in snapshot["batches"]:
        if row["batch_id"] in answered:
            continue
        request = json.loads(row["request_json"])
        if any(
            intent["entity_kind"] == "folder"
            and intent["intent_kind"] == "create"
            and intent["entity_id"] == folder_id
            for intent in request.get("ordered_intents") or []
        ):
            return row
    return None


def folder_revision(store, local_key, path):
    """One folder's revision, or None when no folder stands at that path.

    Separated out because revision zero is the interesting value here and it is
    falsy: an ``or`` default reads a folder the server has never seen as no
    folder at all.
    """
    folder = store.get_folder_by_path(local_key, path)
    return None if folder is None else int(folder["revision"] or 0)


def binder_children(store, local_key, parent_path, extra=""):
    """One parent's children by name, in the order the binder already holds.

    The application sends a parent's whole child list, not just the part that
    changed, so a probe that named only its own folder would reorder the parent
    down to that one child and drop every sibling on the way out. Children the
    order does not mention yet go last, which is where a newly made one goes.
    """
    prefix = f"{parent_path}/"

    def direct_children(rows, key):
        found = {}
        for row in rows:
            path = row["local_path"]
            if (
                row["is_deleted"]
                or not path.startswith(prefix)
                or "/" in path[len(prefix):]
            ):
                continue
            found[row[key]] = path[len(prefix):]
        return found

    names = direct_children(store.list_folders(local_key), "folder_id")
    names.update(
        direct_children(store.list_documents(local_key), "document_id")
    )
    order = store.get_tree_order(local_key, parent_path) or {}
    children = [
        names.pop(child) for child in order.get("children", []) if child in names
    ]
    children.extend(names[key] for key in sorted(names))
    if extra and extra not in children:
        children.append(extra)
    return children


def batch_operation_states(store, snapshot, batch_id):
    """The derived state of every operation in one batch, deduplicated."""
    return sorted({
        store.operation(row["operation_id"])["status"]
        for row in snapshot["structure_operations"]
        if row["batch_id"] == batch_id
    })


def run_structure_write(
    live: Path,
    project_id: str,
    confirm_id: str,
    writing_root: str,
    parent_path: str,
    folder_name: str,
    apply_change: bool,
):
    """Make one folder on the contract path and report what the server did.

    Without --apply nothing leaves this machine and nothing on it is written:
    the batch is built against a throwaway copy of the database and a throwaway
    writing root, checked field by field, and destroyed along with them.

    With --apply the same folder is made for real, in the live database and in
    the project's own writing root, and then dispatched twice. The first
    dispatch is made to fail, because a batch that cannot be sent has to wait
    instead of tearing anything up, and that is only worth believing if it has
    been watched happen on real rows. The second is the real one.
    """
    from sync_manager import SyncManager, load_or_create_device_id
    from sync_v2_store import SyncV2Store

    checks = Checks()
    workspace = Path(tempfile.mkdtemp(prefix="contract-structure-"))
    managers = []

    def stop(reason):
        checks.report()
        show("verdict", f"STOP - {reason}")
        return 1

    def stop_on_failed_checks():
        return stop(", ".join(name for name, _p, _d in checks.failed))

    try:
        print("[structure write]")
        show(
            "mode",
            "apply - the live database and the project's own writing root"
            if apply_change else
            "dry run - a throwaway copy of both; nothing is sent",
        )
        show("target_project_id", project_id)
        show("parent_path", parent_path)
        show("folder_name", folder_name)
        # The same two-hands rule activation uses. Fifteen other projects are
        # one character away, and this one queues a write.
        checks.require(
            "the project id was given twice and matched",
            bool(project_id) and project_id == confirm_id,
            "--project-id and --confirm-project-id differ",
        )
        name_error = ""
        try:
            # The workspace is only a harmless root for validating the logical
            # path before the live project or its database is even opened.
            structure_target_directory(workspace, parent_path, folder_name)
        except (OSError, ValueError) as error:
            name_error = str(error) or type(error).__name__
        checks.require(
            "the parent and folder name form one safe path below the root",
            not name_error,
            name_error or "invalid path",
        )
        if checks.failed:
            return stop_on_failed_checks()
        target_path = f"{parent_path}/{folder_name}"

        connection = sqlite3.connect(live.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            live_row = connection.execute(
                "SELECT local_key FROM sync_projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        finally:
            connection.close()
        if live_row is None:
            return stop("no such project here")
        local_key = live_row["local_key"]

        if apply_change:
            database = live
            checks.require(
                "a writing root was named",
                bool(str(writing_root or "").strip()),
                "--writing-root is required with --apply",
            )
            root = Path(writing_root) if writing_root else workspace / "writing"
            if str(writing_root or "").strip():
                # The root and the row have to be the same project. Making the
                # folder under one project's root while queueing the batch on
                # another's is the one mistake this mode could make silently.
                try:
                    named_key = SyncV2Store.local_key_for(str(root))
                except Exception:
                    named_key = ""
                checks.require(
                    "the writing root is this project's own",
                    named_key == local_key,
                    "--writing-root belongs to a different project",
                )
        else:
            database = workspace / "sync_v2.sqlite3"
            copy_database(live, database)
            root = workspace / "writing"
            root.mkdir(parents=True, exist_ok=True)
        if checks.failed:
            return stop_on_failed_checks()

        try:
            target_directory = structure_target_directory(
                root, parent_path, folder_name
            )
        except (OSError, ValueError) as error:
            checks.require(
                "the resolved target stays inside the writing root",
                False,
                str(error) or type(error).__name__,
            )
            return stop_on_failed_checks()
        checks.require(
            "the resolved target stays inside the writing root", True
        )

        disk_probe = writing_root_fingerprint(root)
        show("manuscript_documents", len(disk_probe["documents"]))
        show("unreadable_manuscripts", len(disk_probe["unreadable"]))
        checks.require(
            "every manuscript can be hashed before any write",
            not disk_probe["unreadable"],
            f'{len(disk_probe["unreadable"])} unreadable manuscript(s)',
        )
        if apply_change:
            checks.require(
                "a manuscript exists for the no-damage check",
                manuscript_evidence_available(disk_probe),
                "the project has no readable .txt manuscript to compare",
            )
        if checks.failed:
            return stop_on_failed_checks()
        show(
            "database",
            "the live file" if apply_change else "a throwaway snapshot",
        )
        show(
            "writing_root",
            "the project's own" if apply_change else "a throwaway directory",
        )
        print()

        store = SyncV2Store(str(database))
        project = store.get_project_by_id(project_id)
        print("[before]")
        checks.require(
            "its gate is open",
            bool(project["contract_path_enabled"]),
            "the gate is shut, so there is nothing here to test",
        )
        work = project_work_counts(database, local_key)
        for name, value in work.items():
            show(name, value)
        unanswered = unanswered_contract_batches(database, local_key)
        show("contract_batches_without_an_answer", unanswered)
        checks.require(
            "nothing else is owed on it",
            not work["queued_operations"]
            and not work["operations_with_errors"]
            and not work["documents_in_conflict"],
            "queued work, errors or conflicts are present",
        )

        parent = store.get_folder_by_path(local_key, parent_path)
        show("parent_revision", (parent or {}).get("revision"))
        checks.require(
            "the parent folder is one the server has proven",
            bool(parent)
            and not parent["is_deleted"]
            and int(parent["revision"] or 0) >= 1,
            "the parent has no server-proven revision to hang a child from",
        )

        standing = store.get_folder_by_path(local_key, target_path)
        resume = (
            unanswered_batch_for_folder(
                database, local_key, standing["folder_id"]
            ) if standing else None
        )
        show("resuming_an_unanswered_batch", bool(resume))
        if resume is not None:
            # What the application does when it starts with a batch that was
            # queued and never answered: send it again under its own id rather
            # than build a second one for the same work.
            checks.require(
                "the folder it was queued for is still locally unproven",
                folder_revision(store, local_key, target_path) == 0,
                f'revision {standing["revision"]}',
            )
            checks.require(
                "the directory it names is still on disk",
                target_directory.is_dir(),
                "it is gone",
            )
            checks.require(
                "it is the only batch still waiting",
                unanswered == 1,
                f"{unanswered} waiting",
            )
        else:
            # Unlike activation this does not demand a zero batch count: a
            # project that has been through this keeps its answered batch rows.
            # What it demands is that none is still waiting, because a second
            # batch would then be built on a revision the server has not
            # agreed to yet.
            checks.require(
                "no earlier batch is still unanswered",
                unanswered == 0,
                f"{unanswered} waiting",
            )
            checks.require(
                "no folder stands at that path yet",
                standing is None,
                "a folder row is already there",
            )
            checks.require(
                "nothing stands at that path on disk",
                not target_directory.exists(),
                "the directory is already there",
            )

        # The device id the application itself writes under. A throwaway one
        # belongs in a real batch. A dry run deliberately uses an ephemeral id
        # so it cannot create a device-id file while rehearsing on copies.
        device_id = (
            load_or_create_device_id() if apply_change else str(uuid.uuid4())
        )
        manager = build_manager(
            store, project, device_id, allow_client=apply_change
        )
        manager._v2_wpm = SimpleNamespace(
            writing_root_path=str(root),
            read_text_file=lambda _path: None,
            project_settings={},
            save_settings=lambda: True,
        )
        managers.append(manager)

        if apply_change:
            client = manager.supabase or SyncManager.create_supabase_client()
            if client is None or not getattr(
                client, "_antigravity_authenticated", False
            ):
                show(
                    "client",
                    "RESTORE FAILED - the stored session was kept"
                    if client is not None else "NO CLIENT",
                )
                show(
                    "restore_error_kind",
                    getattr(client, "_antigravity_restore_error_kind", "")
                    or "(none)",
                )
                checks.require(
                    "a signed-in client is available", False,
                    "not authenticated",
                )
            else:
                checks.require("a signed-in client is available", True)
            if checks.failed:
                return stop_on_failed_checks()
            manager.supabase = client
            print()

            print("[fresh handshake]")
            try:
                reading = manager.perform_contract_handshake(
                    require_connection=True
                )
            except Exception as error:
                code = getattr(error, "code", "") or type(error).__name__
                show("perform_contract_handshake", f"RAISED {code}")
                checks.require("the handshake completed", False, code)
                reading = None
        else:
            print("[stored contract state; no client and no request]")
            try:
                reading = arm_manager_from_stored_contract(manager, project)
            except Exception as error:
                code = getattr(error, "code", "") or type(error).__name__
                show("stored_contract_state", f"REJECTED {code}")
                checks.require("the stored contract state is usable", False, code)
                reading = None
        if reading is not None:
            show("outcome", reading.get("outcome"))
            show("observed_project_sync_mode", reading.get("project_sync_mode"))
            show("observed_migration_epoch", reading.get("migration_epoch"))
            checks.require(
                "the handshake completed" if apply_change
                else "the stored contract state is usable",
                True,
            )
            checks.require(
                "the server supports this client" if apply_change
                else "the stored server state supports this client",
                reading.get("outcome") == "supported",
                str(reading.get("outcome")),
            )
            checks.require(
                "it answered LEGACY at epoch 0" if apply_change
                else "the stored server state is LEGACY at epoch 0",
                (reading.get("project_sync_mode"), reading.get("migration_epoch"))
                == ("LEGACY", 0),
                f'{reading.get("project_sync_mode")}'
                f'/{reading.get("migration_epoch")}',
            )
        uses_contract = manager._uses_contract_structure()
        show("uses_contract_structure", uses_contract)
        # The mirror image of what --application-handshake proves. There the
        # gate is shut and this has to be false. Here it is open and this has
        # to be true, or the folder would go out legacy and the run would say
        # nothing at all about the contract path.
        checks.require(
            "the contract path is armed for this project",
            uses_contract,
            "structure work would still go out legacy",
        )
        if checks.failed:
            return stop_on_failed_checks()
        print()

        before_rows = project_structure_snapshot(database, local_key)
        before_disk = writing_root_fingerprint(root)

        if resume is None:
            print("[making the folder]")
            target_directory.mkdir(parents=True)
            operations = manager.record_path_change(
                target_path, target_path, retry=False
            )
            intents = [
                intent
                for operation in operations or []
                if isinstance(operation, dict)
                for intent in (operation.get("contract_structure_intents") or [])
            ]
            show("path_operations", len(operations or []))
            show("planned_intents", len(intents))
            checks.require(
                "the folder was planned as contract intents",
                bool(intents),
                "record_path_change came back with legacy operations",
            )
            if not intents:
                return stop("no contract intent was planned")

            children = binder_children(
                store, local_key, parent_path, extra=folder_name
            )
            show("parent_children", len(children))
            request = manager.queue_contract_path_change_with_order(
                operations, {parent_path: children}, retry=False
            )
            checks.require(
                "queueing it produced a contract request",
                isinstance(request, dict)
                and request.get("kind") == "atomic_structure_commit_request",
                str((request or {}).get("kind")),
            )
            if not isinstance(request, dict):
                return stop("no contract batch was queued")

            queued_rows = project_structure_snapshot(database, local_key)
            known = {row["batch_id"] for row in before_rows["batches"]}
            fresh = [
                row for row in queued_rows["batches"]
                if row["batch_id"] not in known
            ]
            checks.require(
                "exactly one contract batch appeared",
                len(fresh) == 1,
                f"{len(fresh)} appeared",
            )
            checks.require(
                "no legacy operation was queued alongside it",
                len(queued_rows["operations"])
                == len(before_rows["operations"]),
                f'{len(before_rows["operations"])} -> '
                f'{len(queued_rows["operations"])}',
            )
            if len(fresh) != 1:
                return stop_on_failed_checks()
            batch = fresh[0]
        else:
            print("[the batch that was already waiting]")
            batch = resume
            request = json.loads(batch["request_json"])
            show("planned_intents", len(request.get("ordered_intents") or []))
            checks.require(
                "the waiting batch is a contract request",
                request.get("kind") == "atomic_structure_commit_request",
                str(request.get("kind")),
            )
        batch_id = batch["batch_id"]
        queued_rows = project_structure_snapshot(database, local_key)
        print()

        print("[the batch]")
        show("batch_id", batch_id)
        show("sync_protocol_version", batch["sync_protocol_version"])
        show("contract_version", batch["contract_version"])
        show("canonical_contract_sha256", batch["canonical_contract_sha256"])
        show("client_build_id", batch["client_build_id"])
        show("project_sync_mode", batch["project_sync_mode"])
        show("migration_epoch", batch["migration_epoch"])
        show("batch_payload_sha256", batch["batch_payload_sha256"])
        show("request_sha256", batch["request_sha256"])
        show("intents", " ".join(
            f'{intent["sequence"]}:{intent["entity_kind"]}'
            f'/{intent["intent_kind"]}@{intent["base_revision"]}'
            for intent in request["ordered_intents"]
        ))
        checks.require(
            "it goes out at protocol 3",
            int(batch["sync_protocol_version"] or 0) == SYNC_PROTOCOL_VERSION,
            f'protocol {batch["sync_protocol_version"]}',
        )
        checks.require(
            "it names contract version 0.2.0",
            batch["contract_version"] == CONTRACT_VERSION,
            str(batch["contract_version"]),
        )
        checks.require(
            "it carries the canonical digest this client is pinned to",
            batch["canonical_contract_sha256"] == CANONICAL_CONTRACT_SHA256,
            "the digest differs from the pin",
        )
        # Recomputed rather than compared to itself: a digest that is only ever
        # read back from the row it was written to proves nothing about what
        # the row actually holds.
        checks.require(
            "the batch digest is over the intents that are in it",
            batch["batch_payload_sha256"]
            == json_sha256(request["ordered_intents"]),
            "recomputing it gives something else",
        )
        checks.require(
            "the stored request digest is over the stored request",
            batch["request_sha256"] == json_sha256(request),
            "recomputing it gives something else",
        )
        checks.require(
            "the batch still names LEGACY at epoch 0",
            (batch["project_sync_mode"], int(batch["migration_epoch"] or 0))
            == ("LEGACY", 0),
            f'{batch["project_sync_mode"]}/{batch["migration_epoch"]}',
        )
        checks.require(
            "it is one folder create and one reorder",
            [
                f'{intent["entity_kind"]}/{intent["intent_kind"]}'
                for intent in request["ordered_intents"]
            ] == ["folder/create", "tree_order/reorder"],
            "the batch holds something else",
        )
        checks.require(
            "every operation in it is a contract operation",
            all(
                row["provenance_kind"] == "CONTRACT_BATCH"
                for row in queued_rows["structure_operations"]
                if row["batch_id"] == batch_id
            ),
            "a legacy operation is sitting in a contract batch",
        )

        if not apply_change:
            print()
            if checks.failed:
                return stop_on_failed_checks()
            checks.report()
            show(
                "verdict",
                "the batch is well formed and nothing was sent; re-run with "
                "--apply against the real project to ask the server",
            )
            return 0
        if checks.failed:
            print()
            return stop_on_failed_checks()
        print()

        print("[dispatch 1: the network refused]")
        # Forced offline is refused inside the dispatch, before a request is
        # built, so this is the shallowest failure there is. That is the point:
        # every dispatch failure reaches the same handler, and what is under
        # test is what that handler leaves behind.
        marker = workspace / "offline"
        marker.write_text("", encoding="utf-8")
        previous = os.environ.get("ANTIGRAVITY_SYNC_OFFLINE_FILE")
        refused = ""
        try:
            os.environ["ANTIGRAVITY_SYNC_OFFLINE_FILE"] = str(marker)
            store.mark_structure_batch_attempt(batch_id)
            try:
                manager._process_contract_structure_batch(batch_id)
            except Exception as error:
                refused = getattr(error, "code", "") or type(error).__name__
            if refused:
                # Exactly what the worker's result handler does with a dispatch
                # that threw.
                store.mark_structure_batch_retry(batch_id, refused)
        finally:
            if previous is None:
                os.environ.pop("ANTIGRAVITY_SYNC_OFFLINE_FILE", None)
            else:
                os.environ["ANTIGRAVITY_SYNC_OFFLINE_FILE"] = previous
            marker.unlink(missing_ok=True)
        show("dispatch_raised", refused or "(nothing)")
        checks.require(
            "a dispatch that cannot go through fails instead of pretending",
            bool(refused),
            "it came back as though it had been sent",
        )
        refused_rows = project_structure_snapshot(database, local_key)
        refused_disk = writing_root_fingerprint(root)
        refused_states = batch_operation_states(store, refused_rows, batch_id)
        show("operation_states", " ".join(refused_states) or "(none)")
        show("results_recorded", len(refused_rows["results"]))
        show("unreadable_manuscripts", len(refused_disk["unreadable"]))
        checks.require(
            "the batch is waiting, not lost and not finished",
            refused_states == ["retry_wait"],
            " ".join(refused_states) or "(none)",
        )
        checks.require(
            "nothing was written down as an answer",
            len(refused_rows["results"]) == len(before_rows["results"]),
            f'{len(before_rows["results"])} -> {len(refused_rows["results"])}',
        )
        checks.require(
            "the folder is still locally unproven",
            folder_revision(store, local_key, target_path) == 0,
            "its revision moved without a server answer",
        )
        checks.require(
            "no document row moved",
            refused_rows["documents"] == before_rows["documents"],
            "a document row changed under a refused dispatch",
        )
        checks.require(
            "the manuscript on disk is untouched",
            manuscript_bytes_unchanged(before_disk, refused_disk),
            "a manuscript changed or could not be read under a refused dispatch",
        )
        if checks.failed:
            print()
            return stop_on_failed_checks()
        print()

        print("[dispatch 2: the real one]")
        result = None
        try:
            store.mark_structure_batch_attempt(batch_id)
            result = manager._process_contract_structure_batch(batch_id)
        except Exception as error:
            code = getattr(error, "code", "") or type(error).__name__
            try:
                store.mark_structure_batch_retry(batch_id, code)
            except Exception:
                pass
            # Never the exception text: a transport error can carry a URL or a
            # token fragment.
            show("dispatch", f"RAISED {code}")
            checks.require("the server answered", False, code)
        if result is not None:
            show("dispatch", "ANSWERED")
            show("kind", result.get("kind"))
            show("status", result.get("status"))
            show("applied", result.get("applied") is True)
            error = result.get("error") or {}
            if error:
                show("error_code", error.get("code"))
                show("failed_sequence", error.get("failed_sequence"))
            for item in result.get("results") or []:
                show(
                    f'result.{item["sequence"]}.result_revision',
                    item["result_revision"],
                )
            checks.require("the server answered", True)
            checks.require(
                "the server took the batch",
                result.get("kind") == "atomic_structure_commit_success"
                and result.get("applied") is True,
                str(error.get("code") or result.get("kind")),
            )
        print()

        print("[local state against the server's answer]")
        after_rows = project_structure_snapshot(database, local_key)
        after_disk = writing_root_fingerprint(root)
        show("unreadable_manuscripts_after", len(after_disk["unreadable"]))
        returned = {
            item["sequence"]: item
            for item in (result or {}).get("results") or []
        }
        # By entity kind rather than by position: the sequence a kind lands on
        # is a property of how the batch was planned, and reading a revision
        # off the wrong row would let a mismatch pass as a match.
        planned = {
            intent["entity_kind"]: intent
            for intent in request["ordered_intents"]
        }

        def server_revision(entity_kind):
            intent = planned.get(entity_kind)
            item = returned.get((intent or {}).get("sequence"))
            return None if item is None else int(item["result_revision"])

        folder = store.get_folder_by_path(local_key, target_path)
        order = store.get_tree_order(local_key, parent_path)
        show("folder_revision_local", (folder or {}).get("revision"))
        show("folder_revision_server", server_revision("folder"))
        show("tree_order_revision_local", (order or {}).get("revision"))
        show("tree_order_revision_server", server_revision("tree_order"))
        show("parent_children_local", len((order or {}).get("children") or []))
        states = batch_operation_states(store, after_rows, batch_id)
        show("operation_states", " ".join(states) or "(none)")
        checks.require(
            "the folder carries the revision the server gave it",
            folder_revision(store, local_key, target_path) is not None
            and folder_revision(store, local_key, target_path)
            == server_revision("folder"),
            "the local revision is not the one that came back",
        )
        checks.require(
            "the order carries the revision the server gave it",
            bool(order)
            and int(order["revision"]) == server_revision("tree_order"),
            "the local revision is not the one that came back",
        )
        checks.require(
            "the order holds exactly the children the batch named",
            bool(order)
            and order["children"]
            == planned.get("tree_order", {}).get("payload", {}).get("children"),
            "the order and the batch disagree",
        )
        checks.require(
            "the order names the folder the server created",
            bool(folder) and bool(order)
            and folder["folder_id"] in order["children"],
            "the folder is not in its parent's order",
        )
        checks.require(
            "every operation in the batch is finished",
            states == ["completed"],
            " ".join(states) or "(none)",
        )
        checks.require(
            "the answer is written down as applied",
            any(
                row["batch_id"] == batch_id and row["applied"]
                for row in after_rows["results"]
            ),
            "no applied result row",
        )
        checks.require(
            "no legacy operation was queued at any point",
            len(after_rows["operations"]) == len(before_rows["operations"]),
            f'{len(before_rows["operations"])} -> '
            f'{len(after_rows["operations"])}',
        )
        checks.require(
            "no document row moved",
            after_rows["documents"] == before_rows["documents"],
            "a document row changed",
        )
        checks.require(
            "the manuscript on disk is untouched",
            manuscript_bytes_unchanged(before_disk, after_disk),
            "a manuscript changed or could not be read",
        )
        # On a resumed run the folder was already on disk before this run took
        # its first reading, so what has to hold is that nothing appeared at
        # all -- committing a batch must not make directories.
        checks.require(
            "the only thing that appeared on disk is the folder that was made",
            sorted(set(after_disk["folders"]) - set(before_disk["folders"]))
            == ([] if resume is not None else [target_path])
            and not set(before_disk["folders"]) - set(after_disk["folders"]),
            "the writing root moved in some other way",
        )
        stored_after = store.get_project_by_id(project_id)
        show("gate_after", bool(stored_after["contract_path_enabled"]))
        show("project_sync_mode_after", stored_after["project_sync_mode"])
        show("migration_epoch_after", stored_after["migration_epoch"])
        checks.require(
            "the gate is still open and nothing was promoted",
            bool(stored_after["contract_path_enabled"])
            and (
                stored_after["project_sync_mode"],
                int(stored_after["migration_epoch"] or 0),
            ) == ("LEGACY", 0),
            f'{stored_after["project_sync_mode"]}'
            f'/{stored_after["migration_epoch"]}',
        )
        print()
        if checks.failed:
            return stop_on_failed_checks()
        checks.report()
        show(
            "verdict",
            "the server took one contract batch at protocol 3 and the local "
            "rows carry the revisions it gave back",
        )
        return 0
    finally:
        # One manager serves the whole process. Leaving it holding a store on a
        # directory that is about to be deleted is how the next thing to ask it
        # for a project gets a database that is no longer there.
        for manager in managers:
            try:
                manager.release_v2()
                manager.supabase = None
                manager._v2_store = None
                manager._v2_device_id = None
            except Exception:
                pass
        shutil.rmtree(workspace, ignore_errors=True)


def run_deactivation(
    live: Path, project_id: str, confirm_id: str, apply_change: bool
):
    """Shut one project's gate. The way back, and the only one there is.

    Opening needed the server's answer. Shutting does not, and must not: the
    reason to shut a gate is usually that something about the server is wrong
    or about to change, and a rollback that first has to ask the thing it is
    rolling back from is no rollback. So this builds no client, takes no
    credential lock and sends nothing.

    What the row keeps is deliberate. The digest, protocol and capabilities the
    last handshake recorded stay where they are, as the last observation. They
    open nothing on their own -- a shut gate is the first of three conditions
    and it fails alone.
    """
    from sync_v2_store import SyncV2Store

    checks = Checks()
    print("[deactivation]")
    show(
        "mode",
        "apply" if apply_change else "dry run - nothing will be written",
    )
    show("target_project_id", project_id)
    show("network_used", False)
    checks.require(
        "the project id was given twice and matched",
        bool(project_id) and project_id == confirm_id,
        "--project-id and --confirm-project-id differ",
    )
    if checks.failed:
        checks.report()
        show("verdict", "STOP - the two project ids do not match")
        return 1

    gates_before = gate_snapshot(live)
    if project_id not in gates_before:
        checks.report()
        show("verdict", "STOP - no such project here")
        return 1
    show("other_projects", len(gates_before) - 1)
    show("gate_before", gates_before[project_id][0])
    checks.require(
        "its gate is open to begin with",
        gates_before[project_id][0] is True,
        "it is already shut, so there is nothing to close",
    )

    # Read-only until there is something to write. Opening the store moves the
    # file even when no row changes, and a dry run that does that cannot be
    # held to the promise every other reporting mode is held to.
    connection = sqlite3.connect(live.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        local_key = connection.execute(
            "SELECT local_key FROM sync_projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()["local_key"]
    finally:
        connection.close()
    unanswered = unanswered_contract_batches(live, local_key)
    show("contract_batches_without_an_answer", unanswered)
    # A batch already queued goes out whether or not the gate is still open --
    # the gate is checked when a batch is built, not when it is sent. Shutting
    # over one would send it anyway, from a project that no longer means to.
    checks.require(
        "no batch is still waiting for an answer",
        unanswered == 0,
        f"{unanswered} waiting",
    )
    if checks.failed:
        checks.report()
        show(
            "verdict", "STOP - " + ", ".join(n for n, _p, _d in checks.failed)
        )
        return 1

    if not apply_change:
        checks.report()
        show(
            "verdict", "every check passed; re-run with --apply to shut the gate"
        )
        return 0

    work_before = project_work_counts(live, local_key)
    store = SyncV2Store(str(live))
    store.set_contract_path_enabled(local_key, False)
    gates_after = gate_snapshot(live)
    work_after = project_work_counts(live, local_key)
    stored = store.get_project_by_id(project_id)

    print()
    print("[what changed]")
    moved = [
        other for other in gates_after
        if gates_after[other] != gates_before[other]
    ]
    show("gate_rows_changed", len(moved))
    show("gate_after", gates_after[project_id][0])
    show("recorded_contract_sha256_kept", bool(stored["active_contract_sha256"]))
    show("project_sync_mode", stored["project_sync_mode"])
    show("migration_epoch", stored["migration_epoch"])
    checks.require(
        "exactly one gate moved", moved == [project_id], f"{len(moved)} moved"
    )
    checks.require(
        "it is shut now", gates_after[project_id][0] is False, "still open"
    )
    checks.require(
        "the other gates are where they were",
        all(
            gates_after[other] == gates_before[other]
            for other in gates_after if other != project_id
        ),
        "another project's gate moved",
    )
    checks.require(
        "shutting it queued nothing", work_after == work_before,
        "the queue or the batch table moved",
    )
    checks.require(
        "mode and epoch are untouched",
        (stored["project_sync_mode"], int(stored["migration_epoch"] or 0))
        == ("LEGACY", 0),
        f'{stored["project_sync_mode"]}/{stored["migration_epoch"]}',
    )
    print()
    checks.report()
    if checks.failed:
        show("verdict", "STOP - " + ", ".join(n for n, _p, _d in checks.failed))
        return 1
    show(
        "verdict",
        "the gate is shut for this project and no other; structure work goes "
        "out legacy again from here",
    )
    return 0


def run_stale_reorder_probe(
    live: Path,
    project_id: str,
    confirm_id: str,
    writing_root: str,
    parent_path: str,
    apply_change: bool,
):
    """Send one reorder the server has to refuse, and watch it refuse.

    Everything the contract path has been shown doing so far, the server took.
    The refusal side is written into the client -- a rejected batch is recorded
    as conflicted, applied zero, and its intents stop being sendable -- and none
    of it has ever run. This asks for exactly one refusal.

    The batch is built to be harmless twice over. Its children are the children
    the order already holds, so a server that took it anyway would move nothing;
    and its base revision is one behind, so a server that compares revisions has
    no choice but to refuse. Either answer is safe. Only one of them is
    informative.
    """
    from sync_manager import SyncManager, load_or_create_device_id
    from sync_v2_store import SyncV2Store

    checks = Checks()
    workspace = Path(tempfile.mkdtemp(prefix="contract-stale-"))
    managers = []

    def stop(reason):
        checks.report()
        show("verdict", f"STOP - {reason}")
        return 1

    def stop_on_failed_checks():
        return stop(", ".join(name for name, _p, _d in checks.failed))

    try:
        print("[stale reorder probe]")
        show(
            "mode",
            "apply - the live database, one batch the server should refuse"
            if apply_change else
            "dry run - a throwaway copy; nothing is sent",
        )
        show("target_project_id", project_id)
        show("parent_path", parent_path)
        checks.require(
            "the project id was given twice and matched",
            bool(project_id) and project_id == confirm_id,
            "--project-id and --confirm-project-id differ",
        )
        checks.require(
            "a parent path was given",
            bool(parent_path),
            "--parent-path is required",
        )
        if checks.failed:
            return stop_on_failed_checks()

        connection = sqlite3.connect(live.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT local_key FROM sync_projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return stop("no such project here")
        local_key = row["local_key"]

        if apply_change:
            database = live
            checks.require(
                "a writing root was named",
                bool(str(writing_root or "").strip()),
                "--writing-root is required with --apply",
            )
            root = Path(writing_root) if writing_root else workspace / "writing"
            if str(writing_root or "").strip():
                try:
                    named_key = SyncV2Store.local_key_for(str(root))
                except Exception:
                    named_key = ""
                checks.require(
                    "the writing root is this project's own",
                    named_key == local_key,
                    "--writing-root belongs to a different project",
                )
        else:
            database = workspace / "sync_v2.sqlite3"
            copy_database(live, database)
            root = workspace / "writing"
            root.mkdir(parents=True, exist_ok=True)
        if checks.failed:
            return stop_on_failed_checks()

        store = SyncV2Store(str(database))
        project = store.get_project_by_id(project_id)
        checks.require(
            "its gate is open",
            bool(project["contract_path_enabled"]),
            "the gate is shut, so no contract batch can be built",
        )
        unanswered = unanswered_contract_batches(database, local_key)
        show("contract_batches_without_an_answer", unanswered)
        checks.require(
            "no earlier batch is still unanswered",
            unanswered == 0,
            f"{unanswered} waiting",
        )

        order = store.get_tree_order(local_key, parent_path)
        checks.require(
            "the parent already holds a server-proven order",
            bool(order) and int(order["revision"] or 0) >= 2,
            "there is no order row, or its revision is too low to go one back",
        )
        if checks.failed:
            return stop_on_failed_checks()

        standing = int(order["revision"])
        stale = standing - 1
        children = list(order["children"])
        show("order_revision_on_the_server", standing)
        show("base_revision_this_batch_will_claim", stale)
        show("children_named", len(children))
        # The children are the ones already there. Even a server that ignored
        # the revision entirely would write back what it already holds.
        show("children_differ_from_what_is_there", False)

        disk_before = writing_root_fingerprint(root)
        show("manuscript_documents", len(disk_before["documents"]))
        show("unreadable_manuscripts", len(disk_before["unreadable"]))
        checks.require(
            "every manuscript can be hashed before any request",
            not disk_before["unreadable"],
            f'{len(disk_before["unreadable"])} unreadable manuscript(s)',
        )
        if apply_change:
            checks.require(
                "a manuscript exists for the no-damage check",
                manuscript_evidence_available(disk_before),
                "the project has no readable .txt manuscript to compare",
            )
        if checks.failed:
            return stop_on_failed_checks()
        print()

        client = None
        if apply_change:
            client = SyncManager.create_supabase_client()
            if client is None or not getattr(
                client, "_antigravity_authenticated", False
            ):
                show(
                    "client",
                    "RESTORE FAILED - the stored session was kept"
                    if client is not None else "NO CLIENT",
                )
                checks.require(
                    "a signed-in client is available", False, "not authenticated"
                )
                return stop_on_failed_checks()
            checks.require("a signed-in client is available", True)

        device_id = (
            load_or_create_device_id() if apply_change else str(uuid.uuid4())
        )
        manager = build_manager(
            store, project, device_id, allow_client=apply_change
        )
        managers.append(manager)
        if apply_change:
            manager.supabase = client
            print("[fresh handshake]")
            try:
                reading = manager.perform_contract_handshake(
                    require_connection=True
                )
            except Exception as error:
                code = getattr(error, "code", "") or type(error).__name__
                show("perform_contract_handshake", f"RAISED {code}")
                checks.require("the handshake completed", False, code)
                return stop_on_failed_checks()
            show("outcome", (reading or {}).get("outcome"))
            checks.require(
                "the server supports this client",
                (reading or {}).get("outcome") == "supported",
                str((reading or {}).get("outcome")),
            )
        else:
            print("[stored contract state; no client and no request]")
            try:
                arm_manager_from_stored_contract(manager, project)
            except Exception as error:
                code = getattr(error, "code", "") or type(error).__name__
                checks.require("the stored contract state is usable", False, code)
                return stop_on_failed_checks()
            checks.require("the stored contract state is usable", True)
        checks.require(
            "the contract path is armed for this project",
            manager._uses_contract_structure(),
            "structure work would go out legacy",
        )
        if checks.failed:
            return stop_on_failed_checks()
        print()

        before = project_structure_snapshot(database, local_key)
        print("[the batch]")
        intent = {
            "entity_kind": "tree_order",
            "entity_id": order["tree_order_id"],
            "intent_kind": "reorder",
            "base_revision": stale,
            "payload": {
                "parent_folder_id": order["parent_folder_id"],
                "children": children,
            },
        }
        request = manager.queue_atomic_structure_batch([intent], retry=False)
        batch_id = request["batch"]["batch_id"]
        show("batch_id", batch_id)
        show("sync_protocol_version", request["batch"]["sync_protocol_version"])
        show("contract_version", request["batch"]["contract_version"])
        show(
            "intents",
            " ".join(
                f'{item["sequence"]}:{item["entity_kind"]}'
                f'/{item["intent_kind"]}@{item["base_revision"]}'
                for item in request["ordered_intents"]
            ),
        )
        checks.require(
            "it is one reorder at a revision the server has moved past",
            [
                (item["entity_kind"], item["intent_kind"], item["base_revision"])
                for item in request["ordered_intents"]
            ] == [("tree_order", "reorder", stale)],
            "the batch holds something else",
        )

        if not apply_change:
            print()
            if checks.failed:
                return stop_on_failed_checks()
            checks.report()
            show(
                "verdict",
                "the batch is built and nothing was sent; re-run with --apply "
                "to ask the server for the refusal",
            )
            return 0
        if checks.failed:
            return stop_on_failed_checks()
        print()

        print("[dispatch]")
        result = None
        try:
            store.mark_structure_batch_attempt(batch_id)
            result = manager._process_contract_structure_batch(batch_id)
        except Exception as error:
            code = getattr(error, "code", "") or type(error).__name__
            try:
                store.mark_structure_batch_retry(batch_id, code)
            except Exception:
                pass
            show("dispatch", f"RAISED {code}")
            checks.require("the server answered", False, code)
        if result is not None:
            error = result.get("error") or {}
            show("dispatch", "ANSWERED")
            show("kind", result.get("kind"))
            show("status", result.get("status"))
            show("applied", result.get("applied") is True)
            show("error_code", error.get("code") or "(none)")
            show("failed_sequence", error.get("failed_sequence"))
            checks.require("the server answered", True)
            # The whole point. A server that took this would be one that does
            # not compare the revision it was handed.
            checks.require(
                "the server refused the batch",
                result.get("kind") == "atomic_structure_commit_failure"
                and result.get("applied") is False,
                f'it answered {result.get("kind")}',
            )
            checks.require(
                "it refused with REVISION_CONFLICT",
                error.get("code") == "REVISION_CONFLICT",
                str(error.get("code")),
            )
        print()

        print("[what the refusal left behind]")
        after = project_structure_snapshot(database, local_key)
        after_disk = writing_root_fingerprint(root)
        order_after = store.get_tree_order(local_key, parent_path)
        states = batch_operation_states(store, after, batch_id)
        show("order_revision_after", (order_after or {}).get("revision"))
        show("children_after", len((order_after or {}).get("children") or []))
        show("operation_states", " ".join(states) or "(none)")
        show(
            "result_rows_applied",
            sum(1 for item in after["results"] if item["applied"]),
        )
        checks.require(
            "the order did not move",
            bool(order_after)
            and int(order_after["revision"]) == standing
            and order_after["children"] == children,
            "a refused batch changed the order",
        )
        checks.require(
            "the batch is recorded as not applied",
            any(
                item["batch_id"] == batch_id and not item["applied"]
                for item in after["results"]
            ),
            "no unapplied result row",
        )
        # Conflicted, not retried. A refusal no wait can change must not go
        # back on the wire by itself.
        checks.require(
            "its operation is held in conflict, not queued again",
            states == ["conflict"],
            " ".join(states) or "(none)",
        )
        checks.require(
            "no folder moved",
            after["folders"] == before["folders"],
            "a folder row changed under a refused batch",
        )
        checks.require(
            "no document row moved",
            after["documents"] == before["documents"],
            "a document row changed under a refused batch",
        )
        checks.require(
            "the manuscript on disk is untouched",
            manuscript_bytes_unchanged(disk_before, after_disk),
            "a manuscript changed or could not be read",
        )
        stored_after = store.get_project_by_id(project_id)
        checks.require(
            "the gate is still open and nothing was promoted",
            bool(stored_after["contract_path_enabled"])
            and (
                stored_after["project_sync_mode"],
                int(stored_after["migration_epoch"] or 0),
            ) == ("LEGACY", 0),
            f'{stored_after["project_sync_mode"]}'
            f'/{stored_after["migration_epoch"]}',
        )
        print()
        if checks.failed:
            return stop_on_failed_checks()
        checks.report()
        show(
            "verdict",
            "the server compares the revision it is handed and refuses when it "
            "has moved past it; the refusal changed nothing here",
        )
        return 0
    finally:
        for manager in managers:
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
        "--credential-lock-check", action="store_true",
        help="report whether the machine-wide credential lock is free, then "
             "exit. Builds no client and makes no request",
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
    parser.add_argument(
        "--activate-contract-path", action="store_true",
        help="open the local contract gate for one project, after every check "
             "passes. Reports only unless --apply is given",
    )
    parser.add_argument(
        "--project-id", default="",
        help="the project whose gate to open",
    )
    parser.add_argument(
        "--confirm-project-id", default="",
        help="the same id again; they must match",
    )
    parser.add_argument(
        "--structure-write", action="store_true",
        help="make one folder on the contract path in a project whose gate is "
             "open, and report what the server did with the batch. Works on a "
             "throwaway copy and sends nothing unless --apply is given",
    )
    parser.add_argument(
        "--writing-root", default="",
        help="the project's own writing root, required with "
             "--structure-write --apply. It must be the root this project id "
             "is registered under",
    )
    # No defaults, and deliberately not: a folder name this tool chose for
    # itself would be a folder somebody has to explain later. The operator
    # names it, and the name is echoed back into the record.
    parser.add_argument(
        "--parent-path", default="",
        help="the folder to make the new one inside, required with "
             "--structure-write. It must already have a server-proven revision",
    )
    parser.add_argument(
        "--folder-name", default="",
        help="the name of the folder to make, required with --structure-write",
    )
    parser.add_argument(
        "--deactivate-contract-path", action="store_true",
        help="shut one project's contract gate. Builds no client, takes no "
             "credential lock and sends nothing. Reports only unless --apply",
    )
    parser.add_argument(
        "--stale-reorder-probe", action="store_true",
        help="send one reorder against a revision the server has moved past, "
             "to find out whether it refuses. Its children are the ones "
             "already there, so even being taken would move nothing",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="actually open the gate, or actually make the folder and send "
             "its batch. Without it nothing is written and nothing is sent",
    )
    args = parser.parse_args()

    if args.credential_lock_check:
        # Deliberately before the database is opened and before anything that
        # could build a client. This mode exists so the lock can be tested
        # without a run that might proceed to the server if the test fails.
        from sync_manager import SyncManager

        print("[credential lock]")
        show_lease_scope()
        lease = SyncManager.acquire_auth_lease()
        show(
            "acquired",
            "unavailable on this machine" if lease is None else bool(lease),
        )
        show("network_used", False)
        if lease is True:
            SyncManager.release_auth_lease()
            show("verdict", "the credential is free")
            return 0
        show(
            "verdict",
            "STOP - something else holds the credential"
            if lease is False else
            "STOP - the machine-wide credential lock is unavailable",
        )
        return 1

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
    holds_credential = False
    if (
        args.rpc_handshake
        or args.application_handshake
        or args.activate_contract_path
        or (args.structure_write and args.apply)
        or (args.stale_reorder_probe and args.apply)
    ):
        # Both of these build a real client and spend the stored refresh token.
        # Taking the same lock the application takes is what makes that safe;
        # asking whether the application is running would be a question whose
        # answer is already stale by the time it is acted on.
        from sync_manager import SyncManager

        print("[credential lock]")
        show_lease_scope()
        lease = SyncManager.acquire_auth_lease()
        show(
            "acquired",
            "unavailable on this machine" if lease is None else bool(lease),
        )
        if lease is not True:
            show(
                "verdict",
                "STOP - something else holds the credential; close the "
                "application and any other copy of this tool"
                if lease is False else
                "STOP - the machine-wide credential lock is unavailable",
            )
            print()
            return 1
        holds_credential = True
        print()

    try:
        if args.rpc_handshake:
            run_handshakes(projects, only_project)
        if args.application_handshake:
            status = run_application_handshake(database, only_project)
        if args.activate_contract_path:
            status = run_activation(
                database,
                args.project_id.strip(),
                args.confirm_project_id.strip(),
                args.apply,
            )
        if args.deactivate_contract_path:
            status = run_deactivation(
                database,
                args.project_id.strip(),
                args.confirm_project_id.strip(),
                args.apply,
            )
        if args.stale_reorder_probe:
            status = run_stale_reorder_probe(
                database,
                args.project_id.strip(),
                args.confirm_project_id.strip(),
                args.writing_root.strip(),
                args.parent_path.strip(),
                args.apply,
            )
        if args.structure_write:
            status = run_structure_write(
                database,
                args.project_id.strip(),
                args.confirm_project_id.strip(),
                args.writing_root.strip(),
                args.parent_path.strip(),
                args.folder_name.strip(),
                args.apply,
            )
    finally:
        if holds_credential:
            from sync_manager import SyncManager as _SyncManager

            _SyncManager.release_auth_lease()

    if not args.activate_contract_path and not (
        args.deactivate_contract_path and args.apply
    ) and not (
        (args.structure_write or args.stale_reorder_probe) and args.apply
    ):
        # Every other mode promises not to write. This is where that promise is
        # checked rather than asserted. Activation writes deliberately and
        # accounts for its own changes, field by field, above, and so does a
        # structure write that was told to apply. A structure write that was
        # not works on a copy, so it is held to the promise like the rest.
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
