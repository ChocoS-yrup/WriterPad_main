"""Local acceptance of a freshly observed server checkpoint, never a migration RPC."""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from sync_contract import (CANONICAL_CONTRACT_SHA256, CONTRACT_VERSION, SyncContractError,
                           require_server_compatibility)

SCHEMA_VERSION = 8013
FUNCTION = "writerpad_checkpoint_observation_allowed"
TRIGGER = """
CREATE TRIGGER sync_projects_mode_epoch_transition
BEFORE UPDATE OF project_sync_mode, migration_epoch ON sync_projects
WHEN NOT (
    (NEW.project_sync_mode = OLD.project_sync_mode AND NEW.migration_epoch = OLD.migration_epoch)
    OR (OLD.project_sync_mode = 'LEGACY' AND OLD.migration_epoch = 0
        AND NEW.project_sync_mode = 'MIGRATING' AND NEW.migration_epoch = 1)
    OR (OLD.project_sync_mode = 'MIGRATING' AND OLD.migration_epoch >= 1
        AND NEW.project_sync_mode = 'ID_BASED' AND NEW.migration_epoch = OLD.migration_epoch)
    OR (OLD.project_sync_mode = 'LEGACY' AND OLD.migration_epoch = 0
        AND NEW.project_sync_mode = 'ID_BASED' AND NEW.migration_epoch = 1
        AND OLD.contract_path_enabled = 0 AND NEW.contract_path_enabled = 0
        AND OLD.local_key = NEW.local_key AND OLD.project_id = NEW.project_id
        AND EXISTS (SELECT 1 FROM sync_checkpoint_permits p
            WHERE p.local_key=NEW.local_key AND p.project_id=NEW.project_id
                AND p.project_name=NEW.project_name AND p.protocol=NEW.server_protocol_version
                AND p.digest=NEW.active_contract_sha256))
)
BEGIN SELECT RAISE(ABORT, 'INVALID_PROJECT_MODE_TRANSITION'); END
"""


def register_guard(connection):
    # Permission belongs to this connection/transaction. SQL alone cannot
    # insert a permit, and no process-global flag can arm another connection.
    connection.create_function(FUNCTION, 7, lambda *args: 0)


def upgrade(store):
    with store._transaction() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported sync database user_version {version}")
        if version != 8012:
            return
        # execute, not executescript: DDL and user_version stay in the same
        # BEGIN IMMEDIATE transaction. Failure restores the old trigger too.
        connection.execute("""CREATE TABLE IF NOT EXISTS sync_server_checkpoint_observations (
            observation_id TEXT PRIMARY KEY, local_key TEXT NOT NULL,
            project_id TEXT NOT NULL, old_mode TEXT NOT NULL, old_epoch INTEGER NOT NULL,
            new_mode TEXT NOT NULL, new_epoch INTEGER NOT NULL,
            observed_at TEXT NOT NULL, source_json TEXT NOT NULL)""")
        connection.execute("""CREATE TRIGGER IF NOT EXISTS sync_checkpoint_observations_no_update
            BEFORE UPDATE ON sync_server_checkpoint_observations
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_CHECKPOINT_OBSERVATION'); END""")
        connection.execute("""CREATE TRIGGER IF NOT EXISTS sync_checkpoint_observations_no_delete
            BEFORE DELETE ON sync_server_checkpoint_observations
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_CHECKPOINT_OBSERVATION'); END""")
        # The permit is created and removed within the observation transaction.
        # Its INSERT is guarded by the connection-only function. Keeping that
        # function off the project trigger preserves ordinary SQL constraints
        # (including their IntegrityError) on connections without app functions.
        connection.execute("""CREATE TABLE IF NOT EXISTS sync_checkpoint_permits (
            local_key TEXT PRIMARY KEY, project_id TEXT NOT NULL, project_name TEXT NOT NULL,
            protocol INTEGER NOT NULL, digest TEXT NOT NULL)""")
        if connection.execute("SELECT 1 FROM sync_checkpoint_permits LIMIT 1").fetchone():
            raise RuntimeError("UNEXPECTED_CHECKPOINT_PERMIT")
        connection.execute("""CREATE TRIGGER IF NOT EXISTS sync_checkpoint_permit_insert
            BEFORE INSERT ON sync_checkpoint_permits
            WHEN writerpad_checkpoint_observation_allowed(NEW.local_key, NEW.project_id,
                NEW.local_key, NEW.project_id, NEW.project_name, NEW.protocol, NEW.digest) <> 1
            BEGIN SELECT RAISE(ABORT, 'CHECKPOINT_PERMISSION_REQUIRED'); END""")
        connection.execute("""CREATE TRIGGER IF NOT EXISTS sync_checkpoint_permit_no_update
            BEFORE UPDATE ON sync_checkpoint_permits
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_CHECKPOINT_PERMIT'); END""")
        connection.execute("DROP TRIGGER sync_projects_mode_epoch_transition")
        connection.execute(TRIGGER)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _empty_queue(store, connection, key):
    for table in ("sync_operations", "sync_structure_operations"):
        for row in connection.execute(f"SELECT operation_id FROM {table} WHERE local_key = ?", (key,)):
            if store._derived_state(connection, row["operation_id"]) in {
                "pending", "inflight", "retry_wait", "blocked", "conflict"
            }:
                raise SyncContractError("CONTRACT_PREPARATION_NOT_READY")


