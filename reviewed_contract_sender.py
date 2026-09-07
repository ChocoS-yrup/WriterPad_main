"""Explicit once-only execution of a reviewed preparation; never an outbound queue."""
import json
import uuid
import hashlib
import re
from datetime import datetime, timezone

from contract_preparation import _read_envelope
from contract_recovery_readonly import RecoveryReadOnlyManagerMixin
from sync_contract import CLIENT_BUILD_ID, SyncContractError, canonical_json, json_sha256, validate_atomic_structure_response

REVIEWED_BATCH = '6fd8c11b-5b74-4219-aa0b-a5d408ca8505'
REVIEWED_REQUEST_SHA256 = 'bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045'
PROCESS_TOKEN = str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def paused():
    raise SyncContractError('REVIEWED_EXECUTION_PAUSED')


class ReviewedExecutionStoreMixin:
    def record_reviewed_observation(self, local_key, batch_id, stage, observation=None, error_code=None, *, recovery_id=None, post_coordination=False):
        from contract_readiness_diagnostics import LABELS, sanitize_coordination
        if not re.fullmatch(r'[a-z_]{1,60}', stage):
            raise ValueError('Invalid diagnostic stage')
        trace = {'event': 'reviewed_readiness', 'batch_id': batch_id, 'stage': stage, 'observed_at': now()}
        if error_code:
            trace['error_code'] = error_code if re.fullmatch(r'[A-Z0-9_]{1,80}', error_code) else 'UNCLASSIFIED_ERROR'
        if observation:
            trace['observation'] = {
                'observed_at': observation['observed_at'],
                'conditions': {k: bool(v) for k, v in observation['conditions'].items() if k in LABELS},
                'all_conditions_met': bool(observation['all_conditions_met']),
                'gate_open': bool(observation['gate_open']),
                'active_server_work_count': int(observation['active_server_work_count']),
                'authority': {k: bool(observation['authority'][k]) for k in
                              ('identity_matches','accepted','project_context_matches','project_active','allowed')},
                'stale': bool(observation['stale']),
            }
            if 'coordination' in observation:
                trace['observation']['coordination'] = sanitize_coordination(observation['coordination'])
        with self._transaction() as c:
            if recovery_id is not None:
                trace['recovery_id'] = recovery_id
                table = 'sync_post_coordination_resume_events' if post_coordination else 'sync_reviewed_recovery_events'
                c.execute(f'INSERT INTO {table} (recovery_id,metadata_json) VALUES (?,?)',
                          (recovery_id, canonical_json(trace)))
            c.execute('INSERT INTO sync_contract_diagnostics (trace_id,local_key,event,metadata_json,recorded_at) VALUES (?,?,?,?,?)',
                      (str(uuid.uuid4()), local_key, trace['event'], canonical_json(trace), now()))

    def recover_reviewed_executions(self, c):
        # Reopening a store inside the same running process is not a crash.
        # A new process closes the old execution's gate, never resumes its HTTP.
        c.execute("UPDATE sync_projects SET contract_path_enabled=0,contract_path_enabled_at=NULL "
                  "WHERE local_key IN (SELECT p.local_key FROM sync_contract_preparations p "
                  "JOIN sync_reviewed_executions e ON e.preparation_id=p.preparation_id WHERE e.owner_token<>?)",
                  (PROCESS_TOKEN,))
        c.execute("UPDATE sync_reviewed_executions SET state=CASE WHEN http_attempts=1 THEN 'uncertain' ELSE 'stopped' END, "
                  "finished_at=? WHERE owner_token<>? AND state IN ('preparing','attempted')", (now(), PROCESS_TOKEN))

    def reviewed_execution(self, batch_id):
        with self._reader() as c:
            row = c.execute('SELECT * FROM sync_reviewed_executions WHERE preparation_id=?', (batch_id,)).fetchone()
            return dict(row) if row else None

    def claim_reviewed_execution(self, local_key, envelope, approval):
        batch_id = envelope['request']['batch']['batch_id']
        with self._transaction() as c:
            row = c.execute('SELECT * FROM sync_contract_preparations WHERE preparation_id=? AND local_key=?',
                            (batch_id, local_key)).fetchone()
            if not row or _read_envelope(row) != envelope:
                paused()
            if c.execute('SELECT 1 FROM sync_reviewed_executions WHERE preparation_id=?', (batch_id,)).fetchone():
                raise SyncContractError('REVIEWED_EXECUTION_ALREADY_STARTED')
            c.execute('INSERT INTO sync_reviewed_executions '
                      '(preparation_id,request_sha256,approval_json,state,started_at,owner_token) VALUES (?,?,?,?,?,?)',
                      (batch_id, envelope['request_sha256'], canonical_json(approval), 'preparing', now(), PROCESS_TOKEN))

    def mark_reviewed_http_attempt(self, batch_id):
        with self._transaction() as c:
            cursor = c.execute("UPDATE sync_reviewed_executions SET state='attempted',http_attempts=1 "
                               "WHERE preparation_id=? AND state='preparing' AND http_attempts=0", (batch_id,))
            if cursor.rowcount != 1:
                raise SyncContractError('REVIEWED_EXECUTION_ALREADY_STARTED')

    def finish_reviewed_response(self, batch_id, request, response):
        validate_atomic_structure_response(request, response)
        if response.get('applied') and [r['result_revision'] for r in response['results']] != [1, 2]:
            raise SyncContractError('INVALID_ATOMIC_RESPONSE')
        with self._transaction() as c:
            cursor = c.execute('UPDATE sync_reviewed_executions SET state=?,response_json=?,response_sha256=?,finished_at=? '
                               "WHERE preparation_id=? AND state='attempted' AND request_sha256=?",
                               ('committed' if response['applied'] else 'rejected', canonical_json(response), json_sha256(response),
                                now(), batch_id, json_sha256(request)))
            if cursor.rowcount != 1:
                paused()
        return response

    def stop_reviewed_execution(self, batch_id):
        with self._transaction() as c:
            c.execute("UPDATE sync_reviewed_executions SET state=CASE WHEN http_attempts=1 THEN 'uncertain' ELSE 'stopped' END, "
                      "finished_at=? WHERE preparation_id=? AND state IN ('preparing','attempted')", (now(), batch_id))


