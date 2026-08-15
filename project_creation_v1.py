"""Create projects, folders and documents with UUIDs issued before any file.

Every creation runs as a journalled transaction:

1. All UUIDs for the command are issued up front, in memory.
2. A durable journal records those UUIDs, parents, order and target paths.
3. Files are created, and the identity file is written or replaced atomically.
4. Files and identity are read back and compared.
5. The journal is removed.

If the app stops anywhere in the middle, recovery resumes **the journalled
transaction with the journalled UUIDs** or rolls it back. A UUID is never
reissued. A mismatch with no journal behind it is reported, never guessed at.

Paths recorded as ``legacy_path`` are relative to the writing root
(``<project-root>/집필모드``); the identity file itself lives at
``<project-root>/.writerpad/identity-v1.json``.
"""

import json
import os
import shutil
import tempfile
import uuid as uuid_module

from project_identity_v1 import (
    IdentityError,
    append_nodes,
    identity_path,
    read_identity,
    write_identity,
)

JOURNAL_VERSION = 1

WRITING_ROOT_NAME = "집필모드"
PROJECT_JOURNAL_DIRNAME = ".writerpad-journal"
PROJECT_STAGING_DIRNAME = ".writerpad-staging"
ITEM_JOURNAL_DIRNAME = "journal"

MANUSCRIPT_PATH = "메인/원고"
CHAPTERS_PER_VOLUME = 25

# Standard folders that carry identity. 백업/* and 메인/휴지통 are deliberately
# excluded: they are machine-managed and must not consume user-facing UUIDs.
STANDARD_FOLDERS = (
    "메인",
    "메인/원고",
    "메인/캐릭터",
    "메인/설정집",
    "메인/메모장",
    "메인/플롯",
    "메인/흐름정리",
    "메인/복선",
    "메인/장소",
)

# Created on disk but never given a UUID.
UNTRACKED_FOLDERS = (
    "메인/휴지통",
    "백업/자동저장",
    "백업/전환직전",
    "백업/충돌",
    "백업/복원전",
)


class CreationError(Exception):
    """A creation transaction cannot proceed or cannot be recovered safely."""


def writing_root(project_root):
    return os.path.join(project_root, WRITING_ROOT_NAME)


def workspace_journal_dir(workspace):
    return os.path.join(workspace, PROJECT_JOURNAL_DIRNAME)


def project_journal_dir(project_root):
    return os.path.join(project_root, ".writerpad", ITEM_JOURNAL_DIRNAME)


def _new_uuid(factory):
    return str(factory() if factory else uuid_module.uuid4())


def _write_json_atomic(path, payload):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle_fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".txn-", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def list_journals(directory):
    if not os.path.isdir(directory):
        return []
    found = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            found.append((path, _read_json(path)))
        except (json.JSONDecodeError, OSError) as error:
            raise CreationError(f"unreadable journal {path}: {error}") from error
    return found


def _next_order(nodes, parent_uuid):
    orders = [
        int(node["order"]) for node in nodes if node["parent_uuid"] == parent_uuid
    ]
    return max(orders) + 1 if orders else 0


def _node_by_uuid(identity, node_uuid):
    for node in identity["nodes"]:
        if node["uuid"] == node_uuid:
            return node
    raise CreationError(f"unknown parent uuid {node_uuid}")


def _node_by_path(identity, legacy_path):
    for node in identity["nodes"]:
        if node["legacy_path"] == legacy_path:
            return node
    raise CreationError(f"no node for path {legacy_path!r}")


def _unique_name(parent_dir, base_name, is_folder):
    ext = "" if is_folder else ".txt"
    name = base_name + ext
    counter = 1
    while os.path.exists(os.path.join(parent_dir, name)):
        name = f"{base_name} ({counter}){ext}"
        counter += 1
    return name


def _materialize(root, nodes):
    """Create the folders and empty documents described by ``nodes``."""
    for node in nodes:
        target = os.path.join(root, node["legacy_path"].replace("/", os.sep))
        if node["kind"] == "folder":
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not os.path.exists(target):
                with open(target, "wb"):
                    pass


def _verify(root, nodes):
    """Every journalled node must exist on disk with the right kind."""
    for node in nodes:
        target = os.path.join(root, node["legacy_path"].replace("/", os.sep))
        if node["kind"] == "folder":
            if not os.path.isdir(target):
                raise CreationError(f"missing folder for {node['uuid']}: {target}")
        elif not os.path.isfile(target):
            raise CreationError(f"missing document for {node['uuid']}: {target}")


