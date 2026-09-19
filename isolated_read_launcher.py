"""One-shot read launcher. Import/help never access credentials or send HTTP.

Live entry requires an explicitly reviewed manifest and its external byte hash.
No approval generation, refresh, application store, install, gate edit or resume.
"""
import argparse
import asyncio
import base64
from dataclasses import dataclass
import hmac
import json
import math
import os
from pathlib import Path
import re
import time

import httpx
import isolated_read_collector as c
from cloud_config import validate_cloud_client_config

WORKSPACE = Path(r'D:\안티그래비티\scratch\작가님 힘내세요')
SERVICE = 'Antigravity_WebNovelApp'
ITEM = 'SupabaseAccessToken'
CODE_FILES = ('isolated_read_launcher.py', 'isolated_read_collector.py',
              'sync_contract.py', 'storage_name_tables.py', 'unicode15_casefold.py',
              'cloud_config.py', 'runtime_profile.py')
OLD_EXAMPLES = {'cc5501fd-466e-4a61-b9ee-812f1a1a7192',
                '68100a7a-cf6a-4e53-bbd9-0d9e33a7de26', c.ENDED_RUN}


@dataclass(frozen=True)
class Boundary:
    """Explicit injected file boundary for offline tests; CLI fixes these paths."""
    root: Path
    code_root: Path
    reference_root: Path


def live_boundary():
    c.need(Path(__file__).resolve().parent == WORKSPACE, 'LAUNCHER_LOCATION_CHANGED')
    return Boundary(WORKSPACE / '_evidence/windows-independent-live-read', WORKSPACE,
                    WORKSPACE / 'output/windows-ipad-isolated-target-reply-20260913')


def absolute(value):
    c.need(isinstance(value, str) and Path(value).is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
    return c.safe(value)


def hash_string(value):
    c.need(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'INVALID_HASH')
    return value


def read_pin(pin):
    c.need(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}, 'INVALID_PIN')
    path = absolute(pin['path'])
    expected = hash_string(pin['sha256'])
    raw = path.read_bytes()
    c.need(c.digest(raw) == expected, 'PIN_CHANGED')
    return raw


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def token_check(token, expires_at):
    """Local rejection only, NOT signature/remote-session verification."""
    c.need(isinstance(token, str) and 0 < len(token) <= 32768, 'TOKEN_MISSING_OR_INVALID')
    try:
        parts = token.split('.')
        c.need(len(parts) == 3 and all(re.fullmatch('[A-Za-z0-9_-]+', p) for p in parts),
               'TOKEN_FORMAT')
        payload = parts[1]
        value = c.parse(base64.b64decode(payload + '=' * (-len(payload) % 4),
                                       altchars=b'-_', validate=True))
        c.need(isinstance(value, dict) and value.get('sub') == c.ACCOUNT, 'TOKEN_SUBJECT')
        c.need(finite(value.get('exp')) and value['exp'] >= expires_at + 30, 'TOKEN_EXPIRY')
    except c.ReadStopped:
        raise
    except Exception:
        raise c.ReadStopped('TOKEN_FORMAT') from None


class WindowsAccessToken:
    def read(self):
        # WinVault get_password first reads the generic service entry, which
        # may contain the REFRESH token. Read the access-only compound target.
        # Missing legacy generic-only access is a stop, never a fallback.
        from keyring.backends.Windows import WinVaultKeyring
        credential = WinVaultKeyring()._read_credential(ITEM + '@' + SERVICE)
        if credential is None:
            return ''
        c.need(credential.get('UserName') == ITEM, 'CREDENTIAL_ENTRY_CHANGED')
        return credential.value


class WindowsLease:
    def __init__(self, expected_sid):
        self.sid, self.handle = expected_sid, None

    def acquire(self):
        import ctypes
        from ctypes import wintypes
        from runtime_profile import user_sid, profile_name, credential_lease_name
        c.need(profile_name() == '' and user_sid() == self.sid, 'WINDOWS_IDENTITY_CHANGED')
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        api.CreateMutexW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.CreateMutexW(None, True, credential_lease_name())
        error = ctypes.get_last_error()
        if not handle:
            return False
        if error == 183:
            api.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is not None:
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.ReleaseMutex.argtypes = [wintypes.HANDLE]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.ReleaseMutex(self.handle)
            api.CloseHandle(self.handle)
            self.handle = None


