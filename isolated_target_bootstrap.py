"""Explicit-I/O isolated target creation and receive-baseline preparation.

No CLI, live transport factory, credential loader, app store, or automatic retry.
Future live callers must provide their own approved identity/lease/pin checks.
"""
import asyncio
from copy import deepcopy
import time
from uuid import uuid4

import httpx
import isolated_read_collector as c
import isolated_read_launcher as l
from sync_contract import (build_atomic_structure_request, build_document_commit_request,
    validate_atomic_structure_response, validate_document_commit_response,
    read_handshake_compatibility, require_server_compatibility, json_sha256)

BODY_ID = '949e9332-ee27-4100-b7f1-0421341d165d'
EMPTY_ID = '0eb1aece-7212-4e8d-a5a1-fa0dbeaa89db'
ORDER_ID = 'e3c2f57d-e116-5c5d-8941-1bcf0dfce53c'
PARENT_ORDER = '9ca39b36-aad6-5347-97b9-ff00c9234aea'
BUILD = 'windows-isolated-target-bootstrap-v1'
ENDED = l.OLD_EXAMPLES | {'64896904-d883-4549-8d9e-3c53cadb6cb8',
    'a2e05f32-2387-430d-a46b-0c0fc5aec0b0', '638699a2-7d1f-475d-a55e-a8999a6cbee3',
    '5534ddff-dd36-4857-bdd8-12ce1c353e44'}


def prepare_plan(metadata_bytes, orders_bytes, initial_body_bytes, device_id):
    """Pure nomination: no live revisions, ID issuance, server or app application."""
    meta, orders, refs = c.reference(metadata_bytes, orders_bytes)
    c.uuid(device_id)
    c.need(isinstance(initial_body_bytes, bytes), 'BODY_BYTES_REQUIRED')
    try:
        text = initial_body_bytes.decode('utf-8')
    except UnicodeError:
        raise c.ReadStopped('INVALID_BODY') from None
    boundary = c.body_meta({'content': text})
    parent = next(n for n in meta['nodes'] if n['id'] == c.PARENT)
    c.need(parent['kind'] == 'folder' and not parent['deleted'], 'PARENT_UNAVAILABLE')
    order = next((o for o in orders['orders'] if o['id'] == PARENT_ORDER), None)
    c.need(order is not None and order['parent_id'] == c.PARENT, 'PARENT_ORDER_UNAVAILABLE')
    return dict(format=BUILD, endpoint=c.ENDPOINT, account_id=c.ACCOUNT,
        project_id=c.PROJECT, writer_device_id=device_id,
        reference_sha256=refs, root_id=c.ROOT_CANDIDATE, parent_id=c.PARENT,
        root_path=parent['path'] + '/' + c.ROOT_NAME, parent_order_id=PARENT_ORDER,
        root_order_id=ORDER_ID, body_id=BODY_ID, empty_id=EMPTY_ID,
        body_name='저장경계.txt', empty_name='빈문서.txt', initial_body=boundary,
        initial_revisions=None, execution_allowed=False, baseline_applied=False)


def verify_before(rows, meta, orders):
    observation = c.Observation(meta, orders)
    for table in c.TABLES:
        observation.table(table, rows[table])
    return observation


def row_map(rows, key):
    result = {}
    for row in rows:
        c.need(isinstance(row, dict), 'INVALID_ROW')
        ident = c.uuid(row.get(key))
        c.need(ident not in result, 'DUPLICATE_ROW')
        result[ident] = row
    return result