def create_project(workspace, title, uuid_factory=None):
    """Create a project whose standard folders already have UUIDs on return."""
    project_root = os.path.join(workspace, title)
    if os.path.exists(project_root):
        raise CreationError(f"project already exists: {project_root}")

    transaction_id = _new_uuid(uuid_factory)
    project_uuid = _new_uuid(uuid_factory)

    nodes = []
    assigned = {}
    for index, legacy_path in enumerate(STANDARD_FOLDERS):
        parent_path = legacy_path.rsplit("/", 1)[0] if "/" in legacy_path else None
        node_uuid = _new_uuid(uuid_factory)
        assigned[legacy_path] = node_uuid
        nodes.append(
            {
                "uuid": node_uuid,
                "kind": "folder",
                "parent_uuid": assigned[parent_path] if parent_path else None,
                "legacy_path": legacy_path,
                "title": legacy_path.rsplit("/", 1)[-1],
                "order": index if parent_path else 0,
            }
        )

    staging = os.path.join(workspace, PROJECT_STAGING_DIRNAME, transaction_id)
    journal = os.path.join(workspace_journal_dir(workspace), f"{transaction_id}.json")
    _write_json_atomic(
        journal,
        {
            "format_version": JOURNAL_VERSION,
            "transaction_id": transaction_id,
            "kind": "create_project",
            "workspace": workspace,
            "target_path": project_root,
            "staging_path": staging,
            "project": {"uuid": project_uuid, "title": title},
            "nodes": nodes,
        },
    )

    _finish_create_project(_read_json(journal), journal)
    return read_identity(project_root)


def _finish_create_project(entry, journal_path):
    """Complete or roll back one create_project transaction. Safe to repeat."""
    project_root = entry["target_path"]
    staging = entry["staging_path"]

    if os.path.exists(project_root):
        # The rename already landed. Confirm the journalled ids are what is there.
        identity = read_identity(project_root)
        if identity["project"]["uuid"] != entry["project"]["uuid"]:
            raise CreationError(
                f"{project_root} exists with a different project uuid; "
                "manual recovery required"
            )
        shutil.rmtree(staging, ignore_errors=True)
        os.unlink(journal_path)
        return identity

    staging_writing_root = writing_root(staging)
    os.makedirs(staging_writing_root, exist_ok=True)
    _materialize(staging_writing_root, entry["nodes"])
    for legacy_path in UNTRACKED_FOLDERS:
        os.makedirs(
            os.path.join(staging_writing_root, legacy_path.replace("/", os.sep)),
            exist_ok=True,
        )

    write_identity(
        staging,
        {
            "format_version": 1,
            "project": entry["project"],
            "nodes": entry["nodes"],
        },
        overwrite=True,
    )

    _verify(staging_writing_root, read_identity(staging)["nodes"])

    os.makedirs(os.path.dirname(project_root), exist_ok=True)
    os.replace(staging, project_root)
    os.unlink(journal_path)
    return read_identity(project_root)


def _run_item_transaction(project_root, kind, nodes, uuid_factory=None,
                          transaction_id=None):
    transaction_id = transaction_id or _new_uuid(uuid_factory)
    journal = os.path.join(
        project_journal_dir(project_root), f"{transaction_id}.json"
    )
    _write_json_atomic(
        journal,
        {
            "format_version": JOURNAL_VERSION,
            "transaction_id": transaction_id,
            "kind": kind,
            "project_root": project_root,
            "nodes": nodes,
        },
    )
    return _finish_item_transaction(_read_json(journal), journal)


def _finish_item_transaction(entry, journal_path):
    """Complete one folder/document/volume transaction. Safe to repeat."""
    project_root = entry["project_root"]
    root = writing_root(project_root)
    _materialize(root, entry["nodes"])
    identity = append_nodes(project_root, entry["nodes"])
    _verify(root, entry["nodes"])
    os.unlink(journal_path)
    return identity


