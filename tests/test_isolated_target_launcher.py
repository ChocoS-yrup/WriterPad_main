"""New launcher tests using synthetic fixtures only, never prior test methods."""
import asyncio
import base64
from contextlib import redirect_stdout
from copy import deepcopy
import io
import unittest
from unittest.mock import patch

import httpx
import isolated_target_launcher as t
from tests.test_isolated_target_bootstrap import BootstrapTests, PROTECTED
from tests.test_isolated_read_launcher import Token, Lease

c, b, l = t.c, t.b, t.l


def jwt(exp=1400, issuer=c.ENDPOINT+'/auth/v1', sub=c.ACCOUNT):
    payload=base64.urlsafe_b64encode(c.encoded(dict(sub=sub,exp=exp,iss=issuer))).decode().rstrip('=')
    return 'e30.'+payload+'.c3ludGhldGlj'


_bootstrap_setup = BootstrapTests.setUp


class TargetLauncherTests(unittest.IsolatedAsyncioTestCase):
    path=BootstrapTests.path
    handler=BootstrapTests.handler

    def setUp(self):
        _bootstrap_setup(self)
        env=patch.dict(l.os.environ,{name:'' for name in (
            'ANTIGRAVITY_PROFILE','ANTIGRAVITY_ROOT_DIR','ANTIGRAVITY_APP_DATA_DIR',
            'ANTIGRAVITY_SYNC_PROJECT_ID','ANTIGRAVITY_FORCE_PROJECT_ID',
            'ANTIGRAVITY_INSTANCE_KEY','ANTIGRAVITY_SYNC_OFFLINE_FILE')})
        env.start(); self.addCleanup(env.stop)
        self.boundary=t.Boundary(self.base/'results',self.base/'code',self.base/'proposal',
            self.base/'config.json',(self.base/'hold',self.base/'ended-record'))
        self.boundary.code_root.mkdir()
        for name in t.CODE_FILES:(self.boundary.code_root/name).write_bytes(('synthetic '+name).encode())
        for key,value in dict(metadata=self.metadata_bytes,orders=self.orders_bytes,body=self.body,empty=b'').items():
            p=self.boundary.proposal_root/t.INPUT_FILES[key]
            p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value)
        self.boundary.config_path.write_bytes(c.encoded(dict(supabase_url=c.ENDPOINT,
            supabase_publishable_key='sb_publishable_synthetic_public_key_0001')))
        for p in self.boundary.preserved_paths:p.write_bytes(b'synthetic preserved record')
        self.plan_path,self.scope_path,self.manifest_path=(self.base/n for n in ('plan.json','scope.json','manifest.json'))
        self.plan_path.write_bytes(c.encoded(self.plan));self.scope_path.write_bytes(c.encoded(self.scope))
        self.m=dict(format=t.FORMAT,run_id=self.scope['run_id'],results_root=str(self.boundary.root),
            windows_sid=t.SID,profile='',credential_service=l.SERVICE,writer_device_id=self.plan['writer_device_id'],
            plan=self.pin(self.plan_path),scope=self.pin(self.scope_path),
            inputs={k:self.pin(self.boundary.proposal_root/n) for k,n in t.INPUT_FILES.items()},
            config=self.pin(self.boundary.config_path),source_sha256={n:self.pin(self.boundary.code_root/n)['sha256'] for n in t.CODE_FILES},
            writable_ids=[c.ROOT_CANDIDATE,b.BODY_ID,b.EMPTY_ID],order_ids=[b.PARENT_ORDER,b.ORDER_ID],
            restriction_review='reviewed-for-independent-target-create-v1',
            restrictions=[dict(self.pin(p),effect='preserve_only') for p in self.boundary.preserved_paths])
        self.token,self.lease=Token(),Lease();self.token.value=jwt()
        self.factories=0;self.expected_sid=None
        self.save_manifest()

    def pin(self,p):return dict(path=str(p),sha256=c.digest(p.read_bytes()))
    def save_manifest(self):
        self.manifest_path.write_bytes(c.encoded(self.m));self.sha=self.pin(self.manifest_path)['sha256']
    @property
    def directory(self):return self.boundary.root/'runs'/self.scope['run_id']
    @property
    def attempt(self):return self.boundary.root/'attempts'/self.scope['run_id']
    def lease_factory(self,sid):self.expected_sid=sid;return self.lease
    def transport_factory(self):
        self.factories+=1
        async def handler(request):
            self.assertTrue(self.lease.active)
            return await self.handler(request)
        return httpx.MockTransport(handler)
    async def run_launch(self):
        return await t.launch(self.manifest_path,self.sha,boundary=self.boundary,token_source=self.token,
            lease_factory=self.lease_factory,transport_factory=self.transport_factory,clock=lambda:self.now)
    async def rejected(self):
        with self.assertRaises(c.ReadStopped):await self.run_launch()
        self.assertEqual((self.token.reads,self.factories,len(self.calls)),(0,0,0))

    async def test_full_launcher_chain_preserves_originals_and_secrets(self):
        result=await self.run_launch()
        self.assertEqual((result['status'],result['http_reserved'],result['writes_forwarded']),('candidate-prepared',16,4),result)
        self.assertEqual(self.expected_sid,t.SID);self.assertEqual(self.lease.releases,1)
        self.assertFalse(self.lease.active)
        self.assertEqual(self.server.nodes[PROTECTED],self.before_nodes[PROTECTED])
        self.assertFalse(b.read_candidate(self.directory)['baseline_ready'])
        for p in self.boundary.root.rglob('*'):
            if p.is_file():
                self.assertNotIn(self.token.value.encode(),p.read_bytes())
                self.assertNotIn(b'COOKIE_SECRET',p.read_bytes())
        self.assertGreater(self.token.reads,16)

    async def test_wrong_windows_user_rejected(self):
        self.m['windows_sid']=t.SID[:-4]+'1005';self.save_manifest();await self.rejected()
    async def test_profile_override_rejected(self):
        with patch.dict(l.os.environ,ANTIGRAVITY_PROFILE='other'):await self.rejected()
    async def test_manifest_hash_is_required(self):
        self.sha='0'*64;await self.rejected()
    async def test_write_scope_cannot_expand(self):
        self.m['writable_ids'].append(c.PARENT);self.save_manifest();await self.rejected()
    async def test_ended_run_rejected(self):
        self.m['run_id']=next(iter(b.ENDED));self.save_manifest();await self.rejected()
    async def test_source_drift_rejected_before_credentials(self):
        (self.boundary.code_root/t.CODE_FILES[0]).write_bytes(b'changed');await self.rejected()
    async def test_missing_preservation_pin_rejected(self):
        self.m['restrictions'].pop();self.save_manifest();await self.rejected()
    async def test_limits_cannot_expand(self):
        self.scope['max_requests']=17;self.scope_path.write_bytes(c.encoded(self.scope))
        self.m['scope']=self.pin(self.scope_path);self.save_manifest();await self.rejected()
    async def test_busy_lease_does_not_read_token(self):
        self.lease.allowed=False;result=await self.run_launch()
        self.assertEqual(result['reason'],'CREDENTIAL_LEASE_REFUSED')
        self.assertEqual((self.token.reads,len(self.calls),self.lease.releases),(0,0,0))
    async def test_expired_approval_consumes_attempt_without_credentials(self):
        self.now=1180;result=await self.run_launch()
        self.assertEqual(result['reason'],'OUTSIDE_EXECUTION_WINDOW');self.assertEqual(self.token.reads,0)
        self.now=1000
        with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'):await self.run_launch()
    async def test_expired_session_no_refresh_or_http(self):
        self.token.value=jwt(exp=1181);result=await self.run_launch()
        self.assertEqual(result['reason'],'TOKEN_EXPIRY');self.assertEqual(len(self.calls),0)
        self.assertEqual(self.lease.releases,1)
    async def test_wrong_issuer_no_http(self):
        self.token.value=jwt(issuer='https://other.supabase.co/auth/v1');result=await self.run_launch()
        self.assertEqual(result['reason'],'TOKEN_ISSUER');self.assertEqual(self.factories,0)
    async def test_wrong_account_no_http(self):
        self.token.value=jwt(sub=c.PARENT);result=await self.run_launch()
        self.assertEqual(len(self.calls),0);self.assertEqual(result['status'],'stopped')
    async def test_hold_change_during_preflight_stops_before_write(self):
        self.post_hook=lambda name,response:self.boundary.preserved_paths[0].write_bytes(b'changed') and None
        result=await self.run_launch()
        self.assertEqual((len(self.calls),result['writes_forwarded']),(1,0))
        self.assertEqual(result['status'],'stopped')
    async def test_session_change_after_first_write_does_not_retry(self):
        def hook(name,response):
            if name=='atomic_structure_commit':self.token.value=jwt(exp=1500)
        self.post_hook=hook;result=await self.run_launch()
        self.assertEqual(result['reason'],'SESSION_CHANGED')
        self.assertEqual((len(self.calls),result['writes_forwarded'],self.commits),(8,1,1))
    async def test_input_change_between_requests_stops(self):
        def hook(name,response):
            if name=='tree_orders':(self.boundary.proposal_root/t.INPUT_FILES['body']).write_bytes(b'changed')
        self.post_hook=hook;result=await self.run_launch()
        self.assertEqual((len(self.calls),result['writes_forwarded']),(7,0))
    async def test_completed_attempt_cannot_replay(self):
        await self.run_launch()
        with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'):await self.run_launch()
        self.assertEqual(len(self.calls),16)
    async def test_lost_write_response_keeps_one_forwarded_write(self):
        def hook(name,response):
            if name=='atomic_structure_commit':raise httpx.ReadTimeout('synthetic response lost')
        self.post_hook=hook;result=await self.run_launch()
        self.assertEqual((result['status'],result['writes_forwarded'],self.commits),('stopped',1,1))
        terminal=c.parse(next(self.directory.glob('*-terminal.json')).read_bytes())
        self.assertTrue(terminal['write_outcome_uncertain']);self.assertFalse(result['resumable'])
    async def test_cancel_releases_lease_and_seals_attempt(self):
        def hook(request):raise asyncio.CancelledError()
        self.hook=hook
        with self.assertRaises(asyncio.CancelledError):await self.run_launch()
        self.assertEqual(self.lease.releases,1)
        terminal=c.parse(next(self.attempt.glob('*-terminal.json')).read_bytes())
        self.assertEqual(terminal['reason'],'CANCELLED');self.assertIsNone(terminal['http_reserved'])
    async def test_wire_cookie_injection_is_refused(self):
        original=t.TargetTransport.handle_async_request
        async def altered(guard,request):
            request.headers['cookie']='injected';return await original(guard,request)
        with patch.object(t.TargetTransport,'handle_async_request',altered):result=await self.run_launch()
        self.assertEqual(result['reason'],'HEADERS_CHANGED');self.assertEqual(len(self.calls),0)
    async def test_missing_reservation_is_refused(self):
        original=t.TargetTransport.handle_async_request
        async def altered(guard,request):
            for p in guard.run.glob('*-reserved.json'):p.unlink()
            return await original(guard,request)
        with patch.object(t.TargetTransport,'handle_async_request',altered):result=await self.run_launch()
        self.assertEqual(result['reason'],'RESERVATION_MISSING');self.assertEqual(len(self.calls),0)
    async def test_creation_scope_mutation_rejected_even_with_rehashed_payload(self):
        original=t.TargetTransport.handle_async_request
        async def altered(guard,request):
            if guard.forwarded==7:
                p=guard.run/'creation-requests.json';values=c.parse(p.read_bytes())
                values[0]['ordered_intents'][0]['entity_id']=c.PARENT
                values[0]['batch']['batch_payload_sha256']=b.json_sha256(values[0]['ordered_intents'])
                p.write_bytes(c.encoded(values))
            return await original(guard,request)
        with patch.object(t.TargetTransport,'handle_async_request',altered):result=await self.run_launch()
        self.assertEqual(result['reason'],'CREATION_REQUEST_CHANGED');self.assertEqual(len(self.calls),7)
    async def test_active_block_prevents_token_access(self):
        p=self.base/'block';p.write_bytes(b'block')
        self.m['restrictions'].append(dict(self.pin(p),effect='block_when_present'));self.save_manifest()
        result=await self.run_launch()
        self.assertEqual(result['reason'],'READ_RESTRICTED');self.assertEqual(self.token.reads,0)
    async def test_missing_cli_arguments_do_not_launch(self):
        with patch.object(t,'live_boundary',side_effect=AssertionError('must not launch')),redirect_stdout(io.StringIO()):
            self.assertEqual(t.main([]),2)


# Imported fixture class must not become part of this module's discovered suite.
del BootstrapTests
