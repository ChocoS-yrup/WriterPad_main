"""Offline manuscript persistence and queue ownership, independent of Auth.

Append-only recovery files are private: they contain manuscript snapshots.
No network client, global dispatcher or implicit startup recovery exists here.
"""
import hashlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import normal_editor_plan as plan
from general_validation_service import GeneralValidationService, require
from general_validation_boundary import check_store_binding
from general_validation_control import TemporaryWriteScope, pending_controls, restore_pending_controls
from sync_v2_store import CONTRACT_ACTIVE_STATES


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


class RecoveryLog:
    def __init__(self, directory):
        self.root = Path(directory).absolute()
        self.root.mkdir(parents=True,exist_ok=True)
        require(self.root.resolve()==self.root and not self.root.is_symlink(),'NORMAL_LOG_PATH_CHANGED')
        self.lock = threading.RLock()
        self.records()  # A torn or replaced record blocks later work; never truncate it.

    def records(self):
        result,previous = [],''
        for sequence,path in enumerate(sorted(self.root.glob('*.json')),1):
            require(path.name==f'{sequence:08}.json' and not path.is_symlink()
                and path.resolve()==path.absolute(),'NORMAL_LOG_SEQUENCE_CHANGED')
            raw = path.read_bytes()
            value = json.loads(raw)
            require(value.get('previous')==previous and value.get('sequence')==sequence,
                'NORMAL_LOG_CHAIN_CHANGED')
            require(value.get('sha256')==hashlib.sha256(canonical(value['data'])).hexdigest(),
                'NORMAL_LOG_HASH_CHANGED')
            result.append(value['data'])
            previous = hashlib.sha256(raw).hexdigest()
        return result

    def append(self, event, **data):
        with self.lock:
            records = self.records()
            sequence = len(records)+1
            previous = hashlib.sha256((self.root/f'{sequence-1:08}.json').read_bytes()).hexdigest() if records else ''
            data = dict(data,event=event)
            value = dict(sequence=sequence,previous=previous,data=data,sha256=hashlib.sha256(canonical(data)).hexdigest())
            with (self.root/f'{sequence:08}.json').open('xb') as stream:
                stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
            return data

    def latest(self,event):
        return next((r for r in reversed(self.records()) if r['event']==event),None)


class OfflineInspector(GeneralValidationService):
    def __init__(self, local):
        self.owner = local
        self.store,self.wpm,self.key,self.root = local.store,local.wpm,local.key,local.root
        self.context = local.context
        self.control_scope = None
        self.write_action = False

    def _gates(self, writing):
        local = self.owner
        project = check_store_binding(self.store,self.key)
        require(project['project_name']==plan.PROJECT_NAME and project['server_state']=='active',
            'NORMAL_PROJECT_CHANGED')
        require(local.hold.read_bytes()==local.hold_bytes,'NORMAL_HOLD_CHANGED')
        with self.store._reader() as conn:
            require(not conn.execute('SELECT 1 FROM sync_projects WHERE local_key<>? AND contract_path_enabled<>0',
                (self.key,)).fetchone(),'OTHER_PROJECT_GATE_OPEN')
        if writing:
            require(self.control_scope is not None,'NORMAL_CONTROL_REQUIRED')
            self.control_scope.check()
        else:
            require(project['contract_path_enabled']==0,'NORMAL_CLOSED_GATE_REQUIRED')


