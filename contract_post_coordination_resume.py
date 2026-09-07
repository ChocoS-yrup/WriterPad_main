"""One fixed-policy child of the preserved HTTP-0 parent. No recursive retry policy."""
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from contract_http_zero_recovery import (HttpZeroRecoveryStoreMixin, original_rows,
    install_round_guards, timestamp, fail)
from sync_contract import canonical_json, json_sha256
from contract_preparation import _read_envelope

POLICY = 'post_coordination_once_v1'
APPROVAL_KIND = 'post_coordination_resume_once_v1'
PARENT_ID = 'http0-074b0dc9-007c-4f0e-8516-a84804850e77'
PARENT_ROWS_SHA256 = '176d27df155a477621a97b91aa7d1eea212ef79fdb72327c0ef403e255b3bd4e'
PARENT_EVENTS_SHA256 = '72e27e685ec476dd5eea79ed3b456161615e85202460332f94f03d42d42fefdb'
ROUNDS = 'sync_post_coordination_resumes'
EVENTS = 'sync_post_coordination_resume_events'
RECEIPTS = 'sync_post_coordination_resume_receipts'


@lru_cache(maxsize=1)
def execution_build_sha256():
    # A development interpreter is never an installed execution build.
    if not getattr(sys, 'frozen', False):
        fail('POST_COORDINATION_INSTALLED_BUILD_REQUIRED')
    return hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()


def history(c, local_key, envelope):
    original = original_rows(c, local_key, envelope)
    parent = c.execute('SELECT * FROM sync_reviewed_recoveries WHERE recovery_id=? AND preparation_id=?',
                       (PARENT_ID, original['preparation_id'])).fetchone()
    events = [dict(row) for row in c.execute(
        'SELECT event_id,recovery_id,metadata_json FROM sync_reviewed_recovery_events WHERE recovery_id=? ORDER BY event_id', (PARENT_ID,))]
    if (not parent or json_sha256([dict(parent)]) != PARENT_ROWS_SHA256
            or json_sha256(events) != PARENT_EVENTS_SHA256 or len(events) != 4
            or parent['state'] != 'stopped' or parent['http_attempts'] != 0
            or parent['request_sha256'] != envelope['request_sha256']
            or timestamp(parent['finished_at']) < timestamp(parent['started_at'])
            or c.execute('SELECT 1 FROM sync_reviewed_recovery_receipts WHERE recovery_id=?', (PARENT_ID,)).fetchone()):
        fail('POST_COORDINATION_HISTORY_REQUIRED')
    return original, dict(parent)


def validate_approval(approval, envelope, original, parent):
    from contract_http_zero_recovery import validate_approval as validate_original_approval, APPROVAL_KIND as OLD_KIND
    extra = {'policy','parent_recovery_id','parent_rows_sha256','parent_events_sha256','execution_build_sha256'}
    if not isinstance(approval, dict) or not extra <= set(approval) or approval.get('kind') != APPROVAL_KIND:
        fail('POST_COORDINATION_NEW_APPROVAL_REQUIRED')
    legacy = {k:v for k,v in approval.items() if k not in extra}
    legacy['kind'] = OLD_KIND
    validate_original_approval(legacy, envelope, original)
    if (approval['policy'] != POLICY or approval['parent_recovery_id'] != PARENT_ID
            or approval['parent_rows_sha256'] != PARENT_ROWS_SHA256
            or approval['parent_events_sha256'] != PARENT_EVENTS_SHA256
            or approval['execution_build_sha256'] != execution_build_sha256()
            or timestamp(approval['approved_at']) <= timestamp(parent['finished_at'])
            or approval['approval_id'] == parent['approval_id']
            or approval['approval_id'] == json.loads(original['approval_json']).get('approval_id')):
        fail('POST_COORDINATION_NEW_APPROVAL_REQUIRED')


def new_approval(envelope):
    from contract_http_zero_recovery import ORIGINAL_EXECUTION_ROWS_SHA256, ORIGINAL_PREPARATION_ROWS_SHA256
    return {'kind':APPROVAL_KIND, 'policy':POLICY, 'approval_id':str(uuid.uuid4()),
            'batch_id':envelope['request']['batch']['batch_id'], 'request_sha256':envelope['request_sha256'],
            'original_execution_sha256':ORIGINAL_EXECUTION_ROWS_SHA256,
            'original_preparation_sha256':ORIGINAL_PREPARATION_ROWS_SHA256,
            'account_marker':envelope['account_marker'], 'approved_at':datetime.now(timezone.utc).isoformat(),
            'manual_once':True, 'parent_recovery_id':PARENT_ID, 'parent_rows_sha256':PARENT_ROWS_SHA256,
            'parent_events_sha256':PARENT_EVENTS_SHA256, 'execution_build_sha256':execution_build_sha256()}


