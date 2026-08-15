import hashlib
import json
import os
import tempfile
import unicodedata
import unittest
from pathlib import Path

from project_identity_v1 import (
    IdentityError,
    apply_identity,
    ensure_identity,
    identity_path,
    logical_tree,
    plan_identity,
    read_identity,
)

PROJECT_UUID = "3f2a0000-0000-4000-8000-000000000001"
MAIN_UUID = "8c1d0000-0000-4000-8000-000000000001"
SCRIPT_UUID = "b47e0000-0000-4000-8000-000000000001"
DOC1_UUID = "d90f0000-0000-4000-8000-000000000001"
DRAFT_UUID = "c58f0000-0000-4000-8000-000000000001"
STALE_UUID = "0aa10000-0000-4000-8000-000000000001"
RIVAL_UUID = "0bb20000-0000-4000-8000-000000000001"

GEN_DOC2 = "11110000-0000-4000-8000-000000000001"
GEN_MEMO = "22220000-0000-4000-8000-000000000001"
GEN_EMPTY = "33330000-0000-4000-8000-000000000001"

# UTF-8 BOM, then "첫 문장<emoji>" CRLF "둘째 문장" LF. 33 bytes.
GOLDEN_BYTES = bytes.fromhex(
    "efbbbfecb2ab20ebacb8ec9ea5f09f99820d0aeb9198eca7b820ebacb8ec9ea50a"
)

NFD_DRAFT = unicodedata.normalize("NFD", "메인/초안")
NFC_DRAFT = "메인/초안"


def fixed_uuids(*values):
    """Deterministic replacement for uuid4 so generated ids are assertable."""
    supply = iter(values)
    return lambda: next(supply)


