"""Subscription credential boundaries through the actual CLI and pi storage.

All credential material here is synthetic. No account login or live inference.
"""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import tempfile
import unittest

from goal_native.store import Store


ROOT = Path(__file__).resolve().parents[1]


class SubscriptionTests(unittest.TestCase):
    def command(self, *arguments, environment=None):
        return subprocess.run(
            [sys.executable, '-m', 'goal_native', *map(str, arguments)],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20,
        )
    @unittest.skipUnless(os.name == 'posix', 'requires a pseudo-terminal')
    def test_login_ctrl_c_returns_cancelled_without_creating_credentials(self):
        import pty
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / 'auth.json'
            terminal, slave = pty.openpty()
            process = subprocess.Popen(
                [sys.executable, '-m', 'goal_native', 'login',
                 '--method', 'browser', '--auth-file', str(auth)],
                cwd=ROOT, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True,
            )
            os.close(slave)
            output = bytearray()
            interrupted = False
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    readable, _, _ = select.select([terminal], [], [], 0.1)
                    if readable:
                        try:
                            chunk = os.read(terminal, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                        if not interrupted and b'OpenAI Codex login URL:' in output:
                            os.write(terminal, bytes([3]))
                            interrupted = True
                    if interrupted and process.poll() is not None:
                        break
                self.assertTrue(interrupted, 'upstream OAuth did not present its login URL')
                self.assertEqual(130, process.wait(timeout=3), 'login did not terminate after Ctrl-C')
                while select.select([terminal], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(terminal, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                text = output.decode('utf-8', 'replace').replace('\r\n', '\n')
                result, _ = json.JSONDecoder().raw_decode(text[text.rfind('{'):])
                self.assertEqual(result['status'], 'cancelled')
                self.assertFalse(result['run_started'])
                self.assertNotIn('openai-codex', json.loads(auth.read_text()) if auth.exists() else {})
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except PermissionError:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                os.close(terminal)


    def test_codex_does_not_fall_back_to_an_ambient_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state'
            with Store(state) as store:
                goal = store.create_goal('Use subscription billing only')
            environment = dict(os.environ, OPENAI_API_KEY='API_FALLBACK_FIXTURE_SECRET')
            result = self.command('run', '--state', state, '--auth-file', root / 'auth.json',
                                  '--model', 'gpt-6-astra', goal['id'], environment=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('login', json.loads(result.stdout)['error'].lower())
            self.assertNotIn('API_FALLBACK_FIXTURE_SECRET', result.stdout + result.stderr)
            with Store(state) as store:
                self.assertEqual(store.goal(goal['id'])['invocations'], [])

    def test_status_hides_tokens_and_logout_preserves_other_provider_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / 'auth.json'
            other = {'type': 'api_key', 'key': 'OTHER_PROVIDER_FIXTURE_SECRET'}
            auth.write_text(json.dumps({
                'openai-codex': {'type': 'oauth', 'access': 'ACCESS_FIXTURE_SECRET',
                                 'refresh': 'REFRESH_FIXTURE_SECRET', 'expires': 1},
                'unrelated-provider': other,
            }))
            auth.chmod(0o600)
            status = self.command('auth-status', '--auth-file', auth)
            self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
            metadata = json.loads(status.stdout)
            self.assertTrue(metadata['configured'])
            self.assertTrue(metadata['expired'])
            self.assertNotIn('FIXTURE_SECRET', status.stdout + status.stderr)
            logout = self.command('logout', '--auth-file', auth)
            self.assertEqual(logout.returncode, 0, logout.stdout + logout.stderr)
            self.assertNotIn('FIXTURE_SECRET', logout.stdout + logout.stderr)
            self.assertEqual(json.loads(auth.read_text()), {'unrelated-provider': other})
            absent = self.command('auth-status', '--auth-file', auth)
            self.assertNotEqual(absent.returncode, 0)
            self.assertFalse(json.loads(absent.stdout)['configured'])

    def test_auth_store_cannot_be_inside_workspace_or_selected_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state'
            source = root / 'source'
            source.mkdir()
            with Store(state) as store:
                goal = store.create_goal('Do not stage credentials')
            for auth in (state / 'private.json', source / 'private.json'):
                result = self.command('run', '--state', state, '--source-dir', source,
                                      '--auth-file', auth, '--model', 'gpt-6-astra', goal['id'])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('error', json.loads(result.stdout))
            self.assertFalse((state / 'stages').exists())
            with Store(state) as store:
                self.assertEqual(store.goal(goal['id'])['invocations'], [])


if __name__ == '__main__':
    unittest.main()
