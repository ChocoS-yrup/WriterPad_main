"""UUID project backup package v1, shared with the iPad client.

A package is a directory holding one manifest and one flat store of document
bytes::

    <package>/
    ├── manifest.json
    └── workspace/
        └── <document-uuid>

Tree identity lives in the manifest as ``uuid``/``parent_uuid`` only. ``path``
and ``title`` are display values, so an NFC/NFD mismatch between Windows and
iPad can never change what gets restored. Folder nodes are manifest entries
rather than real directories, which keeps empty folders in the package.

Document bytes are copied verbatim: no newline translation, no BOM handling,
no re-encoding and no Unicode normalization anywhere on the content path.
"""

import hashlib
import json
import os
import re
import unicodedata

FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
WORKSPACE_DIR = "workspace"

KIND_FOLDER = "folder"
KIND_DOCUMENT = "document"
KINDS = (KIND_FOLDER, KIND_DOCUMENT)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

_READ_CHUNK = 1024 * 1024


class BackupFormatError(Exception):
    """The manifest or the caller-supplied tree violates format v1."""


def _require_uuid(value, field):
    text = str(value or "")
    if not _UUID_PATTERN.fullmatch(text):
        raise BackupFormatError(
            f"{field} must be a lowercase canonical UUID, got {value!r}"
        )
    return text


def _display_text(value):
    """NFC-normalize a display value and use '/' as the path separator."""
    return unicodedata.normalize("NFC", str(value or "")).replace("\\", "/")


def _digest_file(path):
    """Return (bytes, sha256) of a file read in binary mode."""
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return total, digest.hexdigest()


_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}


def _require_safe_path(value, field):
    """A manifest path must stay inside the destination and be creatable here.

    A package can arrive from another machine, so its paths are untrusted input:
    an absolute path or a `..` segment would let a restore write outside the
    directory the user picked.
    """
    text = str(value or "")
    if not text:
        raise BackupFormatError(f"{field} must not be empty")
    if text != unicodedata.normalize("NFC", text):
        raise BackupFormatError(f"{field} must be NFC-normalized: {text!r}")
    if "\\" in text or text.startswith("/") or ":" in text:
        raise BackupFormatError(f"{field} must be a relative '/' path: {text!r}")

    for part in text.split("/"):
        if part in ("", ".", ".."):
            raise BackupFormatError(f"{field} has an unusable segment: {text!r}")
        if part != part.rstrip(". "):
            raise BackupFormatError(
                f"{field} segment ends with a dot or space: {text!r}"
            )
        if part.split(".")[0].lower() in _WINDOWS_RESERVED:
            raise BackupFormatError(
                f"{field} uses a reserved Windows name: {text!r}"
            )
    return text


def _validate_tree(project_uuid, nodes):
    """Reject duplicate ids, unknown parents, non-folder parents and cycles."""
    by_uuid = {}
    for node in nodes:
        node_uuid = node["uuid"]
        if node_uuid in by_uuid or node_uuid == project_uuid:
            raise BackupFormatError(f"duplicate node uuid {node_uuid}")
        by_uuid[node_uuid] = node

    for node in nodes:
        parent = node["parent_uuid"]
        if parent is None:
            continue
        if parent not in by_uuid:
            raise BackupFormatError(
                f"node {node['uuid']} references unknown parent_uuid {parent}"
            )
        if by_uuid[parent]["kind"] != KIND_FOLDER:
            raise BackupFormatError(
                f"node {node['uuid']} has a non-folder parent {parent}"
            )

    for node in nodes:
        seen = set()
        cursor = node["parent_uuid"]
        while cursor is not None:
            if cursor in seen:
                raise BackupFormatError(f"parent cycle at node {node['uuid']}")
            seen.add(cursor)
            cursor = by_uuid[cursor]["parent_uuid"]

    return by_uuid


def _normalized_node(raw):
    """Validate one caller-supplied node and drop everything not in v1."""
    kind = str(raw.get("kind") or "")
    if kind not in KINDS:
        raise BackupFormatError(f"kind must be one of {KINDS}, got {kind!r}")

    node = {
        "uuid": _require_uuid(raw.get("uuid"), "node.uuid"),
        "kind": kind,
        "parent_uuid": (
            None
            if raw.get("parent_uuid") in (None, "")
            else _require_uuid(raw.get("parent_uuid"), "node.parent_uuid")
        ),
        "path": _display_text(raw.get("path")),
        "title": _display_text(raw.get("title")),
        "order": int(raw.get("order", 0)),
    }
    return node


