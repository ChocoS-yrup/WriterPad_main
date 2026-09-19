"""Offline, immutable handoff draft. No transport, credentials, app store or CLI.

Copies existing raw bytes; never regenerates server evidence or grants apply rights.
The proposed nested mapping is deliberately marked unresolved pending iPad review.
"""
from copy import deepcopy
from pathlib import Path, PurePosixPath
import re
import stat

import isolated_target_bootstrap as b
import isolated_target_launcher as launcher

c = b.c
FORMAT = 'windows-isolated-receive-handoff-draft-v1'
MAX_INTEGER = 2**53-1
BLOCKERS = ['U1_NESTED_MAPPING_PENDING', 'U1_DATE_DELETE_MAPPING_PENDING',
            'IPAD_INDEPENDENT_RECEIVE_PENDING', 'IPAD_TIME_AND_STORE_PENDING']
AUTHORITY = {k:False for k in ('baseline_ready','baseline_applied','execution_allowed',
    'app_binding_created','complete','atomic_snapshot','editing_allowed',
    'sending_allowed','automatic_receive_allowed')}


def integer(value, minimum=0):
    c.need(type(value) is int and minimum <= value <= MAX_INTEGER, 'HANDOFF_INTEGER')
    return value


def reference(artifact, pointer=''):
    return dict(artifact_id=artifact,json_pointer=pointer)


def resolve(values, ref):
    c.need(isinstance(ref,dict) and set(ref)=={'artifact_id','json_pointer'},'HANDOFF_REFERENCE')
    c.need(ref['artifact_id'] in values and isinstance(ref['json_pointer'],str),'HANDOFF_REFERENCE')
    value=values[ref['artifact_id']];pointer=ref['json_pointer']
    if not pointer:return value
    c.need(pointer.startswith('/'),'HANDOFF_POINTER')
    for token in pointer[1:].split('/'):
        c.need(re.search(r'~(?![01])',token) is None,'HANDOFF_POINTER')
        token=token.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            c.need(re.fullmatch(r'0|[1-9][0-9]*',token) is not None,'HANDOFF_POINTER')
            index=int(token);c.need(index<len(value),'HANDOFF_POINTER');value=value[index]
        else:
            c.need(isinstance(value,dict) and token in value,'HANDOFF_POINTER');value=value[token]
    return value


def safe_relative(name):
    c.need(isinstance(name,str) and name and '\\' not in name and ':' not in name,'HANDOFF_PATH')
    p=PurePosixPath(name)
    c.need(not p.is_absolute() and str(p)==name and all(x not in ('','.','..') for x in name.split('/')),
           'HANDOFF_PATH')
    return name


def snapshot(directory):
    directory=c.safe(directory);c.uuid(directory.name)
    paths=list(directory.iterdir());c.need(0<len(paths)<=200,'HANDOFF_SOURCE_SIZE')
    files={};total=0
    for path in paths:
        path=c.safe(path)
        c.need(stat.S_ISREG(path.stat().st_mode) and path.stat().st_size<=c.MAX_RESPONSE,'HANDOFF_SOURCE_FILE')
        raw=path.read_bytes();total+=len(raw)
        c.need(total<=128*1024*1024,'HANDOFF_SOURCE_SIZE')
        files[path.name]=raw
    return files


def _one(files, suffix):
    names=[n for n in files if n.endswith(suffix)]
    c.need(len(names)==1,'HANDOFF_SOURCE_EVENTS')
    return names[0]


