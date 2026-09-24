from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import goal_native.sandbox as sandbox_module
from goal_native.sandbox import Sandbox, SandboxError, SandboxUnavailable


MACOS = sys.platform == "darwin"



@unittest.skipUnless(MACOS, "macOS enforcement is required for sandbox tests")
class SandboxTests(unittest.TestCase):
    def test_multifile_execution_imports_only_selected_project_and_preserves_script_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            tools = root / "tools"
            tools.mkdir()
            (root / "totals.py").write_text("VALUE = 30\n", encoding="utf-8")
            (tools / "helper.py").write_text("EXTRA = 12\n", encoding="utf-8")
            (tools / "data.txt").write_text("staged data", encoding="utf-8")
            (tools / "check.py").write_text(
                "from pathlib import Path\n"
                "from totals import VALUE\n"
                "from helper import EXTRA\n"
                "import sys\n"
                "print(VALUE + EXTRA)\n"
                "print(Path(__file__).with_name('data.txt').read_text())\n"
                "print(sys.argv[1])\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"PYTHONPATH": "/unselected/host/path"}):
                sandbox = Sandbox(root)
                try:
                    result = sandbox.run({"path": "tools/check.py", "args": ["argument"]})
                finally:
                    sandbox.close()
            self.assertEqual(0, result["exit_code"], result["stderr"])
            self.assertEqual("42\nstaged data\nargument\n", result["stdout"])

    def test_execution_denies_secret_names_at_root_and_nested_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "check.py").write_text(
                "from pathlib import Path\n"
                "Path('nested').mkdir()\n"
                "for name in ['.env', '.ENV', 'nested/.eNv', 'nested/credentials.json', 'nested/key.pem']:\n"
                "    try:\n"
                "        with open(name, 'w') as handle: handle.write('synthetic')\n"
                "    except PermissionError:\n"
                "        print(name + ':DENIED')\n"
                "    else:\n"
                "        raise AssertionError('environment path was writable')\n",
                encoding="utf-8",
            )
            sandbox = Sandbox(root)
            try:
                result = sandbox.run({"path": "check.py"})
            finally:
                sandbox.close()
            self.assertEqual(0, result["exit_code"], result["stderr"])
            self.assertEqual(".env:DENIED\n.ENV:DENIED\nnested/.eNv:DENIED\n"
                             "nested/credentials.json:DENIED\nnested/key.pem:DENIED\n", result["stdout"])

    def test_execution_uses_fixed_enforcer_and_trusted_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            fake_enforcer = fake_bin / "sandbox-exec"
            fake_enforcer.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            fake_enforcer.chmod(0o700)
            original_path = os.environ.get("PATH")
            sandbox = None
            try:
                os.environ["PATH"] = str(fake_bin)
                sandbox = Sandbox(root)
                self.assertEqual(sandbox.sandbox_exec, "/usr/bin/sandbox-exec")
                with self.assertRaises(SandboxError):
                    Sandbox(root, interpreter="/bin/sh")
            finally:
                if sandbox is not None:
                    sandbox.close()
                if original_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = original_path

    def test_execution_fails_closed_when_stage_path_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            (root / "probe.py").write_text("print('should not run')\n", encoding="utf-8")
            sandbox = Sandbox(root)
            moved = Path(directory) / "moved-stage"
            root.rename(moved)
            root.mkdir()
            try:
                with self.assertRaises(SandboxError):
                    sandbox.run({"path": "probe.py", "args": []})
            finally:
                sandbox.close()
    def test_staged_paths_use_no_follow_descriptor_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "stage"
            root.mkdir()
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            outside = base / "outside.txt"
            outside.write_text("private", encoding="utf-8")
            outside_dir = base / "outside-dir"
            outside_dir.mkdir()
            (outside_dir / "secret.txt").write_text("private-dir", encoding="utf-8")
            (root / "link.txt").symlink_to(outside)
            (root / "link-dir").symlink_to(outside_dir, target_is_directory=True)

            sandbox = Sandbox(root)
            self.assertEqual(sandbox.read({"path": "safe.txt"})["content"], "safe")
            self.assertEqual(
                sandbox.write({"path": "new/deep.txt", "content": "inside"})["bytes"],
                6,
            )
            self.assertEqual((root / "new/deep.txt").read_text(encoding="utf-8"), "inside")

            for operation in (
                lambda: sandbox.read({"path": "../outside.txt"}),
                lambda: sandbox.read({"path": "link.txt"}),
                lambda: sandbox.read({"path": "link-dir/secret.txt"}),
                lambda: sandbox.write({"path": "link.txt", "content": "escape"}),
                lambda: sandbox.write({"path": "link-dir/new.txt", "content": "escape"}),
                lambda: sandbox.write({"path": ".env", "content": "no"}),
            ):
                with self.assertRaises(SandboxError):
                    operation()
            self.assertEqual(outside.read_text(encoding="utf-8"), "private")
            self.assertFalse((outside_dir / "new.txt").exists())

    def test_literal_search_is_bounded_and_reports_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            (root / "code.txt").write_text("a.b\naxb\n", encoding="utf-8")
            (root / "large.txt").write_text("needle" * 300, encoding="utf-8")
            (root / ".env.local").write_text("a.b", encoding="utf-8")
            (root / "link.txt").symlink_to(root / "code.txt")

            sandbox = Sandbox(root)
            result = sandbox.search({"query": "a.b", "max_results": 10})
            self.assertEqual(
                [(match["path"], match["line"]) for match in result["matches"]],
                [("code.txt", 1)],
            )
            self.assertFalse(result["truncated"])
            self.assertTrue(result["scope"]["literal"])
            self.assertEqual(result["snapshot"]["kind"], "opened-file-manifest")
            self.assertIn("id", result["snapshot"])
            self.assertGreaterEqual(result["exclusions"]["blocked"], 1)
            self.assertGreaterEqual(result["exclusions"]["symlinks"], 1)

            result_limited = sandbox.search({"query": "a", "max_results": 1})
            self.assertTrue(result_limited["truncated"])
            self.assertFalse(result_limited["snapshot"]["truncated"])
            self.assertEqual(result_limited["exclusions"]["result_limit"], 1)

            limited = sandbox.search(
                {"query": "needle", "max_scan_bytes": 10, "max_file_bytes": 5000}
            )
            self.assertTrue(limited["truncated"])
            self.assertGreaterEqual(limited["exclusions"]["scan_bytes_limit"], 1)
            self.assertEqual(limited["matches"], [])

    def test_search_snapshot_changes_when_a_new_file_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            (root / "before.txt").write_text("before", encoding="utf-8")
            sandbox = Sandbox(root)

            first = sandbox.search({"query": "new"})
            (root / "after.txt").write_text("new", encoding="utf-8")
            second = sandbox.search({"query": "new"})

            self.assertEqual(first["matches"], [])
            self.assertNotEqual(first["snapshot"]["id"], second["snapshot"]["id"])
            self.assertEqual(
                [(match["path"], match["line"]) for match in second["matches"]],
                [("after.txt", 1)],
            )
            self.assertEqual(first["snapshot"]["files"], 1)
            self.assertEqual(second["snapshot"]["files"], 2)

    def test_read_and_write_limits_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            (root / "large.txt").write_text("0123456789", encoding="utf-8")
            sandbox = Sandbox(root)

            with self.assertRaises(SandboxError):
                sandbox.read({"path": "large.txt", "max_bytes": 5})
            with self.assertRaises(SandboxError):
                sandbox.write({"path": "new.txt", "content": "0123456789", "max_bytes": 5})

    def test_constructor_and_run_input_limits_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            (root / "noop.py").write_text("print('ok')\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Sandbox(root, timeout=301)
            with self.assertRaises(ValueError):
                Sandbox(root, max_output_bytes=1 * 1024 * 1024 + 1)

            large = root / "large.bin"
            with large.open("wb") as handle:
                handle.truncate(64 * 1024 * 1024 + 1)
            sandbox = Sandbox(root)
            try:
                with self.assertRaises(SandboxError):
                    sandbox.write({"path": "too-large.txt", "content": "é", "max_bytes": 1})
                with self.assertRaises(SandboxError):
                    sandbox.run({"path": "noop.py", "args": ["x"] * 65})
                with self.assertRaises(SandboxError):
                    sandbox.run({"path": "noop.py", "args": []})
            finally:
                sandbox.close()

    def test_overlapping_runs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            marker = root / "running"
            (root / "wait.py").write_text(
                "from pathlib import Path\n"
                "import time\n"
                "Path('running').write_text('yes')\n"
                "time.sleep(0.5)\n",
                encoding="utf-8",
            )
            sandbox = Sandbox(root, timeout=2)
            result: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def invoke() -> None:
                try:
                    result.append(sandbox.run({"path": "wait.py", "args": []}))
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=invoke)
            thread.start()
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            try:
                self.assertTrue(marker.exists())
                with self.assertRaises(SandboxError):
                    sandbox.run({"path": "wait.py", "args": []})
            finally:
                thread.join(timeout=3)
                sandbox.close()
            self.assertEqual(errors, [])
            self.assertEqual(len(result), 1)
    def test_actual_os_boundary_denies_host_io_network_fork_and_exec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "stage"
            root.mkdir()
            (root / ".env").write_text("environment-secret", encoding="utf-8")
            (root / "controller").write_text("controller-secret", encoding="utf-8")
            (root / ".ENV").write_text("uppercase-environment-secret", encoding="utf-8")
            outside = base / "outside.txt"
            detached = base / "detached.txt"
            script = root / "probe.py"
            script.write_text(
                f"""
import os
import socket
import subprocess

try:
    open('/etc/hosts', 'rb').read(1)
    print('HOST_READ_ALLOWED')
except BaseException as exc:
    print('HOST_READ_DENIED', type(exc).__name__)
try:
    open({str(outside)!r}, 'w').write('escape')
    print('HOST_WRITE_ALLOWED')
except BaseException as exc:
    print('HOST_WRITE_DENIED', type(exc).__name__)
try:
    socket.create_connection(('127.0.0.1', 9), timeout=0.1)
    print('NETWORK_ALLOWED')
except BaseException as exc:
    print('NETWORK_DENIED', type(exc).__name__)
for target in ('.env', 'controller', '.ENV'):
    try:
        open(target, 'rb').read(1)
        print('STAGE_READ_ALLOWED', target)
    except BaseException as exc:
        print('STAGE_READ_DENIED', target, type(exc).__name__)
    try:
        open(target, 'w').write('blocked')
        print('STAGE_WRITE_ALLOWED', target)
    except BaseException as exc:
        print('STAGE_WRITE_DENIED', target, type(exc).__name__)
print('SECRET_ENV', bool(os.environ.get('OPENAI_API_KEY')))
try:
    child = os.fork()
    if child == 0:
        open({str(detached)!r}, 'w').write('detached')
        os._exit(0)
    os.waitpid(child, 0)
    print('FORK_ALLOWED')
except BaseException as exc:
    print('FORK_DENIED', type(exc).__name__)
try:
    subprocess.run(['/bin/echo', 'escape'], check=True)
    print('EXEC_ALLOWED')
except BaseException as exc:
    print('EXEC_DENIED', type(exc).__name__)
open('inside.txt', 'w').write('staged')
""".strip()
                + "\n",
                encoding="utf-8",
            )

            result = Sandbox(root).run({"path": "probe.py", "args": []})
            output = result["stdout"]
            self.assertEqual(result["enforcement"], "macos-sandbox-exec")
            self.assertEqual(result["exit_code"], 0, result)
            self.assertIn("HOST_READ_DENIED", output)
            self.assertIn("HOST_WRITE_DENIED", output)
            self.assertIn("SECRET_ENV False", output)
            self.assertIn("STAGE_READ_DENIED .env", output)
            self.assertIn("STAGE_WRITE_DENIED .env", output)
            self.assertIn("STAGE_READ_DENIED controller", output)
            self.assertIn("STAGE_WRITE_DENIED controller", output)
            self.assertIn("STAGE_READ_DENIED .ENV", output)
            self.assertIn("STAGE_WRITE_DENIED .ENV", output)
            self.assertIn("NETWORK_DENIED", output)
            self.assertIn("FORK_DENIED", output)
            self.assertIn("EXEC_DENIED", output)
            self.assertTrue((root / "inside.txt").exists())
            time.sleep(0.05)
            self.assertFalse(outside.exists())
            self.assertFalse(detached.exists())

    def test_output_and_runtime_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            (root / "output.py").write_text(
                "import sys\nsys.stdout.write('x' * 100000)\n",
                encoding="utf-8",
            )
            sandbox = Sandbox(root, timeout=2, max_output_bytes=1024)
            result = sandbox.run({"path": "output.py", "args": []})
            self.assertTrue(result["output_limited"], result)
            self.assertLessEqual(len(result["stdout"]) + len(result["stderr"]), 1024)

            (root / "timeout.py").write_text("while True:\n    pass\n", encoding="utf-8")
            timed = sandbox.run({"path": "timeout.py", "args": [], "timeout_seconds": 0.1})
            self.assertTrue(timed["timed_out"], timed)
            self.assertNotEqual(timed["exit_code"], 0)



