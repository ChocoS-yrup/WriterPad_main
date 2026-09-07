"""One separately approved recovery round; never a pending/retry queue.

The ledger reader requires the proposed, authenticated, read-only server proof
contract. Missing support fails closed. No service key or admin export fallback.
"""
import json
import uuid
from datetime import datetime, timezone

from sync_contract import SyncContractError, json_sha256, canonical_json, validate_atomic_structure_response
from contract_preparation import _read_envelope

# Hashes of canonical lists of the complete original rows, including owner token.
# Fixed canary only. These are provenance pins, not authorization.
ORIGINAL_EXECUTION_ROWS_SHA256 = 'e18cf5940e6e222f74c2a2733471a22a28071452edad1e99fdb08db4b00588e4'
ORIGINAL_PREPARATION_ROWS_SHA256 = '1f887f310b33ef2805335ddc2c2d36ab049a7e010ea4192a6e517f0aeb54cda4'
APPROVAL_KIND = 'http_zero_recovery_once_v1'
PROOF_KIND = 'contract_recovery_ledger_v1'
LEDGER_RPC = 'get_contract_recovery_preflight'


def fail(code='HTTP_ZERO_RECOVERY_NOT_ELIGIBLE'):
    raise SyncContractError(code)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (TypeError, ValueError):
        fail()


def original_rows(c, local_key, envelope):
    from reviewed_contract_sender import REVIEWED_BATCH, REVIEWED_REQUEST_SHA256
    batch = envelope['request']['batch']['batch_id']
    if (batch, envelope['request_sha256']) != (REVIEWED_BATCH, REVIEWED_REQUEST_SHA256):
        fail()
    p = c.execute('SELECT * FROM sync_contract_preparations WHERE preparation_id=? AND local_key=?',
                  (batch, local_key)).fetchone()
    e = c.execute('SELECT * FROM sync_reviewed_executions WHERE preparation_id=?', (batch,)).fetchone()
    if not p or not e or _read_envelope(p) != envelope:
        fail()
    p, e = dict(p), dict(e)
    if (json_sha256([p]) != ORIGINAL_PREPARATION_ROWS_SHA256
            or json_sha256([e]) != ORIGINAL_EXECUTION_ROWS_SHA256
            or e['request_sha256'] != envelope['request_sha256']
            or e['state'] != 'stopped' or e['http_attempts'] != 0
            or e['response_json'] is not None or e['response_sha256'] is not None
            or not e['owner_token'] or not e['approval_json']
            or timestamp(e['finished_at']) < timestamp(e['started_at'])):
        fail()
    return e


def validate_approval(approval, envelope, original):
    required = {'kind', 'approval_id', 'batch_id', 'request_sha256', 'original_execution_sha256',
                'original_preparation_sha256', 'account_marker', 'approved_at', 'manual_once'}
    if not isinstance(approval, dict) or set(approval) != required:
        fail('HTTP_ZERO_RECOVERY_NEW_APPROVAL_REQUIRED')
    try:
        uuid.UUID(approval['approval_id'])
    except (ValueError, TypeError, AttributeError):
        fail('HTTP_ZERO_RECOVERY_NEW_APPROVAL_REQUIRED')
    approved_at = timestamp(approval['approved_at'])
    age = (datetime.now(timezone.utc) - approved_at).total_seconds()
    if (approval['kind'] != APPROVAL_KIND or approval['manual_once'] is not True
            or approval['batch_id'] != envelope['request']['batch']['batch_id']
            or approval['request_sha256'] != envelope['request_sha256']
            or approval['original_execution_sha256'] != ORIGINAL_EXECUTION_ROWS_SHA256
            or approval['original_preparation_sha256'] != ORIGINAL_PREPARATION_ROWS_SHA256
            or approval['account_marker'] != envelope['account_marker']
            or not 0 <= age <= 300 or approved_at <= timestamp(original['finished_at'])):
        fail('HTTP_ZERO_RECOVERY_NEW_APPROVAL_REQUIRED')


