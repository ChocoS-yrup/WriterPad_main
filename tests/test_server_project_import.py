import json
import shutil
import sqlite3
import tempfile
import threading
import unicodedata
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from project_manager import ProjectManager
from project_manager_writing import WritingProjectManager
from project_paths import PROJECT_NAME_CONFLICT, LocalProjectPathError
from server_project_import import (
    IMPORT_ALREADY_COMPLETE,
    IMPORT_IN_PROGRESS,
    LOCAL_STORAGE_ERROR,
    NETWORK_UNAVAILABLE,
    PENDING_LOCAL_CHANGES,
    SNAPSHOT_APPLY_FAILED,
    ServerProjectImportError,
    ServerProjectImportService,
)
from sync_manager import SyncManager
from sync_v2_store import SyncV2Store


class _DocumentQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.project_id = None

    def select(self, fields):
        self.client.selects.append((self.table_name, fields))
        return self

    def eq(self, field, value):
        if field == "project_id":
            self.project_id = value
        return self

    def execute(self):
        if self.client.block_started is not None:
            self.client.block_started.set()
            self.client.block_release.wait(timeout=5)
        if self.table_name == "documents" and self.client.document_error is not None:
            raise self.client.document_error
        source = (
            self.client.folders
            if self.table_name == "folders"
            else self.client.documents
        )
        rows = [
            dict(row) for row in source
            if row.get("project_id", self.project_id) == self.project_id
        ]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, documents=None, folders=None):
        self.documents = list(documents or [])
        self.folders = list(folders or [])
        self.document_error = None
        self._antigravity_authenticated = True
        self.selects = []
        self.rpc_calls = []
        self.block_started = None
        self.block_release = None

    def table(self, name):
        return _DocumentQuery(self, name)

    def rpc(self, name, payload):
        self.rpc_calls.append((name, payload))
        raise AssertionError("서버 작품 가져오기 중에는 RPC 쓰기를 호출하면 안 됩니다.")


def _remote_document(
    project_id,
    document_id=None,
    path="메인/원고/001화.txt",
    content="서버 원고",
    revision=1,
    is_deleted=False,
):
    return {
        "project_id": project_id,
        "document_id": document_id or str(uuid.uuid4()),
        "relative_path": path,
        "content": content,
        "revision": revision,
        "is_deleted": is_deleted,
        "deleted_at": "2026-07-28T09:00:00Z" if is_deleted else None,
        "updated_at": "2026-07-28T10:00:00Z",
    }


def _remote_folder(project_id, folder_id, parent_folder_id, name):
    return {
        "project_id": project_id,
        "folder_id": folder_id,
        "parent_folder_id": parent_folder_id,
        "name": name,
        "revision": 1,
        "is_deleted": False,
        "updated_at": "2026-07-28T10:00:00Z",
    }


class ServerProjectImportTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name, "작품목록")
        self.workspace.mkdir()
        self.store = SyncV2Store(
            str(Path(self.temp_dir.name, "sync.sqlite3"))
        )
        self.manager = SyncManager()
        self.previous_manager_state = (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            self.manager._v2_device_id,
            self.manager._v2_protected_paths_provider,
            self.manager._v2_active_paths_provider,
            self.manager.supabase,
        )
        self.addCleanup(self._restore_manager)
        self.client = _FakeSupabase()
        self.manager._v2_store = None
        self.manager._v2_context = None
        self.manager._v2_wpm = None
        self.manager._v2_device_id = None
        self.manager._v2_protected_paths_provider = None
        self.manager._v2_active_paths_provider = None
        self.manager.supabase = self.client
        self.device_id = str(uuid.uuid4())

    def _restore_manager(self):
        (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            self.manager._v2_device_id,
            self.manager._v2_protected_paths_provider,
            self.manager._v2_active_paths_provider,
            self.manager.supabase,
        ) = self.previous_manager_state

    def _service(self):
        return ServerProjectImportService(
            self.manager,
            self.store,
            str(self.workspace),
            self.device_id,
        )

    def _visible_projects(self):
        manager = ProjectManager.__new__(ProjectManager)
        manager.workspace_dir = str(self.workspace)
        manager.config_path = str(Path(self.temp_dir.name, "config.json"))
        manager.global_config = {"last_project": "", "project_order": []}
        return manager.get_all_projects()

    def test_success_preserves_document_identity_revision_path_and_content(self):
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        self.client.documents = [
            _remote_document(
                project_id,
                document_id=document_id,
                path="메인/원고/1권/001화.txt",
                content="iPad에서 작성한 본문",
                revision=4,
            )
        ]

        result = self._service().import_project(
            project_id, "iPad 서버 작품", "가져온 작품"
        )

        binding = self.store.get_project_by_id(project_id)
        document = self.store.get_document_by_id(document_id)
        manuscript = Path(
            result.writing_root_path, "메인", "원고", "1권", "001화.txt"
        )
        self.assertEqual(binding["project_name"], "가져온 작품")
        self.assertEqual(binding["server_name"], "iPad 서버 작품")
        self.assertEqual(document["document_id"], document_id)
        self.assertEqual(document["revision"], 4)
        self.assertEqual(document["server_path"], "메인/원고/1권/001화.txt")
        self.assertEqual(manuscript.read_text(encoding="utf-8"), "iPad에서 작성한 본문")
        self.assertEqual(self.store.get_project_import(project_id)["state"], "complete")
        self.assertEqual(result.document_count, 1)
        self.assertEqual(self._visible_projects(), ["가져온 작품"])
        self.assertEqual(
            self.store.counts(binding["local_key"])["total"], 0
        )
        self.assertEqual(self.client.rpc_calls, [])

    def test_ipad_decomposed_hangul_path_imports_into_windows_nfc_folders(self):
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        nfc_path = "메인/원고/1권/001화.txt"
        nfd_path = unicodedata.normalize("NFD", nfc_path)
        self.assertNotEqual(nfd_path, nfc_path)
        self.client.documents = [
            _remote_document(
                project_id,
                document_id=document_id,
                path=nfd_path,
                content="아이패드 원고",
                revision=2,
            )
        ]

        result = self._service().import_project(
            project_id, "iPad 분리형 경로 작품"
        )

        nfc_file = Path(result.writing_root_path, *nfc_path.split("/"))
        nfd_file = Path(result.writing_root_path, *nfd_path.split("/"))
        document = self.store.get_document_by_id(document_id)
        self.assertEqual(nfc_file.read_text(encoding="utf-8"), "아이패드 원고")
        self.assertFalse(nfd_file.exists())
        self.assertEqual(document["local_path"], nfc_path)
        self.assertEqual(document["server_path"], nfc_path)

    def test_existing_decomposed_hangul_tree_is_repaired_on_same_revision_pull(self):
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        nfc_path = "메인/원고/1권/001화.txt"
        nfd_path = unicodedata.normalize("NFD", nfc_path)
        remote = _remote_document(
            project_id,
            document_id=document_id,
            path=nfd_path,
            content="복구할 원고",
            revision=3,
        )
        self.client.documents = [remote]
        result = self._service().import_project(project_id, "경로 복구 작품")
        nfc_file = Path(result.writing_root_path, *nfc_path.split("/"))
        nfd_file = Path(result.writing_root_path, *nfd_path.split("/"))
        nfd_file.parent.mkdir(parents=True, exist_ok=True)
        nfc_file.replace(nfd_file)
        connection = sqlite3.connect(self.store.db_path)
        try:
            connection.execute(
                """
                UPDATE sync_documents
                SET local_path = ?, server_path = ?
                WHERE document_id = ?
                """,
                (nfd_path, nfd_path, document_id),
            )
            connection.commit()
        finally:
            connection.close()

        wpm = WritingProjectManager.create_detached(
            str(self.workspace),
            result.local_project_name,
            result.writing_root_path,
        )
        self.manager.configure_v2(
            wpm,
            result.local_project_name,
            self.device_id,
            store=self.store,
            project_id=project_id,
            recover_local_changes=False,
        )
        changes = self.manager._apply_v2_remote_documents(
            [remote], strict=True
        )

        document = self.store.get_document_by_id(document_id)
        self.assertEqual(nfc_file.read_text(encoding="utf-8"), "복구할 원고")
        self.assertFalse(nfd_file.exists())
        self.assertFalse(
            Path(
                result.writing_root_path,
                unicodedata.normalize("NFD", "메인"),
            ).exists()
        )
        self.assertEqual(document["local_path"], nfc_path)
        self.assertEqual(document["server_path"], nfc_path)
        self.assertEqual(document["revision"], 3)
        self.assertEqual(changes[0]["old_local_path"], nfd_path)
        self.assertEqual(changes[0]["new_local_path"], nfc_path)

    def test_unicode_path_repair_never_overwrites_different_nfc_content(self):
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        nfc_path = "메인/원고/1권/001화.txt"
        nfd_path = unicodedata.normalize("NFD", nfc_path)
        remote = _remote_document(
            project_id,
            document_id=document_id,
            path=nfd_path,
            content="서버 원고",
            revision=3,
        )
        self.client.documents = [remote]
        result = self._service().import_project(project_id, "충돌 보호 작품")
        nfc_file = Path(result.writing_root_path, *nfc_path.split("/"))
        nfd_file = Path(result.writing_root_path, *nfd_path.split("/"))
        nfd_file.parent.mkdir(parents=True, exist_ok=True)
        nfc_file.replace(nfd_file)
        nfc_file.parent.mkdir(parents=True, exist_ok=True)
        nfc_file.write_text("기존 Windows 원고", encoding="utf-8")
        connection = sqlite3.connect(self.store.db_path)
        try:
            connection.execute(
                """
                UPDATE sync_documents
                SET local_path = ?, server_path = ?
                WHERE document_id = ?
                """,
                (nfd_path, nfd_path, document_id),
            )
            connection.commit()
        finally:
            connection.close()
        wpm = WritingProjectManager.create_detached(
            str(self.workspace),
            result.local_project_name,
            result.writing_root_path,
        )
        self.manager.configure_v2(
            wpm,
            result.local_project_name,
            self.device_id,
            store=self.store,
            project_id=project_id,
            recover_local_changes=False,
        )

        with self.assertRaises(FileExistsError):
            self.manager._apply_v2_remote_documents([remote], strict=True)

        self.assertEqual(
            nfc_file.read_text(encoding="utf-8"), "기존 Windows 원고"
        )
        self.assertEqual(nfd_file.read_text(encoding="utf-8"), "서버 원고")
        self.assertEqual(
            self.store.get_document_by_id(document_id)["local_path"], nfd_path
        )

    def test_unicode_path_repair_protects_nfc_file_when_legacy_copy_is_missing(self):
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        nfc_path = "메인/원고/1권/001화.txt"
        nfd_path = unicodedata.normalize("NFD", nfc_path)
        remote = _remote_document(
            project_id,
            document_id=document_id,
            path=nfd_path,
            content="서버 원고",
            revision=3,
        )
        self.client.documents = [remote]
        result = self._service().import_project(project_id, "누락 경로 보호 작품")
        nfc_file = Path(result.writing_root_path, *nfc_path.split("/"))
        nfc_file.write_text("기존 Windows 원고", encoding="utf-8")
        connection = sqlite3.connect(self.store.db_path)
        try:
            connection.execute(
                """
                UPDATE sync_documents
                SET local_path = ?, server_path = ?
                WHERE document_id = ?
                """,
                (nfd_path, nfd_path, document_id),
            )
            connection.commit()
        finally:
            connection.close()
        wpm = WritingProjectManager.create_detached(
            str(self.workspace),
            result.local_project_name,
            result.writing_root_path,
        )
        self.manager.configure_v2(
            wpm,
            result.local_project_name,
            self.device_id,
            store=self.store,
            project_id=project_id,
            recover_local_changes=False,
        )

        with self.assertRaises(FileExistsError):
            self.manager._apply_v2_remote_documents([remote], strict=True)

        self.assertEqual(
            nfc_file.read_text(encoding="utf-8"), "기존 Windows 원고"
        )
        self.assertEqual(
            self.store.get_document_by_id(document_id)["local_path"], nfd_path
        )

    def test_same_uuid_cannot_create_a_second_project_or_binding(self):
        project_id = str(uuid.uuid4())
        self.client.documents = [_remote_document(project_id)]
        service = self._service()
        service.import_project(project_id, "서버 작품", "첫 로컬 이름")

        with self.assertRaises(ServerProjectImportError) as caught:
            service.import_project(project_id, "서버 작품", "다른 로컬 이름")

        self.assertEqual(caught.exception.code, IMPORT_ALREADY_COMPLETE)
        self.assertEqual(len(self.store.list_projects()), 1)
        self.assertTrue(Path(self.workspace, "첫 로컬 이름").is_dir())
        self.assertFalse(Path(self.workspace, "다른 로컬 이름").exists())

    def test_empty_server_project_imports_successfully_without_server_writes(self):
        project_id = str(uuid.uuid4())

        result = self._service().import_project(project_id, "빈 서버 작품")

        self.assertEqual(result.document_count, 0)
        self.assertEqual(self.store.list_documents(
            self.store.get_project_by_id(project_id)["local_key"]
        ), [])
        self.assertTrue(Path(result.writing_root_path, "메인", "원고").is_dir())
        self.assertEqual(self.store.get_project_import(project_id)["state"], "complete")
        self.assertEqual(self.client.rpc_calls, [])

    def test_folder_projection_adopts_server_ids_without_queueing_or_projection_rows(self):
        """Import keeps the fielded folder-ID contract and repeat pull semantics."""
        project_id = str(uuid.uuid4())
        tree_document_id = str(uuid.uuid4())
        root_names = [
            "원고", "캐릭터", "설정집", "메모장", "스토리 플롯",
            "흐름정리", "복선", "장소", "휴지통",
        ]
        folder_ids = {"메인": str(uuid.uuid4())}
        folder_ids.update({
            f"메인/{name}": str(uuid.uuid4()) for name in root_names
        })
        main_id = folder_ids["메인"]
        self.client.folders = [
            _remote_folder(project_id, main_id, None, "메인"),
            *[
                _remote_folder(
                    project_id, folder_ids[f"메인/{name}"], main_id, name
                )
                for name in root_names
            ],
        ]
        tree_order = {"<root>": root_names}
        tree_remote = _remote_document(
            project_id,
            document_id=tree_document_id,
            path="__antigravity__/tree-order.json",
            content=SyncManager._tree_order_content(tree_order),
            revision=1,
        )
        self.client.documents = [tree_remote]

        result = self._service().import_project(
            project_id, "iPad E2E 폴더 작품"
        )

        project_root = Path(result.writing_root_path).parent
        identity_path = project_root / ".writerpad" / "identity-v1.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity_folders = {
            node["legacy_path"]: node["uuid"]
            for node in identity["nodes"]
            if node["kind"] == "folder"
        }
        self.assertEqual(identity_folders, folder_ids)
        for relative_path in folder_ids:
            self.assertTrue(
                Path(result.writing_root_path, *relative_path.split("/")).is_dir(),
                relative_path,
            )

        binding = self.store.get_project_by_id(project_id)
        def operation_row_counts():
            connection = sqlite3.connect(self.store.db_path)
            try:
                return tuple(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE local_key = ?",
                        (binding["local_key"],),
                    ).fetchone()[0]
                    for table in (
                        "sync_operations", "sync_structure_operations"
                    )
                )
            finally:
                connection.close()

        self.assertEqual(self.store.list_folders(binding["local_key"]), [])
        self.assertEqual(operation_row_counts(), (0, 0))
        self.assertEqual(self.store.counts(binding["local_key"])["total"], 0)
        self.assertEqual(self.client.rpc_calls, [])

        first_identity = identity_path.read_bytes()
        first_settings = Path(
            result.writing_root_path, "설정.json"
        ).read_bytes()
        wpm = WritingProjectManager.create_detached(
            str(self.workspace),
            result.local_project_name,
            result.writing_root_path,
        )
        self.manager.configure_v2(
            wpm,
            result.local_project_name,
            self.device_id,
            store=self.store,
            project_id=project_id,
            recover_local_changes=False,
        )

        repeated_changes = self.manager._apply_v2_remote_documents(
            [dict(tree_remote)], strict=True
        )

        self.assertEqual(repeated_changes, [])
        self.assertEqual(identity_path.read_bytes(), first_identity)
        self.assertEqual(
            Path(result.writing_root_path, "설정.json").read_bytes(),
            first_settings,
        )
        self.assertEqual(self.store.list_folders(binding["local_key"]), [])
        self.assertEqual(operation_row_counts(), (0, 0))
        self.assertEqual(self.store.counts(binding["local_key"])["total"], 0)
        self.assertEqual(self.manager.pending_retry_count, 0)
        self.assertEqual(self.client.rpc_calls, [])
        self.assertEqual(
            [table_name for table_name, _fields in self.client.selects],
            ["documents", "folders"],
        )

    def test_missing_completed_local_folder_can_be_imported_again(self):
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        self.client.documents = [
            _remote_document(
                project_id,
                document_id=document_id,
                content="서버에서 복구한 원고",
            )
        ]
        first = self._service().import_project(project_id, "누락 복구 작품")
        shutil.rmtree(Path(first.writing_root_path).parent)

        recovered = self._service().import_project(
            project_id, "누락 복구 작품"
        )

        self.assertTrue(recovered.resumed)
        self.assertEqual(
            Path(
                recovered.writing_root_path, "메인", "원고", "001화.txt"
            ).read_text(encoding="utf-8"),
            "서버에서 복구한 원고",
        )
        self.assertEqual(
            self.store.get_project_import(project_id)["state"], "complete"
        )

    def test_missing_folder_with_pending_changes_is_not_overwritten(self):
        project_id = str(uuid.uuid4())
        first = self._service().import_project(project_id, "대기 변경 작품")
        binding = self.store.get_project_by_id(project_id)
        self.store.enqueue(
            {
                "local_key": binding["local_key"],
                "project_id": project_id,
                "project_name": "대기 변경 작품",
            },
            "메인/원고/001화.txt",
            "서버에 보내지 못한 원고",
        )
        shutil.rmtree(Path(first.writing_root_path).parent)

        with self.assertRaises(ServerProjectImportError) as caught:
            self._service().import_project(project_id, "대기 변경 작품")

        self.assertEqual(caught.exception.code, PENDING_LOCAL_CHANGES)

    def test_pull_failure_stays_hidden_and_retry_reuses_binding(self):
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        self.client.documents = [
            _remote_document(project_id, document_id=document_id)
        ]
        self.client.document_error = RuntimeError("connection timed out")
        service = self._service()

        with self.assertRaises(ServerProjectImportError) as caught:
            service.import_project(project_id, "재시도 작품")

        self.assertEqual(caught.exception.code, NETWORK_UNAVAILABLE)
        first_binding = self.store.get_project_by_id(project_id)
        self.assertEqual(self.store.get_project_import(project_id)["state"], "failed")
        self.assertEqual(self._visible_projects(), [])

        self.client.document_error = None
        result = service.import_project(project_id, "재시도 작품")

        self.assertTrue(result.resumed)
        self.assertEqual(
            self.store.get_project_by_id(project_id)["local_key"],
            first_binding["local_key"],
        )
        self.assertEqual(len(self.store.list_projects()), 1)
        self.assertEqual(self.store.get_document_by_id(document_id)["revision"], 1)
        self.assertEqual(self._visible_projects(), ["재시도 작품"])

    def test_remote_tombstone_and_immediate_path_reuse_follow_existing_policy(self):
        project_id = str(uuid.uuid4())
        old_id = str(uuid.uuid4())
        new_id = str(uuid.uuid4())
        shared_path = "메인/메모장/같은이름.txt"
        self.client.documents = [
            _remote_document(
                project_id, old_id, shared_path, "삭제된 본문", 2, True
            ),
            _remote_document(
                project_id, new_id, shared_path, "새 문서 본문", 1, False
            ),
        ]

        result = self._service().import_project(project_id, "경로 재사용 작품")

        old_document = self.store.get_document_by_id(old_id)
        new_document = self.store.get_document_by_id(new_id)
        self.assertTrue(old_document["is_deleted"])
        self.assertTrue(old_document["local_path"].startswith("메인/휴지통/"))
        self.assertFalse(new_document["is_deleted"])
        self.assertEqual(new_document["local_path"], shared_path)
        self.assertEqual(
            Path(result.writing_root_path, *shared_path.split("/")).read_text(
                encoding="utf-8"
            ),
            "새 문서 본문",
        )
        self.assertEqual(
            Path(
                result.writing_root_path, *old_document["local_path"].split("/")
            ).read_text(encoding="utf-8"),
            "삭제된 본문",
        )

    def test_sqlite_binding_failure_keeps_existing_projects_unchanged(self):
        existing = Path(self.workspace, "기존 작품", "집필모드", "메인", "원고")
        existing.mkdir(parents=True)
        manuscript = Path(existing, "001화.txt")
        manuscript.write_text("기존 원고", encoding="utf-8")
        project_id = str(uuid.uuid4())

        with patch.object(
            self.store,
            "begin_project_import",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertRaises(ServerProjectImportError) as caught:
                self._service().import_project(project_id, "실패 작품")

        self.assertEqual(caught.exception.code, LOCAL_STORAGE_ERROR)
        self.assertEqual(manuscript.read_text(encoding="utf-8"), "기존 원고")
        self.assertIsNone(self.store.get_project_by_id(project_id))
        self.assertEqual(self._visible_projects(), ["기존 작품"])

    def test_different_uuid_cannot_occupy_an_existing_bound_path(self):
        existing_id = str(uuid.uuid4())
        incoming_id = str(uuid.uuid4())
        writing_root = Path(self.workspace, "점유된 작품", "집필모드")
        manuscript = Path(writing_root, "메인", "원고", "001화.txt")
        manuscript.parent.mkdir(parents=True)
        manuscript.write_text("기존 UUID의 원고", encoding="utf-8")
        self.store.configure_project(
            str(writing_root), "점유된 작품", existing_id
        )

        with self.assertRaises(LocalProjectPathError) as caught:
            self._service().import_project(incoming_id, "서버 작품", "점유된 작품")

        self.assertEqual(caught.exception.code, PROJECT_NAME_CONFLICT)
        self.assertEqual(
            manuscript.read_text(encoding="utf-8"), "기존 UUID의 원고"
        )
        self.assertEqual(len(self.store.list_projects()), 1)
        self.assertIsNone(self.store.get_project_by_id(incoming_id))

    def test_document_write_failure_is_not_reported_as_complete(self):
        project_id = str(uuid.uuid4())
        self.client.documents = [_remote_document(project_id)]

        with patch.object(
            WritingProjectManager, "write_text_file", return_value=False
        ):
            with self.assertRaises(ServerProjectImportError) as caught:
                self._service().import_project(project_id, "쓰기 실패 작품")

        self.assertEqual(caught.exception.code, SNAPSHOT_APPLY_FAILED)
        self.assertEqual(self.store.get_project_import(project_id)["state"], "failed")
        self.assertEqual(self._visible_projects(), [])
        self.assertEqual(self.client.rpc_calls, [])

    def test_partial_snapshot_apply_retries_without_duplicate_documents(self):
        project_id = str(uuid.uuid4())
        first_id = str(uuid.uuid4())
        second_id = str(uuid.uuid4())
        self.client.documents = [
            _remote_document(
                project_id, first_id, "메인/메모장/첫째.txt", "첫째 본문"
            ),
            _remote_document(
                project_id, second_id, "메인/메모장/둘째.txt", "둘째 본문"
            ),
        ]
        original_write = WritingProjectManager.write_text_file

        def fail_second_document(wpm, relative_path, content):
            if relative_path == "메인/메모장/둘째.txt":
                return False
            return original_write(wpm, relative_path, content)

        service = self._service()
        with patch.object(
            WritingProjectManager,
            "write_text_file",
            new=fail_second_document,
        ):
            with self.assertRaises(ServerProjectImportError) as caught:
                service.import_project(project_id, "부분 실패 작품")

        self.assertEqual(caught.exception.code, SNAPSHOT_APPLY_FAILED)
        self.assertIsNotNone(self.store.get_document_by_id(first_id))
        self.assertIsNone(self.store.get_document_by_id(second_id))

        result = service.import_project(project_id, "부분 실패 작품")

        documents = self.store.list_documents(
            self.store.get_project_by_id(project_id)["local_key"]
        )
        self.assertTrue(result.resumed)
        self.assertEqual(len(documents), 2)
        self.assertEqual(
            {document["document_id"] for document in documents},
            {first_id, second_id},
        )
        self.assertEqual(self.store.get_project_import(project_id)["state"], "complete")

    def test_concurrent_import_of_same_uuid_allows_only_one_worker(self):
        project_id = str(uuid.uuid4())
        self.client.documents = [_remote_document(project_id)]
        self.client.block_started = threading.Event()
        self.client.block_release = threading.Event()
        service = self._service()
        first_result = []

        def run_first():
            try:
                first_result.append(service.import_project(project_id, "동시 작품"))
            except Exception as error:
                first_result.append(error)

        thread = threading.Thread(target=run_first)
        thread.start()
        try:
            self.assertTrue(
                self.client.block_started.wait(timeout=30),
                "first import did not reach the bounded fake remote read",
            )
            with self.assertRaises(ServerProjectImportError) as caught:
                service.import_project(project_id, "동시 작품")
            self.assertEqual(caught.exception.code, IMPORT_IN_PROGRESS)
        finally:
            self.client.block_release.set()
            thread.join(timeout=30)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(first_result), 1)
        self.assertNotIsInstance(first_result[0], Exception)
        self.assertEqual(len(self.store.list_projects()), 1)

    def test_incomplete_marker_contains_only_project_identity_and_safe_state(self):
        project_id = str(uuid.uuid4())
        self.client.document_error = RuntimeError("network unavailable")

        with self.assertRaises(ServerProjectImportError):
            self._service().import_project(project_id, "표시 제한 작품")

        marker_path = Path(
            self.workspace,
            "표시 제한 작품",
            ".server-project-import.json",
        )
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["project_id"], project_id)
        self.assertEqual(marker["state"], "failed")
        self.assertNotIn("user_id", marker)
        self.assertNotIn("token", marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
