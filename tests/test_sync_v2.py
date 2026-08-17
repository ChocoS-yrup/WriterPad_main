import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import unicodedata
import uuid
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from mode_writing import BinderTreeWidget, RenameDelegate, WritingModeWidget
from project_creation_v1 import (
    create_item_at_path,
    create_project,
    create_volume,
    node_for_path,
    writing_root,
)
from project_identity_v1 import read_identity
from project_manager_writing import WritingProjectManager
from sync_manager import (
    LockWorker,
    TRASH_PURGE_DOCUMENT_PATH,
    TREE_ORDER_DOCUMENT_PATH,
    SyncManager,
    V2QueueWorker,
    is_live_document_path,
)
from sync_v2_store import SyncV2Store
from three_way_merge import build_conflict_report, three_way_merge
from writing_controller import WritingController
from writing_tree import WritingTreeMixin


class ThreeWayMergeTestCase(unittest.TestCase):
    def test_non_overlapping_edits_merge_without_markers(self):
        base = "첫 줄\n둘째 줄\n셋째 줄\n"
        local = "첫 줄 수정\n둘째 줄\n셋째 줄\n"
        remote = "첫 줄\n둘째 줄\n셋째 줄 수정\n"

        result = three_way_merge(base, local, remote)

        self.assertFalse(result.has_conflicts)
        self.assertEqual(result.content, "첫 줄 수정\n둘째 줄\n셋째 줄 수정\n")

    def test_overlapping_edits_keep_all_three_versions(self):
        result = three_way_merge("문장\n", "내 문장\n", "서버 문장\n")

        self.assertTrue(result.has_conflicts)
        self.assertEqual(result.conflict_count, 1)
        self.assertIn("<<<<<<< 내 로컬 편집본", result.content)
        self.assertIn("||||||| 마지막 공통본", result.content)
        self.assertIn(">>>>>>> 서버 최신본", result.content)

    def test_line_edit_and_adjacent_line_insertion_merge_cleanly(self):
        base = "이름 변경 테스트\nA가 파일 이름을 바꿉니다.\nB가 이 줄을 수정합니다.\n"
        local = base + "강제 종료 후에도 남아야 하는 문장"
        remote = (
            "이름 변경 테스트\n"
            "A가 파일 이름을 바꿉니다.\n"
            "B가 이름변경과 동시에 수정했습니다."
        )

        result = three_way_merge(base, local, remote)

        self.assertFalse(result.has_conflicts)
        self.assertEqual(
            result.content,
            remote + "\n강제 종료 후에도 남아야 하는 문장\n",
        )

    def test_multiline_insertion_and_other_line_edit_merge_cleanly(self):
        base = "첫 줄\n둘째 줄\n셋째 줄\n"
        local = "첫 줄\n새 문장 1\n새 문장 2\n둘째 줄\n셋째 줄\n"
        remote = "첫 줄\n둘째 줄\n셋째 줄 수정\n"

        result = three_way_merge(base, local, remote)

        self.assertFalse(result.has_conflicts)
        self.assertEqual(
            result.content,
            "첫 줄\n새 문장 1\n새 문장 2\n둘째 줄\n셋째 줄 수정\n",
        )

    def test_separate_word_edits_on_same_line_stay_conflicted(self):
        base = "주인공은 학교에 걸어갔다.\n"
        local = "어제 주인공은 학교에 걸어갔다.\n"
        remote = "주인공은 빠르게 학교에 걸어갔다.\n"

        result = three_way_merge(base, local, remote)

        self.assertTrue(result.has_conflicts)
        self.assertIn(local, result.content)
        self.assertIn(remote, result.content)

    def test_different_insertions_at_same_boundary_stay_conflicted(self):
        base = "첫 줄\n둘째 줄\n"
        local = "첫 줄\n로컬 추가\n둘째 줄\n"
        remote = "첫 줄\n서버 추가\n둘째 줄\n"

        result = three_way_merge(base, local, remote)

        self.assertTrue(result.has_conflicts)
        self.assertIn("로컬 추가", result.content)
        self.assertIn("서버 추가", result.content)

    def test_conflict_report_is_separate_and_includes_deletions(self):
        base = "유지할 줄\n삭제 대상\n수정 대상\n"
        local = "유지할 줄\n로컬 수정\n"
        remote = "유지할 줄\n삭제 대상\n서버 수정\n"

        result = three_way_merge(base, local, remote)
        report = build_conflict_report(base, local, remote)

        self.assertTrue(result.has_conflicts)
        self.assertIn("<<<<<<< 내 로컬 편집본", result.content)
        self.assertNotIn("차이점 비교", result.content)
        # 보고서는 diff 문법 대신 사람이 읽는 문장으로 쓴다.
        self.assertIn("바꾸기 전 원본", report)
        self.assertIn("서버 최신본", report)
        self.assertIn("차이점 비교", report)
        self.assertIn("로컬 : 로컬 수정", report)
        self.assertIn("서버 : 삭제 대상", report)
        for noise in ("+++", "---", "@@"):
            self.assertNotIn(noise, report)

    def test_conflict_artifacts_keep_original_and_add_separate_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "충돌 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, "충돌 작품", "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            rel_path = "메인/원고/001화.txt"
            local = "어제 주인공은 학교에 걸어갔다.\n"
            remote = "주인공은 빠르게 학교에 걸어갔다.\n"
            base = "주인공은 학교에 걸어갔다.\n"
            self.assertTrue(wpm.write_text_file(rel_path, local))
            merge = three_way_merge(base, local, remote)
            widget = SimpleNamespace(
                wpm=wpm,
                current_loaded_file_left=rel_path,
                current_loaded_file_right=None,
                lbl_current_doc=MagicMock(),
                lbl_r_doc=MagicMock(),
            )

            WritingModeWidget.on_conflict_detected(widget, {
                "operation": {
                    "local_path": rel_path,
                    "base_content": base,
                    "content": local,
                },
                "base_content": base,
                "local_content": local,
                "merged_content": merge.content,
                "remote": {"content": remote},
            })

            conflict_dir = Path(wpm.writing_root_path, "백업", "충돌")
            artifacts = list(conflict_dir.glob("001화 *"))
            self.assertEqual(wpm.read_text_file(rel_path), local)
            self.assertEqual(len(artifacts), 4)
            comparison_path = next(
                path for path in artifacts if "차이점 비교" in path.name
            )
            comparison = comparison_path.read_text(encoding="utf-8")
            self.assertIn("바꾸기 전 원본", comparison)
            self.assertIn("주인공은 학교에 걸어갔다.", comparison)
            self.assertIn("로컬 : 어제 주인공은 학교에 걸어갔다.", comparison)
            self.assertIn("서버 : 주인공은 빠르게 학교에 걸어갔다.", comparison)
            for noise in ("+++", "---", "@@"):
                self.assertNotIn(noise, comparison)


