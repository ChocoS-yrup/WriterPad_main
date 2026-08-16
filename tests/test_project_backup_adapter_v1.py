import itertools
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_backup_adapter_v1 as adapter
import project_creation_v1 as creation
from project_backup_v1 import BackupFormatError, read_manifest
from project_creation_v1 import (
    CreationError,
    create_item,
    create_project,
    create_volume,
    recover_workspace,
    workspace_journal_dir,
    writing_root,
)
from project_identity_v1 import logical_tree, read_identity

# UTF-8 BOM, then "첫 문장<emoji>" CRLF "둘째 문장" LF. 33 bytes.
GOLDEN_BYTES = bytes.fromhex(
    "efbbbfecb2ab20ebacb8ec9ea5f09f99820d0aeb9198eca7b820ebacb8ec9ea50a"
)


def seq_uuids():
    counter = itertools.count(1)
    return lambda: f"{next(counter):08x}-0000-4000-8000-000000000001"


class BackupAdapterTestCase(unittest.TestCase):
    """실제 프로젝트 ↔ 공통 v1 패키지. 합성 임시 위치에서만 수행한다."""

    def setUp(self):
        from project_manager_writing import WritingProjectManager

        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "작품목록"
        self.workspace.mkdir(parents=True)
        self.uuids = seq_uuids()
        self.addCleanup(self.temp_dir.cleanup)

        self.title = "왕복 시험"
        create_project(str(self.workspace), self.title, uuid_factory=self.uuids)
        self.root = str(self.workspace / self.title)
        self.wpm = WritingProjectManager.create_detached(
            str(self.workspace), self.title, writing_root(self.root)
        )

        # A volume with real bytes, an empty document, and an extra folder.
        create_volume(self.root, uuid_factory=self.uuids)
        self._write("메인/원고/1권/001화.txt", GOLDEN_BYTES)
        settings = creation.node_for_path(self.root, "메인/설정집")
        create_item(
            self.root, settings["uuid"], "인물", False, uuid_factory=self.uuids
        )
        self._write("메인/설정집/인물.txt", "주인공".encode("utf-8"))

        # Something in the trash, which the backup must carry.
        memo = creation.node_for_path(self.root, "메인/메모장")
        create_item(self.root, memo["uuid"], "버린 초안", False, uuid_factory=self.uuids)
        self._write("메인/메모장/버린 초안.txt", "버렸지만 남아야 한다".encode("utf-8"))
        self.trashed_uuid = creation.node_for_path(
            self.root, "메인/메모장/버린 초안.txt"
        )["uuid"]
        self.trashed_rel = self.wpm.move_to_trash("메인/메모장/버린 초안.txt")

    def _write(self, relative, payload):
        path = os.path.join(writing_root(self.root), *relative.split("/"))
        with open(path, "wb") as handle:
            handle.write(payload)

    def _read(self, project_root, relative):
        path = os.path.join(writing_root(project_root), *relative.split("/"))
        with open(path, "rb") as handle:
            return handle.read()

    def test_backup_then_restore_reproduces_uuids_and_bytes(self):
        package = str(self.base / "패키지")
        manifest = adapter.backup_project(self.root, package)

        source = read_identity(self.root)
        self.assertEqual(
            {node["uuid"] for node in manifest["nodes"]},
            {node["uuid"] for node in source["nodes"]},
        )
        self.assertEqual(manifest["project"]["uuid"], source["project"]["uuid"])

        restored_root = str(self.workspace / "되살린 작품")
        adapter.restore_project(package, str(self.workspace), "되살린 작품")

        # Same ids, same parents, same order, same bytes.
        adapter.verify_restored(restored_root, manifest)
        restored = read_identity(restored_root)
        self.assertEqual(logical_tree(restored), logical_tree(source))
        self.assertNotEqual(logical_tree(source), {})
        self.assertEqual(
            self._read(restored_root, "메인/원고/1권/001화.txt"), GOLDEN_BYTES
        )
        self.assertEqual(
            os.path.getsize(
                os.path.join(
                    writing_root(restored_root), "메인", "원고", "1권", "002화.txt"
                )
            ),
            0,
        )

        # The restored project opens without repair.
        self.assertEqual(
            creation.prepare_open(restored_root)["status"], creation.OPEN_OK
        )

    def test_trashed_document_survives_the_round_trip(self):
        package = str(self.base / "패키지")
        manifest = adapter.backup_project(self.root, package)

        trashed = [n for n in manifest["nodes"] if n["uuid"] == self.trashed_uuid]
        self.assertEqual(len(trashed), 1)
        self.assertTrue(trashed[0]["path"].startswith(creation.TRASH_PATH + "/"))

        adapter.restore_project(package, str(self.workspace), "되살린 작품")
        restored_root = str(self.workspace / "되살린 작품")

        node = {
            n["uuid"]: n for n in read_identity(restored_root)["nodes"]
        }[self.trashed_uuid]
        self.assertEqual(node["legacy_path"], self.trashed_rel)
        self.assertEqual(
            self._read(restored_root, self.trashed_rel),
            "버렸지만 남아야 한다".encode("utf-8"),
        )

    def test_backup_refuses_when_identity_lists_a_missing_file(self):
        os.remove(
            os.path.join(
                writing_root(self.root), "메인", "원고", "1권", "001화.txt"
            )
        )
        with self.assertRaises(BackupFormatError):
            adapter.backup_project(self.root, str(self.base / "패키지"))
        self.assertFalse((self.base / "패키지").exists())

    def test_restore_refuses_an_existing_project(self):
        package = str(self.base / "패키지")
        adapter.backup_project(self.root, package)

        with self.assertRaises(CreationError):
            adapter.restore_project(package, str(self.workspace), self.title)

        # The original project is untouched.
        self.assertEqual(
            creation.prepare_open(self.root)["status"], creation.OPEN_OK
        )

    def test_interrupted_restore_recovers_with_the_same_uuids(self):
        package = str(self.base / "패키지")
        manifest = adapter.backup_project(self.root, package)
        real_replace = os.replace

        def fail_on_staging(src, dst):
            if creation.PROJECT_STAGING_DIRNAME in str(src):
                raise OSError("simulated crash before the staging rename")
            return real_replace(src, dst)

        with patch("os.replace", side_effect=fail_on_staging):
            with self.assertRaises(OSError):
                adapter.restore_project(package, str(self.workspace), "되살린 작품")

        self.assertFalse((self.workspace / "되살린 작품").exists())
        self.assertEqual(
            len(os.listdir(workspace_journal_dir(str(self.workspace)))), 1
        )

        recover_workspace(str(self.workspace))

        restored_root = str(self.workspace / "되살린 작품")
        adapter.verify_restored(restored_root, manifest)
        self.assertEqual(
            os.listdir(workspace_journal_dir(str(self.workspace))), []
        )


if __name__ == "__main__":
    unittest.main()
