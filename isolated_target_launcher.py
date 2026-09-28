"""Reviewed xiix1 target creation launcher. No default launch or credential refresh."""
import argparse
import asyncio
import base64
from copy import deepcopy
from dataclasses import dataclass
import hmac
import json
from pathlib import Path
import time

import httpx
import isolated_read_collector as c
import isolated_read_launcher as l
import isolated_target_bootstrap as b

SID = 'S-1-5-21-2542480074-635446901-4096835241-1001'
FORMAT = 'windows-isolated-target-launch-v1'
CODE_FILES = ('isolated_target_launcher.py','isolated_target_bootstrap.py') + l.CODE_FILES
INPUT_FILES = {'metadata':'before-metadata.json','orders':'before-orders.json',
               'body':'proposed-bodies/initial-body.txt','empty':'proposed-bodies/initial-empty.txt'}


@dataclass(frozen=True)
class Boundary:
    root: Path
    code_root: Path
    proposal_root: Path
    config_path: Path
    preserved_paths: tuple


def live_boundary():
    c.need(Path(__file__).resolve().parent == l.WORKSPACE, 'LAUNCHER_LOCATION_CHANGED')
    app = Path(r'C:\Users\xiix1\AppData\Local\AntigravityWriter')
    return Boundary(l.WORKSPACE/'_evidence/windows-independent-target-create',l.WORKSPACE,
        l.WORKSPACE/'output/windows-ipad-isolated-target-reply-20260913',
        l.WORKSPACE/'_evidence/windows-integrated-receipt-candidate-20260913/source/release_cloud_config.json',
        (app/('.general-test-send-hold-'+c.PROJECT),app/'integrated-editor-20260913/reviewed-execution.json'))


def token_check(token, expires_at):
    l.token_check(token, expires_at)
    payload = token.split('.')[1]
    claims = c.parse(base64.b64decode(payload+'='*(-len(payload)%4),altchars=b'-_',validate=True))
    c.need(claims.get('iss') == c.ENDPOINT+'/auth/v1','TOKEN_ISSUER')


def read_manifest(path, sha, boundary):
    raw = l.read_pin({'path':str(path),'sha256':sha})
    m = c.parse(raw)
    c.need(isinstance(m,dict) and set(m)=={'format','run_id','results_root','windows_sid','profile',
        'credential_service','writer_device_id','scope','plan','inputs','config','source_sha256',
        'writable_ids','order_ids','restriction_review','restrictions'},'INVALID_TARGET_MANIFEST')
    c.need(m['format']==FORMAT and m['windows_sid']==SID and m['profile']==''
           and m['credential_service']==l.SERVICE,'IDENTITY_BINDING')
    c.uuid(m['run_id']); c.uuid(m['writer_device_id'])
    c.need(m['run_id'] not in b.ENDED,'ENDED_RUN_REFUSED')
    c.need(l.absolute(m['results_root'])==c.safe(boundary.root),'RESULT_ROOT_CHANGED')
    c.need(m['writable_ids']==[c.ROOT_CANDIDATE,b.BODY_ID,b.EMPTY_ID]
           and m['order_ids']==[b.PARENT_ORDER,b.ORDER_ID],'WRITE_SCOPE_CHANGED')
    c.need(isinstance(m['source_sha256'],dict) and set(m['source_sha256'])==set(CODE_FILES),'SOURCE_SET_CHANGED')
    for name,hash_value in m['source_sha256'].items():
        l.read_pin({'path':str(boundary.code_root/name),'sha256':hash_value})
    c.need(isinstance(m['inputs'],dict) and set(m['inputs'])==set(INPUT_FILES),'INPUT_SET_CHANGED')
    for key,name in INPUT_FILES.items():
        c.need(isinstance(m['inputs'][key],dict) and
               l.absolute(m['inputs'][key].get('path'))==c.safe(boundary.proposal_root/name),'INPUT_PATH_CHANGED')
    inputs = {key:l.read_pin(pin) for key,pin in m['inputs'].items()}
    c.need(inputs['empty']==b'','EMPTY_BODY_CHANGED')
    plan_raw,scope_raw = l.read_pin(m['plan']),l.read_pin(m['scope'])
    plan,scope = c.parse(plan_raw),c.parse(scope_raw)
    expected = b.prepare_plan(inputs['metadata'],inputs['orders'],inputs['body'],m['writer_device_id'])
    c.need(plan==expected,'PLAN_BINDING')
    c.need(isinstance(scope,dict) and set(scope)=={'format','run_id','plan_sha256','not_before',
        'expires_at','max_requests','max_writes','max_seconds'},'INVALID_TARGET_SCOPE')
    c.need(scope['format']==b.BUILD and scope['run_id']==m['run_id']
           and scope['plan_sha256']==c.digest(c.encoded(plan)),'SCOPE_BINDING')
    c.need(all(type(scope[k]) is int and scope[k]==v for k,v in (
        ('max_requests',16),('max_writes',4),('max_seconds',180))),'LIMIT_CHANGED')
    c.need(l.finite(scope['not_before']) and l.finite(scope['expires_at'])
           and scope['expires_at']==scope['not_before']+180,'WINDOW_CHANGED')
    c.need(isinstance(m['config'],dict) and
           l.absolute(m['config'].get('path'))==c.safe(boundary.config_path),'CONFIG_PATH_CHANGED')
    c.need(m['restriction_review']=='reviewed-for-independent-target-create-v1'
           and isinstance(m['restrictions'],list),'RESTRICTIONS_UNREVIEWED')
    seen = {}
    for item in m['restrictions']:
        c.need(isinstance(item,dict) and set(item)=={'path','sha256','effect'}
               and item['effect'] in ('preserve_only','block_when_present'),'INVALID_RESTRICTION')
        p = l.absolute(item['path'])
        c.need(p not in seen and p!=c.safe(boundary.root) and c.safe(boundary.root) not in p.parents,
               'RESTRICTION_PATH_CHANGED')
        seen[p]=item
        if item['sha256'] is not None:l.hash_string(item['sha256'])
    for p in boundary.preserved_paths:
        item = seen.get(c.safe(p))
        c.need(item is not None and item['effect']=='preserve_only' and item['sha256'] is not None,
               'PRESERVATION_PIN_MISSING')
    return m,raw,plan,scope,inputs,plan_raw,scope_raw