class SyncV2StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name, "sync.sqlite3"))
        self.store = SyncV2Store(self.db_path)
        self.context = self.store.configure_project(
            str(Path(self.temp.name, "집필모드")), "테스트 작품"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_queue_survives_restart_and_keeps_operation_id(self):
        operation = self.store.enqueue(
            self.context, "메인/원고/001화.txt", "영구 보관할 내용"
        )

        reopened = SyncV2Store(self.db_path)
        queued = reopened.next_ready_operation(self.context["local_key"])

        self.assertEqual(queued["operation_id"], operation["operation_id"])
        self.assertEqual(queued["content"], "영구 보관할 내용")

    def test_append_only_guards_open_for_a_purge_and_shut_behind_it(self):
        """Only a permanent deletion may take the operation log with it.

        The attempt and event rows carry an operation_id but no foreign
        key, so nothing sweeps them up on their own. Left behind they are
        orphans pointing at an operation that no longer exists.
        """
        operation = self.store.enqueue(
            self.context, "메인/원고/001화.txt", "서버가 받아준 원고"
        )
        self.store.mark_attempt(operation["operation_id"])
        self.store.mark_success(operation["operation_id"], {"revision": 1})

        def delete_operation_directly(operation_id):
            raw = sqlite3.connect(self.db_path)
            try:
                raw.execute(
                    "DELETE FROM sync_operations WHERE operation_id = ?",
                    (operation_id,),
                )
            finally:
                raw.close()

        def count(table):
            raw = sqlite3.connect(self.db_path)
            try:
                return raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            finally:
                raw.close()

        with self.assertRaises(sqlite3.IntegrityError):
            delete_operation_directly(operation["operation_id"])

        self.assertTrue(
            self.store.purge_project_records(self.context["project_id"])
        )

        for table in (
            "sync_projects", "sync_documents", "sync_operations",
            "sync_operation_events", "sync_operation_attempts",
            "sync_purge_gate",
        ):
            self.assertEqual(count(table), 0, f"{table} 에 기록이 남았다")

        # The guard has to be shut again, not merely shut for that one call.
        second = self.store.enqueue(
            self.store.configure_project(
                str(Path(self.temp.name, "집필모드")), "테스트 작품"
            ),
            "메인/원고/002화.txt",
            "다음 회차",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            delete_operation_directly(second["operation_id"])

    def test_purge_records_leaves_an_unrelated_work_untouched(self):
        other = self.store.configure_project(
            str(Path(self.temp.name, "다른 작품", "집필모드")), "다른 작품"
        )
        kept = self.store.enqueue(other, "메인/원고/001화.txt", "남아야 할 원고")
        self.store.enqueue(self.context, "메인/원고/001화.txt", "지워질 원고")

        self.store.purge_project_records(self.context["project_id"])

        self.assertIsNone(
            self.store.get_project_by_id(self.context["project_id"])
        )
        self.assertIsNotNone(self.store.get_project_by_id(other["project_id"]))
        self.assertEqual(self.store.counts(other["local_key"])["pending"], 1)
        self.assertIsNotNone(
            self.store.get_document(other["local_key"], "메인/원고/001화.txt")
        )
        self.assertEqual(kept["local_key"], other["local_key"])

    def test_server_acknowledgement_counts_folders_not_only_documents(self):
        """A project can prove itself uploaded through folders alone.

        A brand-new binder publishes its folders before any chapter is
        written, so folders are the first thing the server ever accepts.
        """
        local_key = self.context["local_key"]
        self.assertFalse(self.store.has_server_acknowledged_commit(local_key))

        self.store.ensure_local_folder(local_key, "메인/원고/새 폴더")
        self.assertFalse(
            self.store.has_server_acknowledged_commit(local_key),
            "revision 0 은 아직 서버가 받아준 적 없다는 뜻이다",
        )

        self.store.replace_folder_snapshots(local_key, [{
            "folder_id": "3f8a6d21-94c7-4f0b-8a52-7c1d6e5b9034",
            "parent_folder_id": None,
            "local_path": "메인/원고/새 폴더",
            "name": "새 폴더",
            "revision": 1,
        }])
        self.assertTrue(self.store.has_server_acknowledged_commit(local_key))

    def test_folder_rename_intent_survives_restart_and_coalesces_chain(self):
        local_key = self.context["local_key"]
        first = self.store.record_folder_rename_intent(
            local_key,
            "메인/메모장/처음 이름",
            "메인/메모장/중간 이름",
        )

        reopened = SyncV2Store(self.db_path)
        recovered = reopened.pending_folder_rename_intent(
            local_key,
            "메인/메모장/처음 이름",
            "메인/메모장/중간 이름",
        )
        chained = reopened.record_folder_rename_intent(
            local_key,
            "메인/메모장/중간 이름",
            "메인/메모장/최종 이름",
        )

        self.assertEqual(recovered["intent_id"], first["intent_id"])
        self.assertEqual(chained["intent_id"], first["intent_id"])
        self.assertIsNone(reopened.pending_folder_rename_intent(
            local_key,
            "메인/메모장/처음 이름",
            "메인/메모장/중간 이름",
        ))
        self.assertEqual(
            reopened.pending_folder_rename_intent(
                local_key,
                "메인/메모장/처음 이름",
                "메인/메모장/최종 이름",
            )["intent_id"],
            first["intent_id"],
        )


    def test_force_kill_appends_retry_recovery_with_same_operation_id(self):
        operation = self.store.enqueue(
            self.context, "메인/원고/강제종료.txt", "종료 직전 내용"
        )
        self.store.mark_attempt(operation["operation_id"])
        self.assertEqual(self.store.operation(operation["operation_id"])["status"], "inflight")

        reopened = SyncV2Store(self.db_path)
        recovered = reopened.next_ready_operation(self.context["local_key"])

        self.assertEqual(recovered["operation_id"], operation["operation_id"])
        self.assertEqual(recovered["status"], "retry_wait")
        self.assertEqual(
            reopened.operation_attempts(operation["operation_id"])[0]["outcome"],
            "transport_unknown",
        )

    def test_two_offline_profiles_keep_independent_durable_queues(self):
        first_path = str(Path(self.temp.name, "first.sqlite3"))
        other_path = str(Path(self.temp.name, "other.sqlite3"))
        first = SyncV2Store(first_path)
        other = SyncV2Store(other_path)
        shared_project_id = str(uuid.uuid4())
        context_a = first.configure_project(
            str(Path(self.temp.name, "A", "집필모드")), "공유 작품", shared_project_id
        )
        context_b = other.configure_project(
            str(Path(self.temp.name, "B", "집필모드")), "공유 작품", shared_project_id
        )
        shared_document_id = str(uuid.uuid4())
        first.ensure_document(
            context_a["local_key"], "메인/원고/001화.txt", "공통본", shared_document_id
        )
        other.ensure_document(
            context_b["local_key"], "메인/원고/001화.txt", "공통본", shared_document_id
        )

        queued_a = first.enqueue(context_a, "메인/원고/001화.txt", "A 오프라인 편집")
        queued_b = other.enqueue(context_b, "메인/원고/001화.txt", "B 오프라인 편집")

        self.assertNotEqual(queued_a["operation_id"], queued_b["operation_id"])
        self.assertEqual(SyncV2Store(first_path).counts(context_a["local_key"])["pending"], 1)
        self.assertEqual(SyncV2Store(other_path).counts(context_b["local_key"])["pending"], 1)

    def test_success_supersedes_dependent_with_immutable_promoted_operation(self):
        first = self.store.enqueue(self.context, "메인/원고/001화.txt", "첫 저장")
        self.store.mark_attempt(first["operation_id"])
        second = self.store.enqueue(self.context, "메인/원고/001화.txt", "둘째 저장")
        self.assertIsNone(second["base_revision"])

        self.store.mark_success(first["operation_id"], {
            "revision": 1,
            "content_hash": "a" * 64,
        })
        original_dependent = self.store.operation(second["operation_id"])
        promoted = self.store.next_ready_operation(self.context["local_key"])

        self.assertEqual(original_dependent["status"], "cancelled")
        self.assertNotEqual(promoted["operation_id"], second["operation_id"])
        self.assertEqual(promoted["supersedes_operation_id"], second["operation_id"])
        self.assertEqual(promoted["base_revision"], 1)
        self.assertEqual(promoted["base_content"], "첫 저장")

    def test_unsent_saves_for_one_document_keep_immutable_intents(self):
        first = self.store.enqueue(
            self.context, "메인/원고/001화.txt", "서버 전송 중"
        )
        self.store.mark_attempt(first["operation_id"])
        second = self.store.enqueue(
            self.context, "메인/원고/001화.txt", "중간 수정"
        )
        latest = self.store.enqueue(
            self.context, "메인/원고/001화.txt", "가장 최신 수정"
        )

        self.assertNotEqual(latest["operation_id"], second["operation_id"])
        self.assertEqual(
            self.store.operation(second["operation_id"])["content"], "중간 수정"
        )
        self.assertEqual(latest["content"], "가장 최신 수정")
        counts = self.store.counts(self.context["local_key"])
        self.assertEqual(counts["inflight"], 1)
        self.assertEqual(counts["pending"], 2)
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["documents"], 1)

    def test_nonempty_pending_snapshot_is_visible_to_empty_save_guard(self):
        relative_path = "메인/원고/001화.txt"
        queued = self.store.enqueue(
            self.context, relative_path, "전송 대기 본문"
        )

        self.assertTrue(
            self.store.has_nonempty_active_content(queued["document_id"])
        )

    def test_uuid_survives_file_and_folder_moves(self):
        original = self.store.ensure_document(
            self.context["local_key"], "메인/원고/1권/001화.txt"
        )

        moved = self.store.move_local_path(
            self.context["local_key"], "메인/원고/1권", "메인/원고/2권"
        )
        current = self.store.get_document(
            self.context["local_key"], "메인/원고/2권/001화.txt"
        )

        self.assertEqual(len(moved), 1)
        self.assertEqual(current["document_id"], original["document_id"])

    def test_newer_remote_snapshot_updates_clean_document_and_path(self):
        document = self.store.ensure_document(
            self.context["local_key"], "메인/메모장/예전이름.txt", "기준본"
        )

        applied = self.store.apply_remote_snapshot(
            self.context,
            document["document_id"],
            "메인/메모장/새이름.txt",
            "서버 최신본",
            4,
            local_path="메인/메모장/새이름.txt",
        )

        current = self.store.get_document_by_id(document["document_id"])
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["previous_path"], "메인/메모장/예전이름.txt")
        self.assertEqual(current["local_path"], "메인/메몥/새이름.txt".replace("메몥", "메모장"))
        self.assertEqual(current["revision"], 4)
        self.assertEqual(current["base_content"], "서버 최신본")

    def test_remote_snapshot_never_replaces_document_with_pending_work(self):
        queued = self.store.enqueue(
            self.context, "메인/메모장/대기중.txt", "로컬 편집본"
        )

        applied = self.store.apply_remote_snapshot(
            self.context,
            queued["document_id"],
            "메인/메모장/대기중.txt",
            "서버가 더 최신",
            9,
        )

        current = self.store.get_document_by_id(queued["document_id"])
        self.assertFalse(applied["applied"])
        self.assertEqual(applied["reason"], "active_operations")
        self.assertEqual(current["revision"], 0)

    def test_clean_rebase_uses_latest_merged_snapshot_and_cancels_stale_dependents(self):
        created = self.store.enqueue(self.context, "메인/원고/001화.txt", "공통본")
        self.store.mark_success(created["operation_id"], {
            "revision": 1,
            "content_hash": "a" * 64,
        })
        first = self.store.enqueue(self.context, "메인/원고/001화.txt", "로컬 1")
        self.store.mark_attempt(first["operation_id"])
        stale_dependent = self.store.enqueue(self.context, "메인/원고/001화.txt", "로컬 최신")

        successor = self.store.rebase_clean_merge(
            first["operation_id"], 2, "서버 변경", "자동 병합본"
        )

        original_intent = self.store.operation(first["operation_id"])
        cancelled = self.store.operation(stale_dependent["operation_id"])
        self.assertEqual(original_intent["base_revision"], 1)
        self.assertEqual(original_intent["content"], "로컬 1")
        self.assertEqual(original_intent["status"], "cancelled")
        self.assertEqual(successor["base_revision"], 2)
        self.assertEqual(successor["content"], "자동 병합본")
        self.assertEqual(successor["supersedes_operation_id"], first["operation_id"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_remote_rename_rebase_keeps_uuid_and_changes_only_server_path(self):
        original = self.store.ensure_document(
            self.context["local_key"], "메인/원고/옛이름.txt", "공통본"
        )
        created = self.store.enqueue(self.context, "메인/원고/옛이름.txt", "공통본")
        self.store.mark_success(created["operation_id"], {
            "revision": 1,
            "content_hash": "a" * 64,
        })
        edited = self.store.enqueue(self.context, "메인/원고/옛이름.txt", "로컬 수정")
        self.store.move_local_path(
            self.context["local_key"], "메인/원고/옛이름.txt", "메인/원고/새이름.txt"
        )
        successor = self.store.rebase_clean_merge(
            edited["operation_id"], 2, "서버 수정", "자동 병합본",
            remote_path="메인/원고/새이름.txt",
        )

        document = self.store.get_document(
            self.context["local_key"], "메인/원고/새이름.txt"
        )
        original_operation = self.store.operation(edited["operation_id"])
        self.assertEqual(document["document_id"], original["document_id"])
        self.assertEqual(document["server_path"], "메인/원고/새이름.txt")
        self.assertEqual(original_operation["relative_path"], "메인/원고/옛이름.txt")
        self.assertEqual(successor["relative_path"], "메인/원고/새이름.txt")
        self.assertEqual(successor["supersedes_operation_id"], edited["operation_id"])

    def test_simultaneous_overlapping_save_is_kept_as_conflict(self):
        created = self.store.enqueue(self.context, "메인/원고/동시저장.txt", "공통 문장\n")
        self.store.mark_success(created["operation_id"], {
            "revision": 1,
            "content_hash": "a" * 64,
        })
        local = self.store.enqueue(self.context, "메인/원고/동시저장.txt", "A 문장\n")
        merged = three_way_merge("공통 문장\n", "A 문장\n", "B 문장\n")
        self.assertTrue(merged.has_conflicts)
        self.store.mark_conflict(
            local["operation_id"], 2, "메인/원고/동시저장.txt",
            "B 문장\n", merged.content, "A 문장\n",
        )

        self.assertEqual(self.store.operation(local["operation_id"])["status"], "conflict")
        document = self.store.get_document(
            self.context["local_key"], "메인/원고/동시저장.txt"
        )
        self.assertEqual(document["conflict_local"], "A 문장\n")
        self.assertEqual(document["conflict_remote"], "B 문장\n")

    def test_new_save_after_conflict_uses_remote_revision_as_new_base(self):
        created = self.store.enqueue(self.context, "메인/원고/001화.txt", "공통본")
        self.store.mark_success(created["operation_id"], {
            "revision": 1,
            "content_hash": "a" * 64,
        })
        conflicted = self.store.enqueue(self.context, "메인/원고/001화.txt", "내 수정")
        self.store.mark_conflict(
            conflicted["operation_id"], 2, "메인/원고/001화.txt",
            "서버 수정", "충돌 표시 병합본", "내 수정"
        )

        resolved = self.store.enqueue(self.context, "메인/원고/001화.txt", "직접 해결한 본문")

        self.assertEqual(resolved["base_revision"], 2)
        self.assertEqual(resolved["base_content"], "서버 수정")
        self.assertEqual(self.store.operation(conflicted["operation_id"])["status"], "cancelled")


class SyncAdoptsLocalIdentityTestCase(unittest.TestCase):
    """합성 임시 위치에서만 수행한다. 실제 원고와 운영 경로는 건드리지 않는다."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name, "작품목록")
        self.workspace.mkdir(parents=True)
        create_project(str(self.workspace), "합성 작품")
        self.project_root = str(self.workspace / "합성 작품")
        self.writing_root = writing_root(self.project_root)
        self.store = SyncV2Store(str(Path(self.temp.name, "sync.sqlite3")))
        self.context = self.store.configure_project(
            self.writing_root, "합성 작품"
        )

    def _identity_uuid(self, legacy_path):
        for node in read_identity(self.project_root)["nodes"]:
            if node["legacy_path"] == legacy_path:
                return node["uuid"]
        raise AssertionError(f"identity has no node for {legacy_path}")

    def test_document_reuses_the_identity_uuid_instead_of_minting_one(self):
        node = create_item_at_path(
            self.project_root, "메인/메모장", "설정 메모", False
        )["nodes"][-1]

        document = self.store.ensure_document(
            self.context["local_key"], node["legacy_path"], ""
        )

        self.assertEqual(document["document_id"], node["uuid"])

    def test_folder_reuses_the_identity_uuid_instead_of_minting_one(self):
        folder = self.store.ensure_local_folder(
            self.context["local_key"], "메인/원고"
        )

        self.assertEqual(folder["folder_id"], self._identity_uuid("메인/원고"))

    def test_every_chapter_of_a_new_volume_keeps_its_created_uuid(self):
        create_volume(self.project_root)

        for chapter in range(1, 26):
            legacy_path = f"메인/원고/1권/{chapter:03d}화.txt"
            document = self.store.ensure_document(
                self.context["local_key"], legacy_path, ""
            )
            self.assertEqual(
                document["document_id"], self._identity_uuid(legacy_path)
            )

    def test_enqueue_binds_the_identity_uuid_before_the_first_upload(self):
        node = create_item_at_path(
            self.project_root, "메인/설정집", "세계관", False
        )["nodes"][-1]

        operation = self.store.enqueue(
            self.context, node["legacy_path"], "본문", node["legacy_path"]
        )

        self.assertEqual(operation["document_id"], node["uuid"])

    def test_internal_sync_documents_still_receive_a_generated_uuid(self):
        internal = self.store.ensure_document(
            self.context["local_key"], TREE_ORDER_DOCUMENT_PATH, ""
        )

        known = {
            node["uuid"] for node in read_identity(self.project_root)["nodes"]
        }
        self.assertNotIn(internal["document_id"], known)

    def test_an_explicit_server_uuid_still_wins_over_local_identity(self):
        node = create_item_at_path(
            self.project_root, "메인/복선", "떡밥", False
        )["nodes"][-1]
        remote_id = str(uuid.uuid4())

        document = self.store.ensure_document(
            self.context["local_key"], node["legacy_path"], "", remote_id
        )

        self.assertEqual(document["document_id"], remote_id)

    def _manager(self):
        return WritingProjectManager.create_detached(
            str(self.workspace), "합성 작품", self.writing_root
        )

    def test_naming_a_new_item_moves_its_identity_instead_of_stranding_it(self):
        """새 항목은 임시 이름으로 만들어진 뒤 이름 입력으로 rename 된다."""
        created = create_item_at_path(
            self.project_root, "메인/메모장", "새_문서", False
        )["nodes"][-1]

        self._manager().rename_item(
            "메인/메모장/새_문서.txt", "메인/메모장/초고 메모.txt"
        )

        document = self.store.ensure_document(
            self.context["local_key"], "메인/메모장/초고 메모.txt", ""
        )
        self.assertEqual(document["document_id"], created["uuid"])

    def test_renaming_in_place_keeps_the_sibling_position(self):
        first = create_item_at_path(
            self.project_root, "메인/메모장", "가 메모", False
        )["nodes"][-1]
        create_item_at_path(self.project_root, "메인/메모장", "나 메모", False)

        self._manager().rename_item(
            "메인/메모장/가 메모.txt", "메인/메모장/고친 메모.txt"
        )

        moved = next(
            node for node in read_identity(self.project_root)["nodes"]
            if node["uuid"] == first["uuid"]
        )
        self.assertEqual(moved["order"], first["order"])
        self.assertEqual(moved["legacy_path"], "메인/메모장/고친 메모.txt")

    def test_renaming_a_folder_carries_its_children_paths(self):
        folder = create_item_at_path(
            self.project_root, "메인/설정집", "구세계", True
        )["nodes"][-1]
        child = create_item_at_path(
            self.project_root, "메인/설정집/구세계", "마법", False
        )["nodes"][-1]

        self._manager().rename_item(
            "메인/설정집/구세계", "메인/설정집/구세계(폐기)"
        )

        document = self.store.ensure_document(
            self.context["local_key"], "메인/설정집/구세계(폐기)/마법.txt", ""
        )
        self.assertEqual(document["document_id"], child["uuid"])
        self.assertEqual(
            self.store.ensure_local_folder(
                self.context["local_key"], "메인/설정집/구세계(폐기)"
            )["folder_id"],
            folder["uuid"],
        )

    def test_dragging_an_item_to_another_folder_reparents_its_identity(self):
        node = create_item_at_path(
            self.project_root, "메인/메모장", "옮길 메모", False
        )["nodes"][-1]
        destination = node_for_path(self.project_root, "메인/설정집")

        moved_path = self._manager().move_item(
            "메인/메모장/옮길 메모.txt", "메인/설정집"
        )

        self.assertEqual(moved_path, "메인/설정집/옮길 메모.txt")
        stored = next(
            item for item in read_identity(self.project_root)["nodes"]
            if item["uuid"] == node["uuid"]
        )
        self.assertEqual(stored["parent_uuid"], destination["uuid"])
        self.assertEqual(stored["legacy_path"], moved_path)
        self.assertEqual(
            self.store.ensure_document(
                self.context["local_key"], moved_path, ""
            )["document_id"],
            node["uuid"],
        )

    def test_a_project_without_identity_keeps_the_previous_behaviour(self):
        plain_root = str(Path(self.temp.name, "정체성없음", "집필모드"))
        context = self.store.configure_project(plain_root, "정체성없음")

        document = self.store.ensure_document(
            context["local_key"], "메인/원고/001화.txt", ""
        )

        self.assertTrue(uuid.UUID(document["document_id"]))


class _Response:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        self.client.calls.append((self.name, self.params))
        if self.name == "ensure_project":
            return _Response({"project_id": self.params["p_project_id"]})
        if self.name == "acquire_edit_lease":
            return _Response({"lease_token": str(uuid.uuid4())})
        if self.name == "release_edit_lease":
            return _Response(True)
        if self.name == "commit_document":
            return _Response({
                "status": "committed",
                "revision": self.params["p_base_revision"] + 1,
                "content_hash": "b" * 64,
            })
        if self.name == "commit_folder":
            rows = self.client.folder_rows
            wants_deleted = bool(self.params["p_is_deleted"])
            slot = (
                str(self.params["p_parent_folder_id"] or ""),
                str(self.params["p_name"]).casefold(),
            )

            def name_is_taken(exclude_id=None):
                # 서버의 live sibling 유니크 인덱스를 흉내낸다.
                for other in rows:
                    if other.get("is_deleted"):
                        continue
                    if exclude_id and str(other["folder_id"]) == str(exclude_id):
                        continue
                    taken = (
                        str(other.get("parent_folder_id") or ""),
                        str(other.get("name") or "").casefold(),
                    )
                    if taken == slot:
                        return True
                return False

            for row in rows:
                if str(row.get("folder_id")) != str(self.params["p_folder_id"]):
                    continue
                if wants_deleted and any(
                    not other.get("is_deleted")
                    and str(other.get("parent_folder_id") or "")
                    == str(row["folder_id"])
                    for other in rows
                ):
                    raise RuntimeError("FOLDER_NOT_EMPTY")
                if not wants_deleted and name_is_taken(row["folder_id"]):
                    raise RuntimeError("FOLDER_NAME_CONFLICT")
                was_deleted = bool(row.get("is_deleted"))
                row["name"] = self.params["p_name"]
                row["parent_folder_id"] = self.params["p_parent_folder_id"]
                row["revision"] = self.params["p_base_revision"] + 1
                row["is_deleted"] = wants_deleted
                return _Response({
                    "status": "committed",
                    "folder_id": row["folder_id"],
                    "operation_id": self.params["p_operation_id"],
                    "operation_kind": (
                        "delete" if wants_deleted
                        else ("restore" if was_deleted else "rename")
                    ),
                    "revision": row["revision"],
                    "parent_folder_id": row["parent_folder_id"],
                    "name": row["name"],
                    "is_deleted": wants_deleted,
                })
            if self.params["p_base_revision"] != 0:
                raise AssertionError("folder not found")
            if wants_deleted:
                # 서버는 없는 폴더를 삭제 상태로 만들어 주지 않는다.
                raise RuntimeError("INVALID_ARGUMENT")
            if name_is_taken():
                raise RuntimeError("FOLDER_NAME_CONFLICT")
            row = {
                "folder_id": str(self.params["p_folder_id"]),
                "parent_folder_id": self.params["p_parent_folder_id"],
                "name": self.params["p_name"],
                "revision": 1,
                "is_deleted": False,
            }
            rows.append(row)
            return _Response({
                "status": "committed",
                "folder_id": row["folder_id"],
                "operation_id": self.params["p_operation_id"],
                "operation_kind": "create",
                "revision": row["revision"],
                "parent_folder_id": row["parent_folder_id"],
                "name": row["name"],
                "is_deleted": False,
            })
        raise AssertionError(self.name)


class _FakeClient:
    def __init__(self):
        self.calls = []
        # Every real client can read the folder projection, and tree-order
        # commits now publish folder identity before the document lands.
        self.folder_rows = []

    def rpc(self, name, params):
        return _RpcCall(self, name, params)

    def table(self, name):
        if name != "folders":
            raise AssertionError(name)
        return _FolderQuery(self)


class _FolderQuery:
    def __init__(self, client):
        self.client = client

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _Response([dict(row) for row in self.client.folder_rows])


class _FolderAwareClient(_FakeClient):
    """A client whose folder projection already holds rows."""

    def __init__(self, folder_rows):
        super().__init__()
        self.folder_rows = [dict(row) for row in folder_rows]


class OutboundFolderCreateTestCase(unittest.TestCase):
    """합성 임시 위치에서만 수행한다. 실제 원고와 운영 경로는 건드리지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        workspace = str(Path(self.temp.name, "작품목록"))
        create_project(workspace, "폴더 발행")
        self.project_root = str(Path(workspace, "폴더 발행"))
        self.wpm = WritingProjectManager.create_detached(
            workspace, "폴더 발행", writing_root(self.project_root)
        )
        self.store = SyncV2Store(str(Path(self.temp.name, "sync.sqlite3")))
        self.context = self.store.configure_project(
            self.wpm.writing_root_path, "폴더 발행", str(uuid.uuid4())
        )
        self.manager = SyncManager()
        previous = (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            self.manager._v2_device_id,
        )
        self.addCleanup(self._restore, previous)
        self.manager._v2_store = self.store
        self.manager._v2_context = self.context
        self.manager._v2_wpm = self.wpm
        self.manager._v2_device_id = str(uuid.uuid4())
        self.operation = {
            "operation_id": str(uuid.uuid4()),
            "project_id": self.context["project_id"],
        }

    def _restore(self, previous):
        (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            self.manager._v2_device_id,
        ) = previous

    def _uuid_of(self, legacy_path):
        return node_for_path(self.project_root, legacy_path)["uuid"]

    def test_a_new_project_publishes_its_standard_folders_with_identity_uuids(self):
        client = _FolderAwareClient([])

        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["blocked"], [])
        published = {
            row["name"]: row for row in client.folder_rows
        }
        self.assertEqual(
            published["메인"]["folder_id"], self._uuid_of("메인")
        )
        self.assertEqual(
            published["원고"]["parent_folder_id"], self._uuid_of("메인")
        )
        self.assertEqual(
            published["원고"]["folder_id"], self._uuid_of("메인/원고")
        )

    def test_parents_are_published_before_their_children(self):
        client = _FolderAwareClient([])

        self.manager._commit_outbound_folder_lifecycle(self.operation, client)

        order = [
            params["p_name"]
            for name, params in client.calls if name == "commit_folder"
        ]
        self.assertEqual(order[0], "메인")
        self.assertLess(order.index("원고"), len(order))

    def test_trash_keeps_its_row_while_its_contents_stay_local(self):
        create_item_at_path(self.project_root, "메인/휴지통", "버린 폴더", True)
        client = _FolderAwareClient([])

        self.manager._commit_outbound_folder_lifecycle(self.operation, client)

        names = {row["name"] for row in client.folder_rows}
        self.assertIn("휴지통", names)
        self.assertNotIn("버린 폴더", names)

    def test_publishing_twice_does_not_create_a_second_revision(self):
        client = _FolderAwareClient([])

        first = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )
        calls_after_first = len(client.calls)
        second = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertTrue(first["created"])
        self.assertEqual(second["created"], [])
        self.assertEqual(len(client.calls), calls_after_first)
        self.assertTrue(all(row["revision"] == 1 for row in client.folder_rows))

    def test_a_foreign_identity_holding_the_name_blocks_the_create(self):
        """가져오기 프로젝트처럼 서버 폴더 UUID가 다른 경우다."""
        foreign_main = str(uuid.uuid4())
        client = _FolderAwareClient([
            {
                "folder_id": foreign_main, "parent_folder_id": None,
                "name": "메인", "revision": 1, "is_deleted": False,
            },
        ])

        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertIn("메인", result["blocked"])
        self.assertEqual(result["created"], [])
        self.assertEqual(
            [name for name, _params in client.calls], []
        )
        self.assertEqual(len(client.folder_rows), 1)

    def test_a_blocked_folder_is_reported_and_never_repaired(self):
        client = _FolderAwareClient([
            {
                "folder_id": str(uuid.uuid4()), "parent_folder_id": None,
                "name": "메인", "revision": 1, "is_deleted": False,
            },
        ])

        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        self.operation["operation_id"] = str(uuid.uuid4())
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)

        blocked = {
            record["metadata"]["entity_id"]: record["metadata"]["error_code"]
            for record in self.store.diagnostics(self.context["local_key"])
            if record["event"] == "folder_create_blocked"
        }
        # 메인 이 남의 이름에 막히면 자식 아홉은 부모가 없어 함께 멈춘다.
        self.assertEqual(blocked[self._uuid_of("메인")], "FOLDER_NAME_TAKEN")
        self.assertEqual(
            blocked[self._uuid_of("메인/원고")], "PARENT_NOT_PUBLISHED"
        )
        # 두 번 시도해도 서 있는 상태 하나당 보고는 한 줄이다.
        self.assertEqual(
            len(self.store.diagnostics(self.context["local_key"])), len(blocked)
        )

    def test_a_new_user_folder_is_published_after_the_standard_tree(self):
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        created = create_item_at_path(
            self.project_root, "메인/설정집", "구세계", True
        )["nodes"][-1]

        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["created"], ["메인/설정집/구세계"])
        published = {row["folder_id"] for row in client.folder_rows}
        self.assertIn(created["uuid"], published)

    def test_the_local_folder_row_records_the_published_identity(self):
        client = _FolderAwareClient([])

        self.manager._commit_outbound_folder_lifecycle(self.operation, client)

        stored = self.store.get_folder_by_path(
            self.context["local_key"], "메인/원고"
        )
        self.assertEqual(stored["folder_id"], self._uuid_of("메인/원고"))

    def _rows_by_name(self, client):
        return {row["name"]: row for row in client.folder_rows}

    def test_trashing_a_folder_tombstones_the_row_it_already_had(self):
        """어제 관찰된 결함이다. 폴더 삭제가 서버에 전혀 전달되지 않았다."""
        created = create_item_at_path(
            self.project_root, "메인/설정집", "구세계", True
        )["nodes"][-1]
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        self.wpm.move_to_trash("메인/설정집/구세계")

        self.operation["operation_id"] = str(uuid.uuid4())
        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["deleted"], ["메인/휴지통/구세계"])
        row = next(
            row for row in client.folder_rows
            if row["folder_id"] == created["uuid"]
        )
        self.assertTrue(row["is_deleted"])
        self.assertEqual(row["revision"], 2)

    def test_nested_folders_are_tombstoned_deepest_first(self):
        create_item_at_path(self.project_root, "메인/설정집", "구세계", True)
        create_item_at_path(
            self.project_root, "메인/설정집/구세계", "지도", True
        )
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        self.wpm.move_to_trash("메인/설정집/구세계")

        self.operation["operation_id"] = str(uuid.uuid4())
        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        # 부모부터 보내면 서버가 FOLDER_NOT_EMPTY 로 거절한다.
        deleted_names = [
            params["p_name"]
            for name, params in client.calls
            if name == "commit_folder" and params["p_is_deleted"]
        ]
        self.assertEqual(deleted_names, ["지도", "구세계"])
        self.assertEqual(len(result["deleted"]), 2)
        self.assertTrue(all(
            row["is_deleted"] for row in client.folder_rows
            if row["name"] in {"지도", "구세계"}
        ))

    def test_a_folder_never_published_is_left_alone_when_trashed(self):
        """최초 업로드 전에 이미 휴지통에 있던 폴더다. 서버가 만들어주지 않는다."""
        create_item_at_path(self.project_root, "메인/설정집", "구세계", True)
        self.wpm.move_to_trash("메인/설정집/구세계")
        client = _FolderAwareClient([])

        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["deleted"], [])
        self.assertNotIn("구세계", self._rows_by_name(client))

    def test_restoring_a_folder_brings_its_row_back_to_life(self):
        created = create_item_at_path(
            self.project_root, "메인/설정집", "구세계", True
        )["nodes"][-1]
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        trash_rel = self.wpm.move_to_trash("메인/설정집/구세계")
        self.operation["operation_id"] = str(uuid.uuid4())
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        self.wpm.restore_from_trash(trash_rel)

        self.operation["operation_id"] = str(uuid.uuid4())
        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["restored"], ["메인/설정집/구세계"])
        row = next(
            row for row in client.folder_rows
            if row["folder_id"] == created["uuid"]
        )
        self.assertFalse(row["is_deleted"])
        self.assertEqual(row["parent_folder_id"], self._uuid_of("메인/설정집"))

    def test_a_live_child_of_another_identity_blocks_the_tombstone(self):
        create_item_at_path(self.project_root, "메인/설정집", "구세계", True)
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        client.folder_rows.append({
            "folder_id": str(uuid.uuid4()),
            "parent_folder_id": self._uuid_of("메인/설정집/구세계"),
            "name": "남의 폴더", "revision": 1, "is_deleted": False,
        })
        self.wpm.move_to_trash("메인/설정집/구세계")

        self.operation["operation_id"] = str(uuid.uuid4())
        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["deleted"], [])
        self.assertIn("메인/휴지통/구세계", result["blocked"])
        row = next(
            row for row in client.folder_rows if row["name"] == "구세계"
        )
        self.assertFalse(row["is_deleted"])

    def test_the_name_frees_up_once_the_old_folder_is_tombstoned(self):
        create_item_at_path(self.project_root, "메인/설정집", "구세계", True)
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        self.wpm.move_to_trash("메인/설정집/구세계")
        replacement = create_item_at_path(
            self.project_root, "메인/설정집", "구세계", True
        )["nodes"][-1]

        self.operation["operation_id"] = str(uuid.uuid4())
        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["blocked"], [])
        self.assertEqual(result["created"], ["메인/설정집/구세계"])
        live = [
            row for row in client.folder_rows
            if row["name"] == "구세계" and not row["is_deleted"]
        ]
        self.assertEqual([row["folder_id"] for row in live], [replacement["uuid"]])

    def _pull(self, folder_rows, remote_documents, tree_order):
        """Apply one pull the way _process_v2_pull hands it to the manager."""
        documents = [{
            "document_id": str(uuid.uuid5(
                uuid.UUID(self.context["project_id"]), TREE_ORDER_DOCUMENT_PATH
            )),
            "relative_path": TREE_ORDER_DOCUMENT_PATH,
            "content": self.manager._tree_order_content(tree_order),
            "revision": 1,
            "is_deleted": False,
        }]
        documents.extend(remote_documents)
        return self.manager._apply_v2_remote_documents(
            documents, folder_rows=folder_rows, strict=False
        )

    def test_a_folder_deleted_elsewhere_does_not_stay_in_the_binder(self):
        """어제 관찰된 결함의 거울상이다. iPad 가 지운 폴더가 Windows 에 남는가."""
        folder = create_item_at_path(
            self.project_root, "메인/설정집", "구세계", True
        )["nodes"][-1]
        document = create_item_at_path(
            self.project_root, "메인/설정집/구세계", "마법", False
        )["nodes"][-1]
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        self.assertTrue(
            Path(self.wpm.writing_root_path, "메인/설정집/구세계").is_dir()
        )

        # iPad 가 폴더를 휴지통으로 보냈다. 문서 tombstone 과 폴더 tombstone 이
        # 함께 온다.
        for row in client.folder_rows:
            if row["folder_id"] == folder["uuid"]:
                row["is_deleted"] = True
                row["revision"] = 2
        self._pull(
            client.folder_rows,
            [{
                "document_id": document["uuid"],
                "relative_path": "메인/설정집/구세계/마법.txt",
                "content": "",
                "revision": 2,
                "is_deleted": True,
            }],
            {"<root>": ["설정집"], "메인/설정집": []},
        )

        remaining = Path(self.wpm.writing_root_path, "메인/설정집/구세계")
        self.assertFalse(
            remaining.exists(),
            "원격에서 지운 폴더가 바인더에 빈 폴더로 남았다",
        )

    def _publish_then_delete_remotely(self, extra_files=()):
        folder = create_item_at_path(
            self.project_root, "메인/설정집", "구세계", True
        )["nodes"][-1]
        client = _FolderAwareClient([])
        self.manager._commit_outbound_folder_lifecycle(self.operation, client)
        for name, text in extra_files:
            Path(
                self.wpm.writing_root_path, "메인/설정집/구세계", name
            ).write_text(text, encoding="utf-8")
        for row in client.folder_rows:
            if row["folder_id"] == folder["uuid"]:
                row["is_deleted"] = True
                row["revision"] = 2
        return folder, client

    def test_following_a_remote_delete_keeps_every_byte_left_inside(self):
        folder, client = self._publish_then_delete_remotely(
            extra_files=[("아직 안 올린 초고.txt", "잃으면 안 되는 본문")]
        )

        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})

        survivors = list(
            Path(self.wpm.writing_root_path, "메인/휴지통").rglob(
                "아직 안 올린 초고.txt"
            )
        )
        self.assertEqual(len(survivors), 1)
        self.assertEqual(
            survivors[0].read_text(encoding="utf-8"), "잃으면 안 되는 본문"
        )

    def test_following_a_remote_delete_keeps_the_folder_uuid(self):
        folder, client = self._publish_then_delete_remotely()

        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})

        moved = next(
            node for node in read_identity(self.project_root)["nodes"]
            if node["uuid"] == folder["uuid"]
        )
        self.assertTrue(moved["legacy_path"].startswith("메인/휴지통/"))

    def test_following_a_remote_delete_does_not_start_a_publish_fight(self):
        """따라간 뒤 다시 live 로 올리면 두 기기가 서로를 되돌린다."""
        folder, client = self._publish_then_delete_remotely()
        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})

        self.operation["operation_id"] = str(uuid.uuid4())
        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["created"], [])
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["deleted"], [])
        row = next(
            row for row in client.folder_rows
            if row["folder_id"] == folder["uuid"]
        )
        self.assertTrue(row["is_deleted"])
        self.assertEqual(row["revision"], 2)

    def test_a_second_pull_does_not_move_the_folder_again(self):
        folder, client = self._publish_then_delete_remotely()
        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})
        first = sorted(
            path.name
            for path in Path(self.wpm.writing_root_path, "메인/휴지통").iterdir()
        )

        changes = self._pull(client.folder_rows, [], {"<root>": ["설정집"]})

        self.assertEqual(
            [change for change in changes
             if change.get("kind") == "folder_tombstone"],
            [],
        )
        self.assertEqual(
            sorted(
                path.name
                for path in Path(
                    self.wpm.writing_root_path, "메인/휴지통"
                ).iterdir()
            ),
            first,
        )

    def test_an_open_document_protects_its_folder_from_the_remote_delete(self):
        folder, client = self._publish_then_delete_remotely()
        self.manager.set_remote_protected_paths_provider(
            lambda: {"메인/설정집/구세계/열린 문서.txt"}
        )
        self.addCleanup(
            self.manager.set_remote_protected_paths_provider, None
        )

        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})

        self.assertTrue(
            Path(self.wpm.writing_root_path, "메인/설정집/구세계").is_dir()
        )

    def test_a_folder_restored_elsewhere_comes_back_out_of_the_trash(self):
        folder, client = self._publish_then_delete_remotely()
        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})
        for row in client.folder_rows:
            if row["folder_id"] == folder["uuid"]:
                row["is_deleted"] = False
                row["revision"] = 3

        # 복원한 기기는 tree_order 에도 그 폴더를 다시 올린다.
        changes = self._pull(
            client.folder_rows, [],
            {"<root>": ["설정집"], "메인/설정집": ["구세계"]},
        )

        self.assertTrue(
            Path(self.wpm.writing_root_path, "메인/설정집/구세계").is_dir()
        )
        self.assertEqual(
            [change["kind"] for change in changes
             if change.get("kind") == "folder_restore"],
            ["folder_restore"],
        )
        moved = next(
            node for node in read_identity(self.project_root)["nodes"]
            if node["uuid"] == folder["uuid"]
        )
        self.assertEqual(moved["legacy_path"], "메인/설정집/구세계")

    def test_following_a_remote_restore_does_not_start_a_delete_fight(self):
        """복원을 안 따라가면 바깥으로 내보내는 쪽이 상대의 복원을 되돌린다."""
        folder, client = self._publish_then_delete_remotely()
        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})
        for row in client.folder_rows:
            if row["folder_id"] == folder["uuid"]:
                row["is_deleted"] = False
                row["revision"] = 3
        self._pull(
            client.folder_rows, [],
            {"<root>": ["설정집"], "메인/설정집": ["구세계"]},
        )

        self.operation["operation_id"] = str(uuid.uuid4())
        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(result["deleted"], [])
        row = next(
            row for row in client.folder_rows
            if row["folder_id"] == folder["uuid"]
        )
        self.assertFalse(row["is_deleted"])
        self.assertEqual(row["revision"], 3)

    def test_an_occupied_target_leaves_the_restored_folder_in_the_trash(self):
        folder, client = self._publish_then_delete_remotely()
        self._pull(client.folder_rows, [], {"<root>": ["설정집"]})
        create_item_at_path(self.project_root, "메인/설정집", "구세계", True)
        for row in client.folder_rows:
            if row["folder_id"] == folder["uuid"]:
                row["is_deleted"] = False
                row["revision"] = 3

        # 복원한 기기는 tree_order 에도 그 폴더를 다시 올린다.
        changes = self._pull(
            client.folder_rows, [],
            {"<root>": ["설정집"], "메인/설정집": ["구세계"]},
        )

        self.assertEqual(
            [change for change in changes
             if change.get("kind") == "folder_restore"],
            [],
        )
        blocked = [
            record["metadata"]["error_code"]
            for record in self.store.diagnostics(self.context["local_key"])
            if record["event"] == "folder_restore_blocked"
        ]
        self.assertEqual(blocked, ["RESTORE_TARGET_TAKEN"])

    def _refusing_client(self, code, target_name):
        """A client whose commit_folder refuses one folder the way a server does."""
        client = _FolderAwareClient([])
        original = client.rpc

        def rpc(name, params):
            if name == "commit_folder" and params["p_name"] == target_name:
                raise RuntimeError(f"{code}: 서버가 거절했습니다.")
            return original(name, params)

        client.rpc = rpc
        return client

    def test_a_refusal_that_retrying_cannot_change_is_stepped_over(self):
        for code in (
            "PARENT_FOLDER_NOT_FOUND",
            "FOLDER_NAME_CONFLICT",
            "FOLDER_CYCLE",
            "FOLDER_ALREADY_EXISTS",
            "FOLDER_NOT_FOUND",
            "FOLDER_NOT_EMPTY",
            "INVALID_ARGUMENT",
        ):
            with self.subTest(code=code):
                store = SyncV2Store(str(Path(self.temp.name, f"{code}.sqlite3")))
                self.manager._v2_store = store
                self.manager._v2_context = store.configure_project(
                    self.wpm.writing_root_path, "폴더 발행", str(uuid.uuid4())
                )
                client = self._refusing_client(code, "설정집")
                operation = {
                    "operation_id": str(uuid.uuid4()),
                    "project_id": self.manager._v2_context["project_id"],
                }

                result = self.manager._commit_outbound_folder_lifecycle(
                    operation, client
                )

                self.assertIn("메인/설정집", result["blocked"])
                # 거절 하나가 나머지 폴더 줄을 세우지 않는다.
                self.assertIn("메인/원고", result["created"])
                self.assertIn("메인/휴지통", result["created"])

    def test_a_refusal_is_reported_once_no_matter_how_often_it_repeats(self):
        client = self._refusing_client("FOLDER_NAME_CONFLICT", "설정집")

        for _ in range(3):
            self.operation["operation_id"] = str(uuid.uuid4())
            self.manager._commit_outbound_folder_lifecycle(self.operation, client)

        blocked = [
            record for record in self.store.diagnostics(self.context["local_key"])
            if record["metadata"].get("error_code") == "FOLDER_NAME_CONFLICT"
        ]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(
            blocked[0]["metadata"]["entity_id"], self._uuid_of("메인/설정집")
        )

    def test_a_failure_that_retrying_can_change_still_reaches_the_queue(self):
        """연결 실패는 재시도해야 한다. 영구 거절과 섞이면 안 된다."""
        client = self._refusing_client("network timeout", "설정집")

        with self.assertRaises(RuntimeError):
            self.manager._commit_outbound_folder_lifecycle(self.operation, client)

    def test_parent_not_found_is_not_read_as_the_shorter_folder_not_found(self):
        self.assertEqual(
            SyncManager._stable_error_code(
                RuntimeError("PARENT_FOLDER_NOT_FOUND: 부모가 없습니다.")
            ),
            "PARENT_FOLDER_NOT_FOUND",
        )

    def test_a_project_without_identity_publishes_nothing(self):
        plain_root = str(Path(self.temp.name, "정체성없음", "집필모드"))
        self.manager._v2_wpm = SimpleNamespace(writing_root_path=plain_root)
        client = _FolderAwareClient([])

        result = self.manager._commit_outbound_folder_lifecycle(
            self.operation, client
        )

        self.assertEqual(
            result,
            {"created": [], "restored": [], "deleted": [], "blocked": []},
        )
        self.assertEqual(client.calls, [])


