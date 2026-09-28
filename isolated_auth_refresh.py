"""Independent, explicitly approved, single refresh. Import/help perform no I/O.

Never imports the application session manager, opens a DB or resumes a read run.
Responses/credentials stay in memory; only allowlisted audit metadata is written.
"""
import argparse
import asyncio
import base64
from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import re
import time

import httpx
import isolated_read_collector as c
import isolated_read_launcher as l

SID = 'S-1-5-21-2542480074-635446901-4096835241-1001'
REFRESH = 'SupabaseRefreshToken'
CODE_FILES = ('isolated_auth_refresh.py',) + l.CODE_FILES
ENDED_IDS = l.OLD_EXAMPLES | {'64896904-d883-4549-8d9e-3c53cadb6cb8'}
URL = c.ENDPOINT + '/auth/v1/token?grant_type=refresh_token'
MAX_BODY = 256 * 1024


class Stop(Exception):
    pass


def need(ok, reason):
    if not ok:
        raise Stop(reason)


@dataclass(frozen=True)
class Pair:
    access: str = field(repr=False)
    refresh: str = field(repr=False)


def same(a, b):
    return (isinstance(a, Pair) and isinstance(b, Pair)
            and hmac.compare_digest(a.access.encode(), b.access.encode())
            and hmac.compare_digest(a.refresh.encode(), b.refresh.encode()))


def check_pair(pair, now=None):
    need(isinstance(pair, Pair), 'SESSION_FORMAT')
    need(isinstance(pair.refresh, str) and 0 < len(pair.refresh) <= 32768
         and re.fullmatch(r'[A-Za-z0-9._~-]+', pair.refresh), 'REFRESH_FORMAT')
    need(isinstance(pair.access, str) and 0 < len(pair.access) <= 32768, 'ACCESS_FORMAT')
    try:
        parts = pair.access.split('.')
        need(len(parts) == 3 and all(re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in parts),
             'ACCESS_FORMAT')
        value = c.parse(base64.b64decode(parts[1] + '=' * (-len(parts[1]) % 4),
                                        altchars=b'-_', validate=True))
    except Stop:
        raise
    except Exception:
        raise Stop('ACCESS_FORMAT') from None
    need(isinstance(value, dict) and value.get('sub') == c.ACCOUNT
         and value.get('iss') == c.ENDPOINT + '/auth/v1', 'ACCOUNT_BINDING')
    # Old access may be expired. Claims are local rejection checks, not JWT verification.
    need(l.finite(value.get('exp')), 'ACCESS_EXPIRY')
    if now is not None:
        need(value['exp'] >= now + 60, 'ACCESS_EXPIRY')
    return value['exp']


class WindowsPair:
    """Only the existing generic-refresh / compound-access WinVault layout.

    Do not call get_password/set_password: their generic fallback/migration can
    read or rewrite a different item. Unsupported layouts stop before HTTP.
    The caller must hold the shared credential lease throughout.
    """
    def __init__(self, backend_factory=None):
        self.backend_factory = backend_factory
        self.backend = None

    def _backend(self):
        if self.backend is None:
            if self.backend_factory is None:
                from keyring.backends.Windows import WinVaultKeyring
                self.backend = WinVaultKeyring()
            else:
                self.backend = self.backend_factory()
        return self.backend

    def read(self):
        backend = self._backend()
        access = backend._read_credential(l.ITEM + '@' + l.SERVICE)
        refresh = backend._read_credential(l.SERVICE)
        need(access is not None and refresh is not None
             and access.get('UserName') == l.ITEM and refresh.get('UserName') == REFRESH,
             'CREDENTIAL_LAYOUT')
        pair = Pair(access.value, refresh.value)
        need(isinstance(pair.access, str) and isinstance(pair.refresh, str), 'SESSION_FORMAT')
        return pair

    def replace(self, old, new, before_write):
        need(same(self.read(), old), 'SESSION_CHANGED')
        before_write('refresh')
        need(same(self.read(), old), 'SESSION_CHANGED')
        # Save rotated refresh first; never roll back or retry a partially saved pair.
        self._backend()._set_password(l.SERVICE, REFRESH, new.refresh)
        need(same(self.read(), Pair(old.access, new.refresh)), 'PARTIAL_SAVE_OR_RACE')
        before_write('access')
        need(same(self.read(), Pair(old.access, new.refresh)), 'PARTIAL_SAVE_OR_RACE')
        self._backend()._set_password(l.ITEM + '@' + l.SERVICE, l.ITEM, new.access)
        need(same(self.read(), new), 'READBACK_FAILED')


