"""New full-data/UI-independent recovery boundaries; never real user files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import project_archive as archive
from project_creation_v1 import create_project, create_volume, create_item, node_for_path, prepare_open
from project_identity_v1 import read_identity, logical_tree


class ProjectArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "source" / "작품목록"
        self.workspace.mkdir(parents=True)
        create_project(str(self.workspace), "합성 작품")
        self.project = self.workspace / "합성 작품"
        create_volume(str(self.project))
        self.write("집필모드/메인/원고/1권/001화.txt", b"\xef\xbb\xbfmanuscript\r\n")
        trash = node_for_path(str(self.project), "메인/휴지통")
        create_item(str(self.project), trash["uuid"], "삭제 원고", False)
        self.write("집필모드/메인/휴지통/삭제 원고.txt", "휴지통 내용".encode())
        for name, data in {
            "메인/초안/001화.txt": "AI 원고".encode(),
            "AI/초안/001화_응답.md": "AI 응답 이력".encode(),
            "백업/자동저장/old.txt": b"AI old backup",
            "설정.json": '{"prompt_draft":"작품 프롬프트"}'.encode(),
            "cost_history.json": b'[{"cost_usd":0.1}]',
            "집필모드/설정.json": b'{"tree_order":{},"expanded_folders":[]}',
            "집필모드/백업/휴지통_원위치.json": b'{}',
            "집필모드/백업/자동저장/prior.txt": b"writing backup",
            "집필모드/__antigravity__/queue.json": b"NEVER COPY QUEUE",
            ".server-project-import.json": b"NEVER COPY LINK",
            "config.json": b"NEVER COPY SESSION",
        }.items():
            self.write(name, data)
        self.package = self.base / "외부 백업"
        self.destination = self.base / "독립 복원"
        self.before = archive._inventory(self.project)[0]
        blocker = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def write(self, name, data):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_full_data_export_restore_and_open_detached_project(self):
        result = archive.create_archive(self.project, self.package)
        self.assertTrue(result["portable_v1"])
        self.assertFalse((self.package / "windows-data/config.json").exists())
        self.assertFalse((self.package / "windows-data/집필모드/__antigravity__").exists())
        restored = archive.restore_archive(self.package, self.destination)
        root = Path(restored["project"])
        self.assertEqual(restored["status"], "openable")
        self.assertEqual(logical_tree(read_identity(str(root))), logical_tree(read_identity(str(self.project))))
        for relative, expected in archive._inventory(self.project, user_scope=True)[0]["files"].items():
            if relative != archive.IDENTITY:
                self.assertEqual(archive._digest(root / relative), expected)
        self.assertEqual(prepare_open(str(root))["status"], "ok")
        from project_manager_writing import WritingProjectManager
        manager = WritingProjectManager.create_detached(str(root.parent), root.name, str(root / "집필모드"))
        self.assertIn("manuscript", manager.read_text_file("메인/원고/1권/001화.txt"))
        self.assertEqual(archive._inventory(self.project)[0], self.before)
        self.assertFalse(list(self.destination.rglob("*.sqlite3")))
        self.assertFalse(list(self.destination.rglob(".server-project-import.json")))

    def test_existing_destinations_and_nested_paths_preserved(self):
        self.package.mkdir()
        sentinel = self.package / "keep"
        sentinel.write_bytes(b"keep")
        with self.assertRaises(archive.ArchiveError):
            archive.create_archive(self.project, self.package)
        self.assertEqual(sentinel.read_bytes(), b"keep")
        with self.assertRaises(archive.ArchiveError):
            archive.create_archive(self.project, self.project / "backup")
        self.assertEqual(archive._inventory(self.project)[0], self.before)

    def test_source_changed_during_copy_is_not_published(self):
        original = archive._copy_inventory
        def change(source, target, listing):
            original(source, target, listing)
            self.write("메인/초안/001화.txt", b"changed concurrently")
        with patch.object(archive, "_copy_inventory", side_effect=change):
            with self.assertRaises(archive.ArchiveError):
                archive.create_archive(self.project, self.package)
        self.assertFalse(self.package.exists())
        self.assertFalse(list(self.base.glob(".writerpad-archive-*")))

    def test_write_failure_leaves_no_partial_backup(self):
        with patch.object(archive.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                archive.create_archive(self.project, self.package)
        self.assertFalse(self.package.exists())
        self.assertEqual(archive._inventory(self.project)[0], self.before)

    def test_tampered_backup_refused_before_destination_created(self):
        archive.create_archive(self.project, self.package)
        (self.package / "windows-data/메인/초안/001화.txt").write_bytes(b"tampered")
        with self.assertRaises(archive.ArchiveError):
            archive.restore_archive(self.package, self.destination)
        self.assertFalse(self.destination.exists())

    def test_manifest_path_escape_and_extra_operational_data_refused(self):
        archive.create_archive(self.project, self.package)
        path = self.package / archive.INDEX
        value = json.loads(path.read_text(encoding="utf-8"))
        value["files"]["../outside"] = {"bytes": 0, "sha256": ""}
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises((archive.ArchiveError, ValueError)):
            archive.restore_archive(self.package, self.destination)
        self.assertFalse(self.destination.exists())

    def test_restore_failure_never_publishes_partial_project(self):
        archive.create_archive(self.project, self.package)
        with patch.object(archive, "prepare_open", return_value={"status": "blocked"}):
            with self.assertRaises(archive.ArchiveError):
                archive.restore_archive(self.package, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.base.glob(".writerpad-restore-*")))
        self.assertTrue(archive.verify_archive(self.package))

    def test_restore_destination_race_preserves_newly_created_target(self):
        archive.create_archive(self.project, self.package)
        original = archive._publish
        def race(staging, target):
            target.mkdir()
            (target / "keep").write_bytes(b"other writer")
            return original(staging, target)
        with patch.object(archive, "_publish", side_effect=race):
            with self.assertRaises(archive.ArchiveError):
                archive.restore_archive(self.package, self.destination)
        self.assertEqual((self.destination / "keep").read_bytes(), b"other writer")

    def test_junction_is_refused_without_reading_linked_content(self):
        target = self.base / "outside"
        target.mkdir()
        (target / "private.txt").write_bytes(b"do not follow")
        junction = self.project / "AI" / "junction"
        # Junction creation uses only two synthetic paths and needs no symlink privilege.
        from subprocess import run
        made = run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True)
        self.assertEqual(made.returncode, 0, made.stderr)
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        with self.assertRaises(archive.ArchiveError):
            archive.create_archive(self.project, self.package)
        self.assertFalse(self.package.exists())

    def test_qt_unavailable_cli_preserves_broken_identity_and_verifies(self):
        self.write(archive.IDENTITY, b"broken private identity text")
        script = Path(__file__).resolve().parents[1] / "writerpad_recovery.py"
        # -S removes site packages entirely, making Qt and the cloud SDK unavailable.
        def run(*args):
            return subprocess.run([sys.executable, "-S", str(script), *map(str, args)], capture_output=True)
        result = run("preserve", self.project, self.package)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run("verify", self.package)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run("restore", self.package, self.destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        root = self.destination / "작품목록/합성 작품"
        self.assertEqual((root / "메인/초안/001화.txt").read_bytes(), "AI 원고".encode())
        self.assertEqual((root / archive.IDENTITY).read_bytes(), b"broken private identity text")
        self.assertEqual((self.project / archive.IDENTITY).read_bytes(), b"broken private identity text")
        report = json.loads((self.destination / "restore-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "preserved_only")


if __name__ == "__main__":
    unittest.main()
