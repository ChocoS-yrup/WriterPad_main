"""Isolated reverse review: synthetic IDs, disk and SQLite; no real account/RPC."""
import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import contract_preparation as preparation
from sync_contract import CLIENT_BUILD_ID, SyncContractError, json_sha256
from sync_v2_store import SyncV2Store, STAGE8_USER_VERSION
from tests import test_contract_followup as base
from tests import test_sync_contract_stage8 as fixtures


class ContractPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.ContractFollowupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.manager, self.store = self.fixture.manager, self.fixture.store
        self.key = self.manager._v2_context['local_key']
        self.root = Path(self.manager._v2_wpm.writing_root_path)
        self.ids = {name: str(uuid.uuid4()) for name in ('PARENT', 'FIRST', 'SECOND', 'ORDER')}
        self.ids['PROJECT'] = fixtures.PROJECT_ID
        patched = patch.multiple(preparation, **self.ids)
        patched.start()
        self.addCleanup(patched.stop)
        main = str(uuid.uuid4())
        folders = [(main, None, '메인'), (self.ids['PARENT'], main, '메인/원고'),
                   (self.ids['FIRST'], self.ids['PARENT'], '메인/원고/1권'),
                   (self.ids['SECOND'], self.ids['PARENT'], '메인/원고/2권')]
        folders += [(str(uuid.uuid4()), main, '메인/' + name) for name in
                    ('캐릭터', '설정집', '메모장', '스토리 플롯', '흐름정리', '복선', '장소', '휴지통')]
        nodes = []
        siblings = {}
        with self.store._transaction() as c:
            for fid, parent, path in folders:
                (self.root/path).mkdir(parents=True, exist_ok=True)
                c.execute('INSERT INTO sync_folders '
                          '(folder_id,local_key,parent_folder_id,local_path,name,revision,is_deleted,created_at,updated_at) '
                          'VALUES (?,?,?,?,?,1,0,?,?)', (fid,self.key,parent,path,path.split('/')[-1],'now','now'))
                order = siblings.get(parent, 0); siblings[parent] = order + 1
                nodes.append({'uuid':fid,'kind':'folder','parent_uuid':parent,'legacy_path':path,
                              'path':path,'title':path.split('/')[-1],'order':order})
            for i in range(26):
                path = f'메인/원고/1권/{i:03}화.txt' if i else '__antigravity__/tree-order.json'
                did = str(uuid.uuid4())
                c.execute('INSERT INTO sync_documents '
                          '(document_id,local_key,local_path,server_path,revision,created_at,updated_at) '
                          'VALUES (?,?,?,?,7,?,?)', (did,self.key,path,path,'now','now'))
                if i:
                    (self.root/path).write_text('synthetic manuscript '+str(i),encoding='utf-8')
                    nodes.append({'uuid':did,'kind':'document','parent_uuid':self.ids['FIRST'],
                                  'legacy_path':path,'path':path,'title':str(i),'order':i-1})
        self.store.replace_tree_order_snapshots(self.key,[{
            'tree_order_id':self.ids['ORDER'],'parent_folder_id':self.ids['PARENT'],
            'children':[self.ids['FIRST'],self.ids['SECOND']],'revision':1,
        }])
        identity_dir=self.root.parent/'.writerpad'; identity_dir.mkdir()
        (identity_dir/'identity-v1.json').write_text(json.dumps({
            'format_version':1,'project':{'uuid':fixtures.PROJECT_ID},'nodes':nodes,
        }),encoding='utf-8')
        (self.root/'설정.json').write_text(json.dumps({'tree_order':{'메인/원고':['1권','2권']}}),encoding='utf-8')
        self.manager.perform_contract_handshake()
        self.fixture.allow_structure()
        self.fixture.client.calls.clear()

    def prepare(self):
        return self.manager.prepare_reverse_contract_review()

    def snapshot(self):
        with self.store._reader() as c:
            names=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'sync_contract_preparations'")]
            rows={name:[tuple(r) for r in c.execute('SELECT * FROM '+name)] for name in names}
        disk={str(p.relative_to(self.root.parent)):p.read_bytes() for p in self.root.parent.rglob('*')
              if p.is_file() and p.suffix not in ('.sqlite3',) and not p.name.startswith('sync.sqlite3')}
        return rows,disk

    def test_prepare_reopen_export_preserves_request_and_changes_only_preparations(self):
        before=self.snapshot()
        envelope=self.prepare()
        self.assertEqual(self.snapshot(),before)
        request=envelope['request']; intents=request['ordered_intents']
        self.assertEqual([(i['entity_kind'],i['intent_kind'],i['base_revision']) for i in intents],
                         [('folder','create',0),('tree_order','reorder',1)])
        self.assertEqual(intents[1]['entity_id'],self.ids['ORDER'])
        self.assertEqual(intents[1]['payload']['children'],[self.ids['FIRST'],self.ids['SECOND'],intents[0]['entity_id']])
        self.assertEqual(request['batch']['writer_device_id'],self.manager._v2_device_id)
        self.assertEqual(request['batch']['client_build_id'],CLIENT_BUILD_ID)
        self.assertEqual(json_sha256(request),envelope['request_sha256'])
        self.assertEqual(self.prepare(),envelope)
        reopened=SyncV2Store(self.store.db_path)
        self.manager._v2_store=reopened
        # Export does not need a new handshake and never creates another request.
        destination=self.root.parent.parent/(self.root.parent.name+'-review.json')
        self.addCleanup(lambda: destination.unlink(missing_ok=True))
        self.assertEqual(self.manager.export_reverse_contract_review(str(destination)),envelope)
        first=destination.read_bytes()
        self.manager.export_reverse_contract_review(str(destination))
        self.assertEqual(destination.read_bytes(),first)
        self.assertEqual(json.loads(first),envelope)
        self.assertEqual(self.fixture.client.calls,[])
        self.assertFalse(self.manager.contract_path_enabled())
        self.manager.perform_contract_handshake()
        self.fixture.allow_structure()
        self.assertEqual(self.prepare(),envelope)

    def test_upgrade_from_schema_without_preparations_preserves_all_existing_rows(self):
        before=self.snapshot()
        with self.store._transaction() as c:
            c.execute('DROP TRIGGER sync_contract_preparations_no_dispatch')
            c.execute('DROP TABLE sync_contract_preparations')
            c.execute('PRAGMA user_version=8008')
        self.store=SyncV2Store(self.store.db_path)
        self.manager._v2_store=self.store
        self.assertEqual(self.snapshot(),before)
        self.assertIsNone(self.store.reverse_contract_preparation(self.key))

    def test_context_change_during_local_read_prevents_save(self):
        from project_identity_v1 import read_identity
        def changed(root):
            result=read_identity(root)
            self.manager._forget_contract_handshake()
            return result
        with patch('project_identity_v1.read_identity',side_effect=changed):
            with self.assertRaises(SyncContractError):self.prepare()
        self.assertIsNone(self.store.reverse_contract_preparation(self.key))

    def test_prepare_ui_uses_runtime_manager_and_export_does_not_prepare(self):
        from settings_panel import SettingsPanel
        target=SimpleNamespace(lbl_contract_review=Mock(),_contract_review_error=SettingsPanel._contract_review_error)
        with patch('sync_manager.SyncManager',return_value=self.manager):
            result=SettingsPanel.prepare_contract_review(target)
        self.assertIsNotNone(result)
        self.assertIn(result['request_sha256'],target.lbl_contract_review.setText.call_args.args[0])

    def test_dispatcher_excludes_preparation_even_if_gate_is_later_opened_in_fixture(self):
        self.prepare()
        self.assertIsNone(self.store.next_ready_structure_batch(self.key))
        self.assertIsNone(self.store.next_ready_operation(self.key))
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.store.set_contract_path_enabled(self.key,True)  # synthetic DB only
        self.fixture.allow_structure()
        with patch.object(self.manager,'_launch_contract_structure_batch') as structure, patch.object(
            self.manager,'_launch_v2_operation') as document, patch.object(self.manager,'_maybe_start_deferred_pull',return_value=False):
            self.manager.retry_pending_syncs()
            self.manager.retry_pending_syncs(manual=True)
        structure.assert_not_called(); document.assert_not_called()
        self.assertEqual(self.fixture.client.calls,[])

    def test_prepared_id_cannot_be_inserted_into_send_queue(self):
        envelope=self.prepare(); request=envelope['request']
        self.store.set_contract_path_enabled(self.key,True)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'REVIEW_ONLY'):
            self.store.create_structure_batch(self.manager._v2_context,self.manager._v2_device_id,
                                             request['ordered_intents'],batch_id=request['batch']['batch_id'])
        self.assertIsNone(self.store.next_ready_structure_batch(self.key))
        self.assertEqual(self.store.counts(self.key)['total'],0)

    def test_immutable_and_reopen_schema_migration(self):
        envelope=self.prepare()
        for sql in ('UPDATE sync_contract_preparations SET envelope_json=\'{}\'', 'DELETE FROM sync_contract_preparations'):
            with self.assertRaisesRegex(sqlite3.IntegrityError,'IMMUTABLE'):
                with self.store._transaction() as c:c.execute(sql)
        with self.store._transaction() as c:c.execute('PRAGMA user_version=8008')
        reopened=SyncV2Store(self.store.db_path)
        self.assertEqual(reopened.reverse_contract_preparation(self.key),envelope)
        with reopened._reader() as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],STAGE8_USER_VERSION)

    def test_changed_order_rejected_without_reissuing_but_original_export_available(self):
        original=self.prepare()
        with self.store._transaction() as c:c.execute('UPDATE sync_tree_orders SET revision=2')
        with self.assertRaises(SyncContractError):self.prepare()
        self.assertEqual(self.manager.reverse_contract_review(),original)

    def test_resolved_queue_activity_changes_fingerprint_and_preserves_original(self):
        original=self.prepare()
        operation=self.store.enqueue(self.manager._v2_context,'synthetic.txt','synthetic')
        with self.assertRaises(SyncContractError):self.prepare()
        self.store.cancel_operation(operation['operation_id'],str(uuid.uuid4()))
        with self.assertRaises(SyncContractError):self.prepare()
        self.assertEqual(self.manager.reverse_contract_review(),original)

    def test_new_disk_folder_or_nonempty_second_prevents_preparation(self):
        for path in ('메인/원고/3권','메인/원고/2권/unexpected.txt'):
            p=self.root/path;p.touch()
            with self.assertRaises(SyncContractError):self.prepare()
            self.assertIsNone(self.store.reverse_contract_preparation(self.key))
            p.unlink()

    def test_gate_handshake_account_and_project_fail_closed(self):
        with patch.object(self.manager,'contract_handshake_is_fresh',return_value=False):
            with self.assertRaises(SyncContractError):self.prepare()
        self.store.set_contract_path_enabled(self.key,True)
        with self.assertRaises(SyncContractError):self.prepare()
        self.store.set_contract_path_enabled(self.key,False)
        with patch.object(self.manager,'_contract_identity',return_value=''):
            with self.assertRaises(SyncContractError):self.prepare()
        with patch.dict(self.manager._v2_context,project_id=fixtures.OTHER_PROJECT_ID):
            with self.assertRaises(SyncContractError):self.prepare()
        self.assertIsNone(self.store.reverse_contract_preparation(self.key))

    def test_account_switch_cannot_export_other_accounts_preparation(self):
        self.prepare()
        with patch.object(self.manager,'_contract_identity',return_value='different-subject-marker'):
            with self.assertRaises(SyncContractError):self.manager.reverse_contract_review()

    def test_export_failure_or_cancel_does_not_modify_preparation(self):
        envelope=self.prepare()
        with self.assertRaises(SyncContractError):
            self.manager.export_reverse_contract_review(str(self.root/'설정.json'))
        with self.assertRaises(OSError):
            self.manager.export_reverse_contract_review(str(self.root.parent.parent/('missing-'+str(uuid.uuid4()))/'export.json'))
        self.assertEqual(self.manager.reverse_contract_review(),envelope)
        from settings_panel import SettingsPanel
        target=SimpleNamespace(lbl_contract_review=Mock())
        with patch('sync_manager.SyncManager',return_value=self.manager), patch('settings_panel.QFileDialog.getSaveFileName',return_value=('','')):
            self.assertIsNone(SettingsPanel.export_contract_review(target))
        self.assertEqual(self.manager.reverse_contract_review(),envelope)


if __name__=='__main__':
    unittest.main()
