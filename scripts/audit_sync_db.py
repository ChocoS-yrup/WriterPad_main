"""Metadata-only sync SQLite audit; never prints document paths or bodies."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata(connection: sqlite3.Connection) -> dict:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    def count(table, where="1 = 1"):
        if table not in tables:
            return 0
        return int(connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}"
        ).fetchone()[0])

    return {
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "projects": count("sync_projects"),
        "documents": count("sync_documents"),
        "operations": count("sync_operations"),
        "legacy_epoch_zero_projects": count(
            "sync_projects",
            "project_sync_mode = 'LEGACY' AND migration_epoch = 0 "
            "AND active_contract_sha256 IS NULL",
        ) if "project_sync_mode" in {
            row[1] for row in connection.execute("PRAGMA table_info(sync_projects)")
        } else 0,
        "legacy_epoch_zero_operations": count(
            "sync_operations",
            "provenance_kind = 'LEGACY_EPOCH_0' AND sync_protocol_version = 2 "
            "AND contract_version IS NULL AND batch_id IS NULL",
        ) if "provenance_kind" in {
            row[1] for row in connection.execute("PRAGMA table_info(sync_operations)")
        } else 0,
        "operation_events": count("sync_operation_events"),
        "operation_attempts": count("sync_operation_attempts"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--migrate-copy",
        action="store_true",
        help="Apply the Stage 8 additive migration. Use only on a disposable copy.",
    )
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    before_size = database.stat().st_size
    before_hash = file_sha256(database)

    if args.migrate_copy:
        from sync_v2_store import SyncV2Store

        SyncV2Store(str(database))
        SyncV2Store(str(database))
        connection = sqlite3.connect(str(database))
    else:
        uri = database.as_uri() + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)

    try:
        result = metadata(connection)
    finally:
        connection.close()

    print(f"path={database}")
    print(f"size_before={before_size}")
    print(f"sha256_before={before_hash}")
    for key, value in result.items():
        print(f"{key}={value}")
    print(f"size_after={database.stat().st_size}")
    print(f"sha256_after={file_sha256(database)}")


if __name__ == "__main__":
    main()
