"""New auth-only impact tests. Synthetic sessions and an in-memory WinVault fake."""
import asyncio
import base64
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
import isolated_auth_refresh as a

c, l = a.c, a.l


def jwt(exp=5000, sub=c.ACCOUNT, issuer=c.ENDPOINT + '/auth/v1'):
    payload = base64.urlsafe_b64encode(c.encoded(dict(sub=sub, iss=issuer, exp=exp))).decode().rstrip('=')
    return 'e30.' + payload + '.c3ludGhldGlj'


class Credential(dict):
    def __init__(self, username, value):
        super().__init__(UserName=username)
        self.value = value


class Vault:
    def __init__(self):
        self.data = {l.ITEM + '@' + l.SERVICE: Credential(l.ITEM, jwt(exp=1)),
                     l.SERVICE: Credential(a.REFRESH, 'synthetic-old-refresh'),
                     'unrelated': Credential('unrelated', 'synthetic-unrelated'),
                     a.REFRESH + '@' + l.SERVICE: Credential(a.REFRESH, 'synthetic-legacy-copy')}
        self.reads, self.writes, self.fail_write, self.ignore_write = [], [], None, None
        self.on_read, self.on_write = None, None

    def _read_credential(self, target):
        self.reads.append(target)
        if self.on_read:
            self.on_read(target)
        return self.data.get(target)

    def _set_password(self, target, username, value):
        self.writes.append(target)
        if len(self.writes) == self.fail_write:
            raise OSError('SECRET_EXAMPLE_NEVER_LOG')
        if len(self.writes) != self.ignore_write:
            self.data[target] = Credential(username, value)
        if self.on_write:
            self.on_write(target)