@unittest.skipUnless(sys.version_info >= (3, 11), "Python 3.11+ is required")
class SandboxRepositoryToolsTests(unittest.TestCase):
    def _sandbox(self, root: Path) -> Sandbox:
        sandbox = Sandbox(root, require_os_sandbox=False)
        self.addCleanup(sandbox.close)
        return sandbox

    def test_read_ranges_hash_complete_unicode_and_eof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "unicode.txt"
            source = "é🙂\nlast line\nEOF"
            path.write_text(source, encoding="utf-8")
            sandbox = self._sandbox(root)
            expected_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

            first = sandbox.read({"path": "unicode.txt", "start_line": 1, "end_line": 1})
            self.assertEqual("é🙂\n", first["content"])
            self.assertEqual(len("é🙂\n".encode("utf-8")), first["bytes"])
            self.assertEqual(expected_hash, first["sha256"])
            self.assertEqual(len(source.encode("utf-8")), first["file_bytes"])
            self.assertEqual(3, first["line_count"])
            self.assertTrue(first["truncated"])
            self.assertEqual(
                {"path": "unicode.txt", "start_line": 2, "sha256": expected_hash},
                first["continuation"],
            )

            tail = sandbox.read({"path": "unicode.txt", "start_line": 2, "end_line": 99})
            self.assertEqual("last line\nEOF", tail["content"])
            self.assertTrue(tail["truncated"])
            self.assertIsNone(tail["continuation"])

            eof = sandbox.read({"path": "unicode.txt", "start_line": 4})
            self.assertEqual("", eof["content"])
            self.assertEqual(3, eof["range"]["end_line"])
            self.assertTrue(eof["truncated"])

    def test_new_tool_integer_limits_reject_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("value", encoding="utf-8")
            sandbox = self._sandbox(root)
            with self.assertRaises(ValueError):
                sandbox.read({"path": "file.txt", "start_line": True})
            with self.assertRaises(ValueError):
                sandbox.discover({"max_entries": True})
            with self.assertRaises(ValueError):
                sandbox.regex_search({"pattern": "value", "timeout_seconds": True})
            digest = hashlib.sha256(b"value").hexdigest()
            with self.assertRaises(ValueError):
                sandbox.edit({
                    "path": "file.txt",
                    "expected_sha256": digest,
                    "edits": [{
                        "start_line": True,
                        "start_column": 1,
                        "end_line": 1,
                        "end_column": 1,
                        "replacement": "x",
                    }],
                })

    def test_discovery_is_bounded_and_excludes_blocked_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            (root / "link.txt").symlink_to(root / "a.txt")
            sandbox = self._sandbox(root)

            complete = sandbox.discover({"path": "."})
            self.assertEqual(["a.txt", "b.txt"], [item["path"] for item in complete["files"]])
            self.assertFalse(complete["truncated"])
            self.assertGreaterEqual(complete["exclusions"]["symlinks"], 1)

            limited = sandbox.discover({"path": ".", "max_files": 1})
            self.assertEqual(["a.txt"], [item["path"] for item in limited["files"]])
            self.assertTrue(limited["truncated"])
            self.assertEqual(1, limited["scope"]["max_files"])

    def test_regex_search_rejects_invalid_and_kills_expensive_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.txt").write_text("value=42\nother\n", encoding="utf-8")
            sandbox = self._sandbox(root)
            result = sandbox.regex_search({"pattern": r"value=\d+"})
            self.assertEqual([("code.txt", 1)], [
                (match["path"], match["line"]) for match in result["matches"]
            ])
            self.assertTrue(result["scope"]["regex"])
            with self.assertRaises(ValueError):
                sandbox.regex_search({"pattern": "["})

            (root / "expensive.txt").write_text("a" * 20_000 + "!", encoding="utf-8")
            with self.assertRaises(SandboxError):
                sandbox.regex_search({
                    "pattern": r"(a+)+$",
                    "path": ".",
                    "timeout_seconds": 0.2,
                })

    def test_regex_cancellation_reaps_helper_and_releases_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.txt").write_text("a" * 20_000 + "!", encoding="utf-8")
            sandbox = self._sandbox(root)
            ready = threading.Event()
            processes = []
            errors = []
            real_popen = sandbox_module.subprocess.Popen

            def capture(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                ready.set()
                return process

            def search():
                try:
                    sandbox.regex_search({"pattern": r"(a+)+$", "timeout_seconds": 10})
                except BaseException as error:
                    errors.append(error)

            with patch.object(sandbox_module.subprocess, "Popen", side_effect=capture):
                thread = threading.Thread(target=search)
                thread.start()
                try:
                    self.assertTrue(ready.wait(5), "regex helper did not start")
                    sandbox.cancel()
                    thread.join(2)
                    self.assertFalse(thread.is_alive(), "cancelled regex remained active")
                    self.assertIsInstance(errors[0], SandboxError)
                    self.assertIsNotNone(processes[0].poll())
                finally:
                    for process in processes:
                        if process.poll() is None:
                            sandbox_module.Sandbox._kill(process)
                    thread.join(5)
            result = sandbox.regex_search({"pattern": "^a+"})
            self.assertEqual(result["matches"][0]["line"], 1)

    def test_edits_require_fresh_hash_and_are_all_or_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "edit.txt"
            source = "αβ\nsame\nsame\nEOF"
            path.write_text(source, encoding="utf-8")
            sandbox = self._sandbox(root)
            expected = hashlib.sha256(source.encode("utf-8")).hexdigest()

            with self.assertRaises(SandboxError):
                sandbox.edit({
                    "path": "edit.txt",
                    "expected_sha256": "0" * 64,
                    "edits": [{"old_text": "αβ", "replacement": "changed"}],
                })
            self.assertEqual(source, path.read_text(encoding="utf-8"))

            with self.assertRaises(SandboxError):
                sandbox.edit({
                    "path": "edit.txt",
                    "expected_sha256": expected,
                    "edits": [{"old_text": "same", "replacement": "different"}],
                })
            self.assertEqual(source, path.read_text(encoding="utf-8"))

            with self.assertRaises(SandboxError):
                sandbox.edit({
                    "path": "edit.txt",
                    "expected_sha256": expected,
                    "edits": [
                        {
                            "start_line": 1,
                            "start_column": 1,
                            "end_line": 1,
                            "end_column": 3,
                            "replacement": "x",
                            "old_text": "αβ",
                        },
                        {
                            "start_line": 1,
                            "start_column": 2,
                            "end_line": 1,
                            "end_column": 3,
                            "replacement": "y",
                            "old_text": "β",
                        },
                    ],
                })
            self.assertEqual(source, path.read_text(encoding="utf-8"))

            edited = sandbox.edit({
                "path": "edit.txt",
                "expected_sha256": expected,
                "edits": [
                    {
                        "start_line": 1,
                        "start_column": 2,
                        "end_line": 1,
                        "end_column": 3,
                        "replacement": "🙂",
                        "old_text": "β",
                    },
                    {
                        "start_line": 4,
                        "start_column": 4,
                        "end_line": 4,
                        "end_column": 4,
                        "replacement": "!",
                    },
                ],
            })
            self.assertTrue(edited["written"])
            self.assertEqual("α🙂\nsame\nsame\nEOF!", path.read_text(encoding="utf-8"))
            self.assertEqual(
                edited["sha256"],
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )

    def test_edit_rejects_symlink_and_blocked_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "stage"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / "link.txt").symlink_to(outside)
            sandbox = self._sandbox(root)
            with self.assertRaises(SandboxError):
                sandbox.edit({
                    "path": "link.txt",
                    "expected_sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    "edits": [{"old_text": "outside", "replacement": "escape"}],
                })
            with self.assertRaises(SandboxError):
                sandbox.edit({
                    "path": ".env",
                    "expected_sha256": "0" * 64,
                    "edits": [{"old_text": "x", "replacement": "y"}],
                })


class SandboxUnavailableTests(unittest.TestCase):
    def test_execution_fails_closed_if_enforcement_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = sandbox_module._SANDBOX_EXECUTABLE
            try:
                sandbox_module._SANDBOX_EXECUTABLE = str(root / "missing-sandbox-exec")
                with self.assertRaises(SandboxUnavailable):
                    Sandbox(root)
            finally:
                sandbox_module._SANDBOX_EXECUTABLE = original


if __name__ == "__main__":
    unittest.main()