def environment_check():
    c.need(not any(os.environ.get(k, '').strip() for k in (
        'ANTIGRAVITY_PROFILE', 'ANTIGRAVITY_ROOT_DIR', 'ANTIGRAVITY_APP_DATA_DIR',
        'ANTIGRAVITY_SYNC_PROJECT_ID', 'ANTIGRAVITY_FORCE_PROJECT_ID',
        'ANTIGRAVITY_INSTANCE_KEY', 'ANTIGRAVITY_SYNC_OFFLINE_FILE')), 'RUNTIME_OVERRIDE_REFUSED')


def read_manifest(path, expected_sha, boundary):
    raw = absolute(str(path)).read_bytes()
    c.need(c.digest(raw) == hash_string(expected_sha), 'MANIFEST_HASH_CHANGED')
    m = c.parse(raw)
    c.need(isinstance(m, dict) and set(m) == {
        'format', 'run_id', 'results_root', 'scope', 'references', 'config',
        'source_sha256', 'not_before', 'windows_sid', 'profile', 'credential_service',
        'restriction_review', 'restrictions'}, 'INVALID_MANIFEST')
    c.need(m['format'] == 'windows-isolated-read-launch-v1', 'INVALID_MANIFEST')
    c.uuid(m['run_id'])
    c.need(m['run_id'] not in OLD_EXAMPLES, 'ENDED_OR_EXAMPLE_RUN_REFUSED')
    c.need(absolute(m['results_root']) == c.safe(boundary.root), 'RESULT_ROOT_CHANGED')
    c.need(m['profile'] == '' and m['credential_service'] == SERVICE
           and isinstance(m['windows_sid'], str)
           and re.fullmatch(r'S-1-\d+(?:-\d+)+', m['windows_sid']), 'CREDENTIAL_BINDING')
    c.need(finite(m['not_before']), 'INVALID_START')
    c.need(isinstance(m['source_sha256'], dict)
           and set(m['source_sha256']) == set(CODE_FILES), 'SOURCE_SET_CHANGED')
    for name, sha in m['source_sha256'].items():
        read_pin({'path': str(boundary.code_root / name), 'sha256': sha})
    scope_raw = read_pin(m['scope'])
    scope = c.parse(scope_raw)
    c.need(isinstance(scope, dict) and set(scope) == {
        'format', 'run_id', 'endpoint', 'account_id', 'project_id', 'max_requests',
        'max_seconds', 'expires_at', 'reference_sha256'}, 'INVALID_SCOPE')
    c.need(scope['format'] == 'windows-isolated-read-scope-v1'
           and scope['run_id'] == m['run_id'] and scope['endpoint'] == c.ENDPOINT
           and scope['account_id'] == c.ACCOUNT and scope['project_id'] == c.PROJECT
           and type(scope['max_requests']) is int and scope['max_requests'] == 7
           and finite(scope['max_seconds']) and scope['max_seconds'] == 180
           and finite(scope['expires_at'])
           and scope['expires_at'] == m['not_before'] + 180, 'SCOPE_BINDING')
    c.need(isinstance(m['references'], dict) and set(m['references']) == {'metadata','orders'},
           'REFERENCE_BINDING')
    for key, name in (('metadata','before-metadata.json'), ('orders','before-orders.json')):
        c.need(isinstance(m['references'][key], dict)
               and absolute(m['references'][key].get('path')) == c.safe(boundary.reference_root / name),
               'REFERENCE_PATH_CHANGED')
    c.need(m['restriction_review'] == 'reviewed-for-independent-read-v1'
           and isinstance(m['restrictions'], list) and bool(m['restrictions']), 'RESTRICTIONS_UNREVIEWED')
    seen = set()
    for item in m['restrictions']:
        c.need(isinstance(item, dict) and set(item) == {'path','sha256','effect'}
               and item['effect'] in ('block_when_present','preserve_only'), 'INVALID_RESTRICTION')
        p = absolute(item['path'])
        c.need(p not in seen, 'DUPLICATE_RESTRICTION')
        seen.add(p)
        if item['sha256'] is not None:
            hash_string(item['sha256'])
    return m, raw, scope, scope_raw