def connect(files, run_id, candidate_sha256):
    """Pure index of an already terminated run, including local semantic bindings."""
    c.uuid(run_id)
    c.need(isinstance(files,dict) and 0<len(files)<=200,'HANDOFF_SOURCE_SIZE')
    for name,raw in files.items():
        safe_relative(name);c.need('/' not in name and isinstance(raw,bytes),'HANDOFF_SOURCE_FILE')
    c.need('baseline-candidate.json' in files and c.digest(files['baseline-candidate.json'])==candidate_sha256,
           'HANDOFF_CANDIDATE_PIN')
    terminal_name=_one(files,'-terminal.json');seal_name=_one(files,'-candidate-prepared.json')
    values={name:c.parse(raw) for name,raw in files.items()}
    terminal,seal=values[terminal_name],values[seal_name]
    c.need(terminal.get('status')=='candidate-prepared' and terminal.get('reason') is None
           and type(terminal.get('http_reserved')) is int and terminal['http_reserved']==16
           and type(terminal.get('writes_acknowledged')) is int and terminal['writes_acknowledged']==4
           and terminal.get('write_outcome_uncertain') is False and terminal.get('resumable') is False,
           'HANDOFF_SOURCE_NOT_FINISHED')
    c.need(isinstance(seal.get('files'),dict) and set(files)==set(seal['files'])|{seal_name,terminal_name},
           'HANDOFF_SOURCE_SET')
    for name,sha in seal['files'].items():c.need(c.digest(files[name])==sha,'HANDOFF_SOURCE_HASH')
    c.need(seal.get('sha256')==candidate_sha256,'HANDOFF_CANDIDATE_PIN')
    candidate=values['baseline-candidate.json'];plan=values['plan.json'];scope=values['scope.json']
    c.need(candidate.get('format')=='windows-isolated-receive-baseline-candidate-v1'
           and scope.get('format')==b.BUILD,'HANDOFF_FORMAT')
    c.need(all(type(scope.get(k)) is int and scope[k]==v for k,v in
               (('max_requests',16),('max_writes',4),('max_seconds',180))), 'HANDOFF_SCOPE')
    c.need(b.l.finite(scope.get('not_before')) and b.l.finite(scope.get('expires_at'))
           and scope['expires_at']==scope['not_before']+180,'HANDOFF_SCOPE')
    binding=dict(endpoint=c.ENDPOINT,account_id=c.ACCOUNT,project_id=c.PROJECT,
        project_sync_mode='ID_BASED',migration_epoch=1,contract_version=c.CONTRACT_VERSION,
        contract_sha256=c.CANONICAL_CONTRACT_SHA256)
    c.need(candidate.get('run_id')==scope.get('run_id')==run_id,'HANDOFF_RUN_BINDING')
    for k,v in binding.items():
        if k!='contract_version':c.need(candidate.get(k)==v,'HANDOFF_BINDING')
    c.need(all(candidate.get(k) is False for k in ('baseline_ready','baseline_applied','execution_allowed',
        'app_binding_created','complete','atomic_snapshot')),'HANDOFF_AUTHORITY')
    c.need(candidate.get('plan_sha256')==scope.get('plan_sha256')==c.digest(c.encoded(plan)),
           'HANDOFF_PLAN_BINDING')
    c.need(values['Q1.body'].get('id')==c.ACCOUNT,'HANDOFF_ACCOUNT')
    handshake=values['Q2.body']
    if isinstance(handshake,list):
        c.need(len(handshake)==1,'HANDOFF_HANDSHAKE');handshake=handshake[0]
    compat=b.read_handshake_compatibility(handshake);b.require_server_compatibility(**compat)
    c.need(handshake['project_id']==c.PROJECT and compat['project_sync_mode']=='ID_BASED'
           and type(compat['migration_epoch']) is int and compat['migration_epoch']==1,'HANDOFF_HANDSHAKE')
    before={table:values[f'Q{i+3}.body'] for i,table in enumerate(c.TABLES)}
    after={table:values[f'Q{i+12}.body'] for i,table in enumerate(c.TABLES)}
    for table in c.TABLES:
        for rows in (before[table],after[table]):
            c.need(isinstance(rows,list) and len(rows)<=10000,'HANDOFF_TABLE')
            c.need(all(isinstance(r,dict) and r.get('project_id')==c.PROJECT for r in rows),'HANDOFF_PROJECT')
    for rows in (before,after):
        c.need(len(rows['projects'])==len(rows['project_sync_settings'])==1,'HANDOFF_TABLE')
        c.need(rows['projects'][0].get('owner_id')==c.ACCOUNT,'HANDOFF_ACCOUNT')
        st=rows['project_sync_settings'][0]
        c.need(st.get('project_sync_mode')=='ID_BASED' and type(st.get('migration_epoch')) is int
               and st['migration_epoch']==1,'HANDOFF_HANDSHAKE')

    def events(suffix):
        found={}
        for name,value in values.items():
            if name.endswith(suffix):
                index=integer(value.get('request'),1)
                c.need(index not in found,'HANDOFF_DUPLICATE_EVENT');found[index]=(name,value)
        c.need(set(found)==set(range(1,17)),'HANDOFF_EVENT_SEQUENCE')
        return found
    reserved,responses=events('-reserved.json'),events('-response.json')
    requests=values['creation-requests.json']
    c.need(isinstance(requests,list) and len(requests)==4,'HANDOFF_CREATION')
    batches=set();operations=set();creation=[]
    for i,request in enumerate(requests):
        batch=c.uuid(request['batch']['batch_id']);c.need(batch not in batches,'HANDOFF_DUPLICATE_BATCH');batches.add(batch)
        ops=[]
        for intent in request['ordered_intents']:
            op=c.uuid(intent['operation_id']);c.need(op not in operations,'HANDOFF_DUPLICATE_OPERATION')
            operations.add(op);ops.append(op)
        reply=values[f'Q{i+8}.body']
        validator=b.validate_document_commit_response if i in (1,2) else b.validate_atomic_structure_response
        validator(request,reply)
        c.need(reply.get('applied') is True,'HANDOFF_RECEIPT')
        creation.append(dict(request_index=i+8,request_ref=reference('creation-requests.json',f'/{i}'),
            batch_id=batch,operation_ids=ops,response_ref=reference(f'Q{i+8}.body')))
    replies=[values[f'Q{i}.body'] for i in range(8,12)]
    actual=b.verify_after(before,after,plan,replies)
    c.need(actual==candidate.get('candidate'),'HANDOFF_CANDIDATE_ROWS')
    expected=b.prepare_plan(files['reference-metadata.json'],files['reference-orders.json'],
        actual['documents'][0]['content'].encode('utf-8'),plan['writer_device_id'])
    c.need(plan==expected,'HANDOFF_PLAN_BINDING')
    parent_order=next(r for r in before['tree_orders'] if r['tree_order_id']==b.PARENT_ORDER)
    launcher.validate_creation(requests,b.requests_for_creation(plan,parent_order,
        actual['documents'][0]['content'].encode('utf-8')))
    observations=[];evidence={};missing=[]
    for index in range(1,17):
        phase='precreate' if index<=7 else ('creation' if index<=11 else 'postcreate')
        if index<=7 or index>=12:
            method,path,query,payload=c.requests()[index-1 if index<=7 else index-10]
        else:
            method,path,query,payload='POST','/rest/v1/rpc/'+('atomic_structure_commit' if index in (8,11) else 'document_commit'),None,{'p_request':requests[index-8]}
        raw_body=c.encoded(payload) if payload is not None else b''
        rn,rv=reserved[index];sn,sv=responses[index];raw=files[f'Q{index}.body']
        c.need(rv==dict(request=index,method=method,path=path,http_reserved=index,
            writes_reserved=min(4,max(0,index-7)),body_sha256=c.digest(raw_body)),'HANDOFF_REQUEST_BINDING')
        c.need(sv.get('bytes')==len(raw) and type(sv.get('bytes')) is int and sv.get('sha256')==c.digest(raw),
               'HANDOFF_RESPONSE_BINDING')
        table=index in range(3,8) or index in range(12,17)
        c.need(type(sv.get('status')) is int and sv['status'] in ((200,206) if table else (200,)),'HANDOFF_STATUS')
        refs=[]
        for field in ('started_at','received_at','content_range','reported_total','row_count'):
            key=f'Q{index}.{field}';refs.append(key)
            if field in ('content_range','reported_total','row_count') and not table:
                state,value,source,mid='not_applicable',None,None,None
            elif field=='row_count':
                state,value,source,mid='derived_from_raw',len(values[f'Q{index}.body']),reference(f'Q{index}.body'),None
            else:
                state,value,source,mid='unavailable',None,None,key
                missing.append(dict(evidence_id=key,expected_role=field,phase=phase,request_index=index,
                                    reason='NOT_RETAINED_BY_SOURCE_ENGINE'))
            evidence[key]=dict(state=state,value=value,source_ref=source,missing_evidence_id=mid)
        observations.append(dict(phase=phase,request_index=index,method=method,path=path,query=query or {},
            request_body_sha256=c.digest(raw_body),response_ref=reference(f'Q{index}.body'),
            http_status=sv['status'],reservation_ref=reference(rn),response_event_ref=reference(sn),evidence_ids=refs))

    # Typed normal rows; opaque/special metadata remains context-only with raw refs.
    nodes={};orders={};opaque=[]
    for table,key,kind,q in (('folders','folder_id','folder',15),('documents','document_id','document',14),
                              ('tree_orders','tree_order_id','tree_order',16)):
        for i,row in enumerate(after[table]):
            ident=c.uuid(row.get(key));c.need(ident not in nodes and ident not in orders and
                all(x['entity_id']!=ident for x in opaque),'HANDOFF_DUPLICATE_ENTITY')
            ref=reference(f'Q{q}.body',f'/{i}')
            if kind=='document' and str(row.get('relative_path','')).startswith('__antigravity__/'):
                opaque.append(dict(entity_kind=kind,entity_id=ident,classification='special-metadata',source_refs=[ref]));continue
            c.need('parent_folder_id' in row,'HANDOFF_PARENT_MISSING')
            parent=row['parent_folder_id']
            if parent is not None:c.uuid(parent)
            entry=dict(entity_kind=kind,entity_id=ident,parent_id=parent,name=row.get('name') if kind!='tree_order' else None,
                revision=integer(row.get('revision'),1),allowed_actions=[],source_refs=[ref])
            if kind=='tree_order':
                children=row.get('children');c.need(isinstance(children,list),'HANDOFF_CHILDREN')
                for child in children:c.uuid(child)
                c.need(len(set(children))==len(children),'HANDOFF_CHILDREN')
                entry['children']=children;orders[ident]=entry
            else:
                c.need(isinstance(row.get('name'),str) and type(row.get('is_deleted')) is bool,'HANDOFF_NODE')
                entry['is_deleted']=row['is_deleted']
                if kind=='document':
                    entry['structure_revision']=integer(row.get('structure_revision'),1)
                    entry['body']=c.body_meta(row)
                nodes[ident]=entry
    for ident,node in nodes.items():
        seen=set();at=ident;parts=[]
        while at is not None:
            c.need(at in nodes and at not in seen,'HANDOFF_GRAPH')
            seen.add(at);n=nodes[at]
            c.need(at==ident or n['entity_kind']=='folder','HANDOFF_GRAPH')
            c.need(node['is_deleted'] or not n['is_deleted'],'HANDOFF_GRAPH')
            parts.append(n['name']);at=n['parent_id']
        if node['entity_kind']=='document':
            row=resolve(values,node['source_refs'][0])
            c.need(row.get('relative_path')=='/'.join(reversed(parts)),'HANDOFF_DOCUMENT_PATH')
    ancestors=set();at=c.PARENT
    while at is not None:
        c.need(at in nodes and at not in ancestors and nodes[at]['entity_kind']=='folder'
               and not nodes[at]['is_deleted'],'HANDOFF_ANCESTORS')
        ancestors.add(at);at=nodes[at]['parent_id']
    parents=set()
    for order in orders.values():
        parent=order['parent_id'];c.need(parent not in parents,'HANDOFF_ORDER_PARENT');parents.add(parent)
        c.need(parent is None or parent in nodes and nodes[parent]['entity_kind']=='folder','HANDOFF_ORDER_PARENT')
        active={ident for ident,node in nodes.items() if node['parent_id']==parent and not node['is_deleted']}
        c.need(set(order['children'])==active,'HANDOFF_CHILDREN')
    c.need(parents>={n['parent_id'] for n in nodes.values() if not n['is_deleted']},'HANDOFF_ORDER_MISSING')
    member_ids={c.ROOT_CANDIDATE,b.BODY_ID,b.EMPTY_ID,b.ORDER_ID}
    reference_orders={ident for ident,o in orders.items() if o['parent_id'] in ancestors|{None}}
    reference_ids=ancestors|reference_orders
    for ident in reference_orders:reference_ids.update(orders[ident]['children'])
    reference_ids-=member_ids
    all_entries={**nodes,**orders}
    c.need(member_ids<=set(all_entries) and reference_ids<=set(all_entries),'HANDOFF_MEMBERS')
    target=dict(format='windows-isolated-target-link-draft-v1',binding=binding,root_id=c.ROOT_CANDIDATE,parent_id=c.PARENT,
        members=[all_entries[k] for k in sorted(member_ids)],references=[all_entries[k] for k in sorted(reference_ids)],
        context_only=[all_entries[k] for k in sorted(set(all_entries)-member_ids-reference_ids)]+opaque,
        authority=deepcopy(AUTHORITY))
    artifacts=[dict(artifact_id=n,role='retained-source',path='source/'+n,sha256=c.digest(raw),byte_count=len(raw))
               for n,raw in sorted(files.items())]
    artifacts.append(dict(artifact_id='target.json',role='target-draft',path='target.json',
                          sha256=c.digest(c.encoded(target)),byte_count=len(c.encoded(target))))
    checks=[]
    check_sources={'identity_project':['Q1.body','Q12.body'], 'handshake_contract':['Q2.body'],
        'settings_coherence':['Q2.body','Q13.body'], 'target_binding':['target.json'],
        'body_versions':['Q14.body','Q15.body','Q16.body'],
        'visible_completeness':['Q12.body','Q13.body','Q14.body','Q15.body','Q16.body'],
        'creation_link':['creation-requests.json','Q8.body','Q9.body','Q10.body','Q11.body'],
        'preapply_consistency':[]}
    for name in ('identity_project','handshake_contract','settings_coherence','target_binding','body_versions',
                 'visible_completeness','creation_link','preapply_consistency'):
        checks.append(dict(check_id=name,expected='agreed-contract-with-pending-mapping',
            reported=dict(producer='Windows source engine',run_id=run_id,
                state='unverified' if name in ('preapply_consistency','target_binding') else 'pass',
                value=None if name in ('preapply_consistency','target_binding') else True),
            independent=dict(verifier='iPad',run_id=None,state='unverified',value=None),
            evidence_refs=[reference(n) for n in check_sources[name]]+[reference(terminal_name)],
            reason='SOURCE_REPORT_ONLY; NO_IPAD_VERIFICATION; COUNT_HEADERS_UNAVAILABLE' if name=='visible_completeness'
                else 'NO_IPAD_VERIFICATION'))
    index=dict(format=FORMAT,intended_format='windows-isolated-receive-handoff-v1',schema_version=1,
        schema_finalized=False,blocked_reasons=list(BLOCKERS),source_run_id=run_id,candidate_sha256=candidate_sha256,
        binding=binding,plan_contract_sha256=scope['plan_sha256'],plan_artifact_ref=reference('plan.json'),
        target=dict(manifest_ref=reference('target.json'),**{k:target[k] for k in ('binding','root_id','parent_id','members','references','context_only')}),
        artifacts=artifacts,creation=creation,observations=observations,evidence=evidence,missing_evidence=missing,
        checks=checks,authority=deepcopy(AUTHORITY),local_source_links_verified=True,
        raw_count_independently_verified=False,server_provenance_verified=False)
    return index,target