class Lease:
    def __init__(self):
        self.allowed, self.held, self.released = True, False, 0
    def acquire(self):
        self.held = self.allowed
        return self.allowed
    def release(self):
        self.held = False
        self.released += 1


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(a.os.environ, {name: '' for name in (
            'ANTIGRAVITY_PROFILE', 'ANTIGRAVITY_ROOT_DIR', 'ANTIGRAVITY_APP_DATA_DIR',
            'ANTIGRAVITY_SYNC_PROJECT_ID', 'ANTIGRAVITY_FORCE_PROJECT_ID',
            'ANTIGRAVITY_INSTANCE_KEY', 'ANTIGRAVITY_SYNC_OFFLINE_FILE')})
        env.start()
        self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.boundary = a.Boundary(self.base / 'auth-only', self.base / 'code')
        self.boundary.code_root.mkdir()
        for name in a.CODE_FILES:
            (self.boundary.code_root / name).write_bytes(('synthetic-source-' + name).encode())
        self.config = self.base / 'public-config.json'
        self.config.write_bytes(c.encoded(dict(supabase_url=c.ENDPOINT,
            supabase_publishable_key='sb_publishable_synthetic_public_key_0001')))
        self.hold = self.base / 'existing-hold'
        self.hold.write_bytes(b'preserved synthetic hold')
        self.old_run = self.base / 'ended-run.json'
        self.old_run.write_bytes(c.encoded(dict(used=228, expires_at=1, ended=True)))
        self.path = self.base / 'manifest.json'
        self.now, self.mono, self.calls, self.factories = 1000, 0, [], 0
        self.vault, self.lease = Vault(), Lease()
        self.pair = a.WindowsPair(lambda: self.vault)
        self.hook, self.status, self.raw, self.headers = None, 200, None, {}
        self.payload = dict(access_token=jwt(), refresh_token='synthetic-new-refresh',
            token_type='bearer', expires_at=5000,
            user=dict(id=c.ACCOUNT, email='synthetic-private-email@example.invalid'))
        self.m = dict(format='windows-isolated-auth-refresh-v1', auth_id=str(uuid4()),
            endpoint=c.ENDPOINT, account_id=c.ACCOUNT, windows_sid=a.SID, profile='',
            credential_service=l.SERVICE, results_root=str(self.boundary.root),
            not_before=990, approve_until=1200, max_requests=1, max_auth_requests=1,
            http_timeout_seconds=15, http_deadline_seconds=30, config=self.pin(self.config),
            source_sha256={n: c.digest((self.boundary.code_root / n).read_bytes()) for n in a.CODE_FILES},
            restriction_review='reviewed-for-independent-auth-refresh-v1',
            restrictions=[dict(**self.pin(p), effect='preserve_only') for p in (self.hold, self.old_run)])

    def pin(self, path):
        return dict(path=str(path), sha256=c.digest(path.read_bytes()))

    @property
    def attempt(self):
        return self.boundary.root / self.m['auth_id']

    def transport(self):
        self.factories += 1
        async def handler(request):
            self.assertTrue(self.lease.held)
            self.assertEqual(str(request.url), a.URL)
            self.assertEqual(c.parse(request.content), dict(refresh_token='synthetic-old-refresh'))
            self.assertNotIn('authorization', request.headers)
            self.assertEqual(request.extensions['timeout'], dict(connect=15, read=15, write=15, pool=15))
            # The durable reservation must already exist before entering transport.
            self.assertEqual(c.parse((self.attempt / '002-reserved.json').read_bytes())['data'],
                             dict(http_reserved=1, auth_reserved=1))
            self.calls.append(request.method)
            if self.hook:
                await self.hook()
            return httpx.Response(self.status, headers=self.headers,
                stream=httpx.ByteStream(self.raw if self.raw is not None else c.encoded(self.payload)))
        return httpx.MockTransport(handler)

    async def run_auth(self, *, reuse=False):
        if not reuse:
            self.path.write_bytes(c.encoded(self.m))
        return await a.launch(self.path, c.digest(self.path.read_bytes()), boundary=self.boundary,
            credentials=self.pair, lease_factory=lambda sid: self.lease,
            transport_factory=self.transport, clock=lambda: self.now, monotonic=lambda: self.mono)

    def no_secrets(self):
        combined = b''.join(p.read_bytes() for p in self.boundary.root.rglob('*') if p.is_file())
        for value in ('synthetic-old-refresh', 'synthetic-new-refresh', jwt(), jwt(exp=1),
                      'SECRET_EXAMPLE_NEVER_LOG', 'synthetic-private-email', 'sb_publishable_'):
            self.assertNotIn(value.encode(), combined)

    async def test_success_expired_old_access_exact_pair_only_and_redacted_audit(self):
        result = await self.run_auth()
        self.assertEqual(result['status'], 'refreshed')
        self.assertEqual(result['http_reserved'], 1)
        self.assertFalse(result['session_uncertain'])
        self.assertEqual(self.calls, ['POST'])
        self.assertEqual(self.vault.writes, [l.SERVICE, l.ITEM + '@' + l.SERVICE])
        self.assertEqual(set(self.vault.reads), {l.SERVICE, l.ITEM + '@' + l.SERVICE})
        self.assertEqual(self.vault.data['unrelated'].value, 'synthetic-unrelated')
        self.assertEqual(self.vault.data[a.REFRESH + '@' + l.SERVICE].value, 'synthetic-legacy-copy')
        self.assertEqual(c.parse(self.old_run.read_bytes())['used'], 228)
        self.assertEqual(self.lease.released, 1)
        self.no_secrets()

    async def test_same_auth_id_cannot_replay_success(self):
        await self.run_auth()
        with self.assertRaisesRegex(a.Stop, 'AUTH_ID_ALREADY_USED'):
            await self.run_auth(reuse=True)
        self.assertEqual(len(self.calls), 1)

    async def test_failed_preflight_claim_cannot_be_reused(self):
        self.lease.allowed = False
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'CREDENTIAL_LEASE_REFUSED')
        self.assertEqual(self.vault.reads, [])
        self.lease.allowed = True
        with self.assertRaisesRegex(a.Stop, 'AUTH_ID_ALREADY_USED'):
            await self.run_auth(reuse=True)

    async def test_ended_ids_rejected_before_claim(self):
        for auth_id in a.ENDED_IDS:
            self.m['auth_id'] = auth_id
            with self.assertRaisesRegex(a.Stop, 'ENDED_ID_REFUSED'):
                await self.run_auth()
        self.assertFalse(self.boundary.root.exists())
        self.assertEqual(self.vault.reads, [])

    async def test_sid_profile_endpoint_account_limits_scope_rejected(self):
        for key, bad in [('windows_sid', a.SID[:-4] + '1005'), ('profile', 'candidate'),
                ('endpoint', 'https://different.invalid'), ('account_id', str(uuid4())),
                ('max_requests', 2), ('max_auth_requests', True), ('http_timeout_seconds', 16),
                ('http_deadline_seconds', 31), ('results_root', str(self.base / 'old-run'))]:
            good = self.m[key]
            self.m[key] = bad
            with self.assertRaises((a.Stop, c.ReadStopped)):
                await self.run_auth()
            self.m[key] = good
        self.assertEqual(self.vault.reads, [])

    async def test_unknown_manifest_fields_are_not_copied_or_logged(self):
        self.m['access_token'] = 'SECRET_EXAMPLE_NEVER_LOG'
        with self.assertRaisesRegex(a.Stop, 'MANIFEST_FORMAT'):
            await self.run_auth()
        self.assertFalse(self.boundary.root.exists())

    async def test_manifest_hash_mismatch_has_no_credential_io(self):
        self.path.write_bytes(c.encoded(self.m))
        with self.assertRaises(c.ReadStopped):
            await a.launch(self.path, '0' * 64, boundary=self.boundary, credentials=self.pair,
                lease_factory=lambda _: self.lease, transport_factory=self.transport)
        self.assertEqual(self.vault.reads, [])

    async def test_runtime_override_refused(self):
        with patch.dict(a.os.environ, {'ANTIGRAVITY_PROFILE': 'candidate'}):
            with self.assertRaises(c.ReadStopped):
                await self.run_auth()
        self.assertEqual(self.vault.reads, [])

    async def test_expired_window_claim_stops_without_credentials(self):
        self.now = 1200
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'OUTSIDE_APPROVAL_WINDOW')
        self.assertEqual(self.vault.reads, [])

    async def test_source_pin_changed_stops_before_credentials(self):
        (self.boundary.code_root / a.CODE_FILES[0]).write_bytes(b'changed')
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'PIN_PATH_OR_RESTRICTION_FAILED')
        self.assertEqual(self.vault.reads, [])

    async def test_present_block_prevents_credentials_preserve_only_does_not(self):
        self.m['restrictions'][0]['effect'] = 'block_when_present'
        result = await self.run_auth()
        self.assertEqual(result['http_reserved'], 0)
        self.assertEqual(self.vault.reads, [])

    async def test_missing_or_generic_other_user_layout_has_no_fallback(self):
        self.vault.data[l.SERVICE] = Credential('OtherAccountItem', 'synthetic-unrelated')
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'CREDENTIAL_LAYOUT')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.vault.writes, [])

    async def test_missing_refresh_rejected_before_http(self):
        self.vault.data[l.SERVICE].value = ''
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'REFRESH_FORMAT')
        self.assertEqual(self.factories, 0)

    async def test_old_account_mismatch_rejected_before_http(self):
        self.vault.data[l.ITEM + '@' + l.SERVICE].value = jwt(sub=str(uuid4()))
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'ACCOUNT_BINDING')
        self.assertEqual(self.calls, [])

    async def test_pair_changed_before_dispatch_consumes_no_http(self):
        def mutate(target):
            if len(self.vault.reads) == 3:
                self.vault.data[l.SERVICE].value = 'changed-by-other-process'
        self.vault.on_read = mutate
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'SESSION_CHANGED')
        self.assertEqual(result['http_reserved'], 0)
        self.assertEqual(self.calls, [])

    async def test_reservation_disk_failure_blocks_dispatch_and_is_not_repaired(self):
        original = c.new_file
        def fail(path, raw):
            if 'reserved' in path.name:
                original(path, raw)  # Persisted bytes, then an uncertain durability error.
                raise OSError('SECRET_EXAMPLE_NEVER_LOG')
            original(path, raw)
        with patch.object(c, 'new_file', fail):
            result = await self.run_auth()
        self.assertEqual(result['http_reserved'], 1)
        self.assertFalse(result['session_uncertain'])
        self.assertEqual(self.calls, [])
        self.assertTrue((self.attempt / '002-reserved.json').exists())
        with self.assertRaisesRegex(a.Stop, 'AUTH_ID_ALREADY_USED'):
            await self.run_auth(reuse=True)
        self.no_secrets()

    async def test_401_no_retry_or_password_fallback(self):
        self.status = 401
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'AUTH_HTTP_REJECTED')
        self.assertTrue(result['session_uncertain'])
        self.assertEqual(self.calls, ['POST'])
        self.assertEqual(self.vault.writes, [])

    async def test_redirect_never_followed(self):
        self.status, self.headers = 307, {'location': 'https://different.invalid/secret'}
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'AUTH_HTTP_REJECTED')
        self.assertEqual(self.calls, ['POST'])

    async def test_lost_response_preserves_reservation_and_uncertainty(self):
        async def fail():
            raise httpx.ReadError('SECRET_EXAMPLE_NEVER_LOG')
        self.hook = fail
        result = await self.run_auth()
        self.assertTrue(result['session_uncertain'])
        self.assertEqual(result['http_reserved'], 1)
        self.assertEqual(self.vault.writes, [])
        self.no_secrets()

    async def test_http_timeout_no_retry(self):
        async def fail():
            raise httpx.ReadTimeout('SECRET_EXAMPLE_NEVER_LOG')
        self.hook = fail
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'HTTP_TIMEOUT')
        self.assertTrue(result['session_uncertain'])
        self.assertEqual(self.calls, ['POST'])

    async def test_cancellation_records_uncertainty_releases_lease(self):
        async def cancel():
            raise asyncio.CancelledError()
        self.hook = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.run_auth()
        terminal = c.parse(next(self.attempt.glob('*terminal.json')).read_bytes())['data']
        self.assertEqual(terminal['reason'], 'CANCELLED')
        self.assertTrue(terminal['session_uncertain'])
        self.assertEqual(self.lease.released, 1)

    async def test_monotonic_http_deadline_checked_before_save(self):
        async def delayed():
            self.mono = 30
        self.hook = delayed
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'HTTP_DEADLINE')
        self.assertEqual(self.vault.writes, [])

    async def test_expiry_or_clock_rollback_after_response_no_save(self):
        async def rollback():
            self.now = 999
        self.hook = rollback
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'CLOCK_ROLLBACK')
        self.assertEqual(self.vault.writes, [])

    async def test_malformed_duplicate_response_never_saved(self):
        self.raw = b'{"access_token":"SECRET_EXAMPLE_NEVER_LOG","access_token":"duplicate"}'
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'AUTH_RESPONSE_INVALID')
        self.assertEqual(self.vault.writes, [])
        self.no_secrets()

    async def test_response_size_bound(self):
        self.raw = b'x' * (a.MAX_BODY + 1)
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'RESPONSE_TOO_LARGE')
        self.assertEqual(self.vault.writes, [])

    async def test_response_user_mismatch_no_save(self):
        self.payload['user']['id'] = str(uuid4())
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'AUTH_RESPONSE_BINDING')
        self.assertEqual(self.vault.writes, [])

    async def test_response_access_wrong_issuer_no_save(self):
        self.payload['access_token'] = jwt(issuer='https://different.invalid/auth/v1')
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'ACCOUNT_BINDING')
        self.assertEqual(self.vault.writes, [])

    async def test_response_access_insufficient_expiry_no_save(self):
        self.payload['access_token'] = jwt(exp=1059)
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'ACCESS_EXPIRY')
        self.assertEqual(self.vault.writes, [])

    async def test_session_changed_during_http_never_overwrites(self):
        async def mutate():
            self.vault.data[l.SERVICE].value = 'other-process-refresh'
        self.hook = mutate
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'SESSION_CHANGED')
        self.assertEqual(self.vault.writes, [])
        self.assertEqual(self.vault.data[l.SERVICE].value, 'other-process-refresh')

    async def test_hold_changed_during_http_stops_save_without_restoring_hold(self):
        async def mutate():
            self.hold.write_bytes(b'synthetic external change')
        self.hook = mutate
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'PIN_PATH_OR_RESTRICTION_FAILED')
        self.assertEqual(self.vault.writes, [])
        self.assertEqual(self.hold.read_bytes(), b'synthetic external change')

    async def test_first_write_failure_uncertain_no_rollback(self):
        self.vault.fail_write = 1
        result = await self.run_auth()
        self.assertTrue(result['session_uncertain'])
        self.assertFalse(result['session_saved_and_readback'])
        self.assertEqual(self.vault.writes, [l.SERVICE])
        self.no_secrets()

    async def test_second_write_failure_keeps_rotated_refresh_no_rollback(self):
        self.vault.fail_write = 2
        result = await self.run_auth()
        self.assertTrue(result['session_uncertain'])
        self.assertEqual(len(self.vault.writes), 2)
        self.assertEqual(self.vault.data[l.SERVICE].value, 'synthetic-new-refresh')
        self.assertEqual(self.vault.data[l.ITEM + '@' + l.SERVICE].value, jwt(exp=1))
        self.no_secrets()

    async def test_readback_mismatch_never_declared_success(self):
        self.vault.ignore_write = 2
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'READBACK_FAILED')
        self.assertTrue(result['session_uncertain'])
        self.assertEqual(len(self.vault.writes), 2)

    async def test_other_writer_after_refresh_stops_access_write(self):
        def mutate(target):
            if target == l.SERVICE:
                self.vault.data[l.ITEM + '@' + l.SERVICE].value = 'other-process-access'
        self.vault.on_write = mutate
        result = await self.run_auth()
        self.assertEqual(result['reason'], 'PARTIAL_SAVE_OR_RACE')
        self.assertEqual(self.vault.writes, [l.SERVICE])

    async def test_save_intent_audit_failure_prevents_credential_write(self):
        original = c.new_file
        def fail(path, raw):
            if 'saving-refresh' in path.name:
                raise OSError('SECRET_EXAMPLE_NEVER_LOG')
            original(path, raw)
        with patch.object(c, 'new_file', fail):
            result = await self.run_auth()
        self.assertTrue(result['session_uncertain'])
        self.assertEqual(self.vault.writes, [])

    async def test_terminal_audit_failure_keeps_claim_and_does_not_retry(self):
        original = c.new_file
        def fail(path, raw):
            if 'terminal' in path.name:
                raise OSError('SECRET_EXAMPLE_NEVER_LOG')
            original(path, raw)
        with patch.object(c, 'new_file', fail):
            with self.assertRaises(OSError):
                await self.run_auth()
        self.assertTrue(self.attempt.exists())
        with self.assertRaisesRegex(a.Stop, 'AUTH_ID_ALREADY_USED'):
            await self.run_auth(reuse=True)
        self.assertEqual(self.calls, ['POST'])

    async def test_transport_guard_rejects_second_or_different_request(self):
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx.Response(200, stream=httpx.ByteStream(b'{}'))
        body = c.encoded(dict(refresh_token='synthetic-old-refresh'))
        guard = a.OneRequest(httpx.MockTransport(handler), body, 'synthetic-key', lambda: None)
        async with httpx.AsyncClient(transport=guard, headers={'accept-encoding': 'identity'}) as client:
            await client.post(a.URL, content=body, headers={'apikey': 'synthetic-key', 'content-type': 'application/json'})
            with self.assertRaisesRegex(a.Stop, 'EXTRA_OR_CHANGED_REQUEST'):
                await client.post(a.URL, content=body)
        self.assertEqual(len(calls), 1)
        guard = a.OneRequest(httpx.MockTransport(handler), body, 'synthetic-key', lambda: None)
        with self.assertRaisesRegex(a.Stop, 'EXTRA_OR_CHANGED_REQUEST'):
            await guard.handle_async_request(httpx.Request('GET', c.ENDPOINT + '/auth/v1/user'))
        self.assertEqual(len(calls), 1)

    async def test_import_help_and_no_arguments_do_not_touch_live_adapters(self):
        self.assertIsNone(a.WindowsPair().backend)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(a.main([]), 2)
            with self.assertRaises(SystemExit) as raised:
                a.main(['--help'])
            self.assertEqual(raised.exception.code, 0)
        self.assertEqual(self.vault.reads, [])