def verify_after(before, after, plan, replies):
    """Require exact protected rows and receipt-bound new rows, including raw bodies."""
    for table in c.TABLES:
        c.need(all(r.get('project_id') == c.PROJECT for r in after[table]), 'PROJECT_CHANGED')
    for table in ('projects', 'project_sync_settings'):
        c.need(after[table] == before[table], 'PROJECT_SETTINGS_CHANGED')
    keys = {'documents': 'document_id', 'folders': 'folder_id', 'tree_orders': 'tree_order_id'}
    added = {'documents': {BODY_ID, EMPTY_ID}, 'folders': {c.ROOT_CANDIDATE}, 'tree_orders': {ORDER_ID}}
    maps = {}
    for table, key in keys.items():
        old, new = row_map(before[table], key), row_map(after[table], key)
        c.need(set(new) == set(old) | added[table] and not set(old) & added[table], 'POST_SCOPE_CHANGED')
        for ident, row in old.items():
            if ident == PARENT_ORDER and table == 'tree_orders':
                expected = dict(row, children=row['children'] + [c.ROOT_CANDIDATE],
                                revision=replies[3]['results'][0]['result_revision'])
                # Compare all fields except the server-managed update timestamp.
                c.need({k:v for k,v in new[ident].items() if k != 'updated_at'} ==
                       {k:v for k,v in expected.items() if k != 'updated_at'}, 'PARENT_ORDER_CHANGED')
            else:
                c.need(new[ident] == row, 'PROTECTED_ROW_CHANGED')
        maps[table] = new
    folder = maps['folders'][c.ROOT_CANDIDATE]
    c.need(folder.get('parent_folder_id') == c.PARENT and folder.get('name') == c.ROOT_NAME
         and folder.get('is_deleted') is False
         and type(folder.get('revision')) is int
         and folder.get('revision') == replies[0]['results'][0]['result_revision'], 'NEW_FOLDER_CHANGED')
    for ident, name, reply, expected_body in (
        (BODY_ID, plan['body_name'], replies[1], plan['initial_body']),
        (EMPTY_ID, plan['empty_name'], replies[2], c.body_meta({'content': ''}))):
        row = maps['documents'][ident]
        receipt = reply['results'][0]
        c.need(row.get('parent_folder_id') == c.ROOT_CANDIDATE and row.get('name') == name
             and row.get('is_deleted') is False and type(row.get('revision')) is int
             and row.get('revision') == receipt['result_revision']
             and type(row.get('structure_revision')) is int and row['structure_revision'] == receipt['structure_revision']
             and row.get('relative_path') == plan['root_path'] + '/' + name, 'NEW_DOCUMENT_CHANGED')
        c.need(c.body_meta(row) == expected_body, 'NEW_BODY_CHANGED')
    order = maps['tree_orders'][ORDER_ID]
    c.need(order.get('parent_folder_id') == c.ROOT_CANDIDATE
         and order.get('children') == [BODY_ID, EMPTY_ID]
         and type(order.get('revision')) is int
         and order.get('revision') == replies[3]['results'][1]['result_revision'], 'NEW_ORDER_CHANGED')
    # No inferred revision values: these come from matching receipts and readback.
    return dict(root=deepcopy(folder), documents=[deepcopy(maps['documents'][k]) for k in (BODY_ID, EMPTY_ID)],
                orders=[deepcopy(maps['tree_orders'][k]) for k in (PARENT_ORDER, ORDER_ID)])


def requests_for_creation(plan, parent_order, initial_body_bytes):
    common = dict(project_id=c.PROJECT, project_sync_mode='ID_BASED', migration_epoch=1,
                  writer_device_id=plan['writer_device_id'], client_build_id=BUILD)
    root = build_atomic_structure_request(**common, ordered_intents=[dict(entity_kind='folder',
        entity_id=c.ROOT_CANDIDATE, intent_kind='create', base_revision=0,
        payload=dict(parent_folder_id=c.PARENT, name=c.ROOT_NAME))])
    docs = [build_document_commit_request(**common, document_id=ident, intent_kind='create',
        base_revision=0, structure_revision=1, parent_folder_id=c.ROOT_CANDIDATE,
        name=name, content=content, is_deleted=False) for ident,name,content in (
            (BODY_ID, plan['body_name'], initial_body_bytes.decode('utf-8')),
            (EMPTY_ID, plan['empty_name'], ''))]
    orders = build_atomic_structure_request(**common, ordered_intents=[
        dict(entity_kind='tree_order', entity_id=PARENT_ORDER, intent_kind='reorder',
             base_revision=parent_order['revision'], payload=dict(parent_folder_id=c.PARENT,
                 children=parent_order['children'] + [c.ROOT_CANDIDATE])),
        dict(entity_kind='tree_order', entity_id=ORDER_ID, intent_kind='reorder', base_revision=0,
             payload=dict(parent_folder_id=c.ROOT_CANDIDATE, children=[BODY_ID, EMPTY_ID]))])
    return [root, *docs, orders]