class LocalEditor:
    def __init__(self, *, store, wpm, directory, device_id):
        self.store,self.wpm = store,wpm
        self.root = Path(wpm.writing_root_path).absolute()
        self.key = store.local_key_for(str(self.root))
        self.context = dict(check_store_binding(store,self.key),writer_device_id=device_id)
        self.device_id = device_id
        self.directory = Path(directory).absolute()
        require(self.directory.resolve()==self.directory and self.root.parent not in self.directory.parents,
            'NORMAL_EVIDENCE_LOCATION_CHANGED')
        self.log = RecoveryLog(self.directory/'records')
        self.hold = Path(store.db_path).absolute().parent/('.general-test-send-hold-'+plan.PROJECT_ID)
        require(self.hold.is_file() and self.hold.resolve()==self.hold and not self.hold.is_symlink(),
            'ORIGINAL_SEND_HOLD_REQUIRED')
        self.hold_bytes = self.hold.read_bytes()
        from general_test_gate import writes_held
        require(writes_held(SimpleNamespace(_v2_context=self.context,_v2_store=store)), 'ORIGINAL_SEND_HOLD_REQUIRED')
        self.inspector = OfflineInspector(self)
        self.lock = threading.RLock()
        self.network_busy = False
        bound = self.log.latest('bound')
        if bound is None:
            doc = self.document()
            require(doc['revision']==6 and doc['base_content']==plan.INITIAL_CONTENT,
                'NORMAL_INITIAL_BASE_CHANGED')
            require(not self.active(),'NORMAL_INITIAL_QUEUE_NOT_EMPTY')
            self.expected_disk = plan.INITIAL_CONTENT
            self.check()
            self.log.append('bound',project_id=plan.PROJECT_ID,document_id=plan.DOCUMENT_ID,
                local_key=self.key,device_id=device_id,hold_sha256=hashlib.sha256(self.hold_bytes).hexdigest(),
                content=self.expected_disk)
        else:
            require((bound['project_id'],bound['document_id'],bound['local_key'],bound['device_id'],bound['hold_sha256'])==
                (plan.PROJECT_ID,plan.DOCUMENT_ID,self.key,device_id,hashlib.sha256(self.hold_bytes).hexdigest()),
                'NORMAL_SESSION_BINDING_CHANGED')
            self.expected_disk = self.disk_from_history()
        self.restore_needed = bool(self.pending_controls())
        if not self.restore_needed:
            self.check()

    def document(self):
        doc = self.store.get_document_by_id(plan.DOCUMENT_ID)
        require(doc is not None and doc['local_key']==self.key and type(doc['revision']) is int
            and doc['revision']>=6,'NORMAL_DOCUMENT_CHANGED')
        return doc

    def disk_from_history(self):
        """A crash may leave either side of one documented atomic file replace."""
        expected = self.log.latest('bound')['content']
        for row in self.log.records():
            if row['event'] in ('save_intent','receive_intent'):
                expected = row['before']
            if row['event'] in ('file_saved','received'):
                expected = row['content']
        last = next((r for r in reversed(self.log.records()) if r['event'] in
            ('save_intent','receive_intent','file_saved','received')),None)
        actual = (self.root/plan.PATH).read_bytes()
        if last and last['event'] in ('save_intent','receive_intent') and actual==last['content'].encode('utf-8'):
            return last['content']
        require(actual==expected.encode('utf-8'),'NORMAL_UNRECORDED_FILE_CHANGE')
        return expected

    def active(self):
        return self.inspector._active() if hasattr(self,'inspector') else []

    def pending_controls(self):
        return [p for directory in sorted((self.directory/'saves').glob('*')) if directory.is_dir()
            for p in pending_controls(directory,plan_id=plan.PLAN_ID)]

    def check(self, owned_temp=None):
        doc = self.document()
        self.inspector.plan = plan.snapshot_plan(doc['revision'],doc['base_content'])
        self.inspector._local(doc['revision'],active=self.active(),disk=self.expected_disk,owned_temp=owned_temp)
        return True

    def draft(self):
        row = self.log.latest('draft')
        return row['content'] if row else self.expected_disk

    def preserve_draft(self,text):
        require(isinstance(text,str),'NORMAL_DRAFT_INVALID')
        text.encode('utf-8')
        if text!=self.draft():
            self.log.append('draft',content=text,base_revision=self.document()['revision'])

    def write(self,path,text):
        """Called by the real WritingModeWidget/idle controller persistence path."""
        with self.lock:
            require(path==plan.PATH and not self.network_busy,'NORMAL_WRITE_OUTSIDE_SCOPE')
            self.preserve_draft(text)
            plan.validate_text(text)
            require(not self.pending_receive(),'NORMAL_RECEIVE_RECOVERY_REQUIRED')
            require(not self.pending_controls(),'CONTROL_RESTORE_REQUIRED')
            self.check()
            if text==self.expected_disk:
                return True
            baseline = self.inspector._fingerprint()
            before = self.expected_disk
            self.log.append('save_intent',before=before,content=text,base_revision=self.document()['revision'])
            def guard(owned_temp=None):
                self.check(owned_temp)
                current = self.inspector._fingerprint()
                if owned_temp is not None:
                    relative = str(Path(owned_temp).relative_to(self.root.parent))
                    current = ([r for r in current[0] if r[0]!=relative],current[1],current[2])
                require(current==baseline,'NORMAL_SAVE_BASELINE_CHANGED')
            self.wpm.write_text_file(path,text,expected_bytes=before.encode('utf-8'),guard=guard,temporary_guard=guard)
            self.expected_disk = text
            self.log.append('file_saved',content=text)
            self.check()
            return True

    def owned_operation(self):
        active = self.active()
        require(len(active)<=1,'NORMAL_QUEUE_REQUIRES_REVIEW')
        if not active:return None
        op = self.store.operation(active[0])
        matching = [r for r in self.log.records() if r['event']=='queue_intent'
            and r['content']==op.get('content') and r['base_revision']==op.get('base_revision')]
        require(matching and op.get('local_key')==self.key and op.get('document_id')==plan.DOCUMENT_ID
            and op.get('relative_path')==plan.PATH and op.get('provenance_kind')=='CONTRACT_BATCH'
            and not op.get('is_deleted'),'NORMAL_UNOWNED_OPERATION')
        request = self.store.structure_batch_request(op['batch_id'])
        require(request['batch']['writer_device_id']==self.device_id,'NORMAL_DEVICE_CHANGED')
        return op

    def enqueue_saved(self):
        with self.lock:
            require(not self.network_busy,'NORMAL_BUSY')
            require(not self.pending_controls(),'CONTROL_RESTORE_REQUIRED')
            self.check()
            plan.validate_text(self.expected_disk)
            op = self.owned_operation()
            if op:
                # Further local saves stay durable outside this immutable operation.
                return op
            doc = self.document()
            if self.expected_disk==doc['base_content']:return None
            directory = self.directory/'saves'/str(uuid4())
            directory.mkdir(parents=True)
            self.log.append('queue_intent',content=self.expected_disk,base_revision=doc['revision'])
            scope = TemporaryWriteScope(store=self.store,local_key=self.key,directory=directory,
                stage='save',plan_id=plan.PLAN_ID,guard=self.check)
            try:
                scope.open()
                self.inspector.control_scope,self.inspector.write_action = scope,True
                with self.store._transaction():
                    self.check()
                    op = self.store.enqueue(self.context,plan.PATH,self.expected_disk,relative_path=plan.PATH)
                    require(op['provenance_kind']=='CONTRACT_BATCH','NORMAL_CONTRACT_QUEUE_REQUIRED')
                self.log.append('queued',operation_id=op['operation_id'],batch_id=op['batch_id'])
                return op
            finally:
                scope.restore()
                self.inspector.control_scope,self.inspector.write_action = None,False

    def recover_local(self):
        with self.lock:
            require(not self.network_busy,'NORMAL_BUSY')
            for directory in sorted((self.directory/'saves').glob('*')):
                if directory.is_dir():restore_pending_controls(self.store,self.key,directory,plan_id=plan.PLAN_ID)
            self.restore_needed = False
            self.inspector.control_scope,self.inspector.write_action = None,False
            self.expected_disk = self.disk_from_history()
            self.check()
            pending_receive = self.pending_receive()
            if pending_receive:
                self.finish_receive(pending_receive)
                return
            self.enqueue_saved()

    def pending_receive(self):
        received = {r['receive_id'] for r in self.log.records() if r['event']=='received'}
        return next((r for r in reversed(self.log.records()) if r['event']=='receive_intent'
            and r['receive_id'] not in received),None)

    def finish_receive(self,row):
        """Recover an already authenticated, durably captured snapshot offline."""
        self.check()
        require(not self.active(),'NORMAL_RECEIVE_PENDING_WRITE')
        doc = self.document()
        require((doc['revision'],doc['base_content']) in
            ((row['base_revision'],row['before']),(row['revision'],row['content'])), 'NORMAL_RECEIVE_BASE_CHANGED')
        require(self.draft() in (row['before'],row['content']),'NORMAL_RECEIVE_HAS_DRAFT')
        if self.expected_disk!=row['content']:
            require(self.expected_disk==row['before'],'NORMAL_RECEIVE_DISK_CHANGED')
            self.wpm.compare_write_text_file(plan.PATH,row['before'].encode('utf-8'),row['content'],
                guard=self.check,temporary_guard=self.check)
            self.expected_disk = row['content']
        if doc['revision']!=row['revision']:
            applied = self.store.apply_remote_snapshot(self.context,plan.DOCUMENT_ID,plan.PATH,row['content'],row['revision'],
                local_path=plan.PATH,parent_folder_id=plan.PARENT_ID,name=plan.NAME,structure_revision=1)
            require(applied.get('applied') is True,'NORMAL_RECEIVE_NOT_APPLIED')
        self.check()
        self.log.append('received',receive_id=row['receive_id'],revision=row['revision'],content=row['content'])
        self.log.append('draft',content=row['content'],base_revision=row['revision'])

    def status(self):
        if self.pending_controls():return '관문 복구 필요'
        if self.pending_receive():return '수신 반영 복구 필요'
        if self.draft()!=self.expected_disk:return '초안 보존됨 · 로컬 저장 필요'
        op = self.owned_operation()
        if op:
            if any(r['event']=='conflict' and r['operation_id']==op['operation_id'] for r in self.log.records()):
                return '충돌/복구 대기'
            if op['status'] in ('inflight','retry_wait'):return '송신 결과 확인 필요'
            if op['status'] in ('conflict','blocked'):return '충돌/복구 대기'
            return '로컬 저장됨 · 송신 대기'
        if self.expected_disk!=self.document()['base_content']:return '로컬 저장됨 · 큐 등록 필요'
        return '서버 반영 완료 · revision '+str(self.document()['revision'])
