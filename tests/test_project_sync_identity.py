"""Synthetic projects and temporary databases; no live app or server access."""
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from project_creation_v1 import create_project, writing_root
from project_identity_v1 import IdentityError, identity_path, read_identity
from sync_v2_store import SyncV2Store


class ProjectSyncIdentityTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        create_project(str(self.workspace), '새 작품')
        self.project = self.workspace / '새 작품'
        self.writing = writing_root(str(self.project))
        self.identity_file = Path(identity_path(str(self.project)))
        self.original = self.identity_file.read_bytes()
        self.project_id = read_identity(str(self.project))['project']['uuid']
        self.store = SyncV2Store(str(self.base / 'sync.sqlite3'))

    def test_first_registration_uses_created_project_uuid_and_preserves_identity(self):
        context = self.store.configure_project(self.writing, '새 작품')
        self.assertEqual(context['project_id'], self.project_id)
        self.assertEqual(self.identity_file.read_bytes(), self.original)
        self.assertEqual(self.store.get_project(context['local_key'])['contract_path_enabled'], 0)

    def test_reopen_and_local_enqueue_keep_the_same_project_uuid(self):
        context = self.store.configure_project(self.writing, '새 작품')
        reopened = SyncV2Store(self.store.db_path)
        context = reopened.configure_project(self.writing, '이름 변경')
        operation = reopened.enqueue(context, '__antigravity__/identity-test.txt', '합성 내용')
        self.assertEqual(context['project_id'], self.project_id)
        self.assertEqual(operation['project_id'], self.project_id)
        self.assertEqual(self.identity_file.read_bytes(), self.original)

    def test_conflicting_explicit_id_cannot_create_a_new_binding(self):
        with self.assertRaisesRegex(ValueError, 'UUID'):
            self.store.configure_project(self.writing, '새 작품', str(uuid.uuid4()))
        self.assertIsNone(self.store.get_project(self.store.local_key_for(self.writing)))
        self.assertEqual(self.identity_file.read_bytes(), self.original)

    def test_matching_explicit_id_is_accepted(self):
        context = self.store.configure_project(self.writing, '새 작품', self.project_id)
        self.assertEqual(context['project_id'], self.project_id)

    def test_server_registration_payload_keeps_the_created_uuid(self):
        from sync_manager import SyncManager
        context = self.store.configure_project(self.writing, '새 작품')
        manager = SimpleNamespace(_v2_context=context, _response_data=lambda response: response.data)
        client = Mock()
        client.rpc.return_value.execute.return_value = SimpleNamespace(data=[])
        SyncManager._ensure_remote_project(manager, client)
        client.rpc.assert_called_once_with('ensure_project', {
            'p_project_id': self.project_id, 'p_name': '새 작품',
        })
        self.assertEqual(self.identity_file.read_bytes(), self.original)

    def test_legacy_root_without_identity_still_gets_a_stable_id(self):
        plain = self.base / 'old' / '집필모드'
        plain.mkdir(parents=True)
        context = self.store.configure_project(str(plain), '구형 작품')
        self.assertEqual(str(uuid.UUID(context['project_id'])), context['project_id'])
        self.assertEqual(self.store.configure_project(str(plain), '구형 작품')['project_id'], context['project_id'])

    def test_invalid_identity_does_not_silently_create_another_uuid(self):
        self.identity_file.write_text('{broken', encoding='utf-8')
        with self.assertRaises(IdentityError):
            self.store.configure_project(self.writing, '새 작품')
        self.assertIsNone(self.store.get_project(self.store.local_key_for(self.writing)))
        self.assertEqual(self.identity_file.read_text(encoding='utf-8'), '{broken')

    def test_unreadable_identity_does_not_fall_back_to_a_new_uuid(self):
        with patch('project_identity_v1.read_identity', side_effect=PermissionError('synthetic access denial')):
            with self.assertRaises(PermissionError):
                self.store.configure_project(self.writing, '새 작품')
        self.assertIsNone(self.store.get_project(self.store.local_key_for(self.writing)))

    def test_existing_binding_and_queued_operation_are_never_rekeyed(self):
        # Model a pre-fix project: DB registration existed before identity.
        self.identity_file.unlink()
        historical_id = str(uuid.uuid4())
        context = self.store.configure_project(self.writing, '기존 작품', historical_id)
        operation = self.store.enqueue(context, '__antigravity__/identity-test.txt', '기존 합성 내용')
        self.identity_file.write_bytes(self.original)
        operation_before = self.store.operation(operation['operation_id'])
        restored = self.store.configure_project(self.writing, '기존 작품')
        self.assertEqual(restored['project_id'], historical_id)
        self.assertEqual(self.store.operation(operation['operation_id']), operation_before)
        self.assertEqual(self.identity_file.read_bytes(), self.original)
        with self.assertRaises(ValueError):
            self.store.configure_project(self.writing, '기존 작품', self.project_id)

    def test_same_name_in_separate_workspaces_remains_two_projects(self):
        other_workspace = self.base / 'other'
        other_workspace.mkdir()
        identity = create_project(str(other_workspace), '새 작품')
        first = self.store.configure_project(self.writing, '새 작품')
        second = self.store.configure_project(writing_root(str(other_workspace / '새 작품')), '새 작품')
        self.assertEqual(first['project_id'], self.project_id)
        self.assertEqual(second['project_id'], identity['project']['uuid'])
        self.assertNotEqual(first['project_id'], second['project_id'])

    def test_copy_with_same_uuid_cannot_create_a_second_binding(self):
        first = self.store.configure_project(self.writing, '새 작품')
        duplicate = self.base / 'duplicate'
        shutil.copytree(self.project, duplicate)
        with self.assertRaisesRegex(ValueError, 'UUID'):
            self.store.configure_project(writing_root(str(duplicate)), '사본')
        self.assertEqual(self.store.get_project_by_id(first['project_id'])['local_key'], first['local_key'])
        self.assertIsNone(self.store.get_project(self.store.local_key_for(writing_root(str(duplicate)))))


if __name__ == '__main__':
    unittest.main()