def restrictions_check(items):
    for item in items:
        path = absolute(item['path'])
        actual = c.digest(path.read_bytes()) if path.exists() else None
        c.need(actual == item['sha256'], 'RESTRICTION_CHANGED')
        c.need(not (path.exists() and item['effect'] == 'block_when_present'), 'READ_RESTRICTED')


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner, check, token, api_key):
        self.inner, self.check = inner, check
        self.token, self.api_key = token, api_key
        self.forwarded, self.denied = 0, None
        self.header_difference = None
        self.closed = False

    async def handle_async_request(self, request):
        try:
            c.need(self.forwarded < 7 and not self.closed, 'EXTRA_REQUEST_REFUSED')
            method, path, params, payload = c.requests()[self.forwarded]
            expected_url = httpx.URL(c.ENDPOINT + path, params=params)
            expected_body = c.encoded(payload) if payload else b''
            c.need(request.method == method and request.url == expected_url
                   and request.content == expected_body, 'REQUEST_CHANGED')
            expected = {
                'host': httpx.URL(c.ENDPOINT).host,
                'accept': '*/*', 'accept-encoding': 'identity', 'connection': 'keep-alive',
                'user-agent': 'python-httpx/' + httpx.__version__,
                'authorization': 'Bearer ' + self.token, 'apikey': self.api_key}
            if payload:
                expected.update({'content-type':'application/json', 'content-length':str(len(expected_body))})
            elif params:
                expected['prefer'] = 'count=exact'
            if (dict(request.headers) != expected
                    or len(request.headers.multi_items()) != len(expected)):
                actual = dict(request.headers)
                counts = {}
                for name, _ in request.headers.multi_items():
                    counts[name] = counts.get(name, 0) + 1
                # Only fixed, known header names. Unknown names themselves may
                # contain secrets, so expose counts rather than arbitrary names.
                known = set(expected) | {'cookie'}
                self.header_difference = dict(
                    missing=sorted(set(expected) - set(actual)),
                    changed=sorted(k for k in expected if k in actual and actual[k] != expected[k]),
                    unexpected_known=sorted((set(actual) - set(expected)) & known),
                    unexpected_unknown_count=len(set(actual) - known),
                    duplicates=sorted(k for k in known if counts.get(k, 0) > 1),
                    unknown_duplicate_count=sum(1 for k in counts if k not in known and counts[k] > 1))
                raise c.ReadStopped('HEADERS_CHANGED')
            self.check()
        except c.ReadStopped as error:
            self.denied = str(error)
            raise
        except Exception:
            self.denied = 'PREFLIGHT_IO_FAILED'
            raise c.ReadStopped(self.denied) from None
        self.forwarded += 1  # Do not retry even if inner transport fails.
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.inner.aclose()


def live_transport():
    return httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0,
                                   http1=True, http2=False, proxy=None,
                                   limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))


