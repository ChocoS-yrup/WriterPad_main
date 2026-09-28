"""Fresh one-action Auth boundary; no dispatcher and no automatic retransmit."""
from contextlib import contextmanager
import json
from pathlib import Path
from uuid import uuid4
import httpx

import normal_editor_plan as plan
from body_validation_transport import strict_json
from general_validation_boundary import FrozenRequest, DurableExecutionJournal, require_exact_result
from general_validation_service import GeneralValidationService, TABLES, validate_snapshot, require
from sync_contract import build_document_commit_request, json_sha256, require_uuid, validate_document_commit_response

BATCH_COLUMNS = 'batch_id,project_id,writer_user_id,writer_device_id,client_build_id,sync_protocol_version,contract_version,canonical_contract_sha256,client_capabilities,batch_payload_sha256,project_sync_mode,migration_epoch,request_sha256'
RESULT_COLUMNS = 'batch_id,applied,response,response_sha256'


class NormalRequest(FrozenRequest):
    def validate_scope(self):
        url = httpx.URL(self.url)
        if self.method=='GET' and url.path in ('/rest/v1/sync_batches','/rest/v1/sync_batch_results'):
            require(str(url).split('?')[0]==plan.STAGING_URL+url.path and not self.body,'NORMAL_RECEIPT_ENDPOINT_CHANGED')
            params = list(url.params.multi_items())
            batch = url.params.get('batch_id','').removeprefix('eq.')
            require(require_uuid(batch,'batch_id')==batch,'NORMAL_RECEIPT_BATCH_CHANGED')
            expected = [('select',BATCH_COLUMNS if url.path.endswith('/sync_batches') else RESULT_COLUMNS),
                ('batch_id','eq.'+batch),('limit','2')]
            if url.path.endswith('/sync_batches'):expected.append(('project_id','eq.'+plan.PROJECT_ID))
            require(sorted(params)==sorted(expected),'NORMAL_RECEIPT_QUERY_CHANGED')
            return
        if self.method=='GET' or url.path=='/rest/v1/rpc/get_sync_handshake':
            return super().validate_scope()
        require(self.method=='POST' and str(url)==plan.STAGING_URL+'/rest/v1/rpc/document_commit',
            'NORMAL_RPC_REFUSED')
        wrapper = strict_json(self.body)
        require(isinstance(wrapper,dict) and set(wrapper)=={'p_request'},'NORMAL_WRAPPER_CHANGED')
        request = wrapper['p_request']
        require(isinstance(request,dict) and isinstance(request.get('ordered_intents'),list)
            and len(request['ordered_intents'])==1,'NORMAL_INTENT_CHANGED')
        intent,batch = request['ordered_intents'][0],request.get('batch',{})
        require(isinstance(intent,dict) and isinstance(batch,dict),'NORMAL_CONTRACT_CHANGED')
        revision = intent.get('base_revision')
        require(type(revision) is int and revision>=6,'NORMAL_REVISION_CHANGED')
        content = intent.get('payload',{}).get('content')
        plan.validate_text(content)
        expected = build_document_commit_request(project_id=plan.PROJECT_ID,project_sync_mode='ID_BASED',migration_epoch=1,
            writer_device_id=batch.get('writer_device_id'),document_id=plan.DOCUMENT_ID,intent_kind='update',
            base_revision=revision,parent_folder_id=plan.PARENT_ID,name=plan.NAME,content=content,
            is_deleted=False,structure_revision=1,operation_id=intent.get('operation_id'),
            batch_id=batch.get('batch_id'),client_build_id=batch.get('client_build_id'))
        require(json_sha256(request)==json_sha256(expected),'NORMAL_PAYLOAD_CHANGED')


