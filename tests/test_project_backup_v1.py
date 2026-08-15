import hashlib
import json
import os
import tempfile
import unicodedata
import unittest
from pathlib import Path

from project_backup_v1 import (
    BackupFormatError,
    create_project_backup,
    logical_tree,
    restore_project_backup,
)

PROJECT_UUID = "3f2a0000-0000-4000-8000-000000000001"
MAIN_UUID = "8c1d0000-0000-4000-8000-000000000001"
DRAFT_UUID = "b47e0000-0000-4000-8000-000000000001"
VOLUME_UUID = "c58f0000-0000-4000-8000-000000000001"
DOC1_UUID = "d90f0000-0000-4000-8000-000000000001"
DOC2_UUID = "e01a0000-0000-4000-8000-000000000001"
MEMO_UUID = "a36c0000-0000-4000-8000-000000000001"
EMPTY_FOLDER_UUID = "f12b0000-0000-4000-8000-000000000001"

# UTF-8 BOM, then "첫 문장<emoji>" CRLF "둘째 문장" LF. 33 bytes.
GOLDEN_BYTES = bytes.fromhex(
    "efbbbfecb2ab20ebacb8ec9ea5f09f99820d0aeb9198eca7b820ebacb8ec9ea50a"
)
GOLDEN_SHA256 = "dfff377e734e1edd5ff1a9e1a540edf84d97be15e965ff26d90cce63065f3e3c"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class ProjectBackupV1TestCase(unittest.TestCase):
    """합성 프로젝트만 사용한다. 실제 원고와 기존 백업은 건드리지 않는다."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.addCleanup(self.temp_dir.cleanup)

    def _write_source(self, name, payload):
        path = self.source / name
        with open(path, "wb") as handle:
            handle.write(payload)
        return path

    @staticmethod
    def _fingerprint(path):
        stat = os.stat(path)
        with open(path, "rb") as handle:
            payload = handle.read()
        return stat.st_mtime_ns, len(payload), hashlib.sha256(payload).hexdigest()

    def test_backup_and_restore_preserve_uuid_tree_and_bytes(self):
        doc1 = self._write_source("001.bin", GOLDEN_BYTES)
        doc2 = self._write_source("002.bin", b"")
        before = {p: self._fingerprint(p) for p in (doc1, doc2)}

        # Titles and paths arrive decomposed, the way iOS hands them over.
        nfd_draft = unicodedata.normalize("NFD", "초안")
        self.assertNotEqual(nfd_draft, "초안")

        nodes = [
            {
                "uuid": MAIN_UUID,
                "kind": "folder",
                "parent_uuid": None,
                "path": "메인",
                "title": "메인",
                "order": 0,
            },
            {
                "uuid": DRAFT_UUID,
                "kind": "folder",
                "parent_uuid": MAIN_UUID,
                "path": unicodedata.normalize("NFD", "메인/초안"),
                "title": nfd_draft,
                "order": 0,
            },
            {
                "uuid": VOLUME_UUID,
                "kind": "folder",
                "parent_uuid": DRAFT_UUID,
                "path": "메인/초안/1권",
                "title": "1권",
                "order": 0,
            },
            {
                "uuid": DOC1_UUID,
                "kind": "document",
                "parent_uuid": VOLUME_UUID,
                "path": "메인/초안/1권/001화.txt",
                "title": "001화",
                "order": 0,
                "source_path": str(doc1),
            },
            {
                "uuid": DOC2_UUID,
                "kind": "document",
                "parent_uuid": VOLUME_UUID,
                "path": "메인/초안/1권/002화.txt",
                "title": "002화",
                "order": 1,
                "source_path": str(doc2),
            },
            {
                "uuid": MEMO_UUID,
                "kind": "folder",
                "parent_uuid": MAIN_UUID,
                "path": "메인/메모장",
                "title": "메모장",
                "order": 1,
            },
            {
                "uuid": EMPTY_FOLDER_UUID,
                "kind": "folder",
                "parent_uuid": MEMO_UUID,
                "path": "메인/메모장/빈 폴더",
                "title": "빈 폴더",
                "order": 0,
            },
        ]

        package = self.root / "package"
        manifest = create_project_backup(
            {"uuid": PROJECT_UUID, "title": "복원 시험"}, nodes, str(package)
        )

        # The manifest on disk is the contract, not the returned object.
        with open(package / "manifest.json", "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored, manifest)
        self.assertEqual(stored["format_version"], 1)
        self.assertEqual(stored["project"]["uuid"], PROJECT_UUID)

        by_uuid = {node["uuid"]: node for node in stored["nodes"]}
        self.assertEqual(len(by_uuid), 7)

        # Decomposed input is stored composed.
        self.assertEqual(by_uuid[DRAFT_UUID]["title"], "초안")
        self.assertEqual(by_uuid[DRAFT_UUID]["path"], "메인/초안")

        # Root folders record an explicit null rather than omitting the key.
        self.assertIn("parent_uuid", by_uuid[MAIN_UUID])
        self.assertIsNone(by_uuid[MAIN_UUID]["parent_uuid"])

        # Folders carry no content fields; the empty folder survives.
        for folder_uuid in (
            MAIN_UUID,
            DRAFT_UUID,
            VOLUME_UUID,
            MEMO_UUID,
            EMPTY_FOLDER_UUID,
        ):
            self.assertEqual(by_uuid[folder_uuid]["kind"], "folder")
            self.assertNotIn("bytes", by_uuid[folder_uuid])
            self.assertNotIn("sha256", by_uuid[folder_uuid])

        # Golden document and empty document.
        self.assertEqual(by_uuid[DOC1_UUID]["bytes"], 33)
        self.assertEqual(by_uuid[DOC1_UUID]["sha256"], GOLDEN_SHA256)
        self.assertEqual(by_uuid[DOC2_UUID]["bytes"], 0)
        self.assertEqual(by_uuid[DOC2_UUID]["sha256"], EMPTY_SHA256)

        # Siblings keep order 0 and 1 under the same parent.
        self.assertEqual(by_uuid[DOC1_UUID]["parent_uuid"], VOLUME_UUID)
        self.assertEqual(by_uuid[DOC2_UUID]["parent_uuid"], VOLUME_UUID)
        self.assertEqual(by_uuid[DOC1_UUID]["order"], 0)
        self.assertEqual(by_uuid[DOC2_UUID]["order"], 1)

        # workspace/ holds document UUIDs and nothing else.
        self.assertEqual(
            sorted(os.listdir(package / "workspace")), sorted([DOC1_UUID, DOC2_UUID])
        )
        with open(package / "workspace" / DOC1_UUID, "rb") as handle:
            self.assertEqual(handle.read(), GOLDEN_BYTES)

        # Backing up never touches the source.
        self.assertEqual(before, {p: self._fingerprint(p) for p in (doc1, doc2)})

        # Restore into a destination that does not exist yet.
        restored_dir = self.root / "restored"
        self.assertFalse(restored_dir.exists())
        restored = restore_project_backup(str(package), str(restored_dir))

        self.assertEqual(
            sorted(os.listdir(restored_dir)), sorted([DOC1_UUID, DOC2_UUID])
        )
        with open(restored_dir / DOC1_UUID, "rb") as handle:
            payload = handle.read()
        self.assertEqual(payload, GOLDEN_BYTES)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), GOLDEN_SHA256)
        self.assertEqual(os.path.getsize(restored_dir / DOC2_UUID), 0)

        # The logical tree survives the round trip unchanged.
        self.assertEqual(logical_tree(restored), logical_tree(manifest))
        self.assertEqual(
            logical_tree(restored),
            {
                None: [(0, MAIN_UUID, "folder")],
                MAIN_UUID: [
                    (0, DRAFT_UUID, "folder"),
                    (1, MEMO_UUID, "folder"),
                ],
                MEMO_UUID: [(0, EMPTY_FOLDER_UUID, "folder")],
                DRAFT_UUID: [(0, VOLUME_UUID, "folder")],
                VOLUME_UUID: [
                    (0, DOC1_UUID, "document"),
                    (1, DOC2_UUID, "document"),
                ],
            },
        )

        # Restoring again into the same populated directory is refused.
        with self.assertRaises(BackupFormatError):
            restore_project_backup(str(package), str(restored_dir))


if __name__ == "__main__":
    unittest.main()
