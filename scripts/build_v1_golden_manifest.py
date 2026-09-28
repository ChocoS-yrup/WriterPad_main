"""Build the v1 golden manifest for the Python 3.14 transition (stage B-1).

Run under Python 3.11 with unicodedata2 15.0.0 installed. That pairing is the
last working v1 reference implementation; once 3.11 goes away the answers this
script records cannot be produced again.

Reads only. Server rows arrive as JSON dumps taken with the Supabase CLI, the
local store is opened read-only, and the filesystem walk never writes.

Manuscript titles are user data. The full manifest carries them and belongs in
_evidence, which is gitignored. The summary carries counts and digests only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_contract import (  # noqa: E402
    SyncContractError,
    normalize_storage_name,
    normalize_storage_name_v2,
)

LOCAL_DB = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "AntigravityWriter", "sync_v2.sqlite3"
)
MANUSCRIPT_DIRS = ("작품목록", "메인", "백업", "공유", "saves")


def stored_hex(value):
    """Return the hex of a bytea that the CLI serialised as a JSON buffer."""
    if value is None:
        return None
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        return bytes(value["data"]).hex()
    raise ValueError(f"unexpected storage_name_key shape: {type(value).__name__}")


def local_stored_hex(value):
    """Return the hex of a local key, which sqlite holds as normalised text.

    The server stores the same key as bytea. Both sides are reduced to hex here
    so a comparison against the recomputed key means the same thing either way.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.encode("utf-8").hex()
    if isinstance(value, bytes):
        return value.hex()
    raise ValueError(f"unexpected local storage_name_key: {type(value).__name__}")


def apply(normalizer, name):
    try:
        result = normalizer(name)
    except SyncContractError as error:
        return {"normalized": None, "key_hex": None, "reject_code": error.code}
    return {
        "normalized": result.normalized,
        "key_hex": result.utf8_hex,
        "reject_code": None,
    }


def record(source, scope, parent, record_id, name, stored, is_deleted=False):
    v1 = apply(normalize_storage_name, name)
    v2 = apply(normalize_storage_name_v2, name)
    return {
        "source": source,
        "scope_id": scope,
        "parent_folder_id": parent,
        "record_id": record_id,
        "is_deleted": bool(is_deleted),
        "name": name,
        "stored_key_hex": stored,
        "v1_normalized": v1["normalized"],
        "v1_key_hex": v1["key_hex"],
        "v1_reject_code": v1["reject_code"],
        "v2_normalized": v2["normalized"],
        "v2_key_hex": v2["key_hex"],
        "v2_reject_code": v2["reject_code"],
        "stored_matches_v1": None if stored is None else stored == v1["key_hex"],
        "stored_matches_v2": None if stored is None else stored == v2["key_hex"],
    }


def read_server(dump_dir):
    out = []
    for filename, kind, id_field in (
        ("server_folders.json", "server_folder", "folder_id"),
        ("server_documents.json", "server_document", "document_id"),
    ):
        path = os.path.join(dump_dir, filename)
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)
        for row in rows:
            if row.get("name") is None:
                continue
            out.append(
                record(
                    kind,
                    row.get("project_id"),
                    row.get("parent_folder_id"),
                    row.get(id_field),
                    row["name"],
                    stored_hex(row.get("storage_name_key")),
                    row.get("is_deleted"),
                )
            )
    return out


def read_local_store(db_path):
    if not os.path.exists(db_path):
        return []
    uri = "file:{}?mode=ro".format(db_path.replace("\\", "/"))
    connection = sqlite3.connect(uri, uri=True)
    connection.text_factory = str
    out = []
    try:
        for table, kind, id_field in (
            ("sync_folders", "local_folder", "folder_id"),
            ("sync_documents", "local_document", "document_id"),
        ):
            query = (
                f"select {id_field}, local_key, parent_folder_id, name, storage_name_key, "
                f"is_deleted from {table}"
            )
            for row_id, local_key, parent, name, stored, deleted in connection.execute(query):
                if name is None:
                    continue
                out.append(
                    record(
                        kind,
                        local_key,
                        parent,
                        row_id,
                        name,
                        local_stored_hex(stored),
                        deleted,
                    )
                )
    finally:
        connection.close()
    return out


