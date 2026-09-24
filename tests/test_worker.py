"""Worker authority transitions using the real transactional domain store."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from goal_native.sandbox import Sandbox
from goal_native.store import Store
from goal_native.worker import BridgeError, Worker


class InterruptedResponse:
    """Deterministic adversarial scheduling fixture, not a model provider."""
    def __init__(self, mutation, tool='staged_write', arguments=None):
        self.mutation = mutation
        self.tool = tool
        self.arguments = arguments or {'path': 'late.txt', 'content': 'obsolete write'}
        self.tool_result = None

    def run(self, request, *, rpc_handler, event_handler, cancel_event):
        admitted = rpc_handler('prepare_request', {
            'run_id': request['run_id'], 'invocation_id': None,
            'context': {'messages': request['messages'], 'tools': request['tools']},
            'model': {'id': request['model'], 'contextWindow': 128000, 'maxTokens': 4096},
        })
        self.mutation()
        self.tool_result = rpc_handler('tool_call', {
            'run_id': request['run_id'], 'invocation_id': admitted['invocation_id'],
            'tool_call_id': 'late-request', 'name': self.tool, 'arguments': self.arguments,
        })
        return {'status': 'finished', 'rounds': 1, 'result': 'Fixture response finished'}


class TextWithDiagnosticResponse(InterruptedResponse):
    def run(self, request, *, rpc_handler, event_handler, cancel_event):
        rpc_handler('prepare_request', {
            'run_id': request['run_id'], 'invocation_id': None,
            'context': {'messages': request['messages'], 'tools': request['tools']},
            'model': {'id': request['model'], 'contextWindow': 128000, 'maxTokens': 4096},
        })
        return {
            'status': 'interrupted',
            'stop_reason': 'round_limit',
            'rounds': 1,
            'result': 'partial assistant text',
            'error': 'pi agent round budget exhausted before completion',
        }

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = Store(root / 'controller')
        self.addCleanup(self.store.close)
        self.goal = self.store.create_goal('Keep controlled writes current')
        self.stage = root / 'stage'
        self.stage.mkdir()
        self.sandbox = Sandbox(self.stage)
        self.addCleanup(self.sandbox.close)

    def run_fixture(self, fixture):
        return Worker(self.store, 'fixture-model', provider='openai', sandbox=self.sandbox, bridge=fixture).run(self.goal['id'])

    def test_unread_input_fences_tool_execution(self):
        fixture = InterruptedResponse(lambda: self.store.request(self.goal['id'], 'Do not modify anything'))
        outcome = self.run_fixture(fixture)
        self.assertEqual(outcome['status'], 'failed')
        self.assertFalse((self.stage / 'late.txt').exists())

    def test_replacement_assignment_fences_old_local_tool_effects(self):
        fixture = InterruptedResponse(lambda: self.store.assign(self.goal['id']))
        outcome = self.run_fixture(fixture)
        self.assertEqual(outcome['status'], 'failed')
        self.assertFalse((self.stage / 'late.txt').exists())

    def test_cancellation_rejects_late_tool_rpc(self):
        fixture = InterruptedResponse(lambda: worker.cancel())
        worker = Worker(self.store, 'fixture-model', provider='openai', sandbox=self.sandbox, bridge=fixture)
        outcome = worker.run(self.goal['id'])
        self.assertEqual(outcome['status'], 'cancelled')
        self.assertFalse((self.stage / 'late.txt').exists())
        self.assertEqual(self.store.goal(self.goal['id'])['invocations'][0]['status'], 'cancelled')

    def test_file_change_during_provider_turn_rejects_stale_edit(self):
        original = "answer = 'old'\n"
        current = "answer = 'human revision'\n"
        path = self.stage / "answer.py"
        path.write_text(original, encoding="utf-8")
        fixture = InterruptedResponse(
            lambda: path.write_text(current, encoding="utf-8"),
            tool="staged_edit",
            arguments={
                "path": "answer.py",
                "expected_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "edits": [{"old_text": "'old'", "replacement": "'model revision'"}],
            },
        )
        self.run_fixture(fixture)
        self.assertFalse(fixture.tool_result["ok"])
        self.assertEqual(current, path.read_text(encoding="utf-8"))

    def test_python_profile_cannot_launch_a_container_command(self):
        fixture = InterruptedResponse(
            lambda: None, tool="staged_command",
            arguments={"argv": ["sh", "-c", "echo bypass > bypass.txt"]},
        )
        self.run_fixture(fixture)
        self.assertFalse(fixture.tool_result["ok"])
        self.assertFalse((self.stage / "bypass.txt").exists())


    def test_assistant_text_and_stop_diagnostic_are_separate(self):
        outcome = self.run_fixture(TextWithDiagnosticResponse(lambda: None))
        self.assertEqual(outcome['status'], 'interrupted')
        self.assertEqual(outcome['result'], 'partial assistant text')
        self.assertEqual(outcome['stop_reason'], 'round_limit')
        self.assertEqual(
            outcome['diagnostic']['message'],
            'pi agent round budget exhausted before completion',
        )
        saved = self.store.goal(self.goal['id'])
        self.assertEqual(saved['invocations'][0]['result'], 'partial assistant text')
        self.assertEqual(saved['runs'][0]['assistant_text'], 'partial assistant text')
    def test_busy_worker_rejects_second_goal_without_corrupting_first(self):
        other = self.store.create_goal('Must not replace an active worker')
        def attempt_second_run():
            with self.assertRaisesRegex(BridgeError, 'already owns'):
                worker.run(other['id'])
        fixture = InterruptedResponse(attempt_second_run)
        worker = Worker(self.store, 'fixture-model', provider='openai', sandbox=self.sandbox, bridge=fixture)
        outcome = worker.run(self.goal['id'])
        self.assertEqual(outcome['status'], 'finished')
        self.assertEqual((self.stage / 'late.txt').read_text(), 'obsolete write')
        self.assertEqual(self.store.goal(other['id'])['invocations'], [])

    def test_worker_cannot_grant_effect_authority_through_unknown_tool(self):
        fixture = InterruptedResponse(lambda: None, tool='approve', arguments={'effect_id': 'anything'})
        self.run_fixture(fixture)
        self.assertFalse(fixture.tool_result['ok'])
        self.assertFalse(self.store.goal(self.goal['id'])['effects_allowed'])
        self.assertEqual(self.store.effects(self.goal['id']), [])


    def test_receipt_paging_preserves_unknowns_and_does_not_expose_other_goals_or_provider_context(self):
        assignment = self.store.assign(self.goal["id"])
        invocation = self.store.invoke(self.goal["id"], assignment["id"], {"messages": []})
        self.store.receipt(invocation["id"], "tool.staged_search", {"query": "α"},
                           {"matches": [{"text": "α" * 70_000}], "truncated": True})
        self.store.receipt(invocation["id"], "provider.pi.payload", {}, {"controller": "not a task result"})
        self.store.finish(invocation["id"], "interrupted", "")
        saved = self.store.goal(self.goal["id"])
        task_receipt, provider_receipt = saved["invocations"][0]["receipts"]
        worker = Worker(self.store, "fixture-model", provider="openai", sandbox=self.sandbox)
        worker._goal_id = self.goal["id"]
        first = worker._read_receipt({"receipt_id": task_receipt["id"], "max_chars": 65536})["receipt"]
        second = worker._read_receipt({
            "receipt_id": task_receipt["id"], "offset_chars": first["next_offset_chars"],
            "max_chars": 65536,
        })["receipt"]
        content = first["content"] + second["content"]
        self.assertIsNone(second["next_offset_chars"])
        self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), first["sha256"])
        self.assertTrue(json.loads(content)["result"]["truncated"])
        self.assertTrue(first["historical_only"])
        with self.assertRaises(PermissionError):
            worker._read_receipt({"receipt_id": provider_receipt["id"]})
        other = self.store.create_goal("A separate private task")
        worker._goal_id = other["id"]
        with self.assertRaises(KeyError):
            worker._read_receipt({"receipt_id": task_receipt["id"]})

if __name__ == '__main__':
    unittest.main()