def create_project_backup(project, nodes, destination):
    """Write a v1 package for ``project`` into the non-existent ``destination``.

    ``nodes`` are dicts of uuid/kind/parent_uuid/path/title/order. Document
    nodes additionally carry ``source_path``, whose bytes are copied verbatim
    into ``workspace/<uuid>``. The source project is only ever read.
    """
    project_uuid = _require_uuid(project.get("uuid"), "project.uuid")
    project_title = _display_text(project.get("title"))

    prepared = []
    sources = {}
    for raw in nodes:
        node = _normalized_node(raw)
        if node["kind"] == KIND_DOCUMENT:
            source = raw.get("source_path")
            if not source:
                raise BackupFormatError(
                    f"document {node['uuid']} is missing source_path"
                )
            sources[node["uuid"]] = source
        prepared.append(node)

    _validate_tree(project_uuid, prepared)

    if os.path.exists(destination):
        raise BackupFormatError(f"destination already exists: {destination}")

    workspace = os.path.join(destination, WORKSPACE_DIR)
    os.makedirs(workspace)

    for node in prepared:
        if node["kind"] != KIND_DOCUMENT:
            continue
        target = os.path.join(workspace, node["uuid"])
        with open(sources[node["uuid"]], "rb") as reader:
            with open(target, "wb") as writer:
                while True:
                    chunk = reader.read(_READ_CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
        size, digest = _digest_file(target)
        node["bytes"] = size
        node["sha256"] = digest

    manifest = {
        "format_version": FORMAT_VERSION,
        # A package holds exactly one project, so its order is always 0. The
        # field is written because the iPad client expects to read it.
        "project": {"uuid": project_uuid, "title": project_title, "order": 0},
        "nodes": prepared,
    }
    manifest_path = os.path.join(destination, MANIFEST_NAME)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return manifest


def read_manifest(package):
    """Load and validate the manifest of a v1 package."""
    manifest_path = os.path.join(package, MANIFEST_NAME)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    version = manifest.get("format_version")
    if version != FORMAT_VERSION:
        raise BackupFormatError(
            f"unsupported format_version {version!r}, expected {FORMAT_VERSION}"
        )

    project = manifest.get("project") or {}
    project_uuid = _require_uuid(project.get("uuid"), "project.uuid")
    # A package holds one project. Older packages omit the field entirely.
    if int(project.get("order", 0)) != 0:
        raise BackupFormatError(
            f"project.order must be 0, got {project.get('order')!r}"
        )
    if project.get("title") is not None:
        title = str(project["title"])
        if title != unicodedata.normalize("NFC", title):
            raise BackupFormatError("project.title must be NFC-normalized")

    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        raise BackupFormatError("manifest.nodes must be a list")

    siblings = set()
    for node in nodes:
        _require_uuid(node.get("uuid"), "node.uuid")
        if node.get("kind") not in KINDS:
            raise BackupFormatError(f"unsupported kind {node.get('kind')!r}")
        if "parent_uuid" not in node:
            raise BackupFormatError(f"node {node['uuid']} omits parent_uuid")

        _require_safe_path(node.get("path"), f"node {node['uuid']} path")
        title = str(node.get("title") or "")
        if title != unicodedata.normalize("NFC", title):
            raise BackupFormatError(f"node {node['uuid']} title must be NFC")

        order = int(node.get("order", 0))
        if order < 0:
            raise BackupFormatError(f"node {node['uuid']} has a negative order")
        key = (node["parent_uuid"], order)
        if key in siblings:
            raise BackupFormatError(
                f"two nodes share order {order} under the same parent"
            )
        siblings.add(key)

        if node["kind"] == KIND_DOCUMENT:
            if "bytes" not in node or "sha256" not in node:
                raise BackupFormatError(
                    f"document {node['uuid']} must record bytes and sha256"
                )
        elif "bytes" in node or "sha256" in node:
            raise BackupFormatError(
                f"folder {node['uuid']} must not record bytes or sha256"
            )

    _validate_tree(project_uuid, [_normalized_node(node) for node in nodes])
    return manifest


def restore_project_backup(package, destination):
    """Restore document bytes from ``package`` into an empty ``destination``.

    Every document is verified against the manifest before the copy and again
    after it. Files land as ``<destination>/<uuid>``: identity is the UUID, so
    no title or path is trusted to rebuild the tree. The manifest is returned
    so a later metadata import reuses exactly these ids.
    """
    manifest = read_manifest(package)

    # The destination must not exist at all, matching the iPad client. An empty
    # directory is refused too: restoring into a path something else already
    # reserved is how a restore quietly lands on top of live work.
    if os.path.exists(destination):
        raise BackupFormatError(f"destination already exists: {destination}")

    workspace = os.path.join(package, WORKSPACE_DIR)
    documents = [n for n in manifest["nodes"] if n["kind"] == KIND_DOCUMENT]

    for node in documents:
        stored = os.path.join(workspace, node["uuid"])
        if not os.path.isfile(stored):
            raise BackupFormatError(f"missing workspace file for {node['uuid']}")
        size, digest = _digest_file(stored)
        if size != node["bytes"] or digest != node["sha256"]:
            raise BackupFormatError(
                f"package content for {node['uuid']} does not match the manifest"
            )

    os.makedirs(destination, exist_ok=True)
    for node in documents:
        stored = os.path.join(workspace, node["uuid"])
        target = os.path.join(destination, node["uuid"])
        with open(stored, "rb") as reader:
            with open(target, "wb") as writer:
                while True:
                    chunk = reader.read(_READ_CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
        size, digest = _digest_file(target)
        if size != node["bytes"] or digest != node["sha256"]:
            raise BackupFormatError(
                f"restored content for {node['uuid']} does not match the manifest"
            )

    return manifest


def logical_tree(manifest):
    """Return {parent_uuid or None: [(order, uuid, kind), ...]} sorted by order.

    Built from uuid/parent_uuid only, so two platforms that disagree on path
    normalization still produce the same tree.
    """
    tree = {}
    for node in manifest["nodes"]:
        tree.setdefault(node["parent_uuid"], []).append(
            (int(node["order"]), node["uuid"], node["kind"])
        )
    for children in tree.values():
        children.sort()
    return tree
