"""Review-only reverse canary. No RPC, gate setter or outbound promotion API."""
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

from sync_contract import (
    CLIENT_BUILD_ID, SyncContractError, build_atomic_structure_request,
    canonical_json, json_sha256, require_server_compatibility, require_uuid,
)

PROJECT = '1bd47431-0773-482c-8eb5-ac9e2952b6f4'
PARENT = '14df4a55-b7cd-4790-bc53-f84148418c0f'
FIRST = 'c0b43fc4-89a0-4a5a-b140-768a62cfde5a'
SECOND = '4711b2ce-5de9-44ba-80fe-570515839549'
ORDER = '68c1a7b5-0dda-49ba-bc9a-42e92ce2b758'
PURPOSE = 'reverse-empty-third-volume-v1'


def _refuse(code='CONTRACT_PREPARATION_BASELINE_CHANGED'):
    raise SyncContractError(code)


def _read_envelope(row):
    envelope = json.loads(row['envelope_json'])
    if (json_sha256(envelope) != row['envelope_sha256']
        or json_sha256(envelope['request']) != envelope['request_sha256']):
        _refuse('CONTRACT_PREPARATION_CORRUPT')
    return envelope


class ContractPreparationStoreMixin:
    def _reverse_preparation_baseline(self, connection, local_key, *, executing=False):
        project = connection.execute(
            'SELECT * FROM sync_projects WHERE local_key=?', (local_key,)
        ).fetchone()
        if not project or project['project_id'] != PROJECT:
            _refuse('CONTRACT_PREPARATION_WRONG_PROJECT')
        if project['contract_path_enabled'] and not executing:
            _refuse('CONTRACT_PREPARATION_GATE_OPEN')
        if project['server_state'] != 'active':
            _refuse()
        require_server_compatibility(
            project_sync_mode=project['project_sync_mode'],
            migration_epoch=project['migration_epoch'],
            server_protocol_version=project['server_protocol_version'],
            server_contract_sha256=project['active_contract_sha256'],
            server_capabilities=json.loads(project['server_capabilities_json'] or '[]'),
        )
        if project['project_sync_mode'] != 'LEGACY' or project['migration_epoch'] != 0:
            _refuse()
        def rows(sql):
            return [dict(r) for r in connection.execute(sql, (local_key,))]
        folders = rows('SELECT folder_id,parent_folder_id,name,local_path,revision,is_deleted '
                       'FROM sync_folders WHERE local_key=? ORDER BY folder_id')
        documents = rows('SELECT document_id,parent_folder_id,server_path,revision,structure_revision,'
                         'name,is_deleted FROM sync_documents WHERE local_key=? ORDER BY document_id')
        orders = rows('SELECT tree_order_id,parent_folder_id,parent_path,children_json,revision '
                     'FROM sync_tree_orders WHERE local_key=? ORDER BY tree_order_id')
        active = {f['folder_id']: f for f in folders if not f['is_deleted']}
        if len(active) != 12 or len([d for d in documents if not d['is_deleted']]) != 26:
            _refuse()
        if PARENT not in active or active[PARENT]['local_path'] != '메인/원고':
            _refuse()
        for entity, name in ((FIRST, '1권'), (SECOND, '2권')):
            f = active.get(entity)
            if not f or (f['parent_folder_id'], f['name'], f['local_path']) != (
                PARENT, name, '메인/원고/' + name
            ):
                _refuse()
        if {f['folder_id'] for f in active.values() if f['parent_folder_id'] == PARENT} != {FIRST, SECOND}:
            _refuse()
        if any(d['parent_folder_id'] in (PARENT, SECOND) for d in documents if not d['is_deleted']):
            _refuse()
        if any(f['parent_folder_id'] == SECOND for f in active.values()):
            _refuse()
        if len(orders) != 1 or orders[0] != {
            'tree_order_id': ORDER, 'parent_folder_id': PARENT,
            'parent_path': '메인/원고', 'children_json': canonical_json([FIRST, SECOND]), 'revision': 1,
        }:
            _refuse()
        operation_ids = connection.execute(
            'SELECT operation_id FROM sync_operations WHERE local_key=? UNION '
            'SELECT operation_id FROM sync_structure_operations WHERE local_key=?', (local_key, local_key)
        ).fetchall()
        for row in operation_ids:
            if self._derived_state(connection, row['operation_id']) not in ('completed', 'cancelled', 'superseded'):
                _refuse('CONTRACT_PREPARATION_QUEUE_NOT_EMPTY')
        if connection.execute('SELECT 1 FROM sync_contract_batches WHERE local_key=?', (local_key,)).fetchone():
            _refuse('CONTRACT_PREPARATION_QUEUE_NOT_EMPTY')
        events = [dict(r) for r in connection.execute(
            'SELECT event_id,event_sequence,event_type FROM sync_operation_events WHERE operation_id IN '
            '(SELECT operation_id FROM sync_operations WHERE local_key=? UNION '
            'SELECT operation_id FROM sync_structure_operations WHERE local_key=?) ORDER BY event_id',
            (local_key, local_key),
        )]
        return {'project_id': PROJECT, 'project_sync_mode': 'LEGACY', 'migration_epoch': 0,
                'folders': folders, 'documents': documents, 'tree_orders': orders, 'operation_events': events}

    def reverse_contract_preparation(self, local_key):
        with self._reader() as connection:
            row = connection.execute('SELECT envelope_json,envelope_sha256 FROM sync_contract_preparations '
                                     'WHERE local_key=? AND purpose=?', (local_key, PURPOSE)).fetchone()
            if row is None:
                return None
            return _read_envelope(row)

    def prepare_reverse_contract(self, local_key, *, writer_device_id, client_build_id, account_marker, validate_local):
        """One immutable row, never a batch/operation. Callback performs read-only app checks."""
        require_uuid(writer_device_id, 'writer_device_id')
        if not client_build_id or not account_marker:
            _refuse('CONTRACT_PREPARATION_IDENTITY_REQUIRED')
        with self._transaction() as connection:
            baseline = self._reverse_preparation_baseline(connection, local_key)
            local_metadata = validate_local()
            fingerprint = json_sha256({'store': baseline, 'local': local_metadata})
            row = connection.execute('SELECT envelope_json,envelope_sha256 FROM sync_contract_preparations '
                                     'WHERE local_key=? AND purpose=?', (local_key, PURPOSE)).fetchone()
            if row:
                envelope = _read_envelope(row)
                if (envelope['baseline_sha256'], envelope['account_marker'],
                    envelope['request']['batch']['writer_device_id'], envelope['request']['batch']['client_build_id']) != (
                    fingerprint, account_marker, writer_device_id, client_build_id):
                    _refuse()
                return envelope
            entity_id = str(uuid.uuid4())
            request = build_atomic_structure_request(
                project_id=PROJECT, project_sync_mode='LEGACY', migration_epoch=0,
                writer_device_id=writer_device_id, client_build_id=client_build_id,
                ordered_intents=[
                    {'entity_kind': 'folder', 'entity_id': entity_id, 'intent_kind': 'create',
                     'base_revision': 0, 'payload': {'parent_folder_id': PARENT, 'name': '3권'}},
                    {'entity_kind': 'tree_order', 'entity_id': ORDER, 'intent_kind': 'reorder',
                     'base_revision': 1, 'payload': {'parent_folder_id': PARENT, 'children': [FIRST, SECOND, entity_id]}},
                ],
            )
            envelope = {'kind': 'writerpad_contract_preparation_review', 'format_version': 1,
                        'purpose': PURPOSE, 'prepared_at': datetime.now(timezone.utc).isoformat(),
                        'account_marker': account_marker, 'baseline_sha256': fingerprint,
                        'baseline_scope': 'local received metadata and disk/identity; not a fresh server snapshot',
                        'request': request, 'request_sha256': json_sha256(request),
                        'dispatch_policy': 'review_only_no_automatic_or_manual_send'}
            connection.execute('INSERT INTO sync_contract_preparations '
                               '(preparation_id,local_key,purpose,envelope_json,envelope_sha256) VALUES (?,?,?,?,?)',
                               (request['batch']['batch_id'], local_key, PURPOSE, canonical_json(envelope), json_sha256(envelope)))
            return envelope