def read_filesystem(root):
    out = []
    for top in MANUSCRIPT_DIRS:
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            parent = os.path.relpath(dirpath, root).replace("\\", "/")
            for entry in sorted(dirnames) + sorted(filenames):
                out.append(
                    record(
                        "filesystem",
                        top,
                        parent,
                        f"{parent}/{entry}",
                        entry,
                        None,
                    )
                )
    return out


def read_vectors(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    out = []
    for vector in payload["vectors"]:
        result = apply(normalize_storage_name, vector["input"])
        expected_valid = bool(vector["valid"])
        actual_valid = result["reject_code"] is None
        matches = actual_valid == expected_valid and (
            not expected_valid
            or (
                result["normalized"] == vector.get("normalized")
                and result["key_hex"] == vector.get("utf8_hex")
            )
        )
        out.append(
            {
                "vector_id": vector["vector_id"],
                "expected_valid": expected_valid,
                "actual_valid": actual_valid,
                "matches": matches,
                "reject_code": result["reject_code"],
            }
        )
    return payload, out


def sibling_collisions(records, key_field, live_only=True):
    """Count sibling keys shared by more than one record.

    Folders and documents share one namespace per parent, which is how
    validate_project_sync_migration counts them on the server. Soft-deleted rows
    keep their old name, so a tombstone sitting beside its live replacement is
    not a collision; live_only drops them.
    """
    groups = defaultdict(list)
    for item in records:
        if item[key_field] is None:
            continue
        if live_only and item["is_deleted"]:
            continue
        groups[(item["source"].split("_")[0], item["scope_id"], item["parent_folder_id"])].append(
            item[key_field]
        )
    pairs = set()
    for group, keys in groups.items():
        for key, count in Counter(keys).items():
            if count > 1:
                pairs.add((group, key))
    return pairs


def sorted_name_digest(names):
    digest = hashlib.sha256()
    for raw in sorted(name.encode("utf-8") for name in names):
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--live-read-at", required=True)
    arguments = parser.parse_args()

    import unicodedata2

    if unicodedata2.unidata_version != "15.0.0":
        raise SystemExit(
            f"v1 reference needs Unicode 15.0.0; got {unicodedata2.unidata_version}"
        )

    records = (
        read_server(arguments.dump_dir)
        + read_local_store(LOCAL_DB)
        + read_filesystem(arguments.root)
    )
    records.sort(key=lambda item: (item["source"], str(item["scope_id"]), str(item["record_id"])))

    os.makedirs(arguments.out_dir, exist_ok=True)
    manifest_path = os.path.join(arguments.out_dir, "manifest.jsonl")
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    with open(manifest_path, "rb") as handle:
        manifest_sha256 = hashlib.sha256(handle.read()).hexdigest()

    v1_rejected = [item for item in records if item["v1_reject_code"]]
    v2_rejected = [item for item in records if item["v2_reject_code"]]
    newly_rejected = [
        item for item in records if item["v2_reject_code"] and not item["v1_reject_code"]
    ]
    identical = [
        item
        for item in records
        if item["v1_key_hex"] is not None and item["v1_key_hex"] == item["v2_key_hex"]
    ]
    differing = [
        item
        for item in records
        if item["v1_key_hex"] is not None
        and item["v2_key_hex"] is not None
        and item["v1_key_hex"] != item["v2_key_hex"]
    ]
    both_rejected = [
        item for item in records if item["v1_reject_code"] and item["v2_reject_code"]
    ]

    stored = [item for item in records if item["stored_key_hex"] is not None]
    mismatch_v1 = [item for item in stored if not item["stored_matches_v1"]]
    mismatch_v2 = [item for item in stored if not item["stored_matches_v2"]]

    collisions_v1 = sibling_collisions(records, "v1_key_hex")
    collisions_v2 = sibling_collisions(records, "v2_key_hex")
    collisions_v1_all = sibling_collisions(records, "v1_key_hex", live_only=False)
    collisions_v2_all = sibling_collisions(records, "v2_key_hex", live_only=False)

    names = {item["name"] for item in records}
    by_name = {item["name"]: item for item in records}
    vector_payload, vector_results = read_vectors(
        os.path.join(
            arguments.root, "sync-contract", "conformance_vectors", "storage-name-v1.json"
        )
    )
    vectors_passed = sum(1 for item in vector_results if item["matches"])

    summary = {
        "stage": "python-314-local-implementation-20260919 / B-1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_read_at": arguments.live_read_at,
        "record_count": len(records),
        "distinct_names": len(names),
        "by_source": dict(Counter(item["source"] for item in records)),
        "v1_v2_identical": len(identical),
        "v1_v2_differing": len(differing),
        "v1_rejected": len(v1_rejected),
        "v2_rejected": len(v2_rejected),
        "both_rejected": len(both_rejected),
        "v2_newly_rejected": len(newly_rejected),
        "stored_key_count": len(stored),
        "stored_key_mismatch_v1": len(mismatch_v1),
        "stored_key_mismatch_v2": len(mismatch_v2),
        "sibling_collisions_v1": len(collisions_v1),
        "sibling_collisions_v2": len(collisions_v2),
        "new_collisions": len(collisions_v2 - collisions_v1),
        "sibling_collisions_v1_including_deleted": len(collisions_v1_all),
        "sibling_collisions_v2_including_deleted": len(collisions_v2_all),
        "deleted_record_count": sum(1 for item in records if item["is_deleted"]),
        "contract_0_2_0_vectors_total": len(vector_results),
        "contract_0_2_0_vectors_passed": vectors_passed,
        "contract_0_2_0_vector_contract_version": vector_payload.get("contract_version"),
        "contract_0_3_0_vectors_total": 0,
        "contract_0_3_0_note": "0.3.0 vector asset is not present in this repository",
        "manifest_sha256": manifest_sha256,
        "sorted_name_digest_algorithm": (
            "이름 UTF-8 bytes 를 bytewise 오름차순 정렬, 각 항목 앞 4-byte big-endian 길이, "
            "이어 쓴 뒤 SHA-256"
        ),
        "sorted_name_sha256": sorted_name_digest(names),
        "generator_python": sys.version.split()[0],
        "generator_unicodedata2": unicodedata2.unidata_version,
        "generator_stdlib_unicodedata": __import__("unicodedata").unidata_version,
    }

    summary_path = os.path.join(arguments.out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    # The same JSON shape the contract uses for its own vectors. Once 3.11 and
    # unicodedata2 are gone this file is what v1's answers survive as: pure data,
    # replayable without a Python that can still run the v1 implementation.
    golden = {
        "$schema": "../../sync-contract/storage-name-vectors.schema.json",
        "contract_version": "0.2.0",
        "algorithm_id": "storage-name-v1",
        "unicode_version": unicodedata2.unidata_version,
        "vectors": [
            {
                "vector_id": f"GOLDEN-{index:04d}",
                "input": name,
                "valid": by_name[name]["v1_reject_code"] is None,
                **(
                    {
                        "normalized": by_name[name]["v1_normalized"],
                        "utf8_hex": by_name[name]["v1_key_hex"],
                    }
                    if by_name[name]["v1_reject_code"] is None
                    else {"error_code": by_name[name]["v1_reject_code"]}
                ),
            }
            for index, name in enumerate(sorted(names), start=1)
        ],
    }
    golden_path = os.path.join(arguments.out_dir, "v1-golden-vectors.json")
    with open(golden_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(golden, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with open(golden_path, "rb") as handle:
        summary["v1_golden_vectors_sha256"] = hashlib.sha256(handle.read()).hexdigest()
    summary["v1_golden_vectors_count"] = len(golden["vectors"])

    with open(summary_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    detail_path = os.path.join(arguments.out_dir, "vectors-0.2.0.json")
    with open(detail_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(vector_results, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    for key in sorted(summary):
        print(f"{key}: {summary[key]}")


if __name__ == "__main__":
    main()