def install_schema(c):
    # Only empty tables are added; no UPDATE/re-key of either original ledger.
    c.executescript("""
        CREATE TABLE IF NOT EXISTS sync_reviewed_recoveries (
            recovery_id TEXT PRIMARY KEY,
            preparation_id TEXT NOT NULL UNIQUE REFERENCES sync_reviewed_executions(preparation_id) ON DELETE CASCADE,
            original_execution_sha256 TEXT NOT NULL,
            original_preparation_sha256 TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            approval_id TEXT NOT NULL UNIQUE,
            approval_json TEXT NOT NULL,
            owner_token TEXT NOT NULL,
            started_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('preparing','attempted','stopped','uncertain','committed','rejected')),
            http_attempts INTEGER NOT NULL DEFAULT 0 CHECK(http_attempts IN (0,1)),
            finished_at TEXT,
            CHECK(http_attempts=CASE WHEN state IN ('preparing','stopped') THEN 0 ELSE 1 END)
        );
        CREATE TABLE IF NOT EXISTS sync_reviewed_recovery_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recovery_id TEXT NOT NULL REFERENCES sync_reviewed_recoveries(recovery_id) ON DELETE CASCADE,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_reviewed_recovery_receipts (
            recovery_id TEXT PRIMARY KEY REFERENCES sync_reviewed_recoveries(recovery_id) ON DELETE CASCADE,
            response_json TEXT NOT NULL,
            response_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
    """)
    install_round_guards(c, 'sync_reviewed_recoveries', 'sync_reviewed_recovery_events',
                         'sync_reviewed_recovery_receipts', 'sync_reviewed_recovery_no_reset')


def install_round_guards(c, rounds, events, receipts, trigger, extra_columns=()):
    # Identifiers are internal constants; never supplied by a request or approval.
    immutable = ('recovery_id', 'preparation_id', 'original_execution_sha256',
                 'original_preparation_sha256', 'request_sha256', 'approval_id',
                 'approval_json', 'owner_token', 'started_at') + tuple(extra_columns)
    changed = ' OR '.join(f'NEW.{key} IS NOT OLD.{key}' for key in immutable)
    c.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE UPDATE ON {rounds}
        WHEN {changed}
          OR NOT ((OLD.state='preparing' AND NEW.state IN ('attempted','stopped'))
               OR (OLD.state='attempted' AND NEW.state IN ('uncertain','committed','rejected')))
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HTTP_ZERO_RECOVERY'); END;
    """)
    for table in (rounds, events, receipts):
        c.executescript(f"""
            CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
            WHEN NOT EXISTS (SELECT 1 FROM sync_purge_gate)
            BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HTTP_ZERO_RECOVERY'); END;
        """)
        if table != rounds:
            c.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'IMMUTABLE_HTTP_ZERO_RECOVERY'); END;
            """)


