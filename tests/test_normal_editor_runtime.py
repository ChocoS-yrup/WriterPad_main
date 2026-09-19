"""New normal-UI scenarios only, with real files/store/SDK and synthetic wire."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import httpx
from supabase import create_client, ClientOptions
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from tests.qt_app import APP
import test_general_editor_runtime as prior
from test_general_validation_boundary import ACCOUNT, DEVICE, TOKEN, success
import normal_editor_plan as plan
from normal_editor_local import LocalEditor
from normal_editor_network import NormalNetworkService, NormalRequest
from normal_editor_ui import NormalWritingWidget
from general_validation_transport import GeneralTransport
from body_validation_transport import ForegroundTicket
from sync_contract import json_sha256


class NormalTests(unittest.TestCase):
    def setUp(self):
        self.seed = prior.EditorTests()
        self.seed.setUp()
        self.addCleanup(self.seed.doCleanups)
        self.store,self.root,self.server = self.seed.store,self.seed.root,self.seed.server
        self.key = self.seed.key
        self.store.apply_remote_snapshot(self.seed.seed.context,plan.DOCUMENT_ID,plan.PATH,plan.INITIAL_CONTENT,6,
            local_path=plan.PATH,parent_folder_id=plan.PARENT_ID,name=plan.NAME,structure_revision=1)
        (self.root/plan.PATH).write_bytes(plan.INITIAL_CONTENT.encode('utf-8'))
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=6,content=plan.INITIAL_CONTENT)
        self.directory = self.seed.seed.root/'normal'
        self.receipts = {}
        self.receipt_change = lambda table,rows:rows
        self.receipt_count = None
        self.current = True
        self.local = self.new_local()
        self.windows = []
        self.addCleanup(self.close_windows)

    def close_windows(self):
        for w in self.windows:w.close()
        APP.processEvents()

    def new_local(self):
        return LocalEditor(store=self.store,wpm=self.seed.seed.wpm,directory=self.directory,device_id=DEVICE)

    def wire(self,request):
        table = request.url.path.rsplit('/',1)[-1]
        if table in ('sync_batches','sync_batch_results'):
            self.server.calls.append(('GET',table))
            batch = request.url.params['batch_id'].removeprefix('eq.')
            rows = [copy.deepcopy(self.receipts[batch][table])] if batch in self.receipts else []
            rows = self.receipt_change(table,rows)
            count = len(rows) if self.receipt_count is None else self.receipt_count
            return httpx.Response(200,json=rows,headers={'content-range':('0-0/' if rows else '*/')+str(count)})
        if table=='document_commit':
            contract = json.loads(request.content)['p_request']
            batch = contract['batch']
            response = success(contract)
            self.receipts[batch['batch_id']] = {'sync_batches':dict(batch,project_id=plan.PROJECT_ID,writer_user_id=ACCOUNT,
                project_sync_mode='ID_BASED',migration_epoch=1,request_sha256=json_sha256(contract)),
                'sync_batch_results':dict(batch_id=batch['batch_id'],applied=True,response=response,response_sha256=json_sha256(response))}
        return self.server(request)

    def service(self,ticket=None,local=None):
        local = local or self.local
        ticket = ticket or ForegroundTicket()
        transport = GeneralTransport(ticket,inner=httpx.MockTransport(self.wire))
        transport.bind_verified()
        http = httpx.Client(transport=transport,follow_redirects=False,trust_env=False)
        self.seed.seed.clients.append(http)
        client = create_client(plan.STAGING_URL,'synthetic-key',options=ClientOptions(httpx_client=http,
            auto_refresh_token=False,headers={'Authorization':'Bearer '+TOKEN}))
        return NormalNetworkService(local=local,client=client,transport=transport,ticket=ticket,device_id=DEVICE,
            account_id=ACCOUNT,access_token=TOKEN,session_current=lambda:self.current)

    def window(self,factory=None):
        window = NormalWritingWidget(self.local,network_factory=factory or self.service,closer=lambda service:None,is_active=lambda:True)
        self.windows.append(window)
        return window

    def save(self,text='자유롭게 바꾼 글\n끝에는 빈 줄을 강제하지 않는다.'):
        self.local.write(plan.PATH,text)
        return self.local.enqueue_saved()

    def lost(self):
        op = self.save()
        self.server.failure = 'lost'
        with self.assertRaises(Exception):self.service().send()
        self.server.failure = None
        self.assertEqual(self.store.operation(op['operation_id'])['status'],'inflight')
        return op

    def assert_preserved(self):
        self.seed.assert_preserved()

    def test_normal_widget_startup_has_no_auth_singleton_or_automatic_requests(self):
        factory = Mock(side_effect=AssertionError('auth not requested'))
        with patch('sync_manager.SyncManager.__init__',side_effect=AssertionError('ordinary startup')):
            window = self.window(factory)
            QTest.qWait(1050)
        factory.assert_not_called()
        self.assertEqual(self.server.calls,[])
        self.assertFalse(window.btn_toggle_split.isEnabled())
        self.assertFalse(window.rename_shortcut.isEnabled())
        self.assert_preserved()

    def test_product_ctrl_s_persists_free_text_without_login(self):
        factory = Mock(side_effect=AssertionError('offline save authenticated'))
        window = self.window(factory)
        window.show(); window.activateWindow(); window.left_editor.setFocus()
        window.left_editor.setPlainText('일반 화면 자유 편집 🎉\n마지막 LF 없음')
        QTest.qWait(30)
        QTest.keyClick(window.left_editor,Qt.Key.Key_S,Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(30)
        self.assertEqual((self.root/plan.PATH).read_text('utf-8'),'일반 화면 자유 편집 🎉\n마지막 LF 없음')
        self.assertIsNotNone(self.local.owned_operation())
        self.assertEqual(self.server.calls,[])
        factory.assert_not_called()
        self.assert_preserved()

    def test_idle_save_preserves_lf_and_unicode_without_network(self):
        window = self.window()
        content = '가 e\u0301 é 😀\n\n마지막 줄\n'
        window.left_editor.setPlainText(content)
        QTest.qWait(1000)
        self.assertEqual((self.root/plan.PATH).read_bytes(),content.encode('utf-8'))
        self.assertEqual(self.local.owned_operation()['content'],content)
        self.assertEqual(self.server.calls,[])

    def test_unsaved_draft_survives_new_local_instance(self):
        window = self.window()
        window.left_editor.setPlainText('저장 타이머 전 초안')
        window.close()
        other = self.new_local()
        self.assertEqual(other.draft(),'저장 타이머 전 초안')
        self.assertEqual((self.root/plan.PATH).read_bytes(),plan.INITIAL_CONTENT.encode('utf-8'))

    def test_free_edit_single_send_and_receive_preserve_other_work(self):
        op = self.save('새 Windows 문장')
        result = self.service().send()
        self.assertEqual(result['revision'],7)
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=8,content='iPad에서 자유 수정\n')
        self.assertEqual(self.service().receive()['revision'],8)
        self.assertEqual(self.local.draft(),'iPad에서 자유 수정\n')
        self.assertEqual(self.store.operation(op['operation_id'])['status'],'completed')
        self.assert_preserved()

    def test_multiple_offline_saves_preserve_first_immutable_op_and_later_text(self):
        first = self.save('첫 편집')
        self.save('다음 편집')
        self.assertEqual(self.local.owned_operation()['operation_id'],first['operation_id'])
        self.assertEqual(self.local.expected_disk,'다음 편집')
        self.service().send()
        self.local.enqueue_saved()
        second = self.local.owned_operation()
        self.assertNotEqual(second['operation_id'],first['operation_id'])
        self.assertEqual((second['base_revision'],second['content']),(7,'다음 편집'))
        self.assertEqual(self.service().send()['revision'],8)

    def test_lost_response_recovery_uses_two_selects_and_never_resends(self):
        op = self.lost()
        self.local = self.new_local()
        with self.assertRaisesRegex(Exception,'NO_UNATTEMPTED'):self.service().send()
        result = self.service().recover()
        self.assertEqual(result['revision'],7)
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)
        self.assertEqual(self.server.calls.count(('GET','sync_batches')),1)
        self.assertEqual(self.server.calls.count(('GET','sync_batch_results')),1)
        self.assertEqual(self.store.operation(op['operation_id'])['status'],'completed')
        self.assert_preserved()

    def test_absent_receipt_does_not_authorize_retransmission(self):
        op = self.lost(); self.receipts.clear()
        with self.assertRaisesRegex(Exception,'RECEIPT_NOT_FOUND'):self.service().recover()
        self.assertEqual(self.store.operation(op['operation_id'])['status'],'inflight')
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)

    def test_receipt_wrong_user_hash_and_count_rejected(self):
        op = self.lost()
        for field,value in [('writer_user_id',DEVICE),('request_sha256','0'*64),('writer_device_id',ACCOUNT)]:
            with self.subTest(field=field):
                self.receipt_change = lambda table,rows,f=field,v=value:[dict(r,**{f:v}) for r in rows] if table=='sync_batches' else rows
                with self.assertRaisesRegex(Exception,'RECEIPT_REQUEST_CHANGED'):self.service().recover()
        self.receipt_change = lambda table,rows:rows
        self.receipt_count = 2
        with self.assertRaisesRegex(Exception,'RECEIPT_INCOMPLETE'):self.service().recover()
        self.assertEqual(self.store.operation(op['operation_id'])['status'],'inflight')

    def test_bad_receipt_result_body_is_not_applied(self):
        op = self.lost()
        self.receipts[op['batch_id']]['sync_batch_results']['response']['results'][0]['result_revision'] = 88
        with self.assertRaisesRegex(Exception,'RECEIPT_RESPONSE_CHANGED'):self.service().recover()
        self.assertEqual(self.local.document()['revision'],6)

    def test_receipt_with_replayed_status_is_validated_and_accepted(self):
        op = self.lost()
        receipt = self.receipts[op['batch_id']]['sync_batch_results']
        receipt['response']['status'] = 'replayed'
        receipt['response_sha256'] = json_sha256(receipt['response'])
        self.assertEqual(self.service().recover()['revision'],7)

    def test_auth_expiry_does_not_prevent_local_save_or_erase_completed_status(self):
        ticket = ForegroundTicket(); ticket.cancel()
        self.save()
        with self.assertRaises(Exception):self.service(ticket).send()
        self.assertEqual(self.server.calls,[])
        self.service().send()
        self.current = False
        self.assertIn('서버 반영 완료',self.local.status())
        self.assertIn('서버 반영 완료',self.new_local().status())

    def test_queue_failure_retains_saved_text_and_recovers_without_new_edit(self):
        with patch.object(self.store,'enqueue',side_effect=OSError('synthetic disk failure')):
            self.local.write(plan.PATH,'큐 실패 중 보존')
            with self.assertRaises(OSError):self.local.enqueue_saved()
        self.assertEqual(self.local.expected_disk,'큐 실패 중 보존')
        self.assert_preserved()
        self.local = self.new_local(); self.local.recover_local()
        self.assertEqual(self.local.owned_operation()['content'],'큐 실패 중 보존')

    def test_receive_refuses_dirty_draft_and_unsent_queue(self):
        self.local.preserve_draft('아직 저장 안 됨')
        with self.assertRaisesRegex(Exception,'HAS_LOCAL_WORK'):self.service().receive()
        self.save('저장했지만 미송신')
        with self.assertRaisesRegex(Exception,'HAS_LOCAL_WORK'):self.service().receive()
        self.assertEqual(self.server.calls,[])

    def test_partial_receive_recovers_same_snapshot_without_network(self):
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=7,content='수신할 글')
        with patch.object(self.store,'apply_remote_snapshot',side_effect=OSError('synthetic metadata error')):
            with self.assertRaises(OSError):self.service().receive()
        calls = list(self.server.calls)
        self.local = self.new_local(); self.local.recover_local()
        self.assertEqual((self.local.document()['revision'],self.local.expected_disk),(7,'수신할 글'))
        self.assertEqual(self.server.calls,calls)

    def test_unexpected_file_change_is_never_overwritten(self):
        (self.root/plan.PATH).write_bytes(b'external edit')
        with self.assertRaises(Exception):self.local.write(plan.PATH,'do not overwrite')
        self.assertEqual((self.root/plan.PATH).read_bytes(),b'external edit')

    def test_scope_rejects_other_path_empty_and_old_fixed_request(self):
        with self.assertRaises(Exception):self.local.write('other.txt','no')
        with self.assertRaises(Exception):self.local.write(plan.PATH,'')
        from general_validation_plan import document_request
        request = document_request('windows_create',DEVICE)
        with self.assertRaises(Exception):NormalRequest.capture(httpx.Request('POST',plan.STAGING_URL+'/rest/v1/rpc/document_commit',json={'p_request':request}))
        self.assertEqual(self.server.calls,[])

    def test_duplicate_send_after_completion_never_posts_again(self):
        self.save(); self.service().send()
        with self.assertRaisesRegex(Exception,'NO_UNATTEMPTED'):self.service().send()
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)

    def test_torn_recovery_record_preserved_and_blocks_restart(self):
        self.local.log.append('draft',content='durable draft',base_revision=6)
        path = self.local.log.root/'00000003.json'
        path.write_bytes(b'{')
        with self.assertRaises(Exception):self.new_local()
        self.assertEqual(path.read_bytes(),b'{')

    def test_local_receipt_failure_recovers_without_second_commit(self):
        self.save()
        with patch.object(self.store,'mark_success',side_effect=OSError('synthetic receipt save failure')):
            with self.assertRaises(OSError):self.service().send()
        self.assertIsNotNone(self.local.log.latest('response'))
        self.local = self.new_local()
        self.assertEqual(self.service().recover()['revision'],7)
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)

    def test_remote_revision_conflict_preserves_three_bodies_and_operation(self):
        op = self.save('로컬 글')
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=7,content='다른 기기 글')
        with self.assertRaisesRegex(Exception,'REMOTE_CONFLICT'):self.service().send()
        self.assertEqual(self.store.operation(op['operation_id'])['status'],'pending')
        self.assertEqual(self.local.expected_disk,'로컬 글')
        self.assertNotIn(('POST','document_commit'),self.server.calls)

    def test_working_checkout_cannot_open_installed_runtime(self):
        from normal_editor_runtime import runtime_paths
        with self.assertRaisesRegex(Exception,'CANDIDATE_REQUIRED'):runtime_paths()

    def test_pending_receive_allows_draft_but_not_canonical_save(self):
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=7,content='부분 수신')
        with patch.object(self.store,'apply_remote_snapshot',side_effect=OSError('partial')):
            with self.assertRaises(OSError):self.service().receive()
        with self.assertRaisesRegex(Exception,'RECEIVE_RECOVERY_REQUIRED'):self.local.write(plan.PATH,'보존할 새 초안')
        self.assertEqual(self.local.draft(),'보존할 새 초안')
        self.assertEqual((self.root/plan.PATH).read_text('utf-8'),'부분 수신')

    def test_lost_response_other_account_cannot_accept_cached_or_remote_receipt(self):
        self.lost()
        service = self.service(); service.account_id = DEVICE
        calls = list(self.server.calls)
        with self.assertRaisesRegex(Exception,'RECOVERY_ACCOUNT'):service.recover()
        self.assertEqual(self.server.calls,calls)

    def test_gate_restore_failure_blocks_network_and_offline_recovery_restores_exact_values(self):
        import general_validation_control as control
        self.local.write(plan.PATH,'관문 복원 실패 보존')
        with patch.object(control,'restore_control',side_effect=OSError('restore failure')):
            with self.assertRaises(OSError):self.local.enqueue_saved()
        self.assertEqual(self.store.get_project(self.key)['contract_path_enabled'],1)
        self.local = self.new_local()
        with self.assertRaisesRegex(Exception,'CONTROL_RESTORE_REQUIRED'):self.service().send()
        self.local.recover_local()
        self.assert_preserved()
        self.assertEqual(self.local.owned_operation()['content'],'관문 복원 실패 보존')

    def test_claim_before_http_can_recover_same_operation_first_transmission(self):
        op = self.save()
        service = self.service()
        with patch.object(service,'_commit',side_effect=OSError('before wire')):
            with self.assertRaises(OSError):service.send()
        self.assertEqual(self.server.calls.count(('POST','document_commit')),0)
        self.local = self.new_local()
        self.assertIn('note',self.service().recover())
        self.assertEqual(self.local.owned_operation()['operation_id'],op['operation_id'])
        self.assertEqual(self.service().send()['revision'],7)
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)

    def test_conflict_later_local_save_never_cancels_the_original_operation(self):
        op = self.save('처음 저장')
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=7,content='원격 충돌')
        with self.assertRaises(Exception):self.service().send()
        self.save('추가 로컬 글')
        self.assertEqual(self.local.owned_operation()['operation_id'],op['operation_id'])
        self.assertEqual(self.local.status(),'충돌/복구 대기')
        with self.assertRaisesRegex(Exception,'CONFLICT_REQUIRES_REVIEW'):self.service().send()
        self.assertEqual(self.local.log.latest('conflict')['remote'],'원격 충돌')

    def test_network_worker_ui_is_explicit_and_blocks_duplicate_click(self):
        window = self.window()
        window.left_editor.setPlainText('실제 UI 명시적 송신')
        window.manual_save()
        window.start_network('send'); window.start_network('send')
        for _ in range(300):
            QTest.qWait(10)
            if window.worker is None:break
        self.assertIsNone(window.worker)
        self.assertIn('revision 7',window.result_label.text())
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)
        self.assertFalse(window.left_editor.isReadOnly())

    def test_focus_loss_during_request_retains_uncertain_operation_for_receipt(self):
        self.save()
        ticket = ForegroundTicket()
        self.server.on_request = lambda name:ticket.cancel() if name=='document_commit' else None
        with self.assertRaises(Exception):self.service(ticket).send()
        self.server.on_request = None
        self.assertIn('결과 확인',self.local.status())
        self.assertEqual(self.service().recover()['revision'],7)
        self.assertEqual(self.server.calls.count(('POST','document_commit')),1)

    def test_crash_after_file_replace_before_saved_record_recovers_queue(self):
        append = self.local.log.append
        def failing(event,**data):
            if event=='file_saved':raise OSError('record failure after atomic save')
            return append(event,**data)
        with patch.object(self.local.log,'append',side_effect=failing):
            with self.assertRaises(OSError):self.local.write(plan.PATH,'원자적 저장 후 중단')
        self.local = self.new_local()
        self.local.recover_local()
        self.assertEqual(self.local.owned_operation()['content'],'원자적 저장 후 중단')

    def test_empty_editor_stays_draft_without_periodic_save_retry(self):
        window = self.window()
        window.left_editor.setPlainText('')
        QTest.qWait(1000)
        self.assertFalse(window.controller.idle_timer.isActive())
        self.assertEqual(self.local.draft(),'')
        self.assertEqual(self.local.expected_disk,plan.INITIAL_CONTENT)
        self.assertEqual(self.server.calls,[])

    def test_manual_mode_keeps_draft_until_ctrl_s_then_auto_mode_uses_idle_save(self):
        window = self.window()
        window.autosave_toggle.setChecked(False)
        window.left_editor.setPlainText('입력 중 잠시 멈춤')
        QTest.qWait(1000)
        self.assertEqual(self.local.expected_disk,plan.INITIAL_CONTENT)
        self.assertEqual(self.local.draft(),'입력 중 잠시 멈춤')
        window.manual_save()
        self.assertEqual(self.local.expected_disk,'입력 중 잠시 멈춤')
        window.left_editor.setPlainText('자동저장할 다음 초안')
        window.autosave_toggle.setChecked(True)
        QTest.qWait(1000)
        self.assertEqual(self.local.expected_disk,'자동저장할 다음 초안')
        self.assertEqual(self.server.calls,[])
