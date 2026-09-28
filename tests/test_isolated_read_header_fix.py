"""New cookie/diagnostic impact cases only; existing test methods are not run."""
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
import isolated_read_collector as c
import isolated_read_launcher as l
from tests import test_isolated_read_launcher as fixture


class HeaderFixTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only the synthetic data fixture and helpers, never its test methods.
    setUp = fixture.LauncherTests.setUp
    pin = fixture.LauncherTests.pin
    save_manifest = fixture.LauncherTests.save_manifest
    run_launch = fixture.LauncherTests.run_launch
    run_dir = fixture.LauncherTests.run_dir
    attempt_dir = fixture.LauncherTests.attempt_dir
    transport = fixture.LauncherTests.transport

    async def handler(self, request):
        self.assertTrue(self.lease.active)
        self.calls.append(request)
        value = self.rows[request.url.path.rsplit('/', 1)[-1]]
        headers = {'set-cookie': 'synthetic-session=COOKIE_SECRET_SENTINEL; Path=/; Secure; HttpOnly'}
        if isinstance(value, list):
            headers['content-range'] = f'0-{len(value)-1}/{len(value)}' if value else '*/0'
        return httpx.Response(200, content=c.encoded(value), headers=headers)

    def assert_no_secrets_in_evidence(self):
        combined = b''.join(p.read_bytes() for p in self.boundary.root.rglob('*') if p.is_file())
        for secret in ('COOKIE_SECRET_SENTINEL', 'VALUE_SECRET_SENTINEL',
                       'x-secret-name-sentinel', self.token.value):
            self.assertNotIn(secret.encode(), combined)

    async def test_missing_cookie_clear_reproduces_q2_denial_without_network(self):
        # Reproduce the pre-fix behavior without an untracked source archive.
        with patch.object(httpx.Cookies, 'clear', return_value=None):
            result = await self.run_launch()
        self.assertEqual((result['reason'], result['http_reserved'], result['forwarded']),
                         ('HEADERS_CHANGED', 2, 1))
        self.assertEqual(result['header_difference']['unexpected_known'], ['cookie'])
        self.assertEqual(len(self.calls), 1)
        self.assert_no_secrets_in_evidence()
        # A finished synthetic attempt cannot be retried under the fixed source.
        with self.assertRaisesRegex(c.ReadStopped, 'RUN_ALREADY_USED'):
            await self.run_launch()
        self.assertEqual(len(self.calls), 1)

    async def test_all_seven_requests_ignore_repeated_response_cookies(self):
        result = await self.run_launch()
        self.assertEqual((result['status'], result['http_reserved'], result['forwarded']),
                         ('observed', 7, 7))
        self.assertIsNone(result['header_difference'])
        self.assertEqual([r.url.path for r in self.calls], [p for _, p, _, _ in c.requests()])
        for index, request in enumerate(self.calls):
            self.assertNotIn('cookie', request.headers)
            self.assertEqual(request.headers['authorization'], 'Bearer ' + self.token.value)
            if index >= 2:
                self.assertEqual(request.headers['prefer'], 'count=exact')
        self.assert_no_secrets_in_evidence()

    async def test_seeded_memory_cookie_is_removed_before_first_request(self):
        original = httpx.AsyncClient.__init__
        def seed(client, *args, **kwargs):
            original(client, *args, **kwargs)
            client.cookies.set('synthetic-seeded', 'COOKIE_SECRET_SENTINEL')
        with patch.object(httpx.AsyncClient, '__init__', seed):
            result = await self.run_launch()
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(all('cookie' not in r.headers for r in self.calls))
        self.assert_no_secrets_in_evidence()

    async def inject_q2(self, mutate):
        original = httpx.AsyncClient.build_request
        def build(client, *args, **kwargs):
            request = original(client, *args, **kwargs)
            if request.url.path.endswith('get_sync_handshake'):
                mutate(request)
            return request
        with patch.object(httpx.AsyncClient, 'build_request', build):
            result = await self.run_launch()
        self.assertEqual(result['reason'], 'HEADERS_CHANGED')
        self.assertEqual((result['http_reserved'], result['forwarded'], len(self.calls)), (2, 1, 1))
        terminal = c.parse((self.attempt_dir() / 'launcher-journal.jsonl').read_bytes().splitlines()[-1])
        self.assertEqual(terminal['data']['header_difference'], result['header_difference'])
        self.assert_no_secrets_in_evidence()
        return result['header_difference']

    async def test_explicit_cookie_injection_still_refused_and_redacted(self):
        difference = await self.inject_q2(lambda r: r.headers.__setitem__('cookie', 'COOKIE_SECRET_SENTINEL'))
        self.assertEqual(difference['unexpected_known'], ['cookie'])
        self.assertEqual(difference['unexpected_unknown_count'], 0)

    async def test_changed_authorization_still_refused_without_value_logging(self):
        difference = await self.inject_q2(lambda r: r.headers.__setitem__('authorization', 'VALUE_SECRET_SENTINEL'))
        self.assertEqual(difference['changed'], ['authorization'])

    async def test_unknown_header_names_and_values_are_not_persisted(self):
        difference = await self.inject_q2(lambda r: r.headers.__setitem__('x-secret-name-sentinel', 'VALUE_SECRET_SENTINEL'))
        self.assertEqual(difference['unexpected_known'], [])
        self.assertEqual(difference['unexpected_unknown_count'], 1)

    async def test_missing_api_key_diagnostic_keeps_strict_rejection(self):
        difference = await self.inject_q2(lambda r: r.headers.__delitem__('apikey'))
        self.assertEqual(difference['missing'], ['apikey'])

    async def test_duplicate_known_header_is_rejected_and_identified(self):
        def duplicate(request):
            request.headers = httpx.Headers(list(request.headers.multi_items()) +
                [('content-type', 'VALUE_SECRET_SENTINEL')])
        difference = await self.inject_q2(duplicate)
        self.assertEqual(difference['duplicates'], ['content-type'])

    async def test_duplicate_unknown_header_is_counted_without_name(self):
        def duplicate(request):
            request.headers = httpx.Headers(list(request.headers.multi_items()) +
                [('x-secret-name-sentinel', 'one'), ('x-secret-name-sentinel', 'VALUE_SECRET_SENTINEL')])
        difference = await self.inject_q2(duplicate)
        self.assertEqual(difference['unknown_duplicate_count'], 1)
        self.assertEqual(difference['unexpected_unknown_count'], 1)

    async def test_cookie_on_401_does_not_trigger_extra_request_or_refresh(self):
        async def reject(request):
            self.calls.append(request)
            return httpx.Response(401, content=b'{}', headers={'set-cookie': 's=COOKIE_SECRET_SENTINEL; Path=/'})
        self.handler = reject
        result = await self.run_launch()
        self.assertEqual((result['reason'], result['forwarded']), ('HTTP_STATUS_REFUSED', 1))
        observation = c.parse((self.run_dir() / 'observation.json').read_bytes())
        self.assertEqual(observation['token_refreshes'], 0)
        self.assertEqual(observation['document_structure_writes'], 0)
        self.assert_no_secrets_in_evidence()

    async def test_cookie_on_redirect_does_not_follow_location(self):
        async def redirect(request):
            self.calls.append(request)
            return httpx.Response(302, content=b'{}', headers={
                'set-cookie': 's=COOKIE_SECRET_SENTINEL; Path=/',
                'location': c.ENDPOINT + '/rest/v1/documents'})
        self.handler = redirect
        result = await self.run_launch()
        self.assertEqual((result['reason'], result['forwarded']), ('HTTP_STATUS_REFUSED', 1))
        self.assertEqual(len(self.calls), 1)
        self.assert_no_secrets_in_evidence()
