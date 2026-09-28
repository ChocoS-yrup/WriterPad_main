"""Temporary local gate for the dedicated path; the global send hold stays held.

Persist undo data before the SQLite commit. Cleanup needs no Auth or foreground
grant, never dispatches pending operations, and restores only owned gate fields.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os

from general_validation_boundary import GeneralValidationDenied, check_store_binding
from general_validation_plan import PLAN_ID, PROJECT_ID
from sync_contract import require_server_compatibility

FIELDS = ('contract_path_enabled','contract_path_enabled_at','updated_at')
STAGES = ('create','finish')
CONTROL_PLANS = {PLAN_ID: STAGES, 'general-editor-20260913-v1': ('manual', 'auto'),
    'normal-editor-single-document-20260913-v1': ('save',)}
LEGACY_HELD_MARKER = b'preparation-only; no outbound release\r\n'


def require(condition, code):
    if not condition:
        raise GeneralValidationDenied(code)


def scope_path(directory, stage, *, plan_id=PLAN_ID):
    require(stage in CONTROL_PLANS.get(plan_id, ()), 'UNKNOWN_CONTROL_STAGE')
    return Path(directory)/(plan_id+'-'+stage+'-control.json')


def _write_exclusive(path, value):
    # A partial record survives a disk error, blocking further attempts.
    with Path(path).open('xb') as stream:
        stream.write((json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode())
        stream.flush()
        os.fsync(stream.fileno())


def _read(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.resolve() == path.absolute()
            and path.stat().st_size <= 32768, 'CONTROL_RECORD_INVALID')
    return json.loads(path.read_text('utf-8'))


def _restored(path):
    marker = Path(str(path)+'.restored')
    if not marker.exists():
        return False
    try:
        return _read(marker) == {'manifest_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(), 'restored':True}
    except (OSError,ValueError,GeneralValidationDenied):
        return False


def pending_controls(directory, *, plan_id=PLAN_ID):
    require(plan_id in CONTROL_PLANS, 'UNKNOWN_CONTROL_PLAN')
    paths = [scope_path(directory,stage,plan_id=plan_id) for stage in CONTROL_PLANS[plan_id]]
    return [path for path in paths if path.exists() and not _restored(path)]


class TemporaryWriteScope:
    def __init__(self, *, store, local_key, directory, stage, guard, plan_id=PLAN_ID):
        self.store,self.key,self.guard = store,local_key,guard
        self.plan_id = plan_id
        self.path = scope_path(directory,stage,plan_id=plan_id)
        self.hold = Path(store.db_path).absolute().parent/('.general-test-send-hold-'+PROJECT_ID)
        self.record = None
        self.active = False

    def _hold_bytes(self):
        require(self.hold.is_file() and not self.hold.is_symlink() and self.hold.resolve() == self.hold.absolute()
                and self.hold.stat().st_size <= 4096, 'ORIGINAL_SEND_HOLD_REQUIRED')
        value = self.hold.read_bytes()
        # Existing preparation marker is deliberately not JSON. Match its exact
        # original bytes; never rewrite it or accept arbitrary malformed JSON.
        if value == LEGACY_HELD_MARKER:
            return value
        data = json.loads(value)
        require(isinstance(data,dict) and data.get('format') == 1 and data.get('project_id') == PROJECT_ID
                and isinstance(data.get('state'),str) and data['state'] and data['state'] != 'released',
                'ORIGINAL_SEND_HOLD_REQUIRED')
        return value

    def open(self):
        require(not pending_controls(self.path.parent,plan_id=self.plan_id), 'CONTROL_RESTORE_REQUIRED')
        require(not self.path.exists(), 'CONTROL_STAGE_ALREADY_USED')
        self.guard()
        before = dict(check_store_binding(self.store,self.key))
        require(before['contract_path_enabled'] == 0, 'ORIGINAL_CLOSED_GATE_REQUIRED')
        require_server_compatibility(project_sync_mode=before['project_sync_mode'],migration_epoch=before['migration_epoch'],
            server_protocol_version=before['server_protocol_version'],server_contract_sha256=before['active_contract_sha256'],
            server_capabilities=json.loads(before['server_capabilities_json']))
        hold_sha = hashlib.sha256(self._hold_bytes()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        during = dict(before,contract_path_enabled=1,contract_path_enabled_at=now,updated_at=now)
        record = {'format':1,'plan_id':self.plan_id,'project_id':PROJECT_ID,'local_key':self.key,
            'db_path':str(Path(self.store.db_path).resolve()),'before':before,'during':during,'hold_sha256':hold_sha}
        with self.store._transaction() as conn:
            current = conn.execute('SELECT * FROM sync_projects WHERE local_key=?',(self.key,)).fetchone()
            require(current and dict(current) == before, 'CONTROL_BASELINE_CHANGED')
            self.guard()
            require(hashlib.sha256(self._hold_bytes()).hexdigest() == hold_sha, 'SEND_HOLD_CHANGED')
            _write_exclusive(self.path,record)
            # Record owns precisely these three columns; mode, epoch and server
            # compatibility metadata are observed, never activated or migrated.
            self.guard()
            require(hashlib.sha256(self._hold_bytes()).hexdigest() == hold_sha, 'SEND_HOLD_CHANGED')
            conn.execute('UPDATE sync_projects SET contract_path_enabled=?,contract_path_enabled_at=?,updated_at=? WHERE local_key=?',
                         tuple(during[k] for k in FIELDS)+(self.key,))
        self.record,self.active = record,True
        # The caller's finally block also handles cancellation after this commit.
        self.check()

    def check(self):
        require(self.active and self.record is not None, 'DEDICATED_WRITE_SCOPE_REQUIRED')
        require(dict(check_store_binding(self.store,self.key)) == self.record['during'], 'CONTROL_GATE_CHANGED')
        require(hashlib.sha256(self._hold_bytes()).hexdigest() == self.record['hold_sha256'], 'SEND_HOLD_CHANGED')

    def restore(self):
        if self.path.exists():
            restore_control(self.store,self.key,self.path,plan_id=self.plan_id)
        self.active = False


def restore_control(store, local_key, path, *, plan_id=PLAN_ID):
    """Offline, idempotent cleanup, including crash before or after gate commit."""
    path = Path(path)
    require(plan_id in CONTROL_PLANS, 'UNKNOWN_CONTROL_PLAN')
    require(path.name in {scope_path(path.parent,s,plan_id=plan_id).name for s in CONTROL_PLANS[plan_id]}, 'CONTROL_RECORD_SCOPE_CHANGED')
    record = _read(path)
    require(isinstance(record,dict) and record.get('format') == 1 and record.get('plan_id') == plan_id
            and record.get('project_id') == PROJECT_ID and record.get('local_key') == local_key
            and record.get('db_path') == str(Path(store.db_path).resolve()), 'CONTROL_RECORD_SCOPE_CHANGED')
    before,during = record['before'],record['during']
    require(isinstance(before,dict) and isinstance(during,dict) and set(before) == set(during)
            and (before.get('local_key'),before.get('project_id'),before.get('project_sync_mode'),before.get('migration_epoch')) ==
                (local_key,PROJECT_ID,'ID_BASED',1)
            and before.get('contract_path_enabled') == 0 and during.get('contract_path_enabled') == 1
            and all(before[k] == during[k] for k in before if k not in FIELDS), 'CONTROL_UNDO_FIELDS_CHANGED')
    with store._transaction() as conn:
        row = conn.execute('SELECT * FROM sync_projects WHERE local_key=?',(local_key,)).fetchone()
        current = dict(row) if row else None
        # Do not overwrite an independent edit. The untouched global hold still
        # blocks ordinary dispatch when cleanup needs manual diagnosis.
        require(current == before or current == during, 'CONTROL_RESTORE_CONFLICT')
        if current == during:
            conn.execute('UPDATE sync_projects SET contract_path_enabled=?,contract_path_enabled_at=?,updated_at=? WHERE local_key=?',
                         tuple(before[k] for k in FIELDS)+(local_key,))
    require(dict(check_store_binding(store,local_key)) == before, 'CONTROL_RESTORE_NOT_CONFIRMED')
    marker = Path(str(path)+'.restored')
    if marker.exists():
        require(_restored(path), 'CONTROL_RESTORE_MARKER_CHANGED')
    else:
        _write_exclusive(marker,{'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'restored':True})
    return True


def restore_pending_controls(store, local_key, directory, *, plan_id=PLAN_ID):
    for path in pending_controls(directory,plan_id=plan_id):
        restore_control(store,local_key,path,plan_id=plan_id)
    require(not pending_controls(directory,plan_id=plan_id), 'CONTROL_RESTORE_REQUIRED')