def export(directory, output, *, candidate_sha256):
    """Create one local self-contained draft. Output must be new and disjoint."""
    source=c.safe(directory);output=c.safe(output)
    c.need(source!=output and source not in output.parents and output not in source.parents,'HANDOFF_OUTPUT_OVERLAP')
    files=snapshot(source);index,target=connect(files,source.name,candidate_sha256)
    c.need(snapshot(source)==files,'HANDOFF_SOURCE_CHANGED')
    output.parent.mkdir(parents=True,exist_ok=True)
    try:output.mkdir(exist_ok=False)
    except FileExistsError:raise c.ReadStopped('HANDOFF_OUTPUT_EXISTS') from None
    (output/'source').mkdir()
    for name,raw in sorted(files.items()):c.new_file(output/'source'/name,raw)
    c.new_file(output/'target.json',c.encoded(target));c.new_file(output/'handoff.json',c.encoded(index))
    # Partial output never gains a terminal; no cleanup or resume on failure.
    c.need(snapshot(source)==files,'HANDOFF_SOURCE_CHANGED')
    c.new_file(output/'completed.json',c.encoded(dict(format='windows-handoff-local-seal-v1',
        handoff_sha256=c.digest(c.encoded(index)),target_sha256=c.digest(c.encoded(target)),
        source_run_id=source.name,source_files={n:c.digest(raw) for n,raw in files.items()},
        execution_allowed=False)))
    return index