class HttpZeroRecoveryStoreMixin:
    _rounds = 'sync_reviewed_recoveries'
    _events = 'sync_reviewed_recovery_events'
    _receipts = 'sync_reviewed_recovery_receipts'

    def _history(self, c, local_key, envelope):
        return original_rows(c, local_key, envelope)

    def _receipt_history(self, c, row):
        pass

    def recovery_round(self, batch_id):
        with self._reader() as c:
            row = c.execute(f'SELECT * FROM {self._rounds} WHERE preparation_id=?', (batch_id,)).fetchone()
            return dict(row) if row else None

    def inspect_http_zero_recovery(self, local_key, envelope):
        # No claim, timestamps, event append, gate setter or network.
        with self._reader() as c:
            try:
                self._history(c, local_key, envelope)
                eligible = True
            except (SyncContractError, TypeError, ValueError, KeyError):
                eligible = False
            row = c.execute(f'SELECT * FROM {self._rounds} WHERE preparation_id=?',
                            (envelope['request']['batch']['batch_id'],)).fetchone()
            receipt = c.execute(f'SELECT response_json FROM {self._receipts} WHERE recovery_id=?',
                                (row['recovery_id'],)).fetchone() if row else None
        return {'local_candidate': eligible and row is None, 'original_verified': eligible,
                'round': {k: row[k] for k in ('recovery_id','state','http_attempts','started_at','finished_at')} if row else None,
                'approval_recorded': row is not None, 'execution_authorized': False,
                'already_attempted': bool(row and row['http_attempts']),
                'receipt_status': json.loads(receipt['response_json'])['status'] if receipt else None,
                'server_ledger_verified': False}

    def claim_http_zero_recovery(self, local_key, envelope, approval):
        from reviewed_contract_sender import PROCESS_TOKEN, now
        with self._transaction() as c:
            original = self._history(c, local_key, envelope)
            validate_approval(approval, envelope, original)
            batch = original['preparation_id']
            if c.execute(f'SELECT 1 FROM {self._rounds} WHERE preparation_id=?', (batch,)).fetchone():
                fail('HTTP_ZERO_RECOVERY_ALREADY_STARTED')
            rid = 'http0-' + str(uuid.uuid4())  # Never used as a server batch/operation ID.
            c.execute(f'INSERT INTO {self._rounds} '
                      '(recovery_id,preparation_id,original_execution_sha256,original_preparation_sha256,'
                      'request_sha256,approval_id,approval_json,owner_token,started_at,state) VALUES (?,?,?,?,?,?,?,?,?,?)',
                      (rid, batch, ORIGINAL_EXECUTION_ROWS_SHA256, ORIGINAL_PREPARATION_ROWS_SHA256,
                       envelope['request_sha256'], approval['approval_id'], canonical_json(approval), PROCESS_TOKEN, now(), 'preparing'))
            c.execute(f'INSERT INTO {self._events} (recovery_id,metadata_json) VALUES (?,?)',
                      (rid, canonical_json({'stage': 'claimed', 'observed_at': now()})))
            return rid

    def check_http_zero_round(self, local_key, envelope, recovery_id, *, mark_attempt=False):
        from reviewed_contract_sender import PROCESS_TOKEN, now
        with self._transaction() as c:
            self._history(c, local_key, envelope)
            r = c.execute(f'SELECT * FROM {self._rounds} WHERE recovery_id=?', (recovery_id,)).fetchone()
            if (not r or r['preparation_id'] != envelope['request']['batch']['batch_id']
                    or r['owner_token'] != PROCESS_TOKEN or r['state'] != 'preparing' or r['http_attempts'] != 0
                    or r['request_sha256'] != envelope['request_sha256']
                    or r['original_execution_sha256'] != ORIGINAL_EXECUTION_ROWS_SHA256
                    or r['original_preparation_sha256'] != ORIGINAL_PREPARATION_ROWS_SHA256
                    or c.execute(f'SELECT 1 FROM {self._receipts} WHERE recovery_id=?', (recovery_id,)).fetchone()):
                fail()
            if mark_attempt:
                c.execute(f"UPDATE {self._rounds} SET state='attempted',http_attempts=1 WHERE recovery_id=?", (recovery_id,))
                c.execute(f'INSERT INTO {self._events} (recovery_id,metadata_json) VALUES (?,?)',
                          (recovery_id, canonical_json({'stage':'http_attempt_durable','observed_at':now()})))

    def stop_http_zero_recovery(self, recovery_id):
        from reviewed_contract_sender import now
        with self._transaction() as c:
            c.execute(f"UPDATE {self._rounds} SET state=CASE WHEN http_attempts=1 THEN 'uncertain' ELSE 'stopped' END, "
                      "finished_at=? WHERE recovery_id=? AND state IN ('preparing','attempted')", (now(), recovery_id))

    def recover_http_zero_rounds(self, c):
        from reviewed_contract_sender import PROCESS_TOKEN, now
        # A crash can occur after saving a terminal receipt but before finally
        # closes the gate. Close gates for terminal rounds too on another owner.
        c.execute('UPDATE sync_projects SET contract_path_enabled=0,contract_path_enabled_at=NULL '
                  f'WHERE local_key IN (SELECT p.local_key FROM {self._rounds} r '
                  'JOIN sync_contract_preparations p USING(preparation_id) WHERE r.owner_token<>?)', (PROCESS_TOKEN,))
        rows = c.execute(f"SELECT r.recovery_id,p.local_key FROM {self._rounds} r "
                         "JOIN sync_contract_preparations p USING(preparation_id) WHERE r.owner_token<>? "
                         "AND r.state IN ('preparing','attempted')", (PROCESS_TOKEN,)).fetchall()
        for r in rows:
            c.execute('UPDATE sync_projects SET contract_path_enabled=0,contract_path_enabled_at=NULL WHERE local_key=?', (r['local_key'],))
            changed = c.execute(f"UPDATE {self._rounds} SET state=CASE WHEN http_attempts=1 THEN 'uncertain' ELSE 'stopped' END, "
                                "finished_at=? WHERE recovery_id=? AND state IN ('preparing','attempted')", (now(), r['recovery_id']))
            if changed.rowcount == 1:
                c.execute(f'INSERT INTO {self._events} (recovery_id,metadata_json) VALUES (?,?)',
                          (r['recovery_id'], canonical_json({'stage':'process_interrupted','observed_at':now()})))

    def finish_http_zero_response(self, recovery_id, request, response):
        from reviewed_contract_sender import now
        validate_atomic_structure_response(request, response)
        if response.get('applied') and [r['result_revision'] for r in response['results']] != [1, 2]:
            fail('INVALID_ATOMIC_RESPONSE')
        with self._transaction() as c:
            row = c.execute(f'SELECT * FROM {self._rounds} WHERE recovery_id=?', (recovery_id,)).fetchone()
            if (not row or row['http_attempts'] != 1 or row['state'] not in ('attempted','uncertain')
                    or row['request_sha256'] != json_sha256(request)):
                fail()
            self._receipt_history(c, row)
            c.execute(f'INSERT INTO {self._receipts} VALUES (?,?,?,?)',
                      (recovery_id, canonical_json(response), json_sha256(response), now()))
            # A late response after another process's crash observation is kept
            # without rewriting that terminal observation or creating a new round.
            if row['state'] == 'attempted':
                c.execute(f'UPDATE {self._rounds} SET state=?,finished_at=? WHERE recovery_id=?',
                          ('committed' if response['applied'] else 'rejected', now(), recovery_id))
        return response