class ReviewedDispatch:
    """Adapter for existing C9/transport/single-flight; captures the originating store."""
    def __init__(self, manager, envelope, context):
        self.manager, self.envelope, self.context = manager, envelope, context
        self.store, self.client = context[1:3]
        self.key = context[0][4]
        self.request = envelope['request']
        self.batch_id = self.request['batch']['batch_id']
        self.phase = 'initial_remote_before'

    def trace(self, phase=None, observation=None, error_code=None):
        if phase is not None:
            self.phase = phase
        self.store.record_reviewed_observation(self.key, self.batch_id, self.phase, observation, error_code)

    def local_baseline(self, *, record_observation=True):
        m = self.manager
        if self.context[0] != m._contract_context_key():
            paused()
        if (self.envelope['account_marker'] != m._contract_identity()
            or self.request['batch']['writer_device_id'] != m._v2_device_id
            or self.request['batch']['client_build_id'] != CLIENT_BUILD_ID):
            paused()
        with self.store._transaction() as c:
            baseline = self.store._reverse_preparation_baseline(c, self.key, executing=True)
            local = m._reverse_local_metadata(self.context[0], self.key, executing=True,
                                             observation_sink=(lambda observation: self.trace(observation=observation)) if record_observation else None)
            if json_sha256({'store': baseline, 'local': local}) != self.envelope['baseline_sha256']:
                paused()
        return baseline

    def check_local(self):
        self.local_baseline()

    def check_remote(self):
        # Fresh authenticated handshake and project status, no legacy fallback.
        from contract_transport import execute_contract_rpc
        m = self.manager
        reading = m.perform_contract_handshake(require_connection=True, _expected_key=self.context[0])
        if not reading or reading.get('outcome') != 'supported':
            paused()
        status = m._response_data(execute_contract_rpc(self.client.rpc(
            'get_project_status', {'p_project_id': self.request['project_id']})))
        if not isinstance(status, dict) or status.get('project_id') != self.request['project_id'] or status.get('state') != 'active':
            paused()
        # Auth server validates the session; a decoded JWT cache marker alone is not proof.
        user = getattr(self.client.auth.get_user(), 'user', None)
        user_id = getattr(user, 'id', None)
        if (not isinstance(user_id, str)
            or hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:16] != self.envelope['account_marker']):
            paused()
        owners = (self.client.table('projects').select('project_id,owner_id', count='exact')
                  .eq('project_id', self.request['project_id']).order('project_id').range(0, 1).execute())
        owner_rows = getattr(owners, 'data', None)
        if getattr(owners, 'count', None) != 1 or not isinstance(owner_rows, list) or len(owner_rows) != 1:
            paused()
        if owner_rows[0].get('project_id') != self.request['project_id']:
            paused()
        if owner_rows[0].get('owner_id') != user_id:
            membership = (self.client.table('project_members').select('user_id,role', count='exact')
                          .eq('project_id', self.request['project_id']).eq('user_id', user_id)
                          .order('user_id').range(0, 1).execute())
            members = getattr(membership, 'data', None)
            if (getattr(membership, 'count', None) != 1 or not isinstance(members, list) or len(members) != 1
                or members[0].get('user_id') != user_id or members[0].get('role') not in ('owner', 'editor')):
                paused()
        baseline = self.local_baseline()
        expected = {
            'folders': [{k: f[k] for k in ('folder_id','parent_folder_id','name','revision','is_deleted')} for f in baseline['folders']],
            'documents': [{**{k: d[k] for k in ('document_id','parent_folder_id','revision','structure_revision','name','is_deleted')},
                           'relative_path': d['server_path']} for d in baseline['documents']],
            'tree_orders': [{'tree_order_id': o['tree_order_id'], 'parent_folder_id': o['parent_folder_id'],
                             'children': json.loads(o['children_json']), 'revision': o['revision']} for o in baseline['tree_orders']],
        }
        keys = {'folders':'folder_id','documents':'document_id','tree_orders':'tree_order_id'}
        # Bounded count + range prevents a server row limit from hiding extra rows.
        for table, rows in expected.items():
            columns = ','.join(rows[0])
            response = (self.client.table(table).select(columns, count='exact')
                        .eq('project_id', self.request['project_id']).order(keys[table])
                        .range(0, len(rows)).execute())
            data = getattr(response, 'data', None)
            if getattr(response, 'count', None) != len(rows) or not isinstance(data, list) or len(data) != len(rows):
                paused()
            if sorted(data, key=lambda x:x[keys[table]]) != sorted(rows, key=lambda x:x[keys[table]]):
                paused()
        self.check_local()

    def cached_response(self):
        row = self.store.reviewed_execution(self.batch_id)
        return json.loads(row['response_json']) if row and row['response_json'] else None

    def before_http(self):
        self.trace('http_before')
        self.check_local()
        self.store.mark_reviewed_http_attempt(self.batch_id)

    def record_response(self, response):
        self.trace('response_received')
        return self.store.finish_reviewed_response(self.batch_id, self.request, response)