class ContractPreparationManagerMixin:
    def _reverse_preparation_context(self):
        if not self.is_v2_enabled or self._v2_context.get('project_id') != PROJECT:
            _refuse('CONTRACT_PREPARATION_WRONG_PROJECT')
        marker = self._contract_identity()
        if not marker:
            _refuse('CONTRACT_PREPARATION_IDENTITY_REQUIRED')
        return self._v2_context['local_key'], marker

    def _reverse_local_metadata(self, context_key, local_key, *, executing=False, observation_sink=None):
        from contract_readiness_diagnostics import observe_readiness
        observation = observe_readiness(self, context_key, executing=executing)
        if observation_sink is not None:
            observation_sink(observation)
        if not observation['all_conditions_met']:
            error = SyncContractError('CONTRACT_PREPARATION_NOT_READY')
            error.readiness_observation = observation
            raise error
        from project_identity_v1 import read_identity
        root = os.path.abspath(self._v2_wpm.writing_root_path)
        if self._v2_store.local_key_for(root) != local_key:
            _refuse('CONTRACT_PREPARATION_WRONG_PROJECT')
        identity = read_identity(os.path.dirname(root))
        if identity['project']['uuid'] != PROJECT:
            _refuse()
        nodes = identity['nodes']
        children = sorted((n for n in nodes if n['parent_uuid'] == PARENT), key=lambda n: n['order'])
        if [n['uuid'] for n in children] != [FIRST, SECOND]:
            _refuse()
        by_id = {n['uuid']: n for n in nodes}
        for entity, path in ((PARENT, '메인/원고'), (FIRST, '메인/원고/1권'), (SECOND, '메인/원고/2권')):
            if (by_id.get(entity, {}).get('legacy_path') != path
                or by_id.get(entity, {}).get('kind') != 'folder'):
                _refuse()
        if len(nodes) != 37:
            _refuse()
        parent_path = os.path.join(root, '메인', '원고')
        if sorted(os.listdir(parent_path)) != ['1권', '2권']:
            _refuse()
        if os.listdir(os.path.join(parent_path, '2권')):
            _refuse()
        with open(os.path.join(root, '설정.json'), encoding='utf-8-sig') as handle:
            tree_order = json.load(handle).get('tree_order')
        if (tree_order or {}).get('메인/원고') != ['1권', '2권']:
            _refuse()
        # Names and sizes only. Never open manuscript files.
        paths = []
        for directory, dirs, files in os.walk(parent_path):
            for name in sorted(dirs + files):
                path = os.path.join(directory, name)
                paths.append([os.path.relpath(path, root).replace('\\', '/'),
                              os.path.getsize(path) if os.path.isfile(path) else None])
        if context_key != self._contract_context_key():
            _refuse('CONTRACT_PREPARATION_NOT_READY')
        return {'nodes': nodes, 'tree_order': [[k, v] for k, v in sorted(tree_order.items())],
                'disk_entries': sorted(paths)}

    def prepare_reverse_contract_review(self):
        with self._contract_lock:
            local_key, marker = self._reverse_preparation_context()
            context_key = self._contract_context_key()
            return self._v2_store.prepare_reverse_contract(
                local_key, writer_device_id=self._v2_device_id, client_build_id=CLIENT_BUILD_ID,
                account_marker=marker, validate_local=lambda: self._reverse_local_metadata(context_key, local_key),
            )

    def reverse_contract_review(self):
        with self._contract_lock:
            local_key, marker = self._reverse_preparation_context()
            envelope = self._v2_store.reverse_contract_preparation(local_key)
            if not envelope:
                _refuse('CONTRACT_PREPARATION_MISSING')
            if envelope['account_marker'] != marker:
                _refuse('CONTRACT_PREPARATION_IDENTITY_REQUIRED')
            return envelope

    def export_reverse_contract_review(self, destination):
        """Export the original envelope even if its baseline is now stale; never reissue."""
        with self._contract_lock:
            envelope = self.reverse_contract_review()
            root = os.path.realpath(self._v2_wpm.writing_root_path)
        destination = os.path.realpath(destination)
        # An export may not overwrite a manuscript or either local identity store.
        project_root = os.path.dirname(root)
        try:
            inside_project = os.path.commonpath([os.path.normcase(destination), os.path.normcase(project_root)]) == os.path.normcase(project_root)
        except ValueError:  # Different Windows drives cannot share a project directory.
            inside_project = False
        if inside_project:
            _refuse('CONTRACT_PREPARATION_EXPORT_LOCATION')
        if os.path.splitext(destination)[1].lower() != '.json':
            _refuse('CONTRACT_PREPARATION_EXPORT_LOCATION')
        raw = (canonical_json(envelope) + '\n').encode('utf-8')
        fd, temporary = tempfile.mkstemp(prefix='.contract-review-', dir=os.path.dirname(destination))
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return envelope