def install_schema(c):
    # New tables only. Original tables keep their columns, PKs and UNIQUE rules.
    c.executescript(f"""
        CREATE TABLE IF NOT EXISTS {ROUNDS} (
            recovery_id TEXT PRIMARY KEY,
            preparation_id TEXT NOT NULL UNIQUE REFERENCES sync_reviewed_executions(preparation_id) ON DELETE CASCADE,
            parent_recovery_id TEXT NOT NULL UNIQUE REFERENCES sync_reviewed_recoveries(recovery_id) ON DELETE CASCADE,
            policy TEXT NOT NULL CHECK(policy='{POLICY}'),
            parent_rows_sha256 TEXT NOT NULL,
            parent_events_sha256 TEXT NOT NULL,
            execution_build_sha256 TEXT NOT NULL,
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
        CREATE TABLE IF NOT EXISTS {EVENTS} (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recovery_id TEXT NOT NULL REFERENCES {ROUNDS}(recovery_id) ON DELETE CASCADE,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS {RECEIPTS} (
            recovery_id TEXT PRIMARY KEY REFERENCES {ROUNDS}(recovery_id) ON DELETE CASCADE,
            response_json TEXT NOT NULL, response_sha256 TEXT NOT NULL, recorded_at TEXT NOT NULL
        );
    """)
    install_round_guards(c, ROUNDS, EVENTS, RECEIPTS, 'sync_post_coordination_resume_no_reset',
        ('parent_recovery_id','policy','parent_rows_sha256','parent_events_sha256','execution_build_sha256'))


class ResumeLedger(HttpZeroRecoveryStoreMixin):
    """Fixed table adapter sharing attempt/stop/reopen/receipt code with HTTP-0 recovery."""
    _rounds, _events, _receipts = ROUNDS, EVENTS, RECEIPTS

    def __init__(self, store):
        self._store = store

    def _reader(self): return self._store._reader()
    def _transaction(self): return self._store._transaction()

    def _history(self, c, local_key, envelope):
        return history(c, local_key, envelope)[0]

    def _receipt_history(self, c, row):
        preparation = c.execute('SELECT * FROM sync_contract_preparations WHERE preparation_id=?', (row['preparation_id'],)).fetchone()
        if not preparation: fail('POST_COORDINATION_HISTORY_REQUIRED')
        self._history(c, preparation['local_key'], _read_envelope(preparation))

    def claim_http_zero_recovery(self, local_key, envelope, approval):
        from reviewed_contract_sender import PROCESS_TOKEN, now
        from contract_http_zero_recovery import ORIGINAL_EXECUTION_ROWS_SHA256, ORIGINAL_PREPARATION_ROWS_SHA256
        with self._transaction() as c:
            original, parent = history(c, local_key, envelope)
            validate_approval(approval, envelope, original, parent)
            if c.execute(f'SELECT 1 FROM {ROUNDS} WHERE preparation_id=? OR parent_recovery_id=?',
                         (original['preparation_id'], PARENT_ID)).fetchone():
                fail('POST_COORDINATION_ALREADY_STARTED')
            if self._store.contract_path_enabled(local_key): fail('POST_COORDINATION_CLOSED_GATE_REQUIRED')
            rid = 'post-coordination-' + str(uuid.uuid4())
            values = dict(recovery_id=rid, preparation_id=original['preparation_id'], parent_recovery_id=PARENT_ID,
                policy=POLICY, parent_rows_sha256=PARENT_ROWS_SHA256, parent_events_sha256=PARENT_EVENTS_SHA256,
                execution_build_sha256=approval['execution_build_sha256'],
                original_execution_sha256=ORIGINAL_EXECUTION_ROWS_SHA256,
                original_preparation_sha256=ORIGINAL_PREPARATION_ROWS_SHA256,
                request_sha256=envelope['request_sha256'], approval_id=approval['approval_id'],
                approval_json=canonical_json(approval), owner_token=PROCESS_TOKEN, started_at=now(), state='preparing')
            c.execute(f"INSERT INTO {ROUNDS} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            c.execute(f'INSERT INTO {EVENTS} (recovery_id,metadata_json) VALUES (?,?)',
                      (rid, canonical_json({'stage':'claimed','observed_at':now(),'policy':POLICY,
                                            'parent_recovery_id':PARENT_ID,'execution_build_sha256':approval['execution_build_sha256']})))
            return rid

    def check_http_zero_round(self, local_key, envelope, recovery_id, *, mark_attempt=False):
        with self._transaction() as c:
            row = c.execute(f'SELECT * FROM {ROUNDS} WHERE recovery_id=?', (recovery_id,)).fetchone()
            if (not row or row['parent_recovery_id'] != PARENT_ID or row['policy'] != POLICY
                    or row['parent_rows_sha256'] != PARENT_ROWS_SHA256 or row['parent_events_sha256'] != PARENT_EVENTS_SHA256
                    or row['execution_build_sha256'] != execution_build_sha256()):
                fail('POST_COORDINATION_HISTORY_REQUIRED')
            super().check_http_zero_round(local_key, envelope, recovery_id, mark_attempt=mark_attempt)