@dataclass(frozen=True)
class Boundary:
    root: Path
    code_root: Path


def live_boundary():
    need(Path(__file__).resolve().parent == l.WORKSPACE, 'LOCATION_CHANGED')
    return Boundary(l.WORKSPACE / '_evidence/windows-independent-auth-refresh', l.WORKSPACE)


def manifest_read(path, sha, boundary):
    raw = l.read_pin({'path': str(path), 'sha256': sha})
    m = c.parse(raw)
    need(isinstance(m, dict) and set(m) == {
        'format', 'auth_id', 'endpoint', 'account_id', 'windows_sid', 'profile',
        'credential_service', 'results_root', 'not_before', 'approve_until',
        'max_requests', 'max_auth_requests', 'http_timeout_seconds', 'http_deadline_seconds',
        'config', 'source_sha256', 'restriction_review', 'restrictions'}, 'MANIFEST_FORMAT')
    c.uuid(m['auth_id'])
    need(m['auth_id'] not in ENDED_IDS, 'ENDED_ID_REFUSED')
    need(m['format'] == 'windows-isolated-auth-refresh-v1'
         and m['endpoint'] == c.ENDPOINT and m['account_id'] == c.ACCOUNT
         and m['windows_sid'] == SID and m['profile'] == ''
         and m['credential_service'] == l.SERVICE, 'SCOPE_BINDING')
    need(l.absolute(m['results_root']) == c.safe(boundary.root), 'RESULT_ROOT_CHANGED')
    need(all(type(m[k]) is int and m[k] == v for k, v in (
        ('max_requests', 1), ('max_auth_requests', 1),
        ('http_timeout_seconds', 15), ('http_deadline_seconds', 30))), 'LIMIT_CHANGED')
    need(l.finite(m['not_before']) and l.finite(m['approve_until'])
         and m['not_before'] < m['approve_until'], 'APPROVAL_WINDOW_INVALID')
    need(isinstance(m['source_sha256'], dict) and set(m['source_sha256']) == set(CODE_FILES),
         'SOURCE_SET_CHANGED')
    need(m['restriction_review'] == 'reviewed-for-independent-auth-refresh-v1'
         and isinstance(m['restrictions'], list) and bool(m['restrictions']),
         'RESTRICTIONS_UNREVIEWED')
    seen = set()
    for item in m['restrictions']:
        need(isinstance(item, dict) and set(item) == {'path', 'sha256', 'effect'}
             and item['effect'] in ('preserve_only', 'block_when_present'), 'RESTRICTION_FORMAT')
        p = l.absolute(item['path'])
        need(p not in seen and p != c.safe(boundary.root)
             and c.safe(boundary.root) not in p.parents, 'RESTRICTION_PATH')
        seen.add(p)
        if item['sha256'] is not None:
            l.hash_string(item['sha256'])
    return m, raw


