"""Bounded controller behavior for disposable language-server queries."""
from __future__ import annotations

import json
import unittest

from goal_native.language_tools import LanguageTools, LanguageToolsError
from goal_native.language_runner import LSP_HELPER


class FakeRuntime:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[tuple[dict, bool]] = []

    def command(self, arguments: dict, *, publish: bool = True) -> dict:
        self.calls.append((arguments, publish))
        return self.response


def response_for(action: str = "definition", path: str = "main.py", *, complete: bool = True) -> dict:
    return {
        "exit_code": 0,
        "stdout": json.dumps(
            {
                "ok": True,
                "action": action,
                "path": path,
                "results": [{"path": "lib.py", "line": 2, "column": 1, "message": "fixture diagnostic"}],
                "complete": complete,
                "truncated": False,
            }
        ),
        "before": {"candidate": "a" * 64, "image": "sha256:" + "1" * 64},
        "after": {"candidate": "b" * 64, "image": "sha256:" + "2" * 64},
    }


class LanguageToolsTests(unittest.TestCase):
    def test_invalid_and_oversized_timeouts_fail_before_runtime(self) -> None:
        runtime = FakeRuntime(response_for())
        tools = LanguageTools(runtime)
        with self.assertRaises(LanguageToolsError):
            tools.query({"action": "definition", "path": "main.py", "timeout_seconds": 30.01})
        with self.assertRaises(LanguageToolsError):
            tools.query({"action": "definition", "path": "main.py", "timeout_seconds": True})
        self.assertEqual(runtime.calls, [])

    def test_unsupported_and_malformed_results_fail_visibly(self) -> None:
        tools = LanguageTools(FakeRuntime(response_for()))
        with self.assertRaises(LanguageToolsError):
            tools.query({"action": "definition", "path": "main.txt"})
        malformed = FakeRuntime({"exit_code": 0, "stdout": "not-json"})
        with self.assertRaises(LanguageToolsError):
            LanguageTools(malformed).query({"action": "definition", "path": "main.py"})
        missing_exit = response_for()
        del missing_exit["exit_code"]
        with self.assertRaises(LanguageToolsError):
            LanguageTools(FakeRuntime(missing_exit)).query({"action": "definition", "path": "main.py"})

    def test_out_of_root_results_are_omitted_and_incomplete_diagnostics_fail(self) -> None:
        response = response_for("diagnostics", "main.py")
        payload = json.loads(response["stdout"])
        payload["results"] = [
            {"path": "../outside.py", "line": 1, "column": 1},
            {"path": "inside.py", "line": 1, "column": 1, "message": "fixture diagnostic"},
        ]
        response["stdout"] = json.dumps(payload)
        result = LanguageTools(FakeRuntime(response)).query({"action": "diagnostics", "path": "main.py"})
        self.assertEqual([item["path"] for item in result["results"]], ["inside.py"])
        self.assertEqual(result["omitted"], 1)
        pending = FakeRuntime(response_for("diagnostics", "main.py", complete=False))
        with self.assertRaises(LanguageToolsError):
            LanguageTools(pending).query({"action": "diagnostics", "path": "main.py"})

    def test_missing_jsonrpc_result_is_not_a_valid_empty_navigation_result(self) -> None:
        namespace = {"__name__": "protocol_test"}
        exec(LSP_HELPER, namespace)

        class Connection:
            def __init__(self, response):
                self.response = response

            def send(self, message):
                self.request_id = message["id"]

            def read(self):
                return {"jsonrpc": "2.0", "id": self.request_id, **self.response}

        with self.assertRaises(namespace["ProtocolError"]):
            namespace["request"](Connection({}), 1, "textDocument/definition", {}, {}, "file:///workspace/main.py")
        self.assertIsNone(namespace["request"](
            Connection({"result": None}), 1, "textDocument/definition", {}, {}, "file:///workspace/main.py",
        ))


if __name__ == "__main__":
    unittest.main()
