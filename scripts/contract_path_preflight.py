"""Read-only preflight for the contract write path.

Prints the three things a person needs before deciding whether to open the
local contract gate, and changes none of them:

  1. the client pin, to cross-check against the server allowlist row
  2. the local gate and the server state stored beside it, read straight out
     of the live database with mode=ro
  3. optionally one fresh handshake per project, validated exactly the way the
     client validates it, held entirely in memory

This tool never opens the gate, never writes to the database, and never
promotes a project. It prints no project names, no paths and no document
content; project ids and metadata only. The database digest is reported before
and after so a run can be shown to have changed nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
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
    uri = database.as_uri() + "?mode=ro&immutable=1"
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


def print_projects(user_version, present, projects):
    print("[local database]")
    show("user_version", user_version)
    show("projects", len(projects))
    if "contract_path_enabled" not in present:
        show("gate_column", "ABSENT -- this build predates the local gate")
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
        show("contract_path_enabled", gate_open)
        show("contract_path_enabled_at", project.get("contract_path_enabled_at"))
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, default=None,
        help="sync database to read; defaults to the live profile database",
    )
    parser.add_argument(
        "--handshake", action="store_true",
        help="also call get_sync_handshake once per project (read-only)",
    )
    parser.add_argument(
        "--project", default="",
        help="limit --handshake to one project id",
    )
    args = parser.parse_args()

    database = (args.database or default_database()).resolve(strict=True)
    before = file_sha256(database)

    print_client_pin()
    user_version, present, projects = read_projects(database)
    print("[database file]")
    show("path", database)
    show("sha256_before", before)
    print()
    print_projects(user_version, present, projects)

    if args.handshake:
        run_handshakes(projects, args.project.strip())

    print("[database file]")
    show("sha256_after", file_sha256(database))
    show("unchanged", file_sha256(database) == before)


if __name__ == "__main__":
    main()