class OneRequest(httpx.AsyncBaseTransport):
    def __init__(self, inner, body, api_key, before_send):
        self.inner, self.body, self.api_key, self.before_send = inner, body, api_key, before_send
        self.forwarded = 0

    async def handle_async_request(self, request):
        need(self.forwarded == 0 and request.method == 'POST' and str(request.url) == URL
             and request.content == self.body, 'EXTRA_OR_CHANGED_REQUEST')
        expected = {'host': httpx.URL(URL).host, 'accept': '*/*',
            'accept-encoding': 'identity', 'connection': 'keep-alive',
            'user-agent': 'python-httpx/' + httpx.__version__,
            'content-type': 'application/json', 'content-length': str(len(self.body)),
            'apikey': self.api_key}
        need(dict(request.headers) == expected
             and len(request.headers.multi_items()) == len(expected), 'HEADERS_CHANGED')
        self.before_send()
        self.forwarded = 1  # An attempt, not proof that the server received it.
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


async def launch(manifest_path, manifest_sha256, *, boundary, credentials,
                 lease_factory, transport_factory, clock=time.time, monotonic=time.monotonic):
    """All live adapters explicit. No refresh on import, fallback or automatic read."""
    l.environment_check()
    m, raw = manifest_read(manifest_path, manifest_sha256, boundary)
    root = c.safe(boundary.root)
    root.mkdir(parents=True, exist_ok=True)
    attempt = c.safe(root / m['auth_id'])
    try:
        attempt.mkdir(exist_ok=False)
    except FileExistsError:
        raise Stop('AUTH_ID_ALREADY_USED') from None
    # Claim is permanent, including preflight failures or missing terminal records.
    sequence, reserved, sent = 0, 0, 0
    leased, saved, uncertain = False, False, False
    lease, guard, reason, expiry = None, None, None, None
    last_time = clock()

    def record(event, **data):
        nonlocal sequence
        # Exclusive files avoid append/resume and durably order reservation and writes.
        c.new_file(attempt / f'{sequence + 1:03d}-{event}.json', c.encoded({
            'sequence': sequence + 1, 'event': event, 'data': data}))
        sequence += 1

    def time_check():
        nonlocal last_time
        now = clock()
        need(l.finite(now) and l.finite(last_time) and now >= last_time, 'CLOCK_ROLLBACK')
        last_time = now
        need(m['not_before'] <= now < m['approve_until'], 'OUTSIDE_APPROVAL_WINDOW')
        return now

    def unchanged():
        l.environment_check()
        time_check()
        need(l.read_pin({'path': str(manifest_path), 'sha256': manifest_sha256}) == raw,
             'MANIFEST_CHANGED')
        for name, sha in m['source_sha256'].items():
            l.read_pin({'path': str(boundary.code_root / name), 'sha256': sha})
        l.restrictions_check(m['restrictions'])
        config = l.validate_cloud_client_config(c.parse(l.read_pin(m['config'])))
        need(config.is_ready and config.url == c.ENDPOINT, 'STAGING_CONFIG_REQUIRED')
        return config

    try:
        # Do not copy input bytes or unknown fields into evidence (they may contain secrets).
        record('claimed', auth_id=m['auth_id'], manifest_sha256=manifest_sha256)
        config = unchanged()
        lease = lease_factory(SID)
        need(lease.acquire() is True, 'CREDENTIAL_LEASE_REFUSED')
        leased = True
        time_check()
        old = credentials.read()
        check_pair(old)

        def before_send():
            nonlocal reserved, sent, uncertain
            need(reserved == 0, 'REQUEST_ALREADY_RESERVED')
            unchanged()
            need(same(credentials.read(), old), 'SESSION_CHANGED')
            time_check()
            # A write/fsync error may occur after bytes reached the filesystem.
            # Consume conservatively even then; no dispatch until record returns.
            reserved = 1
            record('reserved', http_reserved=1, auth_reserved=1)
            # Conservative after durable reservation; a crash cannot prove non-dispatch.
            uncertain, sent = True, 1

        guard = OneRequest(transport_factory(), c.encoded({'refresh_token': old.refresh}),
                           config.publishable_key, before_send)
        start = monotonic()
        remaining = min(30, m['approve_until'] - time_check())
        async with asyncio.timeout(remaining):
            async with httpx.AsyncClient(transport=guard, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(15), headers={'accept-encoding': 'identity'}) as client:
                async with client.stream('POST', URL, content=guard.body,
                        headers={'apikey': config.publishable_key, 'content-type': 'application/json'}) as response:
                    need(response.status_code == 200, 'AUTH_HTTP_REJECTED')
                    need(response.headers.get('content-encoding', 'identity').lower() == 'identity',
                         'RESPONSE_ENCODING')
                    chunks, size = [], 0
                    async for chunk in response.aiter_raw():
                        size += len(chunk)
                        need(size <= MAX_BODY, 'RESPONSE_TOO_LARGE')
                        chunks.append(chunk)
                    response_bytes = b''.join(chunks)
        need(0 <= monotonic() - start < 30, 'HTTP_DEADLINE')
        now = time_check()
        try:
            value = c.parse(response_bytes)
        except Exception:
            raise Stop('AUTH_RESPONSE_INVALID') from None
        need(isinstance(value, dict) and isinstance(value.get('user'), dict)
             and value['user'].get('id') == c.ACCOUNT and value.get('token_type') == 'bearer',
             'AUTH_RESPONSE_BINDING')
        new = Pair(value.get('access_token'), value.get('refresh_token'))
        expiry = check_pair(new, now)
        if 'expires_at' in value:
            need(l.finite(value['expires_at']) and value['expires_at'] == expiry, 'ACCESS_EXPIRY')
        unchanged()
        need(same(credentials.read(), old), 'SESSION_CHANGED')

        def before_write(item):
            need(item in ('refresh', 'access'), 'SAVE_PHASE_INVALID')
            unchanged()
            check_pair(new, time_check())
            record('saving-' + item, item=item)

        credentials.replace(old, new, before_write)
        need(same(credentials.read(), new), 'READBACK_FAILED')
        unchanged()
        saved, uncertain = True, False
    except Stop as error:
        reason = str(error)
    except c.ReadStopped:
        reason = 'PIN_PATH_OR_RESTRICTION_FAILED'
    except (asyncio.TimeoutError, httpx.TimeoutException):
        reason = 'HTTP_TIMEOUT'
    except asyncio.CancelledError:
        reason = 'CANCELLED'
        raise
    except Exception:
        reason = 'AUTH_IO_OR_VALIDATION_FAILED'
    except BaseException:
        reason = 'INTERRUPTED'
        raise
    finally:
        try:
            if leased:
                lease.release()
        except Exception:
            reason, saved = 'LEASE_RELEASE_FAILED', False
        # Failure to persist terminal leaves the permanent claim; never repair/replay.
        record('terminal', status='refreshed' if saved else 'stopped', reason=reason,
               http_reserved=reserved, auth_reserved=reserved, dispatch_attempted=sent,
               session_saved_and_readback=saved, session_uncertain=uncertain,
               account_id=c.ACCOUNT if saved else None, expires_at=expiry if saved else None,
               resumable=False)
    return dict(auth_id=m['auth_id'], status='refreshed' if saved else 'stopped', reason=reason,
                http_reserved=reserved, auth_reserved=reserved, dispatch_attempted=sent,
                session_saved_and_readback=saved, session_uncertain=uncertain,
                expires_at=expiry if saved else None, resumable=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Reviewed one-shot authentication refresh; no default run.')
    parser.add_argument('--manifest')
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args(argv)
    if not args.manifest or not args.manifest_sha256:
        parser.print_help()
        return 2
    try:
        result = asyncio.run(launch(args.manifest, args.manifest_sha256, boundary=live_boundary(),
            credentials=WindowsPair(), lease_factory=l.WindowsLease, transport_factory=l.live_transport))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'refreshed' else 1
    except BaseException:
        print('{"status":"stopped","reason":"REFRESH_PREFLIGHT_OR_AUDIT_FAILED","resumable":false}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
