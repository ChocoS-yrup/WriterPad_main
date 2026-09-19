"""Private multi-document workspace, immutable wire requests and durable budgets.

The original single-document DB/files are never migrated or reinitialized here.
SQLite is authoritative; UUID-named TXT projections are recoverable caches.
All state transitions and their private audit snapshots share one transaction.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import UUID, uuid4, uuid5

from integrated_editor_plan import BUILD_ID, PROJECT_ID, PROTECTED_DOCUMENT_ID
from sync_contract import (build_atomic_structure_request, build_document_commit_request,
                           canonical_json, json_sha256, normalize_storage_name, require_uuid)


class EditorError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise EditorError(code)


def body(text):
    require(isinstance(text, str) and '\r' not in text and '\x00' not in text,
            'INVALID_BODY')
    require(len(text.encode('utf-8')) <= 10_485_760, 'BODY_TOO_LARGE')
    return text


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def body_boundary(text):
    return dict(bytes=len(text.encode('utf-8')), ends_lf=text.endswith('\n'), sha256=digest(text))


def safe_path(path):
    path = Path(path).absolute()
    require(path.resolve() == path, 'WORKSPACE_LINK_REFUSED')
    return path


def order_id(parent):
    return str(uuid5(UUID(PROJECT_ID), 'tree-order:' + (parent or 'root')))


def semantic(node):
    return {k: v for k, v in node.items() if k not in ('revision', 'srev')}


def validate_nodes(nodes):
    names = set()
    for key, node in nodes.items():
        require(require_uuid(key, 'node_id') == key and node['id'] == key, 'INVALID_NODE_ID')
        require(node['kind'] in ('folder', 'document') and type(node['deleted']) is bool,
                'INVALID_NODE')
        require(type(node['revision']) is int and node['revision'] >= 0
                and type(node['srev']) is int and node['srev'] >= 1, 'INVALID_REVISION')
        normalized = normalize_storage_name(node['name'])
        parent = node['parent']
        require(parent is None or parent in nodes and nodes[parent]['kind'] == 'folder',
                'PARENT_NOT_FOUND')
        seen = {key}
        while parent:
            require(parent not in seen, 'FOLDER_CYCLE')
            seen.add(parent)
            require(node['deleted'] or not nodes[parent]['deleted'], 'PARENT_DELETED')
            parent = nodes[parent]['parent']
        if not node['deleted']:
            name_key = (node['parent'], normalized.utf8_hex)
            require(name_key not in names, 'NAME_COLLISION')
            names.add(name_key)
        if node['kind'] == 'document':
            body(node['content'])


class IntegratedStore:
    def __init__(self, directory, *, device_id, local_project_id, clock=time.time):
        self.directory = safe_path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = safe_path(self.directory / 'workspace.sqlite3')
        self.files = safe_path(self.directory / 'manuscripts')
        self.files.mkdir(exist_ok=True)
        self.device = require_uuid(device_id, 'device_id')
        self.local_project_id = str(local_project_id)
        self.clock = clock
        self.lock = threading.RLock()
        # One shared lock covers both manual and automatic dispatch, including reads.
        self.dispatch_lock = threading.Lock()
        with self.connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY, event TEXT NOT NULL, data TEXT NOT NULL,
                    previous TEXT NOT NULL, sha256 TEXT NOT NULL);
            ''')
            row = conn.execute('SELECT data FROM state WHERE id=1').fetchone()
            if row is None:
                state = dict(version=1, project_id=PROJECT_ID, local_project_id=self.local_project_id,
                             device_id=self.device, nodes={}, orders={}, jobs=[], batches=[],
                             conflicts=[], runs={}, selected=None, seeded=False, projections={}, trash_groups={})
                self._persist(conn, state, 'created')
            else:
                state = json.loads(row[0])
                require((state['version'], state['project_id'], state['local_project_id'], state['device_id'])
                        == (1, PROJECT_ID, self.local_project_id, self.device), 'WORKSPACE_BINDING_CHANGED')
            self._verify(conn)

    @contextmanager
    def connection(self):
        with self.lock:
            safe_path(self.path)
            conn = sqlite3.connect(self.path, timeout=10)
            try:
                conn.execute('PRAGMA synchronous=FULL')
                conn.execute('BEGIN IMMEDIATE')
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _verify(self, conn):
        previous = ''
        last = None
        for expected, (index, event, data, prev, sha) in enumerate(conn.execute('SELECT * FROM events ORDER BY sequence'), 1):
            require(index == expected and prev == previous and sha == json_sha256([index, event, data, prev]), 'AUDIT_CHANGED')
            previous, last = sha, data
        row = conn.execute('SELECT data FROM state WHERE id=1').fetchone()
        require(row and row[0] == last, 'STATE_AUDIT_CHANGED')

    def _persist(self, conn, state, event):
        # Local state contains UUID map keys and wall-clock fractions, which are
        # intentionally forbidden by the stricter, separate wire canonicalizer.
        data = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        previous = conn.execute('SELECT sequence,sha256 FROM events ORDER BY sequence DESC LIMIT 1').fetchone()
        index, prev = (previous[0] + 1, previous[1]) if previous else (1, '')
        conn.execute('INSERT INTO events VALUES(?,?,?,?,?)',
                     (index, event, data, prev, json_sha256([index, event, data, prev])))
        conn.execute('INSERT OR REPLACE INTO state VALUES(1,?)', (data,))

    def state(self):
        with self.connection() as conn:
            return json.loads(conn.execute('SELECT data FROM state WHERE id=1').fetchone()[0])

    @contextmanager
    def change(self, event):
        with self.connection() as conn:
            # The complete chain is verified when opening. Check the current
            # committed head per edit so typing does not rescan all old bodies.
            current = conn.execute('SELECT data FROM state WHERE id=1').fetchone()[0]
            tail = conn.execute('SELECT * FROM events ORDER BY sequence DESC LIMIT 1').fetchone()
            require(tail and tail[2] == current and tail[4] == json_sha256(list(tail[:4])), 'STATE_AUDIT_CHANGED')
            state = json.loads(current)
            yield state
            self._persist(conn, state, event)

    def seed(self, nodes, orders):
        """One offline import of clean copies; never reset an existing session."""
        nodes = {n['id']: deepcopy(n) for n in nodes}
        validate_nodes(nodes)
        with self.change('seeded') as s:
            require(not s['seeded'] and not s['nodes'], 'ALREADY_SEEDED')
            for key, node in nodes.items():
                s['nodes'][key] = dict(base=deepcopy(node), local=node,
                                       draft=node.get('content'), protected=True)
            for order in orders:
                require(len(order['children']) == len(set(order['children'])), 'INVALID_ORDER')
                s['orders'][order['id']] = dict(base=deepcopy(order), local=deepcopy(order))
            s['seeded'] = True
        self.materialize()

    def select(self, node_id):
        with self.change('selected') as s:
            node = s['nodes'][node_id]['local']
            require(node['kind'] == 'document' and not node['deleted'], 'DOCUMENT_NOT_EDITABLE')
            s['selected'] = node_id
        return self.state()['nodes'][node_id]['draft']

    @staticmethod
    def _editable(s, key):
        require(key in s['nodes'] and not s['nodes'][key]['protected']
                and key != PROTECTED_DOCUMENT_ID, 'PROTECTED_NODE')
        return s['nodes'][key]

    def preserve_draft(self, key, text, *, source='unspecified'):
        body(text)
        if self.state()['nodes'][key]['draft'] == text:
            return
        with self.change('draft_saved') as s:
            row = self._editable(s, key)
            require(row['local']['kind'] == 'document' and not row['local']['deleted'], 'DOCUMENT_NOT_EDITABLE')
            s['last_input_boundary'] = dict(node_id=key, source=source,
                before=body_boundary(row['draft']), after=body_boundary(text))
            row['draft_origin'] = source
            row['draft'] = text

    @staticmethod
    def _job(s, kind, key, before, after):
        s['jobs'].append(dict(id=str(uuid4()), kind=kind, node_id=key,
                              before=deepcopy(before), after=deepcopy(after), status='pending', batch_id=None))

    def save(self, key, *, source='explicit'):
        with self.change('document_saved') as s:
            row = self._editable(s, key)
            require(row['local']['kind'] == 'document' and not row['local']['deleted'], 'DOCUMENT_NOT_EDITABLE')
            before = deepcopy(row['local'])
            row['local']['content'] = body(row['draft'])
            # Only the latest diagnostic is in state; the existing audit chain
            # preserves each boundary without duplicating manuscript text here.
            s['last_save_boundary'] = dict(node_id=key, source=source,
                input_source=row.get('draft_origin', 'unknown'),
                before=body_boundary(before['content']), after=body_boundary(row['local']['content']),
                changed=before['content'] != row['local']['content'])
            if before['content'] != row['local']['content']:
                self._job(s, 'body', key, before, row['local'])
        self.materialize()

    def create(self, kind, parent, name, *, content='', node_id=None):
        require(kind in ('folder', 'document'), 'INVALID_NODE_KIND')
        key = require_uuid(node_id or str(uuid4()), 'node_id')
        with self.change('node_created') as s:
            require(s['seeded'] and key not in s['nodes'], 'NODE_ALREADY_EXISTS')
            if parent is not None:
                p = s['nodes'][parent]['local']
                require(p['kind'] == 'folder' and not p['deleted'], 'PARENT_NOT_FOUND')
            node = dict(id=key, kind=kind, parent=parent, name=name, deleted=False, revision=0, srev=1)
            if kind == 'document':
                node['content'] = body(content)
            s['nodes'][key] = dict(base=None, local=node, draft=node.get('content'), protected=False)
            validate_nodes({k: r['local'] for k, r in s['nodes'].items()})
            self._job(s, 'create', key, None, node)
            self._refresh_orders(s, [parent])
        self.materialize()
        return key

    @staticmethod
    def _refresh_orders(s, parents):
        for parent in dict.fromkeys(parents):
            completed = s['nodes'].get(PROTECTED_DOCUMENT_ID)
            require(completed is None or completed['local']['parent'] != parent,
                    'COMPLETED_DOCUMENT_ORDER_PROTECTED')
            existing = next((r for r in s['orders'].values() if r['local']['parent'] == parent), None)
            if existing is None:
                ident = order_id(parent)
                value = dict(id=ident, parent=parent, children=[], revision=0)
                existing = s['orders'][ident] = dict(base=None, local=value)
            old = deepcopy(existing['local'])
            children = [key for key, row in s['nodes'].items()
                        if row['local']['parent'] == parent and not row['local']['deleted']]
            ordered = [key for key in old['children'] if key in children]
            ordered += [key for key in children if key not in ordered]
            existing['local']['children'] = ordered
            if old['children'] != ordered:
                IntegratedStore._job(s, 'order', old['id'], old, existing['local'])

    def relocate(self, key, *, parent, name):
        with self.change('node_relocated') as s:
            row = self._editable(s, key)
            require(not row['local']['deleted'], 'NODE_DELETED')
            before = deepcopy(row['local'])
            row['local'].update(parent=parent, name=name)
            validate_nodes({k: r['local'] for k, r in s['nodes'].items()})
            if semantic(before) != semantic(row['local']):
                self._job(s, 'relocate', key, before, row['local'])
                self._refresh_orders(s, [before['parent'], parent])

    def lifecycle(self, key, deleted):
        require(type(deleted) is bool, 'INVALID_LIFECYCLE')
        with self.change('nodes_deleted' if deleted else 'nodes_restored') as s:
            self._editable(s, key)
            require(s['nodes'][key]['local']['deleted'] is not deleted, 'LIFECYCLE_UNCHANGED')
            descendants = [key]
            if deleted:
                for item in descendants:
                    descendants += [k for k, r in s['nodes'].items()
                                    if r['local']['parent'] == item and k not in descendants
                                    and not r['local']['deleted']]
                s.setdefault('trash_groups', {})[key] = list(descendants)
            else:
                # Restore only this deletion's members, never older independent tombstones.
                descendants = s.get('trash_groups', {}).get(key, [key])
                descendants = [k for k in descendants if s['nodes'][k]['local']['deleted']]
            ordered = list(reversed(descendants)) if deleted else descendants
            parents = []
            for item in ordered:
                row = self._editable(s, item)
                # Never silently include unsaved work in a delete or erase a draft.
                require(row['local']['kind'] != 'document' or row['draft'] == row['local']['content'],
                        'SAVE_DRAFT_BEFORE_STRUCTURE_CHANGE')
                before = deepcopy(row['local'])
                row['local']['deleted'] = deleted
                self._job(s, 'delete' if deleted else 'restore', item, before, row['local'])
                parents.append(before['parent'])
            validate_nodes({k: r['local'] for k, r in s['nodes'].items()})
            self._refresh_orders(s, parents)

    def reorder(self, parent, children):
        with self.change('children_reordered') as s:
            current = [key for key, row in s['nodes'].items()
                       if row['local']['parent'] == parent and not row['local']['deleted']]
            require(len(children) == len(set(children)) and set(children) == set(current), 'INVALID_ORDER')
            self._refresh_orders(s, [parent])
            row = next(r for r in s['orders'].values() if r['local']['parent'] == parent)
            before = deepcopy(row['local'])
            row['local']['children'] = list(children)
            if before != row['local']:
                self._job(s, 'order', before['id'], before, row['local'])

    def materialize(self):
        """Recover both sides of a crash during a UUID TXT projection replace."""
        with self.lock:
            s = self.state()
            for key, row in s['nodes'].items():
                if row['local']['kind'] != 'document':
                    continue
                target = safe_path(self.files / (key + '.txt'))
                content = row['local']['content'].encode('utf-8')
                expected = s['projections'].get(key)
                actual = target.read_bytes() if target.exists() else None
                if actual != content:
                    require((actual is None and expected is None) or
                            (actual is not None and hashlib.sha256(actual).hexdigest() == expected),
                            'EXTERNAL_FILE_CHANGE_PRESERVED')
                    temp = safe_path(self.files / (key + '.' + str(uuid4()) + '.tmp'))
                    with temp.open('xb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Hold the store lock through replace and projection receipt.
                    require((target.read_bytes() if target.exists() else None) == actual, 'EXTERNAL_FILE_CHANGE_PRESERVED')
                    os.replace(temp, target)
                sha = hashlib.sha256(content).hexdigest()
                if expected != sha:
                    with self.change('file_projected') as live:
                        live['projections'][key] = sha

    def prepare_next(self):
        with self.change('request_prepared') as s:
            require(not s['conflicts'], 'CONFLICT_REVIEW_REQUIRED')
            job = next((j for j in s['jobs'] if j['status'] != 'completed'), None)
            if job is None:
                return None
            require(job['status'] not in ('blocked', 'conflict'), 'QUEUE_BLOCKED')
            if job['batch_id']:
                return deepcopy(next(b for b in s['batches'] if b['id'] == job['batch_id']))
            key, kind, after = job['node_id'], job['kind'], job['after']
            base = (s['orders'] if kind == 'order' else s['nodes'])[key]['base']
            initial_order = kind == 'order' and base is None and job['before']['revision'] == 0 and not job['before']['children']
            if job['before'] is not None and not initial_order:
                require(base is not None and semantic(base) == semantic(job['before']), 'PREDECESSOR_NOT_APPLIED')
            else:
                require(base is None, 'CREATE_BASE_CHANGED')
            args = dict(project_id=PROJECT_ID, project_sync_mode='ID_BASED', migration_epoch=1,
                        writer_device_id=self.device, client_build_id=BUILD_ID)
            if kind in ('body', 'create', 'delete', 'restore') and after['kind'] == 'document':
                request = build_document_commit_request(**args, document_id=key,
                    intent_kind='update' if kind == 'body' else kind,
                    base_revision=base['revision'] if base else 0, parent_folder_id=after['parent'],
                    name=after['name'], content=after['content'], is_deleted=after['deleted'],
                    structure_revision=base['srev'] if base else 1)
            else:
                intents = []
                if kind == 'order':
                    intents.append(dict(entity_kind='tree_order', entity_id=key, intent_kind='reorder',
                        base_revision=base['revision'] if base else 0,
                        payload=dict(parent_folder_id=after['parent'], children=after['children'])))
                else:
                    revision = (base['srev'] if base['kind'] == 'document' else base['revision']) if base else 0
                    def add(action, payload):
                        nonlocal revision
                        intents.append(dict(entity_kind=after['kind'], entity_id=key, intent_kind=action,
                                            base_revision=revision, payload=payload))
                        revision += 1
                    if kind == 'relocate':
                        if base['name'] != after['name']:
                            add('rename', dict(name=after['name']))
                        if base['parent'] != after['parent']:
                            add('move', dict(parent_folder_id=after['parent']))
                    else:
                        add(kind, {} if kind == 'delete' else dict(name=after['name'], parent_folder_id=after['parent']))
                request = build_atomic_structure_request(**args, ordered_intents=intents)
            batch = dict(id=request['batch']['batch_id'], job_id=job['id'], request=request,
                         sha256=json_sha256(request), status='prepared', account_id=None, response=None)
            job['batch_id'] = batch['id']
            s['batches'].append(batch)
            return deepcopy(batch)

    def record_response(self, batch_id, response):
        with self.change('response_captured') as s:
            b = next(b for b in s['batches'] if b['id'] == batch_id)
            require(b['response'] is None or b['response'] == response, 'RECEIPT_CHANGED')
            b['response'] = deepcopy(response)

    def complete(self, batch_id, response):
        from integrated_editor_sync import validate_response
        with self.change('batch_completed') as s:
            b = next(b for b in s['batches'] if b['id'] == batch_id)
            require(json_sha256(b['request']) == b['sha256'], 'REQUEST_CHANGED')
            validate_response(b['request'], response)
            if b['status'] == 'completed':
                require(b['response'] == response, 'RECEIPT_CHANGED')
                return
            require(b['status'] == 'uncertain', 'BATCH_NOT_ATTEMPTED')
            job = next(j for j in s['jobs'] if j['id'] == b['job_id'])
            row = (s['orders'] if job['kind'] == 'order' else s['nodes'])[job['node_id']]
            b['response'] = deepcopy(response)
            if response['applied'] is False:
                b['status'] = job['status'] = 'blocked'
                s['conflicts'].append(dict(id=str(uuid4()), kind='rejected', node_id=job['node_id'],
                    base=deepcopy(row['base']), local=deepcopy(row['local']), remote=None, response=deepcopy(response)))
                return
            base = deepcopy(job['after'])
            revision = response['results'][-1]['result_revision']
            if job['kind'] == 'relocate' and base['kind'] == 'document':
                base['revision'], base['srev'] = row['base']['revision'], revision
            else:
                base['revision'] = revision
                if base.get('kind') == 'document':
                    base['srev'] = response['results'][0]['structure_revision']
            row['base'] = base
            row['local']['revision'] = base['revision']
            if 'srev' in base:
                row['local']['srev'] = base['srev']
            b['status'] = job['status'] = 'completed'

    def accept_snapshot(self, nodes, orders, writable_ids):
        """Validate first, then accept every row atomically or preserve a conflict."""
        nodes = {n['id']: deepcopy(n) for n in nodes}
        orders = {o['id']: deepcopy(o) for o in orders}
        validate_nodes(nodes)
        with self.change('snapshot_received') as s:
            conflicts = []
            for key, row in s['nodes'].items():
                base, remote = row['base'], nodes.get(key)
                if base is None:
                    if remote is not None:
                        conflicts.append(dict(node_id=key, base=None, local=row['local'], remote=remote))
                    continue
                if remote is None:
                    raise EditorError('REMOTE_ROW_MISSING')
                require(remote['revision'] >= base['revision'] and remote['srev'] >= base['srev'], 'REMOTE_REVISION_ROLLBACK')
                if base['kind'] == 'document' and remote['revision'] == base['revision']:
                    require(remote['content'] == base['content'] and remote['deleted'] == base['deleted'],
                            'SAME_REVISION_DIFFERENT_BODY')
                if base['kind'] == 'document' and remote['srev'] == base['srev']:
                    require((remote['parent'], remote['name']) == (base['parent'], base['name']) or
                            base['deleted'] and not remote['deleted'], 'SAME_REVISION_DIFFERENT_STRUCTURE')
                if remote['revision'] == base['revision'] and remote['srev'] == base['srev']:
                    require(remote == base, 'SAME_REVISION_DIFFERENT_DATA')
                if remote != base:
                    dirty = semantic(row['local']) != semantic(base) or row['draft'] != row['local'].get('content')
                    pending = any(j['status'] != 'completed' and j['node_id'] == key for j in s['jobs'])
                    if row['protected'] or dirty or pending:
                        conflicts.append(dict(node_id=key, base=base, local=row['local'],
                                              draft=row['draft'], remote=remote))
            # Structure is a project-wide snapshot. Do not merge order changes over queued structure.
            for key, row in s['orders'].items():
                base, remote = row['base'], orders.get(key)
                if base is None and remote is not None:
                    conflicts.append(dict(node_id=key, base=None, local=row['local'], remote=remote))
                if base is not None:
                    require(remote is not None and remote['revision'] >= base['revision'], 'REMOTE_ORDER_ROLLBACK')
                    if remote['revision'] == base['revision']:
                        require(remote == base, 'SAME_REVISION_DIFFERENT_ORDER')
                    if remote != base and (row['local']['children'] != base['children'] or
                            any(j['status'] != 'completed' for j in s['jobs'])):
                        conflicts.append(dict(node_id=key, base=base, local=row['local'], remote=remote))
            if conflicts:
                record = dict(id=str(uuid4()), kind='snapshot', details=conflicts,
                              remote_nodes=nodes, remote_orders=orders)
                if not s['conflicts'] or json_sha256(s['conflicts'][-1].get('details')) != json_sha256(conflicts):
                    s['conflicts'].append(record)
                return False
            for key, remote in nodes.items():
                row = s['nodes'].get(key)
                if row is None:
                    s['nodes'][key] = dict(base=deepcopy(remote), local=remote,
                        draft=remote.get('content'), draft_origin='remote_snapshot',
                        protected=key not in writable_ids or key == PROTECTED_DOCUMENT_ID)
                elif remote != row['base']:
                    if row['draft'] != remote.get('content'):
                        row['draft_origin'] = 'remote_snapshot'
                    row.update(base=deepcopy(remote), local=remote, draft=remote.get('content'))
            for key, remote in orders.items():
                row = s['orders'].get(key)
                if row is None or row['base'] != remote:
                    s['orders'][key] = dict(base=deepcopy(remote), local=remote)
        self.materialize()
        return True

    def open_run(self, approval):
        from integrated_editor_sync import validate_approval
        validate_approval(approval, self.device)
        with self.change('execution_bound') as s:
            ident = approval['run_id']
            if ident in s['runs']:
                require(s['runs'][ident]['approval'] == approval, 'EXECUTION_SCOPE_CHANGED')
            else:
                now = self.clock()
                s['runs'][ident] = dict(approval=deepcopy(approval), used=0, started=now,
                    last_time=now, deadline=min(approval['expires_at'], now + approval['max_seconds']),
                    stopped=False, requests=[])
                if 'automatic_policy' in approval:
                    s['runs'][ident].update(empty_cycles_used=0, automatic_stop=None)

    def automatic_stop_reason(self, run_id):
        run = self.state()['runs'][run_id]
        policy = run['approval'].get('automatic_policy')
        if policy is None:
            return 'AUTOMATIC_POLICY_REQUIRED'
        used = run.get('empty_cycles_used')
        if type(used) is not int or used < 0:
            return 'AUTOMATIC_COUNTER_INVALID'
        if run.get('automatic_stop'):
            return run['automatic_stop']
        if used >= policy['max_empty_cycles']:
            return 'EMPTY_AUTO_LIMIT_REACHED'
        return None

    def reserve_empty_cycle(self, run_id):
        self.check_run(run_id)
        with self.change('empty_auto_cycle_reserved') as s:
            run = s['runs'][run_id]
            policy = run['approval'].get('automatic_policy')
            require(policy is not None, 'AUTOMATIC_POLICY_REQUIRED')
            used = run.get('empty_cycles_used')
            require(type(used) is int and used >= 0, 'AUTOMATIC_COUNTER_INVALID')
            require(not run.get('automatic_stop') and used < policy['max_empty_cycles'],
                    'EMPTY_AUTO_LIMIT_REACHED')
            run['empty_cycles_used'] += 1
            if run['empty_cycles_used'] == policy['max_empty_cycles']:
                run['automatic_stop'] = 'EMPTY_AUTO_LIMIT_REACHED'
                s['automatic_enabled'] = False

    def check_run(self, run_id):
        with self.change('execution_checked') as s:
            run = s['runs'][run_id]
            now = self.clock()
            if (not math.isfinite(now) or now < run['last_time'] or now >= run['deadline'] or
                    run['used'] >= run['approval']['max_requests']):
                run['stopped'] = True
            run['last_time'] = max(now, run['last_time'])
            allowed = not run['stopped']
        require(allowed, 'EXECUTION_LIMIT_REACHED')

    def reserve_request(self, run_id, method, path, request_sha, *, batch_id=None):
        self.check_run(run_id)
        with self.change('http_attempt') as s:
            run = s['runs'][run_id]
            require(not run['stopped'] and run['used'] < run['approval']['max_requests']
                    and run['last_time'] <= self.clock() < run['deadline'], 'EXECUTION_LIMIT_REACHED')
            if batch_id:
                batch = next(b for b in s['batches'] if b['id'] == batch_id)
                require(batch['status'] == 'prepared', 'UNCERTAIN_WRITE_MUST_NOT_RETRY')
                require(batch['sha256'] == json_sha256(batch['request']), 'REQUEST_CHANGED')
                batch['status'] = 'uncertain'
                batch['account_id'] = run['approval']['account_id']
            run['used'] += 1
            run['requests'].append(dict(sequence=run['used'], method=method, path=path,
                                        sha256=request_sha, batch_id=batch_id))

    def stop_run(self, run_id):
        with self.change('execution_stopped') as s:
            s['runs'][run_id]['stopped'] = True
