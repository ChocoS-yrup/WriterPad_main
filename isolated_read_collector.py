"""Independent seven-request observation collector, with explicit injected I/O.

No app store, session loader, live transport factory, CLI, retry or baseline apply.
Caller supplies scope, reference bytes, credentials and an async transport.
Only collect() can dispatch; inspect() reads a terminated/aborted run without resuming.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from uuid import UUID

import httpx

from sync_contract import (CANONICAL_CONTRACT_SHA256, CONTRACT_VERSION,
    SYNC_PROTOCOL_VERSION, normalize_storage_name, read_handshake_compatibility,
    require_server_compatibility)

ENDPOINT = 'https://mhpnszcorfzrvhyondxr.supabase.co'
ACCOUNT = 'e487c6ea-1c2b-4a90-821e-91e8547106de'
PROJECT = 'd8f50b5f-ae0e-42f8-9296-5d5885a5b304'
PROJECT_NAME = '일반동기화 검증 20260910'
PARENT = '95b8e4d0-1d8d-4af5-b121-0888d0157661'
ROOT_CANDIDATE = '58ed531f-56a3-4279-b0da-d5dbe5209839'
CANDIDATES = frozenset((ROOT_CANDIDATE, '949e9332-ee27-4100-b7f1-0421341d165d',
    '0eb1aece-7212-4e8d-a5a1-fa0dbeaa89db', 'e3c2f57d-e116-5c5d-8941-1bcf0dfce53c'))
ROOT_NAME = '자동수신저장 격리검증 20260913'
ENDED_RUN = '6a7a9c7d-982a-4fcd-90e6-3b4140504860'
TABLES = ('projects', 'project_sync_settings', 'documents', 'folders', 'tree_orders')
MAX_RESPONSE = 64 * 1024 * 1024


class ReadStopped(Exception):
    """Only static codes are exposed; never exception URLs, tokens or bodies."""


def need(condition, code):
    if not condition:
        raise ReadStopped(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: need(False, 'INVALID_JSON_NUMBER'))
    except (UnicodeError, ValueError, TypeError):
        raise ReadStopped('INVALID_JSON') from None


def uuid(value):
    try:
        need(isinstance(value, str) and str(UUID(value)) == value, 'INVALID_UUID')
    except (ValueError, AttributeError):
        raise ReadStopped('INVALID_UUID') from None
    return value


def positive(value):
    return type(value) is int and value > 0


def safe(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            need(not stat.S_ISLNK(info.st_mode) and
                 not (getattr(info, 'st_file_attributes', 0) & 0x400), 'LINK_REFUSED')
    need(path.resolve() == path, 'PATH_REFUSED')
    return path


def new_file(path, raw):
    safe(path)
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def reference(metadata_bytes, orders_bytes):
    meta, orders = parse(metadata_bytes), parse(orders_bytes)
    need(isinstance(meta, dict) and meta.get('format') == 'windows-isolated-target-metadata-proposal-v1'
         and (meta.get('endpoint'), meta.get('account_id'), meta.get('server_project_id')) ==
         (ENDPOINT, ACCOUNT, PROJECT) and meta.get('complete') is False, 'REFERENCE_BINDING')
    need(isinstance(orders, dict) and orders.get('format') == 'windows-retained-orders-v1'
         and orders.get('complete') is False, 'REFERENCE_FORMAT')
    need(isinstance(meta.get('nodes'), list) and isinstance(orders.get('orders'), list), 'REFERENCE_FORMAT')
    node_keys = {'id','kind','parent_id','path','revision','structure_revision','deleted','utf8_bytes','ends_lf','sha256'}
    for node in meta['nodes']:
        need(isinstance(node, dict) and set(node) == node_keys and node['kind'] in ('text','folder')
             and isinstance(node['path'], str) and positive(node['revision'])
             and type(node['deleted']) is bool, 'REFERENCE_NODE')
        if node['parent_id'] is not None:
            uuid(node['parent_id'])
        if node['kind'] == 'text':
            need(positive(node['structure_revision']) and type(node['utf8_bytes']) is int
                 and node['utf8_bytes'] >= 0 and type(node['ends_lf']) is bool
                 and isinstance(node['sha256'], str) and re.fullmatch('[0-9a-f]{64}',node['sha256']), 'REFERENCE_BODY')
        else:
            need(all(node[k] is None for k in ('structure_revision','utf8_bytes','ends_lf','sha256')), 'REFERENCE_FOLDER')
    for order in orders['orders']:
        need(isinstance(order, dict) and set(order) == {'id','parent_id','revision','children'}
             and positive(order['revision']) and isinstance(order['children'], list), 'REFERENCE_ORDER')
        if order['parent_id'] is not None:
            uuid(order['parent_id'])
        for key in order['children']:
            uuid(key)
        need(len(order['children']) == len(set(order['children'])), 'REFERENCE_ORDER')
    ids = [uuid(n['id']) for n in meta['nodes']]
    order_ids = [uuid(n['id']) for n in orders['orders']]
    need(len(ids) == len(set(ids)) and len(order_ids) == len(set(order_ids))
         and PARENT in ids and not CANDIDATES.intersection(ids + order_ids), 'REFERENCE_IDS')
    return meta, orders, dict(metadata=digest(metadata_bytes), orders=digest(orders_bytes))


def requests():
    return [('GET', '/auth/v1/user', None, None),
            ('POST', '/rest/v1/rpc/get_sync_handshake', None,
             dict(p_project_id=PROJECT, p_contract_sha256=CANONICAL_CONTRACT_SHA256))] + [
        ('GET', '/rest/v1/' + table,
         {'project_id': 'eq.' + PROJECT, 'select': '*', 'limit': '10000'}, None) for table in TABLES]


class Journal:
    def __init__(self, directory):
        self.directory = directory
        self.path = directory / 'journal.jsonl'
        self.sequence, self.previous = 0, ''

    def append(self, event, data):
        safe(self.directory)
        row = dict(sequence=self.sequence + 1, previous=self.previous, event=event, data=data)
        row['sha256'] = digest(encoded(row))
        safe(self.path)
        with self.path.open('ab') as handle:
            handle.write(encoded(row))
            handle.flush()
            os.fsync(handle.fileno())
        self.sequence, self.previous = row['sequence'], row['sha256']


def inspect(directory):
    """Read-only, with hash verification. A partial attempt is never refunded/replayed."""
    directory = safe(directory)
    raw = safe(directory / 'journal.jsonl').read_bytes()
    events, previous = [], ''
    need(raw.endswith(b'\n'), 'PARTIAL_JOURNAL_PRESERVED')
    for index, line in enumerate(raw.splitlines(), 1):
        row = parse(line)
        sha = row.pop('sha256')
        need(row['sequence'] == index and row['previous'] == previous
             and sha == digest(encoded(row)), 'JOURNAL_CHANGED')
        previous = sha
        if row['event'] == 'response':
            name = row['data']['file']
            need(re.fullmatch(r'Q[1-7]\.body', name) is not None, 'RESPONSE_PATH_CHANGED')
            need(digest(safe(directory / name).read_bytes()) == row['data']['sha256'], 'RESPONSE_CHANGED')
        events.append(row)
    need(events and events[0]['event'] == 'opened', 'JOURNAL_CHANGED')
    scope_raw = safe(directory / 'scope.json').read_bytes()
    need(digest(scope_raw) == events[0]['data']['scope_sha256']
         and parse(scope_raw)['run_id'] == directory.name, 'SCOPE_FILE_CHANGED')
    attempts = [e['data'] for e in events if e['event'] == 'attempt']
    need([r['request'] for r in attempts] == list(range(1, len(attempts) + 1))
         and len(attempts) <= 7, 'ATTEMPT_SEQUENCE_CHANGED')
    terminal = events[-1]['event'] if events else 'incomplete'
    if terminal in ('observed','stopped'):
        need(digest(safe(directory / 'observation.json').read_bytes()) == events[-1]['data']['observation_sha256'],
             'OBSERVATION_CHANGED')
    return dict(format='windows-read-inspection-v1', execution_allowed=False, resumable=False,
                http_used=len(attempts), auth_user_used=int(bool(attempts)),
                terminal=terminal, complete=False, events=events)


def check_count(header, rows):
    need(isinstance(rows, list) and len(rows) <= 10000, 'INVALID_TABLE')
    need(isinstance(header, str), 'INCOMPLETE_TABLE_COUNT')
    if not rows:
        need(header in ('*/0', '0-0/0'), 'INCOMPLETE_TABLE_COUNT')
        return
    match = re.fullmatch(r'(\d+)-(\d+)/(\d+)', header)
    need(match is not None and tuple(map(int, match.groups())) == (0, len(rows)-1, len(rows)),
         'INCOMPLETE_TABLE_COUNT')


def body_meta(row):
    content = row.get('content')
    need(isinstance(content, str) and '\r' not in content and '\x00' not in content, 'INVALID_BODY')
    raw = content.encode('utf-8')
    need(len(raw) <= 10_485_760, 'BODY_TOO_LARGE')
    result = dict(utf8_bytes=len(raw), ends_lf=raw.endswith(b'\n'), sha256=digest(raw))
    if 'content_sha256' in row:
        need(row['content_sha256'] == result['sha256'], 'BODY_HASH_CHANGED')
    if 'content_byte_count' in row:
        need(type(row['content_byte_count']) is int and row['content_byte_count'] == len(raw), 'BODY_SIZE_CHANGED')
    return result


class Observation:
    def __init__(self, meta, orders):
        self.before = {n['id']: n for n in meta['nodes']}
        self.before_orders = {n['id']: n for n in orders['orders']}
        self.nodes, self.orders, self.special_ids, self.differences = {}, {}, [], []

    def different(self, kind, ident, fields):
        self.differences.append(dict(kind=kind, id=ident, fields=fields))
        raise ReadStopped('REFERENCE_DIFFERENCE')

    def table(self, table, rows):
        need(all(isinstance(r, dict) and r.get('project_id') == PROJECT for r in rows), 'PROJECT_CHANGED')
        if table == 'projects':
            need(len(rows) == 1 and rows[0].get('owner_id') == ACCOUNT
                 and rows[0].get('name') == PROJECT_NAME and rows[0].get('deleted_at') is None, 'OWNER_CHANGED')
        elif table == 'project_sync_settings':
            need(len(rows) == 1 and rows[0].get('project_sync_mode') == 'ID_BASED'
                 and type(rows[0].get('migration_epoch')) is int and rows[0]['migration_epoch'] == 1, 'MODE_CHANGED')
        elif table in ('documents', 'folders'):
            text = table == 'documents'
            seen = set()
            for row in rows:
                ident = uuid(row.get('document_id' if text else 'folder_id'))
                need(ident not in CANDIDATES, 'CANDIDATE_COLLISION')
                need(ident not in seen and ident not in self.nodes and ident not in self.special_ids, 'DUPLICATE_NODE')
                seen.add(ident)
                if text and str(row.get('relative_path', '')).startswith('__antigravity__/'):
                    self.special_ids.append(ident)
                    continue  # Keep exact raw response; not an editable manuscript.
                parent = row.get('parent_folder_id')
                if parent is not None:
                    uuid(parent)
                need(positive(row.get('revision')) and type(row.get('is_deleted')) is bool, 'INVALID_NODE')
                name = row.get('name')
                need(isinstance(name, str), 'INVALID_NAME')
                normalize_storage_name(name)
                if parent == PARENT and normalize_storage_name(name).utf8_hex == normalize_storage_name(ROOT_NAME).utf8_hex:
                    need(row['is_deleted'], 'CANDIDATE_NAME_COLLISION')
                node = dict(id=ident, kind='text' if text else 'folder', parent_id=parent,
                    name=name, revision=row['revision'], structure_revision=None,
                    deleted=row['is_deleted'], utf8_bytes=None, ends_lf=None, sha256=None)
                if text:
                    need(positive(row.get('structure_revision')), 'INVALID_STRUCTURE_REVISION')
                    node.update(structure_revision=row['structure_revision'], **body_meta(row))
                    node['observed_relative_path'] = row.get('relative_path')
                self.nodes[ident] = node
                old = self.before.get(ident)
                if old is None:
                    self.different('unmatched_observed_node', ident, ['id'])
                fields = [key for key in ('kind','parent_id','revision','structure_revision','deleted','utf8_bytes','ends_lf','sha256') if node[key] != old[key]]
                if name != old['path'].rsplit('/', 1)[-1]:
                    fields.append('name')
                if fields:
                    self.different('retained_node', ident, fields)
            expected = {key for key, old in self.before.items() if (old['kind'] == 'text') == text}
            missing = expected - set(self.nodes)
            if missing:
                self.different('missing_retained_node', sorted(missing)[0], ['id'])
            if not text:
                self.graph()
        else:
            parents = set()
            for row in rows:
                ident = uuid(row.get('tree_order_id'))
                parent = row.get('parent_folder_id')
                need(ident not in CANDIDATES and parent != ROOT_CANDIDATE, 'CANDIDATE_COLLISION')
                need(ident not in self.orders and ident not in self.nodes and ident not in self.special_ids
                     and parent not in parents, 'DUPLICATE_ORDER')
                need(parent is None or parent in self.nodes and self.nodes[parent]['kind'] == 'folder', 'ORDER_PARENT_MISSING')
                parents.add(parent)
                children = row.get('children')
                need(positive(row.get('revision')) and isinstance(children, list), 'INVALID_ORDER')
                for child in children:
                    uuid(child)
                expected = {k for k,n in self.nodes.items() if n['parent_id'] == parent and not n['deleted']}
                need(len(children) == len(set(children)) and set(children) == expected, 'INCOMPLETE_ORDER')
                order = dict(id=ident, parent_id=parent, revision=row['revision'], children=children)
                self.orders[ident] = order
                if order != self.before_orders.get(ident):
                    self.different('retained_or_unmatched_order', ident, ['revision_or_membership'])
            need(parents >= {n['parent_id'] for n in self.nodes.values() if not n['deleted']}, 'ORDER_MISSING')
            if set(self.before_orders) != set(self.orders):
                self.different('missing_retained_order', sorted(set(self.before_orders)-set(self.orders))[0], ['id'])

    def graph(self):
        names = set()
        for key, node in self.nodes.items():
            chain, parts, at = set(), [], key
            while at is not None:
                need(at not in chain and at in self.nodes, 'PARENT_CYCLE_OR_MISSING')
                chain.add(at)
                row = self.nodes[at]
                need(at == key or row['kind'] == 'folder', 'PARENT_NOT_FOLDER')
                need(node['deleted'] or not row['deleted'], 'PARENT_DELETED')
                parts.append(row['name'])
                at = row['parent_id']
            node['path'] = '/'.join(reversed(parts))
            if node['path'] != self.before[key]['path']:
                self.different('retained_node', key, ['path'])
            if node['kind'] == 'text' and node['observed_relative_path'] != node['path']:
                self.different('observed_relative_path', key, ['relative_path'])
            if not node['deleted']:
                normalized = (node['parent_id'], normalize_storage_name(node['name']).utf8_hex)
                need(normalized not in names, 'NAME_COLLISION')
                names.add(normalized)


async def collect(results_root, scope, metadata_bytes, orders_bytes, *, transport,
                  access_token, api_key, clock=time.time):
    """One invocation, no resume. All live I/O must be explicitly supplied by a later caller."""
    need(isinstance(transport, (httpx.AsyncBaseTransport, httpx.MockTransport)), 'EXPLICIT_ASYNC_TRANSPORT_REQUIRED')
    need(isinstance(access_token, str) and bool(access_token) and isinstance(api_key, str) and bool(api_key), 'EXPLICIT_CREDENTIALS_REQUIRED')
    scope = deepcopy(scope)
    need(isinstance(scope, dict) and set(scope) == {'format','run_id','endpoint','account_id','project_id',
        'max_requests','max_seconds','expires_at','reference_sha256'}, 'INVALID_READ_SCOPE')
    need(scope['format'] == 'windows-isolated-read-scope-v1'
         and (scope['endpoint'], scope['account_id'], scope['project_id']) == (ENDPOINT, ACCOUNT, PROJECT), 'SCOPE_BINDING')
    ident = uuid(scope['run_id'])
    need(ident != ENDED_RUN, 'ENDED_RUN_REFUSED')
    need(type(scope['max_requests']) is int and scope['max_requests'] == 7, 'REQUEST_LIMIT_CHANGED')
    for field in ('max_seconds', 'expires_at'):
        need(type(scope[field]) in (int, float) and math.isfinite(scope[field]), 'INVALID_TIME')
    need(0 < scope['max_seconds'] <= 180, 'INVALID_TIME')
    meta, orders, hashes = reference(metadata_bytes, orders_bytes)
    need(scope['reference_sha256'] == hashes, 'REFERENCE_HASH_CHANGED')
    started = clock()
    need(math.isfinite(started) and scope['expires_at'] > started, 'EXPIRED_SCOPE')
    loop = asyncio.get_running_loop()
    deadline_wall = min(scope['expires_at'], started + scope['max_seconds'])
    deadline_mono = loop.time() + deadline_wall - started
    last_clock = started
    directory = safe(Path(results_root) / ident)
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(exist_ok=False)  # Refuse even an empty previous/partial run.
    new_file(directory / 'scope.json', encoded(scope))
    journal = Journal(directory)
    journal.append('opened', dict(scope_sha256=digest(encoded(scope)), started=started, deadline=deadline_wall))
    observation = Observation(meta, orders)
    used, terminal, reason = 0, 'stopped', None

    def remaining():
        nonlocal last_clock
        now = clock()
        need(math.isfinite(now) and now >= last_clock, 'CLOCK_ROLLBACK')
        last_clock = now
        left = min(deadline_wall-now, deadline_mono-loop.time())
        need(left > 0, 'DEADLINE_REACHED')
        return left

    try:
        async with asyncio.timeout(remaining()):
            async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                    timeout=15, headers={'Authorization':'Bearer '+access_token, 'apikey':api_key,
                                         'Accept-Encoding':'identity'}) as client:
                for index, (method, path, params, payload) in enumerate(requests(), 1):
                    left = remaining()
                    need(index == used + 1 and index <= 7, 'REQUEST_LIMIT_REACHED')
                    # Bearer-only observation: never propagate a response cookie
                    # into the next request. This is only this client's memory jar.
                    client.cookies.clear()
                    request = client.build_request(method, ENDPOINT+path, params=params,
                        content=encoded(payload) if payload else None,
                        headers={'Content-Type':'application/json'} if payload else
                                ({'Prefer':'count=exact'} if index >= 3 else {}),
                        timeout=min(15, left))
                    journal.append('attempt', dict(request=index, method=method, path=path,
                        params=params, body_sha256=digest(request.content), at=clock()))
                    used = index  # Durable before any send, including a failure.
                    remaining()
                    response = await client.send(request, stream=True)
                    try:
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            remaining()
                            need(len(raw)+len(chunk) <= MAX_RESPONSE, 'RESPONSE_TOO_LARGE')
                            raw.extend(chunk)
                        raw = bytes(raw)
                        remaining()
                        need(access_token.encode() not in raw and api_key.encode() not in raw, 'CREDENTIAL_IN_RESPONSE')
                        name = f'Q{index}.body'
                        new_file(directory / name, raw)
                        content_range = response.headers.get('content-range')
                        range_valid = isinstance(content_range, str) and len(content_range) <= 64 and re.fullmatch(r'(\d+-\d+|\*)/(\d+|\*)', content_range)
                        journal.append('response', dict(request=index, file=name, sha256=digest(raw),
                            bytes=len(raw), status=response.status_code, at=clock(),
                            content_range=content_range if range_valid else None,
                            content_range_state='value' if range_valid else ('missing' if content_range is None else 'invalid')))
                        need(response.status_code in (200,206) and (index >= 3 or response.status_code == 200), 'HTTP_STATUS_REFUSED')
                        value = parse(raw)
                        if index == 1:
                            need(isinstance(value, dict) and value.get('id') == ACCOUNT, 'ACCOUNT_CHANGED')
                        elif index == 2:
                            if isinstance(value, list):
                                need(len(value) == 1, 'HANDSHAKE_SHAPE')
                                value = value[0]
                            need(isinstance(value, dict) and value.get('project_id') == PROJECT, 'HANDSHAKE_PROJECT_CHANGED')
                            compatible = read_handshake_compatibility(value)
                            require_server_compatibility(**compatible)
                            need(compatible['project_sync_mode'] == 'ID_BASED' and compatible['migration_epoch'] == 1, 'HANDSHAKE_MODE_CHANGED')
                        else:
                            check_count(response.headers.get('content-range'), value)
                            observation.table(TABLES[index-3], value)
                        journal.append('validated', dict(request=index, count=len(value) if index >= 3 else None, at=clock()))
                    finally:
                        await response.aclose()
                remaining()
        remaining()
        terminal = 'observed'
    except ReadStopped as error:
        reason = str(error)
    except (TimeoutError, httpx.TimeoutException):
        reason = 'DEADLINE_OR_REQUEST_TIMEOUT'
    except asyncio.CancelledError:
        reason = 'CANCELLED'
        raise
    except Exception:
        reason = 'VALIDATION_OR_IO_FAILED'
    except BaseException:
        reason = 'INTERRUPTED'
        raise
    finally:
        report = dict(format='windows-read-observation-v1', status=terminal, stop_reason=reason,
            execution_allowed=False, complete=False, baseline_ready=False, atomic_snapshot=False,
            visibility='specified account and project only; no global absence proof',
            endpoint=ENDPOINT, account_id=ACCOUNT, server_project_id=PROJECT,
            contract_version=CONTRACT_VERSION, protocol_version=SYNC_PROTOCOL_VERSION,
            reference_sha256=hashes, http_used=used, auth_user_used=int(used > 0),
            token_refreshes=0, document_structure_writes=0, automatic_cycles=0,
            started_at=started, deadline=deadline_wall,
            candidate_check='no collision in observed scope' if terminal == 'observed' else 'not_completed',
            differences=observation.differences, special_metadata_ids=observation.special_ids,
            nodes=list(observation.nodes.values()), orders=list(observation.orders.values()))
        new_file(directory / 'observation.json', encoded(report))
        journal.append(terminal, dict(reason=reason, http_used=used, at=clock(),
                                      observation_sha256=digest(encoded(report))))
    return report
