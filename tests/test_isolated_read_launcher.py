"""New launcher boundary tests only. No previous suite or saved examples run."""
import asyncio
import base64
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
import isolated_read_collector as c
import isolated_read_launcher as l
from sync_contract import SERVER_CAPABILITIES


def jwt(exp=1300, sub=c.ACCOUNT):
    body = base64.urlsafe_b64encode(c.encoded(dict(sub=sub, exp=exp))).decode().rstrip('=')
    return 'e30.' + body + '.c3ludGhldGlj'


class Token:
    def __init__(self):
        self.value, self.reads = jwt(), 0
    def read(self):
        self.reads += 1
        return self.value


class Lease:
    def __init__(self):
        self.allowed, self.active, self.releases = True, False, 0
    def acquire(self):
        self.active = self.allowed
        return self.allowed
    def release(self):
        self.active = False
        self.releases += 1


class LauncherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # tests/__init__.py sets an AppData override for ordinary app tests.
        # This launcher imports no app/store and must test its own clean profile.
        env = patch.dict(l.os.environ,{name:'' for name in (
            'ANTIGRAVITY_PROFILE','ANTIGRAVITY_ROOT_DIR','ANTIGRAVITY_APP_DATA_DIR',
            'ANTIGRAVITY_SYNC_PROJECT_ID','ANTIGRAVITY_FORCE_PROJECT_ID',
            'ANTIGRAVITY_INSTANCE_KEY','ANTIGRAVITY_SYNC_OFFLINE_FILE')})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.boundary = l.Boundary(self.base/'results', self.base/'code', self.base/'refs')
        self.boundary.code_root.mkdir()
        self.boundary.reference_root.mkdir()
        self.now, self.calls, self.factories = 1000, [], 0
        self.token, self.lease, self.hook = Token(), Lease(), None
        for name in l.CODE_FILES:
            (self.boundary.code_root/name).write_bytes(('synthetic pin '+name).encode())
        self.rows = {
            'user': {'id':c.ACCOUNT},
            'get_sync_handshake': dict(project_id=c.PROJECT, project_sync_mode='ID_BASED', migration_epoch=1,
                server_protocol_version=3, supported_protocol_versions=[3],
                server_contract_sha256=c.CANONICAL_CONTRACT_SHA256,
                canonical_contract_sha256=c.CANONICAL_CONTRACT_SHA256,
                server_capabilities=list(SERVER_CAPABILITIES), contract_version=c.CONTRACT_VERSION),
            'projects':[dict(project_id=c.PROJECT,owner_id=c.ACCOUNT,name=c.PROJECT_NAME,deleted_at=None)],
            'project_sync_settings':[dict(project_id=c.PROJECT,project_sync_mode='ID_BASED',migration_epoch=1)],
            'documents':[],
            'folders':[dict(project_id=c.PROJECT,folder_id=c.PARENT,parent_folder_id=None,
                            name='합성 부모',revision=1,is_deleted=False)],
            'tree_orders':[dict(project_id=c.PROJECT,tree_order_id='30000000-0000-4000-8000-000000000001',
                                parent_folder_id=None,revision=1,children=[c.PARENT])]}
        metadata = dict(format='windows-isolated-target-metadata-proposal-v1',endpoint=c.ENDPOINT,
            account_id=c.ACCOUNT,server_project_id=c.PROJECT,complete=False,nodes=[
                dict(id=c.PARENT,kind='folder',parent_id=None,path='합성 부모',revision=1,
                    structure_revision=None,deleted=False,utf8_bytes=None,ends_lf=None,sha256=None)])
        orders = dict(format='windows-retained-orders-v1',complete=False,orders=[
            dict(id=self.rows['tree_orders'][0]['tree_order_id'],parent_id=None,revision=1,children=[c.PARENT])])
        # Deliberately noncanonical bytes: prove byte-preserving reference copies.
        self.meta_raw = json.dumps(metadata,ensure_ascii=True,indent=3).encode()+b'\r\n'
        self.order_raw = json.dumps(orders,indent=2).encode()+b'\n\n'
        (self.boundary.reference_root/'before-metadata.json').write_bytes(self.meta_raw)
        (self.boundary.reference_root/'before-orders.json').write_bytes(self.order_raw)
        self.config = self.base/'public-config.json'
        self.config.write_bytes(c.encoded(dict(supabase_url=c.ENDPOINT,
            supabase_publishable_key='sb_publishable_synthetic_public_key_0001')))
        self.gate = self.base/'read-block'
        self.scope_path = self.base/'input-scope.json'
        self.manifest_path = self.base/'manifest.json'
        self.scope = dict(format='windows-isolated-read-scope-v1',run_id=str(uuid4()),
            endpoint=c.ENDPOINT,account_id=c.ACCOUNT,project_id=c.PROJECT,max_requests=7,max_seconds=180,
            expires_at=1180,reference_sha256=dict(metadata=c.digest(self.meta_raw),orders=c.digest(self.order_raw)))
        self.scope_path.write_bytes(c.encoded(self.scope))
        self.manifest = dict(format='windows-isolated-read-launch-v1',run_id=self.scope['run_id'],
            results_root=str(self.boundary.root),scope=self.pin(self.scope_path),
            references=dict(metadata=self.pin(self.boundary.reference_root/'before-metadata.json'),
                            orders=self.pin(self.boundary.reference_root/'before-orders.json')),
            config=self.pin(self.config),source_sha256={n:c.digest((self.boundary.code_root/n).read_bytes()) for n in l.CODE_FILES},
            not_before=1000,windows_sid='S-1-5-21-123',profile='',credential_service=l.SERVICE,
            restriction_review='reviewed-for-independent-read-v1',
            restrictions=[dict(path=str(self.gate),sha256=None,effect='block_when_present')])
        self.save_manifest()

    def pin(self, path):
        return dict(path=str(path),sha256=c.digest(path.read_bytes()))

    def save_manifest(self):
        self.manifest_path.write_bytes(c.encoded(self.manifest))
        self.sha = c.digest(self.manifest_path.read_bytes())

    def refresh_scope(self):
        self.scope_path.write_bytes(c.encoded(self.scope))
        self.manifest['scope'] = self.pin(self.scope_path)
        self.save_manifest()

    def transport(self):
        self.factories += 1
        return httpx.MockTransport(self.handler)

    async def handler(self, request):
        self.assertTrue(self.lease.active)
        self.calls.append(request)
        if self.hook:
            answer = await self.hook(request)
            if answer is not None:
                return answer
        value = self.rows[request.url.path.rsplit('/',1)[-1]]
        headers = {'content-range': f'0-{len(value)-1}/{len(value)}' if value else '*/0'} if isinstance(value,list) else {}
        return httpx.Response(200,content=c.encoded(value),headers=headers)

    async def run_launch(self):
        return await l.launch(self.manifest_path,self.sha,boundary=self.boundary,token_source=self.token,
            lease_factory=lambda sid:self.lease,transport_factory=self.transport,clock=lambda:self.now)

    def run_dir(self):
        return self.boundary.root/'runs'/self.scope['run_id']

    def attempt_dir(self):
        return self.boundary.root/'attempts'/self.scope['run_id']

    async def test_full_launch_preserves_exact_reference_and_reserves_seven(self):
        result = await self.run_launch()
        self.assertEqual((result['status'],result['http_reserved'],len(self.calls)),('observed',7,7))
        self.assertFalse(result['complete'])
        self.assertEqual((self.attempt_dir()/'reference/before-metadata.json').read_bytes(),self.meta_raw)
        self.assertEqual((self.attempt_dir()/'reference/before-orders.json').read_bytes(),self.order_raw)
        self.assertEqual((self.attempt_dir()/'scope-input.json').read_bytes(),self.scope_path.read_bytes())
        self.assertEqual(self.lease.releases,1)
        for path in self.boundary.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(self.token.value.encode(),path.read_bytes())
                self.assertNotIn(b'sb_publishable_synthetic_public_key_0001',path.read_bytes())

    async def test_missing_token_claims_once_and_never_constructs_transport(self):
        self.token.value = ''
        result = await self.run_launch()
        self.assertEqual((result['reason'],result['http_reserved']),('TOKEN_MISSING_OR_INVALID',0))
        self.assertEqual(self.factories,0)
        before = (self.attempt_dir()/'launcher-journal.jsonl').read_bytes()
        with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'):
            await self.run_launch()
        self.assertEqual((self.attempt_dir()/'launcher-journal.jsonl').read_bytes(),before)

    async def test_token_expiry_and_subject_checks_are_local_only(self):
        for token in ('bad',jwt(exp=1209),jwt(sub=str(uuid4()))):
            with self.subTest(token_kind=token.split('.')[0]):
                with self.assertRaises(c.ReadStopped):
                    l.token_check(token,1180)
        l.token_check(jwt(exp=1210),1180)
        self.assertEqual(self.calls,[])

    async def test_busy_lease_never_reads_token(self):
        self.lease.allowed = False
        result = await self.run_launch()
        self.assertEqual(result['reason'],'CREDENTIAL_LEASE_REFUSED')
        self.assertEqual((self.token.reads,self.factories,self.lease.releases),(0,0,0))

    async def test_token_change_after_q1_consumes_q2_reservation_not_network(self):
        async def hook(request):
            self.token.value = jwt(exp=1400)
        self.hook = hook
        result = await self.run_launch()
        self.assertEqual((result['reason'],result['http_reserved'],result['forwarded']),('TOKEN_CHANGED',2,1))
        self.assertFalse((self.run_dir()/'Q2.body').exists())

    async def test_expiry_after_q1_does_not_continue(self):
        async def hook(request):
            self.now = 1180
        self.hook = hook
        result = await self.run_launch()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(result['http_reserved'],1)
        self.assertEqual(result['status'],'stopped')

    async def test_early_start_claims_and_stops_before_credentials(self):
        self.now = 999
        result = await self.run_launch()
        self.assertEqual(result['reason'],'OUTSIDE_EXECUTION_WINDOW')
        self.assertEqual(self.token.reads,0)
        self.assertTrue(self.attempt_dir().exists())

    async def test_manifest_change_after_q1_stops_with_reserved_two(self):
        async def hook(request):
            self.manifest_path.write_bytes(self.manifest_path.read_bytes()+b' ')
        self.hook = hook
        result = await self.run_launch()
        self.assertEqual((result['reason'],result['http_reserved'],len(self.calls)),('MANIFEST_CHANGED',2,1))

    async def test_pin_change_after_q1_stops_for_reference_config_and_code(self):
        for path in (self.boundary.reference_root/'before-orders.json',self.config,
                     self.boundary.code_root/'isolated_read_collector.py'):
            with self.subTest(kind=path.name):
                raw = path.read_bytes()
                self.scope['run_id']=str(uuid4())
                self.manifest['run_id']=self.scope['run_id']
                self.refresh_scope()
                self.calls=[]
                async def hook(request):
                    path.write_bytes(raw+b' ')
                self.hook=hook
                try:
                    result=await self.run_launch()
                    self.assertEqual((result['reason'],result['http_reserved'],len(self.calls)),('PIN_CHANGED',2,1))
                finally:
                    path.write_bytes(raw)

    async def test_restriction_appearance_is_not_removed(self):
        async def hook(request):
            self.gate.write_bytes(b'blocked')
        self.hook=hook
        result=await self.run_launch()
        self.assertEqual((result['reason'],result['forwarded']),('RESTRICTION_CHANGED',1))
        self.assertEqual(self.gate.read_bytes(),b'blocked')

    async def test_existing_read_block_stops_without_credential_read(self):
        self.gate.write_bytes(b'blocked')
        self.manifest['restrictions'][0]['sha256']=c.digest(b'blocked')
        self.save_manifest()
        result=await self.run_launch()
        self.assertEqual((result['reason'],self.token.reads),('READ_RESTRICTED',0))

    async def test_manifest_hash_root_and_old_run_rejected_before_token(self):
        originals=deepcopy(self.manifest)
        for key,value in [('results_root',str(self.base/'other')),('run_id',c.ENDED_RUN),
                          ('restriction_review','pending'),('restrictions',[])]:
            with self.subTest(field=key):
                self.manifest=deepcopy(originals)
                self.manifest[key]=value
                self.save_manifest()
                with self.assertRaises(c.ReadStopped): await self.run_launch()
        self.manifest=originals
        self.save_manifest()
        self.sha='0'*64
        with self.assertRaisesRegex(c.ReadStopped,'MANIFEST_HASH_CHANGED'): await self.run_launch()
        self.assertEqual(self.token.reads,0)

    async def test_preexisting_partial_run_is_not_touched(self):
        self.run_dir().mkdir(parents=True)
        sentinel=self.run_dir()/'journal.jsonl'
        sentinel.write_bytes(b'{partial')
        with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'): await self.run_launch()
        self.assertEqual(sentinel.read_bytes(),b'{partial')
        self.assertFalse(self.attempt_dir().exists())

    async def test_concurrent_same_run_only_one_reaches_transport(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def hook(request):
            entered.set()
            await release.wait()
        self.hook=hook
        first=asyncio.create_task(self.run_launch())
        await asyncio.wait_for(entered.wait(),3)
        try:
            with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'): await self.run_launch()
        finally:
            release.set()
        result=await first
        self.assertEqual((result['status'],self.factories),('observed',1))

    async def test_cancel_keeps_claim_and_partial_collector_record(self):
        entered=asyncio.Event()
        async def hook(request):
            entered.set()
            await asyncio.Event().wait()
        self.hook=hook
        task=asyncio.create_task(self.run_launch())
        await asyncio.wait_for(entered.wait(),3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.lease.releases,1)
        self.assertTrue(self.attempt_dir().exists())
        with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'): await self.run_launch()
        last=c.parse((self.attempt_dir()/'launcher-journal.jsonl').read_bytes().splitlines()[-1])
        self.assertIsNone(last['data']['http_reserved'])

    async def test_redirect_and_network_failure_are_not_retried(self):
        async def hook(request):
            return httpx.Response(302,headers={'location':'https://example.invalid/'},content=b'{}')
        self.hook=hook
        result=await self.run_launch()
        self.assertEqual((result['reason'],len(self.calls)),('HTTP_STATUS_REFUSED',1))
        self.scope['run_id']=str(uuid4());self.manifest['run_id']=self.scope['run_id'];self.refresh_scope()
        self.calls=[]
        async def failure(request):
            raise httpx.ConnectError('synthetic '+self.token.value)
        self.hook=failure
        result=await self.run_launch()
        self.assertEqual((result['http_reserved'],len(self.calls)),(1,1))
        self.assertNotIn(self.token.value,json.dumps(result))

    async def test_reference_copy_failure_stops_before_credentials(self):
        original=c.new_file
        def denied(path,raw):
            if path.name=='before-orders.json': raise OSError('synthetic disk')
            return original(path,raw)
        with patch.object(c,'new_file',denied): result=await self.run_launch()
        self.assertEqual((result['reason'],self.token.reads),('LAUNCH_IO_OR_VALIDATION_FAILED',0))
        self.assertTrue((self.attempt_dir()/'reference/before-metadata.json').exists())
        with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'): await self.run_launch()

    async def test_wrong_staging_config_stops_before_token(self):
        self.config.write_bytes(c.encoded(dict(supabase_url='https://wrong.supabase.co',
            supabase_publishable_key='sb_publishable_synthetic_public_key_0001')))
        self.manifest['config']=self.pin(self.config);self.save_manifest()
        result=await self.run_launch()
        self.assertEqual((result['reason'],self.token.reads),('STAGING_CONFIG_REQUIRED',0))

    async def test_guard_rejects_wrong_request_headers_body_and_extra_send(self):
        token,key='test-token','test-key'
        async def accepted(request): return httpx.Response(200,content=b'{}')
        async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token,'apikey':key,
                                             'Accept-Encoding':'identity'},transport=httpx.MockTransport(accepted)) as client:
            for change in ('url','method','body','header'):
                guard=l.GuardedTransport(httpx.MockTransport(accepted),lambda:None,token,key)
                request=client.build_request('GET',c.ENDPOINT+'/auth/v1/user')
                if change=='url': request.url=httpx.URL(c.ENDPOINT+'/auth/v1/token')
                if change=='method': request.method='POST'
                if change=='body': request=client.build_request('GET',c.ENDPOINT+'/auth/v1/user',content=b'bad')
                if change=='header': request.headers['Cookie']='bad'
                with self.assertRaises(c.ReadStopped): await guard.handle_async_request(request)
                self.assertEqual(guard.forwarded,0)
            guard=l.GuardedTransport(httpx.MockTransport(accepted),lambda:None,token,key)
            guard.forwarded=7
            with self.assertRaisesRegex(c.ReadStopped,'EXTRA_REQUEST_REFUSED'):
                await guard.handle_async_request(client.build_request('GET',c.ENDPOINT+'/auth/v1/user'))

    async def test_live_factories_only_use_access_compound_and_explicit_http_settings(self):
        from keyring.backends.Windows import WinVaultKeyring
        class Credential(dict):
            value='fake-access-only'
        credential=Credential(UserName=l.ITEM)
        with patch.object(WinVaultKeyring,'_read_credential',return_value=credential) as read:
            self.assertEqual(l.WindowsAccessToken().read(),'fake-access-only')
            read.assert_called_once_with(l.ITEM+'@'+l.SERVICE)
        with patch.object(httpx,'AsyncHTTPTransport') as factory:
            l.live_transport()
            kw=factory.call_args.kwargs
            self.assertEqual((kw['verify'],kw['trust_env'],kw['retries'],kw['proxy']),(True,False,0,None))

    async def test_default_cli_and_help_never_construct_live_dependencies(self):
        with (patch.object(l,'WindowsAccessToken',side_effect=AssertionError('credentials')),
              patch.object(l,'live_transport',side_effect=AssertionError('network'))):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(l.main([]),2)
                with self.assertRaises(SystemExit) as exited: l.main(['--help'])
                self.assertEqual(exited.exception.code,0)

    async def test_environment_override_blocks_before_any_io_dependencies(self):
        with patch.dict(l.os.environ,{'ANTIGRAVITY_PROFILE':'other'}):
            with self.assertRaisesRegex(c.ReadStopped,'RUNTIME_OVERRIDE_REFUSED'): await self.run_launch()
        self.assertEqual((self.token.reads,self.factories),(0,0))

    async def test_import_has_no_live_side_effects(self):
        import importlib
        importlib.reload(l)
        self.assertEqual((self.token.reads,self.factories),(0,0))

    async def test_windows_lease_uses_shared_name_and_refuses_busy_without_release(self):
        import ctypes
        from unittest.mock import MagicMock
        import runtime_profile
        api=MagicMock()
        api.CreateMutexW.return_value=1234
        with (patch.object(runtime_profile,'user_sid',return_value='S-1-5-21-123'),
              patch.object(runtime_profile,'profile_name',return_value=''),
              patch.object(runtime_profile,'credential_lease_name',return_value='Global\\synthetic-lease'),
              patch.object(ctypes,'WinDLL',return_value=api),
              patch.object(ctypes,'get_last_error',return_value=183)):
            lease=l.WindowsLease('S-1-5-21-123')
            self.assertFalse(lease.acquire())
            api.CloseHandle.assert_called_once_with(1234)
            api.ReleaseMutex.assert_not_called()
            api.CreateMutexW.assert_called_once_with(None,True,'Global\\synthetic-lease')
        api.reset_mock()
        with (patch.object(runtime_profile,'user_sid',return_value='S-1-5-21-123'),
              patch.object(runtime_profile,'profile_name',return_value=''),
              patch.object(runtime_profile,'credential_lease_name',return_value='Global\\synthetic-lease'),
              patch.object(ctypes,'WinDLL',return_value=api),
              patch.object(ctypes,'get_last_error',return_value=0)):
            lease=l.WindowsLease('S-1-5-21-123')
            self.assertTrue(lease.acquire())
            lease.release()
            api.ReleaseMutex.assert_called_once_with(1234)
            api.CloseHandle.assert_called_once_with(1234)
