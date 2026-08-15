import hashlib
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from independent_project_backup import (
    BackupEntry,
    BackupInventoryItem,
    IndependentProjectBackupError,
    IndependentProjectBackupStore,
    ProjectIdentity,
    RESTORED_IDENTITY_MANIFEST_FILE_NAME,
    retention_candidates,
)


class IndependentProjectBackupTests(unittest.TestCase):
    NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
    MAIN_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
    MANUSCRIPT_ID = uuid.UUID("20000000-0000-0000-0000-000000000002")
    VOLUME_ID = uuid.UUID("20000000-0000-0000-0000-000000000003")
    CHAPTER_ID = uuid.UUID("20000000-0000-0000-0000-000000000004")
    EMPTY_ID = uuid.UUID("20000000-0000-0000-0000-000000000005")
    NOTES_ID = uuid.UUID("20000000-0000-0000-0000-000000000006")
    NOTE_ID = uuid.UUID("20000000-0000-0000-0000-000000000007")
    BACKUP_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="WriterPadWindowsIndependentBackupTests-"
        )
        self.container = Path(self.temporary_directory.name)
        self.workspace = self.container / "원본" / "집필모드"
        self.chapter_text = "첫 문장🙂\n둘째 문장\n"
        self.note_text = "주인공: 윤슬\n"
        for relative in (
            "메인",
            "메인/원고",
            "메인/원고/1권",
            "메인/메모장",
        ):
            (self.workspace / relative).mkdir(parents=True, exist_ok=True)
        (self.workspace / "메인/원고/1권/001화.txt").write_text(
            self.chapter_text, encoding="utf-8"
        )
        (self.workspace / "메인/원고/1권/002화.txt").write_bytes(b"")
        (self.workspace / "메인/메모장/등장인물.txt").write_text(
            self.note_text, encoding="utf-8"
        )
        self.project = ProjectIdentity(
            self.PROJECT_ID,
            "합성 백업 작품",
            self.NOW - timedelta(days=10),
            self.NOW,
        )
        self.entries = self._entries()
        self.store = IndependentProjectBackupStore(
            clock=lambda: self.NOW,
            uuid_factory=lambda: self.BACKUP_ID,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_creates_readable_package_and_restores_exact_structure(self):
        before = self._regular_files(self.workspace)
        package = self.container / "독립백업-v1"

        receipt = self.store.create_backup(
            source_workspace=self.workspace,
            project=self.project,
            entries=self.entries,
            package_path=package,
        )

        self.assertEqual(receipt.package_path, package.resolve())
        self.assertEqual(receipt.manifest["format_version"], 1)
        self.assertEqual(receipt.manifest["project"]["project_id"], str(self.PROJECT_ID))
        chapter = next(
            entry
            for entry in receipt.manifest["entries"]
            if entry["entry_id"] == str(self.CHAPTER_ID)
        )
        self.assertEqual(chapter["parent_id"], str(self.VOLUME_ID))
        self.assertEqual(
            chapter["content"]["utf8_byte_count"],
            len(self.chapter_text.encode("utf-8")),
        )
        self.assertEqual(
            chapter["content"]["sha256"],
            hashlib.sha256(self.chapter_text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            (package / "files/메인/원고/1권/001화.txt").read_text(encoding="utf-8"),
            self.chapter_text,
        )
        self.assertEqual(self._regular_files(self.workspace), before)

        restored = self.container / "복구-검증"
        restore = self.store.restore_verified_backup(
            package_path=package, restored_workspace=restored
        )
        self.assertEqual(restore.manifest, receipt.manifest)
        self.assertEqual(
            restore.identity_manifest_path.name,
            RESTORED_IDENTITY_MANIFEST_FILE_NAME,
        )
        self.assertEqual(
            (restored / "메인/원고/1권/001화.txt").read_text(encoding="utf-8"),
            self.chapter_text,
        )
        self.assertEqual((restored / "메인/원고/1권/002화.txt").read_bytes(), b"")
        identity = json.loads(restore.identity_manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(identity, receipt.manifest)
        self.assertEqual(self._regular_files(self.workspace), before)

    def test_refuses_existing_backup_and_restore_destinations(self):
        existing_backup = self.container / "기존-백업"
        existing_backup.mkdir()
        backup_sentinel = existing_backup / "사용자.txt"
        backup_sentinel.write_text("보존", encoding="utf-8")
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self._create(existing_backup)
        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")
        self.assertEqual(backup_sentinel.read_text(encoding="utf-8"), "보존")

        package = self.container / "새-백업"
        self._create(package)
        existing_restore = self.container / "기존-복구"
        existing_restore.mkdir()
        restore_sentinel = existing_restore / "사용자.txt"
        restore_sentinel.write_text("복구 보존", encoding="utf-8")
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self.store.restore_verified_backup(
                package_path=package, restored_workspace=existing_restore
            )
        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")
        self.assertEqual(restore_sentinel.read_text(encoding="utf-8"), "복구 보존")

    def test_refuses_destination_inside_source_workspace(self):
        parent = self.workspace / "백업"
        parent.mkdir()
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self._create(parent / "독립인척하는백업")
        self.assertEqual(caught.exception.code, "DESTINATION_INSIDE_WORKSPACE")

    def test_corruption_fails_before_restore_directory_is_created(self):
        package = self.container / "손상-백업"
        self._create(package)
        (package / "files/메인/원고/1권/001화.txt").write_text(
            "손상된 본문", encoding="utf-8"
        )
        restored = self.container / "생기면-안되는-복구"
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self.store.restore_verified_backup(
                package_path=package, restored_workspace=restored
            )
        self.assertEqual(caught.exception.code, "CONTENT_HASH_MISMATCH")
        self.assertFalse(restored.exists())

    def test_metadata_hash_mismatch_removes_only_owned_partial(self):
        wrong_entries = self._entries(chapter_hash="0" * 64)
        before = self._regular_files(self.workspace)
        package = self.container / "실패-백업"
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self.store.create_backup(
                source_workspace=self.workspace,
                project=self.project,
                entries=wrong_entries,
                package_path=package,
            )
        self.assertEqual(caught.exception.code, "CONTENT_HASH_MISMATCH")
        self.assertFalse(package.exists())
        self.assertFalse(any(".partial-" in path.name for path in self.container.iterdir()))
        self.assertEqual(self._regular_files(self.workspace), before)

    def test_duplicate_path_and_missing_parent_fail_before_writing(self):
        duplicate = BackupEntry(
            uuid.uuid4(), self.PROJECT_ID, "text", self.VOLUME_ID,
            "메인/원고/1권/001화.txt", 99, self.NOW,
        )
        duplicate_package = self.container / "중복-백업"
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self.store.create_backup(
                source_workspace=self.workspace,
                project=self.project,
                entries=[*self.entries, duplicate],
                package_path=duplicate_package,
            )
        self.assertEqual(caught.exception.code, "DUPLICATE_RELATIVE_PATH")
        self.assertFalse(duplicate_package.exists())

        missing_parent_entries = [
            BackupEntry(
                entry.entry_id,
                entry.project_id,
                entry.kind,
                uuid.uuid4() if entry.entry_id == self.CHAPTER_ID else entry.parent_id,
                entry.relative_path,
                entry.user_order,
                entry.modified_at,
                entry.content_sha256,
            )
            for entry in self.entries
        ]
        missing_parent_package = self.container / "부모없음-백업"
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self.store.create_backup(
                source_workspace=self.workspace,
                project=self.project,
                entries=missing_parent_entries,
                package_path=missing_parent_package,
            )
        self.assertEqual(caught.exception.code, "PARENT_MISSING")
        self.assertFalse(missing_parent_package.exists())

    def test_unexpected_item_and_symbolic_link_are_rejected(self):
        package = self.container / "검증-백업"
        self._create(package)
        unexpected = package / "메모.txt"
        unexpected.write_text("manifest에 없음", encoding="utf-8")
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self.store.verify_backup(package)
        self.assertEqual(caught.exception.code, "UNEXPECTED_PACKAGE_ITEM")
        unexpected.unlink()

        link = package / "files/링크.txt"
        try:
            link.symlink_to(package / "files/메인/원고/1권/001화.txt")
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")
        with self.assertRaises(IndependentProjectBackupError) as caught:
            self.store.verify_backup(package)
        self.assertEqual(caught.exception.code, "SYMLINK_NOT_ALLOWED")

    def test_thirty_day_retention_returns_candidates_without_deleting(self):
        paths = [self.container / name for name in ("old", "boundary", "recent", "pinned")]
        for path in paths:
            path.mkdir()
        items = [
            BackupInventoryItem(paths[0], self.NOW - timedelta(days=31)),
            BackupInventoryItem(paths[1], self.NOW - timedelta(days=30)),
            BackupInventoryItem(paths[2], self.NOW - timedelta(days=2)),
            BackupInventoryItem(paths[3], self.NOW - timedelta(days=90), True),
        ]
        candidates = retention_candidates(items, now=self.NOW)
        self.assertEqual([item.package_path for item in candidates], [paths[0]])
        self.assertTrue(all(path.exists() for path in paths))

    def _create(self, package):
        return self.store.create_backup(
            source_workspace=self.workspace,
            project=self.project,
            entries=self.entries,
            package_path=package,
        )

    def _entries(self, chapter_hash=None):
        chapter_hash = chapter_hash or hashlib.sha256(
            self.chapter_text.encode("utf-8")
        ).hexdigest()
        empty_hash = hashlib.sha256(b"").hexdigest()
        note_hash = hashlib.sha256(self.note_text.encode("utf-8")).hexdigest()
        return [
            BackupEntry(self.MAIN_ID, self.PROJECT_ID, "folder", None, "메인", 0, self.NOW),
            BackupEntry(
                self.MANUSCRIPT_ID, self.PROJECT_ID, "folder", self.MAIN_ID,
                "메인/원고", 0, self.NOW,
            ),
            BackupEntry(
                self.VOLUME_ID, self.PROJECT_ID, "folder", self.MANUSCRIPT_ID,
                "메인/원고/1권", 0, self.NOW,
            ),
            BackupEntry(
                self.CHAPTER_ID, self.PROJECT_ID, "text", self.VOLUME_ID,
                "메인/원고/1권/001화.txt", 0, self.NOW, chapter_hash,
            ),
            BackupEntry(
                self.EMPTY_ID, self.PROJECT_ID, "text", self.VOLUME_ID,
                "메인/원고/1권/002화.txt", 1, self.NOW, empty_hash,
            ),
            BackupEntry(
                self.NOTES_ID, self.PROJECT_ID, "folder", self.MAIN_ID,
                "메인/메모장", 1, self.NOW,
            ),
            BackupEntry(
                self.NOTE_ID, self.PROJECT_ID, "text", self.NOTES_ID,
                "메인/메모장/등장인물.txt", 0, self.NOW, note_hash,
            ),
        ]

    @staticmethod
    def _regular_files(root):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }


if __name__ == "__main__":
    unittest.main()
