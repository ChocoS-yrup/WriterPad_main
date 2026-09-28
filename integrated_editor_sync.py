"""Counted, serial Staging transport; both body and structure receipt recovery.

No client, credential read, timer, or network request is created on import.
The caller supplies a reviewed execution scope and a current authority check.
"""
from copy import deepcopy
import math
import threading

import httpx

from body_validation_transport import strict_json
from integrated_editor_plan import BUILD_ID, PROJECT_ID, PROJECT_NAME, STAGING_URL, PROTECTED_DOCUMENT_ID
from integrated_editor_store import EditorError, require, body, validate_nodes
from normal_editor_network import BATCH_COLUMNS, RESULT_COLUMNS
from sync_contract import (CANONICAL_CONTRACT_SHA256, canonical_json, json_sha256, require_uuid,
    read_handshake_compatibility, require_server_compatibility,
    validate_document_commit_response, validate_atomic_structure_response)

TABLES = ('projects', 'project_sync_settings', 'documents', 'folders', 'tree_orders')


def validate_approval(value, device):
    require(isinstance(value, dict) and set(value) - {'automatic_policy'} == {
        'run_id', 'project_id', 'account_id', 'device_id', 'build_id', 'url',
        'writable_ids', 'order_ids', 'max_requests', 'max_seconds', 'expires_at', 'automatic'},
        'INVALID_EXECUTION_SCOPE')
    require_uuid(value['run_id'], 'run_id')
    require_uuid(value['account_id'], 'account_id')
    require((value['project_id'], value['device_id'], value['build_id'], value['url']) ==
            (PROJECT_ID, device, BUILD_ID, STAGING_URL), 'EXECUTION_BINDING_CHANGED')
    for field in ('max_requests', 'max_seconds'):
        require(type(value[field]) is int and value[field] > 0, 'INVALID_EXECUTION_LIMIT')
    require(type(value['expires_at']) in (int, float) and math.isfinite(value['expires_at'])
            and type(value['automatic']) is bool, 'INVALID_EXECUTION_LIMIT')
    for field in ('writable_ids', 'order_ids'):
        require(isinstance(value[field], list) and len(value[field]) == len(set(value[field])), 'INVALID_SCOPE_IDS')
        for key in value[field]:
            require_uuid(key, field)
    require(PROTECTED_DOCUMENT_ID not in value['writable_ids'], 'COMPLETED_DOCUMENT_PROTECTED')
    if 'automatic_policy' in value:
        policy = value['automatic_policy']
        require(isinstance(policy, dict) and set(policy) == {'version', 'max_empty_cycles'}
                and type(policy['version']) is int and policy['version'] == 1
                and type(policy['max_empty_cycles']) is int and policy['max_empty_cycles'] >= 0,
                'INVALID_AUTOMATIC_POLICY')


def validate_response(request, response):
    validator = (validate_document_commit_response if request['kind'] == 'document_commit_request'
                 else validate_atomic_structure_response)
    validator(request, response)
    if response['applied']:
        for intent, result in zip(request['ordered_intents'], response['results']):
            require(type(result['result_revision']) is int and
                    result['result_revision'] == intent['base_revision'] + 1, 'RECEIPT_REVISION_CHANGED')
    return response


