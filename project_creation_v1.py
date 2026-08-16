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
    relocate_nodes,
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

# The canonical user tree: 메인 plus the eight folders a writer works in.
CANONICAL_USER_FOLDERS = (
    "메인",
    "메인/원고",
    "메인/캐릭터",
    "메인/설정집",
    "메인/메모장",
    "메인/스토리 플롯",
    "메인/흐름정리",
    "메인/복선",
    "메인/장소",
)

# 휴지통 is not a folder the writer authored, but trashed items keep their UUIDs
# and still need a valid parent_uuid, so it is the one machine-managed directory
# that carries identity. Backups therefore preserve trashed manuscript bytes.
TRASH_PATH = "메인/휴지통"

STANDARD_FOLDERS = CANONICAL_USER_FOLDERS + (TRASH_PATH,)

# Created on disk but never given a UUID.
UNTRACKED_FOLDERS = (
    "백업/자동저장",
    "백업/전환직전",
    "백업/충돌",
    "백업/복원전",
)

# App state that lives beside the user tree and is not a binder node.
UNTRACKED_FILES = (
    "설정.json",
    ".server-project-import.json",
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


def _is_empty_dir(path):
    return os.path.isdir(path) and not os.listdir(path)


def _standard_folder_nodes(uuid_factory):
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
    return nodes


def _start_project_transaction(workspace, title, project_root, staging,
                               uuid_factory):
    transaction_id = _new_uuid(uuid_factory)
    project_uuid = _new_uuid(uuid_factory)
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
            "nodes": _standard_folder_nodes(uuid_factory),
        },
    )
    _finish_create_project(_read_json(journal), journal)
    return read_identity(project_root)


def create_project(workspace, title, uuid_factory=None):
    """Create a project whose standard folders already have UUIDs on return."""
    project_root = os.path.join(workspace, title)
    if os.path.exists(project_root):
        raise CreationError(f"project already exists: {project_root}")

    transaction_id = _new_uuid(uuid_factory)
    staging = os.path.join(workspace, PROJECT_STAGING_DIRNAME, transaction_id)
    return _start_project_transaction(
        workspace, title, project_root, staging, uuid_factory
    )


def initialize_existing_project(workspace, title, uuid_factory=None):
    """Give an already-reserved project directory its standard folders and identity.

    Import flows must create the destination first so they can write their own
    marker, so they cannot hand over an empty directory for the staging rename.
    The journal still records every UUID before a single folder is created, so
    an interrupted import resumes with the same ids rather than new ones.
    """
    project_root = os.path.join(workspace, title)
    if os.path.exists(identity_path(project_root)):
        return read_identity(project_root)
    return _start_project_transaction(
        workspace, title, project_root, None, uuid_factory
    )


def _finish_create_project(entry, journal_path):
    """Complete or roll back one create_project transaction. Safe to repeat."""
    project_root = entry["target_path"]
    staging = entry.get("staging_path")

    if os.path.exists(identity_path(project_root)):
        # The transaction already landed. Confirm the journalled ids are there.
        identity = read_identity(project_root)
        if identity["project"]["uuid"] != entry["project"]["uuid"]:
            raise CreationError(
                f"{project_root} exists with a different project uuid; "
                "manual recovery required"
            )
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
        os.unlink(journal_path)
        return identity

    if staging and os.path.exists(project_root):
        raise CreationError(
            f"{project_root} exists without an identity file; "
            "manual recovery required"
        )

    # With staging the tree is built aside and lands with one rename. Without it
    # the caller already reserved the directory and owns the other files in it.
    build_root = staging or project_root
    build_writing_root = writing_root(build_root)
    os.makedirs(build_writing_root, exist_ok=True)
    _materialize(build_writing_root, entry["nodes"])
    for legacy_path in UNTRACKED_FOLDERS:
        os.makedirs(
            os.path.join(build_writing_root, legacy_path.replace("/", os.sep)),
            exist_ok=True,
        )

    write_identity(
        build_root,
        {
            "format_version": 1,
            "project": entry["project"],
            "nodes": entry["nodes"],
        },
        overwrite=True,
    )

    _verify(build_writing_root, read_identity(build_root)["nodes"])

    if staging:
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