def validate_creation(actual, templates):
    c.need(isinstance(actual,list) and len(actual)==4,'CREATION_REQUEST_SET_CHANGED')
    batches,operations = set(),set()
    for value,template in zip(actual,templates):
        expected=deepcopy(template)
        batch_id=c.uuid(value['batch']['batch_id'])
        c.need(batch_id not in batches,'DUPLICATE_BATCH')
        batches.add(batch_id)
        expected['batch']['batch_id']=batch_id
        c.need(len(value['ordered_intents'])==len(expected['ordered_intents']),'WRITE_SCOPE_CHANGED')
        for supplied,intent in zip(value['ordered_intents'],expected['ordered_intents']):
            operation=c.uuid(supplied['operation_id'])
            c.need(operation not in operations,'DUPLICATE_OPERATION')
            operations.add(operation)
            intent.update(batch_id=batch_id,operation_id=operation)
        expected['batch']['batch_payload_sha256']=b.json_sha256(expected['ordered_intents'])
        c.need(value==expected,'CREATION_REQUEST_CHANGED')


class TargetTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner, check, run, templates, token, api_key):
        self.inner,self.check,self.run,self.templates=inner,check,run,templates
        self.token,self.api_key=token,api_key
        self.forwarded,self.writes,self.closed=0,0,False
        self.creation_raw=None

    async def handle_async_request(self, request):
        c.need(not self.closed and self.forwarded<16,'EXTRA_REQUEST_REFUSED')
        index=self.forwarded+1
        if index<=7 or index>=12:
            method,path,params,payload=c.requests()[index-1 if index<=7 else index-10]
        else:
            raw=c.safe(self.run/'creation-requests.json').read_bytes()
            if self.creation_raw is None:
                validate_creation(c.parse(raw),self.templates)
                self.creation_raw=raw
            c.need(raw==self.creation_raw,'CREATION_REQUEST_CHANGED')
            req=c.parse(raw)[index-8]
            method,params,payload='POST',None,{'p_request':req}
            path='/rest/v1/rpc/'+('atomic_structure_commit' if index in (8,11) else 'document_commit')
        content=c.encoded(payload) if payload is not None else b''
        c.need(request.method==method and request.url==httpx.URL(c.ENDPOINT+path,params=params)
               and request.content==content,'REQUEST_CHANGED')
        headers={'host':httpx.URL(c.ENDPOINT).host,'accept':'*/*','accept-encoding':'identity',
            'connection':'keep-alive','user-agent':'python-httpx/'+httpx.__version__,
            'authorization':'Bearer '+self.token,'apikey':self.api_key}
        if payload is not None:headers.update({'content-type':'application/json','content-length':str(len(content))})
        elif params:headers['prefer']='count=exact'
        c.need(dict(request.headers)==headers and len(request.headers.multi_items())==len(headers),'HEADERS_CHANGED')
        reservations=sorted(self.run.glob('*-reserved.json'))
        c.need(bool(reservations),'RESERVATION_MISSING')
        reservation=c.parse(c.safe(reservations[-1]).read_bytes())
        c.need(reservation==dict(request=index,method=method,path=path,http_reserved=index,
            writes_reserved=min(4,max(0,index-7)),body_sha256=c.digest(content)),'RESERVATION_CHANGED')
        self.check()
        self.forwarded+=1
        self.writes+=int(8<=index<=11)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        if not self.closed:
            self.closed=True
            await self.inner.aclose()


