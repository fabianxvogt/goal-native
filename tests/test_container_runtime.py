"""Docker runtime checks.

After building the image from ``runtime/Dockerfile``, run the integration class
with ``GOAL_NATIVE_RUNTIME_IMAGE_ID=$(docker image inspect --format '{{.Id}}'
goal-native-runtime:local)``.  It exercises Python, Node and Node's TypeScript
strip-types path, source isolation, environment/network denial, timeout and
cancellation, bounded resources, safe output rejection, and snapshot identity.
A parent-death smoke should start a long-running command in a separate process,
then terminate that controller process; the container's stdin guardian and
independent deadline must leave no descendant container or task process.
"""
from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from goal_native import workspace
from goal_native.container_runtime import (
    ContainerLimits,
    ContainerRuntimeError,
    MAX_ARCHIVE_BYTES,
    extract_candidate_archive,
    snapshot_archive,
    validate_argv,
    validate_image_id,
)
from goal_native.container_runner import ContainerRuntime
from goal_native.language_tools import LanguageTools


class ContainerPolicyTests(unittest.TestCase):
    def test_limits_and_image_ids_are_bounded(self) -> None:
        with self.assertRaises(ValueError):
            validate_image_id("goal-native-runtime:local")
        with self.assertRaises(ValueError):
            ContainerLimits(cpus=9)
        with self.assertRaises(ValueError):
            validate_argv("python3")

    def test_snapshot_archive_preserves_unicode_executable_and_rejects_unsafe_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage"
            root.mkdir()
            source = root / "café.py"
            source.write_text("print(7)\n", encoding="utf-8")
            source.chmod(0o755)
            before, selection = workspace.snapshot_directory(root, strict=True)
            archive = snapshot_archive(before, max_bytes=MAX_ARCHIVE_BYTES)
            target = Path(directory) / "candidate"
            after, _ = extract_candidate_archive(
                archive,
                target,
                max_bytes=MAX_ARCHIVE_BYTES,
                tracked=before,
                excluded=selection["exclusions"],
            )
            self.assertEqual(workspace.snapshot_hash(before), workspace.snapshot_hash(after))
            self.assertEqual("100755", after["café.py"]["mode"])

            unsafe = io.BytesIO()
            with tarfile.open(fileobj=unsafe, mode="w") as output:
                link = tarfile.TarInfo("escape")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                output.addfile(link)
            with self.assertRaises(ContainerRuntimeError):
                extract_candidate_archive(
                    unsafe.getvalue(),
                    Path(directory) / "unsafe",
                    max_bytes=MAX_ARCHIVE_BYTES,
                )



IMAGE_ID = os.environ.get("GOAL_NATIVE_RUNTIME_IMAGE_ID")
DOCKER_INTEGRATION = bool(IMAGE_ID and shutil.which("docker"))


