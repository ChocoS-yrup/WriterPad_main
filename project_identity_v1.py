"""Local UUID identity of record for a Windows project, format v1.

Every project keeps its own identity file::

    <project-root>/.writerpad/identity-v1.json

``parent_uuid`` and ``order`` in that file are the logical tree of record. The
sync database is read at most once, to inherit ids for items that were already
synced; once the identity file exists it is never consulted for identity again.

Nothing here moves, renames, reads or re-encodes a manuscript. The only file
written is the identity file itself, and it is written atomically so a failure
leaves any existing identity untouched.
"""

import json
import os
import re
import tempfile
import unicodedata
import uuid as uuid_module

FORMAT_VERSION = 1

IDENTITY_DIRNAME = ".writerpad"
IDENTITY_FILENAME = "identity-v1.json"

KIND_FOLDER = "folder"
KIND_DOCUMENT = "document"
KINDS = (KIND_FOLDER, KIND_DOCUMENT)

BLOCKING_BUCKETS = ("ambiguous_matches", "uuid_collisions", "kind_mismatches")

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


class IdentityError(Exception):
    """The identity file, the local tree or the migration plan is unusable."""


def identity_path(project_root):
    return os.path.join(project_root, IDENTITY_DIRNAME, IDENTITY_FILENAME)


def _nfc(value):
    return unicodedata.normalize("NFC", str(value or ""))


def _display_path(value):
    return _nfc(value).replace("\\", "/")


def _require_uuid(value, field):
    text = str(value or "")
    if not _UUID_PATTERN.fullmatch(text):
        raise IdentityError(
            f"{field} must be a lowercase canonical UUID, got {value!r}"
        )
    return text


