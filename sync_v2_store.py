import hashlib
import json
import os
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from project_creation_v1 import identity_uuid_for_writing_path
from project_identity_v1 import KIND_DOCUMENT, KIND_FOLDER
from sync_contract import (
    CANONICAL_CONTRACT_SHA256,
    CLIENT_CAPABILITIES,
    CLIENT_BUILD_ID,
    CONTRACT_VERSION,
    EVENT_STATE,
    SYNC_PROTOCOL_VERSION,
    TERMINAL_STATES,
    SyncContractError,
    build_atomic_structure_request,
    build_document_commit_request,
    canonical_json,
    json_sha256,
    normalize_storage_name,
    require_server_compatibility,
    safe_trace,
    validate_atomic_structure_response,
    validate_document_commit_response,
)


ACTIVE_OPERATION_STATES = ("pending", "inflight", "conflict")
CONTRACT_ACTIVE_STATES = (
    "pending", "inflight", "retry_wait", "blocked", "conflict"
)
STAGE8_USER_VERSION = 8006


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _normalize_path(path):
    return unicodedata.normalize(
        "NFC", (path or "").replace("\\", "/").strip("/")
    )


# Where a folder row goes once the server projection stops listing it. The
# row stays for its id and revision; the path only has to be one no live folder
# can ever hold, because sync_folders is unique on (local_key, local_path).
FOLDER_TOMBSTONE_PREFIX = "__folder_tombstone__"