async def prepare_baseline(results_root, scope, plan, metadata_bytes, orders_bytes, initial_body_bytes,
                           *, transport, access_token, api_key, check_current, clock=time.time):
    """One fresh scope: preflight 7 reads + 4 commits + postflight 5 reads.

    Results are receive-baseline candidates, not app binding or apply permission.
    Any interruption permanently consumes this attempt; never resumes/replays.
    """
    c.need(isinstance(scope, dict) and set(scope) == {'format','run_id','plan_sha256','not_before',
        'expires_at','max_requests','max_writes','max_seconds'}, 'INVALID_BOOTSTRAP_SCOPE')
    c.need(scope['format'] == BUILD, 'INVALID_BOOTSTRAP_SCOPE')
    ident = c.uuid(scope['run_id'])
    c.need(ident not in ENDED, 'ENDED_RUN_REFUSED')
    c.need(all(type(scope[k]) is int and scope[k] == n for k,n in (
        ('max_requests',16),('max_writes',4),('max_seconds',180))), 'BOOTSTRAP_LIMIT_CHANGED')
    c.need(l.finite(scope['not_before']) and l.finite(scope['expires_at'])
         and scope['expires_at'] == scope['not_before'] + 180, 'INVALID_BOOTSTRAP_WINDOW')
    expected = prepare_plan(metadata_bytes, orders_bytes, initial_body_bytes, plan['writer_device_id'])
    plan_raw, scope_raw = c.encoded(plan), c.encoded(scope)
    c.need(plan == expected and c.digest(plan_raw) == scope['plan_sha256'], 'PLAN_CHANGED')
    c.need(transport is not None and callable(check_current), 'EXPLICIT_AUTHORITY_REQUIRED')
    c.need(isinstance(api_key, str) and api_key and isinstance(access_token, str) and access_token,
         'CREDENTIAL_INPUT_REQUIRED')
    root = l.absolute(str(results_root))
    root.mkdir(parents=True, exist_ok=True)
    directory = c.safe(root / ident)
    try:
        directory.mkdir(exist_ok=False)
    except FileExistsError:
        raise c.ReadStopped('RUN_ALREADY_USED') from None
    used, writes, acknowledged, event_id = 0, 0, 0, 0
    uncertain, candidate, reason = False, None, None
    last = clock()
    deadline = asyncio.get_running_loop().time() + 180

    def record(event, **data):
        nonlocal event_id
        event_id += 1
        c.new_file(directory / f'{event_id:03d}-{event}.json', c.encoded(data))

    def check():
        nonlocal last
        now = clock()
        c.need(l.finite(now) and l.finite(last) and now >= last, 'CLOCK_ROLLBACK')
        last = now
        c.need(scope['not_before'] <= now < scope['expires_at']
             and asyncio.get_running_loop().time() < deadline, 'DEADLINE_REACHED')
        c.need(c.encoded(scope) == scope_raw and c.encoded(plan) == plan_raw, 'INPUT_CHANGED')
        c.need(check_current() is True, 'AUTHORITY_ENDED')
        l.token_check(access_token, scope['expires_at'])

    async def request(client, method, path, *, payload=None, table=None, phase=None):
        nonlocal used, writes, uncertain
        check()
        write = path in ('/rest/v1/rpc/atomic_structure_commit', '/rest/v1/rpc/document_commit')
        c.need(used < 16 and (not write or writes < 4), 'REQUEST_LIMIT_REACHED')
        index = used + 1
        # Fixed schedule: not a generic sender supplied by callers.
        if index <= 7:
            verb, expected_path, params, expected_payload = c.requests()[index-1]
            c.need((method,path,payload) == (verb,expected_path,expected_payload), 'REQUEST_CHANGED')
        elif index <= 11:
            expected_path = '/rest/v1/rpc/' + ('atomic_structure_commit' if index in (8,11) else 'document_commit')
            c.need(write and method == 'POST' and path == expected_path and
                   payload == {'p_request': creation[index-8]}, 'WRITE_OUTSIDE_PLAN')
            params = None
        else:
            c.need(method == 'GET' and path == '/rest/v1/' + c.TABLES[index-12]
                   and payload is None, 'REQUEST_CHANGED')
            params = {'project_id': 'eq.'+c.PROJECT, 'select':'*', 'limit':'10000'}
        client.cookies.clear()
        raw_body = c.encoded(payload) if payload is not None else b''
        req = client.build_request(method, c.ENDPOINT + path, params=params, content=raw_body,
            headers={'Content-Type':'application/json'} if payload is not None else
                    ({'Prefer':'count=exact'} if table else {}))
        used += 1
        writes += int(write)
        record('reserved', request=used, method=method, path=path, http_reserved=used,
               writes_reserved=writes, body_sha256=c.digest(raw_body))
        check()
        if write:
            uncertain = True
        response = await client.send(req, stream=True)
        try:
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                check()
                c.need(len(raw)+len(chunk) <= c.MAX_RESPONSE, 'RESPONSE_TOO_LARGE')
                raw.extend(chunk)
            raw = bytes(raw)
            c.need(access_token.encode() not in raw and api_key.encode() not in raw, 'CREDENTIAL_IN_RESPONSE')
            c.new_file(directory / f'Q{used}.body', raw)
            record('response', request=used, status=response.status_code, bytes=len(raw), sha256=c.digest(raw))
            c.need(response.status_code in (200,206) and (table or response.status_code == 200), 'HTTP_STATUS_REFUSED')
            value = c.parse(raw)
            if table:
                c.check_count(response.headers.get('content-range'), value)
            return value
        finally:
            await response.aclose()

    try:
        for name,raw in (('scope.json',scope_raw), ('plan.json',plan_raw),
                         ('reference-metadata.json',metadata_bytes),('reference-orders.json',orders_bytes)):
            c.new_file(directory/name, raw)
        check()
        meta, orders, _ = c.reference(metadata_bytes, orders_bytes)
        remaining = min(180, scope['expires_at']-clock())
        async with asyncio.timeout(remaining):
            async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=15,
                    headers={'Authorization':'Bearer '+access_token, 'apikey':api_key, 'Accept-Encoding':'identity'}) as client:
                user = await request(client,'GET','/auth/v1/user')
                c.need(isinstance(user,dict) and user.get('id') == c.ACCOUNT, 'ACCOUNT_CHANGED')
                handshake = await request(client,'POST','/rest/v1/rpc/get_sync_handshake',
                    payload={'p_project_id':c.PROJECT, 'p_contract_sha256':c.CANONICAL_CONTRACT_SHA256})
                if isinstance(handshake,list):
                    c.need(len(handshake)==1, 'HANDSHAKE_SHAPE')
                    handshake = handshake[0]
                c.need(isinstance(handshake,dict) and handshake.get('project_id') == c.PROJECT, 'HANDSHAKE_PROJECT_CHANGED')
                compatible = read_handshake_compatibility(handshake)
                require_server_compatibility(**compatible)
                c.need(compatible['project_sync_mode']=='ID_BASED' and compatible['migration_epoch']==1, 'HANDSHAKE_MODE_CHANGED')
                before = {t:await request(client,'GET','/rest/v1/'+t,table=t) for t in c.TABLES}
                verify_before(before, meta, orders)
                parent_order = next(r for r in before['tree_orders'] if r['tree_order_id']==PARENT_ORDER)
                creation = requests_for_creation(plan, parent_order, initial_body_bytes)
                # Immutable requests, including batch/operation IDs, exist before any write.
                c.new_file(directory/'creation-requests.json', c.encoded(creation))
                replies = []
                for req in creation:
                    check()
                    path = '/rest/v1/rpc/' + ('document_commit' if req['kind']=='document_commit_request' else 'atomic_structure_commit')
                    response = await request(client,'POST',path,payload={'p_request':req})
                    validator = validate_document_commit_response if req['kind']=='document_commit_request' else validate_atomic_structure_response
                    validator(req,response)
                    c.need(response.get('applied') is True, 'COMMIT_REJECTED')
                    c.need(all(type(r['result_revision']) is int and
                               r['result_revision'] == i['base_revision']+1
                               for i,r in zip(req['ordered_intents'],response['results'])), 'COMMIT_REVISION_CHANGED')
                    replies.append(response)
                    acknowledged += 1
                    uncertain = False
                    record('commit-confirmed', batch_id=req['batch']['batch_id'], response_sha256=json_sha256(response))
                after = {t:await request(client,'GET','/rest/v1/'+t,table=t) for t in c.TABLES}
                candidate = verify_after(before, after, plan, replies)
                check()
        baseline = dict(format='windows-isolated-receive-baseline-candidate-v1',run_id=ident,
            endpoint=c.ENDPOINT,account_id=c.ACCOUNT,project_id=c.PROJECT,
            project_sync_mode='ID_BASED',migration_epoch=1,contract_sha256=c.CANONICAL_CONTRACT_SHA256,
            plan_sha256=scope['plan_sha256'],source='same-run-post-create-raw-readback',
            candidate=candidate,body_bytes_verified=True,protected_rows_unchanged=True,
            atomic_snapshot=False,baseline_ready=False,baseline_applied=False,
            execution_allowed=False,app_binding_created=False,complete=False)
        c.new_file(directory/'baseline-candidate.json',c.encoded(baseline))
        record('candidate-prepared', sha256=c.digest(c.encoded(baseline)),
               files={p.name:c.digest(p.read_bytes()) for p in directory.iterdir() if p.is_file()})
    except c.ReadStopped as error:
        reason = str(error)
    except (TimeoutError,httpx.TimeoutException):
        reason = 'DEADLINE_OR_REQUEST_TIMEOUT'
    except asyncio.CancelledError:
        reason = 'CANCELLED'
        raise
    except Exception:
        reason = 'BOOTSTRAP_IO_OR_VALIDATION_FAILED'
    except BaseException:
        reason = 'INTERRUPTED'
        raise
    finally:
        report = dict(status='candidate-prepared' if reason is None and candidate is not None else 'stopped',
            reason=reason,http_reserved=used,writes_reserved=writes,writes_acknowledged=acknowledged,
            write_outcome_uncertain=uncertain,remote_changes_possible=writes>0,resumable=False,
            baseline_ready=False,baseline_applied=False,execution_allowed=False,complete=False)
        record('terminal', **report)
    return report