class NormalNetworkService(GeneralValidationService):
    request_class = NormalRequest
    plan = plan

    def __init__(self, *, local, **kwargs):
        self.local = local
        super().__init__(store=local.store,wpm=local.wpm,journal_dir=local.directory/'network',**kwargs)
        self.journal_dir.mkdir(exist_ok=True)

    def _gates(self, writing):
        require(not writing,'NORMAL_NETWORK_WITH_OPEN_GATE')
        self.authority.check()
        require(not self.local.pending_controls(),'CONTROL_RESTORE_REQUIRED')
        self.local.check()

    @contextmanager
    def action(self,name):
        with self.local.lock:
            require(not self.local.network_busy,'NORMAL_BUSY')
            self.local.network_busy = True
        try:
            self._gates(False)
            self.journal = DurableExecutionJournal(self.journal_dir/(name+'-'+str(uuid4())+'.jsonl'))
            try:
                self._handshake()
                yield
                self.journal.append({'event':'completed','outcome':name})
            except Exception:
                self.journal.append({'event':'stopped','outcome':'preserve_and_recover'})
                raise
            finally:
                self.journal.close()
        finally:
            self.local.network_busy = False

    def snapshot(self,label):
        requests = [httpx.Request('GET',plan.STAGING_URL+'/rest/v1/'+table,
            params={'select':'*','project_id':'eq.'+plan.PROJECT_ID}) for table in TABLES]
        self._arm(label,requests)
        rows = {t:self.client.table(t).select('*').eq('project_id',plan.PROJECT_ID).execute().data for t in TABLES}
        docs = [r for r in rows['documents'] if r.get('document_id')==plan.DOCUMENT_ID]
        require(len(docs)==1,'NORMAL_REMOTE_DOCUMENT_CHANGED')
        doc = docs[0]
        require(type(doc.get('revision')) is int and doc['revision']>=6,'NORMAL_REMOTE_REVISION_CHANGED')
        plan.validate_text(doc.get('content'))
        validate_snapshot(rows,doc['revision'],self.account_id,
            plan=plan.snapshot_plan(doc['revision'],doc['content']))
        return doc

    def send(self):
        require(not self.local.pending_receive(),'NORMAL_RECEIVE_RECOVERY_REQUIRED')
        op = self.local.owned_operation()
        require(op is not None and op['status'] in ('pending','retry_wait') and self.no_write_attempt(op),
            'NORMAL_NO_UNATTEMPTED_OPERATION')
        require(not any(r['event']=='conflict' and r['operation_id']==op['operation_id']
            for r in self.local.log.records()),'NORMAL_CONFLICT_REQUIRES_REVIEW')
        request = self.store.structure_batch_request(op['batch_id'])
        NormalRequest.capture(httpx.Request('POST',plan.STAGING_URL+'/rest/v1/rpc/document_commit',json={'p_request':request}))
        with self.action('send'):
            before = self.snapshot('before')
            if before['revision']!=op['base_revision'] or before['content']!=op['base_content']:
                self.local.log.append('conflict',operation_id=op['operation_id'],base=op['base_content'],
                    local=op['content'],remote=before['content'],remote_revision=before['revision'])
                require(False,'NORMAL_REMOTE_CONFLICT')
            require(self.local.owned_operation()==op,'NORMAL_OPERATION_CHANGED')
            self.authority.check()
            self.local.log.append('dispatch_intent',operation_id=op['operation_id'],request=request,account_id=self.account_id,
                journal=self.journal.path.name)
            self.store.mark_attempt(op['operation_id'])
            response = self._commit(request)
            # Preserve an accepted response before changing local receipt/metadata.
            self.local.log.append('response',operation_id=op['operation_id'],response=response)
            self.complete_response(op,request,response)
            return self.result(op['base_revision']+1,op['content'])

    def complete_response(self,op,request,response):
        validate_document_commit_response(request,response)
        require(response.get('applied') is True and response.get('status') in ('committed','replayed')
            and type(response['results'][0]['result_revision']) is int
            and response['results'][0]['result_revision']==op['base_revision']+1,'NORMAL_RECEIPT_REVISION_CHANGED')
        self.local.check()
        self.authority.check()
        with self.store._transaction():
            self.store.record_document_batch_response(op['batch_id'],response)
            self.store.mark_success(op['operation_id'],{'revision':op['base_revision']+1,'content_hash':plan.digest(op['content']),
                'status':response['status'],'parent_folder_id':plan.PARENT_ID,'name':plan.NAME,'structure_revision':1})
        self.local.check()
        self.local.log.append('sent',operation_id=op['operation_id'],revision=op['base_revision']+1,content=op['content'])

    def receive(self):
        require(not self.local.pending_receive(),'NORMAL_RECEIVE_RECOVERY_REQUIRED')
        require(not self.local.active() and self.local.draft()==self.local.expected_disk
            and self.local.expected_disk==self.local.document()['base_content'],'NORMAL_RECEIVE_HAS_LOCAL_WORK')
        with self.action('receive'):
            remote = self.snapshot('receive')
            current = self.local.document()
            require(remote['revision']>=current['revision'],'NORMAL_REMOTE_REVISION_ROLLBACK')
            if remote['revision']==current['revision']:
                require(remote['content']==current['base_content'],'NORMAL_SAME_REVISION_DIFFERENT_BODY')
                return self.result(current['revision'],current['base_content'])
            self.authority.check()
            row = self.local.log.append('receive_intent',receive_id=str(uuid4()),base_revision=current['revision'],
                before=current['base_content'],revision=remote['revision'],content=remote['content'])
            self.authority.check()
            self.local.finish_receive(row)
            return self.result(remote['revision'],remote['content'])

    def recover(self):
        """Use an already validated receipt, never invoke document_commit again."""
        op = self.local.owned_operation()
        require(op is not None and op['status']=='inflight','NORMAL_NO_UNCERTAIN_OPERATION')
        request = self.store.structure_batch_request(op['batch_id'])
        dispatch = next((r for r in reversed(self.local.log.records()) if r['event']=='dispatch_intent'
            and r['operation_id']==op['operation_id']),None)
        require(dispatch is not None and dispatch.get('account_id')==self.account_id
            and dispatch['request']==request,'NORMAL_RECOVERY_ACCOUNT_OR_REQUEST_CHANGED')
        with self.action('recover'):
            if self.no_write_attempt(op):
                self.store.mark_retry(op['operation_id'],'NORMAL_HTTP_NOT_STARTED')
                self.local.log.append('http_not_started',operation_id=op['operation_id'])
                return dict(self.result(op['base_revision'],op['base_content']),
                    note='전송 시작 전 중단을 확인했습니다. 같은 변경을 송신할 수 있습니다.')
            row = next((r for r in reversed(self.local.log.records()) if r['event']=='response'
                and r['operation_id']==op['operation_id']),None)
            response = row['response'] if row else self.fetch_receipt(request)
            if row is None:self.local.log.append('response',operation_id=op['operation_id'],response=response)
            self.complete_response(op,request,response)
            return self.result(op['base_revision']+1,op['content'])

    def no_write_attempt(self,op):
        dispatches = [r for r in self.local.log.records() if r['event']=='dispatch_intent'
            and r['operation_id']==op['operation_id']]
        if not dispatches:return op['attempts']==0
        for row in dispatches:
            name = row.get('journal','')
            require(name and Path(name).name==name,'NORMAL_DISPATCH_LOG_CHANGED')
            path = self.journal_dir/name
            require(path.is_file() and path.resolve()==path.absolute(),'NORMAL_DISPATCH_LOG_MISSING')
            events = [json.loads(line) for line in path.read_text('utf-8').splitlines()]
            require(events and events[0]=={'event':'reserved'},'NORMAL_DISPATCH_LOG_CHANGED')
            if any(e.get('event')=='http_attempt' and e.get('path')=='/rest/v1/rpc/document_commit' for e in events):
                return False
        return True

    def fetch_receipt(self,request):
        batch = request['batch']
        def fetch(table,columns,project=False):
            params = [('select',columns)]
            if project:params.append(('project_id','eq.'+plan.PROJECT_ID))
            params += [('batch_id','eq.'+batch['batch_id']),('limit','2')]
            self._arm(table,[httpx.Request('GET',plan.STAGING_URL+'/rest/v1/'+table,params=params)])
            query = self.client.table(table).select(columns,count='exact')
            if project:query = query.eq('project_id',plan.PROJECT_ID)
            result = query.eq('batch_id',batch['batch_id']).limit(2).execute()
            require(isinstance(result.data,list) and type(result.count) is int
                and result.count==len(result.data) and result.count in (0,1),'NORMAL_RECEIPT_INCOMPLETE')
            require(result.count==1,'NORMAL_RECEIPT_NOT_FOUND')
            return result.data[0]
        row = fetch('sync_batches',BATCH_COLUMNS,True)
        expected = dict(batch,project_id=plan.PROJECT_ID,writer_user_id=self.account_id,
            project_sync_mode='ID_BASED',migration_epoch=1,request_sha256=json_sha256(request))
        require(json_sha256(row)==json_sha256(expected),'NORMAL_RECEIPT_REQUEST_CHANGED')
        result = fetch('sync_batch_results',RESULT_COLUMNS)
        require(set(result)==set(RESULT_COLUMNS.split(',')) and result['batch_id']==batch['batch_id']
            and result['applied'] is True and result['response_sha256']==json_sha256(result['response']),
            'NORMAL_RECEIPT_RESPONSE_CHANGED')
        return result['response']

    @staticmethod
    def result(revision,content):
        return dict(revision=revision,bytes=len(content.encode('utf-8')),sha256=plan.digest(content))