class SyncV2Store:
    """SQLite-backed identity, revision and durable operation queue for sync v2."""

    def __init__(self, db_path=None):
        if db_path is None:
            from runtime_profile import app_data_dir
            app_data = app_data_dir()
            os.makedirs(app_data, exist_ok=True)
            db_path = os.path.join(app_data, "sync_v2.sqlite3")
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._schema_lock = threading.Lock()
        self._transaction_state = threading.local()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _transaction(self):
        active = getattr(self._transaction_state, "connection", None)
        if active is not None:
            yield active
            return
        connection = self._connect()
        self._transaction_state.connection = connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._transaction_state.connection = None
            connection.close()

    @contextmanager
    def _reader(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._schema_lock, self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_projects (
                    local_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL UNIQUE,
                    project_name TEXT NOT NULL,
                    server_name TEXT NOT NULL DEFAULT '',
                    server_state TEXT NOT NULL DEFAULT 'active',
                    server_state_updated_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_project_imports (
                    project_id TEXT PRIMARY KEY
                        REFERENCES sync_projects(project_id) ON DELETE CASCADE,
                    local_key TEXT NOT NULL UNIQUE
                        REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                    project_name TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK (state IN ('preparing', 'pulling', 'failed', 'complete')),
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_documents (
                    document_id TEXT PRIMARY KEY,
                    local_key TEXT NOT NULL REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                    local_path TEXT NOT NULL,
                    server_path TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                    base_content TEXT NOT NULL DEFAULT '',
                    base_hash TEXT NOT NULL DEFAULT '',
                    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
                    sync_state TEXT NOT NULL DEFAULT 'local',
                    last_error TEXT NOT NULL DEFAULT '',
                    conflict_base TEXT,
                    conflict_local TEXT,
                    conflict_remote TEXT,
                    conflict_merged TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(local_key, local_path)
                );

                CREATE INDEX IF NOT EXISTS sync_documents_project_idx
                    ON sync_documents(local_key, document_id);

                CREATE TABLE IF NOT EXISTS sync_folders (
                    folder_id TEXT PRIMARY KEY,
                    local_key TEXT NOT NULL
                        REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                    parent_folder_id TEXT,
                    local_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    storage_name_key TEXT,
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                    is_deleted INTEGER NOT NULL DEFAULT 0
                        CHECK (is_deleted IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(local_key, local_path)
                );

                CREATE INDEX IF NOT EXISTS sync_folders_project_idx
                    ON sync_folders(local_key, folder_id);

                CREATE TABLE IF NOT EXISTS sync_tree_orders (
                    tree_order_id TEXT PRIMARY KEY,
                    local_key TEXT NOT NULL
                        REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                    parent_folder_id TEXT,
                    parent_path TEXT NOT NULL,
                    children_json TEXT NOT NULL DEFAULT '[]',
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(local_key, parent_path)
                );

                CREATE TABLE IF NOT EXISTS sync_operations (
                    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL UNIQUE,
                    local_key TEXT NOT NULL REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                    project_id TEXT NOT NULL,
                    document_id TEXT NOT NULL REFERENCES sync_documents(document_id) ON DELETE CASCADE,
                    local_path TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    base_revision INTEGER,
                    base_content TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS sync_operations_ready_idx
                    ON sync_operations(status, base_revision, queue_id);
                CREATE INDEX IF NOT EXISTS sync_operations_document_idx
                    ON sync_operations(document_id, queue_id);

                CREATE TABLE IF NOT EXISTS sync_tree_barriers (
                    barrier_id TEXT PRIMARY KEY,
                    local_key TEXT NOT NULL UNIQUE
                        REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                    project_id TEXT NOT NULL,
                    tree_order_content TEXT NOT NULL,
                    required_operation_ids TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_folder_rename_intents (
                    intent_id TEXT PRIMARY KEY,
                    local_key TEXT NOT NULL
                        REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                    old_path TEXT NOT NULL,
                    new_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'completed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS sync_folder_rename_intents_pending_idx
                    ON sync_folder_rename_intents(
                        local_key, status, old_path, new_path
                    );

                CREATE TABLE IF NOT EXISTS sync_folder_rename_intent_events (
                    event_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL REFERENCES
                        sync_folder_rename_intents(intent_id) ON DELETE CASCADE,
                    event_sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL
                        CHECK (event_type IN ('recorded', 'retargeted', 'completed')),
                    recorded_at TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(intent_id, event_sequence)
                );

                CREATE INDEX IF NOT EXISTS sync_folder_rename_intent_events_idx
                    ON sync_folder_rename_intent_events(
                        intent_id, event_sequence
                    );
                """
            )
            self._migrate_contract_schema(connection)

    @staticmethod
    def _table_columns(connection, table):
        return {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }

    @classmethod
    def _add_column(cls, connection, table, definition):
        name = definition.split()[0]
        if name not in cls._table_columns(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    @staticmethod
    def _legacy_event_id(operation_id, event_type):
        return str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"writerpad:stage8:legacy:{operation_id}:{event_type}",
        ))

    @staticmethod
    def _legacy_folder_rename_event_id(intent_id, event_type):
        return str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"writerpad:folder-rename-intent:legacy:{intent_id}:{event_type}",
        ))

    def _migrate_contract_schema(self, connection):
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > STAGE8_USER_VERSION:
            raise RuntimeError(
                f"Unsupported sync database user_version {current_version}"
            )

        for definition in (
            "project_sync_mode TEXT NOT NULL DEFAULT 'LEGACY'",
            "migration_epoch INTEGER NOT NULL DEFAULT 0",
            "server_protocol_version INTEGER",
            "active_contract_sha256 TEXT",
            "server_capabilities_json TEXT",
            "contract_validated_at TEXT",
            # The local gate for the contract write path. A handshake proves what
            # the server supports; this column records that a person decided to
            # use it. It stays off until somebody turns it on, and it sits in the
            # project row so the decision is visible next to the server state it
            # applies to.
            "contract_path_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK (contract_path_enabled IN (0, 1))",
            "contract_path_enabled_at TEXT",
        ):
            self._add_column(connection, "sync_projects", definition)

        for definition in (
            "entity_kind TEXT",
            "intent_kind TEXT",
            "provenance_kind TEXT",
            "batch_id TEXT",
            "batch_sequence INTEGER",
            "payload_sha256 TEXT",
            "supersedes_operation_id TEXT",
            "sync_protocol_version INTEGER",
            "contract_version TEXT",
            "canonical_contract_sha256 TEXT",
            "client_build_id TEXT",
            "client_capabilities_json TEXT",
            "legacy_imported_at TEXT",
            "legacy_attempt_count INTEGER NOT NULL DEFAULT 0",
        ):
            self._add_column(connection, "sync_operations", definition)

        for definition in (
            "parent_folder_id TEXT",
            "structure_revision INTEGER",
            "name TEXT",
            "storage_name_key TEXT",
        ):
            self._add_column(connection, "sync_documents", definition)

        self._add_column(connection, "sync_folders", "storage_name_key TEXT")

        connection.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS sync_folders_storage_name_idx
                ON sync_folders(
                    local_key, COALESCE(parent_folder_id, ''), storage_name_key
                ) WHERE storage_name_key IS NOT NULL AND is_deleted = 0;
            CREATE UNIQUE INDEX IF NOT EXISTS sync_documents_storage_name_idx
                ON sync_documents(
                    local_key, COALESCE(parent_folder_id, ''), storage_name_key
                ) WHERE storage_name_key IS NOT NULL AND is_deleted = 0;
            """
        )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_contract_batches (
                batch_id TEXT PRIMARY KEY,
                local_key TEXT NOT NULL REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                project_id TEXT NOT NULL,
                writer_device_id TEXT NOT NULL,
                client_build_id TEXT NOT NULL,
                sync_protocol_version INTEGER NOT NULL,
                contract_version TEXT NOT NULL,
                canonical_contract_sha256 TEXT NOT NULL,
                client_capabilities_json TEXT NOT NULL,
                batch_payload_sha256 TEXT NOT NULL,
                project_sync_mode TEXT NOT NULL,
                migration_epoch INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_structure_operations (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                local_key TEXT NOT NULL REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                project_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                intent_kind TEXT NOT NULL,
                provenance_kind TEXT NOT NULL CHECK (provenance_kind = 'CONTRACT_BATCH'),
                batch_id TEXT NOT NULL REFERENCES sync_contract_batches(batch_id),
                batch_sequence INTEGER NOT NULL,
                base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                supersedes_operation_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(batch_id, batch_sequence)
            );

            CREATE TABLE IF NOT EXISTS sync_operation_attempts (
                attempt_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                rpc_name TEXT NOT NULL,
                outcome TEXT NOT NULL,
                request_sha256 TEXT,
                response_sha256 TEXT,
                http_status INTEGER,
                error_code TEXT,
                error_detail_json TEXT NOT NULL DEFAULT '{}',
                result_revision INTEGER,
                UNIQUE(operation_id, attempt_number)
            );

            CREATE TABLE IF NOT EXISTS sync_operation_events (
                event_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                event_sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                error_code TEXT,
                related_operation_id TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(operation_id, event_sequence)
            );

            CREATE TABLE IF NOT EXISTS sync_contract_batch_results (
                batch_id TEXT PRIMARY KEY REFERENCES sync_contract_batches(batch_id),
                response_json TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                applied INTEGER NOT NULL CHECK (applied IN (0, 1)),
                recorded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_contract_diagnostics (
                trace_id TEXT PRIMARY KEY,
                local_key TEXT,
                event TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_structure_recovery (
                recovery_id TEXT PRIMARY KEY,
                local_key TEXT NOT NULL
                    REFERENCES sync_projects(local_key) ON DELETE CASCADE,
                old_path TEXT NOT NULL,
                new_path TEXT NOT NULL,
                error_code TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS sync_contract_batches_ready_idx
                ON sync_contract_batches(local_key, created_at);
            CREATE INDEX IF NOT EXISTS sync_structure_operations_batch_idx
                ON sync_structure_operations(batch_id, batch_sequence);
            CREATE INDEX IF NOT EXISTS sync_operation_events_operation_idx
                ON sync_operation_events(operation_id, event_sequence);
            CREATE INDEX IF NOT EXISTS sync_operation_attempts_operation_idx
                ON sync_operation_attempts(operation_id, attempt_number);
            """
        )

        now = _utc_now()
        legacy_rows = connection.execute(
            """
            SELECT * FROM sync_operations
            WHERE provenance_kind IS NULL
            ORDER BY queue_id
            """
        ).fetchall()
        for row in legacy_rows:
            payload = {
                "base_content": row["base_content"],
                "content": row["content"],
                "is_deleted": bool(row["is_deleted"]),
                "local_path": row["local_path"],
                "relative_path": row["relative_path"],
            }
            intent_kind = (
                "delete" if row["is_deleted"]
                else "create" if int(row["base_revision"] or 0) == 0
                else "update"
            )
            connection.execute(
                """
                UPDATE sync_operations
                SET entity_kind = 'document', intent_kind = ?,
                    provenance_kind = 'LEGACY_EPOCH_0', payload_sha256 = ?,
                    sync_protocol_version = 2, legacy_imported_at = ?,
                    legacy_attempt_count = attempts
                WHERE operation_id = ?
                """,
                (intent_kind, json_sha256(payload), now, row["operation_id"]),
            )
            events = ["enqueued"]
            status = str(row["status"] or "pending")
            if status == "completed":
                events.append("committed")
            elif status == "cancelled":
                events.append("cancel_requested")
            elif status == "conflict":
                events.append("conflict_detected")
            elif status == "inflight":
                events.extend(("dispatch_started", "retry_scheduled"))
            for sequence, event_type in enumerate(events, 1):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO sync_operation_events (
                        event_id, operation_id, event_sequence, event_type,
                        recorded_at, error_code, detail_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._legacy_event_id(row["operation_id"], event_type),
                        row["operation_id"], sequence, event_type, now,
                        "LEGACY_SNAPSHOT" if event_type in {
                            "conflict_detected", "retry_scheduled"
                        } else None,
                        canonical_json({
                            "legacy_status": status,
                            "source": "legacy_snapshot",
                        }),
                    ),
                )

        # Before 8006 the document queue already derived state from events,
        # but kept the legacy status column frozen at its insertion snapshot.
        # Materialize it once during upgrade so future divergence checks expose
        # new write-path omissions instead of reporting every historical row.
        if current_version < 8006:
            operation_ids = connection.execute(
                "SELECT operation_id FROM sync_operations ORDER BY queue_id"
            ).fetchall()
            for operation in operation_ids:
                try:
                    derived = self._derived_state(
                        connection, operation["operation_id"]
                    )
                except RuntimeError:
                    # A malformed history must remain visible to the explicit
                    # divergence audit; opening the whole store should not hide
                    # every unrelated project's queue.
                    continue
                connection.execute(
                    """
                    UPDATE sync_operations
                    SET status = ?, updated_at = ?
                    WHERE operation_id = ?
                    """,
                    (derived, now, operation["operation_id"]),
                )

        # Folder rename proof predates the append-only contract queues.  Preserve
        # its legacy snapshot once, then derive every subsequent status from the
        # local intent event stream instead of mutating the snapshot column.
        legacy_rename_intents = connection.execute(
            "SELECT * FROM sync_folder_rename_intents ORDER BY created_at, intent_id"
        ).fetchall()
        for row in legacy_rename_intents:
            detail = canonical_json({
                "old_path": row["old_path"],
                "new_path": row["new_path"],
                "source": "legacy_snapshot",
            })
            connection.execute(
                """
                INSERT OR IGNORE INTO sync_folder_rename_intent_events (
                    event_id, intent_id, event_sequence, event_type,
                    recorded_at, detail_json
                ) VALUES (?, ?, 1, 'recorded', ?, ?)
                """,
                (
                    self._legacy_folder_rename_event_id(
                        row["intent_id"], "recorded"
                    ),
                    row["intent_id"], row["created_at"], detail,
                ),
            )
            if row["status"] == "completed":
                connection.execute(
                    """
                    INSERT OR IGNORE INTO sync_folder_rename_intent_events (
                        event_id, intent_id, event_sequence, event_type,
                        recorded_at, detail_json
                    ) VALUES (?, ?, 2, 'completed', ?, ?)
                    """,
                    (
                        self._legacy_folder_rename_event_id(
                            row["intent_id"], "completed"
                        ),
                        row["intent_id"], row["updated_at"],
                        canonical_json({"source": "legacy_snapshot"}),
                    ),
                )

        connection.execute(
            """
            UPDATE sync_projects
            SET project_sync_mode = 'LEGACY', migration_epoch = 0,
                server_protocol_version = NULL, active_contract_sha256 = NULL,
                server_capabilities_json = NULL, contract_validated_at = NULL
            WHERE project_sync_mode IS NULL OR project_sync_mode = ''
            """
        )

        invalid_project = connection.execute(
            """
            SELECT local_key FROM sync_projects
            WHERE NOT (
                (project_sync_mode = 'LEGACY' AND migration_epoch = 0)
                OR
                (project_sync_mode IN ('MIGRATING', 'ID_BASED')
                    AND migration_epoch >= 1)
            )
            LIMIT 1
            """
        ).fetchone()
        if invalid_project is not None:
            raise RuntimeError("INVALID_PROJECT_MODE_EPOCH")

        connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS sync_projects_mode_epoch_insert
            BEFORE INSERT ON sync_projects
            WHEN NOT (
                (NEW.project_sync_mode = 'LEGACY' AND NEW.migration_epoch = 0)
                OR
                (NEW.project_sync_mode IN ('MIGRATING', 'ID_BASED')
                    AND NEW.migration_epoch >= 1)
            )
            BEGIN
                SELECT RAISE(ABORT, 'INVALID_PROJECT_MODE_EPOCH');
            END;

            CREATE TRIGGER IF NOT EXISTS sync_projects_mode_epoch_update
            BEFORE UPDATE OF project_sync_mode, migration_epoch ON sync_projects
            WHEN NOT (
                (NEW.project_sync_mode = 'LEGACY' AND NEW.migration_epoch = 0)
                OR
                (NEW.project_sync_mode IN ('MIGRATING', 'ID_BASED')
                    AND NEW.migration_epoch >= 1)
            )
            BEGIN
                SELECT RAISE(ABORT, 'INVALID_PROJECT_MODE_EPOCH');
            END;

            CREATE TRIGGER IF NOT EXISTS sync_projects_mode_epoch_transition
            BEFORE UPDATE OF project_sync_mode, migration_epoch ON sync_projects
            WHEN NOT (
                (
                    NEW.project_sync_mode = OLD.project_sync_mode
                    AND NEW.migration_epoch = OLD.migration_epoch
                )
                OR
                (
                    OLD.project_sync_mode = 'LEGACY'
                    AND OLD.migration_epoch = 0
                    AND NEW.project_sync_mode = 'MIGRATING'
                    AND NEW.migration_epoch = 1
                )
                OR
                (
                    OLD.project_sync_mode = 'MIGRATING'
                    AND OLD.migration_epoch >= 1
                    AND NEW.project_sync_mode = 'ID_BASED'
                    AND NEW.migration_epoch = OLD.migration_epoch
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'INVALID_PROJECT_MODE_TRANSITION');
            END;

            CREATE TRIGGER IF NOT EXISTS sync_operations_intent_immutable
            BEFORE UPDATE OF operation_id, local_key, project_id, document_id,
                local_path, relative_path, base_revision, base_content, content,
                is_deleted, entity_kind, intent_kind, provenance_kind, batch_id,
                batch_sequence, payload_sha256, supersedes_operation_id,
                sync_protocol_version, contract_version,
                canonical_contract_sha256, client_build_id,
                client_capabilities_json, created_at
            ON sync_operations
            BEGIN
                SELECT RAISE(ABORT, 'IMMUTABLE_OPERATION_INTENT');
            END;

            -- Permanently deleting a work is the one time these records may
            -- legitimately go. A row here opens the append-only guards, and
            -- purge_project_records puts it in and takes it out inside a
            -- single transaction, so a rollback leaves the guards closed.
            CREATE TABLE IF NOT EXISTS sync_purge_gate (
                purge_id TEXT PRIMARY KEY,
                opened_at TEXT NOT NULL
            );

            DROP TRIGGER IF EXISTS sync_folder_rename_intents_status_event_only;
            CREATE TRIGGER sync_folder_rename_intents_status_event_only
            BEFORE UPDATE OF status ON sync_folder_rename_intents
            WHEN NEW.status <> OLD.status
            BEGIN
                SELECT RAISE(ABORT, 'FOLDER_RENAME_STATUS_EVENT_ONLY');
            END;

            -- The delete guards are dropped and rebuilt rather than created
            -- if absent: a database made before the gate existed still has
            -- the older unconditional trigger under the same name, and
            -- CREATE ... IF NOT EXISTS would leave that one in place.
            DROP TRIGGER IF EXISTS sync_operations_no_delete;
            CREATE TRIGGER sync_operations_no_delete
            BEFORE DELETE ON sync_operations
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_OPERATION');
            END;

            CREATE TRIGGER IF NOT EXISTS sync_structure_operations_no_update
            BEFORE UPDATE ON sync_structure_operations
            BEGIN
                SELECT RAISE(ABORT, 'IMMUTABLE_OPERATION_INTENT');
            END;
            DROP TRIGGER IF EXISTS sync_structure_operations_no_delete;
            CREATE TRIGGER sync_structure_operations_no_delete
            BEFORE DELETE ON sync_structure_operations
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_OPERATION');
            END;
            CREATE TRIGGER IF NOT EXISTS sync_operation_events_no_update
            BEFORE UPDATE ON sync_operation_events
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_EVENT');
            END;
            DROP TRIGGER IF EXISTS sync_operation_events_no_delete;
            CREATE TRIGGER sync_operation_events_no_delete
            BEFORE DELETE ON sync_operation_events
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_EVENT');
            END;
            CREATE TRIGGER IF NOT EXISTS sync_folder_rename_intent_events_no_update
            BEFORE UPDATE ON sync_folder_rename_intent_events
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_EVENT');
            END;
            DROP TRIGGER IF EXISTS sync_folder_rename_intent_events_no_delete;
            CREATE TRIGGER sync_folder_rename_intent_events_no_delete
            BEFORE DELETE ON sync_folder_rename_intent_events
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_EVENT');
            END;
            CREATE TRIGGER IF NOT EXISTS sync_operation_attempts_no_update
            BEFORE UPDATE ON sync_operation_attempts
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_ATTEMPT');
            END;
            DROP TRIGGER IF EXISTS sync_operation_attempts_no_delete;
            CREATE TRIGGER sync_operation_attempts_no_delete
            BEFORE DELETE ON sync_operation_attempts
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_ATTEMPT');
            END;
            CREATE TRIGGER IF NOT EXISTS sync_contract_batches_no_update
            BEFORE UPDATE ON sync_contract_batches
            BEGIN
                SELECT RAISE(ABORT, 'IMMUTABLE_BATCH_METADATA');
            END;
            DROP TRIGGER IF EXISTS sync_contract_batches_no_delete;
            CREATE TRIGGER sync_contract_batches_no_delete
            BEFORE DELETE ON sync_contract_batches
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'IMMUTABLE_BATCH_METADATA');
            END;
            CREATE TRIGGER IF NOT EXISTS sync_contract_batch_results_no_update
            BEFORE UPDATE ON sync_contract_batch_results
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_BATCH_RESULT');
            END;
            DROP TRIGGER IF EXISTS sync_contract_batch_results_no_delete;
            CREATE TRIGGER sync_contract_batch_results_no_delete
            BEFORE DELETE ON sync_contract_batch_results
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_BATCH_RESULT');
            END;
            CREATE TRIGGER IF NOT EXISTS sync_structure_recovery_no_update
            BEFORE UPDATE ON sync_structure_recovery
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_RECOVERY');
            END;
            DROP TRIGGER IF EXISTS sync_structure_recovery_no_delete;
            CREATE TRIGGER sync_structure_recovery_no_delete
            BEFORE DELETE ON sync_structure_recovery
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'APPEND_ONLY_RECOVERY');
            END;
            """
        )
        connection.execute(f"PRAGMA user_version = {STAGE8_USER_VERSION}")
        self._recover_interrupted_dispatches(connection)

    @staticmethod
    def _event_state(event_type):
        try:
            return EVENT_STATE[event_type]
        except KeyError as exc:
            raise RuntimeError(f"unknown operation event: {event_type}") from exc

    def _events_for(self, connection, operation_id):
        return connection.execute(
            """
            SELECT * FROM sync_operation_events
            WHERE operation_id = ? ORDER BY event_sequence
            """,
            (operation_id,),
        ).fetchall()

    def _derived_state(self, connection, operation_id):
        events = self._events_for(connection, operation_id)
        if not events:
            raise RuntimeError("operation has no append-only event history")
        for expected, event in enumerate(events, 1):
            if event["event_sequence"] != expected:
                raise RuntimeError("operation event sequence is not contiguous")
        return self._event_state(events[-1]["event_type"])

    def _operation_row(self, connection, operation_id):
        row = connection.execute(
            "SELECT *, 'document' AS queue_kind FROM sync_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row:
            return row
        return connection.execute(
            """
            SELECT *, 'structure' AS queue_kind
            FROM sync_structure_operations WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()

    def _operation_dict(self, connection, row):
        if row is None:
            return None
        result = dict(row)
        operation_id = result["operation_id"]
        events = self._events_for(connection, operation_id)
        result["status"] = self._derived_state(connection, operation_id)
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM sync_operation_attempts WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()[0]
        result["attempts"] = int(result.get("legacy_attempt_count") or 0) + attempt_count
        latest_error = next(
            (event["error_code"] for event in reversed(events) if event["error_code"]),
            "",
        )
        result["last_error"] = latest_error or ""
        if result.get("queue_kind") == "structure":
            result["payload"] = json.loads(result.pop("payload_json"))
            result["sequence"] = result.get("batch_sequence")
        return result

    def _append_event(
        self,
        connection,
        operation_id,
        event_type,
        *,
        event_id=None,
        error_code=None,
        related_operation_id=None,
        detail=None,
    ):
        row = self._operation_row(connection, operation_id)
        if row is None:
            raise KeyError(operation_id)
        event_id = str(uuid.UUID(str(event_id or uuid.uuid4())))
        detail_json = canonical_json(detail or {})
        existing = connection.execute(
            "SELECT * FROM sync_operation_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing:
            same = (
                existing["operation_id"] == operation_id
                and existing["event_type"] == event_type
                and existing["error_code"] == error_code
                and existing["related_operation_id"] == related_operation_id
                and existing["detail_json"] == detail_json
            )
            if not same:
                raise SyncContractError("EVENT_ID_REUSED")
            return dict(existing)

        events = self._events_for(connection, operation_id)
        if events:
            current_state = self._event_state(events[-1]["event_type"])
            if current_state in TERMINAL_STATES:
                raise SyncContractError("OPERATION_TERMINAL")
            sequence = events[-1]["event_sequence"] + 1
        else:
            sequence = 1
        if event_type not in EVENT_STATE:
            raise SyncContractError("INVALID_ARGUMENT")
        recorded_at = _utc_now()
        connection.execute(
            """
            INSERT INTO sync_operation_events (
                event_id, operation_id, event_sequence, event_type, recorded_at,
                error_code, related_operation_id, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, operation_id, sequence, event_type, recorded_at,
                error_code, related_operation_id, detail_json,
            ),
        )
        # `status` is a queryable projection for diagnostics and old callers;
        # the append-only event remains the source of truth.  Keeping both in
        # this transaction lets operation_state_divergences() identify any
        # future path that changes only one side.
        connection.execute(
            """
            UPDATE sync_operations
            SET status = ?, updated_at = ?
            WHERE operation_id = ?
            """,
            (self._event_state(event_type), recorded_at, operation_id),
        )
        return dict(connection.execute(
            "SELECT * FROM sync_operation_events WHERE event_id = ?", (event_id,)
        ).fetchone())

    def _dispatch_info(self, connection, operation_id):
        event = connection.execute(
            """
            SELECT * FROM sync_operation_events
            WHERE operation_id = ? AND event_type = 'dispatch_started'
            ORDER BY event_sequence DESC LIMIT 1
            """,
            (operation_id,),
        ).fetchone()
        if not event:
            return None
        detail = json.loads(event["detail_json"] or "{}")
        return {
            "attempt_number": int(detail.get("attempt_number") or 1),
            "started_at": event["recorded_at"],
            "request_sha256": detail.get("request_sha256"),
            "rpc_name": detail.get("rpc_name") or "commit_document",
        }

    def _finish_attempt(
        self,
        connection,
        operation_id,
        outcome,
        *,
        response_sha256=None,
        http_status=None,
        error_code=None,
        error_detail=None,
        result_revision=None,
    ):
        dispatch = self._dispatch_info(connection, operation_id)
        if dispatch is None:
            return None
        existing = connection.execute(
            """
            SELECT * FROM sync_operation_attempts
            WHERE operation_id = ? AND attempt_number = ?
            """,
            (operation_id, dispatch["attempt_number"]),
        ).fetchone()
        if existing:
            return dict(existing)
        attempt_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO sync_operation_attempts (
                attempt_id, operation_id, attempt_number, started_at, finished_at,
                rpc_name, outcome, request_sha256, response_sha256, http_status,
                error_code, error_detail_json, result_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id, operation_id, dispatch["attempt_number"],
                dispatch["started_at"], _utc_now(), dispatch["rpc_name"], outcome,
                dispatch["request_sha256"], response_sha256, http_status,
                error_code, canonical_json(error_detail or {}), result_revision,
            ),
        )
        return dict(connection.execute(
            "SELECT * FROM sync_operation_attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone())

    def _recover_interrupted_dispatches(self, connection):
        rows = connection.execute(
            """
            SELECT operation_id FROM sync_operations
            UNION ALL
            SELECT operation_id FROM sync_structure_operations
            """
        ).fetchall()
        for row in rows:
            operation_id = row["operation_id"]
            try:
                state = self._derived_state(connection, operation_id)
            except RuntimeError:
                # Leave corrupt/missing histories untouched for the explicit
                # divergence audit instead of making the entire store fail open.
                continue
            if state != "inflight":
                continue
            self._finish_attempt(
                connection, operation_id, "transport_unknown",
                error_code="CLIENT_RESTART_RECOVERY",
            )
            self._append_event(
                connection, operation_id, "retry_scheduled",
                error_code="CLIENT_RESTART_RECOVERY",
                detail={"source": "client_restart"},
            )
            project_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(sync_projects)"
                ).fetchall()
            }
            if "server_name" not in project_columns:
                connection.execute(
                    "ALTER TABLE sync_projects "
                    "ADD COLUMN server_name TEXT NOT NULL DEFAULT ''"
                )
            if "server_state" not in project_columns:
                connection.execute(
                    "ALTER TABLE sync_projects "
                    "ADD COLUMN server_state TEXT NOT NULL DEFAULT 'active'"
                )
            if "server_state_updated_at" not in project_columns:
                connection.execute(
                    "ALTER TABLE sync_projects "
                    "ADD COLUMN server_state_updated_at TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                UPDATE sync_projects
                SET server_name = project_name
                WHERE server_name = ''
                """
            )

    def operation_events(self, operation_id):
        with self._reader() as connection:
            return [dict(row) for row in self._events_for(connection, operation_id)]

    def operation_state_divergences(self, local_key=None):
        """Return document operations whose status projection disagrees.

        Events remain authoritative.  The stored status is deliberately only a
        projection so this audit can expose a durable write path that changed a
        row without appending its corresponding event (or vice versa).
        Structure operations have no duplicate status column and therefore no
        second state to compare.
        """
        params = []
        where = ""
        if local_key:
            where = "WHERE local_key = ?"
            params.append(local_key)
        with self._reader() as connection:
            rows = connection.execute(
                f"""
                SELECT operation_id, status FROM sync_operations
                {where} ORDER BY queue_id
                """,
                params,
            ).fetchall()
            divergences = []
            for row in rows:
                try:
                    derived = self._derived_state(
                        connection, row["operation_id"]
                    )
                except RuntimeError:
                    derived = None
                if derived != row["status"]:
                    divergences.append({
                        "operation_id": row["operation_id"],
                        "stored_status": row["status"],
                        "derived_status": derived,
                    })
            return divergences

    def operation_attempts(self, operation_id):
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sync_operation_attempts
                WHERE operation_id = ? ORDER BY attempt_number
                """,
                (operation_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def local_key_for(writing_root_path):
        """The durable key for one writing root. An empty root has none.

        ``abspath("")`` is the current working directory, which for a packaged
        build is the folder the executable sits in. Reading a missing writing
        root as "here" registered the application's own directory as a project
        and left the sync layer believing it was configured.
        """
        if not str(writing_root_path or "").strip():
            raise ValueError(
                "집필 루트가 없는 프로젝트는 동기화 대상이 아닙니다."
            )
        return os.path.normcase(os.path.abspath(writing_root_path))

    @staticmethod
    def _identity_uuid(local_key, local_path, kind):
        """Return the project's own UUID for a path, or None when it has none.

        ``local_key`` is the normcased writing root, so the identity file sits
        one level above it. Internal sync documents (``__antigravity__/*``) and
        test roots without an identity file legitimately return None and keep
        the previous generated-UUID behaviour.
        """
        return identity_uuid_for_writing_path(local_key, local_path, kind)

    def configure_project(self, writing_root_path, project_name, project_id=None):
        local_key = self.local_key_for(writing_root_path)
        now = _utc_now()
        if project_id:
            project_id = str(uuid.UUID(str(project_id)))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sync_projects WHERE local_key = ?", (local_key,)
            ).fetchone()
            if row is None:
                project_id = project_id or str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO sync_projects
                        (
                            local_key, project_id, project_name, server_name,
                            created_at, updated_at
                        )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        local_key, project_id, project_name, project_name,
                        now, now,
                    ),
                )
            else:
                if project_id and project_id != row["project_id"]:
                    raise ValueError("이 로컬 프로젝트는 이미 다른 Supabase project_id에 연결되어 있습니다.")
                project_id = row["project_id"]
                connection.execute(
                    "UPDATE sync_projects SET project_name = ?, updated_at = ? WHERE local_key = ?",
                    (project_name, now, local_key),
                )
        project = self.get_project(local_key)
        return {
            "local_key": local_key,
            "project_id": project_id,
            "project_name": project_name,
            "project_sync_mode": row["project_sync_mode"] if row else "LEGACY",
            "migration_epoch": int(row["migration_epoch"] or 0) if row else 0,
            "server_name": project.get("server_name") or project_name,
            "server_state": project.get("server_state") or "active",
        }

    def get_project(self, local_key):
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_projects WHERE local_key = ?", (local_key,)
            ).fetchone()
            return dict(row) if row else None

    def set_contract_path_enabled(self, local_key, enabled):
        """Record the local decision to use the contract write path.

        Storing server state and opening this gate are separate acts on
        purpose. A handshake can be replayed by the server turning an allowlist
        row on; this row only changes when somebody asks for it here.
        """
        enabled = bool(enabled)
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT local_key FROM sync_projects WHERE local_key = ?",
                (local_key,),
            ).fetchone()
            if row is None:
                raise KeyError(local_key)
            connection.execute(
                """
                UPDATE sync_projects
                SET contract_path_enabled = ?, contract_path_enabled_at = ?,
                    updated_at = ?
                WHERE local_key = ?
                """,
                (1 if enabled else 0, now if enabled else None, now, local_key),
            )
            return dict(connection.execute(
                "SELECT * FROM sync_projects WHERE local_key = ?", (local_key,)
            ).fetchone())

    def contract_path_enabled(self, local_key):
        with self._reader() as connection:
            row = connection.execute(
                "SELECT contract_path_enabled FROM sync_projects WHERE local_key = ?",
                (local_key,),
            ).fetchone()
            return bool(row and row["contract_path_enabled"])

    def activate_contract_project(
        self,
        local_key,
        *,
        project_sync_mode,
        migration_epoch,
        server_protocol_version,
        server_contract_sha256,
        server_capabilities,
    ):
        """Persist a server-proven project transition; never auto-promote."""
        require_server_compatibility(
            project_sync_mode=project_sync_mode,
            migration_epoch=migration_epoch,
            server_protocol_version=server_protocol_version,
            server_contract_sha256=server_contract_sha256,
            server_capabilities=server_capabilities,
        )
        now = _utc_now()
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM sync_projects WHERE local_key = ?", (local_key,)
            ).fetchone()
            if current is None:
                raise KeyError(local_key)
            old_mode = current["project_sync_mode"] or "LEGACY"
            old_epoch = int(current["migration_epoch"] or 0)
            allowed = {
                ("LEGACY", "LEGACY"),
                ("LEGACY", "MIGRATING"),
                ("MIGRATING", "MIGRATING"),
                ("MIGRATING", "ID_BASED"),
                ("ID_BASED", "ID_BASED"),
            }
            if (old_mode, project_sync_mode) not in allowed:
                raise SyncContractError("INVALID_PROJECT_MODE_TRANSITION")
            new_epoch = int(migration_epoch)
            expected_epoch = old_epoch
            if old_mode == "LEGACY" and project_sync_mode == "MIGRATING":
                expected_epoch = old_epoch + 1
            if new_epoch != expected_epoch:
                raise SyncContractError("STALE_MIGRATION_EPOCH")
            connection.execute(
                """
                UPDATE sync_projects
                SET project_sync_mode = ?, migration_epoch = ?,
                    server_protocol_version = ?, active_contract_sha256 = ?,
                    server_capabilities_json = ?, contract_validated_at = ?,
                    updated_at = ?
                WHERE local_key = ?
                """,
                (
                    project_sync_mode, new_epoch,
                    int(server_protocol_version), server_contract_sha256,
                    canonical_json(sorted(set(server_capabilities))), now, now,
                    local_key,
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM sync_projects WHERE local_key = ?", (local_key,)
            ).fetchone())
    def get_project_by_id(self, project_id):
        try:
            project_id = str(uuid.UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError):
            return None
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_projects(self):
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_projects ORDER BY project_name, project_id"
            ).fetchall()
            return [dict(row) for row in rows]

    def remove_project_binding(self, project_id):
        try:
            project_id = str(uuid.UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError):
            return False
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM sync_projects WHERE project_id = ?",
                (project_id,),
            )
            return cursor.rowcount > 0

    def purge_project_records(self, project_id):
        """작품이 영구 삭제됐을 때 그 지역 기록을 남김없이 지웁니다.

        영구 삭제는 이 id 들이 정당하게 사라지는 유일한 경우다. 서버 행이
        없어졌으니 어떤 operation id 도 다시 재생될 수 없고 시도 기록을
        되물을 상대도 없다. 그 외의 모든 경로에서는 append-only 방벽이
        그대로 닫혀 있다.

        시도·사건 기록은 operation_id 를 들고 있지만 외래키가 없어 연쇄로
        따라 지워지지 않는다. 그대로 두면 고아로 남으므로 여기서 id 로
        직접 지운다.
        """
        try:
            project_id = str(uuid.UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError):
            return False
        purge_id = str(uuid.uuid4())
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT local_key FROM sync_projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                return False
            local_key = row["local_key"]
            operation_ids = [
                item["operation_id"]
                for item in connection.execute(
                    """
                    SELECT operation_id FROM sync_operations
                    WHERE local_key = ?
                    UNION
                    SELECT operation_id FROM sync_structure_operations
                    WHERE local_key = ?
                    """,
                    (local_key, local_key),
                ).fetchall()
            ]
            connection.execute(
                "INSERT INTO sync_purge_gate (purge_id, opened_at)"
                " VALUES (?, ?)",
                (purge_id, _utc_now()),
            )
            try:
                for operation_id in operation_ids:
                    connection.execute(
                        "DELETE FROM sync_operation_events"
                        " WHERE operation_id = ?",
                        (operation_id,),
                    )
                    connection.execute(
                        "DELETE FROM sync_operation_attempts"
                        " WHERE operation_id = ?",
                        (operation_id,),
                    )
                # Results point at their batch with no delete rule, so they
                # have to go before the batches they hold down.
                connection.execute(
                    """
                    DELETE FROM sync_contract_batch_results
                    WHERE batch_id IN (
                        SELECT batch_id FROM sync_contract_batches
                        WHERE local_key = ?
                    )
                    """,
                    (local_key,),
                )
                connection.execute(
                    "DELETE FROM sync_contract_diagnostics WHERE local_key = ?",
                    (local_key,),
                )
                # Everything else hangs off the project row by local_key and
                # goes with it.
                connection.execute(
                    "DELETE FROM sync_projects WHERE project_id = ?",
                    (project_id,),
                )
            finally:
                connection.execute(
                    "DELETE FROM sync_purge_gate WHERE purge_id = ?",
                    (purge_id,),
                )
            return True

    def set_project_server_state(self, project_id, state):
        if state not in {"active", "trashed", "purged"}:
            raise ValueError("유효하지 않은 서버 작품 상태입니다.")
        try:
            project_id = str(uuid.UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError):
            return None
        now = _utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE sync_projects
                SET server_state = ?, server_state_updated_at = ?,
                    updated_at = ?
                WHERE project_id = ?
                """,
                (state, now, now, project_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM sync_projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return dict(row)

    def has_server_acknowledged_commit(self, local_key):
        """서버가 이 작품의 커밋을 받아준 적이 있는지 알려줍니다.

        0 보다 큰 revision 은 서버 응답으로만 생긴다. 그래서 이 값이
        아직 서버에 올린 적 없는 작품과, 올렸는데 사라진 작품을 가른다.
        """
        with self._reader() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM sync_documents
                WHERE local_key = ? AND revision > 0
                UNION ALL
                SELECT 1 FROM sync_folders
                WHERE local_key = ? AND revision > 0
                UNION ALL
                SELECT 1 FROM sync_tree_orders
                WHERE local_key = ? AND revision > 0
                LIMIT 1
                """,
                (local_key, local_key, local_key),
            ).fetchone()
            return row is not None

    def begin_project_import(
        self,
        writing_root_path,
        project_name,
        project_id,
        server_name=None,
        reset_complete=False,
    ):
        local_key = self.local_key_for(writing_root_path)
        project_id = str(uuid.UUID(str(project_id)))
        server_name = str(server_name or project_name)
        now = _utc_now()
        with self._transaction() as connection:
            by_id = connection.execute(
                "SELECT * FROM sync_projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            by_path = connection.execute(
                "SELECT * FROM sync_projects WHERE local_key = ?", (local_key,)
            ).fetchone()
            if by_id is not None and by_id["local_key"] != local_key:
                raise ValueError(
                    "이 Supabase project_id는 이미 다른 로컬 프로젝트에 연결되어 있습니다."
                )
            if by_path is not None and by_path["project_id"] != project_id:
                raise ValueError(
                    "이 로컬 프로젝트는 이미 다른 Supabase project_id에 연결되어 있습니다."
                )
            if by_id is None:
                connection.execute(
                    """
                    INSERT INTO sync_projects
                        (
                            local_key, project_id, project_name, server_name,
                            created_at, updated_at
                        )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        local_key, project_id, project_name, server_name,
                        now, now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE sync_projects
                    SET project_name = ?, updated_at = ?
                    WHERE project_id = ?
                    """,
                    (project_name, now, project_id),
                )

            journal = connection.execute(
                "SELECT * FROM sync_project_imports WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            previous_state = journal["state"] if journal else None
            if journal is None:
                connection.execute(
                    """
                    INSERT INTO sync_project_imports (
                        project_id, local_key, project_name, state,
                        last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, 'preparing', '', ?, ?)
                    """,
                    (project_id, local_key, project_name, now, now),
                )
            elif journal["local_key"] != local_key:
                raise ValueError(
                    "이 Supabase project_id의 가져오기 위치가 기존 기록과 다릅니다."
                )
            elif journal["state"] != "complete" or reset_complete:
                connection.execute(
                    """
                    UPDATE sync_project_imports
                    SET project_name = ?, state = 'preparing',
                        last_error = '', updated_at = ?
                    WHERE project_id = ?
                    """,
                    (project_name, now, project_id),
                )

            current = connection.execute(
                "SELECT * FROM sync_project_imports WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return {
            "local_key": local_key,
            "project_id": project_id,
            "project_name": project_name,
            "server_name": server_name,
            "previous_state": previous_state,
            "import_state": current["state"],
        }

    def get_project_import(self, project_id):
        try:
            project_id = str(uuid.UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError):
            return None
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_project_imports WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return dict(row) if row else None

    def set_project_import_state(self, project_id, state, error_message=""):
        if state not in {"preparing", "pulling", "failed", "complete"}:
            raise ValueError("유효하지 않은 가져오기 상태입니다.")
        project_id = str(uuid.UUID(str(project_id)))
        now = _utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE sync_project_imports
                SET state = ?, last_error = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (state, str(error_message or ""), now, project_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("가져오기 기록을 찾을 수 없습니다.")
            row = connection.execute(
                "SELECT * FROM sync_project_imports WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return dict(row)

    def ensure_document(self, local_key, local_path, content="", document_id=None):
        local_path = _normalize_path(local_path)
        now = _utc_now()
        if document_id:
            document_id = str(uuid.UUID(str(document_id)))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sync_documents WHERE local_key = ? AND local_path = ?",
                (local_key, local_path),
            ).fetchone()
            if row is None:
                # The local identity file is the UUID of record. Minting a
                # second id here is what let one manuscript carry two UUIDs,
                # one in identity-v1.json and one on the server.
                document_id = (
                    document_id
                    or self._identity_uuid(local_key, local_path, KIND_DOCUMENT)
                    or str(uuid.uuid4())
                )
                base_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else ""
                connection.execute(
                    """
                    INSERT INTO sync_documents (
                        document_id, local_key, local_path, server_path, revision,
                        base_content, base_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (document_id, local_key, local_path, local_path, content, base_hash, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM sync_documents WHERE document_id = ?", (document_id,)
                ).fetchone()
            return dict(row)

    def get_document(self, local_key, local_path):
        local_path = _normalize_path(local_path)
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_documents WHERE local_key = ? AND local_path = ?",
                (local_key, local_path),
            ).fetchone()
            return dict(row) if row else None

    def get_document_by_id(self, document_id):
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_documents(self, local_key):
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_documents WHERE local_key = ? ORDER BY document_id",
                (local_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_folder_by_id(self, folder_id):
        try:
            folder_id = str(uuid.UUID(str(folder_id)))
        except (AttributeError, TypeError, ValueError):
            return None
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_folders WHERE folder_id = ?", (folder_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_folder_by_path(self, local_key, local_path):
        local_path = _normalize_path(local_path)
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_folders "
                "WHERE local_key = ? AND local_path = ? AND is_deleted = 0",
                (local_key, local_path),
            ).fetchone()
            return dict(row) if row else None

    def ensure_local_folder(
        self, local_key, local_path, *, folder_id=None, parent_folder_id=None
    ):
        """Create only local revision-zero identity for an unsent folder create."""
        local_path = _normalize_path(local_path)
        if not local_path:
            raise SyncContractError("INVALID_FOLDER_SNAPSHOT")
        name = local_path.rsplit("/", 1)[-1]
        storage_name_key = normalize_storage_name(name).normalized
        folder_id = str(uuid.UUID(str(
            folder_id
            or self._identity_uuid(local_key, local_path, KIND_FOLDER)
            or uuid.uuid4()
        )))
        if parent_folder_id is not None:
            parent_folder_id = str(uuid.UUID(str(parent_folder_id)))
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM sync_folders WHERE local_key = ? AND local_path = ?",
                (local_key, local_path),
            ).fetchone()
            if existing:
                return dict(existing)
            connection.execute(
                """
                INSERT INTO sync_folders (
                    folder_id, local_key, parent_folder_id, local_path,
                    name, storage_name_key, revision, is_deleted,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    folder_id, local_key, parent_folder_id, local_path,
                    name, storage_name_key, now, now,
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM sync_folders WHERE folder_id = ?", (folder_id,)
            ).fetchone())

    def move_folder_paths(self, local_key, old_path, new_path):
        old_path = _normalize_path(old_path)
        new_path = _normalize_path(new_path)
        now = _utc_now()
        moved = []
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sync_folders
                WHERE local_key = ? AND (local_path = ? OR local_path LIKE ?)
                ORDER BY length(local_path)
                """,
                (local_key, old_path, old_path + "/%"),
            ).fetchall()
            for row in rows:
                suffix = row["local_path"][len(old_path):]
                updated_path = new_path + suffix
                updated_name = updated_path.rsplit("/", 1)[-1]
                connection.execute(
                    """
                    UPDATE sync_folders
                    SET local_path = ?, name = ?, storage_name_key = ?, updated_at = ?
                    WHERE folder_id = ?
                    """,
                    (
                        updated_path, updated_name,
                        normalize_storage_name(updated_name).normalized,
                        now, row["folder_id"],
                    ),
                )
                moved.append({
                    **dict(row), "old_local_path": row["local_path"],
                    "local_path": updated_path, "name": updated_name,
                })
        return moved

    def get_tree_order(self, local_key, parent_path):
        parent_path = _normalize_path(parent_path) or "<root>"
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_tree_orders "
                "WHERE local_key = ? AND parent_path = ?",
                (local_key, parent_path),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["children"] = json.loads(result.pop("children_json") or "[]")
            return result

    def replace_tree_order_snapshots(self, local_key, snapshots):
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM sync_tree_orders WHERE local_key = ?", (local_key,)
            )
            for snapshot in snapshots or []:
                tree_order_id = str(uuid.UUID(str(snapshot["tree_order_id"])))
                parent_folder_id = snapshot.get("parent_folder_id")
                parent_folder_id = (
                    str(uuid.UUID(str(parent_folder_id)))
                    if parent_folder_id else None
                )
                parent_path = "<root>"
                if parent_folder_id:
                    parent = connection.execute(
                        "SELECT local_path FROM sync_folders WHERE folder_id = ?",
                        (parent_folder_id,),
                    ).fetchone()
                    if parent is None:
                        raise SyncContractError("TREE_REFERENCE_NOT_FOUND")
                    parent_path = parent["local_path"]
                children = [
                    str(uuid.UUID(str(value)))
                    for value in (snapshot.get("children") or [])
                ]
                revision = int(snapshot.get("revision") or 0)
                if revision < 1:
                    raise SyncContractError("REVISION_CONFLICT")
                connection.execute(
                    """
                    INSERT INTO sync_tree_orders (
                        tree_order_id, local_key, parent_folder_id, parent_path,
                        children_json, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tree_order_id, local_key, parent_folder_id, parent_path,
                        canonical_json(children), revision, now, now,
                    ),
                )

    def move_tree_order_paths(self, local_key, old_path, new_path):
        old_path = _normalize_path(old_path)
        new_path = _normalize_path(new_path)
        now = _utc_now()
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT tree_order_id, parent_path FROM sync_tree_orders
                WHERE local_key = ?
                  AND (parent_path = ? OR parent_path LIKE ?)
                ORDER BY length(parent_path)
                """,
                (local_key, old_path, old_path + "/%"),
            ).fetchall()
            for row in rows:
                suffix = row["parent_path"][len(old_path):]
                connection.execute(
                    """
                    UPDATE sync_tree_orders
                    SET parent_path = ?, updated_at = ?
                    WHERE tree_order_id = ?
                    """,
                    (new_path + suffix, now, row["tree_order_id"]),
                )

    def list_folders(self, local_key):
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_folders WHERE local_key = ? ORDER BY folder_id",
                (local_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def replace_folder_snapshots(self, local_key, snapshots):
        """Apply the server-proven folder ID projection without inventing IDs.

        A folder the projection no longer lists is one the server has
        tombstoned. Dropping its row took the revision with it, and the
        revision is what a later restore has to name to say what it is
        restoring from — a non-create intent with no revision is invalid by the
        contract's own rule. So the row is kept and marked deleted instead.

        A retired row keeps the path it was last seen at, because that is what
        says where the folder was. Only when the incoming projection claims
        that exact path does the row move aside — ``UNIQUE(local_key,
        local_path)`` allows just one holder — and then to a path no live
        folder can ever take. Nothing addresses a folder by that marker:
        ``get_folder_by_path`` reads live rows only and the storage-name index
        excludes deleted ones. The iPad projection keeps tombstones the same
        way, per folder id.
        """
        normalized = []
        for snapshot in snapshots or []:
            folder_id = str(uuid.UUID(str(snapshot["folder_id"])))
            parent_id = snapshot.get("parent_folder_id")
            parent_id = str(uuid.UUID(str(parent_id))) if parent_id else None
            local_path = _normalize_path(snapshot.get("local_path"))
            name = str(snapshot.get("name") or "")
            revision = int(snapshot.get("revision") or 0)
            if not local_path or not name or revision < 1:
                raise SyncContractError("INVALID_FOLDER_SNAPSHOT")
            storage_name_key = normalize_storage_name(name).normalized
            normalized.append((
                folder_id, parent_id, local_path, name, storage_name_key, revision,
                int(bool(snapshot.get("is_deleted"))),
            ))
        if len({item[0] for item in normalized}) != len(normalized):
            raise SyncContractError("FOLDER_IDENTITY_CONFLICT")
        if len({item[2] for item in normalized}) != len(normalized):
            raise SyncContractError("FOLDER_PATH_IDENTITY_CONFLICT")

        now = _utc_now()
        with self._transaction() as connection:
            existing = {
                row["folder_id"]: row["local_key"]
                for row in connection.execute(
                    "SELECT folder_id, local_key FROM sync_folders"
                ).fetchall()
            }
            if any(
                folder_id in existing and existing[folder_id] != local_key
                for folder_id, *_ in normalized
            ):
                raise SyncContractError("FOLDER_PROJECT_IDENTITY_CONFLICT")
            folder_ids = {item[0] for item in normalized}
            # Retire what the projection dropped, keeping its id, revision and
            # the path it was last known at. A restore commits against that
            # revision, and the contract planner reads that path.
            retire = (
                "UPDATE sync_folders SET is_deleted = 1, updated_at = ? "
                "WHERE local_key = ? AND is_deleted = 0"
            )
            if folder_ids:
                placeholders = ",".join("?" for _ in folder_ids)
                connection.execute(
                    f"{retire} AND folder_id NOT IN ({placeholders})",
                    (now, local_key, *sorted(folder_ids)),
                )
            else:
                connection.execute(retire, (now, local_key))

            # Only a retired row standing on a path the incoming projection
            # claims has to move, and it moves to a path no live folder can
            # hold. Everything else keeps where it was.
            incoming_paths = sorted({item[2] for item in normalized})
            if incoming_paths:
                connection.execute(
                    "UPDATE sync_folders "
                    f"SET local_path = '{FOLDER_TOMBSTONE_PREFIX}/' || folder_id, "
                    "updated_at = ? "
                    "WHERE local_key = ? AND is_deleted = 1 "
                    f"AND local_path IN ({','.join('?' for _ in incoming_paths)})",
                    (now, local_key, *incoming_paths),
                )
                # Vacate the paths the incoming rows are about to take.
                for folder_id in sorted(folder_ids):
                    connection.execute(
                        "UPDATE sync_folders SET local_path = ? "
                        "WHERE folder_id = ? AND local_key = ?",
                        (f"__folder_transition__/{folder_id}", folder_id, local_key),
                    )
            for item in normalized:
                (
                    folder_id, parent_id, local_path, name, storage_name_key,
                    revision, is_deleted,
                ) = item
                connection.execute(
                    """
                    INSERT INTO sync_folders (
                        folder_id, local_key, parent_folder_id, local_path,
                        name, storage_name_key, revision, is_deleted,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(folder_id) DO UPDATE SET
                        local_key = excluded.local_key,
                        parent_folder_id = excluded.parent_folder_id,
                        local_path = excluded.local_path,
                        name = excluded.name,
                        storage_name_key = excluded.storage_name_key,
                        revision = excluded.revision,
                        is_deleted = excluded.is_deleted,
                        updated_at = excluded.updated_at
                    """,
                    (
                        folder_id, local_key, parent_id, local_path, name,
                        storage_name_key, revision, is_deleted, now, now,
                    ),
                )
        return self.list_folders(local_key)

    def _has_active_connection(self, connection, document_id):
        rows = connection.execute(
            "SELECT operation_id FROM sync_operations WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        return any(
            self._derived_state(connection, row["operation_id"]) in CONTRACT_ACTIVE_STATES
            for row in rows
        )

    def has_active_operations(self, document_id):
        with self._reader() as connection:
            return self._has_active_connection(connection, document_id)

    def active_document_server_paths(self, local_key):
        """Return server paths whose document work has not reached a terminal state.

        Folder tombstones are a separate RPC from document tombstones.  A tree
        snapshot queued for an earlier local edit must not retire a folder while
        one of its descendants is still being deleted or restored.  The server
        path is stable while the local copy moves through trash, so it is the
        only path suitable for that ordering decision.
        """
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT o.operation_id, d.server_path
                FROM sync_operations AS o
                JOIN sync_documents AS d ON d.document_id = o.document_id
                WHERE o.local_key = ? AND d.server_path IS NOT NULL
                """,
                (local_key,),
            ).fetchall()
            return sorted({
                row["server_path"]
                for row in rows
                if row["server_path"]
                and self._derived_state(connection, row["operation_id"])
                in CONTRACT_ACTIVE_STATES
            })

    def active_local_document_creates(self, local_key):
        """Return distinct local creates that the server has not accepted yet."""
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT queue_id, operation_id, document_id, local_path,
                       relative_path, base_revision, is_deleted, intent_kind
                FROM sync_operations
                WHERE local_key = ?
                  AND entity_kind = 'document'
                  AND intent_kind = 'create'
                  AND is_deleted = 0
                  AND COALESCE(base_revision, 0) = 0
                ORDER BY queue_id
                """,
                (local_key,),
            ).fetchall()
            active = []
            seen = set()
            for row in rows:
                if (
                    row["document_id"] in seen
                    or self._derived_state(connection, row["operation_id"])
                    not in CONTRACT_ACTIVE_STATES
                ):
                    continue
                seen.add(row["document_id"])
                active.append(dict(row))
            return active

    def has_nonempty_active_content(self, document_id):
        """Return whether the newest queued live snapshot contains text."""
        with self._reader() as connection:
            row = connection.execute(
                """
                SELECT content, is_deleted
                FROM sync_operations
                WHERE document_id = ?
                  AND status IN ('pending', 'inflight')
                ORDER BY queue_id DESC
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
            return bool(
                row
                and not row["is_deleted"]
                and row["content"]
            )

    def has_tombstone_for_server_path(self, local_key, server_path):
        """Return whether a path is deleted or has a queued deletion."""
        server_path = _normalize_path(server_path)
        with self._reader() as connection:
            documents = connection.execute(
                """
                SELECT * FROM sync_documents
                WHERE local_key = ? AND server_path = ?
                """,
                (local_key, server_path),
            ).fetchall()
            for document in documents:
                if document["is_deleted"]:
                    return True
                operations = connection.execute(
                    """
                    SELECT * FROM sync_operations
                    WHERE document_id = ? AND is_deleted = 1
                    """,
                    (document["document_id"],),
                ).fetchall()
                if any(
                    self._derived_state(connection, operation["operation_id"])
                    in CONTRACT_ACTIVE_STATES
                    for operation in operations
                ):
                    return True
            return False

    def apply_remote_snapshot(
        self,
        context,
        document_id,
        remote_path,
        content,
        revision,
        is_deleted=False,
        local_path=None,
        parent_folder_id=None,
        name=None,
        structure_revision=None,
    ):
        """Record a newer clean server snapshot without disturbing queued local work."""
        document_id = str(uuid.UUID(str(document_id)))
        remote_path = _normalize_path(remote_path)
        local_path = _normalize_path(local_path or remote_path)
        content = content or ""
        revision = int(revision or 0)
        now = _utc_now()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if parent_folder_id:
            parent_folder_id = str(uuid.UUID(str(parent_folder_id)))
        name = str(name) if name is not None else None
        structure_revision = (
            int(structure_revision) if structure_revision is not None else None
        )
        storage_name_key = (
            normalize_storage_name(name).normalized if name is not None else None
        )

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if self._has_active_connection(connection, document_id):
                return {"applied": False, "reason": "active_operations"}
            if existing and revision <= existing["revision"]:
                return {"applied": False, "reason": "not_newer", "document": dict(existing)}

            collision = connection.execute(
                """
                SELECT document_id FROM sync_documents
                WHERE local_key = ? AND local_path = ? AND document_id <> ?
                """,
                (context["local_key"], local_path, document_id),
            ).fetchone()
            if collision:
                return {"applied": False, "reason": "path_conflict"}

            if existing is None:
                connection.execute(
                    """
                    INSERT INTO sync_documents (
                        document_id, local_key, local_path, server_path, revision,
                        base_content, base_hash, is_deleted, sync_state, last_error,
                        parent_folder_id, name, storage_name_key,
                        structure_revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'synced', '', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        context["local_key"],
                        local_path,
                        remote_path,
                        revision,
                        content,
                        content_hash,
                        int(bool(is_deleted)),
                        parent_folder_id,
                        name,
                        storage_name_key,
                        structure_revision,
                        now,
                        now,
                    ),
                )
                previous_path = None
            else:
                previous_path = existing["local_path"]
                connection.execute(
                    """
                    UPDATE sync_documents
                    SET local_path = ?, server_path = ?, revision = ?,
                        base_content = ?, base_hash = ?, is_deleted = ?,
                        sync_state = 'synced', last_error = '',
                        parent_folder_id = COALESCE(?, parent_folder_id),
                        name = COALESCE(?, name),
                        storage_name_key = COALESCE(?, storage_name_key),
                        structure_revision = COALESCE(?, structure_revision),
                        conflict_base = NULL, conflict_local = NULL,
                        conflict_remote = NULL, conflict_merged = NULL,
                        updated_at = ?
                    WHERE document_id = ?
                    """,
                    (
                        local_path,
                        remote_path,
                        revision,
                        content,
                        content_hash,
                        int(bool(is_deleted)),
                        parent_folder_id,
                        name,
                        storage_name_key,
                        structure_revision,
                        now,
                        document_id,
                    ),
                )

            document = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            return {
                "applied": True,
                "reason": "applied",
                "previous_path": previous_path,
                "document": dict(document),
            }

    def repair_clean_document_path(
        self, document_id, local_path, server_path=None
    ):
        """Canonicalize a clean document path without changing its revision."""
        document_id = str(uuid.UUID(str(document_id)))
        local_path = _normalize_path(local_path)
        server_path = _normalize_path(server_path or local_path)
        now = _utc_now()
        with self._transaction() as connection:
            document = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if document is None:
                return {"applied": False, "reason": "missing_document"}
            active = connection.execute(
                """
                SELECT 1 FROM sync_operations
                WHERE document_id = ?
                  AND status IN ('pending', 'inflight', 'conflict')
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
            if active:
                return {"applied": False, "reason": "active_operations"}
            collision = connection.execute(
                """
                SELECT document_id FROM sync_documents
                WHERE local_key = ? AND local_path = ? AND document_id <> ?
                LIMIT 1
                """,
                (document["local_key"], local_path, document_id),
            ).fetchone()
            if collision:
                return {"applied": False, "reason": "path_conflict"}
            previous_path = document["local_path"]
            connection.execute(
                """
                UPDATE sync_documents
                SET local_path = ?, server_path = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (local_path, server_path, now, document_id),
            )
            repaired = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            return {
                "applied": True,
                "reason": "canonical_path_repaired",
                "previous_path": previous_path,
                "document": dict(repaired),
            }

    def relocate_deleted_document(self, document_id, local_path):
        """Repair an already-synced tombstone that still occupies its live path."""
        document_id = str(uuid.UUID(str(document_id)))
        local_path = _normalize_path(local_path)
        now = _utc_now()
        with self._transaction() as connection:
            document = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if document is None or not document["is_deleted"]:
                return {"applied": False, "reason": "not_deleted"}
            if self._has_active_connection(connection, document_id):
                return {"applied": False, "reason": "active_operations"}
            collision = connection.execute(
                """
                SELECT 1 FROM sync_documents
                WHERE local_key = ? AND local_path = ? AND document_id <> ?
                LIMIT 1
                """,
                (document["local_key"], local_path, document_id),
            ).fetchone()
            if collision:
                return {"applied": False, "reason": "path_conflict"}
            previous_path = document["local_path"]
            connection.execute(
                "UPDATE sync_documents SET local_path = ?, updated_at = ? WHERE document_id = ?",
                (local_path, now, document_id),
            )
            repaired = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            return {
                "applied": True,
                "reason": "relocated",
                "previous_path": previous_path,
                "document": dict(repaired),
            }

    @staticmethod
    def _payload_for_document(local_path, relative_path, base_content, content, is_deleted):
        return {
            "base_content": base_content,
            "content": content,
            "is_deleted": bool(is_deleted),
            "local_path": local_path,
            "relative_path": relative_path,
        }

    @staticmethod
    def _stable_local_error(error_message):
        text = str(error_message or "")
        for token in text.replace(":", " ").split():
            if token and token.replace("_", "").isalnum() and token.upper() == token:
                return token[:80]
        lowered = text.lower()
        if any(marker in lowered for marker in (
            "network", "connection", "offline", "timeout", "서버 연결", "오프라인"
        )):
            return "NETWORK_ERROR"
        return "CLIENT_ERROR"

    def _document_contract_structure(
        self, connection, document, relative_path, base_revision
    ):
        """Resolve only server-proven folder identity for a document request."""
        relative_path = _normalize_path(relative_path)
        name = relative_path.rsplit("/", 1)[-1]
        parent_path = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
        parent_folder_id = None
        if parent_path:
            folder = connection.execute(
                """
                SELECT * FROM sync_folders
                WHERE local_key = ? AND local_path = ? AND is_deleted = 0
                """,
                (document["local_key"], parent_path),
            ).fetchone()
            if folder is None:
                raise SyncContractError("CONTRACT_STRUCTURE_IDS_REQUIRED")
            parent_folder_id = folder["folder_id"]
        structure_revision = document["structure_revision"]
        if structure_revision is None:
            if int(base_revision or 0) != 0:
                raise SyncContractError("CONTRACT_STRUCTURE_REVISION_REQUIRED")
            structure_revision = 1
        storage_name_key = normalize_storage_name(name).normalized
        folder_collision = connection.execute(
            """
            SELECT 1 FROM sync_folders
            WHERE local_key = ? AND parent_folder_id IS ?
              AND storage_name_key = ? AND is_deleted = 0
            LIMIT 1
            """,
            (document["local_key"], parent_folder_id, storage_name_key),
        ).fetchone()
        document_collision = connection.execute(
            """
            SELECT 1 FROM sync_documents
            WHERE local_key = ? AND parent_folder_id IS ?
              AND storage_name_key = ? AND is_deleted = 0
              AND document_id <> ?
            LIMIT 1
            """,
            (
                document["local_key"], parent_folder_id, storage_name_key,
                document["document_id"],
            ),
        ).fetchone()
        pending_collision = False
        pending_rows = connection.execute(
            """
            SELECT operation_id, document_id, relative_path, is_deleted
            FROM sync_operations
            WHERE local_key = ? AND document_id <> ?
            """,
            (document["local_key"], document["document_id"]),
        ).fetchall()
        for pending in pending_rows:
            if bool(pending["is_deleted"]):
                continue
            if self_state := self._derived_state(connection, pending["operation_id"]):
                if self_state not in CONTRACT_ACTIVE_STATES:
                    continue
            pending_path = _normalize_path(pending["relative_path"])
            pending_parent = (
                pending_path.rsplit("/", 1)[0] if "/" in pending_path else ""
            )
            if pending_parent != parent_path:
                continue
            pending_name = pending_path.rsplit("/", 1)[-1]
            if normalize_storage_name(pending_name).normalized == storage_name_key:
                pending_collision = True
                break
        if folder_collision or document_collision or pending_collision:
            raise SyncContractError("PATH_CONFLICT")
        return parent_folder_id, name, storage_name_key, int(structure_revision)

    def _insert_document_operation(
        self,
        connection,
        *,
        context,
        document,
        local_path,
        relative_path,
        base_revision,
        base_content,
        content,
        is_deleted,
        supersedes_operation_id=None,
    ):
        project = connection.execute(
            "SELECT * FROM sync_projects WHERE local_key = ?", (context["local_key"],)
        ).fetchone()
        if project is None:
            raise KeyError(context["local_key"])
        observed_mode = project["project_sync_mode"] or "LEGACY"
        # project_sync_mode is the server's to move, and a handshake records
        # whatever it reports. The gate is not the server's to move. So the
        # observed value stays on the project row untouched, as the last thing
        # the server said, and only the shape of this write is held back: a
        # branch that read the observed mode alone would emit a contract batch
        # for a path nobody opened here.
        effective_write_mode = (
            observed_mode if project["contract_path_enabled"] else "LEGACY"
        )
        payload = self._payload_for_document(
            local_path, relative_path, base_content, content, is_deleted
        )
        operation_id = str(uuid.uuid4())
        batch_id = None
        provenance = "LEGACY_EPOCH_0"
        protocol_version = 2
        contract_version = None
        contract_sha256 = None
        capabilities_json = canonical_json(["folders_authoritative", "tombstones"])
        intent_kind = (
            "delete" if is_deleted else
            "create" if int(base_revision or 0) == 0 else "update"
        )
        if effective_write_mode != "LEGACY" and base_revision is None:
            provenance = "LOCAL_DEFERRED"
            protocol_version = SYNC_PROTOCOL_VERSION
            capabilities_json = canonical_json(list(CLIENT_CAPABILITIES))
        elif effective_write_mode != "LEGACY":
            provenance = "CONTRACT_BATCH"
            protocol_version = SYNC_PROTOCOL_VERSION
            contract_version = CONTRACT_VERSION
            contract_sha256 = CANONICAL_CONTRACT_SHA256
            capabilities_json = canonical_json(list(CLIENT_CAPABILITIES))
            parent_folder_id, name, storage_name_key, structure_revision = (
                self._document_contract_structure(
                    connection, document, relative_path, base_revision
                )
            )
            if is_deleted:
                intent_kind = "delete"
            elif bool(document["is_deleted"]):
                intent_kind = "restore"
            elif int(base_revision or 0) == 0:
                intent_kind = "create"
            else:
                intent_kind = "update"
            request = build_document_commit_request(
                project_id=context["project_id"],
                project_sync_mode=effective_write_mode,
                migration_epoch=int(project["migration_epoch"] or 0),
                writer_device_id=context.get("writer_device_id") or uuid.uuid4(),
                document_id=document["document_id"],
                intent_kind=intent_kind,
                base_revision=int(base_revision or 0),
                parent_folder_id=parent_folder_id,
                name=name,
                content=content,
                is_deleted=bool(is_deleted),
                structure_revision=structure_revision,
                operation_id=operation_id,
                supersedes_operation_id=supersedes_operation_id,
                client_build_id=CLIENT_BUILD_ID,
            )
            payload = request["ordered_intents"][0]["payload"]
            batch = request["batch"]
            batch_id = batch["batch_id"]
            connection.execute(
                """
                INSERT INTO sync_contract_batches (
                    batch_id, local_key, project_id, writer_device_id,
                    client_build_id, sync_protocol_version, contract_version,
                    canonical_contract_sha256, client_capabilities_json,
                    batch_payload_sha256, project_sync_mode, migration_epoch,
                    request_json, request_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, context["local_key"], context["project_id"],
                    batch["writer_device_id"], batch["client_build_id"],
                    batch["sync_protocol_version"], batch["contract_version"],
                    batch["canonical_contract_sha256"],
                    canonical_json(batch["client_capabilities"]),
                    batch["batch_payload_sha256"], effective_write_mode,
                    int(project["migration_epoch"] or 0), canonical_json(request),
                    json_sha256(request), _utc_now(),
                ),
            )

        now = _utc_now()
        connection.execute(
            """
            INSERT INTO sync_operations (
                operation_id, local_key, project_id, document_id, local_path,
                relative_path, base_revision, base_content, content, is_deleted,
                status, attempts, last_error, created_at, updated_at,
                entity_kind, intent_kind, provenance_kind, batch_id,
                batch_sequence, payload_sha256, supersedes_operation_id,
                sync_protocol_version, contract_version,
                canonical_contract_sha256, client_build_id,
                client_capabilities_json, legacy_attempt_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, '', ?, ?,
                'document', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                operation_id, context["local_key"], context["project_id"],
                document["document_id"], local_path, relative_path,
                None if base_revision is None else int(base_revision), base_content, content,
                int(bool(is_deleted)), now, now, intent_kind, provenance,
                batch_id, 1 if batch_id else None, json_sha256(payload),
                supersedes_operation_id, protocol_version, contract_version,
                contract_sha256, CLIENT_BUILD_ID, capabilities_json,
            ),
        )
        self._append_event(connection, operation_id, "enqueued")
        return self._operation_dict(
            connection, self._operation_row(connection, operation_id)
        )

    def enqueue(
        self,
        context,
        local_path,
        content,
        relative_path=None,
        is_deleted=False,
    ):
        local_path = _normalize_path(local_path)
        relative_path = _normalize_path(relative_path or local_path)
        document = self.ensure_document(context["local_key"], local_path, content)
        now = _utc_now()

        with self._transaction() as connection:
            candidates = connection.execute(
                """
                SELECT * FROM sync_operations
                WHERE document_id = ? ORDER BY queue_id DESC
                """,
                (document["document_id"],),
            ).fetchall()
            active = [
                row for row in candidates
                if self._derived_state(connection, row["operation_id"])
                in CONTRACT_ACTIVE_STATES
            ]
            for row in active:
                if self._derived_state(connection, row["operation_id"]) in {
                    "conflict", "blocked"
                }:
                    self._append_event(
                        connection, row["operation_id"], "cancel_requested",
                        detail={"source": "explicit_save"},
                    )
            latest = next((
                row for row in active
                if self._derived_state(connection, row["operation_id"])
                in {"pending", "inflight", "retry_wait"}
            ), None)

            if (
                latest
                and latest["content"] == content
                and latest["relative_path"] == relative_path
                and bool(latest["is_deleted"]) == bool(is_deleted)
            ):
                return self._operation_dict(connection, latest)

            current = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?",
                (document["document_id"],),
            ).fetchone()
            if latest:
                base_revision = None
                base_content = latest["content"]
            else:
                base_revision = current["revision"]
                base_content = current["base_content"]
            operation = self._insert_document_operation(
                connection,
                context=context,
                document=current,
                local_path=local_path,
                relative_path=relative_path,
                base_revision=base_revision,
                base_content=base_content,
                content=content,
                is_deleted=is_deleted,
            )
            connection.execute(
                """
                UPDATE sync_documents
                SET sync_state = 'pending', last_error = '',
                    conflict_base = NULL, conflict_local = NULL,
                    conflict_remote = NULL, conflict_merged = NULL,
                    updated_at = ?
                WHERE document_id = ?
                """,
                (now, document["document_id"]),
            )
            return operation

    def recover_stranded_operations(self, local_key=None):
        """Re-issue chained edits whose predecessor will never complete.

        `base_revision IS NULL` marks an edit that waits for an earlier
        operation on the same document; `mark_success` normally re-issues it
        with the committed revision. When that predecessor never reaches
        `mark_success` - superseded, cancelled, or lost to a crash - nothing
        ever resolves the dependent. `next_ready_operation` skips a NULL
        base_revision forever, so the whole queue stops draining while the UI
        still reports work as pending.

        Returns the number of operations re-issued.
        """
        params = []
        where = "base_revision IS NULL"
        if local_key:
            where += " AND local_key = ?"
            params.append(local_key)
        recovered = 0
        with self._transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM sync_operations WHERE {where} ORDER BY queue_id",
                params,
            ).fetchall()
            for row in rows:
                if self._derived_state(connection, row["operation_id"]) not in {
                    "pending", "retry_wait"
                }:
                    continue
                predecessors = connection.execute(
                    """
                    SELECT operation_id FROM sync_operations
                    WHERE document_id = ? AND queue_id < ?
                    ORDER BY queue_id
                    """,
                    (row["document_id"], row["queue_id"]),
                ).fetchall()
                if any(
                    self._derived_state(connection, item["operation_id"])
                    in {"pending", "inflight", "retry_wait"}
                    for item in predecessors
                ):
                    # 앞선 작업이 아직 살아 있다면 정상적으로 기다리는 중이다.
                    continue
                document = connection.execute(
                    "SELECT * FROM sync_documents WHERE document_id = ?",
                    (row["document_id"],),
                ).fetchone()
                if document is None:
                    continue
                successor = self._insert_document_operation(
                    connection,
                    context={
                        "local_key": row["local_key"],
                        "project_id": row["project_id"],
                    },
                    document=document,
                    local_path=row["local_path"],
                    relative_path=row["relative_path"],
                    base_revision=int(document["revision"] or 0),
                    base_content=document["base_content"],
                    content=row["content"],
                    is_deleted=bool(row["is_deleted"]),
                    supersedes_operation_id=row["operation_id"],
                )
                self._append_event(
                    connection, row["operation_id"], "superseded",
                    related_operation_id=successor["operation_id"],
                    detail={"successor_operation_id": successor["operation_id"]},
                )
                recovered += 1
        return recovered

    def next_ready_operation(self, local_key=None):
        params = []
        where = "base_revision IS NOT NULL"
        if local_key:
            where += " AND local_key = ?"
            params.append(local_key)
        with self._reader() as connection:
            rows = connection.execute(
                f"SELECT * FROM sync_operations WHERE {where} ORDER BY queue_id",
                params,
            ).fetchall()
            ready = []
            for row in rows:
                if self._derived_state(connection, row["operation_id"]) in {
                    "pending", "retry_wait"
                }:
                    ready.append(row)
            if not ready:
                return None
            selected = next(
                (
                    row for row in ready
                    if self._is_volume_folder_skeleton(row)
                ),
                ready[0],
            )
            return self._operation_dict(connection, selected)

    @staticmethod
    def _is_volume_folder_skeleton(operation):
        """Whether a hidden tree snapshot advertises volumes but no chapters."""
        if operation["relative_path"] != "__antigravity__/tree-order.json":
            return False
        try:
            payload = json.loads(operation["content"] or "{}")
            tree_order = payload.get("tree_order")
            volume_names = tree_order.get("메인/원고")
        except (AttributeError, TypeError, json.JSONDecodeError):
            return False
        if not isinstance(volume_names, list) or not volume_names:
            return False
        for volume_name in volume_names:
            children = tree_order.get(f"메인/원고/{volume_name}")
            if children != []:
                return False
        return True

    def mark_attempt(self, operation_id):
        with self._transaction() as connection:
            operation = self._operation_dict(
                connection, self._operation_row(connection, operation_id)
            )
            if operation is None:
                return None
            if operation["status"] not in {"pending", "retry_wait"}:
                if operation["status"] in TERMINAL_STATES:
                    raise SyncContractError("OPERATION_TERMINAL")
                raise SyncContractError("OPERATION_NOT_READY")
            attempt_number = operation["attempts"] + 1
            request_sha256 = operation.get("payload_sha256")
            rpc_name = (
                "atomic_structure_commit"
                if operation.get("queue_kind") == "structure"
                else "document_commit"
                if operation.get("provenance_kind") == "CONTRACT_BATCH"
                else "commit_document"
            )
            return self._append_event(
                connection, operation_id, "dispatch_started",
                detail={
                    "attempt_number": attempt_number,
                    "request_sha256": request_sha256,
                    "rpc_name": rpc_name,
                },
            )

    def mark_retry(self, operation_id, error_message):
        now = _utc_now()
        with self._transaction() as connection:
            row = self._operation_row(connection, operation_id)
            if not row:
                return
            state = self._derived_state(connection, operation_id)
            if state == "retry_wait":
                return self._operation_dict(connection, row)
            if state in TERMINAL_STATES:
                return self._operation_dict(connection, row)
            error_code = self._stable_local_error(error_message)
            self._finish_attempt(
                connection, operation_id, "retryable_error",
                error_code=error_code,
            )
            self._append_event(
                connection, operation_id, "retry_scheduled",
                error_code=error_code,
            )
            if row["queue_kind"] == "structure":
                return self._operation_dict(connection, row)
            connection.execute(
                """
                UPDATE sync_documents
                SET sync_state = 'pending', last_error = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (error_code, now, row["document_id"]),
            )
            return self._operation_dict(connection, row)

    def mark_blocked(self, operation_id, error_code):
        with self._transaction() as connection:
            row = self._operation_row(connection, operation_id)
            if row is None:
                return None
            state = self._derived_state(connection, operation_id)
            if state == "blocked":
                return self._operation_dict(connection, row)
            if state in TERMINAL_STATES:
                raise SyncContractError("OPERATION_TERMINAL")
            self._finish_attempt(
                connection, operation_id, "blocked", error_code=error_code
            )
            self._append_event(
                connection, operation_id, "blocked", error_code=error_code
            )
            return self._operation_dict(connection, row)

    def mark_success(self, operation_id, result):
        now = _utc_now()
        with self._transaction() as connection:
            operation = self._operation_row(connection, operation_id)
            if not operation:
                return None
            state = self._derived_state(connection, operation_id)
            if state == "completed":
                return self._operation_dict(connection, operation)
            response_sha256 = json_sha256({
                key: value for key, value in result.items()
                if key in {"revision", "content_hash", "status"}
                and isinstance(value, (str, int, bool, type(None)))
            })
            outcome = "replayed" if result.get("status") == "replayed" else "committed"
            self._finish_attempt(
                connection, operation_id, outcome,
                response_sha256=response_sha256,
                result_revision=int(result["revision"]),
            )
            self._append_event(connection, operation_id, outcome)
            still_pending = any(
                self._derived_state(connection, row["operation_id"])
                in CONTRACT_ACTIVE_STATES
                for row in connection.execute(
                    """
                    SELECT operation_id FROM sync_operations
                    WHERE document_id = ? AND operation_id <> ?
                    """,
                    (operation["document_id"], operation_id),
                ).fetchall()
            )
            result_name = result.get("name")
            storage_name_key = (
                normalize_storage_name(result_name).normalized
                if result_name is not None else None
            )
            connection.execute(
                """
                UPDATE sync_documents
                SET server_path = ?, revision = ?, base_content = ?, base_hash = ?,
                    is_deleted = ?, sync_state = ?, last_error = '',
                    parent_folder_id = ?, structure_revision = ?, name = ?,
                    storage_name_key = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (
                    operation["relative_path"],
                    result["revision"],
                    operation["content"],
                    result.get("content_hash", hashlib.sha256(operation["content"].encode("utf-8")).hexdigest()),
                    operation["is_deleted"],
                    "pending" if still_pending else "synced",
                    result.get("parent_folder_id"),
                    result.get("structure_revision"),
                    result_name,
                    storage_name_key,
                    now,
                    operation["document_id"],
                ),
            )
            dependent = connection.execute(
                """
                SELECT * FROM sync_operations
                WHERE document_id = ? AND base_revision IS NULL
                ORDER BY queue_id LIMIT 1
                """,
                (operation["document_id"],),
            ).fetchone()
            if dependent and self._derived_state(
                connection, dependent["operation_id"]
            ) in {"pending", "retry_wait"}:
                context = {
                    "local_key": dependent["local_key"],
                    "project_id": dependent["project_id"],
                }
                if operation["batch_id"]:
                    previous_batch = connection.execute(
                        "SELECT writer_device_id FROM sync_contract_batches "
                        "WHERE batch_id = ?",
                        (operation["batch_id"],),
                    ).fetchone()
                    if previous_batch:
                        context["writer_device_id"] = previous_batch["writer_device_id"]
                successor = self._insert_document_operation(
                    connection,
                    context=context,
                    document=connection.execute(
                        "SELECT * FROM sync_documents WHERE document_id = ?",
                        (dependent["document_id"],),
                    ).fetchone(),
                    local_path=dependent["local_path"],
                    relative_path=dependent["relative_path"],
                    base_revision=int(result["revision"]),
                    base_content=operation["content"],
                    content=dependent["content"],
                    is_deleted=bool(dependent["is_deleted"]),
                    supersedes_operation_id=dependent["operation_id"],
                )
                self._append_event(
                    connection, dependent["operation_id"], "superseded",
                    related_operation_id=successor["operation_id"],
                    detail={"successor_operation_id": successor["operation_id"]},
                )
            return self._operation_dict(connection, operation)

    def rebase_clean_merge(
        self, operation_id, remote_revision, remote_content, merged_content,
        remote_path=None,
    ):
        now = _utc_now()
        with self._transaction() as connection:
            operation = self._operation_row(connection, operation_id)
            if not operation:
                return
            self._finish_attempt(
                connection, operation_id, "conflict", error_code="REVISION_CONFLICT"
            )
            if self._derived_state(connection, operation_id) != "conflict":
                self._append_event(
                    connection,
                    operation_id,
                    "conflict_detected",
                    error_code="REVISION_CONFLICT",
                )
            context = {
                "local_key": operation["local_key"],
                "project_id": operation["project_id"],
            }
            document = connection.execute(
                "SELECT * FROM sync_documents WHERE document_id = ?",
                (operation["document_id"],),
            ).fetchone()
            successor = self._insert_document_operation(
                connection,
                context=context,
                document=document,
                local_path=operation["local_path"],
                relative_path=remote_path or operation["relative_path"],
                base_revision=int(remote_revision),
                base_content=remote_content,
                content=merged_content,
                is_deleted=bool(operation["is_deleted"]),
                supersedes_operation_id=operation_id,
            )
            active_rows = connection.execute(
                """
                SELECT operation_id FROM sync_operations
                WHERE document_id = ? AND operation_id <> ?
                """,
                (operation["document_id"], successor["operation_id"]),
            ).fetchall()
            for active in active_rows:
                active_id = active["operation_id"]
                if self._derived_state(connection, active_id) in CONTRACT_ACTIVE_STATES:
                    self._append_event(
                        connection, active_id, "superseded",
                        related_operation_id=successor["operation_id"],
                        detail={"successor_operation_id": successor["operation_id"]},
                    )
            connection.execute(
                """
                UPDATE sync_documents
                SET revision = ?, base_content = ?, base_hash = ?,
                    server_path = COALESCE(?, server_path),
                    sync_state = 'pending', last_error = '', updated_at = ?
                WHERE document_id = ?
                """,
                (
                    remote_revision,
                    remote_content,
                    hashlib.sha256(remote_content.encode("utf-8")).hexdigest(),
                    remote_path,
                    now,
                    operation["document_id"],
                ),
            )
            return successor

    def mark_conflict(
        self,
        operation_id,
        remote_revision,
        remote_path,
        remote_content,
        merged_content,
        local_content=None,
        error_message="REVISION_CONFLICT",
    ):
        now = _utc_now()
        with self._transaction() as connection:
            operation = self._operation_row(connection, operation_id)
            if not operation:
                return None
            self._finish_attempt(
                connection, operation_id, "conflict", error_code="REVISION_CONFLICT"
            )
            self._append_event(
                connection, operation_id, "conflict_detected",
                error_code="REVISION_CONFLICT",
            )
            for dependent in connection.execute(
                """
                SELECT operation_id FROM sync_operations
                WHERE document_id = ? AND operation_id <> ?
                """,
                (operation["document_id"], operation_id),
            ).fetchall():
                dependent_id = dependent["operation_id"]
                if self._derived_state(connection, dependent_id) in {
                    "pending", "retry_wait", "inflight"
                }:
                    self._append_event(
                        connection, dependent_id, "blocked",
                        error_code="BLOCKED_BY_CONFLICT",
                        related_operation_id=operation_id,
                    )
            connection.execute(
                """
                UPDATE sync_documents
                SET server_path = ?, revision = ?, base_content = ?, base_hash = ?,
                    sync_state = 'conflict', last_error = ?,
                    conflict_base = ?, conflict_local = ?, conflict_remote = ?,
                    conflict_merged = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (
                    remote_path,
                    remote_revision,
                    remote_content,
                    hashlib.sha256(remote_content.encode("utf-8")).hexdigest(),
                    error_message,
                    operation["base_content"],
                    operation["content"] if local_content is None else local_content,
                    remote_content,
                    merged_content,
                    now,
                    operation["document_id"],
                ),
            )
            return self._operation_dict(connection, operation)

    def move_local_path(self, local_key, old_path, new_path):
        old_path = _normalize_path(old_path)
        new_path = _normalize_path(new_path)
        now = _utc_now()
        moved = []
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sync_documents
                WHERE local_key = ? AND (local_path = ? OR local_path LIKE ?)
                ORDER BY length(local_path)
                """,
                (local_key, old_path, old_path + "/%"),
            ).fetchall()
            for row in rows:
                suffix = row["local_path"][len(old_path):]
                updated_path = new_path + suffix
                connection.execute(
                    "UPDATE sync_documents SET local_path = ?, updated_at = ? WHERE document_id = ?",
                    (updated_path, now, row["document_id"]),
                )
                moved.append({**dict(row), "old_local_path": row["local_path"], "local_path": updated_path})
        return moved

    def create_structure_batch_with_path_changes(
        self,
        context,
        writer_device_id,
        ordered_intents,
        path_changes,
        *,
        document_changes=None,
        batch_id=None,
    ):
        """Persist one contract batch and every local projection atomically."""
        with self._transaction():
            request = self.create_structure_batch(
                context,
                writer_device_id,
                ordered_intents,
                batch_id=batch_id,
            )
            # A restored folder must be visible before its documents rebuild
            # parent identity. A deleted folder must remain visible until its
            # document tombstones have captured that same parent identity.
            for change in path_changes or []:
                for update in change.get("folder_updates") or []:
                    if update.get("is_deleted"):
                        continue
                    self._apply_local_folder_update(
                        context["local_key"], update
                    )
            for change in document_changes or []:
                document_id = str(uuid.UUID(str(change["document_id"])))
                old_path = _normalize_path(change["old_local_path"])
                new_path = _normalize_path(change["new_local_path"])
                with self._transaction() as connection:
                    document = connection.execute(
                        "SELECT * FROM sync_documents "
                        "WHERE document_id = ? AND local_key = ?",
                        (document_id, context["local_key"]),
                    ).fetchone()
                    if document is None or document["local_path"] != old_path:
                        raise SyncContractError("INVALID_ARGUMENT")
                    connection.execute(
                        "UPDATE sync_documents SET local_path = ?, updated_at = ? "
                        "WHERE document_id = ?",
                        (new_path, _utc_now(), document_id),
                    )
                if change.get("enqueue", True):
                    self.enqueue(
                        context,
                        new_path,
                        change["content"],
                        relative_path=change["relative_path"],
                        is_deleted=bool(change.get("is_deleted")),
                    )
            for change in path_changes or []:
                pending = change.get("pending_folder")
                if pending:
                    self.ensure_local_folder(
                        context["local_key"],
                        pending["local_path"],
                        folder_id=pending["folder_id"],
                        parent_folder_id=pending.get("parent_folder_id"),
                    )
                old_path = change.get("old_path")
                new_path = change.get("new_path")
                if old_path and new_path and old_path != new_path:
                    self.move_folder_paths(
                        context["local_key"], old_path, new_path
                    )
                    self.move_local_path(
                        context["local_key"], old_path, new_path
                    )
                    self.move_tree_order_paths(
                        context["local_key"], old_path, new_path
                    )
                for update in change.get("folder_updates") or []:
                    if not update.get("is_deleted"):
                        continue
                    self._apply_local_folder_update(
                        context["local_key"], update
                    )
            return request

    def _apply_local_folder_update(self, local_key, update):
        name = str(update["local_path"]).rsplit("/", 1)[-1]
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE sync_folders
                SET parent_folder_id = ?, local_path = ?, name = ?,
                    storage_name_key = ?, is_deleted = ?, updated_at = ?
                WHERE folder_id = ? AND local_key = ?
                """,
                (
                    update.get("parent_folder_id"),
                    _normalize_path(update["local_path"]),
                    name,
                    normalize_storage_name(name).normalized,
                    int(bool(update["is_deleted"])),
                    _utc_now(), update["folder_id"], local_key,
                ),
            )

    def record_structure_recovery(
        self, local_key, old_path, new_path, error_code
    ):
        with self._transaction() as connection:
            recovery_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO sync_structure_recovery (
                    recovery_id, local_key, old_path, new_path,
                    error_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    recovery_id, local_key, _normalize_path(old_path),
                    _normalize_path(new_path), str(error_code or "RECOVERY_FAILED")[:80],
                    _utc_now(),
                ),
            )
            return recovery_id

    def move_destination_conflicts(self, local_key, old_path, new_path):
        """Return documents that already reserve a destination of a prefix move."""
        old_path = _normalize_path(old_path)
        new_path = _normalize_path(new_path)
        with self._reader() as connection:
            moving = connection.execute(
                """
                SELECT document_id, local_path
                FROM sync_documents
                WHERE local_key = ? AND (local_path = ? OR local_path LIKE ?)
                """,
                (local_key, old_path, old_path + "/%"),
            ).fetchall()
            if not moving:
                return []
            moving_ids = {row["document_id"] for row in moving}
            conflicts = []
            for row in moving:
                suffix = row["local_path"][len(old_path):]
                destination = new_path + suffix
                occupant = connection.execute(
                    """
                    SELECT * FROM sync_documents
                    WHERE local_key = ? AND local_path = ?
                    """,
                    (local_key, destination),
                ).fetchone()
                if occupant and occupant["document_id"] not in moving_ids:
                    conflicts.append(dict(occupant))
            return conflicts

    def counts(self, local_key=None):
        with self._reader() as connection:
            params = []
            where = ""
            if local_key:
                where = " WHERE local_key = ?"
                params.append(local_key)
            rows = connection.execute(
                f"SELECT operation_id, document_id FROM sync_operations{where}",
                params,
            ).fetchall()
            structure_rows = connection.execute(
                f"SELECT operation_id FROM sync_structure_operations{where}", params
            ).fetchall()
            result = {
                "pending": 0, "inflight": 0, "retry_wait": 0,
                "blocked": 0, "conflict": 0,
            }
            # 사용자에게 보이는 건수다. 이미 끝난 작업의 문서까지 세면 숫자가
            # 늘기만 하고 절대 줄지 않아, 남은 일이 없어도 계속 경고가 뜬다.
            outstanding_documents = set()
            for row in rows:
                state = self._derived_state(connection, row["operation_id"])
                if state in result:
                    result[state] += 1
                    if row["document_id"]:
                        outstanding_documents.add(row["document_id"])
            for row in structure_rows:
                state = self._derived_state(connection, row["operation_id"])
                if state in result:
                    result[state] += 1
        result["total"] = sum(result.values())
        result["documents"] = len(outstanding_documents)
        return result

    def conflict_documents(self, local_key=None):
        """Return every document waiting for the writer to pick a version."""
        params = ["conflict"]
        where = "sync_state = ?"
        if local_key:
            where += " AND local_key = ?"
            params.append(local_key)
        with self._reader() as connection:
            rows = connection.execute(
                f"""
                SELECT document_id, local_path, revision, conflict_base,
                       conflict_local, conflict_remote, conflict_merged
                FROM sync_documents WHERE {where} ORDER BY local_path
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def latest_error(self, local_key=None):
        """Return the newest error still describing unfinished work.

        Errors recorded against an operation that later completed are history:
        a single stale AUTH_REQUIRED must not keep describing a queue that has
        since been committing successfully.
        """
        with self._reader() as connection:
            params = []
            scope = ""
            if local_key:
                scope = " AND operation_id IN (SELECT operation_id FROM sync_operations WHERE local_key = ? UNION SELECT operation_id FROM sync_structure_operations WHERE local_key = ?)"
                params.extend((local_key, local_key))
            cursor = connection.execute(
                """
                SELECT operation_id, error_code FROM sync_operation_events
                WHERE error_code IS NOT NULL
                """ + scope + " ORDER BY recorded_at DESC, event_sequence DESC",
                params,
            )
            # 상태는 append-only 이벤트에서 파생된다. 가장 최근 오류부터 훑되
            # 이미 끝난 작업의 오류는 건너뛴다.
            for row in cursor:
                try:
                    state = self._derived_state(connection, row["operation_id"])
                except RuntimeError:
                    continue
                if state in CONTRACT_ACTIVE_STATES:
                    return row["error_code"]
            return ""

    def operation(self, operation_id):
        with self._reader() as connection:
            return self._operation_dict(
                connection, self._operation_row(connection, operation_id)
            )

    def latest_active_structure_operation(self, entity_id):
        entity_id = str(uuid.UUID(str(entity_id)))
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_structure_operations "
                "WHERE entity_id = ? ORDER BY created_at DESC, operation_id DESC",
                (entity_id,),
            ).fetchall()
            for row in rows:
                if self._derived_state(connection, row["operation_id"]) in {
                    "pending", "inflight", "retry_wait", "blocked", "conflict"
                }:
                    return dict(row)
            return None

    def cancel_operation(self, operation_id, cancel_event_id):
        cancel_event_id = str(uuid.UUID(str(cancel_event_id)))
        with self._transaction() as connection:
            row = self._operation_row(connection, operation_id)
            if row is None:
                raise KeyError(operation_id)
            existing = connection.execute(
                "SELECT * FROM sync_operation_events WHERE event_id = ?",
                (cancel_event_id,),
            ).fetchone()
            if existing:
                if (
                    existing["operation_id"] != operation_id
                    or existing["event_type"] != "cancel_requested"
                ):
                    raise SyncContractError("EVENT_ID_REUSED")
                return {"status": "already_cancelled", "event_id": cancel_event_id}
            state = self._derived_state(connection, operation_id)
            if state == "completed":
                raise SyncContractError("OPERATION_TERMINAL")
            if state == "cancelled":
                return {"status": "already_cancelled", "event_id": None}
            self._append_event(
                connection, operation_id, "cancel_requested",
                event_id=cancel_event_id,
            )
            return {"status": "cancelled", "event_id": cancel_event_id}

    def create_structure_batch(
        self,
        context,
        writer_device_id,
        ordered_intents,
        *,
        batch_id=None,
    ):
        with self._transaction() as connection:
            project = connection.execute(
                "SELECT * FROM sync_projects WHERE local_key = ?",
                (context["local_key"],),
            ).fetchone()
            if project is None:
                raise KeyError(context["local_key"])
            if not project["contract_path_enabled"]:
                # The caller is expected to have consulted the gate already.
                # Checking again here means a future caller that forgets cannot
                # put a contract batch on the queue.
                raise SyncContractError("CONTRACT_NOT_ALLOWED")
            capabilities = json.loads(project["server_capabilities_json"] or "[]")
            require_server_compatibility(
                project_sync_mode=project["project_sync_mode"],
                migration_epoch=int(project["migration_epoch"] or 0),
                server_protocol_version=int(project["server_protocol_version"] or 0),
                server_contract_sha256=project["active_contract_sha256"] or "",
                server_capabilities=capabilities,
            )
            request = build_atomic_structure_request(
                project_id=context["project_id"],
                project_sync_mode=project["project_sync_mode"],
                migration_epoch=int(project["migration_epoch"] or 0),
                writer_device_id=writer_device_id,
                ordered_intents=ordered_intents,
                batch_id=batch_id,
                client_build_id=CLIENT_BUILD_ID,
            )
            batch = request["batch"]
            existing = connection.execute(
                "SELECT * FROM sync_contract_batches WHERE batch_id = ?",
                (batch["batch_id"],),
            ).fetchone()
            if existing:
                if existing["request_sha256"] != json_sha256(request):
                    raise SyncContractError("BATCH_ID_REUSED")
                return json.loads(existing["request_json"])
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO sync_contract_batches (
                    batch_id, local_key, project_id, writer_device_id,
                    client_build_id, sync_protocol_version, contract_version,
                    canonical_contract_sha256, client_capabilities_json,
                    batch_payload_sha256, project_sync_mode, migration_epoch,
                    request_json, request_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["batch_id"], context["local_key"], context["project_id"],
                    batch["writer_device_id"], batch["client_build_id"],
                    batch["sync_protocol_version"], batch["contract_version"],
                    batch["canonical_contract_sha256"],
                    canonical_json(batch["client_capabilities"]),
                    batch["batch_payload_sha256"], request["project_sync_mode"],
                    request["migration_epoch"], canonical_json(request),
                    json_sha256(request), now,
                ),
            )
            superseded_ids = [
                intent.get("supersedes_operation_id")
                for intent in request["ordered_intents"]
                if intent.get("supersedes_operation_id")
            ]
            if len(superseded_ids) != len(set(superseded_ids)):
                raise SyncContractError("INVALID_ARGUMENT")
            for intent in request["ordered_intents"]:
                connection.execute(
                    """
                    INSERT INTO sync_structure_operations (
                        operation_id, local_key, project_id, entity_kind,
                        entity_id, intent_kind, provenance_kind, batch_id,
                        batch_sequence, base_revision, payload_json,
                        payload_sha256, supersedes_operation_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'CONTRACT_BATCH', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent["operation_id"], context["local_key"],
                        context["project_id"], intent["entity_kind"],
                        intent["entity_id"], intent["intent_kind"],
                        intent["batch_id"], intent["sequence"],
                        intent["base_revision"], canonical_json(intent["payload"]),
                        intent["payload_sha256"],
                        intent.get("supersedes_operation_id"), now,
                    ),
                )
                self._append_event(connection, intent["operation_id"], "enqueued")
            for intent in request["ordered_intents"]:
                original_id = intent.get("supersedes_operation_id")
                if original_id:
                    self._append_event(
                        connection, original_id, "superseded",
                        related_operation_id=intent["operation_id"],
                        detail={"successor_operation_id": intent["operation_id"]},
                    )
            return request

    def next_ready_structure_batch(self, local_key=None):
        with self._reader() as connection:
            params = []
            scope = ""
            if local_key:
                scope = "WHERE batch.local_key = ?"
                params.append(local_key)
            rows = connection.execute(
                f"""
                SELECT batch.* FROM sync_contract_batches AS batch
                LEFT JOIN sync_contract_batch_results AS result
                    ON result.batch_id = batch.batch_id
                {scope}
                AND result.batch_id IS NULL
                ORDER BY batch.created_at
                """ if scope else """
                SELECT batch.* FROM sync_contract_batches AS batch
                LEFT JOIN sync_contract_batch_results AS result
                    ON result.batch_id = batch.batch_id
                WHERE result.batch_id IS NULL
                ORDER BY batch.created_at
                """,
                params,
            ).fetchall()
            for batch in rows:
                operations = connection.execute(
                    """
                    SELECT operation_id FROM sync_structure_operations
                    WHERE batch_id = ? ORDER BY batch_sequence
                    """,
                    (batch["batch_id"],),
                ).fetchall()
                states = [
                    self._derived_state(connection, row["operation_id"])
                    for row in operations
                ]
                if states and all(state in {"pending", "retry_wait"} for state in states):
                    return json.loads(batch["request_json"])
            return None

    def structure_batch_request(self, batch_id):
        with self._reader() as connection:
            row = connection.execute(
                "SELECT request_json FROM sync_contract_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            return json.loads(row["request_json"]) if row else None

    def document_batch_response(self, batch_id):
        with self._reader() as connection:
            row = connection.execute(
                "SELECT response_json FROM sync_contract_batch_results "
                "WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            return json.loads(row["response_json"]) if row else None

    def record_document_batch_response(self, batch_id, response):
        """Validate and append a complete document result before local apply."""
        with self._transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM sync_contract_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            request = json.loads(batch["request_json"])
            validated = validate_document_commit_response(request, response)
            existing = connection.execute(
                "SELECT * FROM sync_contract_batch_results WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if existing:
                recorded = json.loads(existing["response_json"])
                same = recorded == validated
                if not same and all(
                    item.get("kind") == "document_commit_success"
                    for item in (recorded, validated)
                ):
                    left = dict(recorded)
                    right = dict(validated)
                    left["status"] = right["status"] = "committed"
                    same = left == right
                if not same:
                    raise SyncContractError("BATCH_ID_REUSED")
                return recorded
            connection.execute(
                """
                INSERT INTO sync_contract_batch_results (
                    batch_id, response_json, response_sha256, applied, recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch_id, canonical_json(validated), json_sha256(validated),
                    int(bool(validated["applied"])), _utc_now(),
                ),
            )
            return validated

    def mark_structure_batch_attempt(self, batch_id):
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sync_structure_operations
                WHERE batch_id = ? ORDER BY batch_sequence
                """,
                (batch_id,),
            ).fetchall()
            if not rows:
                raise KeyError(batch_id)
            for row in rows:
                state = self._derived_state(connection, row["operation_id"])
                if state not in {"pending", "retry_wait"}:
                    if state in TERMINAL_STATES:
                        raise SyncContractError("OPERATION_TERMINAL")
                    raise SyncContractError("OPERATION_NOT_READY")
            operation_ids = []
            for row in rows:
                operation_id = row["operation_id"]
                attempts = connection.execute(
                    "SELECT COUNT(*) FROM sync_operation_attempts WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()[0]
                self._append_event(
                    connection,
                    operation_id,
                    "dispatch_started",
                    detail={
                        "attempt_number": int(attempts) + 1,
                        "request_sha256": row["payload_sha256"],
                        "rpc_name": "atomic_structure_commit",
                    },
                )
                operation_ids.append(operation_id)
            return operation_ids

    def mark_structure_batch_retry(self, batch_id, error_message):
        with self._transaction() as connection:
            operation_ids = [
                row["operation_id"] for row in connection.execute(
                    """
                    SELECT operation_id FROM sync_structure_operations
                    WHERE batch_id = ? ORDER BY batch_sequence
                    """,
                    (batch_id,),
                ).fetchall()
            ]
            if not operation_ids:
                raise KeyError(batch_id)
            error_code = self._stable_local_error(error_message)
            for operation_id in operation_ids:
                state = self._derived_state(connection, operation_id)
                if state == "retry_wait" or state in TERMINAL_STATES:
                    continue
                self._finish_attempt(
                    connection,
                    operation_id,
                    "retryable_error",
                    error_code=error_code,
                )
                self._append_event(
                    connection,
                    operation_id,
                    "retry_scheduled",
                    error_code=error_code,
                )
            return operation_ids

    def record_structure_batch_response(self, batch_id, response):
        with self._transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM sync_contract_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            request = json.loads(batch["request_json"])
            validated = validate_atomic_structure_response(request, response)
            response_sha256 = json_sha256(validated)
            existing = connection.execute(
                "SELECT * FROM sync_contract_batch_results WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if existing:
                if existing["response_sha256"] != response_sha256:
                    raise SyncContractError("BATCH_ID_REUSED")
                return json.loads(existing["response_json"])

            intents = request["ordered_intents"]
            if validated["kind"] == "atomic_structure_commit_success":
                result_by_operation = {
                    item["operation_id"]: item for item in validated["results"]
                }
                event_type = (
                    "replayed" if validated["status"] == "replayed" else "committed"
                )
                for intent in intents:
                    result = result_by_operation[intent["operation_id"]]
                    self._finish_attempt(
                        connection, intent["operation_id"], event_type,
                        response_sha256=response_sha256,
                        result_revision=result["result_revision"],
                    )
                    self._append_event(
                        connection, intent["operation_id"], event_type
                    )
                    result_revision = int(result["result_revision"])
                    if intent["entity_kind"] == "folder":
                        connection.execute(
                            "UPDATE sync_folders SET revision = ?, updated_at = ? "
                            "WHERE folder_id = ? AND local_key = ?",
                            (
                                result_revision, _utc_now(), intent["entity_id"],
                                batch["local_key"],
                            ),
                        )
                    elif intent["entity_kind"] == "document":
                        connection.execute(
                            "UPDATE sync_documents SET structure_revision = ?, "
                            "updated_at = ? WHERE document_id = ? AND local_key = ?",
                            (
                                result_revision, _utc_now(), intent["entity_id"],
                                batch["local_key"],
                            ),
                        )
                    elif intent["entity_kind"] == "tree_order":
                        payload = intent["payload"]
                        parent_folder_id = payload.get("parent_folder_id")
                        parent_path = "<root>"
                        if parent_folder_id:
                            parent = connection.execute(
                                "SELECT local_path FROM sync_folders "
                                "WHERE folder_id = ? AND local_key = ?",
                                (parent_folder_id, batch["local_key"]),
                            ).fetchone()
                            if parent is None:
                                raise SyncContractError("TREE_REFERENCE_NOT_FOUND")
                            parent_path = parent["local_path"]
                        now = _utc_now()
                        connection.execute(
                            """
                            INSERT INTO sync_tree_orders (
                                tree_order_id, local_key, parent_folder_id,
                                parent_path, children_json, revision,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(tree_order_id) DO UPDATE SET
                                parent_folder_id = excluded.parent_folder_id,
                                parent_path = excluded.parent_path,
                                children_json = excluded.children_json,
                                revision = excluded.revision,
                                updated_at = excluded.updated_at
                            """,
                            (
                                intent["entity_id"], batch["local_key"],
                                parent_folder_id, parent_path,
                                canonical_json(payload["children"]),
                                result_revision, now, now,
                            ),
                        )
                applied = 1
            else:
                error = validated["error"]
                for intent in intents:
                    is_failure = intent["sequence"] == error["failed_sequence"]
                    conflict = is_failure and error["code"] == "REVISION_CONFLICT"
                    outcome = "conflict" if conflict else "blocked"
                    event_type = "conflict_detected" if conflict else "blocked"
                    self._finish_attempt(
                        connection, intent["operation_id"], outcome,
                        response_sha256=response_sha256,
                        error_code=error["code"],
                        error_detail={"failed_sequence": error["failed_sequence"]},
                    )
                    self._append_event(
                        connection, intent["operation_id"], event_type,
                        error_code=error["code"],
                        detail={"failed_sequence": error["failed_sequence"]},
                    )
                applied = 0
            connection.execute(
                """
                INSERT INTO sync_contract_batch_results (
                    batch_id, response_json, response_sha256, applied, recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch_id, canonical_json(validated), response_sha256,
                    applied, _utc_now(),
                ),
            )
            return validated

    def record_diagnostic(self, local_key, event, dedupe=False, **metadata):
        """Record one diagnostic. ``dedupe`` keeps a standing state to one row.

        A condition that is re-checked on every dispatch — a folder this client
        may not publish, say — would otherwise append a row per attempt and bury
        everything else.
        """
        trace = safe_trace(event, **metadata)
        with self._transaction() as connection:
            if dedupe:
                existing = connection.execute(
                    """
                    SELECT * FROM sync_contract_diagnostics
                    WHERE local_key IS ? AND event = ? AND metadata_json = ?
                    LIMIT 1
                    """,
                    (local_key, trace["event"], canonical_json(trace)),
                ).fetchone()
                if existing is not None:
                    return {"trace_id": existing["trace_id"], **trace}
            trace_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO sync_contract_diagnostics (
                    trace_id, local_key, event, metadata_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    trace_id, local_key, trace["event"], canonical_json(trace),
                    _utc_now(),
                ),
            )
            return {"trace_id": trace_id, **trace}

    def diagnostics(self, local_key=None, limit=50):
        """Read recorded diagnostics, newest first, so they can be reported."""
        query = (
            "SELECT * FROM sync_contract_diagnostics "
            "{where}ORDER BY recorded_at DESC, trace_id DESC LIMIT ?"
        )
        with self._reader() as connection:
            if local_key is None:
                rows = connection.execute(
                    query.format(where=""), (int(limit),)
                ).fetchall()
            else:
                rows = connection.execute(
                    query.format(where="WHERE local_key = ? "),
                    (local_key, int(limit)),
                ).fetchall()
            return [
                {**dict(row), "metadata": json.loads(row["metadata_json"])}
                for row in rows
            ]

    def defer_tree_order(self, context, tree_order_content, operation_ids):
        operation_ids = [str(uuid.UUID(str(value))) for value in operation_ids]
        if not operation_ids:
            raise ValueError("tree-order barrier requires document operations")
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM sync_tree_barriers WHERE local_key = ?",
                (context["local_key"],),
            ).fetchone()
            required = set(operation_ids)
            barrier_id = str(uuid.uuid4())
            created_at = now
            if existing:
                barrier_id = existing["barrier_id"]
                created_at = existing["created_at"]
                try:
                    required.update(json.loads(existing["required_operation_ids"]))
                except (TypeError, json.JSONDecodeError):
                    pass
            for operation_id in required:
                operation = connection.execute(
                    "SELECT local_key, project_id, relative_path FROM sync_operations "
                    "WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if (
                    operation is None
                    or operation["local_key"] != context["local_key"]
                    or operation["project_id"] != context["project_id"]
                    or operation["relative_path"] == "__antigravity__/tree-order.json"
                ):
                    raise ValueError("invalid tree-order barrier operation")
            payload = json.dumps(sorted(required), separators=(",", ":"))
            connection.execute(
                """
                INSERT INTO sync_tree_barriers (
                    barrier_id, local_key, project_id, tree_order_content,
                    required_operation_ids, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_key) DO UPDATE SET
                    project_id = excluded.project_id,
                    tree_order_content = excluded.tree_order_content,
                    required_operation_ids = excluded.required_operation_ids,
                    updated_at = excluded.updated_at
                """,
                (
                    barrier_id, context["local_key"], context["project_id"],
                    tree_order_content, payload, created_at, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sync_tree_barriers WHERE local_key = ?",
                (context["local_key"],),
            ).fetchone()
            return dict(row)

    def ready_tree_order_barrier(self, local_key):
        with self._reader() as connection:
            barrier = connection.execute(
                "SELECT * FROM sync_tree_barriers WHERE local_key = ?",
                (local_key,),
            ).fetchone()
            if barrier is None:
                return None
            try:
                operation_ids = json.loads(barrier["required_operation_ids"])
            except (TypeError, json.JSONDecodeError):
                return None
            if not operation_ids:
                return None
            placeholders = ",".join("?" for _ in operation_ids)
            rows = connection.execute(
                f"SELECT operation_id FROM sync_operations "
                f"WHERE operation_id IN ({placeholders})",
                operation_ids,
            ).fetchall()
            statuses = {
                row["operation_id"]: self._derived_state(
                    connection, row["operation_id"]
                )
                for row in rows
            }
            # A superseded or cancelled operation has finished; it is not
            # still on its way. It will never report again, and where one
            # was superseded its content already moved to the successor.
            # Holding out for 'committed' from one of those keeps the order
            # — and the folder publication riding with it — back for good.
            if any(
                statuses.get(operation_id) not in {"completed", "cancelled"}
                for operation_id in operation_ids
            ):
                return None
            result = dict(barrier)
            result["required_operation_ids"] = operation_ids
            return result

    def has_active_structure_kind(self, local_key, entity_kind):
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT operation_id FROM sync_structure_operations "
                "WHERE local_key = ? AND entity_kind = ?",
                (local_key, entity_kind),
            ).fetchall()
            return any(
                self._derived_state(connection, row["operation_id"])
                in CONTRACT_ACTIVE_STATES
                for row in rows
            )

    def complete_tree_order_barrier(self, barrier_id):
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM sync_tree_barriers WHERE barrier_id = ?",
                (barrier_id,),
            )

    @staticmethod
    def _folder_rename_intent_events_for(connection, intent_id):
        return connection.execute(
            """
            SELECT * FROM sync_folder_rename_intent_events
            WHERE intent_id = ? ORDER BY event_sequence
            """,
            (intent_id,),
        ).fetchall()

    def _folder_rename_intent_state(self, connection, intent_id):
        events = self._folder_rename_intent_events_for(connection, intent_id)
        if not events:
            raise RuntimeError("folder rename intent has no append-only event history")
        for expected, event in enumerate(events, 1):
            if event["event_sequence"] != expected:
                raise RuntimeError(
                    "folder rename intent event sequence is not contiguous"
                )
        return "completed" if events[-1]["event_type"] == "completed" else "pending"

    def _folder_rename_intent_dict(self, connection, row):
        if row is None:
            return None
        result = dict(row)
        result["status"] = self._folder_rename_intent_state(
            connection, result["intent_id"]
        )
        return result

    def _append_folder_rename_intent_event(
        self, connection, intent_id, event_type, *, detail=None
    ):
        row = connection.execute(
            "SELECT intent_id FROM sync_folder_rename_intents WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        if event_type not in {"recorded", "retargeted", "completed"}:
            raise ValueError("invalid folder rename intent event")
        events = self._folder_rename_intent_events_for(connection, intent_id)
        if not events:
            if event_type != "recorded":
                raise RuntimeError("folder rename intent must start with recorded")
            sequence = 1
        else:
            current = events[-1]["event_type"]
            if current == "completed":
                if event_type == "completed":
                    return dict(events[-1])
                raise RuntimeError("folder rename intent is completed")
            if event_type == "recorded":
                raise RuntimeError("folder rename intent is already recorded")
            sequence = events[-1]["event_sequence"] + 1
        event_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO sync_folder_rename_intent_events (
                event_id, intent_id, event_sequence, event_type,
                recorded_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, intent_id, sequence, event_type, _utc_now(),
                canonical_json(detail or {}),
            ),
        )
        return dict(connection.execute(
            "SELECT * FROM sync_folder_rename_intent_events WHERE event_id = ?",
            (event_id,),
        ).fetchone())

    def record_folder_rename_intent(self, local_key, old_path, new_path):
        """Durably record an explicit local folder rename, coalescing chains."""
        old_path = _normalize_path(old_path)
        new_path = _normalize_path(new_path)
        if not old_path or not new_path or old_path == new_path:
            raise ValueError("invalid folder rename intent")
        now = _utc_now()
        with self._transaction() as connection:
            previous = next((
                row for row in connection.execute(
                    """
                    SELECT * FROM sync_folder_rename_intents
                    WHERE local_key = ? AND new_path = ?
                    ORDER BY updated_at DESC, intent_id
                    """,
                    (local_key, old_path),
                ).fetchall()
                if self._folder_rename_intent_state(
                    connection, row["intent_id"]
                ) == "pending"
            ), None)
            if previous:
                connection.execute(
                    """
                    UPDATE sync_folder_rename_intents
                    SET new_path = ?, updated_at = ? WHERE intent_id = ?
                    """,
                    (new_path, now, previous["intent_id"]),
                )
                intent_id = previous["intent_id"]
                self._append_folder_rename_intent_event(
                    connection,
                    intent_id,
                    "retargeted",
                    detail={"old_path": old_path, "new_path": new_path},
                )
            else:
                existing = next((
                    row for row in connection.execute(
                        """
                        SELECT * FROM sync_folder_rename_intents
                        WHERE local_key = ? AND old_path = ? AND new_path = ?
                        ORDER BY updated_at DESC, intent_id
                        """,
                        (local_key, old_path, new_path),
                    ).fetchall()
                    if self._folder_rename_intent_state(
                        connection, row["intent_id"]
                    ) == "pending"
                ), None)
                if existing:
                    intent_id = existing["intent_id"]
                    connection.execute(
                        """
                        UPDATE sync_folder_rename_intents SET updated_at = ?
                        WHERE intent_id = ?
                        """,
                        (now, intent_id),
                    )
                else:
                    intent_id = str(uuid.uuid4())
                    connection.execute(
                        """
                        INSERT INTO sync_folder_rename_intents (
                            intent_id, local_key, old_path, new_path, status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (intent_id, local_key, old_path, new_path, now, now),
                    )
                    self._append_folder_rename_intent_event(
                        connection,
                        intent_id,
                        "recorded",
                        detail={"old_path": old_path, "new_path": new_path},
                    )
            row = connection.execute(
                "SELECT * FROM sync_folder_rename_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            return self._folder_rename_intent_dict(connection, row)

    def pending_folder_rename_intent(self, local_key, old_path, new_path):
        old_path = _normalize_path(old_path)
        new_path = _normalize_path(new_path)
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sync_folder_rename_intents
                WHERE local_key = ? AND old_path = ? AND new_path = ?
                ORDER BY updated_at DESC, intent_id
                """,
                (local_key, old_path, new_path),
            ).fetchall()
            return next((
                self._folder_rename_intent_dict(connection, row)
                for row in rows
                if self._folder_rename_intent_state(
                    connection, row["intent_id"]
                ) == "pending"
            ), None)

    def pending_folder_rename_intents(self, local_key):
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sync_folder_rename_intents
                WHERE local_key = ?
                ORDER BY created_at, intent_id
                """,
                (local_key,),
            ).fetchall()
            return [
                self._folder_rename_intent_dict(connection, row)
                for row in rows
                if self._folder_rename_intent_state(
                    connection, row["intent_id"]
                ) == "pending"
            ]

    def folder_rename_intent_events(self, intent_id):
        with self._reader() as connection:
            return [
                {**dict(row), "detail": json.loads(row["detail_json"])}
                for row in self._folder_rename_intent_events_for(
                    connection, str(intent_id)
                )
            ]

    def complete_folder_rename_intent(self, intent_id):
        with self._transaction() as connection:
            intent_id = str(intent_id)
            row = connection.execute(
                "SELECT * FROM sync_folder_rename_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                return None
            self._append_folder_rename_intent_event(
                connection, intent_id, "completed"
            )
            return self._folder_rename_intent_dict(connection, row)

    def tree_order_barrier(self, local_key):
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM sync_tree_barriers WHERE local_key = ?",
                (local_key,),
            ).fetchone()
            return dict(row) if row else None

    def adopt_pristine_document_identity(
        self, local_key, local_path, remote_document_id
    ):
        """Replace an unused local UUID only when it has no sync history/state."""
        remote_document_id = str(uuid.UUID(str(remote_document_id)))
        now = _utc_now()
        with self._transaction() as connection:
            local = connection.execute(
                "SELECT * FROM sync_documents WHERE local_key = ? AND local_path = ?",
                (local_key, local_path),
            ).fetchone()
            if local is None:
                return False
            if local["document_id"] == remote_document_id:
                return True
            remote_existing = connection.execute(
                "SELECT 1 FROM sync_documents WHERE document_id = ?",
                (remote_document_id,),
            ).fetchone()
            history = connection.execute(
                "SELECT status FROM sync_operations WHERE document_id = ? LIMIT 1",
                (local["document_id"],),
            ).fetchone()
            if (
                remote_existing is not None
                or int(local["revision"] or 0) != 0
                or local["sync_state"] not in {"local", "synced"}
                or local["last_error"]
                or any(local[name] is not None for name in (
                    "conflict_base", "conflict_local", "conflict_remote",
                    "conflict_merged",
                ))
                or history is not None
            ):
                return False
            connection.execute(
                "UPDATE sync_documents SET document_id = ?, updated_at = ? "
                "WHERE document_id = ?",
                (remote_document_id, now, local["document_id"]),
            )
            return True