def _default_title(legacy_path, kind):
    name = str(legacy_path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if kind == KIND_DOCUMENT and name.lower().endswith(".txt"):
        name = name[: -len(".txt")]
    return name


def _validate_nodes(project_uuid, nodes):
    """Reject duplicate ids, bad parents, cycles and clashing sibling order."""
    by_uuid = {}
    for node in nodes:
        node_uuid = _require_uuid(node.get("uuid"), "node.uuid")
        if node.get("kind") not in KINDS:
            raise IdentityError(f"unsupported kind {node.get('kind')!r}")
        if "parent_uuid" not in node:
            raise IdentityError(f"node {node_uuid} omits parent_uuid")
        if node_uuid in by_uuid or node_uuid == project_uuid:
            raise IdentityError(f"duplicate node uuid {node_uuid}")
        by_uuid[node_uuid] = node

    siblings = {}
    for node in nodes:
        parent = node["parent_uuid"]
        if parent is not None:
            if parent not in by_uuid:
                raise IdentityError(
                    f"node {node['uuid']} references unknown parent_uuid {parent}"
                )
            if by_uuid[parent]["kind"] != KIND_FOLDER:
                raise IdentityError(
                    f"node {node['uuid']} has a non-folder parent {parent}"
                )
        key = (parent, int(node.get("order", 0)))
        if key in siblings:
            raise IdentityError(
                f"nodes {siblings[key]} and {node['uuid']} share order "
                f"{key[1]} under the same parent"
            )
        siblings[key] = node["uuid"]

    for node in nodes:
        seen = set()
        cursor = node["parent_uuid"]
        while cursor is not None:
            if cursor in seen:
                raise IdentityError(f"parent cycle at node {node['uuid']}")
            seen.add(cursor)
            cursor = by_uuid[cursor]["parent_uuid"]

    return by_uuid


def read_identity(project_root):
    """Load and validate an existing identity file. Never repairs or rewrites."""
    path = identity_path(project_root)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            identity = json.load(handle)
    except json.JSONDecodeError as error:
        raise IdentityError(f"identity file is not valid JSON: {path}") from error

    if not isinstance(identity, dict):
        raise IdentityError(f"identity file must hold an object: {path}")
    if identity.get("format_version") != FORMAT_VERSION:
        raise IdentityError(
            f"unsupported format_version {identity.get('format_version')!r}"
        )

    project = identity.get("project") or {}
    project_uuid = _require_uuid(project.get("uuid"), "project.uuid")

    nodes = identity.get("nodes")
    if not isinstance(nodes, list):
        raise IdentityError("identity.nodes must be a list")

    _validate_nodes(project_uuid, nodes)
    return identity


def _sync_pool(sync_rows):
    """Flatten sync folder/document rows into one path-keyed candidate pool."""
    pool = []
    for row in (sync_rows or {}).get("folders", ()):
        pool.append(
            {
                "uuid": _require_uuid(row.get("folder_id"), "sync folder_id"),
                "kind": KIND_FOLDER,
                "local_path": str(row.get("local_path") or ""),
            }
        )
    for row in (sync_rows or {}).get("documents", ()):
        pool.append(
            {
                "uuid": _require_uuid(row.get("document_id"), "sync document_id"),
                "kind": KIND_DOCUMENT,
                "local_path": str(row.get("local_path") or ""),
            }
        )
    return pool


def _match_candidates(legacy_path, pool):
    """Exact raw match first, then NFC-only match. Returns (rows, strategy)."""
    exact = [row for row in pool if row["local_path"] == legacy_path]
    if exact:
        return exact, "exact"
    target = _nfc(legacy_path)
    loose = [row for row in pool if _nfc(row["local_path"]) == target]
    if loose:
        return loose, "nfc"
    return [], None


def _plan_project_uuid(project, sync_rows, uuid_factory, report):
    candidates = list((sync_rows or {}).get("projects", ()))
    local_key = project.get("local_key")
    if local_key is not None:
        candidates = [r for r in candidates if r.get("local_key") == local_key]

    if len(candidates) > 1:
        report["ambiguous_matches"].append("project")
        return None
    if len(candidates) == 1:
        project_uuid = _require_uuid(candidates[0].get("project_id"), "project_id")
        report["project_uuid_inherited"].append(project_uuid)
        return project_uuid

    project_uuid = _require_uuid(uuid_factory(), "generated project uuid")
    report["project_uuid_generated"].append(project_uuid)
    return project_uuid


def plan_identity(project, local_nodes, sync_rows=None, uuid_factory=None):
    """Compute UUID inheritance and generation without writing anything.

    ``local_nodes`` describe the current title-based tree: ``legacy_path`` is
    the raw filesystem string, ``parent_legacy_path`` names the parent (None at
    the root), plus ``kind``, ``title`` and ``order``.
    """
    uuid_factory = uuid_factory or (lambda: str(uuid_module.uuid4()))
    report = {name: [] for name in (
        "project_uuid_inherited",
        "project_uuid_generated",
        "node_uuid_inherited_exact",
        "node_uuid_inherited_nfc",
        "node_uuid_generated",
        "ambiguous_matches",
        "uuid_collisions",
        "kind_mismatches",
        "unmatched_sync_rows",
    )}

    pool = _sync_pool(sync_rows)
    project_uuid = _plan_project_uuid(project, sync_rows, uuid_factory, report)

    assigned = {}
    inherited_uuids = set()
    prepared = []
    for raw in local_nodes:
        kind = str(raw.get("kind") or "")
        if kind not in KINDS:
            raise IdentityError(f"unsupported kind {kind!r}")
        legacy_path = str(raw.get("legacy_path") or "")
        if not legacy_path:
            raise IdentityError("every local node needs a legacy_path")
        if legacy_path in assigned:
            raise IdentityError(f"duplicate legacy_path {legacy_path!r}")

        candidates, strategy = _match_candidates(legacy_path, pool)
        node_uuid = None
        if len(candidates) > 1:
            report["ambiguous_matches"].append(legacy_path)
        elif len(candidates) == 1:
            candidate = candidates[0]
            if candidate["kind"] != kind:
                report["kind_mismatches"].append(legacy_path)
            else:
                node_uuid = candidate["uuid"]
                inherited_uuids.add(node_uuid)
                bucket = (
                    "node_uuid_inherited_exact"
                    if strategy == "exact"
                    else "node_uuid_inherited_nfc"
                )
                report[bucket].append(legacy_path)
        else:
            node_uuid = _require_uuid(uuid_factory(), "generated node uuid")
            report["node_uuid_generated"].append(legacy_path)

        assigned[legacy_path] = node_uuid
        prepared.append(
            {
                "uuid": node_uuid,
                "kind": kind,
                "parent_legacy_path": raw.get("parent_legacy_path"),
                "legacy_path": legacy_path,
                "path": _display_path(legacy_path),
                "title": (
                    _nfc(raw["title"])
                    if raw.get("title") is not None
                    else _nfc(_default_title(legacy_path, kind))
                ),
                "order": int(raw.get("order", 0)),
            }
        )

    seen = {}
    for node in prepared:
        if node["uuid"] is None:
            continue
        if node["uuid"] in seen:
            report["uuid_collisions"].append(node["legacy_path"])
            if seen[node["uuid"]] not in report["uuid_collisions"]:
                report["uuid_collisions"].append(seen[node["uuid"]])
        seen[node["uuid"]] = node["legacy_path"]

    for row in pool:
        if row["uuid"] not in inherited_uuids:
            report["unmatched_sync_rows"].append(row["uuid"])

    nodes = []
    for node in prepared:
        parent_legacy = node.pop("parent_legacy_path")
        if parent_legacy is not None and parent_legacy not in assigned:
            raise IdentityError(
                f"node {node['legacy_path']!r} references unknown parent "
                f"{parent_legacy!r}"
            )
        nodes.append(
            {
                "uuid": node["uuid"],
                "kind": node["kind"],
                "parent_uuid": (
                    None if parent_legacy is None else assigned[parent_legacy]
                ),
                "legacy_path": node["legacy_path"],
                "path": node["path"],
                "title": node["title"],
                "order": node["order"],
            }
        )

    plan = {
        "format_version": FORMAT_VERSION,
        "project": {"uuid": project_uuid, "title": _nfc(project.get("title"))},
        "nodes": nodes,
        "report": report,
    }
    plan["blocked"] = any(report[name] for name in BLOCKING_BUCKETS)
    return plan


def identity_node(raw):
    """Normalize one node into the shape stored in the identity file."""
    kind = str(raw.get("kind") or "")
    if kind not in KINDS:
        raise IdentityError(f"unsupported kind {kind!r}")
    legacy_path = str(raw.get("legacy_path") or "")
    if not legacy_path:
        raise IdentityError("every node needs a legacy_path")
    return {
        "uuid": _require_uuid(raw.get("uuid"), "node.uuid"),
        "kind": kind,
        "parent_uuid": (
            None
            if raw.get("parent_uuid") in (None, "")
            else _require_uuid(raw.get("parent_uuid"), "node.parent_uuid")
        ),
        "legacy_path": legacy_path,
        "path": _display_path(legacy_path),
        "title": (
            _nfc(raw["title"])
            if raw.get("title") is not None
            else _nfc(_default_title(legacy_path, kind))
        ),
        "order": int(raw.get("order", 0)),
    }


def write_identity(project_root, identity, overwrite=False):
    """Validate and write an identity file through a temp file plus atomic replace."""
    _validate_nodes(identity["project"]["uuid"], identity["nodes"])

    target = identity_path(project_root)
    if os.path.exists(target) and not overwrite:
        raise IdentityError(f"identity file already exists: {target}")

    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    handle_fd, temp_path = tempfile.mkstemp(
        dir=directory, prefix=".identity-", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(identity, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, target)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    return identity


def apply_identity(project_root, plan):
    """Write a clean plan to the identity file atomically."""
    blocking = {name: plan["report"][name] for name in BLOCKING_BUCKETS
                if plan["report"][name]}
    if blocking:
        raise IdentityError(f"plan is blocked, nothing was written: {blocking}")

    identity = {
        "format_version": FORMAT_VERSION,
        "project": dict(plan["project"]),
        "nodes": [dict(node) for node in plan["nodes"]],
    }
    return write_identity(project_root, identity)


def append_nodes(project_root, new_nodes):
    """Add nodes to an existing identity file in one atomic replacement.

    Re-adding a uuid that is already recorded is a no-op for that node, so a
    resumed transaction can safely repeat the append with the same ids.
    """
    identity = read_identity(project_root)
    known = {node["uuid"]: node for node in identity["nodes"]}

    merged = list(identity["nodes"])
    for raw in new_nodes:
        node = identity_node(raw)
        existing = known.get(node["uuid"])
        if existing is not None:
            if existing != node:
                raise IdentityError(
                    f"node {node['uuid']} already exists with different values"
                )
            continue
        known[node["uuid"]] = node
        merged.append(node)

    updated = {
        "format_version": FORMAT_VERSION,
        "project": dict(identity["project"]),
        "nodes": merged,
    }
    return write_identity(project_root, updated, overwrite=True)


def remove_nodes(project_root, uuids):
    """Drop nodes and everything under them in one atomic replacement.

    Permanent deletion is the one case where a UUID legitimately stops
    existing. Descendants go with their ancestor because a node whose parent is
    gone would be unreachable, and leaving it behind would report as identity
    pointing at files that are no longer on disk.

    Removing a uuid that is not recorded is a no-op, so a resumed transaction
    can repeat the removal safely.
    """
    identity = read_identity(project_root)
    doomed = {_require_uuid(value, "remove uuid") for value in uuids}
    if not doomed:
        return identity

    children = {}
    for node in identity["nodes"]:
        children.setdefault(node["parent_uuid"], []).append(node["uuid"])

    pending = list(doomed)
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in doomed:
                doomed.add(child)
                pending.append(child)

    remaining = [
        dict(node) for node in identity["nodes"] if node["uuid"] not in doomed
    ]
    if len(remaining) == len(identity["nodes"]):
        return identity

    updated = {
        "format_version": FORMAT_VERSION,
        "project": dict(identity["project"]),
        "nodes": remaining,
    }
    return write_identity(project_root, updated, overwrite=True)


def ensure_identity(project_root, project, local_nodes, sync_rows=None,
                    uuid_factory=None):
    """Return the existing identity, or plan and write one on first migration.

    An existing file always wins, so re-running this never changes a UUID. A
    corrupt file raises instead of being replaced.
    """
    if os.path.exists(identity_path(project_root)):
        return read_identity(project_root)
    plan = plan_identity(project, local_nodes, sync_rows, uuid_factory)
    return apply_identity(project_root, plan)


def relocate_nodes(project_root, moves):
    """Move nodes to a new parent and path without changing a single uuid.

    ``moves`` carry ``uuid`` plus the new ``parent_uuid`` and ``legacy_path``,
    and optionally ``title`` and ``order``. Descendants follow their ancestor's
    new path automatically: a path is a lookup value derived from the tree, so
    moving a folder must never renumber anything inside it.

    Applying the same relocation twice is a no-op, which lets an interrupted
    move be finished from its journal.
    """
    identity = read_identity(project_root)
    nodes = [dict(node) for node in identity["nodes"]]
    index = {node["uuid"]: node for node in nodes}

    for move in moves:
        node_uuid = _require_uuid(move.get("uuid"), "move.uuid")
        node = index.get(node_uuid)
        if node is None:
            raise IdentityError(f"cannot relocate unknown node {node_uuid}")

        old_path = node["legacy_path"]
        new_path = str(move.get("legacy_path") or old_path)
        parent = move.get("parent_uuid", node["parent_uuid"])
        node["parent_uuid"] = (
            None if parent in (None, "") else _require_uuid(parent, "move.parent_uuid")
        )
        node["legacy_path"] = new_path
        node["path"] = _display_path(new_path)
        if move.get("title") is not None:
            node["title"] = _nfc(move["title"])
        if move.get("order") is not None:
            node["order"] = int(move["order"])

        if new_path != old_path:
            prefix = old_path + "/"
            for other in nodes:
                if other["uuid"] == node_uuid:
                    continue
                if other["legacy_path"].startswith(prefix):
                    tail = other["legacy_path"][len(prefix):]
                    other["legacy_path"] = f"{new_path}/{tail}"
                    other["path"] = _display_path(other["legacy_path"])

    updated = {
        "format_version": FORMAT_VERSION,
        "project": dict(identity["project"]),
        "nodes": nodes,
    }
    return write_identity(project_root, updated, overwrite=True)


def logical_tree(identity):
    """Return {parent_uuid or None: [(order, uuid, kind), ...]} sorted by order."""
    tree = {}
    for node in identity["nodes"]:
        tree.setdefault(node["parent_uuid"], []).append(
            (int(node["order"]), node["uuid"], node["kind"])
        )
    for children in tree.values():
        children.sort()
    return tree
