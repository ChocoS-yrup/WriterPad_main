"""Connect a real Windows project to the shared v1 backup format.

Backing up reads ``.writerpad/identity-v1.json`` and hands the core the nodes it
already holds, so the package carries the project's real UUIDs rather than new
ones. ``legacy_path`` locates the file on this disk; ``path`` is what goes in the
manifest, which is why a normalization difference between platforms cannot move
anything.

Restoring rebuilds an openable project from a package, reusing every UUID in the
manifest. It runs as a journalled transaction into a staging directory that
lands with one rename, so an interrupted restore never leaves a half project
where a real one should be.

메인/휴지통 and everything inside it are part of the backup: trashed items keep
the UUID they already had, and dropping their bytes would mean a disaster backup
discarding manuscripts the writer can still recover.
"""

import hashlib
import os
import shutil

from project_backup_v1 import (
    KIND_DOCUMENT,
    MANIFEST_NAME,
    WORKSPACE_DIR,
    BackupFormatError,
    create_project_backup,
    read_manifest,
)
from project_creation_v1 import (
    JOURNAL_VERSION,
    PROJECT_STAGING_DIRNAME,
    UNTRACKED_FOLDERS,
    CreationError,
    _new_uuid,
    _read_json,
    _write_json_atomic,
    workspace_journal_dir,
    writing_root,
)
from project_identity_v1 import identity_node, read_identity, write_identity

_READ_CHUNK = 1024 * 1024

RESTORE_KIND = "restore_project"
MANIFEST_FIELDS = ("uuid", "kind", "parent_uuid", "path", "title", "order")


def package_looks_valid(package):
    """Cheap shape check before offering a package to the user."""
    return os.path.isfile(os.path.join(package, MANIFEST_NAME)) and os.path.isdir(
        os.path.join(package, WORKSPACE_DIR)
    )


def project_uuid_owner(workspace, project_uuid):
    """Return the project title already holding ``project_uuid``, or None."""
    from project_creation_v1 import identity_path

    if not os.path.isdir(workspace):
        return None
    for name in sorted(os.listdir(workspace)):
        if name.startswith("."):
            continue
        candidate = os.path.join(workspace, name)
        if not os.path.isfile(identity_path(candidate)):
            continue
        try:
            identity = read_identity(candidate)
        except Exception:
            continue
        if identity["project"]["uuid"] == project_uuid:
            return name
    return None


def backup_project(project_root, destination):
    """Write a v1 package for a real project into a non-existent destination."""
    identity = read_identity(project_root)
    root = writing_root(project_root)

    nodes = []
    for node in identity["nodes"]:
        entry = {field: node[field] for field in MANIFEST_FIELDS}
        if node["kind"] == KIND_DOCUMENT:
            entry["source_path"] = os.path.join(
                root, node["legacy_path"].replace("/", os.sep)
            )
            if not os.path.isfile(entry["source_path"]):
                raise BackupFormatError(
                    f"identity lists {node['legacy_path']!r} but the file is gone; "
                    "run the audit before backing up"
                )
        nodes.append(entry)

    return create_project_backup(identity["project"], nodes, destination)


def _digest_file(path):
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