class SyncV2RpcTestCase(unittest.TestCase):
    def test_lease_conflict_retry_uses_bounded_progressive_backoff(self):
        self.assertEqual(
            [
                SyncManager._v2_follow_up_delay_ms(
                    "retry", "LEASE_CONFLICT: 다른 기기에서 편집 중", attempt
                )
                for attempt in (1, 2, 3, 4, 20)
            ],
            [3000, 5000, 10000, 30000, 30000],
        )
        self.assertEqual(
            SyncManager._v2_follow_up_delay_ms("committed"),
            0,
        )
        self.assertEqual(
            SyncManager._v2_follow_up_delay_ms(
                "retry", "NETWORK_UNAVAILABLE"
            ),
            5000,
        )
        self.assertEqual(
            [
                SyncManager._v2_follow_up_delay_ms(
                    "retry", "network timeout", network_attempt=attempt
                )
                for attempt in (1, 2, 3, 4, 20)
            ],
            [5000, 15000, 30000, 60000, 60000],
        )

    def test_lease_retry_timer_is_coalesced_and_scoped_to_current_project(self):
        manager = SyncManager()
        previous = (
            manager._v2_context,
            manager._v2_retry_timer,
            manager._v2_retry_context,
        )
        timer = MagicMock()
        timer.isActive.return_value = False
        manager._v2_context = {
            "local_key": "project-a-local",
            "project_id": str(uuid.uuid4()),
            "server_state": "active",
        }
        manager._v2_retry_timer = timer
        try:
            self.assertTrue(manager._schedule_v2_retry(3000))
            timer.start.assert_called_once_with(3000)

            timer.isActive.return_value = True
            timer.remainingTime.return_value = 1200
            self.assertFalse(manager._schedule_v2_retry(5000))
            timer.start.assert_called_once_with(3000)

            manager._v2_context = {
                "local_key": "project-b-local",
                "project_id": str(uuid.uuid4()),
                "server_state": "active",
            }
            self.assertTrue(manager._schedule_v2_retry(3000))
            self.assertEqual(timer.start.call_count, 2)
        finally:
            (
                manager._v2_context,
                manager._v2_retry_timer,
                manager._v2_retry_context,
            ) = previous

    def test_manual_retry_skips_scheduled_backoff_without_replacing_queue_item(self):
        manager = SyncManager()
        previous = (
            manager._v2_context,
            manager._v2_store,
            manager._v2_device_id,
            manager._v2_worker,
            manager._v2_structure_worker,
            manager._active_server_syncs,
            manager._v2_retry_timer,
            manager._v2_retry_context,
            manager._auth_retry_blocked,
            manager._shutting_down,
        )
        operation = {
            "operation_id": str(uuid.uuid4()),
            "content": "보존할 최신 원고",
        }
        timer = MagicMock()
        timer.isActive.return_value = True
        store = SimpleNamespace(next_ready_operation=MagicMock(return_value=operation))
        manager._v2_context = {
            "local_key": "project-a-local",
            "project_id": str(uuid.uuid4()),
            "server_state": "active",
        }
        manager._v2_store = store
        manager._v2_device_id = str(uuid.uuid4())
        manager._v2_worker = None
        manager._v2_structure_worker = None
        manager._active_server_syncs = 0
        manager._v2_retry_timer = timer
        manager._v2_retry_context = (
            manager._v2_context["local_key"],
            manager._v2_context["project_id"],
        )
        manager._auth_retry_blocked = False
        manager._shutting_down = False
        try:
            with patch.object(manager, "_launch_v2_operation") as launch:
                self.assertFalse(manager.retry_pending_syncs())
                self.assertTrue(manager.retry_pending_syncs(manual=True))

            timer.stop.assert_called_once_with()
            store.next_ready_operation.assert_called_once_with("project-a-local")
            launch.assert_called_once_with(operation)
            self.assertEqual(operation["content"], "보존할 최신 원고")
        finally:
            (
                manager._v2_context,
                manager._v2_store,
                manager._v2_device_id,
                manager._v2_worker,
                manager._v2_structure_worker,
                manager._active_server_syncs,
                manager._v2_retry_timer,
                manager._v2_retry_context,
                manager._auth_retry_blocked,
                manager._shutting_down,
            ) = previous

    def test_stale_lease_retry_never_runs_after_project_switch(self):
        manager = SyncManager()
        previous = (
            manager._v2_context,
            manager._v2_retry_context,
            manager.supabase,
            manager.retry_pending_syncs,
        )
        project_a_id = str(uuid.uuid4())
        retry = MagicMock(return_value=True)
        try:
            manager._v2_retry_context = ("project-a-local", project_a_id)
            manager._v2_context = {
                "local_key": "project-b-local",
                "project_id": str(uuid.uuid4()),
                "server_state": "active",
            }
            manager.supabase = object()
            manager.retry_pending_syncs = retry

            self.assertFalse(manager._run_scheduled_v2_retry())
            retry.assert_not_called()
            self.assertIsNone(manager._v2_retry_context)
        finally:
            (
                manager._v2_context,
                manager._v2_retry_context,
                manager.supabase,
                manager.retry_pending_syncs,
            ) = previous

    def test_project_trash_cancels_scheduled_lease_retry(self):
        manager = SyncManager()
        previous = (
            manager._v2_context,
            manager._v2_store,
            manager._v2_retry_timer,
            manager._v2_retry_context,
            manager._v2_lease_retry_operation_id,
            manager._v2_lease_retry_attempt,
        )
        project_id = str(uuid.uuid4())
        timer = MagicMock()
        manager._v2_context = {
            "local_key": "trashed-project-local",
            "project_id": project_id,
            "server_state": "active",
        }
        manager._v2_store = None
        manager._v2_retry_timer = timer
        manager._v2_retry_context = ("trashed-project-local", project_id)
        manager._v2_lease_retry_operation_id = str(uuid.uuid4())
        manager._v2_lease_retry_attempt = 3
        try:
            self.assertTrue(
                manager.mark_project_server_state(project_id, "trashed")
            )
            timer.stop.assert_called_once_with()
            self.assertIsNone(manager._v2_retry_context)
            self.assertIsNone(manager._v2_lease_retry_operation_id)
            self.assertEqual(manager._v2_lease_retry_attempt, 0)
        finally:
            (
                manager._v2_context,
                manager._v2_store,
                manager._v2_retry_timer,
                manager._v2_retry_context,
                manager._v2_lease_retry_operation_id,
                manager._v2_lease_retry_attempt,
            ) = previous

    def test_background_commit_releases_lease_after_document_switch(self):
        manager = SyncManager()
        previous = (
            manager.supabase,
            manager._v2_device_id,
            dict(manager._v2_leases),
            manager._v2_active_paths_provider,
        )
        document_id = str(uuid.uuid4())
        client = _FakeClient()
        try:
            manager.supabase = client
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_leases = {document_id: "lease-token"}
            manager.set_active_document_paths_provider(
                lambda: ["메인/메모장/지금열린문서.txt"]
            )

            manager._finalize_v2_operation_lease("committed", {
                "document_id": document_id,
                "local_path": "메인/메모장/이전에열린문서.txt",
                "is_deleted": False,
            })
            manager.wait_all_workers()

            self.assertNotIn(document_id, manager._v2_leases)
            self.assertEqual(client.calls[0][0], "release_edit_lease")
        finally:
            (
                manager.supabase,
                manager._v2_device_id,
                previous_leases,
                manager._v2_active_paths_provider,
            ) = previous
            manager._v2_leases = previous_leases

    def test_active_document_keeps_lease_for_heartbeat(self):
        manager = SyncManager()
        previous = (
            manager.supabase,
            manager._v2_device_id,
            dict(manager._v2_leases),
            manager._v2_active_paths_provider,
        )
        document_id = str(uuid.uuid4())
        client = _FakeClient()
        active_path = "메인/메모장/계속편집중.txt"
        try:
            manager.supabase = client
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_leases = {document_id: "lease-token"}
            manager.set_active_document_paths_provider(lambda: [active_path])

            manager._finalize_v2_operation_lease("committed", {
                "document_id": document_id,
                "local_path": active_path,
                "is_deleted": False,
            })
            manager.wait_all_workers()

            self.assertEqual(manager._v2_leases[document_id], "lease-token")
            self.assertEqual(client.calls, [])
        finally:
            (
                manager.supabase,
                manager._v2_device_id,
                previous_leases,
                manager._v2_active_paths_provider,
            ) = previous
            manager._v2_leases = previous_leases

    def test_active_new_document_acquires_lease_after_create_commit(self):
        manager = SyncManager()
        previous = (
            manager.supabase,
            manager._v2_device_id,
            dict(manager._v2_leases),
            manager._v2_active_paths_provider,
        )
        document_id = str(uuid.uuid4())
        active_path = "메인/원고/1권/새문서.txt"
        client = _FakeClient()
        try:
            manager.supabase = client
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_leases = {}
            manager.set_active_document_paths_provider(lambda: [active_path])

            manager._finalize_v2_operation_lease("committed", {
                "document_id": document_id,
                "local_path": active_path,
                "is_deleted": False,
            })
            manager.wait_all_workers()

            self.assertIn(document_id, manager._v2_leases)
            self.assertEqual(client.calls[0][0], "acquire_edit_lease")
        finally:
            (
                manager.supabase,
                manager._v2_device_id,
                previous_leases,
                manager._v2_active_paths_provider,
            ) = previous
            manager._v2_leases = previous_leases

    def test_viewing_does_not_acquire_until_first_user_text_change(self):
        controller = WritingController(
            MagicMock(),
            MagicMock(),
            SimpleNamespace(current_project="열람 정책 작품"),
            "device-a",
            lambda: [],
            lambda _path: "",
        )
        controller.acquire_lock_async = MagicMock()
        path = "메인/원고/1권/열람중.txt"

        controller.notify_file_opened(path, "")
        controller.acquire_lock_async.assert_not_called()

        controller.notify_text_changed(path, user_initiated=False)
        controller.acquire_lock_async.assert_not_called()

        controller.notify_text_changed(path)
        controller.acquire_lock_async.assert_called_once()
        controller.idle_timer.stop()

    def test_v2_document_switch_releases_lease_acquired_by_retry_queue(self):
        sync_manager = MagicMock()
        sync_manager.is_v2_enabled = True
        path = "메인/원고/1권/재시도후잠금.txt"
        controller = WritingController(
            MagicMock(),
            sync_manager,
            SimpleNamespace(current_project="재시도 작품"),
            "device-a",
            lambda: [],
            lambda _path: "",
        )

        self.assertNotIn(path, controller.locked_paths)
        controller.release_lock(path)

        sync_manager.release_lock_async.assert_called_once_with(
            "재시도 작품", path, "device-a"
        )

    def test_v2_heartbeat_includes_active_lease_from_retry_queue(self):
        sync_manager = MagicMock()
        sync_manager.is_v2_enabled = True
        path = "메인/원고/1권/재시도후잠금.txt"
        controller = WritingController(
            MagicMock(),
            sync_manager,
            SimpleNamespace(current_project="재시도 작품"),
            "device-a",
            lambda: [path],
            lambda _path: "",
        )

        self.assertNotIn(path, controller.locked_paths)
        controller.on_heartbeat_timeout()

        sync_manager.heartbeat_locks_async.assert_called_once_with(
            "재시도 작품", {path}, "device-a"
        )

    def test_failed_release_stops_local_heartbeat_until_server_ttl_expires(self):
        manager = SyncManager()
        previous = (
            manager.supabase,
            manager._v2_device_id,
            dict(manager._v2_leases),
        )
        document_id = str(uuid.uuid4())
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = RuntimeError(
            "network timeout"
        )
        try:
            manager.supabase = client
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_leases = {document_id: "lease-token"}

            self.assertFalse(manager._release_v2_lease(document_id))

            self.assertNotIn(document_id, manager._v2_leases)
        finally:
            (
                manager.supabase,
                manager._v2_device_id,
                previous_leases,
            ) = previous
            manager._v2_leases = previous_leases

    def test_unchanged_synced_content_does_not_create_another_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "중복 저장 방지 작품"
            wpm.writing_root_path = str(Path(temp_dir, "중복 저장 방지 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            relative_path = "메인/메모장/같은내용.txt"
            content = "이미 서버에 저장된 내용"
            self.assertTrue(wpm.write_text_file(relative_path, content))
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(
                wpm, wpm.current_project, str(uuid.uuid4()), store=store
            )
            created = store.enqueue(manager._v2_context, relative_path, content)
            store.mark_success(created["operation_id"], {
                "revision": 1,
                "content_hash": "a" * 64,
            })
            callback = MagicMock()

            with patch.object(manager, "retry_pending_syncs") as retry:
                worker = manager.upload_content_async(
                    wpm,
                    wpm.current_project,
                    relative_path,
                    content,
                    callback=callback,
                )

            self.assertIsNone(worker)
            self.assertEqual(store.counts(manager._v2_context["local_key"])["total"], 0)
            self.assertIsNone(store.next_ready_operation(manager._v2_context["local_key"]))
            callback.assert_called_once_with(True, "", relative_path, 1)
            retry.assert_not_called()

    def test_pending_count_only_covers_documents_with_outstanding_work(self):
        """완료된 작업의 문서까지 세면 건수가 늘기만 하고 줄지 않는다."""
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "건수 표시 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, wpm.current_project, "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            previous = (
                manager._v2_store, manager._v2_context, manager._v2_wpm
            )
            try:
                manager.configure_v2(
                    wpm, wpm.current_project, str(uuid.uuid4()), store=store
                )
                local_key = manager._v2_context["local_key"]

                done = store.enqueue(
                    manager._v2_context, "메인/원고/1권/001화.txt", "끝난 원고"
                )
                store.enqueue(
                    manager._v2_context, "메인/원고/1권/002화.txt", "남은 원고"
                )
                self.assertEqual(store.counts(local_key)["documents"], 2)

                store.mark_success(
                    done["operation_id"],
                    {"revision": 1, "content_hash": "b" * 64},
                )

                counts = store.counts(local_key)
                self.assertEqual(counts["documents"], 1)
                self.assertEqual(counts["pending"], 1)
                self.assertEqual(manager.pending_retry_count, 1)
            finally:
                (
                    manager._v2_store, manager._v2_context, manager._v2_wpm
                ) = previous

    def test_chained_edit_is_reissued_when_its_predecessor_never_completes(self):
        """base_revision IS NULL 작업이 고아가 되면 큐 전체가 멈춘다."""
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "연쇄 편집 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, wpm.current_project, "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            previous = (
                manager._v2_store, manager._v2_context, manager._v2_wpm
            )
            try:
                manager.configure_v2(
                    wpm, wpm.current_project, str(uuid.uuid4()), store=store
                )
                local_key = manager._v2_context["local_key"]
                path = "메인/원고/1권/001화.txt"

                first = store.enqueue(manager._v2_context, path, "첫 저장")
                second = store.enqueue(manager._v2_context, path, "이어서 저장")
                self.assertIsNone(second["base_revision"])

                # 앞선 작업이 mark_success 를 거치지 않고 사라진다.
                store.cancel_operation(first["operation_id"], str(uuid.uuid4()))

                self.assertIsNone(store.next_ready_operation(local_key))
                self.assertGreater(store.counts(local_key)["pending"], 0)

                self.assertEqual(store.recover_stranded_operations(local_key), 1)

                ready = store.next_ready_operation(local_key)
                self.assertIsNotNone(ready)
                self.assertIsNotNone(ready["base_revision"])
                self.assertEqual(ready["content"], "이어서 저장")

                # 이미 복구했으면 다시 만들지 않는다.
                self.assertEqual(store.recover_stranded_operations(local_key), 0)
            finally:
                (
                    manager._v2_store, manager._v2_context, manager._v2_wpm
                ) = previous

    def test_live_predecessor_keeps_its_chained_edit_waiting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "대기 유지 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, wpm.current_project, "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            previous = (
                manager._v2_store, manager._v2_context, manager._v2_wpm
            )
            try:
                manager.configure_v2(
                    wpm, wpm.current_project, str(uuid.uuid4()), store=store
                )
                local_key = manager._v2_context["local_key"]
                path = "메인/원고/1권/002화.txt"
                store.enqueue(manager._v2_context, path, "첫 저장")
                store.enqueue(manager._v2_context, path, "이어서 저장")

                # 앞 작업이 아직 살아 있으므로 건드리지 않는다.
                self.assertEqual(store.recover_stranded_operations(local_key), 0)
            finally:
                (
                    manager._v2_store, manager._v2_context, manager._v2_wpm
                ) = previous

    def test_completed_operation_error_stops_describing_the_queue(self):
        """A stale AUTH_REQUIRED must not outlive the operation that hit it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "낡은 오류 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, wpm.current_project, "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            previous = (
                manager._v2_store, manager._v2_context, manager._v2_wpm
            )
            try:
                manager.configure_v2(
                    wpm, wpm.current_project, str(uuid.uuid4()), store=store
                )
                local_key = manager._v2_context["local_key"]

                failed = store.enqueue(
                    manager._v2_context, "메인/원고/1권/001화.txt", "첫 본문"
                )
                store.mark_attempt(failed["operation_id"])
                store.mark_retry(failed["operation_id"], "AUTH_REQUIRED")
                self.assertIn("AUTH_REQUIRED", store.latest_error(local_key))

                # 같은 작업이 재로그인 뒤 성공한다.
                store.mark_success(
                    failed["operation_id"],
                    {"revision": 1, "content_hash": "a" * 64},
                )

                self.assertEqual(store.latest_error(local_key), "")

                # 아직 보내지 않은 작업이 남아도 옛 오류가 되살아나지 않는다.
                store.enqueue(
                    manager._v2_context, "메인/원고/1권/002화.txt", "다음 본문"
                )
                self.assertEqual(store.latest_error(local_key), "")
                self.assertEqual(store.counts(local_key)["pending"], 1)
            finally:
                (
                    manager._v2_store, manager._v2_context, manager._v2_wpm
                ) = previous

    def test_v2_upload_rejects_accidental_empty_overwrite_before_disk_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "빈 문서 방어 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, wpm.current_project, "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            relative_path = "메인/원고/1권/002화.txt"
            original = "삭제되면 안 되는 기존 본문"
            self.assertTrue(wpm.write_text_file(relative_path, original))
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            previous = (
                manager._v2_store,
                manager._v2_context,
                manager._v2_wpm,
                manager._v2_device_id,
            )
            try:
                manager.configure_v2(
                    wpm,
                    wpm.current_project,
                    str(uuid.uuid4()),
                    store=store,
                )
                created = store.enqueue(
                    manager._v2_context, relative_path, original
                )
                store.mark_success(created["operation_id"], {
                    "revision": 3,
                    "content_hash": "a" * 64,
                })
                callback = MagicMock()

                with patch.object(manager, "retry_pending_syncs") as retry:
                    worker = manager.upload_content_async(
                        wpm,
                        wpm.current_project,
                        relative_path,
                        "",
                        callback=callback,
                    )

                self.assertIsNone(worker)
                self.assertEqual(
                    wpm.read_text_file(relative_path), original
                )
                self.assertEqual(
                    store.counts(manager._v2_context["local_key"])["total"],
                    0,
                )
                callback.assert_called_once()
                self.assertIn(
                    "자동저장을 중단",
                    callback.call_args.args[1],
                )
                retry.assert_not_called()
            finally:
                (
                    manager._v2_store,
                    manager._v2_context,
                    manager._v2_wpm,
                    manager._v2_device_id,
                ) = previous

    def test_v2_lock_worker_reuses_one_authenticated_profile_client(self):
        authenticated_client = object()
        manager = SimpleNamespace(
            is_v2_enabled=True,
            supabase=authenticated_client,
            check_and_acquire_lock=MagicMock(return_value=(True, "Lock acquired.")),
            get_file_updated_at=MagicMock(return_value=3),
        )
        worker = LockWorker(manager, "작품", "메인/메모장/문서.txt", "device-a")
        result = MagicMock()
        worker.resultReady.connect(result)

        with patch.object(
            SyncManager,
            "create_supabase_client",
            side_effect=AssertionError("v2에서 새 인증 클라이언트를 만들면 안 됩니다."),
        ):
            worker.run()

        manager.check_and_acquire_lock.assert_called_once_with(
            "작품",
            "메인/메모장/문서.txt",
            "device-a",
            client=authenticated_client,
        )
        result.assert_called_once_with(True, "Lock acquired.", 3)

    def test_existing_v2_project_does_not_assign_uuids_to_empty_placeholders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "연속 생성 복구 작품"
            wpm.writing_root_path = str(Path(temp_dir, "연속 생성 복구 작품", "집필모드"))
            memo_dir = Path(wpm.writing_root_path, "메인", "메모장")
            memo_dir.mkdir(parents=True)
            wpm.project_settings = {}
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, wpm.current_project, str(uuid.uuid4()), store=store)

            for index in range(3):
                (memo_dir / f"새_문서 ({index}).txt").write_text("", encoding="utf-8")

            manager.configure_v2(wpm, wpm.current_project, str(uuid.uuid4()), store=store)
            recovered = manager._recover_untracked_local_files_after_pull([])

            self.assertEqual(recovered, 0)
            self.assertIsNone(store.next_ready_operation(
                manager._v2_context["local_key"]
            ))
            self.assertEqual(
                [
                    store.get_document(
                        manager._v2_context["local_key"],
                        f"메인/메모장/새_문서 ({index}).txt",
                    )
                    for index in range(3)
                ],
                [None, None, None],
            )

    def test_remote_tree_order_replaces_local_order_but_keeps_local_trash_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "순서 동기화 작품"
            wpm.writing_root_path = str(Path(temp_dir, "순서 동기화 작품", "집필모드"))
            wpm.settings_path = str(Path(wpm.writing_root_path, "설정.json"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            wpm.project_settings = {
                "tree_order": {
                    "메인/메모장": ["로컬.txt"],
                    "메인/휴지통": ["로컬휴지통.txt"],
                }
            }
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, wpm.current_project, str(uuid.uuid4())
            )
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_wpm = wpm
            manager._v2_device_id = str(uuid.uuid4())
            document_id = str(uuid.uuid5(
                uuid.UUID(context["project_id"]), TREE_ORDER_DOCUMENT_PATH
            ))
            content = manager._tree_order_content({
                "메인/메모장": ["셋째.txt", "첫째.txt", "둘째.txt"],
                "메인/휴지통": ["서버휴지통.txt"],
            })

            remote_documents = [{
                "document_id": document_id,
                "relative_path": TREE_ORDER_DOCUMENT_PATH,
                "content": content,
                "revision": 1,
                "is_deleted": False,
            }]
            remote_documents.extend({
                "document_id": str(uuid.uuid4()),
                "relative_path": f"메인/메모장/{name}",
                "content": name,
                "revision": 1,
                "is_deleted": False,
            } for name in ("셋째.txt", "첫째.txt", "둘째.txt"))
            changes = manager._apply_v2_remote_documents(remote_documents)

            self.assertEqual(changes[-1]["kind"], "tree_order")
            self.assertEqual(
                wpm.project_settings["tree_order"]["메인/메모장"],
                ["셋째.txt", "첫째.txt", "둘째.txt"],
            )
            self.assertEqual(
                wpm.project_settings["tree_order"]["메인/휴지통"],
                ["로컬휴지통.txt"],
            )
            self.assertFalse(Path(wpm.writing_root_path, "__antigravity__").exists())

    def test_tree_order_is_queued_as_one_hidden_deterministic_v2_document(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                str(Path(temp_dir, "집필모드")), "순서 저장 작품", str(uuid.uuid4())
            )
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_device_id = str(uuid.uuid4())

            with patch.object(manager, "retry_pending_syncs"):
                operation = manager.record_tree_order({
                    "메인/메모장": ["셋째.txt", "첫째.txt", "둘째.txt"],
                    "메인/휴지통": ["장치마다 이름이 다른 보관본.txt"],
                })

            expected_id = str(uuid.uuid5(
                uuid.UUID(context["project_id"]), TREE_ORDER_DOCUMENT_PATH
            ))
            payload = json.loads(operation["content"])
            self.assertEqual(operation["document_id"], expected_id)
            self.assertEqual(operation["relative_path"], TREE_ORDER_DOCUMENT_PATH)
            self.assertEqual(
                payload["tree_order"]["메인/메모장"],
                ["셋째.txt", "첫째.txt", "둘째.txt"],
            )
            self.assertNotIn("메인/휴지통", payload["tree_order"])

    def test_trash_paths_are_never_registered_as_live_cloud_documents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "휴지통 제외 작품"
            wpm.writing_root_path = str(Path(temp_dir, "휴지통 제외 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/살아있는문서.txt"
            trash_path = "메인/휴지통/삭제된문서.txt"
            self.assertTrue(wpm.write_text_file(live_path, "정상 문서"))
            self.assertTrue(wpm.write_text_file(trash_path, "휴지통 보관본"))
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, wpm.current_project, str(uuid.uuid4()), store=store)

            callback = MagicMock()
            manager.upload_content_async(
                wpm, wpm.current_project, trash_path, "휴지통 보관본", callback=callback
            )
            acquired, message = manager.check_and_acquire_lock(
                wpm.current_project, trash_path, "session-trash"
            )

            self.assertTrue(is_live_document_path(live_path))
            self.assertFalse(is_live_document_path(trash_path))
            self.assertIsNotNone(store.get_document(manager._v2_context["local_key"], live_path))
            self.assertIsNone(store.get_document(manager._v2_context["local_key"], trash_path))
            self.assertEqual(store.counts(manager._v2_context["local_key"])["total"], 0)
            self.assertFalse(acquired)
            self.assertIn("읽기 전용", message)
            callback.assert_called_once()

    def test_repeated_delete_and_restore_keep_one_document_uuid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "반복 삭제 작품"
            wpm.writing_root_path = str(Path(temp_dir, "반복 삭제 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/반복삭제.txt"
            content = "삭제와 복원을 반복해도 보존되어야 하는 내용"
            self.assertTrue(wpm.write_text_file(live_path, content))
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, wpm.current_project, str(uuid.uuid4()), store=store)
            original = store.get_document(manager._v2_context["local_key"], live_path)
            created = store.enqueue(manager._v2_context, live_path, content)
            store.mark_success(created["operation_id"], {
                "revision": 1,
                "content_hash": "a" * 64,
            })
            next_revision = 2

            with patch.object(manager, "retry_pending_syncs"):
                for _ in range(3):
                    trash_path = wpm.move_to_trash(live_path)
                    moved_to_trash = manager.record_tombstone(live_path, trash_path)
                    delete_operation = store.next_ready_operation(manager._v2_context["local_key"])
                    self.assertEqual(moved_to_trash[0]["document_id"], original["document_id"])
                    self.assertTrue(delete_operation["is_deleted"])
                    self.assertEqual(delete_operation["relative_path"], live_path)
                    store.mark_success(delete_operation["operation_id"], {
                        "revision": next_revision,
                        "content_hash": "b" * 64,
                    })
                    next_revision += 1

                    restored_path = wpm.restore_from_trash(trash_path)
                    moved_to_live = manager.record_restore(trash_path, restored_path)
                    restore_operation = store.next_ready_operation(manager._v2_context["local_key"])
                    self.assertEqual(moved_to_live[0]["document_id"], original["document_id"])
                    self.assertFalse(restore_operation["is_deleted"])
                    self.assertEqual(restore_operation["relative_path"], live_path)
                    store.mark_success(restore_operation["operation_id"], {
                        "revision": next_revision,
                        "content_hash": "c" * 64,
                    })
                    next_revision += 1

            current = store.get_document(manager._v2_context["local_key"], live_path)
            self.assertEqual(current["document_id"], original["document_id"])
            self.assertFalse(current["is_deleted"])
            self.assertEqual(wpm.read_text_file(live_path), content)
            self.assertEqual(wpm.list_trash_items(), [])

    def test_delete_immediately_after_create_waits_behind_create_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "즉시 삭제 작품"
            wpm.writing_root_path = str(Path(temp_dir, "즉시 삭제 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/바로삭제.txt"
            self.assertTrue(wpm.write_text_file(live_path, ""))
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, wpm.current_project, str(uuid.uuid4()), store=store)
            create_operation = store.enqueue(manager._v2_context, live_path, "")

            with patch.object(manager, "retry_pending_syncs"):
                trash_path = wpm.move_to_trash(live_path)
                moved = manager.record_tombstone(live_path, trash_path)

            self.assertEqual(moved[0]["document_id"], create_operation["document_id"])
            store.mark_success(create_operation["operation_id"], {
                "revision": 1,
                "content_hash": "d" * 64,
            })
            delete_operation = store.next_ready_operation(manager._v2_context["local_key"])
            self.assertIsNotNone(delete_operation)
            self.assertTrue(delete_operation["is_deleted"])
            self.assertEqual(delete_operation["base_revision"], 1)
            self.assertEqual(delete_operation["relative_path"], live_path)

    def test_reused_name_delete_relocates_around_stale_trash_uuid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "휴지통 경로 충돌 작품"
            wpm.writing_root_path = str(Path(temp_dir, "휴지통 경로 충돌 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/반복이름.txt"
            stale_trash_path = "메인/휴지통/반복이름.txt"
            old_id = str(uuid.uuid4())
            new_id = str(uuid.uuid4())
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, wpm.current_project, str(uuid.uuid4())
            )
            store.apply_remote_snapshot(
                context, old_id, live_path, "예전 삭제본", 2,
                is_deleted=True, local_path=stale_trash_path,
            )
            store.apply_remote_snapshot(
                context, new_id, live_path, "현재 문서", 2,
                is_deleted=False, local_path=live_path,
            )
            self.assertTrue(wpm.write_text_file(live_path, "현재 문서"))
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_wpm = wpm
            manager._v2_device_id = str(uuid.uuid4())

            initial_trash_path = wpm.move_to_trash(live_path)
            self.assertEqual(initial_trash_path, stale_trash_path)
            with patch.object(manager, "retry_pending_syncs"):
                moved = manager.record_tombstone(live_path, initial_trash_path)

            self.assertEqual(len(moved), 1)
            relocated_path = moved[0]["local_path"]
            self.assertNotEqual(relocated_path, stale_trash_path)
            self.assertTrue(Path(wpm.writing_root_path, relocated_path).exists())
            self.assertEqual(store.get_document_by_id(old_id)["local_path"], stale_trash_path)
            self.assertEqual(store.get_document_by_id(new_id)["local_path"], relocated_path)
            queued = store.next_ready_operation(context["local_key"])
            self.assertTrue(queued["is_deleted"])
            self.assertEqual(queued["document_id"], new_id)
            trash_item = next(
                item for item in wpm.list_trash_items()
                if item["trash_path"] == relocated_path
            )
            self.assertEqual(trash_item["document_id"], new_id)

    def test_equal_revision_remote_tombstone_repairs_missing_trash_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "휴지통 사본 복구 작품"
            wpm.writing_root_path = str(Path(temp_dir, "휴지통 사본 복구 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/사라진삭제본.txt"
            stale_trash_path = "메인/휴지통/사라진삭제본.txt"
            document_id = str(uuid.uuid4())
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, wpm.current_project, str(uuid.uuid4())
            )
            store.apply_remote_snapshot(
                context, document_id, live_path, "보존할 삭제 본문", 3,
                is_deleted=True, local_path=stale_trash_path,
            )
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_wpm = wpm
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_protected_paths_provider = lambda: set()

            changes = manager._apply_v2_remote_documents([{
                "document_id": document_id,
                "relative_path": live_path,
                "content": "보존할 삭제 본문",
                "revision": 3,
                "is_deleted": True,
                "deleted_at": "2026-07-15T09:00:00Z",
            }])

            self.assertEqual(len(changes), 1)
            repaired = store.get_document_by_id(document_id)
            self.assertTrue(Path(wpm.writing_root_path, repaired["local_path"]).exists())
            self.assertEqual(
                wpm.read_text_file(repaired["local_path"]), "보존할 삭제 본문"
            )

    def test_equal_revision_remote_live_document_repairs_missing_local_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "로컬 사본 복구 작품"
            wpm.writing_root_path = str(Path(temp_dir, "로컬 사본 복구 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/삭제실패복구.txt"
            document_id = str(uuid.uuid4())
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, wpm.current_project, str(uuid.uuid4())
            )
            store.apply_remote_snapshot(
                context, document_id, live_path, "서버에 남은 본문", 4
            )
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_wpm = wpm
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_protected_paths_provider = lambda: set()

            changes = manager._apply_v2_remote_documents([{
                "document_id": document_id,
                "relative_path": live_path,
                "content": "서버에 남은 본문",
                "revision": 4,
                "is_deleted": False,
            }])

            self.assertEqual(len(changes), 1)
            self.assertEqual(wpm.read_text_file(live_path), "서버에 남은 본문")
            self.assertEqual(store.get_document_by_id(document_id)["revision"], 4)

    def test_empty_trash_records_synced_purge_and_frees_local_trash_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "휴지통 비우기 작품"
            wpm.writing_root_path = str(Path(temp_dir, "휴지통 비우기 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/영구삭제.txt"
            self.assertTrue(wpm.write_text_file(live_path, "삭제할 본문"))
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, wpm.current_project, str(uuid.uuid4()), store=store)
            created = store.enqueue(manager._v2_context, live_path, "삭제할 본문")
            store.mark_success(created["operation_id"], {
                "revision": 1,
                "content_hash": "a" * 64,
            })
            with patch.object(manager, "retry_pending_syncs"):
                trash_path = wpm.move_to_trash(live_path)
                manager.record_tombstone(live_path, trash_path)
            deletion = store.next_ready_operation(manager._v2_context["local_key"])
            store.mark_success(deletion["operation_id"], {
                "revision": 2,
                "content_hash": "b" * 64,
            })
            trash_items = wpm.list_trash_items()
            wpm.empty_trash()

            with patch.object(manager, "retry_pending_syncs"):
                purge = manager.record_trash_purge(trash_items, empty_all=True)

            self.assertEqual(purge["relative_path"], TRASH_PURGE_DOCUMENT_PATH)
            self.assertEqual(wpm.list_trash_items(), [])
            document = store.get_document_by_id(created["document_id"])
            self.assertTrue(document["local_path"].startswith("__antigravity__/purged/"))
            self.assertEqual(
                wpm.project_settings["trash_purged_revisions"][created["document_id"]],
                2,
            )
            self.assertTrue(wpm.project_settings["trash_empty_generation"])

    def test_remote_empty_trash_marker_prevents_tombstone_rematerialization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "원격 휴지통 비우기 작품"
            wpm.writing_root_path = str(Path(temp_dir, "원격 휴지통 비우기 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/다시생기면안됨.txt"
            document_id = str(uuid.uuid4())
            purge_document_id = str(uuid.uuid4())
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, wpm.current_project, str(uuid.uuid4())
            )
            trash_path = wpm.materialize_remote_tombstone(
                live_path, "삭제 본문", document_id=document_id
            )
            store.apply_remote_snapshot(
                context, document_id, live_path, "삭제 본문", 2,
                is_deleted=True, local_path=trash_path,
            )
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_wpm = wpm
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_protected_paths_provider = lambda: set()
            purge_content = manager._trash_purge_content(
                {document_id: 2}, "empty-generation-1"
            )

            changes = manager._apply_v2_remote_documents([
                {
                    "document_id": document_id,
                    "relative_path": live_path,
                    "content": "삭제 본문",
                    "revision": 2,
                    "is_deleted": True,
                },
                {
                    "document_id": purge_document_id,
                    "relative_path": TRASH_PURGE_DOCUMENT_PATH,
                    "content": purge_content,
                    "revision": 1,
                    "is_deleted": False,
                },
            ])

            self.assertTrue(any(change["kind"] == "trash_purge" for change in changes))
            self.assertEqual(wpm.list_trash_items(), [])
            document = store.get_document_by_id(document_id)
            self.assertTrue(document["local_path"].startswith("__antigravity__/purged/"))

    def test_remote_tombstone_is_applied_before_immediate_path_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "이름 재사용 작품"
            wpm.writing_root_path = str(Path(temp_dir, "이름 재사용 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            reused_path = "메인/메모장/같은이름.txt"
            temporary_path = "메인/메모장/임시이름.txt"
            old_id = str(uuid.uuid4())
            new_id = str(uuid.uuid4())
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, wpm.current_project, str(uuid.uuid4())
            )
            store.apply_remote_snapshot(context, old_id, reused_path, "예전 문서", 1)
            store.apply_remote_snapshot(context, new_id, temporary_path, "새 문서", 1)
            self.assertTrue(wpm.write_text_file(reused_path, "예전 문서"))
            self.assertTrue(wpm.write_text_file(temporary_path, "새 문서"))
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_wpm = wpm
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_protected_paths_provider = lambda: set()

            changes = manager._apply_v2_remote_documents([
                {
                    "document_id": new_id,
                    "relative_path": reused_path,
                    "content": "새 문서",
                    "revision": 2,
                    "is_deleted": False,
                },
                {
                    "document_id": old_id,
                    "relative_path": reused_path,
                    "content": "예전 문서",
                    "revision": 2,
                    "is_deleted": True,
                },
            ])

            self.assertEqual(len(changes), 2)
            self.assertEqual(wpm.read_text_file(reused_path), "새 문서")
            self.assertFalse(Path(wpm.writing_root_path, temporary_path).exists())
            old_document = store.get_document_by_id(old_id)
            new_document = store.get_document_by_id(new_id)
            self.assertTrue(old_document["is_deleted"])
            self.assertTrue(old_document["local_path"].startswith("메인/휴지통/"))
            self.assertEqual(new_document["local_path"], reused_path)

    def test_late_editor_save_cannot_recreate_a_deleted_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "늦은 저장 차단 작품"
            wpm.writing_root_path = str(Path(temp_dir, "늦은 저장 차단 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            live_path = "메인/메모장/삭제완료.txt"
            self.assertTrue(wpm.write_text_file(live_path, "삭제 전"))
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, wpm.current_project, str(uuid.uuid4()), store=store)
            created = store.enqueue(manager._v2_context, live_path, "삭제 전")
            store.mark_success(created["operation_id"], {
                "revision": 1,
                "content_hash": "e" * 64,
            })
            with patch.object(manager, "retry_pending_syncs"):
                trash_path = wpm.move_to_trash(live_path)
                manager.record_tombstone(live_path, trash_path)
            deleted = store.next_ready_operation(manager._v2_context["local_key"])
            store.mark_success(deleted["operation_id"], {
                "revision": 2,
                "content_hash": "f" * 64,
            })
            callback = MagicMock()

            manager.upload_content_async(
                wpm,
                wpm.current_project,
                live_path,
                "편집기에 남은 늦은 한 글자",
                callback=callback,
            )

            self.assertFalse(Path(wpm.writing_root_path, live_path).exists())
            self.assertIsNone(store.get_document(manager._v2_context["local_key"], live_path))
            self.assertEqual(store.counts(manager._v2_context["local_key"])["total"], 0)
            callback.assert_called_once()

    def test_tombstone_received_before_live_file_vacates_reused_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "첫 수신이 삭제인 작품"
            wpm.writing_root_path = str(Path(temp_dir, "첫 수신이 삭제인 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            reused_path = "메인/메모장/재사용된이름.txt"
            old_id = str(uuid.uuid4())
            new_id = str(uuid.uuid4())
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, wpm.current_project, str(uuid.uuid4())
            )
            # Reproduce the old broken B state: tombstone metadata occupies the
            # live path even though B never downloaded the original file.
            store.apply_remote_snapshot(
                context,
                old_id,
                reused_path,
                "삭제된 예전 본문",
                2,
                is_deleted=True,
                local_path=reused_path,
            )
            manager = SyncManager()
            manager._v2_store = store
            manager._v2_context = context
            manager._v2_wpm = wpm
            manager._v2_device_id = str(uuid.uuid4())
            manager._v2_protected_paths_provider = lambda: set()

            changes = manager._apply_v2_remote_documents([
                {
                    "document_id": new_id,
                    "relative_path": reused_path,
                    "content": "이름을 재사용한 새 본문",
                    "revision": 5,
                    "is_deleted": False,
                },
                {
                    "document_id": old_id,
                    "relative_path": reused_path,
                    "content": "삭제된 예전 본문",
                    "revision": 2,
                    "is_deleted": True,
                },
            ])

            self.assertEqual(len(changes), 2)
            self.assertEqual(
                wpm.read_text_file(reused_path), "이름을 재사용한 새 본문"
            )
            old_document = store.get_document_by_id(old_id)
            new_document = store.get_document_by_id(new_id)
            self.assertTrue(old_document["local_path"].startswith("메인/휴지통/"))
            self.assertEqual(new_document["local_path"], reused_path)
            self.assertEqual(
                wpm.read_text_file(old_document["local_path"]), "삭제된 예전 본문"
            )
            trash_item = next(
                item for item in wpm.list_trash_items()
                if item["trash_path"] == old_document["local_path"]
            )
            self.assertEqual(trash_item["original_path"], reused_path)

    def test_v2_worker_does_not_override_native_qthread_finished_signal(self):
        self.assertIn("resultReady", V2QueueWorker.__dict__)
        self.assertNotIn("finished", V2QueueWorker.__dict__)

    def test_lock_worker_does_not_override_native_qthread_finished_signal(self):
        self.assertIn("resultReady", LockWorker.__dict__)
        self.assertNotIn("finished", LockWorker.__dict__)

    def test_new_document_uses_commit_rpc_with_stable_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "RPC 작품"
            wpm.writing_root_path = str(Path(temp_dir, "RPC 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, "RPC 작품", str(uuid.uuid4()), store=store)
            manager.supabase = _FakeClient()
            operation = store.enqueue(
                manager._v2_context, "메인/원고/001화.txt", "RPC 저장"
            )

            result = manager._process_v2_operation(operation["operation_id"])

            self.assertEqual(result["kind"], "committed")
            commit_name, params = manager.supabase.calls[-1]
            self.assertEqual(commit_name, "commit_document")
            self.assertEqual(params["p_document_id"], operation["document_id"])
            self.assertEqual(params["p_operation_id"], operation["operation_id"])
            self.assertEqual(params["p_base_revision"], 0)

    def test_remote_pull_renames_clean_file_but_skips_dirty_editor_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "수신 동기화 작품"
            wpm.writing_root_path = str(Path(temp_dir, "수신 동기화 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path, "수신 동기화 작품", str(uuid.uuid4())
            )
            document_id = str(uuid.uuid4())
            old_path = "메인/메모장/예전이름.txt"
            new_path = "메인/메모장/새이름.txt"
            store.apply_remote_snapshot(
                context, document_id, old_path, "기준본", 1
            )
            self.assertTrue(wpm.write_text_file(old_path, "기준본"))

            manager = SyncManager()
            previous = (
                manager._v2_store,
                manager._v2_context,
                manager._v2_wpm,
                manager._v2_protected_paths_provider,
            )
            try:
                manager._v2_store = store
                manager._v2_context = context
                manager._v2_wpm = wpm
                manager._v2_device_id = str(uuid.uuid4())
                manager._v2_protected_paths_provider = lambda: set()
                changes = manager._apply_v2_remote_documents([{
                    "document_id": document_id,
                    "relative_path": new_path,
                    "content": "서버 최신본",
                    "revision": 2,
                    "is_deleted": False,
                }])

                self.assertEqual(len(changes), 1)
                self.assertEqual(wpm.read_text_file(old_path), "")
                self.assertEqual(wpm.read_text_file(new_path), "서버 최신본")
                self.assertEqual(store.get_document_by_id(document_id)["revision"], 2)

                manager._v2_protected_paths_provider = lambda: {new_path}
                skipped = manager._apply_v2_remote_documents([{
                    "document_id": document_id,
                    "relative_path": new_path,
                    "content": "편집 중에는 덮어쓰면 안 됨",
                    "revision": 3,
                    "is_deleted": False,
                }])

                self.assertEqual(skipped, [])
                self.assertEqual(wpm.read_text_file(new_path), "서버 최신본")
                self.assertEqual(store.get_document_by_id(document_id)["revision"], 2)

                manager._v2_protected_paths_provider = lambda: set()
                deleted = manager._apply_v2_remote_documents([{
                    "document_id": document_id,
                    "relative_path": new_path,
                    "content": "서버 최신본",
                    "revision": 3,
                    "is_deleted": True,
                    "deleted_at": "2026-07-14T08:12:13.456789Z",
                }])

                deleted_document = store.get_document_by_id(document_id)
                self.assertEqual(len(deleted), 1)
                self.assertTrue(deleted_document["is_deleted"])
                self.assertTrue(deleted_document["local_path"].startswith("메인/휴지통/"))
                self.assertEqual(wpm.read_text_file(new_path), "")
                self.assertEqual(
                    wpm.read_text_file(deleted_document["local_path"]), "서버 최신본"
                )
                trash_item = next(
                    item for item in wpm.list_trash_items()
                    if item["trash_path"] == deleted_document["local_path"]
                )
                self.assertEqual(
                    trash_item["deleted_at"], "2026-07-14T08:12:13.456789Z"
                )
                self.assertEqual(trash_item["document_id"], document_id)
            finally:
                (
                    manager._v2_store,
                    manager._v2_context,
                    manager._v2_wpm,
                    manager._v2_protected_paths_provider,
                ) = previous

    def test_equal_revision_refreshes_clean_active_editor_after_shared_store_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "현재 창 갱신 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, "현재 창 갱신 작품", "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                wpm.writing_root_path,
                wpm.current_project,
                str(uuid.uuid4()),
            )
            document_id = str(uuid.uuid4())
            relative_path = "메인/원고/1권/006화.txt"
            content = "아이패드에서 내려온 최신 내용"
            store.apply_remote_snapshot(
                context, document_id, relative_path, content, 14
            )
            self.assertTrue(wpm.write_text_file(relative_path, content))

            manager = SyncManager()
            previous = (
                manager._v2_store,
                manager._v2_context,
                manager._v2_wpm,
                manager._v2_device_id,
                manager._v2_protected_paths_provider,
                manager._v2_active_paths_provider,
            )
            try:
                manager._v2_store = store
                manager._v2_context = context
                manager._v2_wpm = wpm
                manager._v2_device_id = str(uuid.uuid4())
                manager._v2_protected_paths_provider = lambda: set()
                manager._v2_active_paths_provider = lambda: [relative_path]

                changes = manager._apply_v2_remote_documents([{
                    "document_id": document_id,
                    "relative_path": relative_path,
                    "content": content,
                    "revision": 14,
                    "is_deleted": False,
                }])

                self.assertEqual(len(changes), 1)
                self.assertEqual(changes[0]["kind"], "remote_refresh")
                self.assertEqual(changes[0]["content"], content)
            finally:
                (
                    manager._v2_store,
                    manager._v2_context,
                    manager._v2_wpm,
                    manager._v2_device_id,
                    manager._v2_protected_paths_provider,
                    manager._v2_active_paths_provider,
                ) = previous

    def test_forced_offline_mode_does_not_contact_lease_rpcs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "오프라인 작품"
            wpm.writing_root_path = str(Path(temp_dir, "오프라인 작품", "집필모드"))
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            manager.configure_v2(wpm, "오프라인 작품", str(uuid.uuid4()), store=store)
            manager.supabase = _FakeClient()
            relative_path = "메인/원고/충돌테스트.txt"
            created = store.enqueue(manager._v2_context, relative_path, "기준본")
            store.mark_success(created["operation_id"], {
                "revision": 1,
                "content_hash": "c" * 64,
            })
            manager._v2_leases[created["document_id"]] = "lease-token"

            with patch("sync_manager.is_forced_offline", return_value=True):
                acquired, message = manager.check_and_acquire_lock(
                    "오프라인 작품", relative_path, "session-a"
                )
                manager.heartbeat_lock("오프라인 작품", relative_path, "session-a")
                released = manager.release_lock(
                    "오프라인 작품", relative_path, "session-a"
                )

            self.assertTrue(acquired)
            self.assertIn("오프라인", message)
            self.assertTrue(released)
            self.assertEqual(manager.supabase.calls, [])


class RemoteTreeOrderMaterializationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        workspace = str(Path(self.temp.name, "작품목록"))
        writing_root = str(Path(workspace, "빈 폴더 동기화", "집필모드"))
        # Standard user folders only exist after the creation transaction now.
        from project_creation_v1 import create_project

        create_project(workspace, "빈 폴더 동기화")
        self.wpm = WritingProjectManager.create_detached(
            workspace, "빈 폴더 동기화", writing_root
        )
        self.store = SyncV2Store(str(Path(self.temp.name, "sync.sqlite3")))
        self.context = self.store.configure_project(
            self.wpm.writing_root_path,
            self.wpm.current_project,
            str(uuid.uuid4()),
        )
        self.manager = SyncManager()
        previous = (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            self.manager._v2_device_id,
            self.manager._v2_protected_paths_provider,
        )
        self.addCleanup(self._restore_manager, previous)
        self.manager._v2_store = self.store
        self.manager._v2_context = self.context
        self.manager._v2_wpm = self.wpm
        self.manager._v2_device_id = str(uuid.uuid4())
        self.manager._v2_protected_paths_provider = None
        self.tree_document_id = str(uuid.uuid5(
            uuid.UUID(self.context["project_id"]), TREE_ORDER_DOCUMENT_PATH
        ))

    def _restore_manager(self, previous):
        (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            self.manager._v2_device_id,
            self.manager._v2_protected_paths_provider,
        ) = previous

    def test_tree_order_protocol_canonicalizes_fixed_root_aliases(self):
        for alias in (
            "플롯",
            "스토리 플롯",
            "🗺️ 스토리 플롯",
            "🗺️ 메인 스토리 틀",
        ):
            with self.subTest(alias=alias):
                content = self.manager._tree_order_content({
                    "<root>": ["원고", alias, "휴지통"],
                    f"메인/{alias}": [],
                })
                normalized = json.loads(content)["tree_order"]
                self.assertEqual(
                    normalized["<root>"], ["원고", "스토리 플롯", "휴지통"]
                )
                self.assertIn("메인/스토리 플롯", normalized)
                if alias != "스토리 플롯":
                    self.assertNotIn(f"메인/{alias}", normalized)

        # The legacy Windows name now normalizes to the shared canonical one.
        validated = self.manager._validated_remote_tree_order({
            "<root>": ["원고", "플롯", "휴지통"],
            "메인/플롯": [],
        })
        self.assertEqual(
            validated["<root>"], ["원고", "스토리 플롯", "휴지통"]
        )
        self.assertIn("메인/스토리 플롯", validated)
        self.assertNotIn("메인/플롯", validated)

    def test_add_volume_durably_queues_tree_and_all_chapters_immediately(self):
        chapter_names = [f"{number:03d}화.txt" for number in range(1, 26)]
        def defer_tree_order(operations, supplied_tree_order):
            self.assertEqual(supplied_tree_order["메인/원고"], ["1권"])
            self.assertEqual(
                supplied_tree_order["메인/원고/1권"], chapter_names
            )
            self.wpm.project_settings["tree_order"] = supplied_tree_order
            self.assertTrue(self.wpm.save_settings())
            return self.manager.defer_tree_order_until_operations(
                supplied_tree_order, operations
            )

        panel = SimpleNamespace(
            wpm=self.wpm,
            sync_manager=self.manager,
            load_tree_data=MagicMock(),
            defer_tree_order_until_operations=MagicMock(
                side_effect=defer_tree_order
            ),
            _open_new_volume_chapters=MagicMock(),
            binder_tree=MagicMock(),
        )
        # The stand-in panel needs the real journalled creation helpers.
        for helper in ("_binder_project_root", "_create_binder_volume"):
            setattr(
                panel,
                helper,
                MethodType(getattr(WritingTreeMixin, helper), panel),
            )
        with patch.object(
            self.manager, "retry_pending_syncs", return_value=False
        ) as retry, patch(
            "writing_tree.QMessageBox.information"
        ), patch("writing_tree.QTimer.singleShot"):
            WritingTreeMixin.add_volume(panel)

        panel.defer_tree_order_until_operations.assert_called_once()
        retry.assert_called_once_with()
        self.assertEqual(
            self.store.counts(self.context["local_key"])["pending"], 25
        )
        self.assertIsNotNone(self.store.tree_order_barrier(
            self.context["local_key"]
        ))
        self.assertIsNone(self.store.get_document(
            self.context["local_key"], TREE_ORDER_DOCUMENT_PATH
        ))
        first_operation = self.store.next_ready_operation(
            self.context["local_key"]
        )
        self.assertEqual(
            first_operation["relative_path"], "메인/원고/1권/001화.txt"
        )
        chapter_documents = [
            document
            for document in self.store.list_documents(self.context["local_key"])
            if document["local_path"].startswith("메인/원고/1권/")
        ]
        self.assertEqual(len(chapter_documents), 25)
        self.assertTrue(all(
            Path(self.wpm.writing_root_path, document["local_path"]).is_file()
            for document in chapter_documents
        ))

        self.manager.supabase = _FakeClient()
        dispatched_paths = []
        while True:
            queued = self.store.next_ready_operation(self.context["local_key"])
            if queued is None:
                break
            dispatched = self.manager._process_v2_operation(
                queued["operation_id"]
            )
            self.assertEqual(dispatched["kind"], "committed")
            dispatched_paths.append(queued["relative_path"])
            self.store.mark_success(queued["operation_id"], dispatched["result"])
            self.manager._release_ready_tree_order_barrier()

        self.assertEqual(len(dispatched_paths), 26)
        self.assertEqual(dispatched_paths[-1], TREE_ORDER_DOCUMENT_PATH)
        self.assertEqual(
            set(dispatched_paths[:-1]),
            {
                f"메인/원고/1권/{number:03d}화.txt"
                for number in range(1, 26)
            },
        )
        applied_tree = self.store.get_document(
            self.context["local_key"], TREE_ORDER_DOCUMENT_PATH
        )
        applied_order = json.loads(applied_tree["base_content"])["tree_order"]
        self.assertEqual(applied_order["메인/원고"], ["1권"])
        self.assertEqual(applied_order["메인/원고/1권"], chapter_names)
        self.assertEqual(
            self.store.counts(self.context["local_key"])["total"], 0
        )
        self.assertIsNone(self.store.tree_order_barrier(
            self.context["local_key"]
        ))

    def test_real_add_volume_barrier_contains_collapsed_chapter_order(self):
        self.assertIsNotNone(self.app)
        panel = WritingTreeMixin()
        panel.wpm = self.wpm
        panel.sync_manager = self.manager
        panel.binder_tree = BinderTreeWidget()
        self.addCleanup(panel.binder_tree.close)
        panel._open_new_volume_chapters = MagicMock()
        panel.load_tree_data()

        with patch.object(
            self.manager, "retry_pending_syncs", return_value=False
        ), patch(
            "writing_tree.QMessageBox.information"
        ), patch("writing_tree.QTimer.singleShot"):
            panel.add_volume()

        barrier = self.store.tree_order_barrier(self.context["local_key"])
        tree_order = json.loads(barrier["tree_order_content"])["tree_order"]
        self.assertEqual(tree_order["메인/원고"], ["1권"])
        self.assertEqual(
            tree_order["메인/원고/1권"],
            [f"{number:03d}화.txt" for number in range(1, 26)],
        )

    def test_root_only_remote_tree_keeps_existing_and_discovers_new_volume_order(self):
        first_names = [f"{number:03d}화.txt" for number in range(1, 4)]
        first_documents = [
            {
                "document_id": str(uuid.uuid4()),
                "relative_path": f"메인/원고/1권/{name}",
                "content": name,
                "revision": 1,
                "is_deleted": False,
            }
            for name in first_names
        ]
        baseline = {
            "<root>": ["원고"],
            "메인/원고": ["1권"],
            "메인/원고/1권": first_names,
        }
        self._apply(baseline, first_documents, revision=1)

        second_names = [f"{number:03d}화.txt" for number in range(4, 7)]
        second_documents = [
            {
                "document_id": str(uuid.uuid4()),
                "relative_path": f"메인/원고/2권/{name}",
                "content": name,
                "revision": 1,
                "is_deleted": False,
            }
            for name in reversed(second_names)
        ]
        root_only = {"<root>": ["원고"]}

        changes = self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(root_only, revision=2),
                *first_documents,
                *second_documents,
            ],
            strict=True,
        )

        applied_order = self.wpm.project_settings["tree_order"]
        self.assertEqual(applied_order["메인/원고"], ["1권", "2권"])
        self.assertEqual(applied_order["메인/원고/1권"], first_names)
        self.assertEqual(applied_order["메인/원고/2권"], second_names)
        self.assertEqual(changes[-1]["kind"], "tree_order")
        stored_tree = self.store.get_document_by_id(self.tree_document_id)
        self.assertEqual(
            json.loads(stored_tree["base_content"])["tree_order"], root_only
        )

    def test_root_only_snapshot_repairs_only_exact_generated_volume_set(self):
        chapter_names = [f"{number:03d}화.txt" for number in range(1, 26)]
        documents = [
            {
                "document_id": str(uuid.uuid4()),
                "relative_path": f"메인/원고/1권/{name}",
                "content": name,
                "revision": 1,
                "is_deleted": False,
            }
            for name in chapter_names
        ]
        root_only = {"<root>": ["원고"]}
        self._apply(root_only, documents, revision=1)
        scrambled = [
            chapter_names[index]
            for index in (
                3, 0, 1, 5, 2, 4, 7, 9, 8, 6, 10, 12, 13,
                11, 18, 16, 14, 17, 15, 20, 19, 22, 21, 23, 24,
            )
        ]
        self.wpm.project_settings["tree_order"][
            "메인/원고/1권"
        ] = scrambled
        self.assertTrue(self.wpm.save_settings())

        change = self._apply_direct(
            root_only, revision=1, live_paths={
                row["relative_path"] for row in documents
            }
        )

        self.assertEqual(change["kind"], "tree_order")
        self.assertEqual(
            self.wpm.project_settings["tree_order"]["메인/원고/1권"],
            chapter_names,
        )

    def test_tree_order_barrier_survives_retry_and_restart(self):
        paths = [
            "메인/원고/1권/001화.txt",
            "메인/원고/1권/002화.txt",
        ]
        operations = []
        for relative_path in paths:
            self.assertTrue(self.wpm.write_text_file(relative_path, ""))
            operations.append(self.manager.record_created_document(
                relative_path, retry=False
            ))
        tree_order = {
            "메인/원고": ["1권"],
            "메인/원고/1권": ["001화.txt", "002화.txt"],
        }
        barrier = self.manager.defer_tree_order_until_operations(
            tree_order, operations
        )

        self.store.mark_success(operations[0]["operation_id"], {
            "revision": 1, "content_hash": "a" * 64,
        })
        self.store.mark_retry(
            operations[1]["operation_id"], "NETWORK_UNAVAILABLE"
        )
        self.assertIsNone(self.manager._release_ready_tree_order_barrier())
        self.assertIsNone(self.store.get_document(
            self.context["local_key"], TREE_ORDER_DOCUMENT_PATH
        ))

        reopened = SyncV2Store(self.store.db_path)
        self.manager._v2_store = reopened
        recovered_barrier = reopened.tree_order_barrier(
            self.context["local_key"]
        )
        self.assertEqual(recovered_barrier["barrier_id"], barrier["barrier_id"])
        self.assertIsNone(self.manager._release_ready_tree_order_barrier())

        reopened.mark_success(operations[1]["operation_id"], {
            "revision": 1, "content_hash": "b" * 64,
        })
        released = self.manager._release_ready_tree_order_barrier()

        self.assertIsNotNone(released)
        self.assertEqual(released["relative_path"], TREE_ORDER_DOCUMENT_PATH)
        self.assertIsNone(reopened.tree_order_barrier(
            self.context["local_key"]
        ))

    def test_project_open_adopts_remote_uuid_before_recovering_untracked_files(self):
        remote_paths = [
            "메인/원고/1권/001화.txt",
            "메인/원고/1권/002화.txt",
        ]
        empty_local_only = "메인/원고/1권/003화.txt"
        nonempty_local_only = "메인/원고/1권/004화.txt"
        for relative_path in remote_paths + [empty_local_only]:
            self.assertTrue(self.wpm.write_text_file(relative_path, ""))
        self.assertTrue(
            self.wpm.write_text_file(nonempty_local_only, "복구해야 할 문장")
        )

        self.manager.configure_v2(
            self.wpm,
            self.wpm.current_project,
            self.manager._v2_device_id,
            store=self.store,
            project_id=self.context["project_id"],
        )

        for relative_path in remote_paths + [empty_local_only, nonempty_local_only]:
            self.assertIsNone(self.store.get_document(
                self.context["local_key"], relative_path
            ))
        self.assertEqual(
            self.store.counts(self.context["local_key"])["total"], 0
        )

        remote_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        remote_documents = [
            {
                "document_id": document_id,
                "relative_path": relative_path,
                "content": "",
                "revision": 1,
                "is_deleted": False,
            }
            for document_id, relative_path in zip(remote_ids, remote_paths)
        ]
        self.manager._apply_v2_remote_documents(remote_documents, strict=True)
        recovered = self.manager._recover_untracked_local_files_after_pull(
            remote_documents
        )

        self.assertEqual(recovered, 1)
        for document_id, relative_path in zip(remote_ids, remote_paths):
            adopted = self.store.get_document(
                self.context["local_key"], relative_path
            )
            self.assertEqual(adopted["document_id"], document_id)
            self.assertEqual(adopted["revision"], 1)
            self.assertNotIn(
                relative_path, self.manager._v2_untracked_recovery_paths
            )
        resumed_callback = MagicMock()
        self.manager.upload_content_async(
            self.wpm,
            self.wpm.current_project,
            remote_paths[0],
            "",
            callback=resumed_callback,
        )
        resumed_callback.assert_called_once_with(
            True, "", remote_paths[0], 1
        )
        self.assertIsNone(self.store.get_document(
            self.context["local_key"], empty_local_only
        ))
        recovered_document = self.store.get_document(
            self.context["local_key"], nonempty_local_only
        )
        self.assertIsNotNone(recovered_document)
        queued = self.store.next_ready_operation(self.context["local_key"])
        self.assertEqual(queued["relative_path"], nonempty_local_only)
        self.assertEqual(queued["content"], "복구해야 할 문장")

    def test_uuid_adoption_requires_no_history_conflict_or_active_edit(self):
        active_path = "메인/메모장/활성.txt"
        self.assertTrue(self.wpm.write_text_file(active_path, "같은 바이트"))
        local_active = self.store.ensure_document(
            self.context["local_key"], active_path, "같은 바이트"
        )
        remote_active_id = str(uuid.uuid4())
        self.manager._v2_active_paths_provider = lambda: {active_path}
        remote_active = [{
            "document_id": remote_active_id,
            "relative_path": active_path,
            "content": "같은 바이트",
            "revision": 4,
            "is_deleted": False,
        }]

        self.assertEqual(
            self.manager._apply_v2_remote_documents(remote_active), []
        )
        self.assertEqual(
            self.store.get_document(
                self.context["local_key"], active_path
            )["document_id"],
            local_active["document_id"],
        )
        self.manager._v2_active_paths_provider = lambda: set()
        adopted_changes = self.manager._apply_v2_remote_documents(
            remote_active, strict=True
        )
        adopted = self.store.get_document(
            self.context["local_key"], active_path
        )
        self.assertEqual(len(adopted_changes), 1)
        self.assertEqual(adopted["document_id"], remote_active_id)
        self.assertEqual(adopted["revision"], 4)
        self.assertIsNone(self.store.get_document_by_id(
            local_active["document_id"]
        ))
        reopened_store = SyncV2Store(self.store.db_path)
        self.assertEqual(
            reopened_store.get_document(
                self.context["local_key"], active_path
            )["document_id"],
            remote_active_id,
        )

        history_path = "메인/메모장/이력.txt"
        self.assertTrue(self.wpm.write_text_file(history_path, "이력 있음"))
        history_operation = self.store.enqueue(
            self.context, history_path, "이력 있음"
        )
        self.store.mark_success(history_operation["operation_id"], {
            "revision": 1, "content_hash": "c" * 64,
        })
        history_local_id = history_operation["document_id"]
        self.assertEqual(self.manager._apply_v2_remote_documents([{
            "document_id": str(uuid.uuid4()),
            "relative_path": history_path,
            "content": "이력 있음",
            "revision": 2,
            "is_deleted": False,
        }]), [])
        self.assertEqual(
            self.store.get_document(
                self.context["local_key"], history_path
            )["document_id"],
            history_local_id,
        )

        conflict_path = "메인/메모장/충돌.txt"
        self.assertTrue(self.wpm.write_text_file(conflict_path, "충돌 로컬"))
        conflict_operation = self.store.enqueue(
            self.context, conflict_path, "충돌 로컬"
        )
        self.store.mark_conflict(
            conflict_operation["operation_id"],
            2,
            conflict_path,
            "충돌 서버",
            "병합 후보",
            "충돌 로컬",
        )
        conflict_local_id = conflict_operation["document_id"]
        self.assertEqual(self.manager._apply_v2_remote_documents([{
            "document_id": str(uuid.uuid4()),
            "relative_path": conflict_path,
            "content": "충돌 로컬",
            "revision": 3,
            "is_deleted": False,
        }]), [])
        self.assertEqual(
            self.store.get_document(
                self.context["local_key"], conflict_path
            )["document_id"],
            conflict_local_id,
        )

    def test_project_open_never_assigns_second_uuid_to_different_local_bytes(self):
        relative_path = "메인/원고/1권/001화.txt"
        local_bytes = "로컬에만 있는 중요한 문장".encode("utf-8")
        full_path = Path(self.wpm.writing_root_path, relative_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(local_bytes)
        before_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
        self.manager.configure_v2(
            self.wpm,
            self.wpm.current_project,
            self.manager._v2_device_id,
            store=self.store,
            project_id=self.context["project_id"],
        )
        remote_documents = [{
            "document_id": str(uuid.uuid4()),
            "relative_path": relative_path,
            "content": "서버의 다른 문장",
            "revision": 3,
            "is_deleted": False,
        }]

        changes = self.manager._apply_v2_remote_documents(remote_documents)
        recovered = self.manager._recover_untracked_local_files_after_pull(
            remote_documents
        )

        self.assertEqual(changes, [])
        self.assertEqual(recovered, 0)
        self.assertIsNone(self.store.get_document(
            self.context["local_key"], relative_path
        ))
        self.assertEqual(
            self.store.counts(self.context["local_key"])["total"], 0
        )
        self.assertEqual(
            hashlib.sha256(full_path.read_bytes()).hexdigest(), before_hash
        )
        self.assertEqual(self.manager.current_sync_state, "conflict")

    def _tree_remote(self, tree_order, revision=1):
        content = self.manager._tree_order_content(tree_order)
        return {
            "document_id": self.tree_document_id,
            "relative_path": TREE_ORDER_DOCUMENT_PATH,
            "content": content,
            "revision": revision,
            "is_deleted": False,
        }

    @staticmethod
    def _live_remote(path, content="문서", revision=1):
        return {
            "document_id": str(uuid.uuid4()),
            "relative_path": path,
            "content": content,
            "revision": revision,
            "is_deleted": False,
        }

    def _apply(self, tree_order, live_documents=None, revision=1, strict=True):
        documents = [self._tree_remote(tree_order, revision)]
        documents.extend(live_documents or [])
        return self.manager._apply_v2_remote_documents(documents, strict=strict)

    def _apply_direct(self, tree_order, revision=1, live_paths=None):
        content = self.manager._tree_order_content(tree_order)
        return self.manager._apply_remote_tree_order_document(
            self.tree_document_id,
            content,
            revision,
            remote_live_document_paths=set(live_paths or []),
        )

    def test_remote_document_rename_does_not_scramble_equivalent_volume_orders(self):
        first_volume = [f"{number:03d}화.txt" for number in range(1, 7)]
        second_volume = [f"{number:03d}화.txt" for number in range(7, 13)]
        baseline_order = {
            "<root>": ["원고"],
            "메인/원고": ["1권", "2권"],
            "메인/원고/1권": first_volume,
            "메인/원고/2권": second_volume,
        }
        document_rows = []
        for volume, names in (("1권", first_volume), ("2권", second_volume)):
            for name in names:
                document_rows.append({
                    "document_id": str(uuid.uuid4()),
                    "relative_path": f"메인/원고/{volume}/{name}",
                    "content": f"보존할 원고 {volume} {name}",
                    "revision": 1,
                    "is_deleted": False,
                })
        self._apply(baseline_order, document_rows, revision=1)
        before_hashes = {
            row["document_id"]: hashlib.sha256(
                Path(
                    self.wpm.writing_root_path, row["relative_path"]
                ).read_bytes()
            ).hexdigest()
            for row in document_rows
        }

        renamed_row = next(
            row for row in document_rows
            if row["relative_path"].endswith("002화.txt")
        )
        renamed_row["relative_path"] = "메인/원고/1권/002화 새 이름.txt"
        renamed_row["revision"] = 2
        remote_order = {
            "<root>": ["원고"],
            "메인/원고": ["1권", "2권"],
            "메인/원고/1권": [
                "005화.txt", "002화 새 이름.txt", "001화.txt",
                "006화.txt", "003화.txt", "004화.txt",
            ],
            "메인/원고/2권": [
                "011화.txt", "008화.txt", "012화.txt",
                "007화.txt", "010화.txt", "009화.txt",
            ],
        }
        remote_documents = [self._tree_remote(remote_order, revision=2)]
        remote_documents[0]["content"] = json.dumps(
            {"version": 1, "tree_order": remote_order}, ensure_ascii=False
        )
        remote_documents.extend(document_rows)

        changes = self.manager._apply_v2_remote_documents(
            remote_documents, strict=True
        )

        expected_order = copy.deepcopy(baseline_order)
        expected_order["메인/원고/1권"][1] = "002화 새 이름.txt"
        self.assertEqual(self.wpm.project_settings["tree_order"], expected_order)
        self.assertFalse(Path(
            self.wpm.writing_root_path, "메인/원고/1권/002화.txt"
        ).exists())
        self.assertTrue(Path(
            self.wpm.writing_root_path, "메인/원고/1권/002화 새 이름.txt"
        ).is_file())
        self.assertEqual(
            [change.get("kind") for change in changes], [None, "tree_order"]
        )
        for row in document_rows:
            self.assertEqual(
                hashlib.sha256(Path(
                    self.wpm.writing_root_path, row["relative_path"]
                ).read_bytes()).hexdigest(),
                before_hashes[row["document_id"]],
            )
        stored_tree = self.store.get_document_by_id(self.tree_document_id)
        self.assertEqual(
            json.loads(stored_tree["base_content"])["tree_order"], remote_order
        )

        repeated = self.manager._apply_v2_remote_documents(
            remote_documents, strict=True
        )
        self.assertEqual(repeated, [])
        self.assertEqual(self.wpm.project_settings["tree_order"], expected_order)

    def test_manuscript_tree_reorder_is_always_normalized_by_chapter_number(self):
        names = ["001화.txt", "002화.txt", "003화.txt"]
        baseline_order = {
            "<root>": ["원고"],
            "메인/원고": ["1권"],
            "메인/원고/1권": names,
        }
        documents = [
            {
                "document_id": str(uuid.uuid4()),
                "relative_path": f"메인/원고/1권/{name}",
                "content": name,
                "revision": 1,
                "is_deleted": False,
            }
            for name in names
        ]
        self._apply(baseline_order, documents, revision=1)
        reordered = copy.deepcopy(baseline_order)
        reordered["메인/원고/1권"] = [
            "003화.txt", "001화.txt", "002화.txt"
        ]

        raw_remote = self._tree_remote(reordered, revision=2)
        raw_remote["content"] = json.dumps(
            {"version": 1, "tree_order": reordered}, ensure_ascii=False
        )
        changes = self.manager._apply_v2_remote_documents(
            [raw_remote, *documents],
            strict=True,
        )

        self.assertEqual(changes[-1]["kind"], "tree_order")
        self.assertEqual(self.wpm.project_settings["tree_order"], baseline_order)
        outbound = json.loads(self.manager._tree_order_content(reordered))
        self.assertEqual(outbound["tree_order"], baseline_order)

        self.wpm.project_settings["tree_order"] = copy.deepcopy(reordered)
        self.assertTrue(self.wpm.save_settings())
        repeated = self.manager._apply_v2_remote_documents(
            [raw_remote, *documents], strict=True
        )
        self.assertEqual(repeated[-1]["kind"], "tree_order")
        self.assertEqual(self.wpm.project_settings["tree_order"], baseline_order)

    def test_tree_only_reorder_is_still_applied_for_freely_ordered_folder(self):
        baseline = {"메인/메모장": ["A.txt", "B.txt", "C.txt"]}
        reordered = {"메인/메모장": ["C.txt", "A.txt", "B.txt"]}
        live_documents = [
            self._live_remote(f"메인/메모장/{name}", content=name)
            for name in baseline["메인/메모장"]
        ]
        self._apply(baseline, live_documents, revision=1)

        changes = self._apply(reordered, live_documents, revision=2)

        self.assertEqual(changes[-1]["kind"], "tree_order")
        self.assertEqual(self.wpm.project_settings["tree_order"], reordered)

    def test_remote_tree_order_materializes_nested_and_leaf_empty_folders(self):
        tree_order = {
            "<root>": ["13-2 테스트"],
            "메인/13-2 테스트": ["빈 폴더"],
            "메인/13-2 테스트/빈 폴더": ["하위 빈 폴더"],
        }

        changes = self._apply(tree_order)

        root = Path(self.wpm.writing_root_path)
        self.assertTrue(Path(root, "메인", "13-2 테스트").is_dir())
        self.assertTrue(Path(root, "메인", "13-2 테스트", "빈 폴더").is_dir())
        self.assertTrue(
            Path(root, "메인", "13-2 테스트", "빈 폴더", "하위 빈 폴더").is_dir()
        )
        self.assertEqual([change["kind"] for change in changes], ["tree_order"])
        self.assertEqual(self.wpm.project_settings["tree_order"], tree_order)
        self.assertFalse(Path(root, "__antigravity__").exists())

        reloaded = WritingProjectManager.create_detached(
            self.wpm.workspace_dir,
            self.wpm.current_project,
            self.wpm.writing_root_path,
        )
        self.assertEqual(reloaded.project_settings["tree_order"], tree_order)
        self.assertTrue(
            Path(root, "메인", "13-2 테스트", "빈 폴더", "하위 빈 폴더").is_dir()
        )

    def test_remote_tree_order_defers_unresolved_txt_instead_of_making_folder(self):
        live_path = "메인/13-2 테스트/문서 A.txt"
        blocked_path = "메인/13-2 테스트/대기.txt"
        self.manager._v2_protected_paths_provider = lambda: {blocked_path}
        tree_order = {
            "<root>": ["13-2 테스트"],
            "메인/13-2 테스트": [
                "폴더 B",
                "문서 A.txt",
                "자료.txt",
                "대기.txt",
            ],
        }

        changes = self._apply(
            tree_order,
            [self._live_remote(live_path), self._live_remote(blocked_path)],
            strict=False,
        )

        root = Path(self.wpm.writing_root_path, "메인", "13-2 테스트")
        self.assertTrue(Path(root, "문서 A.txt").is_file())
        self.assertFalse(Path(root, "대기.txt").exists())
        self.assertFalse(Path(root, "자료.txt").exists())
        self.assertFalse(Path(root, "폴더 B").exists())
        self.assertFalse(any(
            change.get("kind") == "tree_order" for change in changes
        ))
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_folder_projection_defers_unresolved_extensionless_document(self):
        main_id = str(uuid.uuid4())
        memo_id = str(uuid.uuid4())
        unresolved_path = "메인/메모장/팯-문서"
        legacy_content = json.dumps({
            "version": 1,
            "tree_order": {
                "<root>": ["메모장"],
                "메인/메모장": ["팯-문서"],
            },
        }, ensure_ascii=False)
        remote = {
            "document_id": self.tree_document_id,
            "relative_path": TREE_ORDER_DOCUMENT_PATH,
            "content": legacy_content,
            "revision": 1,
            "is_deleted": False,
        }
        folder_rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": memo_id,
                "parent_folder_id": main_id,
                "name": "메모장",
                "revision": 1,
                "is_deleted": False,
            },
        ]

        changes = self.manager._apply_v2_remote_documents(
            [remote], strict=False, folder_rows=folder_rows
        )

        self.assertEqual(changes, [])
        self.assertFalse(Path(
            self.wpm.writing_root_path, unresolved_path
        ).exists())
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_ipad_extensionless_document_alias_allows_same_snapshot_empty_folder(self):
        baseline = {
            "<root>": ["메모장"],
            "메인/메모장": [],
        }
        self._apply(baseline, revision=9)

        main_id = str(uuid.uuid4())
        empty_folder_id = str(uuid.uuid4())
        nonempty_folder_id = str(uuid.uuid4())
        empty_folder = "메인/팯-빈폴더"
        nonempty_folder = "메인/팯-든폴더"
        document_path = f"{nonempty_folder}/팯-문서.txt"
        remote_order = {
            "<root>": ["메모장", "팯-빈폴더", "팯-든폴더"],
            "메인/메모장": [],
            empty_folder: [],
            nonempty_folder: ["팯-문서"],
        }
        remote_tree = {
            "document_id": self.tree_document_id,
            "relative_path": TREE_ORDER_DOCUMENT_PATH,
            "content": json.dumps({
                "version": 1,
                "tree_order": remote_order,
            }, ensure_ascii=False),
            "revision": 12,
            "is_deleted": False,
        }
        folder_rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": empty_folder_id,
                "parent_folder_id": main_id,
                "name": "팯-빈폴더",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": nonempty_folder_id,
                "parent_folder_id": main_id,
                "name": "팯-든폴더",
                "revision": 1,
                "is_deleted": False,
            },
        ]

        changes = self.manager._apply_v2_remote_documents(
            [remote_tree, self._live_remote(document_path)],
            strict=True,
            folder_rows=folder_rows,
        )

        root = Path(self.wpm.writing_root_path)
        self.assertTrue(Path(root, empty_folder).is_dir())
        self.assertTrue(Path(root, document_path).is_file())
        self.assertFalse(Path(root, nonempty_folder, "팯-문서").exists())
        stored_tree = self.store.get_document_by_id(self.tree_document_id)
        self.assertEqual(stored_tree["revision"], 12)
        self.assertEqual(
            json.loads(stored_tree["base_content"])["tree_order"],
            remote_order,
        )
        self.assertEqual(changes[-1]["kind"], "tree_order")

    def test_ipad_partial_folder_projection_keeps_tree_parent_folders(self):
        main_id = str(uuid.uuid4())
        manuscript_id = str(uuid.uuid4())
        third_volume_id = str(uuid.uuid4())
        fourth_volume_id = str(uuid.uuid4())
        empty_folder_id = str(uuid.uuid4())
        nonempty_folder_id = str(uuid.uuid4())
        document_path = "메인/팯-든폴더/팯-문서.txt"
        remote_order = {
            "<root>": ["원고", "팯-빈폴더", "팯-든폴더"],
            "메인/원고": ["1권", "2권", "3권", "4권"],
            "메인/원고/1권": [],
            "메인/원고/2권": [],
            "메인/원고/3권": [],
            "메인/원고/4권": [],
            "메인/팯-빈폴더": [],
            "메인/팯-든폴더": ["팯-문서"],
        }
        remote_tree = {
            "document_id": self.tree_document_id,
            "relative_path": TREE_ORDER_DOCUMENT_PATH,
            "content": json.dumps({
                "version": 1,
                "tree_order": remote_order,
            }, ensure_ascii=False),
            "revision": 13,
            "is_deleted": False,
        }
        # Older Windows-created volumes have no stable folder rows. Newer iPad
        # folders do. The tree parent keys are still authoritative evidence that
        # all four volume paths are directories.
        folder_rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": manuscript_id,
                "parent_folder_id": main_id,
                "name": "원고",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": third_volume_id,
                "parent_folder_id": manuscript_id,
                "name": "3권",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": fourth_volume_id,
                "parent_folder_id": manuscript_id,
                "name": "4권",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": empty_folder_id,
                "parent_folder_id": main_id,
                "name": "팯-빈폴더",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": nonempty_folder_id,
                "parent_folder_id": main_id,
                "name": "팯-든폴더",
                "revision": 1,
                "is_deleted": False,
            },
        ]

        changes = self.manager._apply_v2_remote_documents(
            [remote_tree, self._live_remote(document_path)],
            strict=True,
            folder_rows=folder_rows,
        )

        root = Path(self.wpm.writing_root_path)
        for volume in ("1권", "2권", "3권", "4권"):
            self.assertTrue(Path(root, "메인", "원고", volume).is_dir())
        self.assertTrue(Path(root, "메인", "팯-빈폴더").is_dir())
        self.assertTrue(Path(root, document_path).is_file())
        self.assertFalse(Path(root, "메인", "팯-든폴더", "팯-문서").exists())
        stored_tree = self.store.get_document_by_id(self.tree_document_id)
        self.assertEqual(stored_tree["revision"], 13)
        self.assertEqual(changes[-1]["kind"], "tree_order")

    def test_ipad_document_alias_conflicting_with_stable_folder_is_rejected(self):
        main_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())
        conflicting_folder_id = str(uuid.uuid4())
        parent_path = "메인/팯-든폴더"
        document_path = f"{parent_path}/팯-문서.txt"
        remote_tree = {
            "document_id": self.tree_document_id,
            "relative_path": TREE_ORDER_DOCUMENT_PATH,
            "content": json.dumps({
                "version": 1,
                "tree_order": {
                    "<root>": ["팯-든폴더"],
                    parent_path: ["팯-문서"],
                    f"{parent_path}/팯-문서": [],
                },
            }, ensure_ascii=False),
            "revision": 12,
            "is_deleted": False,
        }
        folder_rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": parent_id,
                "parent_folder_id": main_id,
                "name": "팯-든폴더",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": conflicting_folder_id,
                "parent_folder_id": parent_id,
                "name": "팯-문서",
                "revision": 1,
                "is_deleted": False,
            },
        ]

        with self.assertRaises(FileExistsError):
            self.manager._apply_v2_remote_documents(
                [remote_tree, self._live_remote(document_path)],
                strict=True,
                folder_rows=folder_rows,
            )

        root = Path(self.wpm.writing_root_path)
        self.assertTrue(Path(root, document_path).is_file())
        self.assertFalse(Path(root, parent_path, "팯-문서").exists())
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_modern_tree_payload_marks_empty_folders_explicitly(self):
        empty_folder = "메인/메모장/윈_빈폴더"
        tree_order = {
            "<root>": ["메모장"],
            "메인/메모장": ["윈_빈폴더"],
            empty_folder: [],
        }
        payload = json.loads(self.manager._tree_order_content(tree_order))

        self.assertIn("folder_paths", payload)
        self.assertIn(empty_folder, payload["folder_paths"])

        change = self.manager._apply_remote_tree_order_document(
            self.tree_document_id,
            json.dumps(payload, ensure_ascii=False),
            1,
            remote_folder_paths=set(),
            has_remote_folder_projection=True,
        )

        self.assertEqual(change["kind"], "tree_order")
        self.assertTrue(Path(
            self.wpm.writing_root_path, empty_folder
        ).is_dir())

    def test_stable_folder_identity_can_materialize_folder_named_txt(self):
        main_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())
        txt_folder_id = str(uuid.uuid4())
        rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": parent_id,
                "parent_folder_id": main_id,
                "name": "13-2 테스트",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": txt_folder_id,
                "parent_folder_id": parent_id,
                "name": "자료.txt",
                "revision": 1,
                "is_deleted": False,
            },
        ]
        tree_order = {
            "<root>": ["13-2 테스트"],
            "메인/13-2 테스트": ["자료.txt"],
        }

        changes = self.manager._apply_v2_remote_documents(
            [self._tree_remote(tree_order)],
            strict=True,
            folder_rows=rows,
        )

        target = Path(
            self.wpm.writing_root_path, "메인", "13-2 테스트", "자료.txt"
        )
        self.assertTrue(target.is_dir())
        self.assertEqual(changes[-1]["kind"], "tree_order")

    def test_remote_live_document_path_conflicting_with_directory_blocks_tree_baseline(self):
        document_path = "메인/메모장/문서 충돌.txt"
        Path(self.wpm.writing_root_path, document_path).mkdir()
        tree_order = {"메인/메모장": ["문서 충돌.txt"]}

        with self.assertRaises(FileExistsError):
            self._apply_direct(tree_order, live_paths={document_path})

        self.assertTrue(Path(self.wpm.writing_root_path, document_path).is_dir())
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_is_idempotent_for_existing_directories(self):
        existing = Path(
            self.wpm.writing_root_path, "메인", "13-2 테스트", "기존 빈 폴더"
        )
        existing.mkdir(parents=True)
        marker = Path(existing, "보존.txt")
        marker.write_text("보존", encoding="utf-8")
        tree_order = {
            "<root>": ["13-2 테스트"],
            "메인/13-2 테스트": ["기존 빈 폴더", "새 빈 폴더"],
        }

        first = self._apply(tree_order)
        second = self._apply(tree_order)

        self.assertEqual([item["kind"] for item in first], ["tree_order"])
        self.assertEqual(second, [])
        self.assertEqual(marker.read_text(encoding="utf-8"), "보존")
        self.assertEqual(
            [path.name for path in existing.parent.iterdir()].count("새 빈 폴더"), 1
        )

    def test_remote_empty_folder_rename_replaces_old_name_once(self):
        old_order = {
            "메인/메모장": ["가나다"],
            "메인/메모장/가나다": [],
        }
        new_order = {
            "메인/메모장": ["가나다바"],
            "메인/메모장/가나다바": [],
        }
        self._apply(old_order, revision=1)

        changes = self._apply(new_order, revision=2)

        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        self.assertFalse(Path(parent, "가나다").exists())
        self.assertTrue(Path(parent, "가나다바").is_dir())
        self.assertEqual([item["kind"] for item in changes], ["tree_order"])

    def test_remote_root_empty_folder_rename_accepts_omitted_leaf_key(self):
        old_path = "메인/새 폴더2"
        new_path = "메인/새 폴더3"
        old_order = {
            "<root>": ["원고", "새 폴더2"],
            old_path: [],
        }
        # iPad publishes an empty root folder in the parent list without the
        # otherwise redundant ``메인/새 폴더3: []`` leaf entry.
        new_order = {
            "<root>": ["원고", "새 폴더3"],
        }
        self._apply(old_order, revision=1)

        changes = self._apply(new_order, revision=2)

        root = Path(self.wpm.writing_root_path, "메인")
        self.assertFalse(Path(root, "새 폴더2").exists())
        self.assertTrue(Path(root, "새 폴더3").is_dir())
        self.assertEqual(
            [path.name for path in root.iterdir()].count("새 폴더3"), 1
        )
        self.assertEqual([item["kind"] for item in changes], ["tree_order"])
        self.assertEqual(self.wpm.project_settings["tree_order"], new_order)

    def test_remote_root_rename_ignores_fixed_label_and_trash_omission(self):
        old_path = "메인/새 폴더4"
        new_path = "메인/새 폴더ㅅ"
        chapters = [f"{number:03d}화.txt" for number in range(1, 26)]
        old_order = {
            "<root>": [
                "원고", "캐릭터", "설정집", "메모장", "흐름정리",
                "복선", "장소", "새 폴더4", "플롯", "휴지통",
            ],
            "메인/메모장": [],
            old_path: [],
            "메인/원고": ["1권"],
            "메인/원고/1권": chapters,
        }
        # This mirrors the iPad representation: a fixed-root alias, no trash,
        # omitted empty fixed-folder keys, and no redundant custom leaf key.
        new_order = {
            "<root>": [
                "원고", "캐릭터", "설정집", "메모장", "흐름정리",
                "복선", "장소", "새 폴더ㅅ", "스토리 플롯",
            ],
            "메인/원고": ["1권"],
            "메인/원고/1권": chapters,
        }
        chapter_documents = [
            self._live_remote(
                f"메인/원고/1권/{name}", content=f"본문 {name}"
            )
            for name in chapters
        ]
        self._apply(old_order, chapter_documents, revision=9)

        changes = self._apply(new_order, chapter_documents, revision=10)

        root = Path(self.wpm.writing_root_path, "메인")
        self.assertFalse(Path(root, "새 폴더4").exists())
        self.assertTrue(Path(root, "새 폴더ㅅ").is_dir())
        self.assertEqual(
            [path.name for path in root.iterdir()].count("새 폴더ㅅ"), 1
        )
        self.assertEqual([item["kind"] for item in changes], ["tree_order"])

    def test_remote_root_does_not_pair_multiple_custom_name_changes(self):
        old_order = {
            "<root>": ["원고", "옛 A", "옛 B", "플롯", "휴지통"],
        }
        new_order = {
            "<root>": ["원고", "새 A", "새 B", "스토리 플롯"],
        }
        self._apply(old_order, revision=1)

        with patch("sync_manager.os.rename", wraps=os.rename) as rename:
            self._apply(new_order, revision=2)

        root = Path(self.wpm.writing_root_path, "메인")
        for name in ("옛 A", "옛 B", "새 A", "새 B"):
            self.assertTrue(Path(root, name).is_dir())
        rename.assert_not_called()

    def test_remote_empty_folder_rename_snapshot_reapply_is_idempotent(self):
        old_order = {"메인/메모장": ["옛 이름"]}
        new_order = {"메인/메모장": ["새 이름"]}
        self._apply(old_order, revision=1)
        self._apply(new_order, revision=2)

        changes = self._apply(new_order, revision=2)

        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        self.assertEqual(changes, [])
        self.assertFalse(Path(parent, "옛 이름").exists())
        self.assertTrue(Path(parent, "새 이름").is_dir())
        self.assertEqual([path.name for path in parent.iterdir()].count("새 이름"), 1)

    def test_remote_empty_folder_rename_never_moves_nonempty_folder(self):
        old_order = {"메인/메모장": ["옛 이름"]}
        new_order = {"메인/메모장": ["새 이름"]}
        self._apply(old_order, revision=1)
        manuscript = Path(
            self.wpm.writing_root_path, "메인", "메모장", "옛 이름", "원고.bin"
        )
        original = b"\x00\x01\xff\xed\x95\x9c\xea\xb8\x80"
        manuscript.write_bytes(original)
        before_hash = hashlib.sha256(manuscript.read_bytes()).hexdigest()

        with patch("sync_manager.os.rename", wraps=os.rename) as rename:
            self._apply(new_order, revision=2)

        parent = manuscript.parent.parent
        self.assertTrue(manuscript.is_file())
        self.assertTrue(Path(parent, "새 이름").is_dir())
        self.assertEqual(
            hashlib.sha256(manuscript.read_bytes()).hexdigest(), before_hash
        )
        rename.assert_not_called()

    def test_remote_empty_folder_rename_never_overwrites_existing_target(self):
        old_order = {"메인/메모장": ["옛 이름"]}
        new_order = {"메인/메모장": ["새 이름"]}
        self._apply(old_order, revision=1)
        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        new_marker = Path(parent, "새 이름", "new.bin")
        new_marker.parent.mkdir()
        new_marker.write_bytes(b"new manuscript")
        new_hash = hashlib.sha256(new_marker.read_bytes()).hexdigest()
        old_folder = Path(parent, "옛 이름")
        old_entries = list(old_folder.iterdir())

        with patch("sync_manager.os.rename", wraps=os.rename) as rename:
            self._apply(new_order, revision=2)

        self.assertEqual(list(old_folder.iterdir()), old_entries)
        self.assertEqual(hashlib.sha256(new_marker.read_bytes()).hexdigest(), new_hash)
        rename.assert_not_called()

    def test_remote_empty_folder_rename_skips_local_unsent_tree_order(self):
        old_order = {"메인/메모장": ["옛 이름"]}
        new_order = {"메인/메모장": ["새 이름"]}
        local_order = {"메인/메모장": ["옛 이름", "로컬 폴더"]}
        self._apply(old_order, revision=1)
        self.wpm.project_settings["tree_order"] = local_order
        self.assertTrue(self.wpm.save_settings())
        operation = self.manager.record_tree_order(local_order, retry=False)
        self.assertIsNotNone(operation)

        with patch("sync_manager.os.rename", wraps=os.rename) as rename:
            changes = self._apply(new_order, revision=2, strict=False)

        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        self.assertEqual(changes, [])
        self.assertTrue(Path(parent, "옛 이름").is_dir())
        self.assertFalse(Path(parent, "새 이름").exists())
        self.assertEqual(self.wpm.project_settings["tree_order"], local_order)
        rename.assert_not_called()

    def test_remote_empty_folder_rename_never_moves_reparse_point(self):
        old_order = {"메인/메모장": ["옛 이름"]}
        new_order = {"메인/메모장": ["새 이름"]}
        self._apply(old_order, revision=1)
        old_folder = Path(self.wpm.writing_root_path, "메인", "메모장", "옛 이름")
        real_is_reparse = self.manager._is_reparse_path

        with patch.object(
            SyncManager,
            "_is_reparse_path",
            side_effect=lambda path: (
                os.path.abspath(path) == os.path.abspath(old_folder)
                or real_is_reparse(path)
            ),
        ), patch("sync_manager.os.rename", wraps=os.rename) as rename:
            self._apply(new_order, revision=2)

        self.assertTrue(old_folder.is_dir())
        self.assertTrue(Path(old_folder.parent, "새 이름").is_dir())
        rename.assert_not_called()

    def test_remote_empty_folder_rename_rolls_back_when_settings_save_fails(self):
        old_order = {"메인/메모장": ["옛 이름"]}
        new_order = {"메인/메모장": ["새 이름"]}
        self._apply(old_order, revision=1)

        with patch.object(self.wpm, "save_settings", return_value=False):
            with self.assertRaises(OSError):
                self._apply_direct(new_order, revision=2)

        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        self.assertTrue(Path(parent, "옛 이름").is_dir())
        self.assertFalse(Path(parent, "새 이름").exists())
        self.assertEqual(self.wpm.project_settings["tree_order"], old_order)
        reloaded = WritingProjectManager.create_detached(
            self.wpm.workspace_dir,
            self.wpm.current_project,
            self.wpm.writing_root_path,
        )
        self.assertEqual(reloaded.project_settings["tree_order"], old_order)
        self.assertEqual(
            self.store.get_document_by_id(self.tree_document_id)["revision"], 1
        )

    def test_remote_tree_order_does_not_pair_multiple_additions_and_removals(self):
        old_order = {"메인/메모장": ["옛 A", "옛 B"]}
        new_order = {"메인/메모장": ["새 A", "새 B"]}
        self._apply(old_order, revision=1)

        with patch("sync_manager.os.rename", wraps=os.rename) as rename:
            self._apply(new_order, revision=2)

        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        self.assertTrue(Path(parent, "옛 A").is_dir())
        self.assertTrue(Path(parent, "옛 B").is_dir())
        self.assertTrue(Path(parent, "새 A").is_dir())
        self.assertTrue(Path(parent, "새 B").is_dir())
        rename.assert_not_called()

    def test_remote_document_sync_still_works_with_tree_order_materialization(self):
        document_path = "메인/메모장/문서 폴더/원고.txt"
        document = self._live_remote(document_path, "첫 내용", revision=1)
        tree_order = {
            "메인/메모장": ["문서 폴더"],
            "메인/메모장/문서 폴더": ["원고.txt"],
        }
        self._apply(tree_order, [document], revision=1)
        document["content"] = "원격 최신 내용"
        document["revision"] = 2

        self._apply(tree_order, [document], revision=2)

        manuscript = Path(self.wpm.writing_root_path, document_path)
        self.assertEqual(manuscript.read_text(encoding="utf-8"), "원격 최신 내용")
        self.assertTrue(manuscript.parent.is_dir())

    def test_local_empty_folder_rename_still_queues_tree_order_for_ipad(self):
        old_order = {"메인/메모장": ["옛 이름"]}
        new_order = {"메인/메모장": ["새 이름"]}
        self._apply(old_order, revision=1)
        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        os.rename(Path(parent, "옛 이름"), Path(parent, "새 이름"))
        self.wpm.project_settings["tree_order"] = new_order
        self.assertTrue(self.wpm.save_settings())

        operation = self.manager.record_tree_order(new_order, retry=False)
        payload = json.loads(operation["content"])

        self.assertEqual(payload["tree_order"], new_order)
        self.assertNotIn("옛 이름", payload["tree_order"]["메인/메모장"])
        self.assertEqual(operation["relative_path"], TREE_ORDER_DOCUMENT_PATH)

    def test_reloaded_custom_root_empty_folder_rename_is_durably_queued(self):
        old_path = "메인/새 폴더1"
        new_path = "메인/새 폴더2"
        self._apply({
            "<root>": ["새 폴더1"],
            old_path: [],
        }, revision=1)
        panel = WritingTreeMixin()
        panel.wpm = self.wpm
        panel.sync_manager = self.manager
        panel.binder_tree = BinderTreeWidget()
        self.addCleanup(panel.binder_tree.close)
        panel.controller = SimpleNamespace(rename_path=MagicMock())
        panel.loaded_versions = {}
        panel.current_loaded_file_left = None
        panel.current_loaded_file_right = None
        panel.lbl_current_doc = MagicMock()
        panel.lbl_r_doc = MagicMock()
        panel.load_tree_data()
        item = next(
            panel.binder_tree.topLevelItem(index)
            for index in range(panel.binder_tree.topLevelItemCount())
            if panel.binder_tree.topLevelItem(index).data(
                0, Qt.ItemDataRole.UserRole
            ) == old_path
        )

        self.assertIs(
            item.data(0, Qt.ItemDataRole.UserRole + 1), True
        )
        item.setText(0, "새 폴더2")
        with patch.object(
            self.manager, "retry_pending_syncs", return_value=False
        ), patch("writing_tree.QTimer.singleShot"), patch(
            "writing_tree.QMessageBox.warning"
        ) as warning:
            panel.on_tree_item_changed(item, 0)

        warning.assert_not_called()
        self.assertFalse(Path(self.wpm.writing_root_path, old_path).exists())
        self.assertTrue(Path(self.wpm.writing_root_path, new_path).is_dir())
        self.assertIn(
            "새 폴더2", self.wpm.project_settings["tree_order"]["<root>"]
        )
        self.assertNotIn(
            "새 폴더1", self.wpm.project_settings["tree_order"]["<root>"]
        )
        operation = self.store.next_ready_operation(self.context["local_key"])
        self.assertIsNotNone(operation)
        queued_order = json.loads(operation["content"])["tree_order"]
        self.assertIn("새 폴더2", queued_order["<root>"])
        self.assertNotIn("새 폴더1", queued_order["<root>"])

    def test_inline_nonempty_folder_rename_closes_editor_before_durable_move(self):
        old_path = "메인/메모장/원고 폴더"
        new_path = "메인/메모장/바꾸는 폴더"
        old_document_path = f"{old_path}/보존 원고.txt"
        new_document_path = f"{new_path}/보존 원고.txt"
        content = "이 바이트는 폴더 이름을 바꿔도 그대로여야 합니다."
        old_order = {
            "<root>": ["메모장"],
            "메인/메모장": ["원고 폴더"],
            old_path: ["보존 원고.txt"],
        }
        self._apply(
            old_order,
            [self._live_remote(old_document_path, content)],
            revision=1,
        )
        before_hash = hashlib.sha256(
            Path(self.wpm.writing_root_path, old_document_path).read_bytes()
        ).hexdigest()

        panel = WritingTreeMixin()
        panel.wpm = self.wpm
        panel.sync_manager = self.manager
        panel.binder_tree = BinderTreeWidget()
        self.addCleanup(panel.binder_tree.close)
        panel.controller = SimpleNamespace(rename_path=MagicMock())
        panel.loaded_versions = {}
        panel.current_loaded_file_left = None
        panel.current_loaded_file_right = None
        panel.lbl_current_doc = MagicMock()
        panel.lbl_r_doc = MagicMock()
        panel.load_tree_data()
        memo_root = next(
            panel.binder_tree.topLevelItem(index)
            for index in range(panel.binder_tree.topLevelItemCount())
            if panel.binder_tree.topLevelItem(index).data(
                0, Qt.ItemDataRole.UserRole
            ) == "메인/메모장"
        )
        folder_item = next(
            memo_root.child(index)
            for index in range(memo_root.childCount())
            if memo_root.child(index).data(
                0, Qt.ItemDataRole.UserRole
            ) == old_path
        )
        with patch.object(
            self.manager, "retry_pending_syncs", return_value=False
        ):
            # Exercise the durable handler directly. Real focused QLineEdit
            # simulation is platform-plugin dependent and can terminate the
            # Windows Actions interpreter before unittest prints a traceback.
            folder_item.setText(0, "바꾸는 폴더")
            panel._apply_tree_item_changed(folder_item, 0)

        self.assertFalse(Path(self.wpm.writing_root_path, old_path).exists())
        self.assertTrue(Path(self.wpm.writing_root_path, new_path).is_dir())
        renamed_file = Path(self.wpm.writing_root_path, new_document_path)
        self.assertEqual(
            hashlib.sha256(renamed_file.read_bytes()).hexdigest(), before_hash
        )
        self.assertEqual(renamed_file.read_text(encoding="utf-8"), content)
        moved_document = self.store.get_document(
            self.context["local_key"], new_document_path
        )
        self.assertIsNotNone(moved_document)
        self.assertIsNone(self.store.get_document(
            self.context["local_key"], old_document_path
        ))
        queued = self.store.next_ready_operation(self.context["local_key"])
        self.assertEqual(queued["relative_path"], new_document_path)
        barrier = self.store.tree_order_barrier(self.context["local_key"])
        self.assertIsNotNone(barrier)
        barrier_order = json.loads(barrier["tree_order_content"])["tree_order"]
        self.assertEqual(
            barrier_order["메인/메모장"], ["바꾸는 폴더"]
        )
        self.assertIn(new_path, barrier_order)
        self.assertNotIn(old_path, barrier_order)

    def _persist_local_empty_folder_rename(
        self,
        old_name,
        new_name,
        new_order,
        renamed_event=None,
        allow_enqueue_event=None,
    ):
        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        with self.manager.local_structure_mutation():
            os.rename(Path(parent, old_name), Path(parent, new_name))
            self.manager.record_folder_rename_intent(
                f"메인/메모장/{old_name}",
                f"메인/메모장/{new_name}",
            )
            self.wpm.project_settings["tree_order"] = new_order
            self.assertTrue(self.wpm.save_settings())
            if renamed_event is not None:
                renamed_event.set()
            if (
                allow_enqueue_event is not None
                and not allow_enqueue_event.wait(5)
            ):
                raise TimeoutError("tree-order enqueue test gate timed out")
            return self.manager.record_tree_order(new_order, retry=False)

    def _assert_local_tree_order_is(self, folder_name, expected_order):
        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        self.assertTrue(Path(parent, folder_name).is_dir())
        self.assertEqual(self.wpm.project_settings["tree_order"], expected_order)
        operation = self.store.next_ready_operation(self.context["local_key"])
        self.assertIsNotNone(operation)
        self.assertEqual(
            json.loads(operation["content"])["tree_order"], expected_order
        )
        return operation

    def test_local_rename_holds_gate_until_tree_order_enqueue_is_durable(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        manuscript = Path(self.wpm.writing_root_path, "메인", "원고", "보존.txt")
        manuscript.write_bytes(b"preserve manuscript bytes\x00\xff")
        manuscript_hash = hashlib.sha256(manuscript.read_bytes()).hexdigest()
        renamed = threading.Event()
        allow_enqueue = threading.Event()
        remote_started = threading.Event()
        remote_done = threading.Event()
        errors = []

        def run_local():
            try:
                self._persist_local_empty_folder_rename(
                    "새 폴더F",
                    "새 폴더H",
                    local_order,
                    renamed_event=renamed,
                    allow_enqueue_event=allow_enqueue,
                )
            except Exception as error:
                errors.append(error)

        def run_remote():
            remote_started.set()
            try:
                self._apply_direct(remote_order, revision=12)
            except Exception as error:
                errors.append(error)
            finally:
                remote_done.set()

        local_thread = threading.Thread(target=run_local)
        remote_thread = threading.Thread(target=run_remote)
        local_thread.start()
        self.assertTrue(renamed.wait(5))
        remote_thread.start()
        self.assertTrue(remote_started.wait(5))
        self.assertFalse(remote_done.wait(0.2))

        allow_enqueue.set()
        local_thread.join(5)
        remote_thread.join(5)

        self.assertFalse(local_thread.is_alive())
        self.assertFalse(remote_thread.is_alive())
        self.assertEqual(errors, [])
        self._assert_local_tree_order_is("새 폴더H", local_order)
        self.assertFalse(
            Path(self.wpm.writing_root_path, "메인", "메모장", "새 폴더F").exists()
        )
        self.assertEqual(
            hashlib.sha256(manuscript.read_bytes()).hexdigest(), manuscript_hash
        )

    def test_remote_plan_fetched_before_local_rename_cannot_revert_new_generation(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        remote_validating = threading.Event()
        allow_remote_apply = threading.Event()
        remote_done = threading.Event()
        errors = []
        real_validate = self.manager._validated_remote_tree_order

        def pause_after_fetch(tree_order):
            remote_validating.set()
            if not allow_remote_apply.wait(5):
                raise TimeoutError("remote apply test gate timed out")
            return real_validate(tree_order)

        def run_remote():
            try:
                self._apply_direct(remote_order, revision=12)
            except Exception as error:
                errors.append(error)
            finally:
                remote_done.set()

        with patch.object(
            self.manager,
            "_validated_remote_tree_order",
            side_effect=pause_after_fetch,
        ):
            remote_thread = threading.Thread(target=run_remote)
            remote_thread.start()
            self.assertTrue(remote_validating.wait(5))
            self._persist_local_empty_folder_rename(
                "새 폴더F", "새 폴더H", local_order
            )
            allow_remote_apply.set()
            remote_thread.join(5)

        self.assertFalse(remote_thread.is_alive())
        self.assertTrue(remote_done.is_set())
        self.assertEqual(errors, [])
        self._assert_local_tree_order_is("새 폴더H", local_order)

    def test_remote_apply_first_then_local_rename_queues_latest_order(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)

        change = self._apply_direct(remote_order, revision=13)
        operation = self._persist_local_empty_folder_rename(
            "새 폴더F", "새 폴더H", local_order
        )

        self.assertEqual(change["revision"], 13)
        self.assertEqual(operation["base_revision"], 13)
        self._assert_local_tree_order_is("새 폴더H", local_order)

    def test_local_tree_order_pending_rename_survives_store_and_settings_restart(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        operation = self._persist_local_empty_folder_rename(
            "새 폴더F", "새 폴더H", local_order
        )

        reloaded_wpm = WritingProjectManager.create_detached(
            self.wpm.workspace_dir,
            self.wpm.current_project,
            self.wpm.writing_root_path,
        )
        reloaded_store = SyncV2Store(self.store.db_path)
        recovered = reloaded_store.operation(operation["operation_id"])

        self.assertEqual(reloaded_wpm.project_settings["tree_order"], local_order)
        self.assertTrue(
            Path(reloaded_wpm.writing_root_path, "메인", "메모장", "새 폴더H").is_dir()
        )
        self.assertEqual(recovered["status"], "pending")
        self.assertEqual(
            json.loads(recovered["content"])["tree_order"], local_order
        )

    def test_dispatched_tree_order_next_revision_contains_local_folder_name(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        operation = self._persist_local_empty_folder_rename(
            "새 폴더F", "새 폴더H", local_order
        )
        main_id = str(uuid.uuid4())
        memo_id = str(uuid.uuid4())
        folder_id = str(uuid.uuid4())
        self.manager.supabase = _FolderAwareClient([
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": memo_id,
                "parent_folder_id": main_id,
                "name": "메모장",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": folder_id,
                "parent_folder_id": memo_id,
                "name": "새 폴더F",
                "revision": 2,
                "is_deleted": False,
            },
        ])

        result = self.manager._process_v2_operation(operation["operation_id"])
        folder_commit_name, folder_params = next(
            call for call in self.manager.supabase.calls
            if call[0] == "commit_folder"
        )
        commit_name, params = self.manager.supabase.calls[-1]
        self.store.mark_success(operation["operation_id"], result["result"])
        document = self.store.get_document_by_id(operation["document_id"])

        self.assertEqual(result["kind"], "committed")
        self.assertEqual(folder_commit_name, "commit_folder")
        self.assertEqual(folder_params["p_folder_id"], folder_id)
        self.assertEqual(folder_params["p_base_revision"], 2)
        self.assertEqual(folder_params["p_parent_folder_id"], memo_id)
        self.assertEqual(folder_params["p_name"], "새 폴더H")
        self.assertEqual(commit_name, "commit_document")
        self.assertEqual(params["p_base_revision"], 12)
        self.assertEqual(
            json.loads(params["p_content"])["tree_order"], local_order
        )
        self.assertEqual(document["revision"], 13)
        self.assertEqual(
            json.loads(document["base_content"])["tree_order"], local_order
        )

    def test_outbound_folder_rename_replay_does_not_create_second_revision(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        operation = self._persist_local_empty_folder_rename(
            "새 폴더F", "새 폴더H", local_order
        )
        main_id = str(uuid.uuid4())
        memo_id = str(uuid.uuid4())
        folder_id = str(uuid.uuid4())
        client = _FolderAwareClient([
            {
                "folder_id": main_id, "parent_folder_id": None,
                "name": "메인", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": memo_id, "parent_folder_id": main_id,
                "name": "메모장", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": folder_id, "parent_folder_id": memo_id,
                "name": "새 폴더F", "revision": 2, "is_deleted": False,
            },
        ])
        self.manager.supabase = client

        first = self.manager._commit_outbound_folder_rename(operation, client)
        second = self.manager._commit_outbound_folder_rename(operation, client)

        self.assertEqual(first["operation_kind"], "rename")
        self.assertIsNone(second)
        self.assertEqual(
            [name for name, _params in client.calls], ["commit_folder"]
        )
        self.assertEqual(client.folder_rows[-1]["name"], "새 폴더H")
        self.assertEqual(client.folder_rows[-1]["revision"], 3)

    def test_explicit_rename_commits_when_server_tree_already_has_new_name(self):
        old_path = "메인/아팯_빈폴더_윈"
        new_path = "메인/아팯_빈폴더_팯"
        current_order = {"<root>": ["아팯_빈폴더_팯"]}
        tree_content = self.manager._tree_order_content(current_order)
        self.store.apply_remote_snapshot(
            self.context,
            self.tree_document_id,
            TREE_ORDER_DOCUMENT_PATH,
            tree_content,
            49,
            local_path=TREE_ORDER_DOCUMENT_PATH,
        )
        self.wpm.project_settings["tree_order"] = current_order
        self.assertTrue(self.wpm.save_settings())
        old_folder = Path(self.wpm.writing_root_path, old_path)
        new_folder = Path(self.wpm.writing_root_path, new_path)
        old_folder.mkdir(parents=True)
        os.rename(old_folder, new_folder)
        self.manager.record_folder_rename_intent(old_path, new_path)

        operation = self.manager.record_tree_order(current_order, retry=False)
        main_id = str(uuid.uuid4())
        folder_id = str(uuid.uuid4())
        client = _FolderAwareClient([
            {
                "folder_id": main_id, "parent_folder_id": None,
                "name": "메인", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": folder_id, "parent_folder_id": main_id,
                "name": "아팯_빈폴더_윈", "revision": 4,
                "is_deleted": False,
            },
        ])
        self.manager.supabase = client

        result = self.manager._process_v2_operation(operation["operation_id"])

        self.assertEqual(result["kind"], "committed")
        folder_call = next(
            params for name, params in client.calls if name == "commit_folder"
        )
        self.assertEqual(folder_call["p_folder_id"], folder_id)
        self.assertEqual(folder_call["p_base_revision"], 4)
        self.assertEqual(folder_call["p_name"], "아팯_빈폴더_팯")
        self.assertIsNone(self.store.pending_folder_rename_intent(
            self.context["local_key"], old_path, new_path
        ))

    def test_outbound_folder_rename_does_not_guess_missing_folder_identity(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        operation = self._persist_local_empty_folder_rename(
            "새 폴더F", "새 폴더H", local_order
        )
        client = _FolderAwareClient([])

        result = self.manager._commit_outbound_folder_rename(operation, client)

        self.assertIsNone(result)
        self.assertEqual(client.calls, [])

    def test_tree_order_without_explicit_local_intent_never_commits_folder(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        parent = Path(self.wpm.writing_root_path, "메인", "메모장")
        os.rename(Path(parent, "새 폴더F"), Path(parent, "새 폴더H"))
        operation = self.manager.record_tree_order(local_order, retry=False)
        main_id = str(uuid.uuid4())
        memo_id = str(uuid.uuid4())
        client = _FolderAwareClient([
            {
                "folder_id": main_id, "parent_folder_id": None,
                "name": "메인", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": memo_id, "parent_folder_id": main_id,
                "name": "메모장", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": str(uuid.uuid4()),
                "parent_folder_id": memo_id,
                "name": "새 폴더F", "revision": 2, "is_deleted": False,
            },
        ])

        result = self.manager._commit_outbound_folder_rename(operation, client)

        self.assertIsNone(result)
        self.assertEqual(client.calls, [])

    def test_inconsistent_folder_identity_and_tree_order_never_mkdirs_duplicate(self):
        main_id = str(uuid.uuid4())
        folder_id = str(uuid.uuid4())
        old_path = "메인/아팯_빈폴더_윈"
        new_path = "메인/아팯_빈폴더_팯"
        folder_rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": folder_id,
                "parent_folder_id": main_id,
                "name": "아팯_빈폴더_윈",
                "revision": 4,
                "is_deleted": False,
            },
        ]
        resolved = self.manager._folder_rows_with_tree_paths(folder_rows)
        self.store.replace_folder_snapshots(
            self.context["local_key"], list(resolved.values())
        )
        old_folder = Path(self.wpm.writing_root_path, old_path)
        new_folder = Path(self.wpm.writing_root_path, new_path)
        old_folder.mkdir(parents=True)

        changes = self.manager._apply_v2_remote_documents(
            [self._tree_remote({"<root>": ["아팯_빈폴더_팯"]}, revision=49)],
            strict=True,
            folder_rows=folder_rows,
        )

        self.assertEqual(changes, [])
        self.assertFalse(self.manager._v2_last_pull_apply_blocked)
        self.assertTrue(old_folder.is_dir())
        self.assertFalse(new_folder.exists())
        stored = self.store.get_folder_by_id(folder_id)
        self.assertEqual(stored["local_path"], old_path)

    def test_ipad_add_volume_applies_when_tree_omits_fixed_trash_root(self):
        first_document = self._live_remote(
            "메인/원고/1권/001화.txt", "1권 원고", revision=1
        )
        baseline = {
            "<root>": ["원고", "스토리 플롯", "휴지통"],
            "메인/원고": ["1권"],
            "메인/원고/1권": ["001화.txt"],
        }
        self._apply(baseline, [first_document], revision=2)
        second_document = self._live_remote(
            "메인/원고/2권/002화.txt", "2권 원고", revision=1
        )
        ipad_order = {
            "<root>": ["원고", "스토리 플롯"],
            "메인/원고": ["1권", "2권"],
            "메인/원고/1권": ["001화.txt"],
            "메인/원고/2권": ["002화.txt"],
        }
        main_id = str(uuid.uuid4())
        manuscript_id = str(uuid.uuid4())
        folder_rows = [
            {
                "folder_id": main_id, "parent_folder_id": None,
                "name": "메인", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": manuscript_id, "parent_folder_id": main_id,
                "name": "원고", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": str(uuid.uuid4()),
                "parent_folder_id": main_id,
                "name": "스토리 플롯", "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": str(uuid.uuid4()),
                "parent_folder_id": main_id,
                "name": "휴지통", "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": str(uuid.uuid4()),
                "parent_folder_id": manuscript_id,
                "name": "2권", "revision": 1, "is_deleted": False,
            },
        ]

        changes = self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(ipad_order, revision=3),
                first_document,
                second_document,
            ],
            strict=True,
            folder_rows=folder_rows,
        )

        self.assertFalse(self.manager._v2_last_pull_apply_blocked)
        self.assertTrue(changes)
        second_path = Path(
            self.wpm.writing_root_path, "메인", "원고", "2권", "002화.txt"
        )
        self.assertEqual(second_path.read_text(encoding="utf-8"), "2권 원고")
        # The legacy name must not appear alongside the canonical root.
        self.assertFalse(Path(
            self.wpm.writing_root_path, "메인", "플롯"
        ).exists())
        self.assertEqual(
            self.wpm.project_settings["tree_order"]["메인/원고"],
            ["1권", "2권"],
        )
        self.assertEqual(
            self.wpm.project_settings["tree_order"]["<root>"],
            ["원고", "스토리 플롯"],
        )

    def test_tree_ahead_folder_defers_only_tree_and_applies_confirmed_sibling(self):
        main_id = str(uuid.uuid4())
        empty_id = str(uuid.uuid4())
        filled_id = str(uuid.uuid4())
        old_empty = "메인/팯_빈폴더_윈"
        new_empty = "메인/팯_빈폴더_팯"
        old_filled = "메인/팯_든폴더_윈"
        new_filled = "메인/팯_든폴더_팯"
        old_document_path = f"{old_filled}/팯_문서_윈.txt"
        new_document_path = f"{new_filled}/팯_문서_팯.txt"
        document = self._live_remote(
            old_document_path, "보존할 원고", revision=1
        )
        old_order = {
            "<root>": ["팯_빈폴더_윈", "팯_든폴더_윈"],
            old_filled: ["팯_문서_윈.txt"],
        }
        old_rows = [
            {
                "folder_id": main_id, "parent_folder_id": None,
                "name": "메인", "revision": 1, "is_deleted": False,
            },
            {
                "folder_id": empty_id, "parent_folder_id": main_id,
                "name": "팯_빈폴더_윈", "revision": 2,
                "is_deleted": False,
            },
            {
                "folder_id": filled_id, "parent_folder_id": main_id,
                "name": "팯_든폴더_윈", "revision": 2,
                "is_deleted": False,
            },
        ]
        self.manager._apply_v2_remote_documents(
            [self._tree_remote(old_order, revision=24), document],
            strict=True,
            folder_rows=old_rows,
        )
        before_hash = hashlib.sha256(Path(
            self.wpm.writing_root_path, old_document_path
        ).read_bytes()).hexdigest()

        new_order = {
            "<root>": ["팯_빈폴더_팯", "팯_든폴더_팯"],
            new_filled: ["팯_문서_팯.txt"],
        }
        partial_rows = [
            old_rows[0],
            old_rows[1],
            {
                **old_rows[2],
                "name": "팯_든폴더_팯",
                "revision": 3,
            },
        ]
        renamed_document = {
            **document,
            "relative_path": new_document_path,
            "revision": 2,
        }

        partial_changes = self.manager._apply_v2_remote_documents(
            [self._tree_remote(new_order, revision=27), renamed_document],
            strict=True,
            folder_rows=partial_rows,
        )

        self.assertFalse(self.manager._v2_last_pull_apply_blocked)
        self.assertTrue(partial_changes)
        self.assertTrue(Path(self.wpm.writing_root_path, old_empty).is_dir())
        self.assertFalse(Path(self.wpm.writing_root_path, new_empty).exists())
        self.assertFalse(Path(self.wpm.writing_root_path, old_filled).exists())
        renamed_document_file = Path(
            self.wpm.writing_root_path, new_document_path
        )
        self.assertTrue(renamed_document_file.is_file())
        self.assertEqual(
            hashlib.sha256(renamed_document_file.read_bytes()).hexdigest(),
            before_hash,
        )
        tree_document = self.store.get_document_by_id(self.tree_document_id)
        self.assertEqual(tree_document["revision"], 24)

        complete_rows = [
            old_rows[0],
            {
                **old_rows[1],
                "name": "팯_빈폴더_팯",
                "revision": 3,
            },
            partial_rows[2],
        ]
        completed_changes = self.manager._apply_v2_remote_documents(
            [self._tree_remote(new_order, revision=27), renamed_document],
            strict=True,
            folder_rows=complete_rows,
        )

        self.assertTrue(completed_changes)
        self.assertFalse(Path(self.wpm.writing_root_path, old_empty).exists())
        self.assertTrue(Path(self.wpm.writing_root_path, new_empty).is_dir())
        self.assertEqual(
            self.store.get_document_by_id(self.tree_document_id)["revision"],
            27,
        )
        self.assertEqual(
            self.wpm.project_settings["tree_order"]["<root>"],
            ["팯_빈폴더_팯", "팯_든폴더_팯"],
        )

    def test_outbound_folder_identity_never_touches_nonempty_renamed_folder(self):
        remote_order = {"메인/메모장": ["새 폴더F"]}
        local_order = {"메인/메모장": ["새 폴더H"]}
        self._apply(remote_order, revision=12)
        operation = self._persist_local_empty_folder_rename(
            "새 폴더F", "새 폴더H", local_order
        )
        manuscript = Path(
            self.wpm.writing_root_path,
            "메인", "메모장", "새 폴더H", "보존.txt",
        )
        manuscript.write_bytes(b"never alter manuscript bytes\x00\xff")
        before_hash = hashlib.sha256(manuscript.read_bytes()).hexdigest()
        client = _FolderAwareClient([{
            "folder_id": str(uuid.uuid4()),
            "parent_folder_id": None,
            "name": "새 폴더F",
            "revision": 2,
            "is_deleted": False,
        }])

        result = self.manager._commit_outbound_folder_rename(operation, client)

        self.assertIsNone(result)
        self.assertEqual(client.calls, [])
        self.assertEqual(
            hashlib.sha256(manuscript.read_bytes()).hexdigest(), before_hash
        )

    def test_remote_folder_rename_moves_directory_once_without_old_empty_copy(self):
        old_folder = "메인/새 폴더"
        new_folder = "메인/새 폴 더"
        documents = []
        for relative_leaf, content in (
            ("첫 문서.txt", "첫 내용"),
            ("하위 폴더/둘째 문서.txt", "둘째 내용"),
        ):
            document_id = str(uuid.uuid4())
            old_path = f"{old_folder}/{relative_leaf}"
            new_path = f"{new_folder}/{relative_leaf}"
            self.assertTrue(self.wpm.write_text_file(old_path, content))
            applied = self.store.apply_remote_snapshot(
                self.context, document_id, old_path, content, 1
            )
            self.assertTrue(applied["applied"])
            documents.append({
                "document_id": document_id,
                "relative_path": new_path,
                "content": content,
                "revision": 2,
                "is_deleted": False,
            })
        old_order = {
            "<root>": ["새 폴더"],
            old_folder: ["첫 문서.txt", "하위 폴더"],
            f"{old_folder}/하위 폴더": ["둘째 문서.txt"],
        }
        self.wpm.project_settings["tree_order"] = old_order
        self.assertTrue(self.wpm.save_settings())
        new_order = {
            "<root>": ["새 폴 더"],
            new_folder: ["첫 문서.txt", "하위 폴더"],
            f"{new_folder}/하위 폴더": ["둘째 문서.txt"],
        }

        real_rename = os.rename
        with patch("sync_manager.os.rename", wraps=real_rename) as rename:
            changes = self._apply(new_order, documents, revision=2)

        root = Path(self.wpm.writing_root_path)
        self.assertFalse(Path(root, old_folder).exists())
        self.assertTrue(Path(root, new_folder).is_dir())
        self.assertEqual(
            Path(root, new_folder, "첫 문서.txt").read_text(encoding="utf-8"),
            "첫 내용",
        )
        self.assertEqual(
            Path(root, new_folder, "하위 폴더", "둘째 문서.txt").read_text(
                encoding="utf-8"
            ),
            "둘째 내용",
        )
        self.assertEqual(rename.call_count, 1)
        renamed_from, renamed_to = rename.call_args.args
        self.assertEqual(
            os.path.normpath(renamed_from), os.path.normpath(root / old_folder)
        )
        self.assertEqual(
            os.path.normpath(renamed_to), os.path.normpath(root / new_folder)
        )
        self.assertEqual(self.wpm.project_settings["tree_order"], new_order)
        self.assertEqual(
            {
                self.store.get_document_by_id(item["document_id"])["local_path"]
                for item in documents
            },
            {
                f"{new_folder}/첫 문서.txt",
                f"{new_folder}/하위 폴더/둘째 문서.txt",
            },
        )
        self.assertEqual(len(changes), 3)

    @staticmethod
    def _folder_identity_fixture(old_name, new_name, revision=2):
        main_id = str(uuid.uuid4())
        folder_id = str(uuid.uuid4())
        rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": folder_id,
                "parent_folder_id": main_id,
                "name": new_name,
                "revision": revision,
                "is_deleted": False,
            },
        ]
        versions = [
            {
                "folder_id": folder_id,
                "parent_folder_id": main_id,
                "name": old_name,
                "revision": revision - 1,
                "is_deleted": False,
                "operation_kind": "rename",
            },
            {
                "folder_id": folder_id,
                "parent_folder_id": main_id,
                "name": new_name,
                "revision": revision,
                "is_deleted": False,
                "operation_kind": "rename",
            },
        ]
        return folder_id, rows, versions

    def _seed_nonempty_folder_identity_document(self, old_folder):
        old_document = f"{old_folder}/보존.txt"
        content = "절대 변경되면 안 되는 원고 \x00 바이트"
        document_id = str(uuid.uuid4())
        self.assertTrue(self.wpm.write_text_file(old_document, content))
        applied = self.store.apply_remote_snapshot(
            self.context, document_id, old_document, content, 1
        )
        self.assertTrue(applied["applied"])
        old_order = {
            "<root>": [old_folder.removeprefix("메인/")],
            old_folder: ["보존.txt"],
        }
        self._apply_direct(old_order, revision=1, live_paths={old_document})
        return document_id, old_document, content, old_order

    def test_folder_id_renames_nonempty_directory_before_child_document_apply(self):
        old_folder = "메인/든폴더_윈"
        new_folder = "메인/든폴더_팏"
        document_id, old_document, content, _old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        new_document = f"{new_folder}/보존.txt"
        new_order = {
            "<root>": ["든폴더_팏"],
            new_folder: ["보존.txt"],
        }
        folder_id, rows, versions = self._folder_identity_fixture(
            "든폴더_윈", "든폴더_팏"
        )
        source = Path(self.wpm.writing_root_path, old_document)
        before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        remote = [
            self._tree_remote(new_order, revision=2),
            {
                "document_id": document_id,
                "relative_path": new_document,
                "content": content,
                "revision": 2,
                "is_deleted": False,
            },
        ]

        changes = self.manager._apply_v2_remote_documents(
            remote,
            strict=True,
            folder_rows=rows,
            folder_versions=versions,
        )

        self.assertFalse(Path(self.wpm.writing_root_path, old_folder).exists())
        target = Path(self.wpm.writing_root_path, new_document)
        self.assertTrue(target.is_file())
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(
            self.store.get_document_by_id(document_id)["local_path"], new_document
        )
        stored_folder = self.store.get_folder_by_id(folder_id)
        self.assertEqual(stored_folder["local_path"], new_folder)
        self.assertEqual(stored_folder["revision"], 2)
        self.assertTrue(any(
            change.get("kind") == "folder_identity_rename"
            for change in changes
        ))

    def test_folder_id_rename_projects_stale_child_parent_without_data_loss(self):
        old_folder = "메인/빠른변경_윈"
        new_folder = "메인/빠른변경_팯"
        document_id, old_document, content, _old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        new_document = f"{new_folder}/보존.txt"
        folder_id, rows, versions = self._folder_identity_fixture(
            "빠른변경_윈", "빠른변경_팯"
        )
        new_order = {
            "<root>": ["빠른변경_팯"],
            new_folder: ["보존.txt"],
        }
        source = Path(self.wpm.writing_root_path, old_document)
        before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        remote = [
            self._tree_remote(new_order, revision=2),
            {
                "document_id": document_id,
                # commit_folder is newer, but this child projection still has
                # the old parent prefix.
                "relative_path": old_document,
                "content": content,
                "revision": 1,
                "is_deleted": False,
            },
        ]
        self.manager._v2_active_paths_provider = lambda: {old_document}

        first = self.manager._apply_v2_remote_documents(
            remote,
            strict=True,
            folder_rows=rows,
            folder_versions=versions,
        )
        repeated = self.manager._apply_v2_remote_documents(
            remote,
            strict=True,
            folder_rows=rows,
            folder_versions=versions,
        )

        target = Path(self.wpm.writing_root_path, new_document)
        self.assertFalse(Path(self.wpm.writing_root_path, old_folder).exists())
        self.assertTrue(target.is_file())
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before_hash)
        stored = self.store.get_document_by_id(document_id)
        self.assertEqual(stored["local_path"], new_document)
        self.assertEqual(stored["server_path"], old_document)
        self.assertEqual(self.store.get_folder_by_id(folder_id)["local_path"], new_folder)
        self.assertTrue(any(
            change.get("kind") == "folder_identity_rename" for change in first
        ))
        self.assertEqual(repeated, [])

    def test_fast_folder_and_missing_document_rename_never_creates_txt_directory(self):
        old_folder = "메인/동시변경_윈"
        new_folder = "메인/동시변경_팯"
        document_id, old_document, content, old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        new_document = f"{new_folder}/새 문서_팯.txt"
        old_leaf_after_folder = f"{new_folder}/보존.txt"
        _folder_id, rows, versions = self._folder_identity_fixture(
            "동시변경_윈", "동시변경_팯"
        )
        remote_order = {
            "<root>": ["동시변경_팯"],
            new_folder: ["새 문서_팯.txt"],
        }
        before_hash = hashlib.sha256(
            Path(self.wpm.writing_root_path, old_document).read_bytes()
        ).hexdigest()
        remote = [
            self._tree_remote(remote_order, revision=2),
            {
                "document_id": document_id,
                # The iPad tree changed the leaf, but commit_document has not
                # reached the server projection yet.
                "relative_path": old_document,
                "content": content,
                "revision": 1,
                "is_deleted": False,
            },
        ]

        changes = self.manager._apply_v2_remote_documents(
            remote,
            strict=False,
            folder_rows=rows,
            folder_versions=versions,
        )

        preserved = Path(self.wpm.writing_root_path, old_leaf_after_folder)
        self.assertTrue(preserved.is_file())
        self.assertFalse(Path(self.wpm.writing_root_path, new_document).exists())
        self.assertEqual(
            hashlib.sha256(preserved.read_bytes()).hexdigest(), before_hash
        )
        self.assertEqual(
            self.store.get_document_by_id(document_id)["local_path"],
            old_leaf_after_folder,
        )
        self.assertEqual(
            self.wpm.project_settings["tree_order"], old_order
        )
        self.assertTrue(any(
            change.get("kind") == "folder_identity_rename" for change in changes
        ))

    def test_partial_document_snapshot_waits_for_matching_tree_order(self):
        old_folder = "메인/순서_이전"
        new_folder = "메인/순서_이후"
        document_id, old_document, content, old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        new_document = f"{new_folder}/보존.txt"
        _folder_id, rows, versions = self._folder_identity_fixture(
            "순서_이전", "순서_이후"
        )
        partial = [
            self._tree_remote(old_order, revision=1),
            {
                "document_id": document_id,
                "relative_path": new_document,
                "content": content,
                "revision": 2,
                "is_deleted": False,
            },
        ]

        changes = self.manager._apply_v2_remote_documents(
            partial,
            strict=True,
            folder_rows=rows,
            folder_versions=versions,
        )

        self.assertEqual(changes, [])
        self.assertTrue(self.manager._v2_last_pull_apply_blocked)
        self.assertTrue(Path(self.wpm.writing_root_path, old_document).is_file())
        self.assertFalse(Path(self.wpm.writing_root_path, new_folder).exists())
        self.assertEqual(
            self.store.get_document_by_id(document_id)["local_path"], old_document
        )

    def test_folder_id_repairs_only_exact_empty_source_residue(self):
        old_folder = "메인/남은_예전폴더"
        new_folder = "메인/서버_새폴더"
        document_id, old_document, content, _old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        new_document = f"{new_folder}/보존.txt"
        os.makedirs(Path(self.wpm.writing_root_path, new_folder), exist_ok=True)
        os.rename(
            Path(self.wpm.writing_root_path, old_document),
            Path(self.wpm.writing_root_path, new_document),
        )
        applied = self.store.apply_remote_snapshot(
            self.context, document_id, new_document, content, 2
        )
        self.assertTrue(applied["applied"])
        new_order = {
            "<root>": ["서버_새폴더"],
            new_folder: ["보존.txt"],
        }
        folder_id, rows, versions = self._folder_identity_fixture(
            "남은_예전폴더", "서버_새폴더"
        )
        before_hash = hashlib.sha256(
            Path(self.wpm.writing_root_path, new_document).read_bytes()
        ).hexdigest()
        self.manager._v2_active_paths_provider = lambda: {new_document}

        self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(new_order, revision=2),
                {
                    "document_id": document_id,
                    "relative_path": new_document,
                    "content": content,
                    "revision": 2,
                    "is_deleted": False,
                },
            ],
            strict=True,
            folder_rows=rows,
            folder_versions=versions,
        )

        self.assertFalse(Path(self.wpm.writing_root_path, old_folder).exists())
        target = Path(self.wpm.writing_root_path, new_document)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(
            self.store.get_folder_by_id(folder_id)["local_path"], new_folder
        )

    def test_folder_id_collision_preserves_both_nonempty_directories(self):
        old_folder = "메인/충돌_이전"
        new_folder = "메인/충돌_이후"
        document_id, old_document, content, _old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        target_collision = Path(
            self.wpm.writing_root_path, new_folder, "다른 원고.txt"
        )
        target_collision.parent.mkdir(parents=True)
        target_collision.write_bytes(b"unrelated target bytes\x00\xff")
        old_hash = hashlib.sha256(
            Path(self.wpm.writing_root_path, old_document).read_bytes()
        ).hexdigest()
        target_hash = hashlib.sha256(target_collision.read_bytes()).hexdigest()
        new_order = {
            "<root>": ["충돌_이후"],
            new_folder: ["보존.txt", "다른 원고.txt"],
        }
        _folder_id, rows, versions = self._folder_identity_fixture(
            "충돌_이전", "충돌_이후"
        )

        changes = self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(new_order, revision=2),
                {
                    "document_id": document_id,
                    "relative_path": f"{new_folder}/보존.txt",
                    "content": content,
                    "revision": 2,
                    "is_deleted": False,
                },
            ],
            strict=True,
            folder_rows=rows,
            folder_versions=versions,
        )

        self.assertEqual(changes, [])
        self.assertTrue(Path(self.wpm.writing_root_path, old_document).is_file())
        self.assertEqual(
            hashlib.sha256(
                Path(self.wpm.writing_root_path, old_document).read_bytes()
            ).hexdigest(),
            old_hash,
        )
        self.assertEqual(
            hashlib.sha256(target_collision.read_bytes()).hexdigest(), target_hash
        )

    def test_folder_id_rename_waits_for_local_document_operation(self):
        old_folder = "메인/로컬작업_이전"
        new_folder = "메인/로컬작업_이후"
        document_id, old_document, content, _old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        operation = self.store.enqueue(
            self.context,
            old_document,
            content + " 로컬 수정",
            relative_path=old_document,
        )
        new_order = {
            "<root>": ["로컬작업_이후"],
            new_folder: ["보존.txt"],
        }
        _folder_id, rows, versions = self._folder_identity_fixture(
            "로컬작업_이전", "로컬작업_이후"
        )
        before_hash = hashlib.sha256(
            Path(self.wpm.writing_root_path, old_document).read_bytes()
        ).hexdigest()

        changes = self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(new_order, revision=2),
                {
                    "document_id": document_id,
                    "relative_path": f"{new_folder}/보존.txt",
                    "content": content,
                    "revision": 2,
                    "is_deleted": False,
                },
            ],
            strict=True,
            folder_rows=rows,
            folder_versions=versions,
        )

        self.assertEqual(changes, [])
        self.assertTrue(Path(self.wpm.writing_root_path, old_document).is_file())
        self.assertFalse(Path(self.wpm.writing_root_path, new_folder).exists())
        self.assertEqual(
            hashlib.sha256(
                Path(self.wpm.writing_root_path, old_document).read_bytes()
            ).hexdigest(),
            before_hash,
        )
        self.assertEqual(
            self.store.operation(operation["operation_id"])["status"], "pending"
        )

    def test_folder_id_mapping_survives_restart_without_history_fetch(self):
        old_folder = "메인/재시작_이전"
        new_folder = "메인/재시작_이후"
        document_id, old_document, content, old_order = (
            self._seed_nonempty_folder_identity_document(old_folder)
        )
        folder_id, initial_rows, _versions = self._folder_identity_fixture(
            "재시작_이전", "재시작_이전", revision=1
        )
        self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(old_order, revision=1),
                {
                    "document_id": document_id,
                    "relative_path": old_document,
                    "content": content,
                    "revision": 1,
                    "is_deleted": False,
                },
            ],
            strict=True,
            folder_rows=initial_rows,
            folder_versions=[],
        )
        reopened = SyncV2Store(self.store.db_path)
        self.manager._v2_store = reopened
        new_document = f"{new_folder}/보존.txt"
        new_order = {
            "<root>": ["재시작_이후"],
            new_folder: ["보존.txt"],
        }
        main_id = initial_rows[0]["folder_id"]
        renamed_rows = [
            initial_rows[0],
            {
                "folder_id": folder_id,
                "parent_folder_id": main_id,
                "name": "재시작_이후",
                "revision": 2,
                "is_deleted": False,
            },
        ]

        self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(new_order, revision=2),
                {
                    "document_id": document_id,
                    "relative_path": new_document,
                    "content": content,
                    "revision": 2,
                    "is_deleted": False,
                },
            ],
            strict=True,
            folder_rows=renamed_rows,
            folder_versions=[],
        )

        self.assertFalse(Path(self.wpm.writing_root_path, old_folder).exists())
        self.assertTrue(Path(self.wpm.writing_root_path, new_document).is_file())
        self.assertEqual(
            reopened.get_folder_by_id(folder_id)["local_path"], new_folder
        )

    def test_already_applied_active_folder_does_not_block_other_empty_rename(self):
        main_id = str(uuid.uuid4())
        active_folder_id = str(uuid.uuid4())
        empty_folder_id = str(uuid.uuid4())
        active_old = "메인/든폴더_이전"
        active_new = "메인/든폴더_이후"
        active_document = f"{active_new}/열린 원고.txt"
        empty_old = "메인/빈폴더_이전"
        empty_new = "메인/빈폴더_이후"
        content = "열려 있지만 이미 옮겨진 문서"
        self.assertTrue(self.wpm.write_text_file(active_document, content))
        document_id = str(uuid.uuid4())
        applied = self.store.apply_remote_snapshot(
            self.context, document_id, active_document, content, 2
        )
        self.assertTrue(applied["applied"])
        Path(self.wpm.writing_root_path, empty_old).mkdir(parents=True)
        self.store.replace_folder_snapshots(self.context["local_key"], [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "local_path": "메인",
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": active_folder_id,
                "parent_folder_id": main_id,
                "local_path": active_old,
                "name": "든폴더_이전",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": empty_folder_id,
                "parent_folder_id": main_id,
                "local_path": empty_old,
                "name": "빈폴더_이전",
                "revision": 1,
                "is_deleted": False,
            },
        ])
        folder_rows = [
            {
                "folder_id": main_id,
                "parent_folder_id": None,
                "name": "메인",
                "revision": 1,
                "is_deleted": False,
            },
            {
                "folder_id": active_folder_id,
                "parent_folder_id": main_id,
                "name": "든폴더_이후",
                "revision": 2,
                "is_deleted": False,
            },
            {
                "folder_id": empty_folder_id,
                "parent_folder_id": main_id,
                "name": "빈폴더_이후",
                "revision": 2,
                "is_deleted": False,
            },
        ]
        tree_order = {
            "<root>": ["든폴더_이후", "빈폴더_이후"],
            active_new: ["열린 원고.txt"],
            empty_new: [],
        }
        self.manager._v2_active_paths_provider = lambda: {active_document}

        changes = self.manager._apply_v2_remote_documents(
            [
                self._tree_remote(tree_order, revision=2),
                {
                    "document_id": document_id,
                    "relative_path": active_document,
                    "content": content,
                    "revision": 2,
                    "is_deleted": False,
                },
            ],
            strict=True,
            folder_rows=folder_rows,
            folder_versions=[],
        )

        self.assertFalse(self.manager._v2_last_pull_apply_blocked)
        self.assertFalse(Path(self.wpm.writing_root_path, empty_old).exists())
        self.assertTrue(Path(self.wpm.writing_root_path, empty_new).is_dir())
        self.assertTrue(Path(self.wpm.writing_root_path, active_document).is_file())
        self.assertEqual(
            self.store.get_folder_by_id(empty_folder_id)["local_path"], empty_new
        )
        self.assertTrue(any(
            change.get("folder_id") == empty_folder_id for change in changes
        ))

    def test_existing_tree_order_baseline_repairs_folders_missing_from_old_receiver(self):
        tree_order = {"<root>": ["기존 baseline 빈 폴더"]}
        content = self.manager._tree_order_content(tree_order)
        self.wpm.project_settings["tree_order"] = tree_order
        self.assertTrue(self.wpm.save_settings())
        applied = self.store.apply_remote_snapshot(
            self.context,
            self.tree_document_id,
            TREE_ORDER_DOCUMENT_PATH,
            content,
            1,
            local_path=TREE_ORDER_DOCUMENT_PATH,
        )
        self.assertTrue(applied["applied"])

        change = self._apply_direct(tree_order)

        self.assertEqual(change["kind"], "tree_order")
        self.assertTrue(
            Path(
                self.wpm.writing_root_path, "메인", "기존 baseline 빈 폴더"
            ).is_dir()
        )

    def test_remote_tree_order_rejects_file_case_and_unicode_collisions(self):
        parent = Path(self.wpm.writing_root_path, "메인", "13-2 테스트")
        parent.mkdir(parents=True)
        Path(parent, "파일 충돌").write_text("보존", encoding="utf-8")
        Path(parent, "CaseFolder").mkdir()
        nfd_name = unicodedata.normalize("NFD", "한글폴더")
        Path(parent, nfd_name).mkdir()

        for name in ("파일 충돌", "casefolder", "한글폴더"):
            with self.subTest(name=name):
                with self.assertRaises(FileExistsError):
                    self._apply_direct({
                        "<root>": ["13-2 테스트"],
                        "메인/13-2 테스트": [name],
                    })
        self.assertEqual(Path(parent, "파일 충돌").read_text(encoding="utf-8"), "보존")
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_rejects_unsafe_paths_reserved_names_and_duplicates(self):
        invalid_orders = (
            {"C:/탈출": ["폴더"]},
            {"메인/../탈출": ["폴더"]},
            {"<root>": ["CON"]},
            {"<root>": ["중복", "중복"]},
            {"<root>": ["Case", "case"]},
            {"<root>": ["끝점."]},
            {"<root>": ["__antigravity__"]},
            {"<root>": ["C:\\절대경로"]},
            {"<root>": ["가" * 256]},
            {"메인/Case": [], "메인/case": []},
        )

        for tree_order in invalid_orders:
            with self.subTest(tree_order=tree_order):
                with self.assertRaises((ValueError, FileExistsError)):
                    self._apply_direct(tree_order)
        invalid_type_content = json.dumps({
            "version": 1,
            "tree_order": {"<root>": [123]},
        })
        with self.assertRaises(ValueError):
            self.manager._apply_remote_tree_order_document(
                self.tree_document_id, invalid_type_content, 1
            )
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_never_follows_symbolic_link(self):
        outside = Path(self.temp.name, "outside")
        outside.mkdir()
        link = Path(self.wpm.writing_root_path, "메인", "연결 폴더")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symbolic link 생성 권한 없음: {error}")

        with self.assertRaises(FileExistsError):
            self._apply_direct({"<root>": ["연결 폴더"]})
        self.assertEqual(list(outside.iterdir()), [])
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_rejects_existing_reparse_point(self):
        candidate = Path(self.wpm.writing_root_path, "메인", "연결 폴더")
        candidate.mkdir()
        real_is_reparse = self.manager._is_reparse_path

        with patch.object(
            SyncManager,
            "_is_reparse_path",
            side_effect=lambda path: (
                os.path.abspath(path) == os.path.abspath(candidate)
                or real_is_reparse(path)
            ),
        ):
            with self.assertRaises(FileExistsError):
                self._apply_direct({"<root>": ["연결 폴더"]})
        self.assertTrue(candidate.is_dir())
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_preserves_trash_and_never_creates_remote_trash_folders(self):
        self.wpm.project_settings["tree_order"] = {
            "메인/휴지통": ["로컬 보관본.txt"],
            "메인/휴지통/로컬 폴더": ["로컬 하위.txt"],
        }
        tree_order = {
            "<root>": ["새 빈 폴더"],
            "메인/휴지통": ["원격 폴더"],
            "메인/휴지통/원격 폴더": ["하위 원격 폴더"],
        }

        self._apply(tree_order)

        trash_order = self.wpm.project_settings["tree_order"]
        self.assertEqual(trash_order["메인/휴지통"], ["로컬 보관본.txt"])
        self.assertEqual(
            trash_order["메인/휴지통/로컬 폴더"], ["로컬 하위.txt"]
        )
        self.assertFalse(
            Path(self.wpm.writing_root_path, "메인", "휴지통", "원격 폴더").exists()
        )

    def test_remote_tree_order_rolls_back_new_folders_when_creation_fails(self):
        tree_order = {
            "<root>": ["13-2 테스트"],
            "메인/13-2 테스트": ["첫 폴더"],
            "메인/13-2 테스트/첫 폴더": ["둘째 폴더"],
        }
        real_mkdir = os.mkdir

        def fail_second_folder(path, *args, **kwargs):
            if str(path).endswith("둘째 폴더"):
                raise OSError("의도한 생성 실패")
            return real_mkdir(path, *args, **kwargs)

        with patch("sync_manager.os.mkdir", side_effect=fail_second_folder):
            with self.assertRaises(OSError):
                self._apply_direct(tree_order)

        self.assertFalse(
            Path(self.wpm.writing_root_path, "메인", "13-2 테스트").exists()
        )
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_settings_failure_does_not_advance_baseline(self):
        tree_order = {"<root>": ["저장 실패 폴더"]}

        with patch.object(self.wpm, "save_settings", return_value=False):
            with self.assertRaises(OSError):
                self._apply_direct(tree_order)

        self.assertFalse(Path(self.wpm.writing_root_path, "메인", "저장 실패 폴더").exists())
        self.assertNotIn("tree_order", self.wpm.project_settings)
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_cas_failure_rolls_back_only_new_empty_directories(self):
        existing = Path(self.wpm.writing_root_path, "메인", "기존 폴더")
        existing.mkdir()
        marker = Path(existing, "보존.txt")
        marker.write_text("보존", encoding="utf-8")
        tree_order = {
            "<root>": ["기존 폴더", "새 폴더"],
            "메인/새 폴더": ["새 하위 폴더"],
        }

        with patch.object(
            self.store,
            "apply_remote_snapshot",
            return_value={"applied": False, "reason": "not_newer"},
        ):
            with self.assertRaises(RuntimeError):
                self._apply_direct(tree_order)

        self.assertTrue(existing.is_dir())
        self.assertEqual(marker.read_text(encoding="utf-8"), "보존")
        self.assertFalse(Path(self.wpm.writing_root_path, "메인", "새 폴더").exists())
        self.assertNotIn("tree_order", self.wpm.project_settings)
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

        change = self._apply_direct(tree_order)
        self.assertEqual(change["kind"], "tree_order")
        self.assertTrue(
            Path(self.wpm.writing_root_path, "메인", "새 폴더", "새 하위 폴더").is_dir()
        )

    def test_remote_tree_order_rollback_keeps_folder_that_became_nonempty(self):
        tree_order = {
            "<root>": ["새 폴더"],
            "메인/새 폴더": ["새 하위 폴더"],
        }
        deepest = Path(
            self.wpm.writing_root_path, "메인", "새 폴더", "새 하위 폴더"
        )

        def fail_after_external_write(*_args, **_kwargs):
            Path(deepest, "동시 생성.txt").write_text("보존", encoding="utf-8")
            return {"applied": False, "reason": "active_operations"}

        with patch.object(
            self.store, "apply_remote_snapshot", side_effect=fail_after_external_write
        ):
            with self.assertRaises(RuntimeError):
                self._apply_direct(tree_order)

        self.assertEqual(
            Path(deepest, "동시 생성.txt").read_text(encoding="utf-8"), "보존"
        )
        self.assertIsNone(self.store.get_document_by_id(self.tree_document_id))

    def test_remote_tree_order_notification_refreshes_once_without_outbound_echo(self):
        panel = SimpleNamespace(
            _schedule_remote_tree_refresh=MagicMock(),
            _refresh_storage_status_for_editor_state=MagicMock(),
            save_tree_order=MagicMock(),
        )

        WritingModeWidget.on_remote_documents_applied(
            panel, [{"kind": "tree_order"}]
        )

        panel._schedule_remote_tree_refresh.assert_called_once_with()
        panel.save_tree_order.assert_not_called()


class WritingManualSaveTestCase(unittest.TestCase):
    def test_scheduled_remote_refresh_does_not_clear_a_new_inline_editor(self):
        panel = WritingTreeMixin()
        panel.binder_tree = MagicMock()
        panel.load_tree_data = MagicMock()
        panel._remote_tree_refresh_pending = False
        panel._remote_tree_refresh_scheduled = False
        panel._tree_item_creation_active = False
        callbacks = []

        with patch(
            "writing_tree.QTimer.singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            panel._schedule_remote_tree_refresh()
            panel._tree_item_creation_active = True
            callbacks[0]()

        panel.load_tree_data.assert_not_called()
        self.assertTrue(panel._remote_tree_refresh_pending)

        panel._tree_item_creation_active = False
        panel.binder_tree.state.return_value = 0
        panel._flush_remote_tree_refresh()

        panel.load_tree_data.assert_called_once_with()
        self.assertFalse(panel._remote_tree_refresh_pending)

    def test_rapid_creation_closes_and_queues_the_item_that_owned_the_editor(self):
        first_item = MagicMock()
        first_item.data.side_effect = lambda _column, role: {
            Qt.ItemDataRole.UserRole: "메인/메모장/새_문서.txt",
            Qt.ItemDataRole.UserRole + 1: False,
            Qt.ItemDataRole.UserRole + 4: True,
        }.get(role)
        last_item = MagicMock()
        panel = SimpleNamespace(
            _tree_creation_item=first_item,
            binder_tree=MagicMock(),
            sync_manager=MagicMock(),
            _finish_tree_item_creation=MagicMock(),
            save_tree_order=MagicMock(),
        )
        panel._commit_tree_item_creation = lambda item: (
            WritingTreeMixin._commit_tree_item_creation(panel, item)
        )
        panel.binder_tree.currentItem.return_value = last_item
        callbacks = []

        with patch(
            "writing_tree.QTimer.singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            WritingTreeMixin.on_tree_editor_closed(panel)
            panel._tree_creation_item = last_item
            callbacks[0]()

        panel.sync_manager.record_path_change.assert_called_once_with(
            "메인/메모장/새_문서.txt", "메인/메모장/새_문서.txt"
        )
        panel._finish_tree_item_creation.assert_called_once_with(first_item)
        panel.save_tree_order.assert_called_once_with()

    def test_ctrl_s_queues_open_document_without_undefined_worker(self):
        editor = MagicMock()
        editor.toPlainText.return_value = "기준본"
        label = MagicMock()
        label.text.return_value = "충돌테스트.txt"
        panel = SimpleNamespace(
            current_loaded_file_left="메인/원고/충돌테스트.txt",
            current_loaded_file_right=None,
            left_editor=editor,
            is_dirty_left=True,
            is_dirty_right=False,
            wpm=MagicMock(),
            sync_manager=MagicMock(),
            pm=SimpleNamespace(current_project="V2 테스트"),
            on_sync_finished=MagicMock(),
            lbl_current_doc=label,
        )
        panel.wpm.write_text_file.return_value = True

        with patch("PyQt6.QtCore.QTimer.singleShot"):
            result = WritingModeWidget.manual_save(panel)

        self.assertIsNone(result)
        panel.wpm.write_text_file.assert_called_once_with(
            "메인/원고/충돌테스트.txt", "기준본"
        )
        panel.sync_manager.upload_content_async.assert_called_once()

    def test_ctrl_s_on_trash_copy_never_creates_cloud_document(self):
        editor = MagicMock()
        editor.toPlainText.return_value = "휴지통 보관본"
        panel = SimpleNamespace(
            current_loaded_file_left="메인/휴지통/삭제된문서.txt",
            current_loaded_file_right=None,
            left_editor=editor,
            is_dirty_left=False,
            is_dirty_right=False,
            wpm=MagicMock(),
            sync_manager=MagicMock(),
            pm=SimpleNamespace(current_project="V2 테스트"),
            on_sync_finished=MagicMock(),
            lbl_current_doc=MagicMock(),
        )

        WritingModeWidget.manual_save(panel)

        panel.wpm.write_text_file.assert_not_called()
        panel.sync_manager.upload_content_async.assert_not_called()

    def test_temporary_new_item_is_not_opened_before_name_is_confirmed(self):
        item = MagicMock()
        item.data.side_effect = lambda _column, role: (
            True if role == Qt.ItemDataRole.UserRole + 4 else "메인/메모장/새_문서.txt"
        )
        panel = SimpleNamespace(_open_file_by_path=MagicMock())

        WritingTreeMixin.on_tree_current_item_changed(panel, item, None)

        panel._open_file_by_path.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
