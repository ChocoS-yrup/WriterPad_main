"""Actual product files/store/SDK, synthetic Auth/HTTP; no installed app access."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from supabase import ClientOptions, create_client

import general_validation_plan as plan
from general_validation_boundary import GeneralValidationDenied
from general_validation_transport import GeneralTransport
from general_validation_service import (GeneralValidationService, CONTROL_ID, TABLES,
    expected_orders, folder_paths, validate_snapshot)
from general_test_gate_target import EXPECTED_FOLDERS, EXPECTED_ORDERS
from body_validation_transport import ForegroundTicket
from project_manager_writing import WritingProjectManager
from sync_v2_store import SyncV2Store
from sync_contract import CANONICAL_CONTRACT_SHA256, SERVER_CAPABILITIES, CONTRACT_VERSION
from test_general_validation_boundary import ACCOUNT, DEVICE, TOKEN, success


def fixture():
    control = json.loads((Path(__file__).parent/'fixtures/general_validation_control.json').read_text('utf-8'))['content']
    return {'projects':[{'project_id':plan.PROJECT_ID,'owner_id':ACCOUNT,'name':plan.PROJECT_NAME,'deleted_at':None}],
        'project_sync_settings':[{'project_id':plan.PROJECT_ID,'project_sync_mode':'ID_BASED','migration_epoch':1}],
        'documents':[{'project_id':plan.PROJECT_ID,'document_id':CONTROL_ID,'relative_path':'__antigravity__/tree-order.json',
            'revision':1,'content':control,'is_deleted':False,'deleted_at':None}],
        'folders':[{'project_id':plan.PROJECT_ID,'folder_id':r['id'],'parent_folder_id':r['parent'],
            'name':r['name'],'revision':r['revision'],'is_deleted':False,'deleted_at':None} for r in EXPECTED_FOLDERS],
        'tree_orders':[{'project_id':plan.PROJECT_ID,'tree_order_id':r['id'],'parent_folder_id':r['parent'],
            'children':r['children'],'revision':1} for r in EXPECTED_ORDERS]}


class FakeServer:
    def __init__(self):
        self.rows,self.calls = fixture(),[]
        self.failure,self.on_request = None,None

    def __call__(self,req):
        name = req.url.path.rsplit('/',1)[-1]
        self.calls.append((req.method,name))
        if self.on_request:
            self.on_request(name)
        if req.method == 'GET':
            return httpx.Response(200,json=copy.deepcopy(self.rows[name]))
        if name == 'get_sync_handshake':
            return httpx.Response(200,json={'project_id':plan.PROJECT_ID,'project_sync_mode':'ID_BASED','migration_epoch':1,
                'server_protocol_version':3,'supported_protocol_versions':[3],'server_contract_sha256':CANONICAL_CONTRACT_SHA256,
                'canonical_contract_sha256':CANONICAL_CONTRACT_SHA256,'contract_version':CONTRACT_VERSION,'server_capabilities':SERVER_CAPABILITIES})
        contract = json.loads(req.content)['p_request']
        intent = contract['ordered_intents'][0]
        payload = intent['payload']
        if name == 'document_commit':
            body = next((r for r in self.rows['documents'] if r['document_id'] == plan.DOCUMENT_ID),None)
            if body is None:
                body = {'document_id':plan.DOCUMENT_ID,'project_id':plan.PROJECT_ID,'relative_path':plan.PATH,
                    'deleted_at':None}
                self.rows['documents'].append(body)
            body.update(content=payload['content'],revision=intent['base_revision']+1,parent_folder_id=plan.PARENT_ID,
                        name=plan.NAME,structure_revision=1,is_deleted=False)
        elif name == 'atomic_structure_commit':
            if self.failure == 'order':
                return httpx.Response(409,json={'code':'synthetic conflict'})
            next(r for r in self.rows['tree_orders'] if r['tree_order_id'] == plan.ORDER_ID).update(children=[plan.DOCUMENT_ID],revision=2)
        else:
            raise AssertionError(name)
        if self.failure == 'lost':
            raise httpx.ReadTimeout('synthetic response loss')
        return httpx.Response(200,json=success(contract))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.writing = self.root/plan.PROJECT_NAME/'집필모드'
        self.writing.mkdir(parents=True)
        self.journals = self.root/'journals'; self.journals.mkdir()
        self.server = FakeServer()
        paths,orders = folder_paths(),expected_orders(False)
        nodes = []
        for r in EXPECTED_FOLDERS:
            path = paths[r['id']]
            (self.writing/path).mkdir(parents=True,exist_ok=True)
            siblings = next(children for parent,children,_ in orders.values() if parent == r['parent'])
            # Saved identity predates the view's trash-last ordering. Match the
            # actual preserved identity instead of cloning server order here.
            siblings = list(siblings)
            if r['parent']=='e87a0e44-9a8d-4b18-b974-b40f4ab1eaad':
                siblings[-2:] = reversed(siblings[-2:])
            nodes.append({'uuid':r['id'],'kind':'folder','parent_uuid':r['parent'],'legacy_path':path,
                'path':path,'title':r['name'],'order':siblings.index(r['id'])})
        identity = self.writing.parent/'.writerpad';identity.mkdir()
        (identity/'identity-v1.json').write_text(json.dumps({'format_version':1,'project':{'uuid':plan.PROJECT_ID},'nodes':nodes}), 'utf-8')
        settings = {('<root>' if parent is None else paths[parent]):[paths[c].rsplit('/',1)[-1] for c in children]
                    for parent,children,_ in orders.values()}
        settings.pop('<root>')
        settings['<root>'] = settings.pop('메인')  # Actual Windows saved view hides this container.
        (self.writing/'설정.json').write_text(json.dumps({'tree_order':settings,'unrelated_setting':'preserve'}),'utf-8')
        self.store = SyncV2Store(str(self.root/'sync.sqlite3'))
        self.context = self.store.configure_project(str(self.writing),plan.PROJECT_NAME,plan.PROJECT_ID)
        self.key = self.context['local_key']
        control = self.server.rows['documents'][0]
        self.store.ensure_document(self.key,control['relative_path'],'',CONTROL_ID)
        self.store.apply_remote_snapshot(self.context,CONTROL_ID,control['relative_path'],control['content'],1)
        self.store.replace_folder_snapshots(self.key,[dict(r,local_path=paths[r['folder_id']]) for r in self.server.rows['folders']])
        self.store.replace_tree_order_snapshots(self.key,self.server.rows['tree_orders'])
        with self.store._transaction() as conn:
            conn.execute("UPDATE sync_projects SET project_sync_mode='MIGRATING',migration_epoch=1 WHERE local_key=?",(self.key,))
            conn.execute("UPDATE sync_projects SET project_sync_mode='ID_BASED',migration_epoch=1 WHERE local_key=?",(self.key,))
        self.store.activate_contract_project(self.key,project_sync_mode='ID_BASED',migration_epoch=1,
            server_protocol_version=3,server_contract_sha256=CANONICAL_CONTRACT_SHA256,server_capabilities=SERVER_CAPABILITIES)
        self.hold = self.root/('.general-test-send-hold-'+plan.PROJECT_ID)
        self.hold.write_text(json.dumps({'format':1,'project_id':plan.PROJECT_ID,'state':'held'}),'utf-8')
        other = self.store.configure_project(str(self.root/'other'),'other',str(uuid4()))
        self.other_op = self.store.enqueue(other,'other.txt','preserve pending work')
        self.wpm = object.__new__(WritingProjectManager)
        self.wpm.writing_root_path = str(self.writing)
        self.clients = []
        self.current = True
        self.service = self.new_service()

    def new_service(self):
        ticket = ForegroundTicket()
        transport = GeneralTransport(ticket,inner=httpx.MockTransport(self.server))
        transport.bind_verified()
        http = httpx.Client(transport=transport,follow_redirects=False,trust_env=False)
        client = create_client(plan.STAGING_URL,'synthetic-key',options=ClientOptions(httpx_client=http,
            auto_refresh_token=False,headers={'Authorization':'Bearer '+TOKEN}))
        self.clients.append(http)
        return GeneralValidationService(store=self.store,wpm=self.wpm,client=client,transport=transport,ticket=ticket,
            journal_dir=self.journals,device_id=DEVICE,account_id=ACCOUNT,access_token=TOKEN,session_current=lambda:self.current)

    def tearDown(self):
        for client in self.clients:client.close()
        self.tmp.cleanup()

    def test_full_create_order_receive_update_uses_real_store_sdk_and_keeps_other_queue(self):
        original_project = self.store.get_project(self.key)
        original_hold = self.hold.read_bytes()
        self.assertEqual(self.service.run('create')['revision'],1)
        self.assertEqual(self.store.get_project(self.key),original_project)
        self.assertEqual(self.store.get_document_by_id(plan.DOCUMENT_ID)['revision'],1)
        next(r for r in self.server.rows['documents'] if r['document_id']==plan.DOCUMENT_ID).update(revision=2,content=plan.IPAD)
        result = self.new_service().run('finish')
        self.assertEqual((result['revision'],result['bytes'],result['sha256']),(3,159,plan.digest(plan.WINDOWS)))
        self.assertEqual((self.writing/plan.PATH).read_bytes(),plan.WINDOWS.encode())
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertEqual(self.store.operation(self.other_op['operation_id']),self.other_op)
        self.assertEqual(self.store.get_project(self.key),original_project)
        self.assertEqual(self.hold.read_bytes(),original_hold)
        self.assertEqual([name for method,name in self.server.calls if method=='POST'],
            ['get_sync_handshake','document_commit','atomic_structure_commit','get_sync_handshake','document_commit'])
        with self.assertRaises(FileExistsError):self.new_service().run('finish')

    def test_initially_open_gate_refused_without_file_creation_or_write_rpc(self):
        self.store.set_contract_path_enabled(self.key,True)
        with self.assertRaisesRegex(GeneralValidationDenied,'ORIGINAL_CLOSED_GATE'):self.service.run('create')
        self.assertFalse(any(name in ('document_commit','atomic_structure_commit') for _,name in self.server.calls))
        self.assertFalse((self.writing/plan.PATH).exists())

    def test_prepare_with_closed_gate_is_read_only_and_cannot_repeat(self):
        self.store.set_contract_path_enabled(self.key,False)
        before = self.service._fingerprint()
        self.assertEqual(self.service.run('prepare')['revision'],0)
        self.assertEqual(self.service._fingerprint(),before)
        self.assertEqual(len(self.server.calls),6)
        with self.assertRaises(FileExistsError):self.new_service().run('prepare')

    def test_order_failure_preserves_created_body_and_pending_order_without_retry(self):
        self.server.failure = 'order'
        with self.assertRaises(GeneralValidationDenied):self.service.run('create')
        self.assertEqual(self.store.get_document_by_id(plan.DOCUMENT_ID)['revision'],1)
        self.assertEqual((self.writing/plan.PATH).read_bytes(),plan.BASE.encode())
        count = len(self.server.calls)
        with self.assertRaises(Exception):self.new_service().run('create')
        self.assertEqual(len(self.server.calls),count)
        self.assertEqual(self.store.counts(self.key)['inflight'],1)

    def test_unknown_create_response_preserves_queue_and_blocks_next_stage(self):
        self.server.failure = 'lost'
        with self.assertRaises(httpx.ReadTimeout):self.service.run('create')
        self.assertEqual(self.store.get_document_by_id(plan.DOCUMENT_ID)['revision'],0)
        with self.assertRaisesRegex(GeneralValidationDenied,'CREATE_NOT_COMPLETED'):self.new_service().run('finish')
        self.assertEqual(sum(name=='document_commit' for _,name in self.server.calls),1)

    def test_local_file_changed_during_read_stops_before_any_write(self):
        def mutate(name):
            if name == 'projects':(self.writing/'설정.json').write_text('{}','utf-8')
        self.server.on_request = mutate
        with self.assertRaisesRegex(GeneralValidationDenied,'LOCAL_CHANGED_DURING_REQUEST'):self.service.run('create')
        self.assertFalse((self.writing/plan.PATH).exists())
        self.assertEqual(self.server.calls,[('POST','get_sync_handshake'),('GET','projects')])

    def test_logout_during_request_discards_result(self):
        self.server.on_request = lambda name:setattr(self,'current',False)
        with self.assertRaisesRegex(GeneralValidationDenied,'AUTHORITY_CHANGED'):self.service.run('create')
        self.assertEqual(len(self.server.calls),1)
        self.assertFalse((self.writing/plan.PATH).exists())

    def test_owned_save_temp_exception_does_not_hide_another_untracked_file(self):
        original = self.wpm.compare_write_text_file
        def extra_file(*args,**kwargs):
            guard = kwargs['temporary_guard']
            def temp_guard(path):
                (self.writing/'메인/원고/unowned.scope-test').write_text('not ours','utf-8')
                guard(path)
            return original(*args,**dict(kwargs,temporary_guard=temp_guard))
        with patch.object(self.wpm,'compare_write_text_file',side_effect=extra_file):
            with self.assertRaisesRegex(GeneralValidationDenied,'LOCAL_TREE_AUDIT_CHANGED'):self.service.run('create')
        self.assertEqual((self.writing/plan.PATH).read_bytes(),b'')
        self.assertFalse(any(name=='document_commit' for _,name in self.server.calls))

    def test_foreground_loss_during_create_keeps_unknown_result_and_no_order(self):
        def cancel(name):
            if name == 'document_commit':self.service.ticket.cancel()
        self.server.on_request = cancel
        with self.assertRaises(Exception):self.service.run('create')
        self.assertEqual(self.store.get_document_by_id(plan.DOCUMENT_ID)['revision'],0)
        self.assertFalse(any(name=='atomic_structure_commit' for _,name in self.server.calls))

    def test_owner_mode_body_collision_and_order_drift_rejected_before_create(self):
        original = copy.deepcopy(self.server.rows)
        for change in ('owner','mode','document','order'):
            rows = copy.deepcopy(original)
            if change=='owner':rows['projects'][0]['owner_id']=str(uuid4())
            elif change=='mode':rows['project_sync_settings'][0]['project_sync_mode']='LEGACY'
            elif change=='document':rows['documents'].append(dict(rows['documents'][0],document_id=plan.DOCUMENT_ID))
            else:rows['tree_orders'][0]['revision']=2
            with self.subTest(change=change),self.assertRaises(GeneralValidationDenied):validate_snapshot(rows,0,ACCOUNT)

    def test_identity_order_drift_is_not_normalized_or_ignored(self):
        path = self.writing.parent/'.writerpad/identity-v1.json'
        before = path.read_bytes()
        for changed_id,new_order in (('13186784-2e57-4e29-b28a-178301679824',9),
                                    ('f4c92790-d675-4970-b1fc-b90f3a929ffb',1),
                                    ('a2d9f760-1a42-4ead-a2d0-51870b062fd9',99)):
            identity = json.loads(before)
            next(n for n in identity['nodes'] if n['uuid']==changed_id)['order'] = new_order
            data = json.dumps(identity).encode()
            path.write_bytes(data)
            with self.subTest(node=changed_id),self.assertRaises(Exception):self.new_service().run('prepare')
            self.assertEqual(path.read_bytes(),data)
        self.assertEqual(self.server.calls,[])


class AuthAndUITests(unittest.TestCase):
    def test_restart_restores_only_next_completed_stage_and_never_retries_interrupted_one(self):
        from general_validation_ui import available_action
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(available_action(folder),'prepare')
            Path(folder,plan.PLAN_ID+'-prepare.jsonl').write_text('{"event":"completed"}\n','utf-8')
            self.assertEqual(available_action(folder),'create')
            p = Path(folder,plan.PLAN_ID+'-create.jsonl')
            p.write_text('{"event":"reserved"}\n','utf-8')
            self.assertIsNone(available_action(folder))
            p.write_text('{"event":"completed"}\n','utf-8')
            self.assertEqual(available_action(folder),'finish')

    def test_real_sdk_auth_phase_cannot_read_data_and_cannot_reopen_after_binding(self):
        calls = []
        def auth(req):
            calls.append(req.url.path)
            return httpx.Response(200,json={'id':ACCOUNT,'aud':'authenticated','role':'authenticated',
                'email':'synthetic@example.invalid','created_at':'2026-09-12T00:00:00Z','app_metadata':{},'user_metadata':{}})
        transport = GeneralTransport(ForegroundTicket(),inner=httpx.MockTransport(auth))
        with httpx.Client(transport=transport,trust_env=False) as http:
            client = create_client(plan.STAGING_URL,'synthetic-key',options=ClientOptions(httpx_client=http,auto_refresh_token=False))
            self.assertEqual(str(client.auth.get_user(TOKEN).user.id),ACCOUNT)
            transport.bind_verified()
            from supabase_auth.errors import AuthRetryableError
            with self.assertRaises(AuthRetryableError):client.auth.get_user(TOKEN)
        self.assertEqual(calls,['/auth/v1/user'])
        transport = GeneralTransport(ForegroundTicket(),inner=httpx.MockTransport(auth))
        with self.assertRaises(Exception):transport.handle_request(httpx.Request('GET',plan.STAGING_URL+'/rest/v1/documents'))
        self.assertEqual(len(calls),1)
        transport.close()

    def test_window_startup_has_no_service_and_focus_loss_cancels_ticket(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt
        import general_validation_ui as ui
        app = QApplication.instance() or QApplication([])
        with patch.object(ui,'open_service',side_effect=AssertionError('startup must be inert')):
            window = ui.ValidationWindow()
            self.assertIsNone(window.worker)
            window.ticket = ForegroundTicket()
            window.application_state_changed(Qt.ApplicationState.ApplicationInactive)
            with self.assertRaises(Exception):window.ticket.check()
            window.close()
        app.processEvents()


if __name__ == '__main__':unittest.main()