def decode_snapshot(rows, account, *, transitional=False):
    require(set(rows) == set(TABLES), 'INCOMPLETE_SNAPSHOT')
    require(all(isinstance(rows[t], list) and all(r.get('project_id') == PROJECT_ID for r in rows[t])
                for t in TABLES), 'REMOTE_PROJECT_CHANGED')
    require(len(rows['projects']) == 1 and rows['projects'][0].get('owner_id') == account
            and rows['projects'][0].get('name') == PROJECT_NAME
            and rows['projects'][0].get('deleted_at') is None, 'REMOTE_OWNER_CHANGED')
    require(len(rows['project_sync_settings']) == 1
            and rows['project_sync_settings'][0].get('project_sync_mode') == 'ID_BASED'
            and type(rows['project_sync_settings'][0].get('migration_epoch')) is int
            and rows['project_sync_settings'][0]['migration_epoch'] == 1, 'REMOTE_MODE_CHANGED')
    nodes = []
    for table, kind, key in (('documents', 'document', 'document_id'), ('folders', 'folder', 'folder_id')):
        for r in rows[table]:
            # Legacy view metadata is not a manuscript and cannot become an editable node.
            if kind == 'document' and str(r.get('relative_path', '')).startswith('__antigravity__/'):
                continue
            n = dict(id=r[key], kind=kind, parent=r['parent_folder_id'], name=r['name'],
                     deleted=r['is_deleted'], revision=r['revision'], srev=r.get('structure_revision', 1))
            if kind == 'document':
                n['content'] = body(r['content'])
            nodes.append(n)
    by_id = {n['id']: n for n in nodes}
    require(len(nodes) == len(by_id), 'DUPLICATE_REMOTE_NODE')
    validate_nodes(by_id)
    orders, parents = [], set()
    for r in rows['tree_orders']:
        ident, parent, children = r['tree_order_id'], r['parent_folder_id'], r['children']
        require_uuid(ident, 'tree_order_id')
        require(parent not in parents and (parent is None or parent in by_id
                and by_id[parent]['kind'] == 'folder'), 'INVALID_REMOTE_ORDER_PARENT')
        parents.add(parent)
        require(type(r['revision']) is int and r['revision'] >= 1
                and isinstance(children, list) and len(children) == len(set(children)), 'INVALID_REMOTE_ORDER')
        expected = {k for k, n in by_id.items() if n['parent'] == parent and not n['deleted']}
        require(set(children) <= set(by_id), 'REMOTE_ORDER_REFERENCE_MISSING')
        require(transitional or set(children) == expected, 'INCOMPLETE_REMOTE_ORDER')
        orders.append(dict(id=ident, parent=parent, children=children, revision=r['revision']))
    require(len({o['id'] for o in orders}) == len(orders), 'DUPLICATE_REMOTE_ORDER')
    require(transitional or {n['parent'] for n in nodes if not n['deleted']} <= parents, 'REMOTE_ORDER_MISSING')
    return nodes, orders


