from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

class SlowResponsesFixture(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.server.started.set()
        self.server.release.wait(20)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"event: response.completed\\ndata: {}\\n\\n")
        except OSError:
            pass

class CompletedResponsesFixture(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        request_length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(request_length))
        turn = self.server.turns
        self.server.turns += 1
        item = {
            "type": "message",
            "id": f"msg_cli_{turn}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "CLI resume completed.", "annotations": []}],
        }
        response = {
            "id": f"resp_cli_{turn}",
            "object": "response",
            "status": "completed",
            "model": "gpt-4.1-mini",
            "output": [item],
            "usage": {"input_tokens": 32, "output_tokens": 8, "total_tokens": 40},
        }
        events = [
            {"type": "response.created", "response": {"id": response["id"], "status": "in_progress"}},
            {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        body = "".join(
            f"event: {event['type']}{chr(10)}data: {json.dumps(event)}{chr(10)}{chr(10)}"
            for event in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CLISubprocessTests(unittest.TestCase):
    def run_cli(self, *arguments: str, expected: int = 0, env: dict[str, str] | None = None) -> dict:
        process = subprocess.run(
            [sys.executable, "-m", "goal_native", *arguments],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(expected, process.returncode, process.stdout + process.stderr)
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"CLI did not emit JSON stdout: {process.stdout!r}; stderr={process.stderr!r}")
        self.assertIsInstance(payload, dict)
        return payload

    def test_summary_distinguishes_finished_work_from_changed_contract_and_acceptance(self) -> None:
        from goal_native.store import Store

        with tempfile.TemporaryDirectory(prefix="goal-native-cli-summary-") as temporary:
            goal = self.run_cli("create", "Calculate a local result", "--state", temporary)
            goal_id = goal["id"]
            empty = self.run_cli("show", goal_id, "--summary", "--state", temporary)
            self.assertIsNone(empty["latest_invocation_status"])
            with Store(Path(temporary)) as store:
                assignment = store.assign(goal_id)
                invocation = store.invoke(goal_id, assignment["id"], {"messages": ["private trace"]})
                artifact = store.artifact(
                    goal_id, invocation["id"], "candidate", "42",
                    name="answer", trust="worker", limitations="Not independently checked",
                )
                store.receipt(
                    invocation["id"], "tool.staged_run", {"arguments": {"argv": ["python", "answer.py"]}},
                    {"stdout": "42\n", "exit_code": 0},
                )
                store.finish(invocation["id"], "finished", "Calculated 42")
                # A completed provider turn can precede a rejected next request.
                store.receipt(
                    invocation["id"], "controller.cli.run", {},
                    {"status": "failed", "result": "Context budget exhausted"},
                )
            completed = self.run_cli("show", goal_id, "--summary", "--state", temporary)
            self.assertEqual("finished", completed["latest_invocation_status"])
            self.assertEqual("failed", completed["recorded_runs"][0]["result"]["status"])
            self.assertEqual("draft", completed["goal_status"])
            self.assertEqual([], completed["acceptances"])
            self.assertIsNone(completed["invocations"][0]["usage"])
            self.assertTrue(completed["invocations"][0]["matches_current_contract"])
            self.assertEqual(
                {"stdout": "42\n", "exit_code": 0},
                completed["invocations"][0]["tools"][0]["result"],
            )
            self.assertEqual([artifact["id"]], [item["id"] for item in completed["artifacts"]])
            self.assertEqual("worker", completed["artifacts"][0]["trust"])
            self.assertEqual("Not independently checked", completed["artifacts"][0]["limitations"])
            self.run_cli("request", goal_id, "Now calculate the revised result", "--state", temporary)
            changed = self.run_cli("show", goal_id, "--summary", "--state", temporary)
            self.assertFalse(changed["invocations"][0]["matches_current_contract"])
            self.assertEqual("finished", changed["latest_invocation_status"])
            self.assertEqual([], changed["acceptances"])
            self.assertEqual(completed["artifacts"], changed["artifacts"])

    def test_durable_lifecycle_mock_effect_export_import_and_stale_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-cli-") as temporary:
            state = Path(temporary) / "state"
            imported_state = Path(temporary) / "imported"
            export_file = Path(temporary) / "workspace.json"

            created = self.run_cli("create", "Publish the local record", "--state", str(state))
            goal_id = created["id"]
            self.run_cli(
                "request",
                "--state",
                str(state),
                goal_id,
                "Permit the exact mock effect",
                "--control",
                "allow_effects",
            )
            revised = self.run_cli(
                "revise",
                "--state",
                str(state),
                goal_id,
                "--expected-revision",
                "1",
                "--outcome",
                "Publish the revised local record",
                "--criteria",
                "Keep the candidate exact",
            )
            self.assertEqual(2, revised["revision"])

            artifact = self.run_cli(
                "artifacts",
                "add",
                "--state",
                str(state),
                goal_id,
                "--text",
                "Approved report",
                "--kind",
                "candidate",
                "--name",
                "Report",
            )
            evidence = self.run_cli(
                "assess",
                "--state",
                str(state),
                goal_id,
                "--artifact-id",
                artifact["id"],
                "--check",
                "human content review",
                "--passed",
                "--details",
                '{"reviewer":"cli test"}',
            )
            accepted = self.run_cli(
                "accept",
                "--state",
                str(state),
                goal_id,
                "--artifact-id",
                artifact["id"],
                "--evidence-id",
                evidence["id"],
                "--expected-revision",
                "2",
            )
            self.assertEqual(goal_id, accepted["goal_id"])
            prepared = self.run_cli(
                "prepare",
                "--state",
                str(state),
                "--invocation-id",
                artifact["invocation_id"],
                "--artifact-id",
                artifact["id"],
                "--target",
                "report",
                "--expected-version",
                "0",
                "--evidence-id",
                evidence["id"],
            )
            self.run_cli("approve", "--state", str(state), prepared["id"])
            unresolved = self.run_cli("commit", "--state", str(state), prepared["id"], "--lose-response")
            self.assertEqual("unresolved", unresolved["state"])
            reconciled = self.run_cli("reconcile", "--state", str(state), prepared["id"])
            self.assertEqual("committed", reconciled["state"])

            exported = self.run_cli("export", "--state", str(state), "--output", str(export_file))
            self.assertEqual("goal-native-export", exported["format"])
            self.assertTrue(export_file.is_file())
            imported = self.run_cli("import", "--state", str(imported_state), str(export_file))
            self.assertTrue(imported["imported"])
            shown = self.run_cli("show", "--state", str(imported_state), goal_id)
            self.assertEqual("imported", shown["effects"][0]["state"])

            self.run_cli(
                "request",
                "--state",
                str(state),
                goal_id,
                "Revoke the old candidate input",
                "--control",
                "draft",
            )
            stale_environment = dict(os.environ)
            stale_environment.pop("OPENAI_API_KEY", None)
            stale = self.run_cli(
                "accept",
                "--state",
                str(state),
                goal_id,
                "--artifact-id",
                artifact["id"],
                "--evidence-id",
                evidence["id"],
                "--expected-revision",
                "2",
                expected=1,
            )
            self.assertIn("error", stale)
            missing = self.run_cli(
                "run",
                "--state",
                str(state),
                "--provider", "openai",
                "--model",
                "gpt-4.1-mini",
                "--api-key-env",
                "CLI_TEST_MISSING_KEY",
                goal_id,
                expected=1,
                env=stale_environment,
            )
            self.assertIn("credential environment variable", missing["error"])

            listed = self.run_cli("artifacts", "list", "--state", str(state), goal_id)
            self.assertEqual(2, len(listed["artifacts"]))

    @unittest.skipUnless(
        sys.version_info >= (3, 11) and sys.platform == "darwin",
        "Goal Native CLI Worker transport requires Python 3.11+ on macOS",
    )
    def test_resume_after_export_import_uses_a_replacement_assignment(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), CompletedResponsesFixture)
        server.turns = 0
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop_fixture() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop_fixture)
        with tempfile.TemporaryDirectory(prefix="goal-native-cli-resume-") as temporary:
            root = Path(temporary)
            state = root / "state"
            restored = root / "restored"
            export_file = root / "workspace.json"
            created = self.run_cli("create", "--state", str(state), "Resume me")
            environment = dict(os.environ)
            environment["OPENAI_API_KEY"] = "CLI_RESUME_FIXTURE_KEY"
            environment["OPENAI_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
            first = self.run_cli(
                "run",
                "--state",
                str(state),
                "--provider", "openai",
                "--model",
                "gpt-4.1-mini",
                created["id"],
                env=environment,
            )
            self.assertEqual("finished", first["status"])
            self.run_cli("export", "--state", str(state), "--output", str(export_file))
            self.run_cli("import", "--state", str(restored), str(export_file))
            resumed = self.run_cli(
                "resume",
                "--state",
                str(restored),
                "--provider", "openai",
                "--model",
                "gpt-4.1-mini",
                created["id"],
                env=environment,
            )
            self.assertEqual("finished", resumed["status"])
            self.assertNotEqual(first["stage_dir"], resumed["stage_dir"])
            shown = self.run_cli("show", "--state", str(restored), created["id"])
            self.assertEqual(2, len(shown["invocations"]))
            self.assertNotEqual(
                shown["invocations"][0]["assignment_id"],
                shown["invocations"][1]["assignment_id"],
            )

    @unittest.skipUnless(
        sys.version_info >= (3, 11) and sys.platform == "darwin",
        "Goal Native CLI cancellation requires Python 3.11+ on macOS",
    )
    def test_ctrl_c_fences_run_and_leaves_no_running_invocation(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowResponsesFixture)
        server.started = threading.Event()
        server.release = threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop_fixture() -> None:
            server.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop_fixture)
        with tempfile.TemporaryDirectory(prefix="goal-native-cli-signal-") as temporary:
            state = Path(temporary) / "state"
            source = Path(temporary) / "selected-source"
            source.mkdir()
            (source / "safe.txt").write_text("safe", encoding="utf-8")
            (source / ".env").write_text("not staged", encoding="utf-8")
            (source / "secret.txt").write_text("not staged", encoding="utf-8")
            (source / "link.txt").symlink_to(source / "safe.txt")
            (source / ".env.local").write_text("not staged", encoding="utf-8")
            (source / ".env.production").write_text("not staged", encoding="utf-8")
            (source / "OPENAI_API_KEY").write_text("not staged", encoding="utf-8")
            (source / "SERVICE_TOKEN").write_text("not staged", encoding="utf-8")
            created = self.run_cli("create", "--state", str(state), "--outcome", "Interruptible run")
            environment = dict(os.environ)
            environment["OPENAI_API_KEY"] = "CLI_SIGNAL_FIXTURE_KEY"
            environment["OPENAI_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "goal_native",
                    "run",
                    "--state",
                    str(state),
                    "--provider", "openai",
                    "--model",
                    "gpt-4.1-mini",
                    "--max-time",
                    "30",
                    "--source-dir",
                    str(source),
                    created["id"],
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if not server.started.wait(10):
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                self.fail(f"slow provider fixture was not reached: {stdout} {stderr}")
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(130, process.returncode, stdout + stderr)
            outcome = json.loads(stdout)
            self.assertEqual("cancelled", outcome["status"])
            stage = Path(outcome["stage_dir"])
            self.assertEqual("safe", (stage / "safe.txt").read_text(encoding="utf-8"))
            self.assertFalse((stage / ".env").exists())
            self.assertFalse((stage / "secret.txt").exists())
            self.assertFalse((stage / "link.txt").exists())
            self.assertFalse((stage / ".env.local").exists())
            self.assertFalse((stage / ".env.production").exists())
            self.assertFalse((stage / "OPENAI_API_KEY").exists())
            self.assertFalse((stage / "SERVICE_TOKEN").exists())
            self.assertTrue(Path(outcome["stage_dir"]).is_dir())
            shown = self.run_cli("show", "--state", str(state), created["id"])
            self.assertEqual("paused", shown["status"])
            self.assertFalse(any(item["status"] == "running" for item in shown["invocations"]))

    def test_chat_navigation_does_not_create_work_or_revive_cancelled_goals(self) -> None:
        from goal_native.store import Store

        with tempfile.TemporaryDirectory(prefix="goal-native-chat-navigation-") as temporary:
            state = Path(temporary) / "state"
            empty = subprocess.run(
                [sys.executable, "-m", "goal_native", "--state", str(state)],
                input="/new\n/help\n/exit\n", cwd=ROOT,
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(0, empty.returncode, empty.stdout + empty.stderr)
            self.assertFalse(state.exists())
            with Store(state) as store:
                goal = store.create_goal("Existing work")
                store.request(goal["id"], "Permit mock effects", control="allow_effects")
                cancelled = store.create_goal("Cancelled work")
                store.request(cancelled["id"], "Stop", control="cancel")
                before = store.export()
            opened = subprocess.run(
                [sys.executable, "-m", "goal_native", "chat", "--state", str(state)],
                input=f"/sessions\n/resume {goal['id']}\n/status\n"
                      f"/resume {cancelled['id']}\n/new\n/exit\n",
                cwd=ROOT, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(0, opened.returncode, opened.stdout + opened.stderr)
            with Store(state) as store:
                after = store.export()
                for key in ("goals", "requests", "assignments", "invocations", "artifacts"):
                    self.assertEqual(before["records"][key], after["records"][key])

    def test_chat_setup_failure_keeps_requests_and_can_continue_after_reopening(self) -> None:
        from goal_native.store import Store

        with tempfile.TemporaryDirectory(prefix="goal-native-chat-failure-") as temporary:
            environment = dict(os.environ, HOME=temporary)
            environment.pop("CHAT_UNSET_TEST_KEY", None)
            arguments = [
                sys.executable, "-m", "goal_native", "--state", temporary,
                "--provider", "openai", "--model", "gpt-4.1-mini",
                "--api-key-env", "CHAT_UNSET_TEST_KEY", "chat",
            ]
            failed = subprocess.run(
                arguments, input="Keep this exact request\\\nand this line\n/exit\n",
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(1, failed.returncode)
            with Store(temporary) as store:
                goal, = store.list_goals()
                self.assertEqual("Keep this exact request\nand this line", goal["outcome"])
                store.request(goal["id"], "Pause", control="pause")
            continued = subprocess.run(
                arguments, input="/sessions\n/resume 1\nContinue without effects\n/exit\n",
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(1, continued.returncode)
            with Store(temporary) as store:
                saved = store.goal(goal["id"])
                self.assertEqual("Continue without effects", saved["requests"][-1]["text"])
                self.assertEqual(3, len(saved["requests"]))
                self.assertEqual("draft", saved["status"])
                self.assertFalse(saved["effects_allowed"])
                self.assertEqual([], saved["invocations"])
                self.assertEqual([], saved["acceptances"])

    def test_terminal_output_neutralizes_untrusted_control_sequences(self) -> None:
        from goal_native.cli import _terminal_text

        self.assertEqual(
            "safe[2J]52;c;clipboard\n\ttext",
            _terminal_text("safe\x1b[2J\x1b]52;c;clipboard\x07\n\ttext\r\x9b"),
        )

    def test_parser_errors_and_nonfinite_max_time_are_json(self) -> None:
        invalid_command = self.run_cli("not-a-command", expected=2)
        self.assertEqual("ArgumentError", invalid_command["type"])
        self.assertIn("invalid choice", invalid_command["error"])
        missing_argument = self.run_cli("run", expected=2)
        self.assertEqual("ArgumentError", missing_argument["type"])
        self.assertIn("required: goal_id", missing_argument["error"])
        invalid_option = self.run_cli("run", "goal", "--unknown-option", expected=2)
        self.assertEqual("ArgumentError", invalid_option["type"])
        self.assertIn("unrecognized arguments", invalid_option["error"])

        for value in ("nan", "inf", "-inf"):
            invalid_time = self.run_cli("run", f"--max-time={value}", "goal", expected=2)
            self.assertEqual("ArgumentError", invalid_time["type"])
            self.assertIn("positive finite number", invalid_time["error"])

    def test_export_is_private_and_leaves_no_predictable_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-cli-export-") as temporary:
            root = Path(temporary)
            state = root / "state"
            export_file = root / "workspace.json"
            self.run_cli("create", "--state", str(state), "Export securely")
            self.run_cli("export", "--state", str(state), "--output", str(export_file))
            self.assertEqual(0o600, stat.S_IMODE(export_file.stat().st_mode))
            self.assertEqual([], list(root.glob(f".{export_file.name}.tmp-*")))

    def test_setup_interrupt_reports_that_no_run_started(self) -> None:
        from goal_native import cli as cli_module

        with tempfile.TemporaryDirectory(prefix="goal-native-cli-setup-") as temporary:
            state = Path(temporary) / "state"
            created = self.run_cli("create", "--state", str(state), "Setup cancellation")
            before = self.run_cli("show", "--state", str(state), created["id"])
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"CLI_SETUP_INTERRUPT_KEY": "fixture"}, clear=False),
                patch.object(cli_module, "Sandbox"),
                patch.object(cli_module, "_stage_inputs", side_effect=KeyboardInterrupt),
                contextlib.redirect_stdout(output),
            ):
                status = cli_module.main(
                    [
                        "run",
                        "--state",
                        str(state),
                        "--provider", "openai",
                        "--model",
                        "fixture-model",
                        "--api-key-env",
                        "CLI_SETUP_INTERRUPT_KEY",
                        created["id"],
                    ]
                )
            self.assertEqual(130, status)
            cancelled = json.loads(output.getvalue())
            self.assertEqual("cancelled", cancelled["status"])
            self.assertFalse(cancelled["run_started"])
            self.assertIn("no run started", cancelled["result"])
            after = self.run_cli("show", "--state", str(state), created["id"])
            self.assertEqual(before["status"], after["status"])
            self.assertEqual([], after["invocations"])

    def test_staging_rechecks_opened_sizes_and_records_copied_bytes(self) -> None:
        from goal_native import cli as cli_module

        with tempfile.TemporaryDirectory(prefix="goal-native-cli-stage-") as temporary:
            root = Path(temporary)
            source = root / "source"
            stage = root / "stage"
            source.mkdir()
            stage.mkdir()
            growing = source / "growing.txt"
            shrinking = source / "shrinking.txt"
            growing.write_bytes(b"safe")
            shrinking.write_bytes(b"safe")
            real_open = cli_module.os.open

            def mutate_before_open(path: object, flags: int, mode: int = 0o777) -> int:
                if Path(path).resolve() == growing.resolve():
                    growing.write_bytes(b"grown")
                elif Path(path).resolve() == shrinking.resolve():
                    shrinking.write_bytes(b"ok")
                return real_open(path, flags, mode)

            with (
                patch.object(cli_module, "_MAX_SOURCE_FILE_BYTES", 4),
                patch.object(cli_module.os, "open", side_effect=mutate_before_open),
            ):
                counts = cli_module._copy_selected_directory(str(source), stage)
            self.assertEqual(1, counts["files"])
            self.assertEqual(2, counts["bytes"])
            self.assertEqual(1, counts["excluded"])
            self.assertFalse((stage / "growing.txt").exists())
            self.assertEqual(b"ok", (stage / "shrinking.txt").read_bytes())

    def test_import_rejects_symlinks_and_bounds_file_and_stdin_input(self) -> None:
        from goal_native import cli as cli_module

        with tempfile.TemporaryDirectory(prefix="goal-native-cli-import-") as temporary:
            root = Path(temporary)
            state = root / "state"
            valid = root / "valid.json"
            link = root / "link.json"
            oversized = root / "oversized.json"
            valid.write_text("{}", encoding="utf-8")
            link.symlink_to(valid)
            rejected = self.run_cli("import", "--state", str(state), str(link), expected=1)
            self.assertIn("regular, non-symlink", rejected["error"])
            oversized.write_bytes(b"x" * 17)

            for arguments, standard_input in (
                (["import", "--state", str(state), str(oversized)], None),
                (["import", "--state", str(state), "-"], io.BytesIO(b"x" * 17)),
            ):
                output = io.StringIO()
                with (
                    patch.object(cli_module, "_MAX_IMPORT_BYTES", 16),
                    patch.object(cli_module.sys, "stdin", standard_input),
                    contextlib.redirect_stdout(output),
                ):
                    status = cli_module.main(arguments)
                self.assertEqual(1, status)
                payload = json.loads(output.getvalue())
                self.assertIn("exceeds 16 bytes", payload["error"])


if __name__ == "__main__":
    unittest.main()
