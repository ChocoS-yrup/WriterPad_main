import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_creation_v1 as creation
from project_creation_v1 import (
    STANDARD_FOLDERS,
    audit,
    create_item,
    create_project,
    create_volume,
    project_journal_dir,
    recover_project,
    recover_workspace,
    workspace_journal_dir,
    writing_root,
)
from project_identity_v1 import identity_path, read_identity


def seq_uuids():
    """Deterministic canonical UUIDs so assertions can name exact ids."""
    counter = itertools.count(1)
    return lambda: f"{next(counter):08x}-0000-4000-8000-000000000001"


class ProjectCreationV1TestCase(unittest.TestCase):
    """합성 임시 위치에서만 수행한다. 실제 원고와 운영 경로는 건드리지 않는다."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "작품목록"
        self.workspace.mkdir(parents=True)
        # One sequence per test: reusing a fresh one would reissue ids that the
        # identity file already holds, which append_nodes rightly refuses.
        self.uuids = seq_uuids()
        self.addCleanup(self.temp_dir.cleanup)

    def _project(self, title="합성 프로젝트"):
        create_project(str(self.workspace), title, uuid_factory=self.uuids)
        return str(self.workspace / title)

    def _pending(self, directory):
        return os.listdir(directory) if os.path.isdir(directory) else []

    def test_project_returns_with_every_standard_folder_already_numbered(self):
        root = self._project()
        identity = read_identity(root)

        self.assertEqual(len(identity["nodes"]), len(STANDARD_FOLDERS))
        by_path = {node["legacy_path"]: node for node in identity["nodes"]}
        self.assertEqual(sorted(by_path), sorted(STANDARD_FOLDERS))

        # Every standard folder has a uuid and exists on disk at return time.
        for legacy_path, node in by_path.items():
            self.assertTrue(node["uuid"])
            self.assertTrue(
                os.path.isdir(os.path.join(writing_root(root), legacy_path))
            )

        # 메인 is the root; the rest hang off it.
        self.assertIsNone(by_path["메인"]["parent_uuid"])
        self.assertEqual(by_path["메인/원고"]["parent_uuid"], by_path["메인"]["uuid"])

        # Machine-managed folders exist but stay out of identity.
        self.assertTrue(os.path.isdir(os.path.join(writing_root(root), "백업", "자동저장")))
        self.assertNotIn("백업/자동저장", by_path)
        self.assertIn("메인/휴지통", by_path)

        # Nothing is left pending.
        self.assertEqual(self._pending(workspace_journal_dir(str(self.workspace))), [])
        self.assertEqual(audit(root)["missing_on_disk"], [])
        self.assertEqual(audit(root)["missing_in_identity"], [])

    def test_interrupted_project_creation_resumes_with_the_same_uuids(self):
        title = "중단 시험"
        real_replace = os.replace

        def fail_on_staging(src, dst):
            if creation.PROJECT_STAGING_DIRNAME in str(src):
                raise OSError("simulated crash before the staging rename")
            return real_replace(src, dst)

        with patch("os.replace", side_effect=fail_on_staging):
            with self.assertRaises(OSError):
                create_project(str(self.workspace), title, uuid_factory=self.uuids)

        # The project is not visible, but the journal survived.
        self.assertFalse((self.workspace / title).exists())
        journals = os.listdir(workspace_journal_dir(str(self.workspace)))
        self.assertEqual(len(journals), 1)

        import json

        with open(
            os.path.join(workspace_journal_dir(str(self.workspace)), journals[0]),
            encoding="utf-8",
        ) as handle:
            journalled = json.load(handle)

        recovered = recover_workspace(str(self.workspace))
        self.assertEqual(len(recovered), 1)

        identity = read_identity(str(self.workspace / title))
        self.assertEqual(
            identity["project"]["uuid"], journalled["project"]["uuid"]
        )
        self.assertEqual(
            [node["uuid"] for node in identity["nodes"]],
            [node["uuid"] for node in journalled["nodes"]],
        )
        self.assertEqual(self._pending(workspace_journal_dir(str(self.workspace))), [])

    def test_user_folder_and_document_are_numbered_on_creation(self):
        root = self._project()
        manuscript = {
            node["legacy_path"]: node for node in read_identity(root)["nodes"]
        }["메인/원고"]
        factory = self.uuids

        create_item(root, manuscript["uuid"], "설정 메모", True, uuid_factory=factory)
        identity = create_item(
            root, manuscript["uuid"], "001화", False, uuid_factory=factory
        )

        by_path = {node["legacy_path"]: node for node in identity["nodes"]}
        folder = by_path["메인/원고/설정 메모"]
        document = by_path["메인/원고/001화.txt"]

        self.assertEqual(folder["kind"], "folder")
        self.assertEqual(document["kind"], "document")
        self.assertEqual(document["title"], "001화")
        self.assertEqual(folder["parent_uuid"], manuscript["uuid"])
        self.assertEqual(document["parent_uuid"], manuscript["uuid"])
        self.assertNotEqual(folder["order"], document["order"])

        self.assertTrue(
            os.path.isfile(os.path.join(writing_root(root), "메인", "원고", "001화.txt"))
        )
        self.assertEqual(audit(root)["missing_in_identity"], [])

    def test_same_name_gets_a_suffix_instead_of_overwriting(self):
        """이름이 충돌하면 덮어쓰지 않고 고유 이름을 만든다."""
        root = self._project()
        manuscript = {
            node["legacy_path"]: node for node in read_identity(root)["nodes"]
        }["메인/원고"]

        create_item(root, manuscript["uuid"], "아이디어", False, uuid_factory=self.uuids)
        first = os.path.join(writing_root(root), "메인", "원고", "아이디어.txt")
        with open(first, "wb") as handle:
            handle.write("첫 번째".encode("utf-8"))

        identity = create_item(
            root, manuscript["uuid"], "아이디어", False, uuid_factory=self.uuids
        )
        by_path = {node["legacy_path"]: node for node in identity["nodes"]}
        self.assertIn("메인/원고/아이디어.txt", by_path)
        self.assertIn("메인/원고/아이디어 (1).txt", by_path)
        self.assertNotEqual(
            by_path["메인/원고/아이디어.txt"]["uuid"],
            by_path["메인/원고/아이디어 (1).txt"]["uuid"],
        )

        # The first document keeps its content and the second starts empty.
        with open(first, "rb") as handle:
            self.assertEqual(handle.read().decode("utf-8"), "첫 번째")
        second = os.path.join(writing_root(root), "메인", "원고", "아이디어 (1).txt")
        self.assertEqual(os.path.getsize(second), 0)

        # Folders collide by the same rule.
        create_item(root, manuscript["uuid"], "묶음", True, uuid_factory=self.uuids)
        identity = create_item(
            root, manuscript["uuid"], "묶음", True, uuid_factory=self.uuids
        )
        paths = {node["legacy_path"] for node in identity["nodes"]}
        self.assertIn("메인/원고/묶음", paths)
        self.assertIn("메인/원고/묶음 (1)", paths)

    def test_volume_creates_twenty_six_numbered_nodes_in_one_transaction(self):
        root = self._project()
        identity = create_volume(root, uuid_factory=self.uuids)

        by_path = {node["legacy_path"]: node for node in identity["nodes"]}
        self.assertIn("메인/원고/1권", by_path)
        chapters = [p for p in by_path if p.startswith("메인/원고/1권/")]
        self.assertEqual(len(chapters), 25)
        self.assertIn("메인/원고/1권/001화.txt", by_path)
        self.assertIn("메인/원고/1권/025화.txt", by_path)

        volume = by_path["메인/원고/1권"]
        for path in chapters:
            self.assertEqual(by_path[path]["parent_uuid"], volume["uuid"])
            self.assertEqual(
                os.path.getsize(
                    os.path.join(writing_root(root), path.replace("/", os.sep))
                ),
                0,
            )

        # A second volume continues the numbering instead of colliding.
        identity = create_volume(root, uuid_factory=self.uuids)
        second = {node["legacy_path"] for node in identity["nodes"]}
        self.assertIn("메인/원고/2권/026화.txt", second)
        self.assertIn("메인/원고/2권/050화.txt", second)
        self.assertEqual(
            len([p for p in second if p.startswith("메인/원고/2권/")]), 25
        )
        self.assertEqual(audit(root)["missing_in_identity"], [])

    def test_interrupted_volume_resumes_with_the_same_uuids(self):
        root = self._project()

        with patch(
            "project_creation_v1.append_nodes",
            side_effect=OSError("simulated crash before identity replace"),
        ):
            with self.assertRaises(OSError):
                create_volume(root, uuid_factory=self.uuids)

        journals = os.listdir(project_journal_dir(root))
        self.assertEqual(len(journals), 1)

        import json

        with open(
            os.path.join(project_journal_dir(root), journals[0]), encoding="utf-8"
        ) as handle:
            journalled = json.load(handle)

        # All 26 ids were recorded up front, in one journal, before the crash.
        self.assertEqual(journalled["kind"], "create_volume")
        self.assertEqual(len(journalled["nodes"]), 26)
        self.assertEqual(len({node["uuid"] for node in journalled["nodes"]}), 26)

        # The identity file has not grown yet, but the files may already exist.
        self.assertEqual(len(read_identity(root)["nodes"]), len(STANDARD_FOLDERS))

        recover_project(root)
        identity = read_identity(root)
        recorded = {node["uuid"] for node in identity["nodes"]}
        for node in journalled["nodes"]:
            self.assertIn(node["uuid"], recorded)
        self.assertEqual(self._pending(project_journal_dir(root)), [])

        # Recovery is idempotent.
        self.assertEqual(recover_project(root), [])
        self.assertEqual(len(read_identity(root)["nodes"]), len(identity["nodes"]))

    def _tree_snapshot(self, root):
        """Every path under root with its size and mtime, for change detection."""
        snapshot = {}
        for current, directories, files in os.walk(root):
            for name in list(directories) + files:
                path = os.path.join(current, name)
                stat = os.stat(path)
                snapshot[os.path.relpath(path, root)] = (
                    os.path.isdir(path),
                    stat.st_size if os.path.isfile(path) else None,
                    stat.st_mtime_ns,
                )
        return snapshot

    def test_opening_a_project_without_identity_changes_no_file(self):
        """레거시 프로젝트는 열기만으로 어떤 파일도 바뀌지 않는다."""
        legacy = self.workspace / "레거시 작품"
        manuscript = legacy / "집필모드" / "메인" / "원고"
        manuscript.mkdir(parents=True)
        (manuscript / "001화.txt").write_bytes("옛 원고".encode("utf-8"))
        # A legacy 메인/플롯 folder must survive untouched for a later import.
        (legacy / "집필모드" / "메인" / "플롯").mkdir()

        before = self._tree_snapshot(legacy)

        verdict = creation.prepare_open(str(legacy))

        self.assertEqual(verdict["status"], creation.OPEN_LEGACY)
        self.assertIn("가져오기", verdict["reason"])
        self.assertEqual(self._tree_snapshot(legacy), before)
        self.assertFalse((legacy / ".writerpad").exists())
        self.assertTrue((legacy / "집필모드" / "메인" / "플롯").is_dir())

    def test_repeated_open_never_changes_uuids_or_the_user_tree(self):
        root = self._project()
        identity_file = Path(identity_path(root))

        first_identity = read_identity(root)
        first_bytes = identity_file.read_bytes()
        first_tree = self._tree_snapshot(writing_root(root))

        for _ in range(3):
            verdict = creation.prepare_open(root)
            self.assertEqual(verdict["status"], creation.OPEN_OK)
            creation.ensure_machine_folders(root)

        self.assertEqual(read_identity(root), first_identity)
        self.assertEqual(identity_file.read_bytes(), first_bytes)
        self.assertEqual(self._tree_snapshot(writing_root(root)), first_tree)

    def test_journalless_mismatch_is_reported_and_never_repaired(self):
        root = self._project()
        before = read_identity(root)

        # A stray folder nobody journalled, and a numbered folder deleted by hand.
        os.makedirs(os.path.join(writing_root(root), "메인", "원고", "정체불명"))
        os.rmdir(os.path.join(writing_root(root), "메인", "장소"))

        report = audit(root)
        self.assertEqual(report["missing_in_identity"], ["메인/원고/정체불명"])
        self.assertEqual(report["missing_on_disk"], ["메인/장소"])
        self.assertEqual(report["pending_journals"], [])

        # audit only reports: identity is untouched and no uuid was invented.
        self.assertEqual(read_identity(root), before)
        self.assertFalse(os.path.isdir(os.path.join(writing_root(root), "메인", "장소")))


class TrashIdentityTestCase(unittest.TestCase):
    """휴지통 이동·복원이 UUID를 유지하고 identity와 어긋나지 않는다."""

    def setUp(self):
        from project_manager_writing import WritingProjectManager

        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "작품목록"
        self.workspace.mkdir(parents=True)
        self.uuids = seq_uuids()
        self.addCleanup(self.temp_dir.cleanup)

        self.title = "휴지통 시험"
        create_project(str(self.workspace), self.title, uuid_factory=self.uuids)
        self.root = str(self.workspace / self.title)
        self.wpm = WritingProjectManager.create_detached(
            str(self.workspace), self.title, writing_root(self.root)
        )

        memo = creation.node_for_path(self.root, "메인/메모장")
        create_item(
            self.root, memo["uuid"], "버릴 메모", False, uuid_factory=self.uuids
        )
        self.source_rel = "메인/메모장/버릴 메모.txt"
        with open(
            os.path.join(writing_root(self.root), *self.source_rel.split("/")), "wb"
        ) as handle:
            handle.write("지워도 남아야 하는 원고".encode("utf-8"))
        self.document_uuid = creation.node_for_path(self.root, self.source_rel)["uuid"]

    def _node(self, node_uuid):
        for node in read_identity(self.root)["nodes"]:
            if node["uuid"] == node_uuid:
                return node
        return None

    def test_trashing_keeps_the_uuid_and_reparents_under_trash(self):
        trash_uuid = creation.node_for_path(self.root, creation.TRASH_PATH)["uuid"]

        trashed_rel = self.wpm.move_to_trash(self.source_rel)

        node = self._node(self.document_uuid)
        self.assertIsNotNone(node)
        self.assertEqual(node["parent_uuid"], trash_uuid)
        self.assertEqual(node["legacy_path"], trashed_rel)

        # The project still opens: identity and the tree agree.
        report = creation.audit(self.root)
        self.assertEqual(report["missing_on_disk"], [])
        self.assertEqual(report["missing_in_identity"], [])
        self.assertEqual(report["pending_journals"], [])
        self.assertEqual(
            creation.prepare_open(self.root)["status"], creation.OPEN_OK
        )

        # The bytes are still there, so a backup can still carry them.
        with open(
            os.path.join(writing_root(self.root), *trashed_rel.split("/")), "rb"
        ) as handle:
            self.assertEqual(
                handle.read().decode("utf-8"), "지워도 남아야 하는 원고"
            )

    def test_restoring_from_trash_reuses_the_same_uuid(self):
        trashed_rel = self.wpm.move_to_trash(self.source_rel)
        memo_uuid = creation.node_for_path(self.root, "메인/메모장")["uuid"]

        restored_rel = self.wpm.restore_from_trash(trashed_rel)

        self.assertEqual(restored_rel, self.source_rel)
        node = self._node(self.document_uuid)
        self.assertEqual(node["legacy_path"], self.source_rel)
        self.assertEqual(node["parent_uuid"], memo_uuid)
        self.assertEqual(creation.audit(self.root)["missing_in_identity"], [])

    def test_trashing_a_folder_moves_its_children_without_renumbering(self):
        memo = creation.node_for_path(self.root, "메인/메모장")
        create_item(self.root, memo["uuid"], "묶음", True, uuid_factory=self.uuids)
        folder = creation.node_for_path(self.root, "메인/메모장/묶음")
        create_item(self.root, folder["uuid"], "안쪽", False, uuid_factory=self.uuids)
        child_uuid = creation.node_for_path(
            self.root, "메인/메모장/묶음/안쪽.txt"
        )["uuid"]

        trashed_rel = self.wpm.move_to_trash("메인/메모장/묶음")

        self.assertEqual(self._node(folder["uuid"])["legacy_path"], trashed_rel)
        child = self._node(child_uuid)
        self.assertEqual(child["uuid"], child_uuid)
        self.assertEqual(child["legacy_path"], f"{trashed_rel}/안쪽.txt")
        self.assertEqual(child["parent_uuid"], folder["uuid"])
        self.assertEqual(creation.audit(self.root)["missing_in_identity"], [])


class InitializeExistingProjectTestCase(unittest.TestCase):
    """가져오기 전용 제자리 초기화. 일반 열기 경로에서는 쓰이지 않는다."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "작품목록"
        self.workspace.mkdir(parents=True)
        self.uuids = seq_uuids()
        self.addCleanup(self.temp_dir.cleanup)

    def _reserved(self, title="가져온 작품", state="preparing"):
        """Reserve a destination the way server import does, marker and all."""
        from project_paths import IMPORT_MARKER_FILENAME

        project_root = self.workspace / title
        (project_root / "집필모드").mkdir(parents=True)
        marker = project_root / IMPORT_MARKER_FILENAME
        marker.write_text(
            json.dumps({"project_id": "server-uuid", "state": state}),
            encoding="utf-8",
        )
        return str(project_root), marker

    def test_reserved_marker_survives_initialization(self):
        project_root, marker = self._reserved()
        before = marker.read_bytes()

        identity = creation.initialize_existing_project(
            str(self.workspace), "가져온 작품", uuid_factory=self.uuids
        )

        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual(len(identity["nodes"]), len(STANDARD_FOLDERS))
        self.assertTrue(
            os.path.isdir(os.path.join(writing_root(project_root), "메인", "원고"))
        )
        self.assertEqual(creation.audit(project_root)["missing_on_disk"], [])

    def test_interrupted_initialization_recovers_with_the_same_uuids(self):
        self._reserved()

        with patch(
            "project_creation_v1.write_identity",
            side_effect=OSError("simulated crash before identity write"),
        ):
            with self.assertRaises(OSError):
                creation.initialize_existing_project(
                    str(self.workspace), "가져온 작품", uuid_factory=self.uuids
                )

        journals = os.listdir(workspace_journal_dir(str(self.workspace)))
        self.assertEqual(len(journals), 1)
        with open(
            os.path.join(workspace_journal_dir(str(self.workspace)), journals[0]),
            encoding="utf-8",
        ) as handle:
            journalled = json.load(handle)

        recover_workspace(str(self.workspace))
        identity = read_identity(str(self.workspace / "가져온 작품"))

        self.assertEqual(
            identity["project"]["uuid"], journalled["project"]["uuid"]
        )
        self.assertEqual(
            [node["uuid"] for node in identity["nodes"]],
            [node["uuid"] for node in journalled["nodes"]],
        )

    def test_a_half_built_project_is_not_listed_as_a_normal_project(self):
        from project_manager import ProjectManager

        self._reserved(state="preparing")
        with patch(
            "project_creation_v1.write_identity",
            side_effect=OSError("simulated crash before identity write"),
        ):
            with self.assertRaises(OSError):
                creation.initialize_existing_project(
                    str(self.workspace), "가져온 작품", uuid_factory=self.uuids
                )

        manager = ProjectManager()
        manager.workspace_dir = str(self.workspace)
        manager.global_config = {}
        manager.save_global_config = lambda: None

        listed = manager.get_all_projects()
        self.assertNotIn("가져온 작품", listed)
        # The workspace journal directory is not a project either.
        self.assertEqual(listed, [])

    def test_opening_a_normal_project_never_initializes(self):
        create_project(str(self.workspace), "정상 작품", uuid_factory=self.uuids)
        project_root = str(self.workspace / "정상 작품")

        with patch.object(
            creation, "initialize_existing_project"
        ) as never_called:
            verdict = creation.prepare_open(project_root)

        self.assertEqual(verdict["status"], creation.OPEN_OK)
        never_called.assert_not_called()

    def test_only_the_import_path_calls_initialize_existing_project(self):
        """제품 코드에서 호출 가능한 곳은 명시적 가져오기 경로뿐이다."""
        repository = Path(__file__).resolve().parent.parent
        callers = set()
        for module in repository.glob("*.py"):
            text = module.read_text(encoding="utf-8", errors="ignore")
            if "initialize_existing_project" in text:
                callers.add(module.name)

        self.assertEqual(
            callers, {"project_creation_v1.py", "server_project_import.py"}
        )


if __name__ == "__main__":
    unittest.main()