@unittest.skipUnless(DOCKER_INTEGRATION, "set GOAL_NATIVE_RUNTIME_IMAGE_ID after building the pinned image")
class DockerRuntimeTests(unittest.TestCase):
    """These are live local-engine checks, never a remote or privileged fallback."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="goal-native-container-test-")
        self.addCleanup(self.temporary.cleanup)
        self.stage = Path(self.temporary.name).resolve() / "stage"
        self.stage.mkdir()
        self.runtime = ContainerRuntime(self.stage, image=IMAGE_ID or "")
        self.addCleanup(self.runtime.close)

    def _start_ready_command(self, script: str):
        """Wait for the actual task marker, never infer startup from a delay."""
        named = threading.Event()
        names: list[str] = []
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []
        original_read = self.runtime._read_process

        def observe(process, **arguments):
            names.append(arguments["name"])
            named.set()
            return original_read(process, **arguments)

        observer = patch.object(self.runtime, "_read_process", side_effect=observe)
        observer.start()
        self.addCleanup(observer.stop)

        def run():
            try:
                results.append(self.runtime.command({"argv": ["python3", "-c", script], "timeout_seconds": 30}))
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()

        def stop():
            self.runtime.cancel()
            thread.join(timeout=10)

        self.addCleanup(stop)
        self.assertTrue(named.wait(10), "container command did not start")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready = self.runtime._docker_call(["exec", names[0], "test", "-f", "/workspace/task.ready"])
            if ready.returncode == 0:
                return thread, results, errors, names[0]
            self.assertTrue(thread.is_alive(), f"task stopped before readiness: {errors}")
            time.sleep(0.02)
        self.fail("task did not reach its readiness marker")

    def test_empty_candidate_can_create_its_first_file(self) -> None:
        result = self.runtime.command({
            "argv": ["python3", "-c", "from pathlib import Path; Path('first.txt').write_text('created')"],
        })
        self.assertEqual(0, result["exit_code"])
        self.assertEqual("created", (self.stage / "first.txt").read_text())

    def test_cancellation_before_command_admission_cannot_start_a_task(self) -> None:
        self.runtime.cancel()
        with self.assertRaises(ContainerRuntimeError):
            self.runtime.command({"argv": ["python3", "-c", "open('ran.txt', 'w').write('ran')"]})
        self.assertFalse((self.stage / "ran.txt").exists())

    def test_command_can_replace_files_with_directories_and_back(self) -> None:
        (self.stage / "module").mkdir()
        (self.stage / "module/old.py").write_text("old")
        (self.stage / "flat").write_text("old")
        result = self.runtime.command({"argv": ["python3", "-c",
            "import shutil; from pathlib import Path; shutil.rmtree('module'); "
            "Path('module').write_text('file'); Path('flat').unlink(); Path('flat').mkdir(); "
            "Path('flat/new.py').write_text('nested')"]})
        self.assertEqual(0, result["exit_code"])
        self.assertEqual("file", (self.stage / "module").read_text())
        self.assertEqual("nested", (self.stage / "flat/new.py").read_text())

    def test_python_node_typescript_source_isolation_and_default_denials(self) -> None:
        host_marker = Path(self.temporary.name) / "host-only.txt"
        host_marker.write_text("controller", encoding="utf-8")
        self.stage.joinpath("input.py").write_text("print('python-ok')\n", encoding="utf-8")
        python_result = self.runtime.command({"argv": ["python3", "input.py"]})
        self.assertEqual(0, python_result["exit_code"])
        self.assertIn("python-ok", python_result["stdout"])

        node_result = self.runtime.command({
            "argv": ["node", "-e", "console.log('node-ok')"],
        })
        self.assertEqual(0, node_result["exit_code"])
        self.assertIn("node-ok", node_result["stdout"])

        self.stage.joinpath("input.ts").write_text("const value: number = 3; console.log(value)\n", encoding="utf-8")
        ts_result = self.runtime.command({
            "argv": ["node", "input.ts"],
        })
        self.assertEqual(0, ts_result["exit_code"])
        self.assertIn("3", ts_result["stdout"])

        probe = (
            "import os, socket\n"
            "from pathlib import Path\n"
            f"print('HOST_ISOLATED', not Path({str(host_marker)!r}).exists())\n"
            "print('SENTINEL_ISOLATED', 'GOAL_NATIVE_TEST_SENTINEL' not in os.environ)\n"
            "try:\n socket.create_connection(('example.com', 80), 0.5)\n print('NETWORK_ALLOWED')\n"
            "except OSError:\n print('NETWORK_DENIED')\n"
        )
        with patch.dict(os.environ, {"GOAL_NATIVE_TEST_SENTINEL": "synthetic-host-only-value"}):
            result = self.runtime.command({"argv": ["python3", "-c", probe]})
        self.assertIn("HOST_ISOLATED True", result["stdout"])
        self.assertIn("SENTINEL_ISOLATED True", result["stdout"])
        self.assertIn("NETWORK_DENIED", result["stdout"])
        self.assertEqual("none", result["environment"]["network"])

    def test_timeout_cancel_and_descendants_are_bounded(self) -> None:
        timed = self.runtime.command({
            "argv": [
                "python3",
                "-c",
                "import subprocess, time; subprocess.Popen(['python3', '-c', 'import time; time.sleep(30)']); time.sleep(30)",
            ],
            "timeout_seconds": 0.3,
        })
        self.assertTrue(timed["timed_out"])

        thread, result, errors, name = self._start_ready_command(
            "import subprocess, time; from pathlib import Path; "
            "subprocess.Popen(['python3', '-c', 'import time; time.sleep(30)'], start_new_session=True); "
            "Path('task.ready').touch(); time.sleep(30)"
        )
        self.runtime.cancel()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(result and result[0]["cancelled"])
        removed = self.runtime._docker_call(["container", "inspect", name])
        self.assertNotEqual(0, removed.returncode)

    def test_limits_candidate_identity_deletion_mode_and_no_publish(self) -> None:
        self.stage.joinpath("keep.txt").write_text("old", encoding="utf-8")
        self.stage.joinpath("remove.txt").write_text("delete", encoding="utf-8")
        self.stage.joinpath("run.py").write_text("print('run')\n", encoding="utf-8")
        self.stage.joinpath("run.py").chmod(0o755)
        script = (
            "from pathlib import Path\n"
            "Path('keep.txt').write_text('new', encoding='utf-8')\n"
            "Path('remove.txt').unlink()\n"
            "Path('新.py').write_text('print(1)\\n', encoding='utf-8')\n"
        )
        result = self.runtime.command({"argv": ["python3", "-c", script]})
        self.assertEqual(result["before_snapshot"]["id"], result["before"]["candidate"])
        self.assertEqual(result["image"], result["before"]["image"])
        self.assertNotEqual(result["before_snapshot"]["id"], result["after_snapshot"]["id"])
        self.assertEqual(
            "deleted",
            next(item for item in result["candidate"]["changes"] if item["path"] == "remove.txt")["status"],
        )
        self.assertTrue((self.stage / "新.py").exists())
        self.assertFalse((self.stage / "remove.txt").exists())
        self.assertTrue(self.stage.joinpath("run.py").stat().st_mode & stat.S_IXUSR)

        before = (self.stage / "keep.txt").read_text(encoding="utf-8")
        dry = self.runtime.command({
            "argv": ["python3", "-c", "from pathlib import Path; Path('keep.txt').write_text('dry')"],
        }, publish=False)
        self.assertFalse(dry["published"])
        self.assertEqual(before, (self.stage / "keep.txt").read_text(encoding="utf-8"))

    def test_symlink_output_and_host_divergence_fail_before_publication(self) -> None:
        original = self.stage.joinpath("safe.txt")
        original.write_text("safe", encoding="utf-8")
        with self.assertRaises(ContainerRuntimeError):
            self.runtime.command({
                "argv": ["python3", "-c", "import os; os.symlink('/etc/passwd', 'escape')"],
            })
        self.assertFalse((self.stage / "escape").exists())

        mutating, _, errors, name = self._start_ready_command(
            "import time\nfrom pathlib import Path\n"
            "Path('task.ready').touch()\n"
            "while not Path('release').exists(): time.sleep(0.01)\n"
            "Path('safe.txt').write_text('worker')\n"
        )
        original.write_text("host-change", encoding="utf-8")
        released = self.runtime._docker_call(["exec", "--user", "65532:65532", name, "touch", "/workspace/release"])
        self.assertEqual(0, released.returncode, released.stderr)
        mutating.join(timeout=5)
        self.assertFalse(mutating.is_alive())
        self.assertTrue(errors and isinstance(errors[0], ContainerRuntimeError))
        self.assertEqual("host-change", original.read_text(encoding="utf-8"))

    def test_cold_typescript_navigation_and_semantic_diagnostics(self) -> None:
        (self.stage / "src").mkdir()
        (self.stage / "src/calc.ts").write_text(
            "export function add(a: number, b: number): number { return a + b; }\n", encoding="utf-8",
        )
        line = 'const label = "😀"; const result: string = add(2, 3);'
        (self.stage / "main.ts").write_text(
            'import { add } from "./src/calc";\n' + line + "\nconsole.log(result);\n", encoding="utf-8",
        )
        before = workspace.snapshot_hash(workspace.snapshot_directory(self.stage, strict=True)[0])
        tools = LanguageTools(self.runtime)
        results = {
            action: tools.query({"action": action, "path": "main.ts", "line": 2, "column": line.index("add") + 1})
            for action in ("definition", "references", "diagnostics")
        }
        self.assertTrue(any(item["path"] == "src/calc.ts" for item in results["definition"]["results"]))
        self.assertTrue(any(
            item["path"] == "main.ts" and item["range"]["start"] == {"line": 2, "column": line.index("add") + 1}
            for item in results["references"]["results"]
        ))
        self.assertTrue(any(item.get("code") == 2322 and item.get("severity") == 1 for item in results["diagnostics"]["results"]))
        self.assertEqual(before, workspace.snapshot_hash(workspace.snapshot_directory(self.stage, strict=True)[0]))


if __name__ == "__main__":
    unittest.main()
