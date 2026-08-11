import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from project_trash import (
    ACCESS_DENIED,
    CURRENT_PROJECT_OPEN,
    LOCAL_NAME_CONFLICT,
    PENDING_SYNC,
    ProjectTrashError,
    ProjectTrashService,
    SERVER_PROJECT_MISSING,
    SERVER_PROJECT_PURGED,
    TrashedProject,
)
from sync_v2_store import SyncV2Store


class _Response:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        self.client.calls.append((self.name, self.params))
        if self.client.error:
            raise self.client.error
        if self.name == "list_trashed_projects":
            return _Response(list(self.client.trashed.values()))
        project_id = self.params["p_project_id"]
        if self.name == "trash_project":
            row = {
                "project_id": project_id,
                "name": self.client.names[project_id],
                "trashed_at": "2026-07-28T12:00:00+00:00",
                "updated_at": "2026-07-28T12:00:00+00:00",
            }
            self.client.trashed[project_id] = row
            return _Response({"status": "trashed", **row})
        if self.name == "restore_project":
            self.client.trashed.pop(project_id, None)
            return _Response({
                "status": "active",
                "project_id": project_id,
                "restored": True,
            })
        if self.name == "purge_project":
            self.client.trashed.pop(project_id, None)
            self.client.purged.add(project_id)
            return _Response({
                "status": "purged",
                "project_id": project_id,
                "already_purged": False,
            })
        raise AssertionError(self.name)


class _FakeSupabase:
    _antigravity_authenticated = True

    def __init__(self):
        self.calls = []
        self.names = {}
        self.trashed = {}
        self.purged = set()
        self.error = None

    def rpc(self, name, params):
        return _Rpc(self, name, params)


class _ProjectManager:
    def __init__(self, root):
        self.workspace_dir = str(Path(root, "작품목록"))
        Path(self.workspace_dir).mkdir(parents=True)
        self.global_config = {"project_order": [], "last_project": ""}
        self.current_project = None
        self.saved_configs = 0

    def save_global_config(self):
        self.saved_configs += 1


class ProjectTrashServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.pm = _ProjectManager(self.temp_dir.name)
        self.store = SyncV2Store(str(Path(self.temp_dir.name, "sync.sqlite3")))
        self.server = _FakeSupabase()
        self.service = ProjectTrashService(
            self.pm,
            self.store,
            self.server,
            trash_root=str(Path(self.temp_dir.name, "작품휴지통")),
        )

    def create_project(self, name, content="보존할 원고"):
        project_path = Path(self.pm.workspace_dir, name)
        writing_root = project_path / "집필모드"
        writing_root.mkdir(parents=True)
        (writing_root / "원고.txt").write_text(content, encoding="utf-8")
        self.pm.global_config["project_order"].append(name)
        self.pm.global_config["last_project"] = name
        return project_path

    def bind_project(self, name):
        project_id = str(uuid.uuid4())
        writing_root = Path(self.pm.workspace_dir, name, "집필모드")
        self.store.configure_project(
            str(writing_root), name, project_id=project_id
        )
        self.server.names[project_id] = name
        return project_id

    def test_local_project_moves_to_trash_restores_and_purges(self):
        source = self.create_project("로컬 작품")

        entry = self.service.trash_project("로컬 작품")

        self.assertFalse(source.exists())
        self.assertTrue(entry.local_available)
        self.assertFalse(entry.is_server_project)
        self.assertEqual(
            Path(
                self.service._entry_data_path(entry.entry_id),
                "집필모드",
                "원고.txt",
            ).read_text(encoding="utf-8"),
            "보존할 원고",
        )
        self.assertEqual(self.pm.global_config["project_order"], [])
        self.assertEqual(self.service.list_projects(), [entry])

        restored_name = self.service.restore_project(entry)
        self.assertEqual(restored_name, "로컬 작품")
        self.assertEqual(
            Path(source, "집필모드", "원고.txt").read_text(encoding="utf-8"),
            "보존할 원고",
        )

        entry = self.service.trash_project("로컬 작품")
        self.assertTrue(self.service.purge_project(entry))
        self.assertFalse(Path(self.service._entry_data_path(entry.entry_id)).exists())
        self.assertEqual(self.service.list_projects(), [])

    def test_synced_project_uses_server_rpc_and_keeps_binding_until_purge(self):
        self.create_project("서버 작품")
        project_id = self.bind_project("서버 작품")

        entry = self.service.trash_project("서버 작품")

        self.assertEqual(
            self.server.calls[0],
            ("trash_project", {"p_project_id": project_id}),
        )
        self.assertIsNotNone(self.store.get_project_by_id(project_id))
        self.assertTrue(entry.server_available)

        self.service.purge_project(entry)

        self.assertEqual(self.server.calls[-1][0], "purge_project")
        self.assertIn(project_id, self.server.purged)
        self.assertEqual(
            self.store.get_project_by_id(project_id)["server_state"], "purged"
        )

    def test_locally_remaining_purged_project_moves_to_local_trash(self):
        source = self.create_project("서버 영구 삭제 작품")
        project_id = self.bind_project("서버 영구 삭제 작품")
        self.store.set_project_server_state(project_id, "purged")
        binding = self.store.get_project_by_id(project_id)
        self.store.enqueue(
            {
                "local_key": binding["local_key"],
                "project_id": project_id,
                "project_name": "서버 영구 삭제 작품",
            },
            "메인/원고/보존본.txt",
            "서버에 더 이상 보낼 수 없는 로컬 원고",
        )

        entry = self.service.trash_project("서버 영구 삭제 작품")

        self.assertFalse(source.exists())
        self.assertTrue(entry.local_available)
        self.assertFalse(entry.server_available)
        self.assertEqual(entry.project_id, "")
        self.assertEqual(self.server.calls, [])
        self.assertEqual(
            self.store.get_project_by_id(project_id)["server_state"], "purged"
        )
        self.assertEqual(
            self.service._read_metadata(entry.entry_id)["project_id"], ""
        )

    def test_stale_active_binding_falls_back_when_server_is_already_purged(self):
        source = self.create_project("다른 기기에서 영구 삭제")
        project_id = self.bind_project("다른 기기에서 영구 삭제")
        self.server.error = RuntimeError({
            "code": "P0001",
            "message": "PROJECT_PURGED",
        })

        entry = self.service.trash_project("다른 기기에서 영구 삭제")

        self.assertFalse(source.exists())
        self.assertFalse(entry.server_available)
        self.assertEqual(
            self.server.calls,
            [("trash_project", {"p_project_id": project_id})],
        )
        self.assertEqual(
            self.store.get_project_by_id(project_id)["server_state"], "purged"
        )

    def test_missing_legacy_server_project_also_moves_to_local_trash(self):
        source = self.create_project("구형 서버 삭제 작품")
        project_id = self.bind_project("구형 서버 삭제 작품")
        self.server.error = RuntimeError({
            "code": "P0001",
            "message": "PROJECT_NOT_FOUND",
        })

        entry = self.service.trash_project("구형 서버 삭제 작품")

        self.assertFalse(source.exists())
        self.assertTrue(entry.local_available)
        self.assertFalse(entry.server_available)
        self.assertEqual(entry.project_id, "")
        self.assertEqual(
            self.store.get_project_by_id(project_id)["server_state"], "purged"
        )

    def test_server_project_purged_error_is_classified(self):
        error = RuntimeError({
            "code": "P0001",
            "message": "PROJECT_PURGED",
        })

        self.assertEqual(
            self.service._classify_server_error(error),
            SERVER_PROJECT_PURGED,
        )

    def test_server_project_missing_error_is_classified(self):
        error = RuntimeError({
            "code": "P0001",
            "message": "PROJECT_NOT_FOUND",
        })

        self.assertEqual(
            self.service._classify_server_error(error),
            SERVER_PROJECT_MISSING,
        )

    def test_server_failure_never_moves_or_deletes_local_project(self):
        source = self.create_project("권한 없는 작품")
        project_id = self.bind_project("권한 없는 작품")
        self.server.error = RuntimeError({
            "code": "42501",
            "message": "permission denied for function trash_project",
        })

        with self.assertRaises(ProjectTrashError) as raised:
            self.service.trash_project("권한 없는 작품")

        self.assertEqual(raised.exception.code, ACCESS_DENIED)
        self.assertTrue(source.exists())
        self.assertIsNotNone(self.store.get_project_by_id(project_id))
        self.assertEqual(self.service._local_entries(), {})
        self.assertEqual(list(Path(self.service.index_dir).glob("*")), [])

    def test_restore_name_collision_preserves_trash_and_server_state(self):
        self.create_project("복원 충돌 작품")
        project_id = self.bind_project("복원 충돌 작품")
        entry = self.service.trash_project("복원 충돌 작품")
        Path(self.pm.workspace_dir, "복원 충돌 작품").mkdir()
        calls_before = list(self.server.calls)

        with self.assertRaises(ProjectTrashError) as raised:
            self.service.restore_project(entry)

        self.assertEqual(raised.exception.code, LOCAL_NAME_CONFLICT)
        self.assertEqual(self.server.calls, calls_before)
        self.assertTrue(Path(self.service._entry_data_path(entry.entry_id)).exists())
        self.assertIn(project_id, self.server.trashed)

    def test_local_move_failure_after_server_trash_is_safe_and_retryable(self):
        source = self.create_project("이동 재시도 작품")
        project_id = self.bind_project("이동 재시도 작품")
        real_replace = os.replace

        def fail_only_project_move(old_path, new_path):
            if os.path.abspath(old_path) == os.path.abspath(source):
                raise PermissionError("test project move failure")
            return real_replace(old_path, new_path)

        with patch("project_trash.os.replace", side_effect=fail_only_project_move):
            with self.assertRaises(ProjectTrashError):
                self.service.trash_project("이동 재시도 작품")

        self.assertTrue(source.exists())
        self.assertIn(project_id, self.server.trashed)
        metadata = self.service._read_metadata(project_id)
        self.assertEqual(metadata["state"], "server_trashed")

        entry = self.service.trash_project("이동 재시도 작품")

        self.assertFalse(source.exists())
        self.assertTrue(entry.local_available)
        self.assertEqual(
            Path(
                self.service._entry_data_path(entry.entry_id),
                "집필모드",
                "원고.txt",
            ).read_text(encoding="utf-8"),
            "보존할 원고",
        )

    def test_server_only_trash_entry_can_be_restored_or_purged(self):
        project_id = str(uuid.uuid4())
        self.server.trashed[project_id] = {
            "project_id": project_id,
            "name": "다른 Windows에서 삭제",
            "trashed_at": "2026-07-28T13:00:00+00:00",
            "updated_at": "2026-07-28T13:00:00+00:00",
        }

        entries = self.service.list_projects()

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertFalse(entry.local_available)
        self.assertTrue(entry.server_available)
        self.service.restore_project(entry)
        self.assertNotIn(project_id, self.server.trashed)

        self.server.trashed[project_id] = {
            "project_id": project_id,
            "name": entry.project_name,
            "trashed_at": entry.trashed_at,
            "updated_at": entry.trashed_at,
        }
        self.service.purge_project(
            TrashedProject(
                entry_id=project_id,
                project_name=entry.project_name,
                project_id=project_id,
                trashed_at=entry.trashed_at,
                server_available=True,
            )
        )
        self.assertIn(project_id, self.server.purged)

    def test_active_server_project_can_be_trashed_without_local_folder(self):
        project_id = str(uuid.uuid4())
        self.server.names[project_id] = "서버에만 있는 작품"

        entry = self.service.trash_server_project(
            project_id, "서버에만 있는 작품"
        )

        self.assertEqual(
            self.server.calls[-1],
            ("trash_project", {"p_project_id": project_id}),
        )
        self.assertTrue(entry.server_available)
        self.assertFalse(entry.local_available)
        self.assertFalse(Path(
            self.pm.workspace_dir, "서버에만 있는 작품"
        ).exists())

    def test_currently_open_project_cannot_be_trashed(self):
        source = self.create_project("열린 작품")
        self.pm.current_project = "열린 작품"

        with self.assertRaises(ProjectTrashError) as raised:
            self.service.trash_project("열린 작품")

        self.assertEqual(raised.exception.code, CURRENT_PROJECT_OPEN)
        self.assertTrue(source.exists())
        self.assertEqual(self.server.calls, [])

    def test_pending_or_conflicted_sync_blocks_server_project_trash(self):
        source = self.create_project("동기화 대기 작품")
        project_id = self.bind_project("동기화 대기 작품")
        binding = self.store.get_project_by_id(project_id)
        self.store.enqueue(
            {
                "local_key": binding["local_key"],
                "project_id": project_id,
                "project_name": "동기화 대기 작품",
            },
            "메인/원고/001화.txt",
            "아직 서버에 올라가지 않은 본문",
        )

        with self.assertRaises(ProjectTrashError) as raised:
            self.service.trash_project("동기화 대기 작품")

        self.assertEqual(raised.exception.code, PENDING_SYNC)
        self.assertTrue(source.exists())
        self.assertEqual(self.server.calls, [])

    def test_invalid_or_missing_metadata_never_escapes_trash_root(self):
        invalid = TrashedProject(
            entry_id="../outside",
            project_name="잘못된 항목",
        )
        outside = Path(self.temp_dir.name, "outside")
        outside.mkdir()

        with self.assertRaises(ProjectTrashError):
            self.service.purge_project(invalid)

        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