class ProjectIdentityV1TestCase(unittest.TestCase):
    """합성 프로젝트만 사용한다. 실제 원고와 실제 sync DB는 건드리지 않는다."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "project"
        self.root.mkdir(parents=True)
        self.addCleanup(self.temp_dir.cleanup)

    def _write_manuscript(self, relative, payload):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path

    @staticmethod
    def _fingerprint(path):
        stat = os.stat(path)
        with open(path, "rb") as handle:
            payload = handle.read()
        return stat.st_mtime_ns, len(payload), hashlib.sha256(payload).hexdigest()

    def _local_nodes(self):
        return [
            {
                "legacy_path": "메인",
                "parent_legacy_path": None,
                "kind": "folder",
                "title": "메인",
                "order": 0,
            },
            {
                "legacy_path": "메인/원고",
                "parent_legacy_path": "메인",
                "kind": "folder",
                "title": "원고",
                "order": 0,
            },
            {
                "legacy_path": "메인/원고/001화.txt",
                "parent_legacy_path": "메인/원고",
                "kind": "document",
                "title": "001화",
                "order": 0,
            },
            {
                "legacy_path": "메인/원고/002화.txt",
                "parent_legacy_path": "메인/원고",
                "kind": "document",
                "title": "002화",
                "order": 1,
            },
            # On disk this folder is decomposed; the sync row is composed.
            {
                "legacy_path": NFD_DRAFT,
                "parent_legacy_path": "메인",
                "kind": "folder",
                "title": unicodedata.normalize("NFD", "초안"),
                "order": 1,
            },
            {
                "legacy_path": "메인/메모장",
                "parent_legacy_path": "메인",
                "kind": "folder",
                "title": "메모장",
                "order": 2,
            },
            {
                "legacy_path": "메인/메모장/빈 폴더",
                "parent_legacy_path": "메인/메모장",
                "kind": "folder",
                "title": "빈 폴더",
                "order": 0,
            },
        ]

    def _sync_rows(self):
        return {
            "projects": [{"project_id": PROJECT_UUID, "local_key": "합성작품"}],
            "folders": [
                {"folder_id": MAIN_UUID, "local_path": "메인"},
                {"folder_id": SCRIPT_UUID, "local_path": "메인/원고"},
                {"folder_id": DRAFT_UUID, "local_path": NFC_DRAFT},
                {"folder_id": STALE_UUID, "local_path": "메인/사라진폴더"},
            ],
            "documents": [
                {"document_id": DOC1_UUID, "local_path": "메인/원고/001화.txt"},
            ],
        }

    def test_first_migration_inherits_generates_and_stays_stable(self):
        doc1 = self._write_manuscript("메인/원고/001화.txt", GOLDEN_BYTES)
        doc2 = self._write_manuscript("메인/원고/002화.txt", b"")
        before = {p: self._fingerprint(p) for p in (doc1, doc2)}

        plan = plan_identity(
            {"title": "합성 프로젝트", "local_key": "합성작품"},
            self._local_nodes(),
            self._sync_rows(),
            uuid_factory=fixed_uuids(GEN_DOC2, GEN_MEMO, GEN_EMPTY),
        )
        report = plan["report"]
        self.assertFalse(plan["blocked"])

        # 1. Raw path matches inherit directly.
        self.assertEqual(
            sorted(report["node_uuid_inherited_exact"]),
            sorted(["메인", "메인/원고", "메인/원고/001화.txt"]),
        )
        # 2. A path that differs only by normalization still inherits.
        self.assertEqual(report["node_uuid_inherited_nfc"], [NFD_DRAFT])
        # 3. Everything with no sync history gets a fresh id.
        self.assertEqual(
            report["node_uuid_generated"],
            ["메인/원고/002화.txt", "메인/메모장", "메인/메모장/빈 폴더"],
        )
        self.assertEqual(report["project_uuid_inherited"], [PROJECT_UUID])
        self.assertEqual(report["project_uuid_generated"], [])
        self.assertEqual(report["unmatched_sync_rows"], [STALE_UUID])
        for bucket in ("ambiguous_matches", "uuid_collisions", "kind_mismatches"):
            self.assertEqual(report[bucket], [])

        identity = apply_identity(str(self.root), plan)
        stored = read_identity(str(self.root))
        self.assertEqual(stored, identity)
        self.assertEqual(stored["format_version"], 1)
        self.assertEqual(stored["project"]["uuid"], PROJECT_UUID)

        by_path = {node["legacy_path"]: node for node in stored["nodes"]}
        self.assertEqual(len(by_path), 7)

        # 6. legacy_path keeps the raw bytes; path and title are composed.
        draft = by_path[NFD_DRAFT]
        self.assertEqual(draft["legacy_path"], NFD_DRAFT)
        self.assertNotEqual(draft["legacy_path"], NFC_DRAFT)
        self.assertEqual(draft["path"], NFC_DRAFT)
        self.assertEqual(draft["title"], "초안")
        self.assertEqual(draft["uuid"], DRAFT_UUID)

        # 4 and 5. The logical tree comes from parent_uuid/order, and the
        # childless folder is still present as its own node.
        self.assertEqual(
            logical_tree(stored),
            {
                None: [(0, MAIN_UUID, "folder")],
                MAIN_UUID: [
                    (0, SCRIPT_UUID, "folder"),
                    (1, DRAFT_UUID, "folder"),
                    (2, GEN_MEMO, "folder"),
                ],
                SCRIPT_UUID: [
                    (0, DOC1_UUID, "document"),
                    (1, GEN_DOC2, "document"),
                ],
                GEN_MEMO: [(0, GEN_EMPTY, "folder")],
            },
        )
        self.assertNotIn(GEN_EMPTY, logical_tree(stored))

        # 7. A second run reuses the file byte for byte.
        identity_file = Path(identity_path(str(self.root)))
        first_bytes = identity_file.read_bytes()
        first_stat = os.stat(identity_file).st_mtime_ns
        again = ensure_identity(
            str(self.root),
            {"title": "합성 프로젝트", "local_key": "합성작품"},
            self._local_nodes(),
            self._sync_rows(),
            uuid_factory=fixed_uuids(),
        )
        self.assertEqual(again, stored)
        self.assertEqual(identity_file.read_bytes(), first_bytes)
        self.assertEqual(os.stat(identity_file).st_mtime_ns, first_stat)

        # 11. Migration never touches a manuscript file.
        self.assertEqual(before, {p: self._fingerprint(p) for p in (doc1, doc2)})
        self.assertEqual(
            sorted(os.listdir(self.root / "메인" / "원고")),
            ["001화.txt", "002화.txt"],
        )

    def test_ambiguous_candidates_block_apply_and_write_nothing(self):
        """두 sync row가 NFC로 같아지면 승계하지 않고 중단한다."""
        sync_rows = {
            "projects": [],
            "folders": [
                {"folder_id": DRAFT_UUID, "local_path": NFD_DRAFT},
                {"folder_id": RIVAL_UUID, "local_path": NFD_DRAFT},
            ],
            "documents": [],
        }
        plan = plan_identity(
            {"title": "합성 프로젝트"},
            [
                {
                    "legacy_path": NFC_DRAFT,
                    "parent_legacy_path": None,
                    "kind": "folder",
                    "title": "초안",
                    "order": 0,
                }
            ],
            sync_rows,
            uuid_factory=fixed_uuids(PROJECT_UUID),
        )
        self.assertTrue(plan["blocked"])
        self.assertEqual(plan["report"]["ambiguous_matches"], [NFC_DRAFT])
        self.assertEqual(plan["report"]["node_uuid_generated"], [])

        with self.assertRaises(IdentityError):
            apply_identity(str(self.root), plan)
        self.assertFalse(os.path.exists(identity_path(str(self.root))))
        self.assertFalse((self.root / ".writerpad").exists())

    def test_two_nodes_inheriting_one_uuid_block_apply(self):
        """같은 폴더의 NFC/NFD 두 표기가 한 UUID를 물면 중단한다."""
        sync_rows = {
            "projects": [],
            "folders": [{"folder_id": DRAFT_UUID, "local_path": NFC_DRAFT}],
            "documents": [],
        }
        plan = plan_identity(
            {"title": "합성 프로젝트"},
            [
                {
                    "legacy_path": NFC_DRAFT,
                    "parent_legacy_path": None,
                    "kind": "folder",
                    "title": "초안",
                    "order": 0,
                },
                {
                    "legacy_path": NFD_DRAFT,
                    "parent_legacy_path": None,
                    "kind": "folder",
                    "title": "초안",
                    "order": 1,
                },
            ],
            sync_rows,
            uuid_factory=fixed_uuids(PROJECT_UUID),
        )
        self.assertTrue(plan["blocked"])
        self.assertEqual(
            sorted(plan["report"]["uuid_collisions"]), sorted([NFC_DRAFT, NFD_DRAFT])
        )

        with self.assertRaises(IdentityError):
            apply_identity(str(self.root), plan)
        self.assertFalse(os.path.exists(identity_path(str(self.root))))

    def test_damaged_identity_file_is_never_overwritten(self):
        target = Path(identity_path(str(self.root)))
        target.parent.mkdir(parents=True)
        damaged = b'{"format_version": 1, "project": {'
        target.write_bytes(damaged)

        with self.assertRaises(IdentityError):
            ensure_identity(
                str(self.root),
                {"title": "합성 프로젝트", "local_key": "합성작품"},
                self._local_nodes(),
                self._sync_rows(),
                uuid_factory=fixed_uuids(GEN_DOC2, GEN_MEMO, GEN_EMPTY),
            )
        self.assertEqual(target.read_bytes(), damaged)
        self.assertEqual(sorted(os.listdir(target.parent)), [target.name])


if __name__ == "__main__":
    unittest.main()