def inspect(output, *, expected_handoff_sha256):
    output=c.safe(output)
    raw=c.safe(output/'handoff.json').read_bytes()
    c.need(c.digest(raw)==expected_handoff_sha256,'HANDOFF_INDEX_PIN')
    seal=c.parse(c.safe(output/'completed.json').read_bytes());index=c.parse(raw)
    c.need(seal.get('handoff_sha256')==expected_handoff_sha256 and seal.get('execution_allowed') is False,
           'HANDOFF_SEAL')
    c.need(set(p.name for p in output.iterdir())=={'source','handoff.json','target.json','completed.json'},'HANDOFF_OUTPUT_SET')
    source=c.safe(output/'source');files={}
    for p in source.iterdir():
        c.safe(p);c.need(p.is_file(),'HANDOFF_SOURCE_FILE');files[p.name]=p.read_bytes()
    c.need({n:c.digest(v) for n,v in files.items()}==seal.get('source_files'),'HANDOFF_SOURCE_HASH')
    expected,target=connect(files,index['source_run_id'],index['candidate_sha256'])
    target_raw=c.safe(output/'target.json').read_bytes()
    c.need(raw==c.encoded(expected) and target_raw==c.encoded(target)
           and c.digest(target_raw)==seal.get('target_sha256'),'HANDOFF_LINKS_CHANGED')
    return expected


def require_apply_input(_):
    raise c.ReadStopped('REAL_CONTRACT_UNRESOLVED')
