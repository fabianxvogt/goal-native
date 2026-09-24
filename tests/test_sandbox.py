from __future__ import annotations

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
