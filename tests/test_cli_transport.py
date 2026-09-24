"""Real Python CLI -> Node -> cloned pi -> HTTP transport correctness.

The loopback provider is a deterministic protocol fixture, NOT a model or
performance baseline. No external network or real credential is used.
"""
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
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
        items = getattr(self.server, 'first_items', [item]) if turn == 1 else [item]
        response = {'id': f'resp_fixture_{turn}', 'object': 'response', 'status': 'completed',
                    'model': getattr(self.server, 'model', 'gpt-4.1-mini'), 'output': items,
                    'usage': {'input_tokens': 64, 'output_tokens': 32, 'total_tokens': 96,
                              'input_tokens_details': {'cached_tokens': 16},
                              'output_tokens_details': {'reasoning_tokens': 8}}}
        events = [{'type': 'response.created', 'response': {'id': response['id'], 'status': 'in_progress'}}]
        for index, output in enumerate(items):
            initial = {**output, 'arguments': ''} if output['type'] == 'function_call' else {**output, 'content': []}
            events.extend([
                {'type': 'response.output_item.added', 'output_index': index, 'item': initial},
                {'type': 'response.output_item.done', 'output_index': index, 'item': output},
            ])
        events.append({'type': 'response.completed', 'response': response})
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


class StreamingFixture(ResponsesFixture):
    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        item = {'type': 'message', 'id': 'msg_stream', 'role': 'assistant',
                'status': 'completed', 'content': [{'type': 'output_text',
                'text': 'Early streamed text. Final text.', 'annotations': []}]}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()

        def send(event):
            self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.flush()

        send({'type': 'response.created', 'response': {'id': 'resp_stream', 'status': 'in_progress'}})
        send({'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'content': []}})
        send({'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0,
              'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}})
        send({'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0,
              'content_index': 0, 'delta': 'Early streamed text. '})
        self.server.release.wait(20)
        try:
            send({'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0,
                  'content_index': 0, 'delta': 'Final text.'})
            send({'type': 'response.output_item.done', 'output_index': 0, 'item': item})
            send({'type': 'response.completed', 'response': {
                'id': 'resp_stream', 'object': 'response', 'status': 'completed',
                'model': 'gpt-4.1-mini', 'output': [item],
                'usage': {'input_tokens': 32, 'output_tokens': 8, 'total_tokens': 40},
            }})
        except OSError:
            pass


class CLITransportTests(unittest.TestCase):
    def test_context_stop_retains_assistant_text_and_rejected_admission_across_reopen(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ResponsesFixture)
        server.requests = []
        assistant_text = 'I will inspect the selected source before continuing.'
        server.first_items = [
            {'type': 'message', 'id': 'msg_budget', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': assistant_text, 'annotations': []}]},
            {'type': 'function_call', 'id': 'fc_budget', 'call_id': 'call_budget',
             'name': 'staged_read', 'arguments': json.dumps({'path': 'large.txt'}), 'status': 'completed'},
        ]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(prefix='goal-native-budget-stop-') as temporary:
            source, state = Path(temporary) / 'source', Path(temporary) / 'state'
            source.mkdir()
            (source / 'large.txt').write_text('bounded source data ' * 800, encoding='utf-8')
            environment = dict(os.environ, OPENAI_API_KEY='TRANSPORT_FIXTURE_NOT_A_SECRET',
                               OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1')
            result = subprocess.run(
                [sys.executable, '-m', 'goal_native', 'ask', 'Inspect large.txt and explain it.',
                 '--state', str(state), '--source-dir', str(source), '--provider', 'openai',
                 '--model', 'gpt-4.1-mini', '--context-budget', '16384'],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            outcome = json.loads(result.stdout)
            self.assertEqual(1, len(server.requests))
            with Store(state) as store:
                goal = store.goal(outcome['goal_id'])
                attempt = goal['runs'][-1]
                self.assertEqual('context_budget', attempt['stop_reason'])
                self.assertEqual('interrupted', attempt['status'])
                self.assertEqual(assistant_text, attempt['assistant_text'])
                self.assertEqual('context_budget', attempt['diagnostic']['kind'])
                self.assertGreater(attempt['admission']['total_tokens'], 16384)
                self.assertEqual(16384, attempt['admission']['max_context_tokens'])
                self.assertEqual(Path(outcome['run']['stage_dir']).name, store.local_stage(goal['id']))

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

    def test_chat_creates_goals_lazily_and_keeps_followups_in_one_session(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ResponsesFixture)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(prefix='goal-native-chat-') as temporary:
            state = Path(temporary) / 'state'
            source = Path(temporary) / 'selected'
            source.mkdir()
            (source / 'note.txt').write_text('Selected source', encoding='utf-8')
            environment = dict(os.environ, HOME=temporary,
                               OPENAI_API_KEY='TRANSPORT_FIXTURE_NOT_A_SECRET',
                               OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1')
            result = subprocess.run(
                [sys.executable, '-m', 'goal_native', '--state', str(state),
                 '--provider', 'openai', '--model', 'gpt-4.1-mini',
                 '--source-dir', str(source), 'chat'],
                input='/new\nSave useful work\nUse that saved work\n/new\nUnrelated task\n/exit\n',
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with Store(state) as store:
                first, second = store.list_goals()
                self.assertEqual(first['outcome'], 'Save useful work')
                self.assertEqual(second['outcome'], 'Unrelated task')
                detail = store.goal(first['id'])
                self.assertEqual([r['text'] for r in detail['requests']],
                                 ['Save useful work', 'Use that saved work'])
                self.assertFalse(detail['effects_allowed'])
                self.assertEqual(detail['acceptances'], [])
                self.assertEqual([r['text'] for r in store.goal(second['id'])['requests']],
                                 ['Unrelated task'])
                runs = [r['result'] for invocation in detail['invocations']
                        for r in invocation['receipts'] if r['tool'] == 'controller.cli.run']
                self.assertEqual([r['status'] for r in runs], ['finished', 'finished'])
                self.assertNotEqual(runs[0]['stage_dir'], runs[1]['stage_dir'])
                self.assertEqual((Path(runs[1]['stage_dir']) / 'note.txt').read_text(),
                                 'Selected source')
            # The actual second user run receives saved work; a new session does not.
            self.assertEqual(len(server.requests), 4)
            self.assertIn('Useful saved transport-fixture work', json.dumps(server.requests[2]))
            self.assertNotIn('Useful saved transport-fixture work', json.dumps(server.requests[3]))
            # Reopen in another process after removing the original input.
            (source / 'note.txt').unlink()
            reopened = subprocess.run(
                [sys.executable, '-m', 'goal_native', '--state', str(state),
                 '--provider', 'openai', '--model', 'gpt-4.1-mini'],
                input=f"/resume {first['id']}\nContinue with my files\n/exit\n",
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
            with Store(state) as store:
                retained = store.local_stage(first['id'])
                self.assertIsNotNone(retained)
                restored_stage = state / 'stages' / first['id'] / retained
                self.assertNotEqual(str(restored_stage.resolve()), runs[-1]['stage_dir'])
                self.assertEqual((restored_stage / 'note.txt').read_text(), 'Selected source')
            fresh = subprocess.run(
                [sys.executable, '-m', 'goal_native', 'resume', first['id'], '--state', str(state),
                 '--provider', 'openai', '--model', 'gpt-4.1-mini', '--fresh'],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
            self.assertFalse((Path(json.loads(fresh.stdout)['stage_dir']) / 'note.txt').exists())

    def test_cancelled_stream_keeps_visible_partial_work_without_repeating_it(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), StreamingFixture)
        server.requests, server.release = [], threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.release.set)
        with tempfile.TemporaryDirectory(prefix='goal-native-partial-stop-') as temporary:
            environment = dict(os.environ, HOME=temporary, OPENAI_API_KEY='STREAM_FIXTURE_KEY',
                               OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1')
            process = subprocess.Popen(
                [sys.executable, '-m', 'goal_native', '--state', temporary,
                 '--provider', 'openai', '--model', 'gpt-4.1-mini'],
                cwd=ROOT, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                process.stdin.write(b'Respond in two parts.\n/exit\n')
                process.stdin.flush()
                prefix, deadline = b'', time.monotonic() + 15
                while b'Early streamed text. ' not in prefix and time.monotonic() < deadline:
                    if select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        prefix += chunk
                self.assertIn(b'Early streamed text. ', prefix)
                process.send_signal(signal.SIGINT)
                suffix, stderr = process.communicate(timeout=20)
                self.assertEqual(130, process.returncode, (prefix + suffix + stderr).decode())
                self.assertEqual(1, (prefix + suffix).count(b'Early streamed text. '))
                with Store(temporary) as store:
                    detail = store.goal(store.list_goals()[0]['id'])
                    self.assertEqual('Early streamed text. ', detail['runs'][-1]['assistant_text'])
                    self.assertEqual('cancelled', detail['runs'][-1]['status'])
                    self.assertEqual('paused', detail['status'])
                    self.assertEqual(1, len(detail['requests']))
                    self.assertFalse(any(inv['status'] == 'running' for inv in detail['invocations']))
            finally:
                server.release.set()
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def test_chat_shows_partial_text_before_provider_completion_without_repeating_it(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), StreamingFixture)
        server.requests = []
        server.release = threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.release.set)
        with tempfile.TemporaryDirectory(prefix='goal-native-streaming-') as temporary:
            environment = dict(os.environ, HOME=temporary, OPENAI_API_KEY='STREAM_FIXTURE_KEY',
                               OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1')
            process = subprocess.Popen(
                [sys.executable, '-m', 'goal_native', '--state', temporary,
                 '--provider', 'openai', '--model', 'gpt-4.1-mini'],
                cwd=ROOT, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                process.stdin.write(b'Respond in two parts.\n/exit\n')
                process.stdin.flush()
                prefix = b''
                deadline = time.monotonic() + 10
                while b'Early streamed text. ' not in prefix and time.monotonic() < deadline:
                    if select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        prefix += chunk
                self.assertIn(b'Early streamed text. ', prefix)
                self.assertNotIn(b'Final text.', prefix)
                self.assertIsNone(process.poll())
                server.release.set()
                suffix, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, (prefix + suffix + stderr).decode())
                self.assertEqual((prefix + suffix).count(b'Early streamed text. Final text.'), 1)
            finally:
                server.release.set()
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