def read_candidate(directory):
    """Local handoff only. A missing/failed terminal or altered evidence blocks it."""
    directory = l.absolute(str(directory))
    terminals = list(directory.glob('*-terminal.json'))
    seals = list(directory.glob('*-candidate-prepared.json'))
    c.need(len(terminals)==1 and len(seals)==1, 'CANDIDATE_NOT_FINISHED')
    terminal, seal = c.parse(c.safe(terminals[0]).read_bytes()), c.parse(c.safe(seals[0]).read_bytes())
    c.need(terminal.get('status')=='candidate-prepared' and terminal.get('reason') is None
           and terminal.get('http_reserved')==16 and terminal.get('writes_acknowledged')==4
           and terminal.get('write_outcome_uncertain') is False, 'CANDIDATE_NOT_FINISHED')
    c.need(isinstance(seal.get('files'),dict), 'CANDIDATE_SEAL_INVALID')
    c.need(set(p.name for p in directory.iterdir()) == set(seal['files']) | {terminals[0].name,seals[0].name},
           'CANDIDATE_FILES_CHANGED')
    for name,sha in seal['files'].items():
        c.need('/' not in name and '\\' not in name and name not in ('.','..'), 'CANDIDATE_SEAL_INVALID')
        c.need(c.digest(c.safe(directory/name).read_bytes())==sha, 'CANDIDATE_FILES_CHANGED')
    raw = c.safe(directory/'baseline-candidate.json').read_bytes()
    c.need(c.digest(raw)==seal['sha256'], 'CANDIDATE_FILES_CHANGED')
    result = c.parse(raw)
    c.need(result.get('run_id')==directory.name and all(result.get(k) is False for k in (
        'baseline_ready','baseline_applied','execution_allowed','app_binding_created','complete','atomic_snapshot')),
        'CANDIDATE_AUTHORITY_CHANGED')
    return result
