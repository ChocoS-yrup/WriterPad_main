import copy
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import httpx
from supabase import ClientOptions, create_client

import general_validation_plan as plan
from general_validation_boundary import (ForegroundAuthority, FrozenRequest, GeneralHTTPBoundary,
    GeneralValidationDenied, DurableExecutionJournal, check_store_binding)
from sync_v2_store import SyncV2Store
from sync_contract import CANONICAL_CONTRACT_SHA256, SERVER_CAPABILITIES

ACCOUNT = '10000000-0000-4000-8000-000000000001'
DEVICE = '10000000-0000-4000-8000-000000000002'
TOKEN = 'isolated-general-token'


def wire(contract=None, *, table=None):
    if table:
        return httpx.Request('GET', plan.STAGING_URL+'/rest/v1/'+table,
            params={'select':'*','project_id':'eq.'+plan.PROJECT_ID},
            headers={'Authorization':'Bearer '+TOKEN})
    contract = contract or plan.document_request('windows_create', DEVICE)
    rpc = 'document_commit' if contract['kind']=='document_commit_request' else 'atomic_structure_commit'
    return httpx.Request('POST',plan.STAGING_URL+'/rest/v1/rpc/'+rpc,
        json={'p_request':contract},headers={'Authorization':'Bearer '+TOKEN})


