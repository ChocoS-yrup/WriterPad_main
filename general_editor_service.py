"""Editor save -> durable contract queue -> separately authenticated single send."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace, MethodType
from uuid import uuid4

import general_editor_plan as plan
from general_editor_boundary import EditorRequest
from general_validation_service import GeneralValidationService, require
from general_validation_boundary import DurableExecutionJournal, GeneralValidationDenied
from general_validation_control import TemporaryWriteScope, pending_controls


def error_code(error):
    from bidirectional_sync_scope import ScopeDenied
    if isinstance(error,(GeneralValidationDenied,ScopeDenied)):
        return str(error)[:160]
    if isinstance(error,OSError) and error.errno is not None:
        return type(error).__name__+':errno='+str(error.errno)
    return type(error).__name__


def journal_path(directory, action):
    require(action in ('manual_save','manual_send','auto_receive','auto_save','auto_send'), 'EDITOR_ACTION_CHANGED')
    return Path(directory)/(plan.PLAN_ID+'-'+action+'.jsonl')


def completed(directory, action):
    path = journal_path(directory,action)
    if not path.exists():
        return False
    events = [json.loads(s) for s in path.read_text('utf-8').splitlines()]
    require(bool(events) and events[-1].get('event')=='completed', 'EDITOR_PRIOR_ATTEMPT_REQUIRES_REVIEW')
    return True


def available_action(directory):
    if pending_controls(directory,plan_id=plan.PLAN_ID):
        return 'restore'
    try:
        done = {a:completed(directory,a) for a in ('manual_save','manual_send','auto_receive','auto_save','auto_send')}
        require(not done['manual_send'] or done['manual_save'], 'EDITOR_STAGE_GAP')
        require(not done['auto_receive'] or done['manual_send'], 'EDITOR_STAGE_GAP')
        require(not done['auto_save'] or done['auto_receive'], 'EDITOR_STAGE_GAP')
        require(not done['auto_send'] or done['auto_save'], 'EDITOR_STAGE_GAP')
        if not done['manual_save']: return 'manual_prepare'
        if not done['manual_send']: return 'manual_send'
        if not done['auto_save']: return 'auto_prepare'
        if not done['auto_send']: return 'auto_send'
        return 'done'
    except (ValueError,OSError,GeneralValidationDenied):
        return 'review'


class EditorService(GeneralValidationService):
    plan = plan
    request_class = EditorRequest

    def _gates(self, writing):
        super()._gates(writing)
        from general_test_gate import writes_held
        require(writes_held(SimpleNamespace(_v2_context=self.context,_v2_store=self.store)), 'EDITOR_GLOBAL_HOLD_REQUIRED')
        if not writing:
            require(self.store.get_project(self.key)['contract_path_enabled']==0, 'EDITOR_CLOSED_GATE_REQUIRED')

    def _start(self, action):
        self._gates(False)
        require(not pending_controls(self.journal_dir,plan_id=plan.PLAN_ID), 'CONTROL_RESTORE_REQUIRED')
        require(available_action(self.journal_dir)==action, 'EDITOR_STAGE_NOT_AVAILABLE')

    def _record_failure(self, error):
        self.ticket.cancel()
        self.journal.append({'event':'stopped','outcome':error_code(error)})

    def prepare(self, stage_name):
        stage = plan.WINDOWS_STAGES[stage_name]
        self._start(stage_name+'_prepare')
        # Read-only preparations may get fresh grants; each attempt is retained.
        self.journal = DurableExecutionJournal(self.journal_dir/(plan.PLAN_ID+'-'+stage_name+'-prepare-'+str(uuid4())+'.jsonl'))
        try:
            received = stage_name=='auto' and completed(self.journal_dir,'auto_receive')
            initial = 5 if received else (4 if stage_name=='auto' else 3)
            self._local(initial)
            self._handshake()
            self._snapshot('before',stage.base_revision)
            if stage_name=='auto' and not received:
                preparation = self.journal
                self.journal = DurableExecutionJournal(journal_path(self.journal_dir,'auto_receive'))
                try:
                    self._replace_body(4,plan.IPAD)
                    self.journal.append({'event':'receive_file_saved','outcome':'5'})
                    self.authority.check()
                    applied = self.store.apply_remote_snapshot(self.context,plan.DOCUMENT_ID,plan.PATH,plan.IPAD,5,
                        local_path=plan.PATH,parent_folder_id=plan.PARENT_ID,name=plan.NAME,structure_revision=1)
                    require(applied.get('applied') is True,'EDITOR_RECEIVE_NOT_APPLIED')
                    self._local(5)
                    self.journal.append({'event':'completed','outcome':'5'})
                except Exception as error:
                    self._record_failure(error)
                    raise
                finally:
                    self.journal.close()
                    self.journal = preparation
            self._local(stage.base_revision)
            self.edit_fingerprint = self._fingerprint()
            self.prepared_stage = stage_name
            self.journal.append({'event':'completed','outcome':'editor_ready'})
            return plan.STAGES[stage.base_revision-1].content
        except Exception as error:
            self._record_failure(error)
            raise
        finally:
            self.journal.close()

    def save(self, stage_name, text, save_callback):
        """Invoke the real manual/idle save path with guarded product adapters."""
        require(getattr(self,'prepared_stage',None)==stage_name, 'EDITOR_PREPARE_REQUIRED')
        self._start(stage_name+'_prepare')
        stage = plan.WINDOWS_STAGES[stage_name]
        require(text==stage.content, 'EDITOR_TEXT_NOT_AS_AGREED')
        self.authority.check()
        require(self._fingerprint()==self.edit_fingerprint, 'EDITOR_BASELINE_CHANGED')
        self.journal = DurableExecutionJournal(journal_path(self.journal_dir,stage_name+'_save'))
        self.control_scope = TemporaryWriteScope(store=self.store,local_key=self.key,directory=self.journal_dir,
            stage=stage_name,plan_id=plan.PLAN_ID,guard=lambda:self._local(stage.base_revision))
        bridge = None
        try:
            try:
                self.control_scope.open()
                self.write_action = True
                self.journal.append({'event':'local_gate_opened','outcome':'global_hold_unchanged'})
                bridge = SaveBridge(self,stage)
                save_callback(bridge.files,bridge.manager)
                if bridge.failure is not None:
                    raise bridge.failure
                require(bridge.operation is not None, 'EDITOR_QUEUE_NOT_CREATED')
                self._local(stage.base_revision,active=[bridge.operation['operation_id']],disk=text)
            finally:
                self.control_scope.restore()
                self.write_action = False
                self.journal.append({'event':'local_gate_restored','outcome':'global_hold_unchanged'})
            self.authority.check()
            self.journal.append({'event':'completed','outcome':bridge.operation['operation_id']})
            return bridge.operation
        except Exception as error:
            self._record_failure(error)
            raise
        finally:
            self.journal.close()
            self.prepared_stage = None

    def send(self, stage_name):
        self._start(stage_name+'_send')
        stage = plan.WINDOWS_STAGES[stage_name]
        events = [json.loads(s) for s in journal_path(self.journal_dir,stage_name+'_save').read_text('utf-8').splitlines()]
        op_id = events[-1]['outcome']
        op = self.store.operation(op_id)
        require(op and op['provenance_kind']=='CONTRACT_BATCH' and op['local_key']==self.key
            and op['document_id']==plan.DOCUMENT_ID and op['base_revision']==stage.base_revision
            and op['content']==stage.content, 'EDITOR_SAVED_OPERATION_CHANGED')
        request = self.store.structure_batch_request(op['batch_id'])
        require(request['batch']['writer_device_id']==self.device_id,'EDITOR_WRITER_DEVICE_CHANGED')
        import httpx
        EditorRequest.capture(httpx.Request('POST',plan.STAGING_URL+'/rest/v1/rpc/document_commit',json={'p_request':request}))
        self._local(stage.base_revision,active=[op_id],disk=stage.content)
        require(self.store.next_ready_operation(self.key)['operation_id']==op_id,'EDITOR_OPERATION_NOT_READY')
        self.journal = DurableExecutionJournal(journal_path(self.journal_dir,stage_name+'_send'))
        try:
            self._handshake()
            self._snapshot('before',stage.base_revision)
            self._local(stage.base_revision,active=[op_id],disk=stage.content)
            self.store.mark_attempt(op_id)
            response = self._commit(request)
            self.authority.check()
            with self.store._transaction():
                self.store.record_document_batch_response(op['batch_id'],response)
                self.store.mark_success(op_id,{'revision':stage.result_revision,'content_hash':plan.digest(stage.content),
                    'status':'committed','parent_folder_id':plan.PARENT_ID,'name':plan.NAME,'structure_revision':1})
            self.journal.append({'event':'body_committed','outcome':str(stage.result_revision)})
            self._snapshot('after',stage.result_revision)
            result = self._complete(stage.result_revision)
            self.journal.append({'event':'completed','outcome':str(stage.result_revision)})
            return result
        except Exception as error:
            self._record_failure(error)
            raise
        finally:
            self.journal.close()


def retain_save_error(method):
    def guarded(self,*args,**kwargs):
        try:
            return method(self,*args,**kwargs)
        except Exception as error:
            self.failure = error
            raise
    return guarded


class SaveBridge:
    """Same product upload method, with a guarded file/store and no dispatcher."""
    def __init__(self, service, stage):
        from sync_manager import SyncManager
        self.service,self.stage = service,stage
        self.written,self.operation = False,None
        self.failure = None
        self.baseline = service._fingerprint()
        self.files = SimpleNamespace(write_text_file=self.write)
        self.manager = SimpleNamespace(is_v2_enabled=True,_v2_store=self,_v2_context=service.context,
            _v2_untracked_recovery_paths=set(),_v2_callbacks={},_v2_conflict_callbacks={},_v2_worker=None,
            _publish_sync_state=lambda:None, upload_autosave_async=self.backup_deferred,
            report_server_queue_failure=self.queue_failed)
        for name in ('can_save_path','would_erase_nonempty_document','upload_content_async','retry_pending_syncs'):
            setattr(self.manager,name,MethodType(getattr(SyncManager,name),self.manager))
        self.manager._tree_path_comparison_key = SyncManager._tree_path_comparison_key

    def __getattr__(self,name):
        return getattr(self.service.store,name)

    def guard(self, owned_temp=None):
        service,stage = self.service,self.stage
        service._local(stage.base_revision,disk=stage.content if self.written else None,owned_temp=owned_temp)
        current = service._fingerprint()
        if owned_temp is not None:
            relative = str(Path(owned_temp).relative_to(service.root.parent))
            current = ([r for r in current[0] if r[0]!=relative],current[1],current[2])
        require(current==self.baseline,'EDITOR_SAVE_BASELINE_CHANGED')

    @retain_save_error
    def write(self,path,text):
        require(path==plan.PATH and text==self.stage.content and self.operation is None,'EDITOR_WRITE_SCOPE_CHANGED')
        self.guard()
        if not self.written:
            old = plan.STAGES[self.stage.base_revision-1].content.encode('utf-8')
            self.service.wpm.write_text_file(path,text,expected_bytes=old,guard=self.guard,temporary_guard=self.guard)
            self.written = True
            # Only the known body file may differ after the atomic save.
            files,rows,hold = self.baseline
            relative = str((self.service.root/path).relative_to(self.service.root.parent))
            self.baseline = ([(p,hashlib.sha256(text.encode()).hexdigest() if p==relative else sha) for p,sha in files],rows,hold)
            self.service.journal.append({'event':'local_file_saved','outcome':str(self.stage.base_revision)})
            self.guard()
        return True

    @retain_save_error
    def enqueue(self,context,path,text,**kwargs):
        require(context==self.service.context and path==plan.PATH and text==self.stage.content
            and kwargs=={'relative_path':plan.PATH} and self.written and self.operation is None,'EDITOR_QUEUE_SCOPE_CHANGED')
        with self.service.store._transaction():
            self.guard()
            operation = self.service.store.enqueue(context,path,text,**kwargs)
            require(operation['provenance_kind']=='CONTRACT_BATCH','EDITOR_CONTRACT_QUEUE_REQUIRED')
        self.operation = operation
        self.service.journal.append({'event':'queued','outcome':operation['operation_id']})
        return operation

    def backup_deferred(self,*args,**kwargs):
        # The fixed editor trial preserves its source backup; no extra backup worker.
        self.service.journal.append({'event':'backup_worker_suppressed'})

    def queue_failed(self,path,error):
        raise error
