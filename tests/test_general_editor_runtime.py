"""Real editor, save/controller, file/store, SDK and bounded HTTP; synthetic only."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from supabase import create_client, ClientOptions
from tests.qt_app import APP
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

import test_general_validation_runtime as prior
import general_editor_plan as plan
from body_validation_transport import ForegroundTicket
from general_editor_service import EditorService, available_action, journal_path
from general_editor_ui import EditorWindow
from general_editor_boundary import EditorRequest
from general_validation_boundary import GeneralValidationDenied, FrozenRequest
from general_validation_transport import GeneralTransport
from general_validation_control import restore_pending_controls, scope_path
from general_validation_service import view_orders
from project_creation_v1 import create_item


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.seed = prior.RuntimeTests()
        self.seed.setUp()
        self.addCleanup(self.seed.tearDown)
        self.store,self.root,self.server = self.seed.store,self.seed.writing,self.seed.server
        self.key = self.seed.key
        self.journals = self.seed.root/'editor'
        self.journals.mkdir()
        ids = iter((plan.DOCUMENT_ID,'6d3c273c-2515-4a09-bd3f-f42e5711c357'))
        create_item(str(self.root.parent),plan.PARENT_ID,plan.NAME.removesuffix('.txt'),False,uuid_factory=lambda:next(ids))
        (self.root/plan.PATH).write_bytes(plan.WINDOWS.encode())
        self.store.ensure_document(self.key,plan.PATH,'',plan.DOCUMENT_ID)
        self.store.apply_remote_snapshot(self.seed.context,plan.DOCUMENT_ID,plan.PATH,plan.WINDOWS,3,
            local_path=plan.PATH,parent_folder_id=plan.PARENT_ID,name=plan.NAME,structure_revision=1)
        self.server.rows['documents'].append({'project_id':plan.PROJECT_ID,'document_id':plan.DOCUMENT_ID,
            'relative_path':plan.PATH,'content':plan.WINDOWS,'revision':3,'parent_folder_id':plan.PARENT_ID,
            'name':plan.NAME,'structure_revision':1,'is_deleted':False,'deleted_at':None})
        next(r for r in self.server.rows['tree_orders'] if r['tree_order_id']==plan.ORDER_ID).update(children=[plan.DOCUMENT_ID],revision=2)
        self.store.replace_tree_order_snapshots(self.key,self.server.rows['tree_orders'])
        (self.root/'설정.json').write_text(json.dumps({'tree_order':view_orders(True)}),'utf-8')
        self.store.set_contract_path_enabled(self.key,False)
        self.project = self.store.get_project(self.key)
        self.hold = self.seed.hold.read_bytes()
        self.current = True
        self.windows = []
        self.addCleanup(self.close_windows)

    def close_windows(self):
        for window in self.windows:window.close()
        APP.processEvents()

    def service(self,ticket=None):
        ticket = ticket or ForegroundTicket()
        transport = GeneralTransport(ticket,inner=httpx.MockTransport(self.server))
        transport.bind_verified()
        http = httpx.Client(transport=transport,follow_redirects=False,trust_env=False)
        self.seed.clients.append(http)
        client = create_client(plan.STAGING_URL,'synthetic-key',options=ClientOptions(httpx_client=http,
            auto_refresh_token=False,headers={'Authorization':'Bearer '+prior.TOKEN}))
        return EditorService(store=self.store,wpm=self.seed.wpm,client=client,transport=transport,ticket=ticket,
            journal_dir=self.journals,device_id=prior.DEVICE,account_id=prior.ACCOUNT,access_token=prior.TOKEN,
            session_current=lambda:self.current)

    def window(self):
        window = EditorWindow(self.journals,factory=self.service,closer=lambda service:None,is_active=lambda:True)
        self.windows.append(window)
        return window

    def prepare_window(self,stage='manual'):
        service = self.service()
        window = self.window()
        window.attach_editor(service,stage,service.prepare(stage))
        return window,service

    def saved_manual(self):
        window,_ = self.prepare_window()
        window.editor.setPlainText(plan.MANUAL)
        window.start('manual_save')
        self.assertEqual(available_action(self.journals),'manual_send',window.log.toPlainText())
        return window

    def remote_ipad(self):
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=5,content=plan.IPAD)

    def assert_preserved(self):
        self.assertEqual(self.store.get_project(self.key),self.project)
        self.assertEqual(self.seed.hold.read_bytes(),self.hold)
        self.assertEqual(self.store.operation(self.seed.other_op['operation_id']),self.seed.other_op)

    def test_real_manual_and_idle_editor_paths_roundtrip_with_closed_send_gate(self):
        self.saved_manual()
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.MANUAL.encode())
        self.assertEqual(self.store.next_ready_operation(self.key)['provenance_kind'],'CONTRACT_BATCH')
        self.assert_preserved()
        def wire(name):
            if name=='document_commit':self.assert_preserved()
        self.server.on_request = wire
        self.assertEqual(self.service().send('manual')['revision'],4)
        self.remote_ipad()
        window,_ = self.prepare_window('auto')
        window.editor.setPlainText(plan.AUTO)
        QTest.qWait(1050)  # Product 800 ms debounce, real Qt event loop.
        self.assertEqual(available_action(self.journals),'auto_send',window.log.toPlainText())
        self.assertEqual(self.service().send('auto')['revision'],6)
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.AUTO.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertEqual(available_action(self.journals),'done')
        self.assert_preserved()
        posts = [name for method,name in self.server.calls if method=='POST']
        self.assertEqual(posts.count('document_commit'),2)
        self.assertNotIn('atomic_structure_commit',posts)
        self.assertFalse(any('lease' in name for name in posts))

    def test_save_has_zero_data_requests_and_new_grant_can_send_later(self):
        window,service = self.prepare_window()
        calls = list(self.server.calls)
        window.editor.setPlainText(plan.MANUAL)
        window.start('manual_save')
        self.assertEqual(self.server.calls,calls)
        service.ticket.cancel()
        self.assertEqual(self.service().send('manual')['revision'],4)

    def test_window_buttons_run_workers_and_create_fresh_services(self):
        window = self.window()
        window.start('manual_prepare')
        for _ in range(200):
            QTest.qWait(10)
            if window.worker is None:break
        self.assertIsNotNone(window.service,window.log.toPlainText())
        window.editor.setPlainText(plan.MANUAL)
        window.start('manual_save')
        window.start('manual_send')
        window.start('manual_send')  # A second UI event cannot launch another worker.
        for _ in range(200):
            QTest.qWait(10)
            if window.worker is None:break
        self.assertEqual(available_action(self.journals),'auto_prepare',window.log.toPlainText())
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)

    def test_startup_is_inert_and_runtime_flag_remains_disabled(self):
        from unittest.mock import Mock
        import general_editor_build
        factory = Mock(side_effect=AssertionError('startup must not authenticate'))
        window = EditorWindow(self.journals,factory=factory,closer=lambda service:None,is_active=lambda:True)
        self.windows.append(window)
        factory.assert_not_called()
        self.assertTrue(window.editor.isReadOnly())
        self.assertEqual(list(self.journals.iterdir()),[])
        self.assertFalse(general_editor_build.GENERAL_EDITOR_ONLY)

    def test_ctrl_s_shortcut_calls_real_manual_save(self):
        window,_ = self.prepare_window()
        window.show()
        window.activateWindow()
        window.editor.setFocus()
        window.editor.setPlainText(plan.MANUAL)
        QTest.qWait(50)
        QTest.keyClick(window.editor,Qt.Key.Key_S,Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(20)
        self.assertEqual(available_action(self.journals),'manual_send',window.log.toPlainText())

    def test_expiry_before_save_preserves_body_and_draft(self):
        clock = [0]
        service = self.service(ForegroundTicket(clock=lambda:clock[0]))
        window = self.window()
        window.attach_editor(service,'manual',service.prepare('manual'))
        window.editor.setPlainText(plan.MANUAL)
        clock[0] = 301
        window.check_expiry()
        window.start('manual_save')
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.WINDOWS.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertEqual(len(list(self.journals.glob('*-draft-*.txt'))),1)
        self.assert_preserved()

    def test_focus_loss_cancels_and_retains_unsaved_text(self):
        window,_ = self.prepare_window()
        window.editor.setPlainText(plan.MANUAL)
        window.application_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertIsNone(window.service)
        self.assertTrue(window.editor.isReadOnly())
        self.assertEqual(next(self.journals.glob('*-draft-*.txt')).read_bytes(),plan.MANUAL.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)

    def test_wrong_text_never_opens_gate_or_writes(self):
        window,_ = self.prepare_window()
        window.editor.setPlainText('다른 내용')
        window.start('manual_save')
        self.assertIn('EDITOR_TEXT_NOT_AS_AGREED',window.log.toPlainText())
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.WINDOWS.encode())
        self.assertFalse(scope_path(self.journals,'manual',plan_id=plan.PLAN_ID).exists())

    def test_external_file_change_refused_before_save(self):
        window,_ = self.prepare_window()
        (self.root/'설정.json').write_text('{}','utf-8')
        window.editor.setPlainText(plan.MANUAL)
        window.start('manual_save')
        self.assertIn('EDITOR_BASELINE_CHANGED',window.log.toPlainText())
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.WINDOWS.encode())

    def test_cancellation_before_atomic_replace_preserves_old_file(self):
        window,service = self.prepare_window()
        window.editor.setPlainText(plan.MANUAL)
        original = self.seed.wpm.compare_write_text_file
        def interrupted(*args,**kwargs):
            guard = kwargs['temporary_guard']
            def expire(path):
                service.ticket.cancel()
                guard(path)
            kwargs['temporary_guard'] = expire
            return original(*args,**kwargs)
        with patch.object(self.seed.wpm,'compare_write_text_file',side_effect=interrupted):
            window.start('manual_save')
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.WINDOWS.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertEqual(available_action(self.journals),'review')
        self.assert_preserved()

    def test_os_foreground_check_cancels_save_without_waiting_for_ui_event(self):
        from general_editor_ui import EditorTicket
        focused = [True]
        service = self.service(EditorTicket(lambda:focused[0]))
        window = self.window()
        window.attach_editor(service,'manual',service.prepare('manual'))
        window.editor.setPlainText(plan.MANUAL)
        original = self.seed.wpm.compare_write_text_file
        def lost_focus(*args,**kwargs):
            focused[0] = False
            return original(*args,**kwargs)
        with patch.object(self.seed.wpm,'compare_write_text_file',side_effect=lost_focus):
            window.start('manual_save')
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.WINDOWS.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertIn('EDITOR_WINDOW_NO_LONGER_FOREGROUND',window.log.toPlainText())
        self.assertIn('EDITOR_WINDOW_NO_LONGER_FOREGROUND',journal_path(self.journals,'manual_save').read_text())
        self.assert_preserved()

    def test_queue_failure_preserves_new_file_and_failure_record(self):
        window,_ = self.prepare_window()
        window.editor.setPlainText(plan.MANUAL)
        with patch.object(self.store,'enqueue',side_effect=OSError(28,'isolated queue failure')):
            window.start('manual_save')
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.MANUAL.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertEqual(available_action(self.journals),'review')
        self.assertIn('local_file_saved',journal_path(self.journals,'manual_save').read_text())
        self.assertIn('OSError:errno=28',journal_path(self.journals,'manual_save').read_text())
        self.assert_preserved()

    def test_lost_response_retains_attempt_and_forbids_fresh_service_resend(self):
        self.saved_manual()
        self.server.failure = 'lost'
        with self.assertRaises(httpx.ReadTimeout):self.service().send('manual')
        calls = list(self.server.calls)
        self.assertEqual(available_action(self.journals),'review')
        with self.assertRaises(GeneralValidationDenied):self.service().send('manual')
        self.assertEqual(self.server.calls,calls)
        self.assertEqual(self.store.counts(self.key)['inflight'],1)
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.MANUAL.encode())
        self.assert_preserved()

    def test_account_change_at_response_refuses_receipt(self):
        self.saved_manual()
        def change(name):
            if name=='document_commit':self.current=False
        self.server.on_request = change
        with self.assertRaises(GeneralValidationDenied):self.service().send('manual')
        self.assertEqual(self.store.get_document(self.key,plan.PATH)['revision'],3)
        self.assertEqual(available_action(self.journals),'review')

    def test_duplicate_http_attempt_is_denied_before_second_wire(self):
        self.saved_manual()
        service = self.service()
        inner = service.transport.inner
        original = inner.handle_request
        def duplicate(request):
            response = original(request)
            if request.url.path.endswith('/document_commit'):
                with self.assertRaises(GeneralValidationDenied):service.transport.handle_request(request)
            return response
        with patch.object(inner,'handle_request',side_effect=duplicate):
            with self.assertRaises(GeneralValidationDenied):service.send('manual')
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)
        self.assertEqual(self.store.get_document(self.key,plan.PATH)['revision'],3)

    def test_durable_attempt_failure_has_zero_write_requests(self):
        self.saved_manual()
        service = self.service()
        from general_validation_boundary import DurableExecutionJournal
        original = DurableExecutionJournal.append
        def fail_write(journal,event):
            if event.get('path','').endswith('/document_commit'):raise OSError('isolated journal failure')
            return original(journal,event)
        with patch.object(DurableExecutionJournal,'append',new=fail_write):
            with self.assertRaises(OSError):service.send('manual')
        self.assertEqual(self.server.calls.count(('POST','document_commit')),0)
        self.assertEqual(available_action(self.journals),'review')

    def test_restore_failure_enables_only_offline_undo(self):
        window,_ = self.prepare_window()
        window.editor.setPlainText(plan.MANUAL)
        with patch('general_validation_control.restore_control',side_effect=OSError('isolated restore failure')):
            window.start('manual_save')
        self.assertEqual(available_action(self.journals),'restore')
        self.assertEqual(self.store.get_project(self.key)['contract_path_enabled'],1)
        restore_pending_controls(self.store,self.key,self.journals,plan_id=plan.PLAN_ID)
        self.assertEqual(available_action(self.journals),'review')
        self.assert_preserved()

    def test_old_requests_and_ipad_revision_are_not_windows_authority(self):
        for stage in (plan.STAGES[0],plan.STAGES[4]):
            if stage.base_revision==0:
                req = prior.plan.document_request(stage.name,prior.DEVICE)
            else:
                req = plan.document_request(stage,writer_device_id=prior.DEVICE,operation_id='c2e89d52-ddcf-4157-ad15-5d8a52c048d4',
                    batch_id='415b05ec-3b7c-44bb-9271-3c1c0e8541bf',client_build_id='isolated')
            with self.assertRaises(GeneralValidationDenied):
                EditorRequest.capture(httpx.Request('POST',plan.STAGING_URL+'/rest/v1/rpc/document_commit',json={'p_request':req}))
        with self.assertRaises(GeneralValidationDenied):
            EditorRequest.capture(httpx.Request('POST',plan.STAGING_URL+'/rest/v1/rpc/atomic_structure_commit',json={'p_request':prior.plan.order_request(prior.DEVICE)}))

    def test_old_boundary_still_refuses_new_editor_revision(self):
        self.saved_manual()
        op = self.store.next_ready_operation(self.key)
        req = self.store.structure_batch_request(op['batch_id'])
        with self.assertRaises(GeneralValidationDenied):
            FrozenRequest.capture(httpx.Request('POST',plan.STAGING_URL+'/rest/v1/rpc/document_commit',json={'p_request':req}))

    def test_partial_auto_receive_preserves_body_and_blocks_reentry(self):
        self.saved_manual()
        self.service().send('manual')
        self.remote_ipad()
        with patch.object(self.store,'apply_remote_snapshot',side_effect=OSError('isolated baseline failure')):
            with self.assertRaises(OSError):self.service().prepare('auto')
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.IPAD.encode())
        self.assertEqual(self.store.get_document(self.key,plan.PATH)['revision'],4)
        self.assertEqual(available_action(self.journals),'review')
        self.assert_preserved()

    def test_readonly_reauthentication_before_save_preserves_prior_records(self):
        first = self.service()
        first.prepare('manual')
        first.ticket.cancel()
        before = {p.name:p.read_bytes() for p in self.journals.iterdir()}
        self.service().prepare('manual')
        self.assertEqual(len(list(self.journals.glob('*-prepare-*.jsonl'))),2)
        self.assertTrue(all((self.journals/name).read_bytes()==data for name,data in before.items()))
        self.assertEqual(self.store.counts(self.key)['total'],0)

    def test_partial_typing_does_not_save_before_agreed_final_line(self):
        self.saved_manual()
        self.service().send('manual')
        self.remote_ipad()
        window,_ = self.prepare_window('auto')
        window.editor.setPlainText(plan.IPAD+'Windows 자동')
        QTest.qWait(1000)
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.IPAD.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertIsNotNone(window.service)

    def test_idle_queue_failure_retains_concrete_error_in_journal(self):
        self.saved_manual()
        self.service().send('manual')
        self.remote_ipad()
        window,_ = self.prepare_window('auto')
        window.editor.setPlainText(plan.AUTO)
        with patch.object(self.store,'enqueue',side_effect=OSError('isolated idle enqueue failure')):
            window.save_auto()
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.AUTO.encode())
        events = [json.loads(s) for s in journal_path(self.journals,'auto_save').read_text().splitlines()]
        self.assertEqual(events[-1],{'event':'stopped','outcome':'OSError'})
        self.assertEqual(available_action(self.journals),'review')
        self.assert_preserved()

    def test_undo_persistence_failure_cannot_open_gate_or_replace_body(self):
        window,_ = self.prepare_window()
        window.editor.setPlainText(plan.MANUAL)
        with patch('general_validation_control._write_exclusive',side_effect=OSError('isolated undo failure')):
            window.start('manual_save')
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.WINDOWS.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assert_preserved()

    def test_new_foreign_queue_entry_blocks_send_before_http(self):
        self.saved_manual()
        # A second pending edit may not silently become part of the fixed send.
        self.store.enqueue(dict(self.seed.context,writer_device_id=prior.DEVICE),plan.PATH,'another edit')
        calls = list(self.server.calls)
        with self.assertRaises(GeneralValidationDenied):self.service().send('manual')
        self.assertEqual(self.server.calls,calls)

    def test_released_hold_refuses_preparation(self):
        self.seed.hold.write_text(json.dumps({'format':1,'project_id':plan.PROJECT_ID,'state':'released'}),'utf-8')
        with self.assertRaisesRegex(GeneralValidationDenied,'EDITOR_GLOBAL_HOLD_REQUIRED'):self.service().prepare('manual')
        self.assertEqual(self.server.calls,[])

    def test_old_control_plan_cannot_restore_editor_manifest(self):
        window,_ = self.prepare_window()
        window.editor.setPlainText(plan.MANUAL)
        with patch('general_validation_control.restore_control',side_effect=OSError('isolated restore failure')):
            window.start('manual_save')
        from general_validation_control import restore_control
        path = scope_path(self.journals,'manual',plan_id=plan.PLAN_ID)
        with self.assertRaises(GeneralValidationDenied):restore_control(self.store,self.key,path)
        restore_pending_controls(self.store,self.key,self.journals,plan_id=plan.PLAN_ID)
        self.assert_preserved()


if __name__=='__main__':unittest.main()
