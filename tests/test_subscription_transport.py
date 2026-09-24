"""Actual CLI/pi Codex adapter with offline OAuth and HTTP protocol fixtures.

A test-only Node preload intercepts network calls. Production has no Codex
endpoint override or authentication fallback. No real credentials are used.
"""
import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from goal_native.store import Store
from tests.test_cli_transport import ResponsesFixture, ROOT


class SubscriptionTransportTests(unittest.TestCase):
    def test_expired_subscription_refreshes_and_runs_without_api_key_billing(self):
        self.exercise_refresh(reject=False)

    def test_malformed_refresh_response_does_not_leak_tokens_or_replace_credentials(self):
        self.exercise_refresh(reject=True)

    def exercise_refresh(self, *, reject):
        real_node = shutil.which('node')
        self.assertIsNotNone(real_node)
        server = ThreadingHTTPServer(('127.0.0.1', 0), ResponsesFixture)
        server.requests = []
        server.model = 'gpt-6-astra'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(prefix='goal-native-codex-') as directory:
            root = Path(directory)
            state = root / 'state'
            auth = root / 'auth.json'
            trace = root / 'network.jsonl'
            claims = base64.urlsafe_b64encode(json.dumps({
                'https://api.openai.com/auth': {'chatgpt_account_id': 'fixture-account'},
            }).encode()).decode().rstrip('=')
            access = f'eyJhbGciOiJub25lIn0.{claims}.SUBSCRIPTION_FIXTURE'
            refresh = 'ROTATED_REFRESH_FIXTURE'
            original_credentials = {'openai-codex': {
                'type': 'oauth', 'access': 'EXPIRED_ACCESS_FIXTURE',
                'refresh': 'EXPIRED_REFRESH_FIXTURE', 'expires': 1,
            }}
            auth.write_text(json.dumps(original_credentials))
            auth.chmod(0o600)
            config = {
                'access': access, 'refresh': refresh, 'trace': str(trace),
                'reject': reject,
                'endpoint': f'http://127.0.0.1:{server.server_port}/v1/responses',
            }
            preload = root / 'network-fixture.mjs'
            preload.write_text('const config = ' + json.dumps(config) + ';\n' + r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as zlib from 'node:zlib';
const actualFetch = globalThis.fetch;
const record = value => fs.appendFileSync(config.trace, JSON.stringify(value) + '\n');
globalThis.fetch = async (input, options = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url ?? input);
  if (url.href === 'https://auth.openai.com/oauth/token') {
    const form = new URLSearchParams(options.body);
    assert.equal(form.get('grant_type'), 'refresh_token');
    assert.equal(form.get('refresh_token'), 'EXPIRED_REFRESH_FIXTURE');
    record({kind: 'refresh'});
    if (config.reject) return Response.json({access_token: config.access, expires_in: 3600});
    return Response.json({access_token: config.access, refresh_token: config.refresh, expires_in: 3600});
  }
  if (url.origin === 'https://chatgpt.com' && url.pathname.endsWith('/codex/responses')) {
    const headers = new Headers(options.headers);
    assert.equal(headers.get('authorization'), 'Bearer ' + config.access);
    assert.equal(headers.get('chatgpt-account-id'), 'fixture-account');
    let body = options.body;
    if (headers.get('content-encoding') === 'zstd') body = zlib.zstdDecompressSync(body);
    const parsed = JSON.parse(typeof body === 'string' ? body : new TextDecoder().decode(body));
    assert.equal(parsed.model, 'gpt-6-astra');
    record({kind: 'subscription_request', model: parsed.model});
    return actualFetch(config.endpoint, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(parsed), signal: options.signal,
    });
  }
  throw new Error('Unexpected network destination in subscription fixture');
};
''')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            wrapper = bin_dir / 'node'
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(real_node) + ' --import '
                               + shlex.quote(str(preload)) + ' "$@"\n')
            wrapper.chmod(0o700)
            with Store(state) as store:
                goal = store.create_goal('Save a qualified draft through Codex subscription')
            environment = dict(os.environ)
            environment['PATH'] = str(bin_dir) + os.pathsep + environment.get('PATH', '')
            environment['OPENAI_API_KEY'] = 'FORBIDDEN_API_FALLBACK_FIXTURE'
            environment['OPENAI_BASE_URL'] = 'https://not-authorized.invalid/v1'
            result = subprocess.run(
                [sys.executable, '-m', 'goal_native', 'run', '--state', str(state),
                 '--auth-file', str(auth), '--model', 'gpt-6-astra', goal['id']],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=45,
            )
            if reject:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([json.loads(line)['kind'] for line in trace.read_text().splitlines()], ['refresh'])
                self.assertEqual(server.requests, [])
                self.assertEqual(json.loads(auth.read_text()), original_credentials)
                with Store(state) as store:
                    exported = json.dumps(store.export())
                for secret in (access, refresh, 'EXPIRED_REFRESH_FIXTURE', 'FORBIDDEN_API_FALLBACK_FIXTURE'):
                    self.assertNotIn(secret, exported + result.stdout + result.stderr)
                return
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'finished')
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertEqual([event['kind'] for event in events],
                             ['refresh', 'subscription_request', 'subscription_request'])
            refreshed = json.loads(auth.read_text())['openai-codex']
            self.assertEqual(refreshed['access'], access)
            self.assertEqual(refreshed['refresh'], refresh)
            self.assertEqual(auth.stat().st_mode & 0o777, 0o600)
            with Store(state) as store:
                saved = store.goal(goal['id'])
                self.assertEqual(len(saved['invocations']), 2)
                draft = next(item for item in saved['artifacts'] if item['kind'] == 'draft')
                self.assertEqual(draft['content'], 'Useful saved transport-fixture work')
                for invocation in saved['invocations']:
                    self.assertEqual(invocation['usage']['normalized']['total_tokens'], 96)
                exported = json.dumps(store.export())
            for secret in (access, refresh, 'EXPIRED_REFRESH_FIXTURE', 'FORBIDDEN_API_FALLBACK_FIXTURE'):
                self.assertNotIn(secret, exported + result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