def _materialize(build_root, stored_workspace, nodes):
    """Create the tree and copy document bytes. Safe to repeat during recovery."""
    root = writing_root(build_root)
    for node in nodes:
        target = os.path.join(root, node["legacy_path"].replace("/", os.sep))
        if node["kind"] != KIND_DOCUMENT:
            os.makedirs(target, exist_ok=True)
            continue

        stored = os.path.join(stored_workspace, node["uuid"])
        if not os.path.isfile(stored):
            raise BackupFormatError(f"missing workspace file for {node['uuid']}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(stored, "rb") as reader, open(target, "wb") as writer:
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

    for legacy_path in UNTRACKED_FOLDERS:
        os.makedirs(
            os.path.join(root, legacy_path.replace("/", os.sep)), exist_ok=True
        )


def restore_project(package, workspace, title):
    """Rebuild an openable project from ``package``, reusing its UUIDs.

    The manifest's ``path`` becomes this project's ``legacy_path``: the files are
    created here and now, so the two agree by construction. No UUID is issued.
    """
    manifest = read_manifest(package)
    project_root = os.path.join(workspace, title)
    if os.path.exists(project_root):
        raise CreationError(f"project already exists: {project_root}")

    existing = project_uuid_owner(workspace, manifest["project"]["uuid"])
    if existing is not None:
        raise CreationError(
            f"project uuid {manifest['project']['uuid']} is already used by "
            f"{existing}; restoring would create a second copy of one project"
        )

    nodes = []
    for node in manifest["nodes"]:
        entry = {
            "uuid": node["uuid"],
            "kind": node["kind"],
            "parent_uuid": node["parent_uuid"],
            "legacy_path": node["path"],
            "title": node["title"],
            "order": node["order"],
        }
        if node["kind"] == KIND_DOCUMENT:
            entry["bytes"] = node["bytes"]
            entry["sha256"] = node["sha256"]
        nodes.append(entry)

    transaction_id = _new_uuid(None)
    staging = os.path.join(workspace, PROJECT_STAGING_DIRNAME, transaction_id)
    journal = os.path.join(
        workspace_journal_dir(workspace), f"{transaction_id}.json"
    )
    _write_json_atomic(
        journal,
        {
            "format_version": JOURNAL_VERSION,
            "transaction_id": transaction_id,
            "kind": RESTORE_KIND,
            "workspace": workspace,
            "target_path": project_root,
            "staging_path": staging,
            "package_path": os.path.abspath(package),
            "project": dict(manifest["project"]),
            "nodes": nodes,
        },
    )
    return finish_restore(_read_json(journal), journal)


def finish_restore(entry, journal_path):
    """Complete or roll back one restore transaction. Safe to repeat."""
    from project_creation_v1 import identity_path

    project_root = entry["target_path"]
    staging = entry["staging_path"]

    if os.path.exists(identity_path(project_root)):
        identity = read_identity(project_root)
        if identity["project"]["uuid"] != entry["project"]["uuid"]:
            raise CreationError(
                f"{project_root} exists with a different project uuid; "
                "manual recovery required"
            )
        shutil.rmtree(staging, ignore_errors=True)
        os.unlink(journal_path)
        return identity

    if os.path.exists(project_root):
        raise CreationError(
            f"{project_root} exists without an identity file; "
            "manual recovery required"
        )

    stored_workspace = os.path.join(entry["package_path"], WORKSPACE_DIR)
    if not os.path.isdir(stored_workspace):
        raise BackupFormatError(
            f"package is no longer readable: {entry['package_path']}"
        )

    os.makedirs(writing_root(staging), exist_ok=True)
    _materialize(staging, stored_workspace, entry["nodes"])

    write_identity(
        staging,
        {
            "format_version": 1,
            "project": entry["project"],
            "nodes": [identity_node(node) for node in entry["nodes"]],
        },
        overwrite=True,
    )

    os.makedirs(os.path.dirname(project_root), exist_ok=True)
    os.replace(staging, project_root)
    os.unlink(journal_path)
    return read_identity(project_root)


def verify_restored(project_root, manifest):
    """Confirm a restored project matches the manifest it came from."""
    identity = read_identity(project_root)
    if identity["project"]["uuid"] != manifest["project"]["uuid"]:
        raise BackupFormatError("restored project uuid does not match the manifest")

    restored = {node["uuid"]: node for node in identity["nodes"]}
    if set(restored) != {node["uuid"] for node in manifest["nodes"]}:
        raise BackupFormatError("restored node ids do not match the manifest")

    root = writing_root(project_root)
    for node in manifest["nodes"]:
        mine = restored[node["uuid"]]
        if mine["parent_uuid"] != node["parent_uuid"]:
            raise BackupFormatError(f"parent changed for {node['uuid']}")
        if int(mine["order"]) != int(node["order"]):
            raise BackupFormatError(f"order changed for {node['uuid']}")
        if node["kind"] != KIND_DOCUMENT:
            continue
        size, digest = _digest_file(
            os.path.join(root, mine["legacy_path"].replace("/", os.sep))
        )
        if size != node["bytes"] or digest != node["sha256"]:
            raise BackupFormatError(f"restored bytes differ for {node['uuid']}")
    return identity