def success(contract):
    results = []
    for intent in contract['ordered_intents']:
        result = {'sequence':intent['sequence'],'operation_id':intent['operation_id'],
                  'result_revision':intent['base_revision']+1}
        if contract['kind']=='document_commit_request':
            payload = intent['payload']
            result.update(document_id=intent['document_id'],structure_revision=payload['structure_revision'],
                parent_folder_id=payload['parent_folder_id'],name=payload['name'],
                content_sha256=payload['content_sha256'],content_byte_count=payload['content_byte_count'],is_deleted=False)
        else:
            result['entity_id'] = intent['entity_id']
        results.append(result)
    return {'kind':contract['kind'].replace('_request','_success'),
        'batch_id':contract['batch']['batch_id'],'batch_payload_sha256':contract['batch']['batch_payload_sha256'],
        'status':'committed','applied':True,'results':results}


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.attempts = [], []
        self.now, self.current, self.local_ok = 0, True, True
        self.authority = ForegroundAuthority(account_id=ACCOUNT,local_key='synthetic-local',access_token=TOKEN,
            check_current=lambda a,l,p: self.current and (a,l,p)==(ACCOUNT,'synthetic-local',plan.PROJECT_ID),
            clock=lambda:self.now)
        self.boundaries = []

    def tearDown(self):
        for boundary in self.boundaries:boundary.close()

    def server(self, req):
        self.calls.append(req)
        if req.method=='GET':return httpx.Response(200,json=[])
        return httpx.Response(200,json=success(json.loads(req.content)['p_request']))

    def boundary(self, requests=None, server=None, persist=None):
        b = GeneralHTTPBoundary(authority=self.authority,
            requests=tuple(FrozenRequest.capture(r) for r in (requests or [wire()])),
            check_local=lambda:self.local_ok,persist_attempt=persist or self.attempts.append,
            inner=httpx.MockTransport(server or self.server))
        self.boundaries.append(b);return b

    def test_actual_sdk_sends_only_one_frozen_contract_request(self):
        contract = plan.document_request('windows_create', DEVICE)
        b = self.boundary()
        with httpx.Client(transport=b,follow_redirects=False,trust_env=False) as http:
            client = create_client(plan.STAGING_URL,'synthetic-key',options=ClientOptions(httpx_client=http,auto_refresh_token=False))
            client.postgrest.auth(TOKEN)
            result = client.rpc('document_commit',{'p_request':contract}).execute()
            self.assertTrue(result.data['applied'])
            with self.assertRaises(GeneralValidationDenied):client.rpc('document_commit',{'p_request':contract}).execute()
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.attempts),1)

    def test_exact_bytes_cannot_be_changed_even_with_equivalent_json(self):
        b = self.boundary();req = wire()
        changed = httpx.Request(req.method,req.url,headers=req.headers,content=req.content+b' ')
        with self.assertRaises(GeneralValidationDenied):b.handle_request(changed)
        self.assertEqual(self.calls,[])

    def test_unrelated_document_parent_order_content_and_mode_are_not_reviewable(self):
        variants = []
        for field,value in [('project_id',str(uuid4())),('project_sync_mode','LEGACY'),('migration_epoch',0)]:
            req = plan.document_request('windows_create',DEVICE);req[field]=value;variants.append(req)
        for field,value in [('document_id',str(uuid4())),('base_revision',8)]:
            req = plan.document_request('windows_create',DEVICE);req['ordered_intents'][0][field]=value;variants.append(req)
        for field,value in [('parent_folder_id',str(uuid4())),('content','unreviewed'),('name','other.txt')]:
            req = plan.document_request('windows_create',DEVICE);req['ordered_intents'][0]['payload'][field]=value;variants.append(req)
        order = plan.order_request(DEVICE);order['ordered_intents'][0]['payload']['children'].append(str(uuid4()));variants.append(order)
        for req in variants:
            with self.subTest(req=req['kind']):
                with self.assertRaises(Exception):FrozenRequest.capture(wire(req))
        self.assertEqual(self.calls,[])

    def test_unknown_auth_legacy_lease_and_realtime_paths_denied(self):
        for path in ('/auth/v1/user','/auth/v1/token','/rest/v1/rpc/commit_document',
                     '/rest/v1/rpc/acquire_edit_lease','/realtime/v1/websocket'):
            with self.subTest(path=path),self.assertRaises(GeneralValidationDenied):
                FrozenRequest.capture(httpx.Request('POST',plan.STAGING_URL+path,json={}))

    def test_read_rejects_broad_duplicate_relation_and_other_project_filters(self):
        for query in ('select=*','select=*&project_id=eq.'+str(uuid4()),
            'select=*&project_id=eq.'+plan.PROJECT_ID+'&project_id=eq.'+plan.PROJECT_ID,
            'select=*,projects(*)&project_id=eq.'+plan.PROJECT_ID,
            'select=*&project_id=eq.'+plan.PROJECT_ID+'&limit=1'):
            with self.subTest(query=query),self.assertRaises(GeneralValidationDenied):
                FrozenRequest.capture(httpx.Request('GET',plan.STAGING_URL+'/rest/v1/documents?'+query))
        self.assertEqual(self.calls,[])

    def test_header_range_schema_or_bearer_changes_stop_before_wire(self):
        for header,value in [('Range','0-0'),('Accept-Profile','private'),('Authorization','Bearer other')]:
            self.setUp();b=self.boundary();req=wire();req.headers[header]=value
            with self.assertRaises(GeneralValidationDenied):b.handle_request(req)
            self.assertEqual(self.calls,[]);b.close()

    def test_cancel_expiry_binding_or_local_change_prevent_send(self):
        for kind in ('cancel','expiry','binding','local'):
            self.setUp();b=self.boundary()
            if kind=='cancel':self.authority.cancel()
            if kind=='expiry':self.now=300
            if kind=='binding':self.current=False
            if kind=='local':self.local_ok=False
            with self.assertRaises(GeneralValidationDenied):b.handle_request(wire())
            self.assertEqual(self.calls,[]);b.close()

    def test_revocation_in_persist_callback_stops_before_wire(self):
        def persist(event):self.attempts.append(event);self.authority.cancel()
        b=self.boundary(persist=persist)
        with self.assertRaises(GeneralValidationDenied):b.handle_request(wire())
        self.assertEqual(self.calls,[]);self.assertEqual(len(self.attempts),1)

    def test_durable_attempt_failure_stops_before_wire(self):
        def fail(_):raise OSError('synthetic disk full')
        b=self.boundary(persist=fail)
        with self.assertRaises(OSError):b.handle_request(wire())
        with self.assertRaises(GeneralValidationDenied):b.handle_request(wire())
        self.assertEqual(self.calls,[])

    def test_response_loss_consumes_budget_and_cannot_retry(self):
        def lost(req):self.calls.append(req);raise httpx.ReadTimeout('synthetic lost response')
        b=self.boundary(server=lost)
        with self.assertRaises(httpx.ReadTimeout):b.handle_request(wire())
        with self.assertRaises(GeneralValidationDenied):b.handle_request(wire())
        self.assertEqual(len(self.calls),1);self.assertEqual(b.consumed,1)

    def test_late_response_after_auth_change_is_not_accepted(self):
        def late(req):response=self.server(req);self.current=False;return response
        b=self.boundary(server=late)
        with self.assertRaises(GeneralValidationDenied):b.handle_request(wire())
        self.assertEqual(len(self.calls),1)

    def test_wrong_revision_replay_or_partial_receipt_ends_scope(self):
        for kind in ('revision','replayed','partial'):
            self.setUp()
            def bad(req):
                self.calls.append(req);r=success(json.loads(req.content)['p_request'])
                if kind=='revision':r['results'][0]['result_revision']=99
                if kind=='replayed':r['status']='replayed'
                if kind=='partial':r['results']=[]
                return httpx.Response(200,json=r)
            b=self.boundary(server=bad)
            with self.assertRaises(Exception):b.handle_request(wire())
            with self.assertRaises(GeneralValidationDenied):b.handle_request(wire())
            self.assertEqual(len(self.calls),1);b.close()

    def test_initial_create_and_order_must_follow_reviewed_sequence(self):
        first,second=wire(),wire(plan.order_request(DEVICE))
        b=self.boundary([first,second])
        with self.assertRaises(GeneralValidationDenied):b.handle_request(second)
        self.assertEqual(self.calls,[])
        b=self.boundary([first,second]);b.handle_request(first);b.handle_request(second)
        self.assertEqual(len(self.calls),2)

    def test_durable_reservation_survives_restart_after_unknown_response(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fixed-plan-windows-create.jsonl'
            journal=DurableExecutionJournal(path)
            def lost(req):self.calls.append(req);raise httpx.ReadTimeout('synthetic')
            b=self.boundary(server=lost,persist=journal.append)
            try:
                with self.assertRaises(httpx.ReadTimeout):b.handle_request(wire())
            finally:journal.close()
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r['event'] for r in rows],['reserved','http_attempt'])
            self.assertNotIn(TOKEN,path.read_text())
            with self.assertRaises(FileExistsError):DurableExecutionJournal(path)
            self.assertEqual(len(self.calls),1)

    def test_wrong_endpoint_or_encoded_path_never_reaches_wire(self):
        for url in ('https://other.invalid/rest/v1/documents',
                    plan.STAGING_URL+'/rest/v1/%64ocuments',
                    plan.STAGING_URL+'/rest/v1/documents#fragment'):
            with self.subTest(url=url),self.assertRaises(GeneralValidationDenied):
                FrozenRequest.capture(httpx.Request('GET',url,params={'select':'*','project_id':'eq.'+plan.PROJECT_ID}))
        self.assertEqual(self.calls,[])

    def test_order_failure_retains_prior_create_and_stops(self):
        first,second=wire(),wire(plan.order_request(DEVICE))
        def failed_order(req):
            if req.url.path.endswith('/atomic_structure_commit'):
                self.calls.append(req);return httpx.Response(409,json={'code':'synthetic conflict'})
            return self.server(req)
        b=self.boundary([first,second],server=failed_order)
        b.handle_request(first)
        with self.assertRaises(GeneralValidationDenied):b.handle_request(second)
        with self.assertRaises(GeneralValidationDenied):b.handle_request(first)
        self.assertEqual(len(self.calls),2);self.assertEqual(len(self.attempts),2)

    def test_real_store_builds_contract_request_and_preserves_other_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            store=SyncV2Store(str(Path(folder)/'sync.sqlite3'))
            context=store.configure_project(str(Path(folder)/'selected'),plan.PROJECT_NAME,plan.PROJECT_ID)
            other=store.configure_project(str(Path(folder)/'other'),'other',str(uuid4()))
            other_op=store.enqueue(other,'other.txt','unchanged pending content')
            key=context['local_key']
            with store._transaction() as c:
                c.execute("UPDATE sync_projects SET project_sync_mode='MIGRATING',migration_epoch=1 WHERE local_key=?",(key,))
                c.execute("UPDATE sync_projects SET project_sync_mode='ID_BASED',migration_epoch=1 WHERE local_key=?",(key,))
            self.assertEqual(check_store_binding(store,key)['contract_path_enabled'],0)
            with self.assertRaises(GeneralValidationDenied):check_store_binding(store,other['local_key'])
            store.set_contract_path_enabled(key,True)  # Synthetic DB only.
            store.replace_folder_snapshots(key,[{'folder_id':plan.PARENT_ID,'parent_folder_id':None,
                'name':'원고','local_path':'메인/원고','revision':1,'is_deleted':False}])
            store.ensure_document(key,plan.PATH,'',plan.DOCUMENT_ID)
            context['writer_device_id']=DEVICE
            op=store.enqueue(context,plan.PATH,plan.BASE,relative_path=plan.PATH)
            self.assertEqual(op['provenance_kind'],'CONTRACT_BATCH')
            request=store.structure_batch_request(op['batch_id'])
            frozen=FrozenRequest.capture(wire(request))
            b=self.boundary([wire(request)])
            b.handle_request(wire(request))
            self.assertEqual(frozen.method,'POST')
            self.assertEqual(store.operation(other_op['operation_id']),other_op)
            self.assertEqual(store.get_document_by_id(plan.DOCUMENT_ID)['revision'],0)
            # Transport acceptance alone does not apply a receipt or drain queues.
            self.assertEqual(store.operation(op['operation_id'])['status'],'pending')

    def test_real_store_order_request_keeps_generated_ids_and_all_database_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            store=SyncV2Store(str(Path(folder)/'sync.sqlite3'))
            context=store.configure_project(str(Path(folder)/'selected'),plan.PROJECT_NAME,plan.PROJECT_ID)
            other=store.configure_project(str(Path(folder)/'other'),'other',str(uuid4()))
            store.enqueue(other,'other.txt','keep this pending')
            key=context['local_key']
            with store._transaction() as c:
                c.execute("UPDATE sync_projects SET project_sync_mode='MIGRATING',migration_epoch=1 WHERE local_key=?",(key,))
                c.execute("UPDATE sync_projects SET project_sync_mode='ID_BASED',migration_epoch=1 WHERE local_key=?",(key,))
            store.set_contract_path_enabled(key,True)  # Only this synthetic fixture.
            store.activate_contract_project(key,project_sync_mode='ID_BASED',migration_epoch=1,
                server_protocol_version=3,server_contract_sha256=CANONICAL_CONTRACT_SHA256,
                server_capabilities=SERVER_CAPABILITIES)
            intent=copy.deepcopy(plan.order_request(DEVICE)['ordered_intents'][0])
            del intent['operation_id']  # Let the actual store/builder allocate it.
            request=store.create_structure_batch(context,DEVICE,[intent])
            actual=store.structure_batch_request(request['batch']['batch_id'])
            self.assertEqual(request,actual)
            self.assertNotEqual(request['batch']['batch_id'],plan.identifier('windows_order/batch'))
            self.assertNotEqual(request['ordered_intents'][0]['operation_id'],plan.identifier('windows_order/operation'))
            self.assertNotEqual(request['batch']['client_build_id'],plan.order_request(DEVICE)['batch']['client_build_id'])
            def rows():
                with store._reader() as c:
                    tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    return {t:sorted(repr(tuple(r)) for r in c.execute('SELECT * FROM "'+t.replace('"','""')+'"')) for t in tables}
            before=rows()
            b=self.boundary([wire(actual)])
            b.handle_request(wire(actual))
            self.assertEqual(json.loads(self.calls[0].content)['p_request'],actual)
            self.assertEqual(rows(),before)

    def test_order_runtime_ids_are_frozen_before_wire_and_cannot_be_replaced(self):
        request=plan.order_request(DEVICE,operation_id=str(uuid4()),batch_id=str(uuid4()),client_build_id='synthetic-runtime-build')
        b=self.boundary([wire(request)])
        changed=plan.order_request(DEVICE,operation_id=str(uuid4()),batch_id=str(uuid4()),client_build_id='synthetic-runtime-build')
        with self.assertRaises(GeneralValidationDenied):b.handle_request(wire(changed))
        self.assertEqual(self.calls,[])

    def test_order_runtime_identifiers_do_not_relax_payload_or_contract_checks(self):
        request=plan.order_request(DEVICE,operation_id=str(uuid4()),batch_id=str(uuid4()),client_build_id='synthetic-runtime-build')
        changes=[('entity_id',str(uuid4())),('base_revision',2),('intent_kind','delete')]
        for key,value in changes:
            changed=copy.deepcopy(request);changed['ordered_intents'][0][key]=value
            with self.subTest(field=key),self.assertRaises(GeneralValidationDenied):FrozenRequest.capture(wire(changed))
        for key,value in [('parent_folder_id',str(uuid4())),('children',[str(uuid4())])]:
            changed=copy.deepcopy(request);changed['ordered_intents'][0]['payload'][key]=value
            with self.subTest(field=key),self.assertRaises(GeneralValidationDenied):FrozenRequest.capture(wire(changed))
        changed=copy.deepcopy(request);changed['batch']['canonical_contract_sha256']='0'*64
        with self.assertRaises(GeneralValidationDenied):FrozenRequest.capture(wire(changed))


if __name__=='__main__':unittest.main()