class ReviewedSenderManagerMixin(RecoveryReadOnlyManagerMixin):
    def inspect_reviewed_contract_readiness(self):
        from contract_readiness_diagnostics import observe_readiness
        import copy
        with self._contract_lock:
            if not self.is_v2_enabled:
                raise SyncContractError('CONTRACT_PREPARATION_WRONG_PROJECT')
            key = self._contract_context_key()
            store, local_key = self._v2_store, self._v2_context['local_key']
            root = self._v2_wpm.writing_root_path
            observation = observe_readiness(self, key)
            envelope = store.reverse_contract_preparation(local_key)
            if envelope is None:
                raise SyncContractError('CONTRACT_PREPARATION_MISSING')
            batch_id = envelope['request']['batch']['batch_id']
            execution = store.reviewed_execution(batch_id)
            recovery = store.inspect_http_zero_recovery(local_key, envelope)
            from contract_post_coordination_resume import ResumeLedger, POLICY, PARENT_ID
            resume = ResumeLedger(store).inspect_http_zero_recovery(local_key, envelope)
            resume.update(policy=POLICY, parent_recovery_id=PARENT_ID,
                          preserved_history_verified=resume['original_verified'])
            stale = key != self._contract_context_key() or observation['stale']
            if stale:
                observation['conditions']['observation_current'] = False
                observation['all_conditions_met'] = False
                observation['stale'] = True
                observation['failed_conditions'] = [k for k,v in observation['conditions'].items() if not v]
            report = {
                'kind': 'reviewed_contract_readiness_observation', 'format_version': 1,
                'project_id': envelope['request']['project_id'], 'batch_id': batch_id,
                'request_sha256': envelope['request_sha256'], 'observation': observation,
                'already_executed': execution is not None,
                'execution': {k:execution[k] for k in ('state','http_attempts','started_at','finished_at')} if execution else None,
                'execution_authorized': False, 'stale': stale,
                'http_zero_recovery': recovery,
                'post_coordination_resume': resume,
                'scope': 'Current process predicates only; no claim, RPC, refresh, gate change, or full baseline validation. Does not explain historical failure.',
            }
            self._last_reviewed_observation = (copy.deepcopy(report), key, root)
            return report

    def export_reviewed_readiness(self, destination):
        return self._export_contract_observation(destination, '_last_reviewed_observation')

    def _export_contract_observation(self, destination, cache_name):
        import copy
        import os
        import tempfile
        with self._contract_lock:
            cached = getattr(self, cache_name, None)
            if cached is None:
                raise SyncContractError('READINESS_OBSERVATION_MISSING')
            report, key, root = cached
            report = copy.deepcopy(report)
            report['context_changed_since_observation'] = key != self._contract_context_key()
            destination = os.path.realpath(destination)
            roots = [root]
            current_root = getattr(getattr(self, '_v2_wpm', None), 'writing_root_path', None)
            if current_root:
                roots.append(current_root)
            inside = False
            for candidate in roots:
                project_root = os.path.normcase(os.path.realpath(os.path.dirname(candidate)))
                try:
                    inside = inside or os.path.commonpath([os.path.normcase(destination), project_root]) == project_root
                except ValueError:
                    pass
            if inside or not destination.lower().endswith('.json'):
                raise SyncContractError('CONTRACT_PREPARATION_EXPORT_LOCATION')
        fd, temporary = tempfile.mkstemp(prefix='.readiness-', dir=os.path.dirname(destination))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(canonical_json(report) + '\n')
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return report

    def send_reviewed_contract_once(self, batch_id, request_sha256, *, approved=False):
        """Only the dedicated explicit UI calls this. No retry or resume entry point."""
        return self._send_reviewed_once(batch_id, request_sha256, approved=approved)

    def send_http_zero_recovery_once(self, *, approval=None):
        # The old boolean approval and old approval JSON cannot enter this path.
        from contract_http_zero_recovery import APPROVAL_KIND
        if not isinstance(approval, dict) or approval.get('kind') != APPROVAL_KIND:
            raise SyncContractError('HTTP_ZERO_RECOVERY_NEW_APPROVAL_REQUIRED')
        return self._send_reviewed_once(approval.get('batch_id'), approval.get('request_sha256'),
                                        approved=True, recovery_approval=approval)

    def send_post_coordination_resume_once(self, *, approval=None):
        from contract_post_coordination_resume import APPROVAL_KIND
        if not isinstance(approval, dict) or approval.get('kind') != APPROVAL_KIND:
            raise SyncContractError('POST_COORDINATION_NEW_APPROVAL_REQUIRED')
        return self._send_reviewed_once(approval.get('batch_id'), approval.get('request_sha256'),
            approved=True, recovery_approval=approval, post_coordination=True)

    def _send_reviewed_once(self, batch_id, request_sha256, *, approved=False, recovery_approval=None, post_coordination=False):
        if approved is not True or (batch_id, request_sha256) != (REVIEWED_BATCH, REVIEWED_REQUEST_SHA256):
            raise SyncContractError('REVIEWED_EXECUTION_APPROVAL_REQUIRED')
        with self._contract_lock:
            if getattr(self, '_review_execution_busy', False) or getattr(self, '_recovery_read_busy', False):
                raise SyncContractError('REVIEWED_EXECUTION_ALREADY_STARTED')
            # Pull reserves its coordinator under this same lock, before worker
            # construction. Reject without consuming either execution claim.
            from contract_readiness_diagnostics import coordination_snapshot, observe_readiness
            coordination = coordination_snapshot(self)
            if coordination['pulling'] or coordination['pull_worker_current']:
                error = SyncContractError('CONTRACT_PREPARATION_NOT_READY')
                error.readiness_observation = observe_readiness(self, self._contract_context_key())
                raise error
            envelope = self.reverse_contract_review()
            if envelope['request_sha256'] != request_sha256 or envelope['request']['batch']['batch_id'] != batch_id:
                paused()
            if self.contract_path_enabled():
                paused()
            context = self._contract_dispatch_context()
            store, local_key = context[1], context[0][4]
            if post_coordination:
                # All local readiness/baseline predicates precede the new claim.
                # A missing baseline never consumes this fixed policy's slot.
                ReviewedDispatch(self, envelope, context).local_baseline(record_observation=False)
            self._review_execution_busy = True
        claimed = False
        dispatch = None
        recovery_id = None
        ledger = store
        try:
            # Persist before slow reads: crashes/duplicate callers cannot resume the write.
            if recovery_approval is not None:
                from contract_http_zero_recovery import HttpZeroRecoveryDispatch
                if post_coordination:
                    from contract_post_coordination_resume import ResumeLedger
                    ledger = ResumeLedger(store)
                recovery_id = ledger.claim_http_zero_recovery(local_key, envelope, recovery_approval)
                dispatch = HttpZeroRecoveryDispatch(self, envelope, context, recovery_id,
                    ledger=ledger, post_coordination=post_coordination)
            else:
                store.claim_reviewed_execution(local_key, envelope, {
                    'batch_id':batch_id, 'request_sha256':request_sha256, 'manual_once':True,
                    'account_marker':envelope['account_marker'], 'approved_at':now(),
                })
                dispatch = ReviewedDispatch(self, envelope, context)
            claimed = True
            dispatch.trace('initial_remote_before')
            dispatch.check_remote()
            dispatch.trace('initial_remote_after')
            with self._contract_lock:
                dispatch.trace('gate_before')
                dispatch.check_local()
                # This explicit path alone arms its captured project's gate.
                # check_remote just refreshed handshake/status with the signed-in client.
                if not self.contract_handshake_is_fresh():
                    paused()
                store.set_contract_path_enabled(local_key, True)
                context = self._contract_dispatch_context()
                dispatch.context = context
                dispatch.trace('gate_after')
            return self._send_contract_request('atomic_structure_commit', envelope['request'], context, reviewed=dispatch)
        except BaseException as error:
            if claimed:
                if recovery_id is not None:
                    ledger.stop_http_zero_recovery(recovery_id)
                else:
                    store.stop_reviewed_execution(batch_id)
                if dispatch is not None:
                    dispatch.trace(error_code=getattr(error, 'code', 'UNCLASSIFIED_ERROR'),
                                   observation=getattr(error, 'readiness_observation', None))
            raise
        finally:
            try:
                with self._contract_lock:
                    # Close original storage even after an account/project switch.
                    if claimed:
                        store.set_contract_path_enabled(local_key, False)
                        if store.contract_path_enabled(local_key):
                            raise SyncContractError('REVIEWED_GATE_CLOSE_FAILED')
                        store.record_reviewed_observation(local_key, batch_id, 'gate_closed', recovery_id=recovery_id,
                                                          post_coordination=post_coordination)
                    if claimed and self._v2_store is store and (self._v2_context or {}).get('local_key') == local_key:
                        self._forget_contract_handshake()
            finally:
                with self._contract_lock:
                    self._review_execution_busy = False
                    # The queued UI slot checks this exact generation/coordinator
                    # again; no pull or network wait runs on the sender thread.
                    self._queue_pull_after_review()

    def launch_reviewed_contract_once(self, batch_id, request_sha256, *, approved=False):
        return self._launch_reviewed_worker(lambda: self.send_reviewed_contract_once(batch_id, request_sha256, approved=approved))

    def launch_http_zero_recovery_once(self, *, approval):
        import copy
        approval = copy.deepcopy(approval)
        return self._launch_reviewed_worker(lambda: self.send_http_zero_recovery_once(approval=approval))

    def launch_post_coordination_resume_once(self, *, approval):
        import copy
        approval = copy.deepcopy(approval)
        return self._launch_reviewed_worker(lambda: self.send_post_coordination_resume_once(approval=approval))

    def _launch_reviewed_worker(self, action):
        from PyQt6.QtCore import QThread, pyqtSignal
        if getattr(self, '_review_execution_worker', None) is not None:
            raise SyncContractError('REVIEWED_EXECUTION_ALREADY_STARTED')
        manager = self
        class Worker(QThread):
            resultReady = pyqtSignal(bool, str)
            def run(self):
                try:
                    response = action()
                    self.resultReady.emit(True, response['status'])
                except Exception as error:
                    self.resultReady.emit(False, getattr(error, 'code', 'REVIEWED_EXECUTION_STOPPED'))
        worker = Worker(self)
        self._review_execution_worker = worker
        def finished():
            if self._review_execution_worker is worker:
                self._review_execution_worker = None
        worker.finished.connect(finished)
        worker.finished.connect(worker.deleteLater)
        return worker  # UI connects result before starting; never auto-start on restore.
