"""Generated test data only; never reads local evidence or a server export."""
import json
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import bidirectional_sync_scope as scope


def folder_id(path):
    return str(uuid5(NAMESPACE_URL, "https://fixture.invalid/body-round-trip/" + path))


def baseline():
    paths = ["메인", "메인/원고", "메인/원고/1권", "메인/휴지통"]
    paths.extend(f"메인/합성폴더{index}" for index in range(1, 8))
    folders = []
    order = {"<root>": ["메인"]}
    for path in paths:
        parent, _, name = path.rpartition("/")
        folders.append({"folder_id": folder_id(path),
                        "parent_folder_id": folder_id(parent) if parent else None,
                        "name": name, "revision": 1, "is_deleted": False})
        order.setdefault(path, [])
        if parent:
            order[parent].append(name)
    order["메인/원고/1권"] = ["1화.txt"]
    # The wire representation omits the empty trash entry.
    order.pop("메인/휴지통")
    control = json.dumps({"tree_order": order, "version": 1}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    documents = []
    for document_id, path, content in (
        (scope.DOCUMENT_ID, scope.PATH, scope.BASE),
        (scope.CONTROL_ID, "__antigravity__/tree-order.json", control),
    ):
        documents.append({"project_id": scope.PROJECT_ID, "document_id": document_id,
                          "relative_path": path, "content": content, "revision": 1,
                          "is_deleted": False, "deleted_at": None,
                          "parent_folder_id": None, "name": None, "structure_revision": None})
    return {"project_uuid": scope.PROJECT_ID, "endpoint": scope.ENDPOINT,
            "project_sync_mode": "LEGACY", "migration_epoch": 0,
            "project_sync_settings_rows": 0, "folders": folders,
            "documents": documents, "tree_orders": []}


def pin_baseline(test):
    """Pin the generated baseline before each test mutates its independent copy.

    Only test-scoped expected hashes change. Product validation, entity-count,
    body, identity, and drift checks all execute unchanged.
    """
    snapshot = baseline()
    rows = sorted(snapshot["folders"], key=lambda row: row["folder_id"])
    folder_sha = scope.content_sha(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                              separators=(",", ":")))
    control_sha = scope.content_sha(snapshot["documents"][1]["content"])
    for target, value in (("bidirectional_sync_scope.FOLDER_BASELINE_SHA", folder_sha),
                          ("bidirectional_sync_scope.CONTROL_SHA", control_sha),
                          ("body_validation_service.CONTROL_SHA", control_sha),
                          ("body_validation_service.PARENT_ID", folder_id("메인/원고/1권"))):
        guard = patch(target, value)
        guard.start()
        test.addCleanup(guard.stop)
    return snapshot