async def launch(manifest_path, manifest_sha256, *, boundary, token_source,
                 lease_factory, transport_factory, clock=time.time):
    """Explicit dependencies; offline tests supply only fakes and a temp boundary."""
    environment_check()
    m, manifest_raw, scope, scope_raw = read_manifest(manifest_path, manifest_sha256, boundary)
    root = c.safe(boundary.root)
    attempt = c.safe(root / 'attempts' / m['run_id'])
    run = c.safe(root / 'runs' / m['run_id'])
    c.need(not run.exists() and not attempt.exists(), 'RUN_ALREADY_USED')
    attempt.parent.mkdir(parents=True, exist_ok=True)
    try:
        attempt.mkdir(exist_ok=False)
    except FileExistsError:
        raise c.ReadStopped('RUN_ALREADY_USED') from None
    # Permanent claim even if ANY later local step fails. Never repaired or removed.
    report, guard, lease = None, None, None
    leased, completed = False, False
    reason, collector_entered = None, False
    counter = 0
    journal_path = attempt / 'launcher-journal.jsonl'

    def record(event, data):
        nonlocal counter
        row = {'sequence': counter + 1, 'event': event, 'data': data}
        c.safe(journal_path)
        with journal_path.open('ab') as handle:
            handle.write(c.encoded(row))
            handle.flush()
            os.fsync(handle.fileno())
        counter += 1

    last_clock = clock()
    def time_check():
        nonlocal last_clock
        now = clock()
        c.need(finite(now) and finite(last_clock) and now >= last_clock, 'CLOCK_ROLLBACK')
        last_clock = now
        c.need(m['not_before'] <= now < scope['expires_at'], 'OUTSIDE_EXECUTION_WINDOW')

    try:
        c.new_file(attempt / 'launch-manifest.json', manifest_raw)
        c.new_file(attempt / 'scope-input.json', scope_raw)
        record('claimed', dict(manifest_sha256=manifest_sha256, scope_sha256=c.digest(scope_raw)))
        c.need(not run.exists(), 'RUN_ALREADY_USED')
        time_check()
        refs = {key: read_pin(pin) for key, pin in m['references'].items()}
        _, _, hashes = c.reference(refs['metadata'], refs['orders'])
        c.need(hashes == scope['reference_sha256'], 'REFERENCE_HASH_CHANGED')
        refdir = c.safe(attempt / 'reference')
        refdir.mkdir()
        for key, name in (('metadata','before-metadata.json'), ('orders','before-orders.json')):
            c.new_file(refdir / name, refs[key])
        config_raw = read_pin(m['config'])
        config = validate_cloud_client_config(c.parse(config_raw))
        c.need(config.is_ready and config.url == c.ENDPOINT, 'STAGING_CONFIG_REQUIRED')
        restrictions_check(m['restrictions'])
        lease = lease_factory(m['windows_sid'])
        c.need(lease.acquire() is True, 'CREDENTIAL_LEASE_REFUSED')
        leased = True
        time_check()
        token = token_source.read()
        token_check(token, scope['expires_at'])

        def unchanged():
            environment_check()
            time_check()
            c.need(absolute(str(manifest_path)).read_bytes() == manifest_raw, 'MANIFEST_CHANGED')
            c.need(read_pin(m['scope']) == scope_raw, 'SCOPE_CHANGED')
            for key, pin in m['references'].items():
                c.need(read_pin(pin) == refs[key], 'REFERENCE_CHANGED')
            c.need(read_pin(m['config']) == config_raw, 'CONFIG_CHANGED')
            for name, sha in m['source_sha256'].items():
                read_pin({'path': str(boundary.code_root / name), 'sha256': sha})
            restrictions_check(m['restrictions'])
            current = token_source.read()
            c.need(isinstance(current, str) and hmac.compare_digest(current.encode(), token.encode()),
                   'TOKEN_CHANGED')
            token_check(current, scope['expires_at'])
            time_check()

        unchanged()
        guard = GuardedTransport(transport_factory(), unchanged, token, config.publishable_key)
        record('collector_entering', dict(at=clock(), reference_sha256=hashes))
        collector_entered = True
        report = await c.collect(root / 'runs', scope, refs['metadata'], refs['orders'],
                                 transport=guard, access_token=token,
                                 api_key=config.publishable_key, clock=clock)
        reason = report['stop_reason']
        completed = True
    except c.ReadStopped as error:
        reason = str(error)
    except asyncio.CancelledError:
        reason = 'CANCELLED'
        raise
    except Exception:
        reason = 'LAUNCH_IO_OR_VALIDATION_FAILED'
    except BaseException:
        reason = 'INTERRUPTED'
        raise
    finally:
        # Finalization failure never removes the claim or retries the collector.
        try:
            if guard is not None:
                await guard.aclose()
        finally:
            try:
                if leased:
                    lease.release()
            finally:
                record('returned' if completed else 'stopped', dict(
                    reason=reason, collector_entered=collector_entered,
                    collector_returned=completed,
                    http_reserved=report['http_used'] if report else (None if collector_entered else 0),
                    forwarded=guard.forwarded if guard else 0,
                    header_difference=guard.header_difference if guard else None,
                    transport_denial=guard.denied if guard else None, at=clock()))
    return dict(format='windows-read-launch-result-v1',
                status=report['status'] if report else 'stopped', reason=reason,
                http_reserved=report['http_used'] if report else (None if collector_entered else 0),
                forwarded=guard.forwarded if guard else 0,
                header_difference=guard.header_difference if guard else None,
                transport_denial=guard.denied if guard else None,
                complete=False, execution_allowed=False, resumable=False,
                attempt_directory=str(attempt), run_directory=str(run))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Single reviewed read; no default launch, refresh or resume.')
    parser.add_argument('--manifest')
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args(argv)
    if not args.manifest or not args.manifest_sha256:
        parser.print_help()
        return 2
    try:
        result = asyncio.run(launch(args.manifest, args.manifest_sha256,
            boundary=live_boundary(), token_source=WindowsAccessToken(),
            lease_factory=WindowsLease, transport_factory=live_transport))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'observed' else 1
    except c.ReadStopped as error:
        print(json.dumps({'status':'stopped', 'reason':str(error), 'resumable':False}))
        return 1
    except BaseException:
        print('{"status":"stopped","reason":"LAUNCH_INTERRUPTED_OR_IO_FAILED","resumable":false}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
