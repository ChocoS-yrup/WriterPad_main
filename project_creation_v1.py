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
import threading
import unicodedata
import uuid as uuid_module

from binder_order import (
    canonical_manuscript_children,
    canonical_root_storage_name,
)
from project_identity_v1 import (
    BLOCKING_BUCKETS,
    KIND_DOCUMENT,
    KIND_FOLDER,
    KINDS,
    IdentityError,
    plan_identity,
    append_nodes,
    relocate_nodes,
    remove_nodes,
    identity_node,
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

# Paths the sync layer owns. They hold protocol state, not manuscript, so they
# carry no UUID and identity must never name them. Nothing here creates them
# either: unlike UNTRACKED_FOLDERS these are not part of a project's shape, and
# a project that has never synced has none of them. They are listed so a stray
# one on disk is skipped rather than read as identity divergence, which would
# otherwise refuse to open the whole project over a file the writer cannot see.
SYNC_INTERNAL_ROOTS = (
    "__antigravity__",
)


def is_sync_internal_path(relative_path):
    """Whether a writing-root path belongs to the sync layer, not the binder.

    One rule, so the audit that skips these and the writer that refuses them
    cannot drift apart. A second copy of the rule is how a path ends up
    skipped by one side and written by the other.
    """
    path = str(relative_path or "").replace("\\", "/").strip("/")
    if not path:
        return False
    return path.split("/")[0] in SYNC_INTERNAL_ROOTS


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
    """Build the standard tree with order counted per parent.

    Order is only meaningful among siblings, so 메인's children start at 0.
    Numbering them from a single index over the whole list gave 1~8 here and
    disagreed with the iPad for the same logical tree.
    """
    nodes = []
    assigned = {}
    next_order = {}
    for legacy_path in STANDARD_FOLDERS:
        parent_path = legacy_path.rsplit("/", 1)[0] if "/" in legacy_path else None
        parent_uuid = assigned[parent_path] if parent_path else None
        node_uuid = _new_uuid(uuid_factory)
        assigned[legacy_path] = node_uuid
        order = next_order.get(parent_uuid, 0)
        next_order[parent_uuid] = order + 1
        nodes.append(
            {
                "uuid": node_uuid,
                "kind": "folder",
                "parent_uuid": parent_uuid,
                "legacy_path": legacy_path,
                "title": legacy_path.rsplit("/", 1)[-1],
                "order": order,
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
            # Normalize here so every node in the file has the same shape,
            # whoever assembled it.
            "nodes": [identity_node(node) for node in entry["nodes"]],
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


def adopt_remote_nodes(project_root, entries, uuid_factory=None):
    """Record the ids a peer already issued for items a pull is materializing.

    Each entry carries ``legacy_path``, ``kind`` and the ``uuid`` the server
    proved for that path. That uuid is used exactly as given: a snapshot that
    arrives with a folder id is that folder, and minting a second id for it
    here is what left the file tree and identity disagreeing after a pull.
    Only a path no peer has an id for gets a generated one, which is the same
    thing that happens when the writer creates it locally.

    A path identity already knows keeps the id it already has, so an
    interrupted pull can repeat the whole batch. It is never re-pointed at the
    id the snapshot carries: a recorded id is the one other devices, backups
    and journals already refer to. When the two disagree, that is a divergence
    to report — ``identity_uuid_conflicts`` names them — and never something to
    resolve by overwriting one side here.

    A parent that identity does not know, a kind that disagrees with the
    recorded one, or an id already recorded at another path is refused: the
    pull must be deferred rather than guessed at.

    Nodes are created through the ordinary journalled transaction, so the ids
    are durable before the first directory or file exists.
    """
    if not os.path.exists(identity_path(project_root)):
        return []

    identity = read_identity(project_root)
    by_path = {node["legacy_path"]: node for node in identity["nodes"]}
    by_uuid = {node["uuid"]: node for node in identity["nodes"]}
    next_order = {}
    for node in identity["nodes"]:
        parent = node["parent_uuid"]
        next_order[parent] = max(
            next_order.get(parent, 0), int(node["order"]) + 1
        )

    pending = []
    # Shallowest first: a node cannot be recorded before its parent, and the
    # batch may carry both.
    for entry in sorted(
        entries, key=lambda item: str(item.get("legacy_path") or "").count("/")
    ):
        legacy_path = str(entry.get("legacy_path") or "")
        kind = str(entry.get("kind") or "")
        if not legacy_path or kind not in KINDS:
            raise CreationError(f"unusable remote node {entry!r}")

        existing = by_path.get(legacy_path)
        if existing is not None:
            if existing["kind"] != kind:
                raise CreationError(
                    f"{legacy_path!r} is recorded as a {existing['kind']}, "
                    f"the snapshot calls it a {kind}"
                )
            continue

        parent_uuid = None
        if "/" in legacy_path:
            parent_path = legacy_path.rsplit("/", 1)[0]
            parent = by_path.get(parent_path)
            if parent is None or parent["kind"] != KIND_FOLDER:
                raise CreationError(
                    f"no folder identity for the parent of {legacy_path!r}"
                )
            parent_uuid = parent["uuid"]

        node_uuid = str(entry.get("uuid") or "") or _new_uuid(uuid_factory)
        clashing = by_uuid.get(node_uuid)
        if clashing is not None:
            raise CreationError(
                f"{node_uuid} is already recorded at "
                f"{clashing['legacy_path']!r}, not {legacy_path!r}"
            )

        order = next_order.get(parent_uuid, 0)
        next_order[parent_uuid] = order + 1
        node = identity_node({
            "uuid": node_uuid,
            "kind": kind,
            "parent_uuid": parent_uuid,
            "legacy_path": legacy_path,
            "order": order,
        })
        by_path[legacy_path] = node
        by_uuid[node_uuid] = node
        pending.append(node)

    if not pending:
        return []

    root = writing_root(project_root)
    created = [
        node for node in pending
        if not os.path.lexists(
            os.path.join(root, node["legacy_path"].replace("/", os.sep))
        )
    ]
    transaction_id = _new_uuid(uuid_factory)
    try:
        _run_item_transaction(
            project_root, "adopt_remote", pending, uuid_factory, transaction_id
        )
    except BaseException:
        _discard_adoption(project_root, transaction_id, created)
        raise
    return pending


def identity_uuid_conflicts(project_root, entries):
    """Report entries whose path identity knows under a different id.

    Adoption skips a path it already knows, which is right — the recorded id is
    the one everything else already refers to — but skipping quietly is what
    let a folder keep one uuid here and another on the server. Nothing here
    writes; the caller decides that a pull carrying such a claim cannot be
    reported as applied.
    """
    if not os.path.exists(identity_path(project_root)):
        return []
    recorded = {
        node["legacy_path"]: node
        for node in read_identity(project_root)["nodes"]
    }
    conflicts = []
    for entry in entries or ():
        legacy_path = str(entry.get("legacy_path") or "")
        proven = str(entry.get("uuid") or "")
        node = recorded.get(legacy_path)
        if not proven or node is None or node["uuid"] == proven:
            continue
        conflicts.append({
            "legacy_path": legacy_path,
            "recorded": node["uuid"],
            "proven": proven,
        })
    return conflicts


def _discard_adoption(project_root, transaction_id, created):
    """Drop a half-applied adoption instead of resuming it.

    A creation journal is resumed because only it remembers what the writer
    asked for. An adoption remembers nothing of the sort: every id in it is the
    server's, or one nothing has referenced yet, so the next pull carrying the
    same snapshot adopts exactly the same ids. Leaving the transaction pending
    would instead materialize part of a snapshot the pull rolled back.

    Only entries this transaction created are removed, only while they are
    still empty, and never once identity has recorded them.
    """
    journal = os.path.join(
        project_journal_dir(project_root), f"{transaction_id}.json"
    )
    if os.path.exists(journal):
        os.unlink(journal)
    try:
        recorded = {node["uuid"] for node in read_identity(project_root)["nodes"]}
    except (IdentityError, OSError):
        return
    root = writing_root(project_root)
    for node in reversed(created):
        if node["uuid"] in recorded:
            continue
        target = os.path.join(root, node["legacy_path"].replace("/", os.sep))
        try:
            if node["kind"] == KIND_FOLDER:
                if _is_empty_dir(target):
                    os.rmdir(target)
            elif os.path.isfile(target) and os.path.getsize(target) == 0:
                os.unlink(target)
        except OSError:
            pass


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


_IDENTITY_INDEX_CACHE = {}
_IDENTITY_INDEX_LOCK = threading.Lock()


def _nfc(value):
    return unicodedata.normalize("NFC", str(value or ""))


def _identity_index(project_root):
    """Index identity by path, re-reading only when the file itself changes.

    Sync asks for a UUID once per created item, and a full disk sweep asks once
    per file, so re-reading and re-validating the whole identity every time
    would be quadratic. Returning ``None`` means "no identity of record here",
    which is a normal answer: internal sync documents and synthetic test roots
    legitimately have none.
    """
    path = identity_path(project_root)
    try:
        stamp = os.stat(path)
        stamp = (stamp.st_mtime_ns, stamp.st_size)
    except OSError:
        return None

    with _IDENTITY_INDEX_LOCK:
        cached = _IDENTITY_INDEX_CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1]

    try:
        nodes = read_identity(project_root)["nodes"]
    except (IdentityError, OSError, ValueError):
        return None

    index = {"exact": {}, "nfc": {}}
    for node in nodes:
        index["exact"][node["legacy_path"]] = node
        index["nfc"].setdefault(_nfc(node["legacy_path"]), []).append(node)
    with _IDENTITY_INDEX_LOCK:
        _IDENTITY_INDEX_CACHE[path] = (stamp, index)
    return index


def identity_uuid_for_writing_path(writing_root_path, relative_path, kind):
    """Return the UUID of record for one path under the writing root, or None.

    ``writing_root_path`` is the sync root, so the identity file sits one level
    above it. Matching is exact first and NFC second, the same order the
    migration planner uses. An ambiguous or wrong-kind match returns ``None``
    so the caller falls back rather than binding a guessed identity.
    """
    relative_path = str(relative_path or "").replace("\\", "/").strip("/")
    if not writing_root_path or not relative_path or kind not in KINDS:
        return None
    index = _identity_index(os.path.dirname(os.path.abspath(writing_root_path)))
    if index is None:
        return None
    node = index["exact"].get(relative_path)
    if node is None:
        matches = index["nfc"].get(_nfc(relative_path)) or []
        if len(matches) != 1:
            return None
        node = matches[0]
    if node["kind"] != kind:
        return None
    return node["uuid"]


def identity_folder_nodes(writing_root_path):
    """Return identity folder nodes shallowest first, or ``[]`` when absent.

    Depth ordering lets a caller publish a parent before any of its children.
    Copies are returned so a caller can never mutate the cached index.
    """
    if not writing_root_path:
        return []
    index = _identity_index(os.path.dirname(os.path.abspath(writing_root_path)))
    if index is None:
        return []
    folders = [
        dict(node) for node in index["exact"].values()
        if node["kind"] == KIND_FOLDER
    ]
    folders.sort(
        key=lambda node: (node["legacy_path"].count("/"), node["legacy_path"])
    )
    return folders


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


RELOCATION_TARGET_RECORDED = "target_recorded"
RELOCATION_PARENT_MISSING = "parent_missing"


def relocation_blocker(project_root, source_rel, target_rel):
    """Which rule stops identity following this move, or None when none does.

    ``relocate_path`` refuses the same two cases, but only once the caller has
    already made room on disk. A caller that creates the destination first has
    to ask before it does: a refusal that arrives afterwards leaves a directory
    nothing names, which is a project that will not open.

    Nothing here writes, and a tree with no identity or a source it does not
    know is not blocked — those move on the filesystem alone.
    """
    if not os.path.exists(identity_path(project_root)):
        return None
    if node_for_path(project_root, source_rel) is None:
        return None
    if node_for_path(project_root, target_rel) is not None:
        return RELOCATION_TARGET_RECORDED
    parent_rel = target_rel.rsplit("/", 1)[0] if "/" in target_rel else None
    if parent_rel and node_for_path(project_root, parent_rel) is None:
        return RELOCATION_PARENT_MISSING
    return None


def relocate_path(project_root, source_rel, target_rel, apply_filesystem):
    """Move one node on disk and follow it in identity, keeping its UUID.

    A project with no identity file (a legacy tree, or a bare manager in a
    test) just gets the filesystem move: there is nothing to keep in sync, and
    inventing an entry here would issue a UUID outside a creation.

    A target identity already knows, or a target whose parent it does not, is
    refused before anything moves. Picking one of two nodes for one path, or
    parenting a node to nothing, is exactly the guess the shared contract
    forbids.
    """
    node = None
    if os.path.exists(identity_path(project_root)):
        node = node_for_path(project_root, source_rel)
    if node is None:
        return apply_filesystem()

    blocker = relocation_blocker(project_root, source_rel, target_rel)
    if blocker == RELOCATION_TARGET_RECORDED:
        raise CreationError(
            f"{target_rel!r} is already recorded under another uuid; "
            f"{source_rel!r} cannot be moved onto it"
        )
    if blocker == RELOCATION_PARENT_MISSING:
        raise CreationError(
            f"no folder identity for the parent of {target_rel!r}"
        )

    parent_rel = target_rel.rsplit("/", 1)[0] if "/" in target_rel else None
    parent = node_for_path(project_root, parent_rel) if parent_rel else None
    parent_uuid = parent["uuid"] if parent else None
    move = {
        "uuid": node["uuid"],
        "parent_uuid": parent_uuid,
        "legacy_path": target_rel,
        "title": target_rel.rsplit("/", 1)[-1],
    }
    if parent_uuid != node["parent_uuid"]:
        # Only an actual reparenting takes a new sibling slot. Renaming in
        # place must keep the position the writer put the item in.
        move["order"] = next_order_under(project_root, parent_uuid)
    if node["kind"] == KIND_DOCUMENT and move["title"].endswith(".txt"):
        move["title"] = move["title"][: -len(".txt")]
    _identity, result = journalled_relocate(project_root, [move], apply_filesystem)
    return result


def journalled_remove(project_root, removals, apply_filesystem):
    """Delete files and their identity entries as one recoverable transaction.

    ``removals`` carry ``uuid`` and the ``legacy_path`` being destroyed. The
    journal records both, the filesystem delete runs, and identity follows. If
    the process stops in between, recovery looks at whether the files are still
    there: gone means finish the identity removal, present means the delete
    never happened and the journal is dropped without touching anything.
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
            "kind": "remove",
            "project_root": project_root,
            "removals": [dict(removal) for removal in removals],
        },
    )
    try:
        result = apply_filesystem()
    except BaseException:
        os.unlink(journal)
        raise

    identity = remove_nodes(
        project_root, [removal["uuid"] for removal in removals]
    )
    os.unlink(journal)
    return identity, result


def release_adopted_nodes(project_root, nodes, apply_filesystem):
    """Drop adopted ids, and the entries they name only if that write landed.

    An ordinary removal deletes the files first because the writer asked for
    the deletion and identity has to follow it. Nothing asked for this one: it
    undoes an adoption a pull could not finish. So identity goes first, and the
    entries are only removed once they are nameless. Deleting them on a failed
    identity write would turn a rollback into the opposite corruption — nodes
    naming files that are gone, which is what refuses to open — while keeping
    both leaves a tree that still opens: named directories nothing has
    published yet, which the next pull reconciles.

    The journal records the intent before either step, so an interrupted
    release is something the next open sees rather than something nobody knows
    happened.
    """
    if not nodes:
        return None, apply_filesystem()

    transaction_id = _new_uuid(None)
    journal = os.path.join(
        project_journal_dir(project_root), f"{transaction_id}.json"
    )
    _write_json_atomic(
        journal,
        {
            "format_version": JOURNAL_VERSION,
            "transaction_id": transaction_id,
            "kind": "release_adopted",
            "project_root": project_root,
            "removals": [
                {"uuid": node["uuid"], "legacy_path": node["legacy_path"],
                 "kind": node["kind"]}
                for node in nodes
            ],
        },
    )
    # A failure here deliberately leaves the journal behind: identity and the
    # tree still agree, and the next open is told that this release is unfinished.
    identity = remove_nodes(project_root, [node["uuid"] for node in nodes])
    result = apply_filesystem()
    os.unlink(journal)
    return identity, result


def _finish_release_adopted(entry, journal_path):
    """Finish an interrupted release without deleting anything that holds bytes.

    Whatever identity still records stays exactly where it is — that half of
    the release simply did not happen. What identity no longer records is
    removed only while it is empty, which is all an adoption can have created.

    A delete that fails keeps the journal. Dropping it would leave a directory
    nothing names and no record that anything meant to remove it, which is a
    project that will not open and cannot say why. Repeating the removal is
    free: an entry already gone is skipped.
    """
    project_root = entry["project_root"]
    root = writing_root(project_root)
    recorded = {node["uuid"] for node in read_identity(project_root)["nodes"]}
    unfinished = False
    for removal in reversed(entry["removals"]):
        if removal["uuid"] in recorded:
            continue
        target = os.path.join(
            root, removal["legacy_path"].replace("/", os.sep)
        )
        try:
            if removal.get("kind") == KIND_FOLDER:
                if _is_empty_dir(target):
                    os.rmdir(target)
            elif os.path.isfile(target) and os.path.getsize(target) == 0:
                os.unlink(target)
        except OSError:
            # A lock or a full volume can pass. Anything this transaction is
            # not allowed to remove — a directory someone has put a file in —
            # is not an error and is left to the audit to report.
            unfinished = True
    if unfinished:
        return []
    os.unlink(journal_path)
    return entry["removals"]


def _finish_remove(entry, journal_path):
    root = writing_root(entry["project_root"])
    gone = [
        removal for removal in entry["removals"]
        if not os.path.exists(
            os.path.join(root, removal["legacy_path"].replace("/", os.sep))
        )
    ]
    if gone:
        remove_nodes(
            entry["project_root"], [removal["uuid"] for removal in gone]
        )
    os.unlink(journal_path)
    return gone


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
        kind = entry.get("kind")
        if kind == "create_project":
            _finish_create_project(entry, journal_path)
        elif kind == "restore_project":
            # Imported here so the adapter can build on this module.
            from project_backup_adapter_v1 import finish_restore

            finish_restore(entry, journal_path)
        else:
            raise CreationError(f"unexpected journal kind in {journal_path}")
        results.append((entry["transaction_id"], entry["target_path"]))
    return results


def recover_project(project_root):
    """Resume interrupted folder/document/volume/relocate transactions."""
    results = []
    for journal_path, entry in list_journals(project_journal_dir(project_root)):
        if entry.get("kind") == "relocate":
            _finish_relocate(entry, journal_path)
        elif entry.get("kind") == "remove":
            _finish_remove(entry, journal_path)
        elif entry.get("kind") == "release_adopted":
            _finish_release_adopted(entry, journal_path)
        else:
            _finish_item_transaction(entry, journal_path)
        results.append((entry["transaction_id"], entry["kind"]))
    return results


def _sibling_order(parent_path, names, order_hint):
    """Order one parent's children the way the shared tree already orders them."""
    fixed = canonical_manuscript_children(parent_path, list(names))
    if fixed is not None:
        return fixed

    hint_key = "<root>" if parent_path == "메인" else parent_path
    hinted = [str(name) for name in (order_hint or {}).get(hint_key, [])]
    if parent_path == "메인":
        hinted = [canonical_root_storage_name(name) for name in hinted]
        # A partial hint must not push the standard folders into alphabetical
        # order; they have one shared arrangement on both platforms.
        hinted.extend(
            path.rsplit("/", 1)[-1] for path in STANDARD_FOLDERS
            if path != "메인"
        )
    ordered = []
    for name in hinted:
        if name in names and name not in ordered:
            ordered.append(name)
    ordered.extend(sorted(set(names) - set(ordered)))
    return ordered


def _sync_rows_with_local_fallback(project_root, sync_rows):
    """Let ids already recorded here stand in wherever the server has none.

    A resumed import must not hand a folder the server never heard of a second
    brand new id. Only paths the server does not cover are added, because two
    candidates for one path is exactly the ambiguity the planner refuses.
    """
    rows = {key: list(value) for key, value in (sync_rows or {}).items()
            if key in ("projects", "folders", "documents")}
    rows.setdefault("projects", [])
    rows.setdefault("folders", [])
    rows.setdefault("documents", [])
    if not os.path.exists(identity_path(project_root)):
        return rows

    covered = {
        row["local_path"] for row in rows["folders"] + rows["documents"]
    }
    try:
        nodes = read_identity(project_root)["nodes"]
    except (IdentityError, OSError):
        return rows
    for node in nodes:
        if node["legacy_path"] in covered:
            continue
        if node["kind"] == KIND_FOLDER:
            rows["folders"].append({
                "folder_id": node["uuid"], "local_path": node["legacy_path"],
            })
        else:
            rows["documents"].append({
                "document_id": node["uuid"], "local_path": node["legacy_path"],
            })
    return rows


def adopt_server_identity(project_root, sync_rows, title, order_hint=None,
                          uuid_factory=None):
    """Rebuild a freshly imported project's identity from the server's ids.

    Importing used to mint brand new UUIDs for the standard folders and record
    nothing at all for the documents it pulled. The project then failed its own
    open check, because identity did not know about files that were sitting
    right there, and its folders could never be published because the server
    already held the same folders under the ids the other device had issued.

    Every id here is inherited, never invented, wherever the server has one for
    that path — the project uuid included, so it matches the server project.
    Only a path the server has never heard of gets a fresh id. The plan is
    refused outright if it is ambiguous, so an unclear tree is reported rather
    than guessed at.

    This replaces the identity file, which is safe only because the import just
    created this project moments ago and nothing has referenced those ids yet.
    Never call it on a project the writer has been working in.
    """
    root = writing_root(project_root)
    entries = sorted(tracked_tree_entries(root))
    folders = {path for path, is_folder in entries if is_folder}

    children = {}
    for path, _is_folder in entries:
        parent = path.rsplit("/", 1)[0] if "/" in path else None
        children.setdefault(parent, []).append(path)

    local_nodes = []
    for parent, paths in children.items():
        names = [path.rsplit("/", 1)[-1] for path in paths]
        ordered = _sibling_order(parent, names, order_hint)
        positions = {name: index for index, name in enumerate(ordered)}
        for path in paths:
            name = path.rsplit("/", 1)[-1]
            local_nodes.append({
                "kind": KIND_FOLDER if path in folders else KIND_DOCUMENT,
                "legacy_path": path,
                "parent_legacy_path": parent,
                "order": positions.get(name, len(positions)),
            })

    # Parents must be planned before their children can reference them.
    local_nodes.sort(key=lambda node: node["legacy_path"].count("/"))
    plan = plan_identity(
        {"title": title, "local_key": (sync_rows or {}).get("local_key")},
        local_nodes,
        _sync_rows_with_local_fallback(project_root, sync_rows),
        uuid_factory,
    )
    if plan["blocked"]:
        raise CreationError(
            "가져온 프로젝트의 정체성을 확정할 수 없습니다: "
            f"{ {name: plan['report'][name] for name in BLOCKING_BUCKETS if plan['report'][name]} }"
        )
    return write_identity(
        project_root,
        {
            "format_version": plan["format_version"],
            "project": dict(plan["project"]),
            "nodes": [dict(node) for node in plan["nodes"]],
        },
        overwrite=True,
    )


def tracked_tree_entries(root):
    """Yield ``(legacy_path, is_folder)`` for everything identity must know.

    Opening a project compares identity against this exact set, so anything
    that rebuilds identity has to walk it the same way or the project will
    refuse to open over a difference nobody can see. Both read it from here.
    """
    skip = set(UNTRACKED_FOLDERS) | set(SYNC_INTERNAL_ROOTS)
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
            if candidate in skip or candidate.split("/")[0] in (
                "백업",
            ) + SYNC_INTERNAL_ROOTS:
                continue
            yield candidate, os.path.isdir(os.path.join(current, name))


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
    missing_in_identity = [
        path for path, _is_folder in tracked_tree_entries(root)
        if path not in known
    ]

    return {
        "missing_on_disk": sorted(missing_on_disk),
        "missing_in_identity": sorted(missing_in_identity),
        "pending_journals": [
            entry["transaction_id"]
            for _, entry in list_journals(project_journal_dir(project_root))
        ],
    }