def read_server_ledger(client, envelope, response_data):
    """Strict proof response. A bare SELECT/count=0 cannot establish RLS coverage."""
    from contract_transport import execute_contract_rpc
    request = envelope['request']
    batch_id = request['batch']['batch_id']
    nonce = str(uuid.uuid4())
    data = response_data(execute_contract_rpc(client.rpc(LEDGER_RPC, {
        'p_project_id': request['project_id'], 'p_batch_id': batch_id,
        'p_request_sha256': envelope['request_sha256'],
        'p_operation_ids': [i['operation_id'] for i in request['ordered_intents']], 'p_nonce': nonce,
    })))
    required = {'kind','project_id','batch_id','request_sha256','operation_ids','nonce','account_marker',
                'complete','authorized','counts','checked_at'}
    if not isinstance(data, dict) or set(data) != required:
        fail('HTTP_ZERO_LEDGER_PROOF_REQUIRED')
    if (data['kind'] != PROOF_KIND or data['project_id'] != request['project_id']
            or data['batch_id'] != batch_id or data['request_sha256'] != envelope['request_sha256']
            or data['operation_ids'] != [i['operation_id'] for i in request['ordered_intents']]
            or data['nonce'] != nonce or data['account_marker'] != envelope['account_marker']
            or data['complete'] is not True or data['authorized'] is not True
            or not isinstance(data['counts'], dict)
            or set(data['counts']) != {'batches','operations','attempts','results'}):
        fail('HTTP_ZERO_LEDGER_PROOF_REQUIRED')
    age = (datetime.now(timezone.utc) - timestamp(data['checked_at'])).total_seconds()
    if not -5 <= age <= 60 or any(type(n) is not int or n < 0 for n in data['counts'].values()):
        fail('HTTP_ZERO_LEDGER_NOT_EMPTY_OR_UNKNOWN')
    return data


def check_server_ledger(dispatch):
    data = read_server_ledger(dispatch.client, dispatch.envelope, dispatch.manager._response_data)
    if any(data['counts'].values()):
        fail('HTTP_ZERO_LEDGER_NOT_EMPTY_OR_UNKNOWN')
    dispatch.trace('ledger_verified')


# Import after store definitions to keep SyncV2Store's mixins acyclic.
from reviewed_contract_sender import ReviewedDispatch


class HttpZeroRecoveryDispatch(ReviewedDispatch):
    def __init__(self, manager, envelope, context, recovery_id, *, ledger=None, post_coordination=False):
        super().__init__(manager, envelope, context)
        self.recovery_id = recovery_id
        self.ledger = ledger if ledger is not None else self.store
        self.post_coordination = post_coordination

    def trace(self, phase=None, observation=None, error_code=None):
        if phase is not None:
            self.phase = phase
        self.store.record_reviewed_observation(self.key, self.batch_id, self.phase, observation, error_code,
                                               recovery_id=self.recovery_id, post_coordination=self.post_coordination)

    def check_local(self):
        super().check_local()
        self.ledger.check_http_zero_round(self.key, self.envelope, self.recovery_id)

    def check_remote(self):
        super().check_remote()
        check_server_ledger(self)
        self.check_local()

    def cached_response(self):
        return None  # A claimed round is never resumed, even with a receipt.

    def before_http(self):
        self.trace('http_before')
        self.check_local()
        self.ledger.check_http_zero_round(self.key, self.envelope, self.recovery_id, mark_attempt=True)

    def record_response(self, response):
        self.trace('response_received')
        return self.ledger.finish_http_zero_response(self.recovery_id, self.request, response)
