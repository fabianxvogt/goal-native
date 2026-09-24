"""Real Python CLI -> Node -> cloned pi -> HTTP transport correctness.

The loopback provider is a deterministic protocol fixture, NOT a model or
performance baseline. No external network or real credential is used.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from goal_native.store import Store


ROOT = Path(__file__).resolve().parents[1]


class ResponsesFixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(request)
        turn = len(self.server.requests)
        if turn == 1:
            arguments = json.dumps({'kind': 'draft', 'name': 'Transport fixture draft',
                                    'content': 'Useful saved transport-fixture work',
                                    'limitations': 'Protocol fixture; not model-generated evidence'})
            item = {'type': 'function_call', 'id': 'fc_fixture', 'call_id': 'call_fixture',
                    'name': 'save_artifact', 'arguments': arguments, 'status': 'completed'}
        else:
            item = {'type': 'message', 'id': 'msg_fixture', 'role': 'assistant',
                    'status': 'completed', 'content': [{'type': 'output_text',
                    'text': 'Transport fixture completed; draft saved.', 'annotations': []}]}
        response = {'id': f'resp_fixture_{turn}', 'object': 'response', 'status': 'completed',
                    'model': getattr(self.server, 'model', 'gpt-4.1-mini'), 'output': [item],
                    'usage': {'input_tokens': 64, 'output_tokens': 32, 'total_tokens': 96,
                              'input_tokens_details': {'cached_tokens': 16},
                              'output_tokens_details': {'reasoning_tokens': 8}}}
        events = [
            {'type': 'response.created', 'response': {'id': response['id'], 'status': 'in_progress'}},
            {'type': 'response.output_item.added', 'output_index': 0,
             'item': {**item, 'arguments': ''} if turn == 1 else {**item, 'content': []}},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
            {'type': 'response.completed', 'response': response},
        ]
        body = ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n'
                       for event in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProviderErrorFixture(ResponsesFixture):
    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        body = json.dumps({'error': {'message': 'fixture provider unavailable', 'type': 'server_error'}}).encode()
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CLITransportTests(unittest.TestCase):
    def test_failed_provider_does_not_invent_zero_usage_or_retry(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ProviderErrorFixture)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(prefix='goal-native-provider-error-') as state:
            store = Store(state)
            goal = store.create_goal('Report provider failure faithfully')
            store.close()
            environment = dict(os.environ, OPENAI_API_KEY='TRANSPORT_FIXTURE_NOT_A_SECRET',
                               OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1')
            result = subprocess.run(
                [sys.executable, '-m', 'goal_native', 'run', '--state', state,
                 '--provider', 'openai', '--model', 'gpt-4.1-mini', goal['id']],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(server.requests), 1)
            store = Store(state)
            try:
                invocation = store.goal(goal['id'])['invocations'][0]
                self.assertEqual(invocation['status'], 'failed')
                self.assertIn('fixture provider unavailable', invocation['result'])
                self.assertIsNone(invocation['usage']['raw']['raw'])
                self.assertTrue(all(value is None for value in invocation['usage']['normalized'].values()))
            finally:
                store.close()

    def test_actual_command_captures_each_provider_turn_and_saved_work(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ResponsesFixture)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(prefix='goal-native-transport-') as state:
            store = Store(state)
            goal = store.create_goal('Save useful work, then return a qualified draft')
            store.close()
            environment = dict(os.environ)
            environment['OPENAI_API_KEY'] = 'TRANSPORT_FIXTURE_NOT_A_SECRET'
            environment['OPENAI_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/v1'
            result = subprocess.run(
                [sys.executable, '-m', 'goal_native', 'run', '--state', state,
                 '--provider', 'openai', '--model', 'gpt-4.1-mini', goal['id']],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            outcome = json.loads(result.stdout)
            self.assertEqual(outcome['status'], 'finished')
            self.assertEqual(len(server.requests), 2)
            # The real provider continuation includes the result of the real saved-artifact tool.
            second = json.dumps(server.requests[1])
            self.assertIn('function_call_output', second)
            store = Store(state)
            try:
                saved = store.goal(goal['id'])
                self.assertEqual(len(saved['invocations']), 2)
                self.assertEqual(len({v['id'] for v in saved['invocations']}), 2)
                for invocation in saved['invocations']:
                    self.assertEqual(invocation['usage']['normalized'], {
                        'input_tokens': 64, 'output_tokens': 32, 'total_tokens': 96,
                        'cached_tokens': 16, 'reasoning_tokens': 8,
                    })
                draft = next(a for a in saved['artifacts'] if a['kind'] == 'draft')
                self.assertEqual(draft['content'], 'Useful saved transport-fixture work')
                self.assertEqual(draft['trust'], 'worker')
                self.assertEqual(draft['limitations'], 'Protocol fixture; not model-generated evidence')
                self.assertNotIn('TRANSPORT_FIXTURE_NOT_A_SECRET', json.dumps(store.export()))
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
