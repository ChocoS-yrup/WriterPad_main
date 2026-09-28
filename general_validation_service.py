"""Manual ID_BASED roundtrip, using product transactions and no dispatcher.

The global hold remains held. After fresh authenticated baseline checks, the
dedicated path temporarily opens only the target local gate and restores it.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx

import general_validation_plan as plan
from general_test_gate_target import EXPECTED_FOLDERS, EXPECTED_ORDERS
from general_validation_boundary import (DurableExecutionJournal, ForegroundAuthority,
    FrozenRequest, GeneralValidationDenied, check_store_binding)
from general_validation_control import TemporaryWriteScope, pending_controls
from sync_contract import CANONICAL_CONTRACT_SHA256, require_server_compatibility

CONTROL_ID = '6cbe47cd-67e5-5e27-8dbf-f3ae59255d52'
CONTROL_SHA = 'e290f19f8c47350c5b9b6e7314aadea1477508174b770860040148280517e1f6'
TABLES = ('projects', 'project_sync_settings', 'documents', 'folders', 'tree_orders')


def require(condition, code):
    if not condition:
        raise GeneralValidationDenied(code)


def expected_orders(created):
    rows = deepcopy(EXPECTED_ORDERS)
    if created:
        row = next(r for r in rows if r['id'] == plan.ORDER_ID)
        row.update(children=[plan.DOCUMENT_ID], revision=2)
    return {r['id']:(r['parent'],r['children'],r['revision']) for r in rows}


def expected_identity_orders(created):
    """Pin the existing identity order independently from the server/view order.

    The preserved pre-install identity has trash at 8 and 연결확인 at 9.
    The UUID tree-order table and Windows view put 연결확인 before trash.
    Accept only these exact existing representations; never reorder user data.
    """
    rows = expected_orders(created)
    parent,children,revision = rows['c0655c5f-97f1-5b4b-ac26-d4f64189a180']
    require(children[-2:] == ['a2d9f760-1a42-4ead-a2d0-51870b062fd9','13186784-2e57-4e29-b28a-178301679824'],
            'FIXED_ORDER_BASELINE_CHANGED')
    rows['c0655c5f-97f1-5b4b-ac26-d4f64189a180'] = (parent,children[:-2]+list(reversed(children[-2:])),revision)
    return rows


def folder_paths():
    rows = {r['id']:r for r in EXPECTED_FOLDERS}
    def path(key):
        r = rows[key]
        return (path(r['parent'])+'/' if r['parent'] else '')+r['name']
    return {key:path(key) for key in rows}


def view_orders(created):
    """Windows hides the physical '메인' container in its saved view root."""
    paths = folder_paths()
    result = {('<root>' if parent is None else paths[parent]):
        [plan.NAME if child == plan.DOCUMENT_ID else paths[child].rsplit('/',1)[-1] for child in children]
        for parent,children,_ in expected_orders(created).values()}
    require(result.pop('<root>') == ['메인'], 'FIXED_ROOT_CHANGED')
    result['<root>'] = result.pop('메인')
    return result


def validate_snapshot(rows, revision, account_id, *, ordered=None, plan=plan):
    """Validate all five complete target tables, not only the chosen body."""
    require(set(rows) == set(TABLES) and all(isinstance(rows[t],list) for t in TABLES), 'SNAPSHOT_SHAPE_CHANGED')
    require(all(r.get('project_id') == plan.PROJECT_ID for table in TABLES for r in rows[table]), 'SNAPSHOT_PROJECT_CHANGED')
    projects, settings = rows['projects'], rows['project_sync_settings']
    require(len(projects) == 1 and projects[0].get('owner_id') == account_id
            and projects[0].get('name') == plan.PROJECT_NAME and projects[0].get('deleted_at') is None,
            'PROJECT_OWNER_CHANGED')
    require(len(settings) == 1 and settings[0].get('project_sync_mode') == 'ID_BASED'
            and type(settings[0].get('migration_epoch')) is int and settings[0]['migration_epoch'] == 1,
            'REMOTE_MODE_CHANGED')
    folders = rows['folders']
    require(len(folders) == len(EXPECTED_FOLDERS) and
        {r['folder_id']:(r['parent_folder_id'],r['name'],r['revision'],r['is_deleted']) for r in folders} ==
        {r['id']:(r['parent'],r['name'],r['revision'],r['deleted']) for r in EXPECTED_FOLDERS}
        and all(r.get('deleted_at') is None and type(r['revision']) is int and r['is_deleted'] is False for r in folders),
        'REMOTE_FOLDERS_CHANGED')
    orders = rows['tree_orders']
    require(len(orders) == 12 and
        {r['tree_order_id']:(r['parent_folder_id'],r['children'],r['revision']) for r in orders} ==
        expected_orders(bool(revision) if ordered is None else ordered)
        and all(type(r['revision']) is int for r in orders), 'REMOTE_ORDER_CHANGED')
    documents = rows['documents']
    by_id = {r['document_id']:r for r in documents}
    require(len(documents) == (2 if revision else 1) and set(by_id) ==
            ({CONTROL_ID,plan.DOCUMENT_ID} if revision else {CONTROL_ID}), 'REMOTE_DOCUMENT_SET_CHANGED')
    control = by_id[CONTROL_ID]
    require(control['revision'] == 1 and control['relative_path'] == '__antigravity__/tree-order.json'
            and plan.digest(control['content']) == CONTROL_SHA, 'REMOTE_CONTROL_CHANGED')
    require(all(r['is_deleted'] is False and r.get('deleted_at') is None
            and type(r['revision']) is int for r in documents), 'REMOTE_DOCUMENT_DELETED')
    if revision:
        doc = by_id[plan.DOCUMENT_ID]
        require((doc['revision'],doc['relative_path'],doc['content'],doc['parent_folder_id'],doc['name'],doc['structure_revision']) ==
            (revision,plan.PATH,plan.STAGES[revision-1].content,plan.PARENT_ID,plan.NAME,1), 'REMOTE_BODY_CHANGED')
    return deepcopy(rows)


class GeneralValidationService:
    plan = plan
    request_class = FrozenRequest

    def __init__(self, *, store, wpm, client, transport, ticket, journal_dir,
                 device_id, account_id, access_token, session_current):
        self.store, self.wpm, self.client, self.transport = store, wpm, client, transport
        self.ticket, self.journal_dir = ticket, Path(journal_dir)
        self.device_id, self.account_id = device_id, account_id
        self.root = Path(wpm.writing_root_path).absolute()
        self.key = store.local_key_for(str(self.root))
        self.context = dict(check_store_binding(store,self.key), writer_device_id=device_id)
        self.authority = ForegroundAuthority(account_id=account_id, local_key=self.key, access_token=access_token,
            check_current=lambda a,k,p: self._authority_current(a,k,p,session_current))
        self.journal = None
        self.write_action = False
        self.control_scope = None

    def _authority_current(self, account, key, project, session_current):
        self.ticket.check()
        require((account,key,project) == (self.account_id,self.key,self.plan.PROJECT_ID), 'AUTHORITY_BINDING_CHANGED')
        check_store_binding(self.store,self.key)
        return session_current() is True

    def _active(self):
        from sync_v2_store import CONTRACT_ACTIVE_STATES
        with self.store._reader() as conn:
            return sorted(r[0] for table in ('sync_operations','sync_structure_operations')
                for r in conn.execute('SELECT operation_id FROM '+table+' WHERE local_key=?',(self.key,))
                if self.store._derived_state(conn,r[0]) in CONTRACT_ACTIVE_STATES)

    def _gates(self, writing):
        self.authority.check()
        project = check_store_binding(self.store,self.key)
        require(project['project_name'] == self.plan.PROJECT_NAME and project['server_state'] == 'active', 'LOCAL_PROJECT_CHANGED')
        with self.store._reader() as conn:
            require(not conn.execute('SELECT 1 FROM sync_projects WHERE local_key<>? AND contract_path_enabled<>0',
                                    (self.key,)).fetchone(), 'OTHER_PROJECT_GATE_OPEN')
        if writing:
            require(self.control_scope is not None, 'DEDICATED_WRITE_SCOPE_REQUIRED')
            self.control_scope.check()
            require_server_compatibility(project_sync_mode=project['project_sync_mode'],migration_epoch=project['migration_epoch'],
                server_protocol_version=project['server_protocol_version'],server_contract_sha256=project['active_contract_sha256'],
                server_capabilities=json.loads(project['server_capabilities_json']))

    def _local(self, revision, *, ordered=None, active=(), disk=None, owned_temp=None):
        from project_creation_v1 import audit
        from project_identity_v1 import read_identity
        self._gates(self.write_action)
        require(self._active() == sorted(active) and not self.store.pending_folder_delete_intents(self.key)
                and not self.store.pending_folder_rename_intents(self.key), 'LOCAL_QUEUE_CHANGED')
        audit_result = audit(str(self.root.parent))
        if owned_temp is not None:
            temp = Path(owned_temp).absolute()
            require(temp.is_file() and temp.resolve() == temp and temp.parent == (self.root/self.plan.PATH).parent,
                    'OWNED_TEMP_PATH_CHANGED')
            relative = temp.relative_to(self.root).as_posix()
            audit_result['missing_in_identity'] = [p for p in audit_result['missing_in_identity'] if p != relative]
        require(self.root.resolve() == self.root and not any(audit_result.values()), 'LOCAL_TREE_AUDIT_CHANGED')
        identity = read_identity(str(self.root.parent))
        require(identity['project']['uuid'] == self.plan.PROJECT_ID, 'LOCAL_IDENTITY_CHANGED')
        paths = folder_paths()
        nodes = identity['nodes']
        folders = [n for n in nodes if n['kind'] == 'folder']
        require(len(folders) == 11 and {n['uuid']:(n['parent_uuid'],n['legacy_path']) for n in folders} ==
            {r['id']:(r['parent'],paths[r['id']]) for r in EXPECTED_FOLDERS}, 'LOCAL_FOLDER_IDENTITY_CHANGED')
        for path in paths.values():
            p = self.root/path
            require(p.is_dir() and p.resolve() == p, 'LOCAL_FOLDER_PATH_CHANGED')
        docs = self.store.list_documents(self.key)
        by_id = {r['document_id']:r for r in docs}
        has_doc = revision is not None
        require(len(docs) == (2 if has_doc else 1) and set(by_id) ==
                ({CONTROL_ID,self.plan.DOCUMENT_ID} if has_doc else {CONTROL_ID}), 'LOCAL_DOCUMENT_SET_CHANGED')
        control = by_id[CONTROL_ID]
        require(control['revision'] == 1 and not control['is_deleted'] and
                control['local_path'] == control['server_path'] == '__antigravity__/tree-order.json'
                and self.plan.digest(control['base_content']) == CONTROL_SHA, 'LOCAL_CONTROL_CHANGED')
        dn = [n for n in nodes if n['kind'] == 'document']
        require(len(dn) == int(has_doc), 'LOCAL_DOCUMENT_IDENTITY_CHANGED')
        if has_doc:
            require((dn[0]['uuid'],dn[0]['parent_uuid'],dn[0]['legacy_path'],dn[0]['order']) ==
                    (self.plan.DOCUMENT_ID,self.plan.PARENT_ID,self.plan.PATH,0), 'LOCAL_DOCUMENT_IDENTITY_CHANGED')
            doc = by_id[self.plan.DOCUMENT_ID]
            content = self.plan.STAGES[revision-1].content if revision else ''
            require(doc['revision'] == revision and not doc['is_deleted'] and doc['local_path'] == doc['server_path'] == self.plan.PATH
                    and doc['base_content'] == content and doc['base_hash'] == (self.plan.digest(content) if revision else ''), 'LOCAL_BODY_BASE_CHANGED')
            if revision:
                require((doc['parent_folder_id'],doc['name'],doc['structure_revision']) == (self.plan.PARENT_ID,self.plan.NAME,1), 'LOCAL_BODY_STRUCTURE_CHANGED')
            require((self.root/self.plan.PATH).read_bytes() == (content if disk is None else disk).encode('utf-8'), 'LOCAL_BODY_FILE_CHANGED')
        else:
            require(not (self.root/self.plan.PATH).exists(), 'LOCAL_BODY_COLLISION')
        with self.store._reader() as conn:
            fr = [dict(r) for r in conn.execute('SELECT * FROM sync_folders WHERE local_key=?',(self.key,))]
            orders = [dict(r) for r in conn.execute('SELECT * FROM sync_tree_orders WHERE local_key=?',(self.key,))]
        require(len(fr) == 11 and {r['folder_id']:(r['parent_folder_id'],r['name'],r['revision'],bool(r['is_deleted']),r['local_path']) for r in fr} ==
            {r['id']:(r['parent'],r['name'],r['revision'],r['deleted'],paths[r['id']]) for r in EXPECTED_FOLDERS}, 'LOCAL_FOLDER_STORE_CHANGED')
        created_order = bool(revision) if ordered is None else ordered
        require(len(orders) == 12 and {r['tree_order_id']:(r['parent_folder_id'],json.loads(r['children_json']),r['revision']) for r in orders} ==
                expected_orders(created_order), 'LOCAL_ORDER_STORE_CHANGED')
        settings = json.loads((self.root/'설정.json').read_text('utf-8'))
        require(settings.get('tree_order') == view_orders(created_order), 'LOCAL_ORDER_SETTINGS_CHANGED')
        # Identity orders are checked independently from the name-based view settings.
        for parent,children,_ in expected_identity_orders(has_doc).values():
            require([(n['uuid'],n['order']) for n in sorted((n for n in nodes if n['parent_uuid']==parent),key=lambda n:n['order'])]
                    == [(child,index) for index,child in enumerate(children)],
                    'LOCAL_IDENTITY_ORDER_CHANGED')
        return True

    def _fingerprint(self):
        """Freeze existing files and all local DB rows; only our explicit steps may advance it."""
        files = []
        for p in sorted(self.root.parent.rglob('*')):
            require(not p.is_symlink() and p.resolve() == p.absolute(), 'LOCAL_LINK_REFUSED')
            files.append((str(p.relative_to(self.root.parent)),hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None))
        with self.store._reader() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            rows = {t:sorted(repr(tuple(r)) for r in conn.execute('SELECT * FROM "'+t.replace('"','""')+'"')) for t in tables}
        hold = Path(self.store.db_path).parent/('.general-test-send-hold-'+self.plan.PROJECT_ID)
        return files,rows,hold.read_bytes() if hold.exists() else None

    def _arm(self, label, requests):
        baseline = self._fingerprint()
        def unchanged():
            self._gates(self.write_action)
            return self._fingerprint() == baseline
        self.transport.arm(label, authority=self.authority,
            requests=[self.request_class.capture(r) for r in requests], check_local=unchanged,
            persist_attempt=self.journal.append)

    def _snapshot(self, label, revision, *, ordered=None):
        requests = [httpx.Request('GET',self.plan.STAGING_URL+'/rest/v1/'+table,
                    params={'select':'*','project_id':'eq.'+self.plan.PROJECT_ID}) for table in TABLES]
        self._arm(label,requests)
        rows = {table:self.client.table(table).select('*').eq('project_id',self.plan.PROJECT_ID).execute().data for table in TABLES}
        return validate_snapshot(rows,revision,self.account_id,ordered=ordered,plan=self.plan)

    def _handshake(self):
        args = {'p_project_id':self.plan.PROJECT_ID,'p_contract_sha256':CANONICAL_CONTRACT_SHA256}
        self._arm('handshake',[httpx.Request('POST',self.plan.STAGING_URL+'/rest/v1/rpc/get_sync_handshake',json=args)])
        self.client.rpc('get_sync_handshake',args).execute()

    def _commit(self, request):
        name = 'document_commit' if request['kind'] == 'document_commit_request' else 'atomic_structure_commit'
        self._arm(name,[httpx.Request('POST',self.plan.STAGING_URL+'/rest/v1/rpc/'+name,json={'p_request':request})])
        response = self.client.rpc(name,{'p_request':request}).execute().data
        return response

    def _send_body(self, stage):
        with self.store._transaction():
            self._local(stage.base_revision, ordered=stage.base_revision>0, disk=stage.content)
            operation = self.store.enqueue(self.context,self.plan.PATH,stage.content,relative_path=self.plan.PATH)
        require(operation['provenance_kind'] == 'CONTRACT_BATCH', 'CONTRACT_QUEUE_REQUIRED')
        request = self.store.structure_batch_request(operation['batch_id'])
        self.store.mark_attempt(operation['operation_id'])
        self._local(stage.base_revision,ordered=stage.base_revision>0,active=[operation['operation_id']],disk=stage.content)
        result = self._commit(request)
        self.authority.check()
        with self.store._transaction():
            self.store.record_document_batch_response(operation['batch_id'],result)
            self.store.mark_success(operation['operation_id'],{'revision':stage.result_revision,'content_hash':self.plan.digest(stage.content),
                'status':'committed','parent_folder_id':self.plan.PARENT_ID,'name':self.plan.NAME,'structure_revision':1})
        self.journal.append({'event':'body_committed','outcome':str(stage.result_revision)})

    def _replace_body(self, revision, content, *, ordered=None):
        old = self.plan.STAGES[revision-1].content if revision else ''
        self.wpm.compare_write_text_file(self.plan.PATH,old.encode(),content,
            guard=lambda:self._local(revision,ordered=ordered),
            temporary_guard=lambda path:self._local(revision,ordered=ordered,owned_temp=path))

    def _complete(self, revision):
        self._local(revision)
        self.authority.check()
        return {'revision':revision,'bytes':len(self.plan.STAGES[revision-1].content.encode()) if revision else 0,
                'sha256':self.plan.digest(self.plan.STAGES[revision-1].content) if revision else ''}

    def run(self, action):
        require(action in ('prepare','create','finish'), 'UNKNOWN_ACTION')
        require(not pending_controls(self.journal_dir), 'CONTROL_RESTORE_REQUIRED')
        if (self.journal_dir/(self.plan.PLAN_ID+'-'+action+'.jsonl')).exists():
            raise FileExistsError('PRIOR_RUN_REQUIRES_REVIEW')
        self.write_action = False
        self._gates(False)
        if action == 'finish':
            prior = self.journal_dir/(self.plan.PLAN_ID+'-create.jsonl')
            require(prior.is_file() and json.loads(prior.read_text('utf-8').splitlines()[-1]).get('event') == 'completed', 'CREATE_NOT_COMPLETED')
        self._local(1 if action == 'finish' else None)
        self.journal = DurableExecutionJournal(self.journal_dir/(self.plan.PLAN_ID+'-'+action+'.jsonl'))
        try:
            self._handshake()
            self._snapshot('before',2 if action == 'finish' else 0)
            if action == 'prepare':
                self._local(None)
                self.journal.append({'event':'completed','outcome':'baseline_only'})
                return {'revision':0,'bytes':0,'sha256':''}
            initial_revision = 1 if action == 'finish' else None
            self.control_scope = TemporaryWriteScope(store=self.store,local_key=self.key,directory=self.journal_dir,
                stage=action,guard=lambda:self._local(initial_revision))
            try:
                self.control_scope.open()
                self.write_action = True
                self.journal.append({'event':'local_gate_opened','outcome':'global_hold_unchanged'})
                result = self._execute_write(action)
            finally:
                # Restoration must run after grant expiry/logout/cancellation too.
                # It changes no manuscript, queue, Auth state or server state.
                self.control_scope.restore()
                self.write_action = False
                self.journal.append({'event':'local_gate_restored','outcome':'global_hold_unchanged'})
            self.authority.check()
            self.journal.append({'event':'completed','outcome':str(result['revision'])})
            return result
        except Exception:
            self.ticket.cancel()
            self.journal.append({'event':'stopped','outcome':'preserve_and_review'})
            raise
        finally:
            self.journal.close()

    def _execute_write(self, action):
        if action == 'create':
            from project_creation_v1 import create_item
            self._local(None)
            ids = iter((self.plan.DOCUMENT_ID,str(uuid4())))
            create_item(str(self.root.parent),self.plan.PARENT_ID,self.plan.NAME.removesuffix('.txt'),False,uuid_factory=lambda:next(ids))
            self.authority.check()
            self.store.ensure_document(self.key,self.plan.PATH,'',self.plan.DOCUMENT_ID)
            self._local(0,ordered=False)
            self._replace_body(0,self.plan.BASE,ordered=False)
            self._send_body(self.plan.STAGES[0])
            self._local(1,ordered=False)
            self._snapshot('created',1,ordered=False)
            intent = deepcopy(self.plan.order_request(self.device_id)['ordered_intents'][0])
            intent.pop('operation_id')
            with self.store._transaction():
                self._local(1,ordered=False)
                request = self.store.create_structure_batch(self.context,self.device_id,[intent])
            op_ids = self.store.mark_structure_batch_attempt(request['batch']['batch_id'])
            self._local(1,ordered=False,active=op_ids)
            response = self._commit(request)
            self.authority.check()
            self.store.record_structure_batch_response(request['batch']['batch_id'],response)
            before = (self.root/'설정.json').read_bytes()
            settings = json.loads(before)
            require(settings['tree_order']['메인/원고'] == [], 'LOCAL_ORDER_SETTINGS_CHANGED')
            settings['tree_order']['메인/원고'] = [self.plan.NAME]
            self.wpm.compare_write_text_file('설정.json',before,json.dumps(settings,ensure_ascii=False,indent=4),guard=self.authority.check)
            self._snapshot('after',1)
            return self._complete(1)
        self._local(1)
        self._replace_body(1,self.plan.IPAD)
        self.authority.check()
        applied = self.store.apply_remote_snapshot(self.context,self.plan.DOCUMENT_ID,self.plan.PATH,self.plan.IPAD,2,
            local_path=self.plan.PATH,parent_folder_id=self.plan.PARENT_ID,name=self.plan.NAME,structure_revision=1)
        require(applied.get('applied') is True, 'REMOTE_BODY_NOT_APPLIED')
        self._local(2)
        self._replace_body(2,self.plan.WINDOWS)
        self._send_body(self.plan.STAGES[2])
        self._snapshot('after',3)
        return self._complete(3)
