import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from goal_native.server import GoalHTTPServer
from goal_native.store import Store


class RunningServer:
    def __init__(self, state: str | Path, model: str | None = None):
        self.server = GoalHTTPServer(("127.0.0.1", 0), state, model)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.host = f"127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path: str, method: str = "GET", payload=None, origin: str | None = None, host: str | None = None):
        headers = {"Host": host or self.host}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        else:
            data = None
        if origin is not None:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))


class ServerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.server = RunningServer(self.tempdir.name)

    def tearDown(self):
        self.server.close()
        self.tempdir.cleanup()

    def create_goal(self):
        status, body = self.server.request(
            "/api/goals",
            "POST",
            {"outcome": "Publish a considered note", "criteria": "A human can inspect the note"},
            origin=self.server.base,
        )
        self.assertEqual(status, 201, body)
        with Store(self.tempdir.name) as store:
            self.assertEqual(store.goal(body["id"])["id"], body["id"])
        return body

    def test_host_and_origin_fences_are_enforced(self):
        status, body = self.server.request("/api/config", host=f"evil.example:{self.server.server.server_address[1]}")
        self.assertEqual(status, 403)
        self.assertIn("Host", body["error"])

        status, body = self.server.request(
            "/api/goals",
            "POST",
            {"outcome": "must not be created"},
            origin=f"http://evil.example:{self.server.server.server_address[1]}",
        )
        self.assertEqual(status, 403)
        self.assertIn("Origin", body["error"])
        self.assertEqual(self.server.request("/api/goals")[1]["goals"], [])

    def test_manual_draft_assessment_effect_and_reconcile(self):
        goal = self.create_goal()
        goal_id = goal["id"]
        status, goal = self.server.request(
            f"/api/goals/{goal_id}/request",
            "POST",
            {"text": "Allow this explicitly named local destination", "control": "allow_effects"},
            origin=self.server.base,
        )
        self.assertEqual(status, 200, goal)

        status, artifact = self.server.request(
            f"/api/goals/{goal_id}/artifacts",
            "POST",
            {"content": "<candidate>\nA useful note", "name": "Human note", "kind": "product", "limitations": "Not externally published."},
            origin=self.server.base,
        )
        self.assertEqual(status, 201, artifact)
        self.assertEqual(artifact["trust"], "human")
        self.assertEqual(artifact["content"], "<candidate>\nA useful note")

        status, evidence = self.server.request(
            f"/api/goals/{goal_id}/assess",
            "POST",
            {"artifact_id": artifact["id"], "invocation_id": artifact["invocation_id"], "check": "readability", "passed": True, "details": "Read by a human."},
            origin=self.server.base,
        )
        self.assertEqual(status, 201, evidence)
        self.assertTrue(evidence["trusted"])

        status, effect = self.server.request(
            f"/api/goals/{goal_id}/prepare-effect",
            "POST",
            {"artifact_id": artifact["id"], "invocation_id": artifact["invocation_id"], "evidence_id": evidence["id"], "target": "demo/inbox", "expected_version": 0},
            origin=self.server.base,
        )
        self.assertEqual(status, 201, effect)
        effect_id = effect["id"]
        status, effect = self.server.request(f"/api/effects/{effect_id}/approve", "POST", {}, origin=self.server.base)
        self.assertEqual(status, 200, effect)
        status, effect = self.server.request(f"/api/effects/{effect_id}/commit", "POST", {"lose_response": True}, origin=self.server.base)
        self.assertEqual(status, 200, effect)
        self.assertEqual(effect["state"], "unresolved")
        status, effect = self.server.request(f"/api/effects/{effect_id}/commit", "POST", {}, origin=self.server.base)
        self.assertEqual(status, 200, effect)
        self.assertEqual(effect["state"], "unresolved")
        status, effect = self.server.request(f"/api/effects/{effect_id}/reconcile", "POST", {}, origin=self.server.base)
        self.assertEqual(status, 200, effect)
        self.assertEqual(effect["state"], "committed")
        status, destination = self.server.request("/api/destinations/demo/inbox")
        self.assertEqual(status, 200, destination)
        self.assertEqual(destination["content"], "<candidate>\nA useful note")
        self.assertEqual(self.server.request(f"/api/goals/{goal_id}")[1]["events"][-1]["type"], "effect_reconciled")

    def test_revision_search_export_import_and_missing_provider_are_honest(self):
        goal = self.create_goal()
        goal_id = goal["id"]
        status, revised = self.server.request(
            f"/api/goals/{goal_id}/revise",
            "POST",
            {"expected_revision": 1, "outcome": "Publish a revised considered note", "criteria": "It is inspectable", "constraints": "Local only"},
            origin=self.server.base,
        )
        self.assertEqual(status, 200, revised)
        self.assertEqual(revised["revision"], 2)

        status, search = self.server.request("/api/search?q=revised")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in search["goals"]], [goal_id])

        status, run_error = self.server.request(f"/api/goals/{goal_id}/run", "POST", {"context": ""}, origin=self.server.base)
        self.assertEqual(status, 400)
        self.assertIn("model", run_error["error"])
        self.assertNotIn("fallback", run_error["error"].lower())

        status, export = self.server.request("/api/export")
        self.assertEqual(status, 200)
        self.assertNotIn("OPENAI_API_KEY", json.dumps(export))

        imported_tempdir = tempfile.TemporaryDirectory()
        imported = RunningServer(imported_tempdir.name)
        try:
            status, body = imported.request("/api/import", "POST", {"data": export}, origin=imported.base)
            self.assertEqual(status, 200, body)
            status, reopened = imported.request(f"/api/goals/{goal_id}")
            self.assertEqual(status, 200, reopened)
            self.assertEqual(reopened["outcome"], "Publish a revised considered note")
            self.assertEqual(reopened["authority_version"], 1)
            status, second_import = imported.request("/api/import", "POST", {"data": export}, origin=imported.base)
            self.assertEqual(status, 400)
            self.assertIn("empty", second_import["error"])
        finally:
            imported.close()
            imported_tempdir.cleanup()

    def test_static_ui_uses_fixed_assets_and_no_path_traversal(self):
        request = urllib.request.Request(self.server.base + "/", headers={"Host": self.server.host})
        with urllib.request.urlopen(request) as response:
            html = response.read().decode("utf-8")
        self.assertIn("/style.css", html)
        status, _ = self.server.request("/api/../goal-native.sqlite3")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