async def launch(manifest_path, manifest_sha256, *, boundary, token_source,
                 lease_factory, transport_factory, clock=time.time):
    l.environment_check()
    m,raw,plan,scope,inputs,plan_raw,scope_raw=read_manifest(manifest_path,manifest_sha256,boundary)
    root=c.safe(boundary.root)
    attempt=c.safe(root/'attempts'/m['run_id']); run=c.safe(root/'runs'/m['run_id'])
    c.need(not run.exists(),'RUN_ALREADY_USED')
    attempt.parent.mkdir(parents=True,exist_ok=True)
    try:attempt.mkdir(exist_ok=False)
    except FileExistsError:raise c.ReadStopped('RUN_ALREADY_USED') from None
    leased,entered=False,False
    lease,guard,report,reason=None,None,None,None
    sequence,last=0,clock()

    def record(event,**data):
        nonlocal sequence
        sequence+=1
        c.new_file(attempt/f'{sequence:03d}-{event}.json',c.encoded(data))

    def unchanged():
        nonlocal last
        l.environment_check()
        now=clock()
        c.need(l.finite(now) and l.finite(last) and now>=last,'CLOCK_ROLLBACK')
        last=now
        c.need(scope['not_before']<=now<scope['expires_at'],'OUTSIDE_EXECUTION_WINDOW')
        c.need(l.read_pin({'path':str(manifest_path),'sha256':manifest_sha256})==raw,'MANIFEST_CHANGED')
        c.need(l.read_pin(m['plan'])==plan_raw and l.read_pin(m['scope'])==scope_raw,'PLAN_OR_SCOPE_CHANGED')
        for key,pin in m['inputs'].items():c.need(l.read_pin(pin)==inputs[key],'INPUT_CHANGED')
        for name,hash_value in m['source_sha256'].items():l.read_pin({'path':str(boundary.code_root/name),'sha256':hash_value})
        l.restrictions_check(m['restrictions'])
        config=l.validate_cloud_client_config(c.parse(l.read_pin(m['config'])))
        c.need(config.is_ready and config.url==c.ENDPOINT,'STAGING_CONFIG_REQUIRED')
        return config

    try:
        record('claimed',run_id=m['run_id'],manifest_sha256=manifest_sha256)
        c.new_file(attempt/'launch-manifest.json',raw)
        config=unchanged()
        lease=lease_factory(SID)
        c.need(lease.acquire() is True,'CREDENTIAL_LEASE_REFUSED')
        leased=True
        unchanged()
        token=token_source.read()
        token_check(token,scope['expires_at'])

        def current():
            unchanged()
            value=token_source.read()
            c.need(isinstance(value,str) and hmac.compare_digest(value.encode(),token.encode()),'SESSION_CHANGED')
            token_check(value,scope['expires_at'])
            return True

        current()
        orders=c.parse(inputs['orders'])
        order=next(o for o in orders['orders'] if o['id']==b.PARENT_ORDER)
        templates=b.requests_for_creation(plan,order,inputs['body'])
        guard=TargetTransport(transport_factory(),current,run,templates,token,config.publishable_key)
        record('engine-entering',plan_sha256=scope['plan_sha256'])
        entered=True
        report=await b.prepare_baseline(root/'runs',scope,plan,inputs['metadata'],inputs['orders'],inputs['body'],
            transport=guard,access_token=token,api_key=config.publishable_key,check_current=current,clock=clock)
        reason=report['reason']
    except c.ReadStopped as error:reason=str(error)
    except asyncio.CancelledError:
        reason='CANCELLED'; raise
    except Exception:reason='TARGET_LAUNCH_IO_OR_VALIDATION_FAILED'
    except BaseException:
        reason='INTERRUPTED'; raise
    finally:
        try:
            if guard is not None:await guard.aclose()
        finally:
            try:
                if leased:lease.release()
            finally:
                record('terminal',reason=reason,engine_entered=entered,engine_returned=report is not None,
                    http_reserved=report['http_reserved'] if report else (None if entered else 0),
                    writes_reserved=report['writes_reserved'] if report else (None if entered else 0),
                    http_forwarded=guard.forwarded if guard else 0,writes_forwarded=guard.writes if guard else 0,
                    resumable=False)
    return dict(format='windows-isolated-target-launch-result-v1',status=report['status'] if report else 'stopped',
        reason=reason,http_reserved=report['http_reserved'] if report else (None if entered else 0),
        writes_reserved=report['writes_reserved'] if report else (None if entered else 0),
        http_forwarded=guard.forwarded if guard else 0,writes_forwarded=guard.writes if guard else 0,
        baseline_ready=False,baseline_applied=False,execution_allowed=False,resumable=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description='One reviewed isolated target creation; no refresh or resume.')
    parser.add_argument('--manifest'); parser.add_argument('--manifest-sha256')
    args=parser.parse_args(argv)
    if not args.manifest or not args.manifest_sha256:
        parser.print_help(); return 2
    try:
        result=asyncio.run(launch(args.manifest,args.manifest_sha256,boundary=live_boundary(),
            token_source=l.WindowsAccessToken(),lease_factory=l.WindowsLease,transport_factory=l.live_transport))
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['status']=='candidate-prepared' else 1
    except BaseException:
        print('{"status":"stopped","reason":"TARGET_PREFLIGHT_OR_AUDIT_FAILED","resumable":false}')
        return 1


if __name__=='__main__':raise SystemExit(main())