class IntegratedNetwork:
    def __init__(self, store, approval, *, access_token, api_key, check_current, transport=None):
        validate_approval(approval, store.device)
        require(access_token and api_key and callable(check_current), 'AUTHORITY_REQUIRED')
        self.store, self.approval = store, deepcopy(approval)
        self.check_current = check_current
        self.cancelled = threading.Event()
        self.owner_thread = None
        self.verified = False
        self.run_id = approval['run_id']
        store.open_run(approval)
        # No retries, SDK refresh, redirects, proxies or unrelated background clients.
        self.http = httpx.Client(transport=transport or httpx.HTTPTransport(retries=0, trust_env=False),
            headers={'Authorization': 'Bearer ' + access_token, 'apikey': api_key},
            follow_redirects=False, trust_env=False, timeout=15)

    def authority(self):
        require(not self.cancelled.is_set() and self.check_current() is True, 'AUTHORITY_ENDED')
        self.store.check_run(self.run_id)

    def request(self, method, path, *, params=None, payload=None, batch_id=None):
        require(self.owner_thread == threading.get_ident(), 'SERIAL_EXECUTION_REQUIRED')
        self.authority()
        require(path in {'/auth/v1/user', '/rest/v1/rpc/get_sync_handshake',
                        '/rest/v1/rpc/document_commit', '/rest/v1/rpc/atomic_structure_commit',
                        '/rest/v1/sync_batches', '/rest/v1/sync_batch_results'} |
                {'/rest/v1/' + t for t in TABLES}, 'ENDPOINT_REFUSED')
        if path == '/auth/v1/user':
            require(method == 'GET' and not params and payload is None and batch_id is None, 'AUTH_REQUEST_CHANGED')
        elif path == '/rest/v1/rpc/get_sync_handshake':
            require(method == 'POST' and not params and batch_id is None and payload == {
                'p_project_id': PROJECT_ID, 'p_contract_sha256': CANONICAL_CONTRACT_SHA256}, 'HANDSHAKE_REQUEST_CHANGED')
        elif '/rpc/' in path:
            require(self.verified and method == 'POST' and not params and batch_id is not None, 'WRITE_NOT_VERIFIED')
            batch = next(b for b in self.store.state()['batches'] if b['id'] == batch_id)
            self.validate_request(batch)
            rpc = 'document_commit' if batch['request']['kind'] == 'document_commit_request' else 'atomic_structure_commit'
            require(path == '/rest/v1/rpc/' + rpc and payload == {'p_request': batch['request']}, 'WRITE_REQUEST_CHANGED')
        else:
            require(self.verified and method == 'GET' and payload is None and batch_id is None, 'READ_NOT_VERIFIED')
            table = path.rsplit('/', 1)[-1]
            if table in TABLES:
                require(params == {'select': '*', 'limit': '10000', 'project_id': 'eq.' + PROJECT_ID}, 'READ_SCOPE_CHANGED')
            else:
                ident = (params or {}).get('batch_id', '').removeprefix('eq.')
                stored = next((b for b in self.store.state()['batches'] if b['id'] == ident), None)
                require(stored and stored['status'] == 'uncertain' and stored['account_id'] == self.approval['account_id'],
                        'RECEIPT_SCOPE_CHANGED')
                expected = {'select': BATCH_COLUMNS if table == 'sync_batches' else RESULT_COLUMNS,
                            'batch_id': 'eq.' + ident, 'limit': '2'}
                if table == 'sync_batches':
                    expected['project_id'] = 'eq.' + PROJECT_ID
                require(params == expected, 'RECEIPT_SCOPE_CHANGED')
        kwargs = dict(params=params, headers={'Prefer': 'count=exact'} if method == 'GET' and
                      path != '/auth/v1/user' else {})
        if payload is not None:
            kwargs.update(content=canonical_json(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
        req = self.http.build_request(method, STAGING_URL + path, **kwargs)
        # Durable reservation occurs before any transport call. Failure is conservatively charged.
        self.store.reserve_request(self.run_id, method, path,
            json_sha256(dict(method=method, url=str(req.url), body=req.content.decode('utf-8'))), batch_id=batch_id)
        response = self.http.send(req)
        # Acceptance checks identity/cancellation, not the exhausted request budget: the last
        # permitted reply must still be recorded and applied without another network request.
        require(not self.cancelled.is_set() and self.check_current() is True, 'AUTHORITY_ENDED')
        response.raise_for_status()
        require(len(response.content) <= 64 * 1024 * 1024, 'RESPONSE_TOO_LARGE')
        return strict_json(response.content), response.headers

    def authenticate(self):
        data, _ = self.request('GET', '/auth/v1/user')
        require(isinstance(data, dict) and data.get('id') == self.approval['account_id'], 'ACCOUNT_CHANGED')
        data, _ = self.request('POST', '/rest/v1/rpc/get_sync_handshake', payload={
            'p_project_id': PROJECT_ID, 'p_contract_sha256': CANONICAL_CONTRACT_SHA256})
        if isinstance(data, list):
            require(len(data) == 1, 'INVALID_HANDSHAKE')
            data = data[0]
        require(isinstance(data, dict) and data.get('project_id') == PROJECT_ID, 'HANDSHAKE_PROJECT_CHANGED')
        compatible = read_handshake_compatibility(data)
        require_server_compatibility(**compatible)
        require(compatible['project_sync_mode'] == 'ID_BASED' and compatible['migration_epoch'] == 1,
                'HANDSHAKE_MODE_CHANGED')
        self.verified = True

    def table(self, table, *, columns='*', batch_id=None):
        params = {'select': columns, 'limit': '2' if batch_id else '10000'}
        if table != 'sync_batch_results':
            params['project_id'] = 'eq.' + PROJECT_ID
        if batch_id:
            params['batch_id'] = 'eq.' + batch_id
        rows, headers = self.request('GET', '/rest/v1/' + table, params=params)
        count = headers.get('content-range', '').rsplit('/', 1)[-1]
        require(isinstance(rows, list) and count.isdecimal() and int(count) == len(rows), 'INCOMPLETE_TABLE_COUNT')
        require(not batch_id or len(rows) == 1, 'RECEIPT_NOT_FOUND_OR_AMBIGUOUS')
        return rows

    def snapshot(self):
        rows = {table: self.table(table) for table in TABLES}
        pending = any(j['status'] != 'completed' for j in self.store.state()['jobs'])
        return decode_snapshot(rows, self.approval['account_id'], transitional=pending)

    def validate_request(self, batch):
        req = batch['request']
        require(json_sha256(req) == batch['sha256'], 'REQUEST_CHANGED')
        require((req['project_id'], req['project_sync_mode'], req['migration_epoch'],
                 req['batch']['writer_device_id'], req['batch']['client_build_id']) ==
                (PROJECT_ID, 'ID_BASED', 1, self.store.device, BUILD_ID), 'REQUEST_BINDING_CHANGED')
        s = self.store.state()
        for intent in req['ordered_intents']:
            key = intent.get('document_id', intent.get('entity_id'))
            field = 'order_ids' if intent['entity_kind'] == 'tree_order' else 'writable_ids'
            require(key in self.approval[field] and key != PROTECTED_DOCUMENT_ID, 'WRITE_OUTSIDE_SCOPE')
            if field == 'writable_ids':
                require(key in s['nodes'] and not s['nodes'][key]['protected'], 'PROTECTED_NODE')
            else:
                # The completed document's parent ordering is part of the preserved baseline.
                require(PROTECTED_DOCUMENT_ID not in s['orders'][key]['local']['children'],
                        'COMPLETED_DOCUMENT_ORDER_PROTECTED')

    def receipt(self, batch):
        req = batch['request']
        require(batch['account_id'] == self.approval['account_id'], 'RECEIPT_ACCOUNT_CHANGED')
        row = self.table('sync_batches', columns=BATCH_COLUMNS, batch_id=batch['id'])[0]
        expected = dict(req['batch'], project_id=PROJECT_ID, writer_user_id=batch['account_id'],
            project_sync_mode='ID_BASED', migration_epoch=1, request_sha256=json_sha256(req))
        received_caps = row.get('client_capabilities') if isinstance(row, dict) else None
        requested_caps = expected['client_capabilities']
        for caps in (received_caps, requested_caps):
            require(isinstance(caps, list) and all(isinstance(c, str) and c for c in caps)
                    and len(caps) == len(set(caps)), 'RECEIPT_REQUEST_CHANGED')
        require(set(received_caps) == set(requested_caps), 'RECEIPT_REQUEST_CHANGED')
        # SQL stores capabilities sorted. Normalize only this comparison copy;
        # the immutable wire request and its original hashes must not change.
        comparable = dict(row, client_capabilities=requested_caps)
        require(json_sha256(comparable) == json_sha256(expected), 'RECEIPT_REQUEST_CHANGED')
        result = self.table('sync_batch_results', columns=RESULT_COLUMNS, batch_id=batch['id'])[0]
        require(set(result) == set(RESULT_COLUMNS.split(',')) and result['batch_id'] == batch['id']
                and result['applied'] is True and result['response_sha256'] == json_sha256(result['response']),
                'RECEIPT_RESPONSE_CHANGED')
        return validate_response(req, result['response'])

    def cycle(self, *, automatic=False, receive_only=False):
        """One bounded cycle. Timers schedule cycles, never recurse or retry writes."""
        require(not automatic or self.approval['automatic'], 'AUTOMATIC_NOT_APPROVED')
        if not self.store.dispatch_lock.acquire(blocking=False):
            return 'busy'
        try:
            self.owner_thread = threading.get_ident()
            self.verified = False
            self.authority()
            if automatic:
                reason = self.store.automatic_stop_reason(self.run_id)
                require(reason is None, reason)
            self.store.materialize()
            require(receive_only or not self.store.state()['conflicts'], 'CONFLICT_REVIEW_REQUIRED')
            batch = None if receive_only else self.store.prepare_next()
            if batch:
                self.validate_request(batch)
            if automatic and batch is None:
                # Empty means no outgoing/recovery batch at dispatch, even if
                # the snapshot later contains changes. Reserve before Auth;
                # failed or interrupted attempts are never refunded.
                self.store.reserve_empty_cycle(self.run_id)
            self.authenticate()
            if batch and batch['status'] == 'uncertain':
                # Both commit kinds recover the same immutable batch. Never write-RPC replay.
                response = batch['response'] if batch['response'] is not None else self.receipt(batch)
                validate_response(batch['request'], response)
                self.store.record_response(batch['id'], response)
                self.store.complete(batch['id'], response)
                return 'recovered' if response['applied'] else 'blocked'
            nodes, orders = self.snapshot()
            if not self.store.accept_snapshot(nodes, orders, self.approval['writable_ids']):
                return 'conflict'
            if batch is None:
                return 'received'
            self.validate_request(batch)
            rpc = 'document_commit' if batch['request']['kind'] == 'document_commit_request' else 'atomic_structure_commit'
            response, _ = self.request('POST', '/rest/v1/rpc/' + rpc,
                                        payload={'p_request': batch['request']}, batch_id=batch['id'])
            validate_response(batch['request'], response)
            self.store.record_response(batch['id'], response)
            self.store.complete(batch['id'], response)
            return 'sent' if response['applied'] else 'blocked'
        finally:
            self.owner_thread = None
            self.verified = False
            self.store.dispatch_lock.release()

    def close(self):
        self.cancelled.set()
        self.http.close()


class AutoSync:
    """Platform-neutral scheduler state; reconnect and foreground resume use tick."""
    def __init__(self, network, *, clock, interval=10):
        self.network, self.clock, self.interval = network, clock, interval
        self.enabled, self.online, self.foreground = False, True, True
        self.next_at, self.failures, self.last_error = 0, 0, None

    def tick(self):
        if not (self.enabled and self.online and self.foreground) or self.clock() < self.next_at:
            return 'idle'
        try:
            result = self.network.cycle(automatic=True)
            self.failures, self.last_error = 0, None
            if result in ('blocked', 'conflict'):
                self.enabled = False
            self.next_at = self.clock() + self.interval
            return result
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            self.failures += 1
            self.last_error = type(error).__name__
            self.next_at = self.clock() + min(60, self.interval * 2 ** min(self.failures, 4))
            # A write that failed on the wire is already uncertain; the next tick only queries receipts.
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code < 500:
                self.enabled = False
            return 'retry_wait'
        except EditorError as error:
            self.last_error = str(error)
            if str(error) in ('INCOMPLETE_REMOTE_ORDER', 'REMOTE_ORDER_MISSING',
                              'REMOTE_ORDER_REFERENCE_MISSING', 'PARENT_NOT_FOUND', 'PARENT_DELETED'):
                # Separate table reads may observe the peer between ordered
                # commits. Keep local state and retry a full snapshot within
                # the same persistent execution budget; never partially apply.
                self.next_at = self.clock() + self.interval
                return 'retry_wait'
            self.enabled = False
            raise
        except Exception as error:
            self.last_error = str(error)
            self.enabled = False
            raise
        finally:
            reason = self.network.store.automatic_stop_reason(self.network.run_id)
            if reason:
                self.enabled = False
                self.last_error = reason