OPEN_OK = "ok"
OPEN_LEGACY = "legacy"
OPEN_BLOCKED = "blocked"


def ensure_machine_folders(project_root):
    """Recreate only the machine-managed directories.

    These carry no UUID and never appear in the sync tree or in a backup
    manifest, so remaking them is safe. User folders are never recreated here:
    a missing one is an audit error, not something to paper over.
    """
    root = writing_root(project_root)
    for legacy_path in UNTRACKED_FOLDERS:
        os.makedirs(os.path.join(root, legacy_path.replace("/", os.sep)), exist_ok=True)


def prepare_open(project_root):
    """Decide whether a project may be opened. Creates no user folder or UUID.

    Order: finish any pending journal, validate the identity file, audit it
    against the file tree, and only then report ``OPEN_OK``. A project with no
    identity file is reported as legacy and left untouched — importing it is an
    explicit user action, never a side effect of opening.
    """
    if not os.path.exists(identity_path(project_root)):
        return {
            "status": OPEN_LEGACY,
            "reason": "레거시 프로젝트 — 명시적 가져오기/마이그레이션 필요",
            "audit": None,
        }

    try:
        recover_project(project_root)
        read_identity(project_root)
        report = audit(project_root)
    except (IdentityError, CreationError, OSError) as error:
        return {"status": OPEN_BLOCKED, "reason": str(error), "audit": None}

    if any(report[key] for key in report):
        return {
            "status": OPEN_BLOCKED,
            "reason": "identity와 파일 트리가 일치하지 않는다",
            "audit": report,
        }
    return {"status": OPEN_OK, "reason": "", "audit": report}


def create_item_at_path(project_root, parent_legacy_path, base_name, is_folder,
                        uuid_factory=None):
    """create_item addressed by the parent's path instead of its uuid."""
    parent = _node_by_path(read_identity(project_root), parent_legacy_path)
    return create_item(
        project_root, parent["uuid"], base_name, is_folder, uuid_factory
    )


def node_for_path(project_root, legacy_path):
    """Return the identity node at ``legacy_path``, or None."""
    for node in read_identity(project_root)["nodes"]:
        if node["legacy_path"] == legacy_path:
            return node
    return None


def next_order_under(project_root, parent_uuid):
    return _next_order(read_identity(project_root)["nodes"], parent_uuid)


def journalled_relocate(project_root, moves, apply_filesystem):
    """Move files and their identity entries as one recoverable transaction.

    No UUID is issued here, so recovery is simpler than for a creation: the
    journal records the intended final paths, ``apply_filesystem`` performs the
    move, and identity follows. If the process stops in between, recovery looks
    at where the files actually are — present at the target means finish the
    identity update, absent means the move never happened and the journal is
    dropped without touching anything.
    """
    transaction_id = _new_uuid(None)
    journal = os.path.join(
        project_journal_dir(project_root), f"{transaction_id}.json"
    )
    _write_json_atomic(
        journal,
        {
            "format_version": JOURNAL_VERSION,
            "transaction_id": transaction_id,
            "kind": "relocate",
            "project_root": project_root,
            "moves": [dict(move) for move in moves],
        },
    )
    try:
        result = apply_filesystem()
    except BaseException:
        os.unlink(journal)
        raise

    identity = relocate_nodes(project_root, moves)
    os.unlink(journal)
    return identity, result


def _finish_relocate(entry, journal_path):
    project_root = entry["project_root"]
    root = writing_root(project_root)
    landed = [
        move
        for move in entry["moves"]
        if os.path.exists(
            os.path.join(root, move["legacy_path"].replace("/", os.sep))
        )
    ]
    if landed:
        relocate_nodes(project_root, landed)
    os.unlink(journal_path)
    return landed


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
    """Resume interrupted folder/document/volume/relocate transactions."""
    results = []
    for journal_path, entry in list_journals(project_journal_dir(project_root)):
        if entry.get("kind") == "relocate":
            _finish_relocate(entry, journal_path)
        else:
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
            if name in UNTRACKED_FILES or name.startswith("."):
                continue
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