def accept(store, *, binding, expected_checkpoint, compatibility, source, guard,
           legacy_candidate=None):
    """Accept a validated checkpoint under the manager lock.

    guard revalidates runtime binding, generation, durable hold, closed gate,
    memory work and queue stamp after SQLite has reserved the write lock. A
    rejected legacy chain may cross only when the caller keeps this transaction
    open through gate opening and successor preparation.
    Completed requests and every historical event remain untouched.
    """
    require_server_compatibility(**compatibility)
    if (compatibility["project_sync_mode"], compatibility["migration_epoch"],
            compatibility["server_protocol_version"], compatibility["server_contract_sha256"]) != (
                "ID_BASED", 1, 3, CANONICAL_CONTRACT_SHA256):
        raise SyncContractError("INVALID_PROJECT_MODE_TRANSITION")
    key, project_id, name = binding
    with store._transaction() as connection:
        guard()
        row = connection.execute("SELECT * FROM sync_projects WHERE local_key = ?", (key,)).fetchone()
        if (row is None or (row["project_id"], row["project_name"]) != (project_id, name)
                or row["contract_path_enabled"]
                or (row["project_sync_mode"], row["migration_epoch"]) != expected_checkpoint):
            raise SyncContractError("CONTRACT_PREPARATION_NOT_READY")
        if legacy_candidate is None:
            _empty_queue(store, connection, key)
        elif store.rejected_legacy_create_candidate(key) != legacy_candidate:
            raise SyncContractError("LEGACY_CHAIN_UNSAFE")
        old = (row["project_sync_mode"], row["migration_epoch"])
        if old == ("ID_BASED", 1):
            return dict(row), False
        if old != ("LEGACY", 0):
            raise SyncContractError("INVALID_PROJECT_MODE_TRANSITION")
        expected = (key, project_id, key, project_id, name, 3, CANONICAL_CONTRACT_SHA256)
        def permitted(*args):
            guard()
            return int(args == expected)
        connection.create_function(FUNCTION, 7, permitted)
        try:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO sync_checkpoint_permits VALUES (?, ?, ?, ?, ?)",
                               (key, project_id, name, 3, CANONICAL_CONTRACT_SHA256))
            connection.execute("""UPDATE sync_projects SET project_sync_mode='ID_BASED', migration_epoch=1,
                server_protocol_version=3, active_contract_sha256=?, server_capabilities_json=?,
                contract_validated_at=?, updated_at=? WHERE local_key=?""",
                (CANONICAL_CONTRACT_SHA256, json.dumps(sorted(compatibility["server_capabilities"])), now, now, key))
            connection.execute("""INSERT INTO sync_server_checkpoint_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), key, project_id, old[0], old[1], "ID_BASED", 1, source["observed_at"],
                 json.dumps(source, ensure_ascii=False, sort_keys=True)))
            connection.execute("DELETE FROM sync_checkpoint_permits WHERE local_key=?", (key,))
        finally:
            register_guard(connection)
        return dict(connection.execute("SELECT * FROM sync_projects WHERE local_key=?", (key,)).fetchone()), True


def source_record(reading):
    # The binding contains private local paths/account identifiers. Persist
    # only their digest, plus the already validated response metadata.
    fingerprint = hashlib.sha256(repr(reading["context_key"]).encode("utf-8")).hexdigest()
    return {"kind": "fresh_get_sync_handshake", "observed_at": reading["observed_at"],
            "binding_sha256": fingerprint, "contract_sha256": CANONICAL_CONTRACT_SHA256,
            "protocol": 3, "contract_version": CONTRACT_VERSION}