def create_item(project_root, parent_uuid, base_name, is_folder,
                uuid_factory=None):
    """Create one user folder or document under ``parent_uuid``."""
    identity = read_identity(project_root)
    parent = _node_by_uuid(identity, parent_uuid)
    if parent["kind"] != "folder":
        raise CreationError(f"parent {parent_uuid} is not a folder")

    parent_dir = os.path.join(
        writing_root(project_root), parent["legacy_path"].replace("/", os.sep)
    )
    name = _unique_name(parent_dir, base_name, is_folder)
    node = {
        "uuid": _new_uuid(uuid_factory),
        "kind": "folder" if is_folder else "document",
        "parent_uuid": parent_uuid,
        "legacy_path": f"{parent['legacy_path']}/{name}",
        "title": name[:-4] if not is_folder and name.endswith(".txt") else name,
        "order": _next_order(identity["nodes"], parent_uuid),
    }
    return _run_item_transaction(
        project_root, "create_item", [node], uuid_factory
    )


def create_volume(project_root, uuid_factory=None):
    """Create the next N권 folder plus its 25 empty chapters in one transaction."""
    identity = read_identity(project_root)
    manuscript = _node_by_path(identity, MANUSCRIPT_PATH)

    used = set()
    for node in identity["nodes"]:
        if node["parent_uuid"] == manuscript["uuid"] and node["title"].endswith("권"):
            head = node["title"][:-1]
            if head.isdigit():
                used.add(int(head))
    volume_number = max(used) + 1 if used else 1
    volume_name = f"{volume_number}권"

    volume_uuid = _new_uuid(uuid_factory)
    nodes = [
        {
            "uuid": volume_uuid,
            "kind": "folder",
            "parent_uuid": manuscript["uuid"],
            "legacy_path": f"{MANUSCRIPT_PATH}/{volume_name}",
            "title": volume_name,
            "order": _next_order(identity["nodes"], manuscript["uuid"]),
        }
    ]
    first = (volume_number - 1) * CHAPTERS_PER_VOLUME + 1
    for offset in range(CHAPTERS_PER_VOLUME):
        chapter = first + offset
        nodes.append(
            {
                "uuid": _new_uuid(uuid_factory),
                "kind": "document",
                "parent_uuid": volume_uuid,
                "legacy_path": f"{MANUSCRIPT_PATH}/{volume_name}/{chapter:03d}화.txt",
                "title": f"{chapter:03d}화",
                "order": offset,
            }
        )
    return _run_item_transaction(project_root, "create_volume", nodes, uuid_factory)


def recover_workspace(workspace):
    """Resume or roll back interrupted create_project transactions."""
    results = []
    for journal_path, entry in list_journals(workspace_journal_dir(workspace)):
        if entry.get("kind") != "create_project":
            raise CreationError(f"unexpected journal kind in {journal_path}")
        _finish_create_project(entry, journal_path)
        results.append((entry["transaction_id"], entry["target_path"]))
    return results


def recover_project(project_root):
    """Resume interrupted folder/document/volume transactions for one project."""
    results = []
    for journal_path, entry in list_journals(project_journal_dir(project_root)):
        _finish_item_transaction(entry, journal_path)
        results.append((entry["transaction_id"], entry["kind"]))
    return results


def audit(project_root):
    """Report identity/filesystem divergence. Never repairs anything.

    Callers must treat a non-empty result as a stop condition: with no journal
    to resume from, guessing at the right UUID is exactly what the shared
    contract forbids.
    """
    identity = read_identity(project_root)
    root = writing_root(project_root)

    missing_on_disk = []
    for node in identity["nodes"]:
        target = os.path.join(root, node["legacy_path"].replace("/", os.sep))
        exists = os.path.isdir(target) if node["kind"] == "folder" else os.path.isfile(
            target
        )
        if not exists:
            missing_on_disk.append(node["legacy_path"])

    known = {node["legacy_path"] for node in identity["nodes"]}
    skip = set(UNTRACKED_FOLDERS)
    missing_in_identity = []
    for current, directories, files in os.walk(root):
        relative = os.path.relpath(current, root).replace(os.sep, "/")
        if relative == ".":
            relative = ""
        if any(part in skip for part in (relative, relative.split("/")[0])):
            directories[:] = []
            continue
        for name in list(directories) + files:
            candidate = f"{relative}/{name}" if relative else name
            if candidate in skip or candidate.split("/")[0] in ("백업",):
                continue
            if candidate not in known:
                missing_in_identity.append(candidate)

    return {
        "missing_on_disk": sorted(missing_on_disk),
        "missing_in_identity": sorted(missing_in_identity),
        "pending_journals": [
            entry["transaction_id"]
            for _, entry in list_journals(project_journal_dir(project_root))
        ],
    }
