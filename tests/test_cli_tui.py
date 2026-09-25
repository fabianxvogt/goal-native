"""Real Pi editor/controller boundary checks, with a loopback protocol fixture."""
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
import fcntl
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from goal_native.store import Store
from goal_native.tui import PiTerminal, goal_indicator
from tests.test_cli_transport import StreamingFixture

ROOT = Path(__file__).resolve().parents[1]


class GoalStatusTests(unittest.TestCase):
    def test_latest_attempt_does_not_imply_liveness_or_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary, Store(temporary) as store:
            goal = store.create_goal("Keep work separate from acceptance")
            assignment = store.assign(goal["id"])
            first = store.start_run(goal["id"], assignment["id"])
            store.finish_run(first["id"], "finished", assistant_text="untrusted completion")
            summary = store.session_summaries()[0]
            self.assertEqual("draft", summary["status"])
            self.assertEqual("run finished / review", goal_indicator(summary)[0])
            second = store.start_run(goal["id"], assignment["id"])
            self.assertEqual("unfinished", goal_indicator(store.session_summaries()[0])[0])
            self.assertEqual("running", goal_indicator(store.session_summaries()[0], running=True)[0])
            store.finish_run(second["id"], "failed", stop_reason="provider_error")
            self.assertEqual("failed", goal_indicator(store.session_summaries()[0])[0])
            store.request(goal["id"], "Stop", control="cancel")
            self.assertEqual("cancelled", goal_indicator(store.session_summaries()[0])[0])
            self.assertEqual([], store.goal(goal["id"])["acceptances"])


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("node") and
                     (ROOT / "upstream/pi/packages/tui/dist/index.js").is_file(),
                     "requires the bootstrapped macOS Pi terminal runtime")
class PiTerminalTests(unittest.TestCase):
    def test_multiline_cancel_reopen_and_continue_without_new_request(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StreamingFixture)
        server.requests = []
        server.release = threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="goal-native-tui-test-") as temporary:
                state = Path(temporary) / "state"
                environment = dict(os.environ, TERM="xterm-256color", TERM_PROGRAM="xterm", NO_COLOR="",
                                   OPENAI_API_KEY="TRANSPORT_FIXTURE_NOT_A_SECRET",
                                   OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1")
                command = [sys.executable, "-m", "goal_native", "--state", str(state),
                           "--provider", "openai", "--model", "gpt-4.1-mini"]
                request = "Retain this exact request.\nKeep the second line too."
                for resumed in (False, True):
                    master, slave = pty.openpty()
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 48, 0, 0))
                    original_modes = termios.tcgetattr(slave)
                    process = subprocess.Popen(command, cwd=ROOT, env=environment,
                                               stdin=slave, stdout=slave, stderr=slave,
                                               start_new_session=True)
                    os.close(slave)
                    output = bytearray()
                    def until(predicate):
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            if predicate():
                                return
                            if process.poll() is not None:
                                self.fail(f"CLI exited {process.returncode}: {output.decode(errors='replace')}")
                            if select.select([master], [], [], .05)[0]:
                                try:
                                    output.extend(os.read(master, 65536))
                                except OSError:
                                    pass
                        self.fail(f"CLI condition timed out: {output.decode(errors='replace')}")
                    def send(text):
                        os.write(master, b"\x1b[200~" + text.encode() + b"\x1b[201~")
                        # Pi parses bracketed paste asynchronously; submit separately
                        # after the editor has rendered the pasted text.
                        until(lambda: text.splitlines()[-1].encode() in output)
                        os.write(master, b"\r")
                    def saved_status(status):
                        if not (state / "store.sqlite3").is_file():
                            return False
                        with Store(state) as store:
                            rows = store.session_summaries()
                            return bool(rows and rows[0]["last_run_status"] == status)
                    try:
                        until(lambda: b"Enter send" in output)
                        modes = termios.tcgetattr(master)
                        self.assertFalse(modes[3] & (termios.ICANON | termios.ECHO))
                        if resumed:
                            server.release.set()
                            send("/resume 1")
                            until(lambda: output.rfind(b"Enter send") > output.rfind(b"Resumed:") > 0)
                            send("/continue")
                            until(lambda: saved_status("finished") and
                                  output.rfind(b"Enter send") > output.rfind(b"Run finished") > 0)
                        else:
                            send(request)
                            until(lambda: b"Early streamed text." in output)
                            os.write(master, b"\x03")
                            until(lambda: saved_status("cancelled") and
                                  output.rfind(b"Enter send") > output.rfind(b"Run stopped") > 0)
                        send("/exit")
                        # Drain renderer output while shutdown restores terminal modes.
                        deadline = time.monotonic() + 5
                        while process.poll() is None and time.monotonic() < deadline:
                            if select.select([master], [], [], .05)[0]:
                                try:
                                    output.extend(os.read(master, 65536))
                                except OSError:
                                    break
                        self.assertEqual(0 if resumed else 130, process.wait(timeout=5))
                        self.assertEqual(original_modes, termios.tcgetattr(master))
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=5)
                        os.close(master)
                with Store(state) as store:
                    rows = store.session_summaries()
                    self.assertEqual(1, len(rows))
                    goal = store.goal(rows[0]["id"])
                    self.assertEqual([request], [item["text"] for item in goal["requests"]])
                    self.assertEqual(["cancelled", "finished"], [item["status"] for item in goal["runs"]])
                    self.assertEqual([], goal["acceptances"])
                    self.assertEqual("Early streamed text. Final text.", goal["runs"][-1]["assistant_text"])
        finally:
            server.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


    def test_renderer_termination_cancels_active_run_and_fences_tools(self):
        import signal
        server = ThreadingHTTPServer(("127.0.0.1", 0), StreamingFixture)
        server.requests = []
        server.release = threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="goal-native-tui-disconnect-") as temporary:
                state = Path(temporary) / "state"
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
                environment = dict(os.environ, TERM="xterm-256color", TERM_PROGRAM="xterm",
                                   OPENAI_API_KEY="TRANSPORT_FIXTURE_NOT_A_SECRET",
                                   OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1")
                process = subprocess.Popen(
                    [sys.executable, "-m", "goal_native", "--state", str(state),
                     "--provider", "openai", "--model", "gpt-4.1-mini"],
                    cwd=ROOT, env=environment, stdin=slave, stdout=slave, stderr=slave,
                    start_new_session=True,
                )
                os.close(slave)
                output = bytearray()
                def observe(fragment):
                    deadline = time.monotonic() + 12
                    while fragment not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], .1)[0]:
                            try:
                                output.extend(os.read(master, 65536))
                            except OSError:
                                break
                    self.assertIn(fragment, output)
                try:
                    observe(b"Enter send")
                    children = subprocess.check_output(
                        ["pgrep", "-P", str(process.pid)], text=True,
                    ).splitlines()
                    renderer = next(
                        int(pid) for pid in children if "bridge/terminal.mjs" in
                        subprocess.check_output(["ps", "-o", "command=", "-p", pid], text=True)
                    )
                    os.write(master, b"Keep the partial answer after renderer loss\r")
                    observe(b"Early streamed text.")
                    os.kill(renderer, signal.SIGTERM)
                    deadline = time.monotonic() + 10
                    while process.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], .1)[0]:
                            try:
                                output.extend(os.read(master, 65536))
                            except OSError:
                                break
                    self.assertEqual(130, process.wait(timeout=5))
                    while select.select([master], [], [], 0)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                    self.assertNotIn(b'"status": "cancelled"', output)
                    self.assertNotIn(b"CLI interrupted by Ctrl-C", output)
                    with Store(state) as store:
                        goal = store.goal(store.session_summaries()[0]["id"])
                        self.assertEqual("paused", goal["status"])
                        self.assertEqual(["cancelled"], [run["status"] for run in goal["runs"]])
                        self.assertFalse(any(item["status"] == "running" for item in goal["invocations"]))
                        self.assertEqual([], goal["acceptances"])
                finally:
                    server.release.set()
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    os.close(master)
        finally:
            server.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_killed_renderer_restores_exact_terminal_modes(self):
        import signal
        with tempfile.TemporaryDirectory(prefix="goal-native-tty-recovery-") as temporary:
            master, slave = pty.openpty()
            initial = termios.tcgetattr(slave)
            process = subprocess.Popen(
                [sys.executable, "-m", "goal_native", "--state", str(Path(temporary) / "state")],
                cwd=ROOT,
                env={
                    **os.environ,
                    "TERM": "xterm-256color",
                    "TERM_PROGRAM": "xterm",
                    "OPENAI_API_KEY": "RENDERER_ENV_SECRET_SHOULD_NOT_CROSS",
                    "NODE_OPTIONS": "--require=/definitely/missing/renderer-option.js",
                },
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            )
            os.close(slave)
            output = bytearray()
            try:
                deadline = time.monotonic() + 10
                while b"Enter send" not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output.extend(os.read(master, 65536))
                self.assertIn(b"Enter send", output)
                renderer = next(
                    int(pid) for pid in subprocess.check_output(
                        ["pgrep", "-P", str(process.pid)], text=True,
                    ).splitlines() if "bridge/terminal.mjs" in
                    subprocess.check_output(["ps", "-o", "command=", "-p", pid], text=True)
                )
                renderer_environment = subprocess.check_output(
                    ["ps", "-p", str(renderer), "-wwE", "-o", "command="],
                    text=True,
                )
                self.assertNotIn("RENDERER_ENV_SECRET_SHOULD_NOT_CROSS", renderer_environment)
                self.assertNotIn("NODE_OPTIONS=", renderer_environment)
                os.kill(renderer, signal.SIGKILL)
                self.assertEqual(130, process.wait(timeout=10))
                self.assertEqual(initial, termios.tcgetattr(master))
                with Store(Path(temporary) / "state") as store:
                    self.assertEqual([], store.list_goals())
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                os.close(master)

    def test_terminal_restore_failure_preserves_active_error(self):
        master, slave = pty.openpty()
        terminal = PiTerminal()
        terminal._tty_fd = slave
        terminal._tty_state = termios.tcgetattr(slave)
        os.close(slave)
        with tempfile.TemporaryFile(mode="w") as closed_events:
            terminal._events = closed_events
        terminal.arm_run()
        with self.assertRaises(KeyboardInterrupt):
            terminal.send("activity", text="renderer gone")
        terminal.disarm_run()
        try:
            original = ValueError("original controller failure")
            self.assertIsNone(terminal.__exit__(ValueError, original, None))
            self.assertIn("Terminal cleanup also failed:", original.__notes__[0])
            with self.assertRaises(termios.error):
                terminal.__exit__(None, None, None)
        finally:
            os.close(master)

    def test_pipe_failure_and_renderer_watcher_emit_one_interruption(self):
        terminal = PiTerminal()
        terminal.process = Mock()
        terminal.process.wait.return_value = 1
        terminal.arm_run()
        with tempfile.TemporaryFile(mode="w") as closed_events:
            terminal._events = closed_events
        with self.assertRaises(KeyboardInterrupt):
            terminal.send("output", text="renderer gone")
        with patch("goal_native.tui.os.kill") as signal_controller:
            terminal._watch_renderer()
            terminal.send("output", text="already cancelled")
            signal_controller.assert_not_called()

        terminal = PiTerminal()
        terminal.process = Mock()
        terminal.process.wait.return_value = 1
        terminal.arm_run()
        with patch("goal_native.tui.os.kill") as signal_controller:
            terminal._watch_renderer()
            terminal.send("output", text="already signalled")
            signal_controller.assert_called_once()

    def test_sigkill_renderer_cancels_active_run_without_waiting_for_provider(self):
        import signal
        server = ThreadingHTTPServer(("127.0.0.1", 0), StreamingFixture)
        server.requests = []
        server.release = threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="goal-native-tui-kill-active-") as temporary:
                state = Path(temporary) / "state"
                master, slave = pty.openpty()
                initial = termios.tcgetattr(slave)
                process = subprocess.Popen(
                    [sys.executable, "-m", "goal_native", "--state", str(state),
                     "--provider", "openai", "--model", "gpt-4.1-mini"],
                    cwd=ROOT, env={**os.environ, "TERM": "xterm-256color", "TERM_PROGRAM": "xterm",
                                   "OPENAI_API_KEY": "TRANSPORT_FIXTURE_NOT_A_SECRET",
                                   "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"},
                    stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                )
                os.close(slave)
                output = bytearray()
                def observe(fragment):
                    deadline = time.monotonic() + 10
                    while fragment not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], .1)[0]:
                            output.extend(os.read(master, 65536))
                    self.assertIn(fragment, output)
                try:
                    observe(b"Enter send")
                    renderer = next(
                        int(pid) for pid in subprocess.check_output(
                            ["pgrep", "-P", str(process.pid)], text=True,
                        ).splitlines() if "bridge/terminal.mjs" in
                        subprocess.check_output(["ps", "-o", "command=", "-p", pid], text=True)
                    )
                    os.write(master, b"Stop work if the interface disappears\r")
                    observe(b"Early streamed text.")
                    os.kill(renderer, signal.SIGKILL)
                    self.assertEqual(130, process.wait(timeout=5))
                    while select.select([master], [], [], 0)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                    self.assertNotIn(b'"status": "cancelled"', output)
                    self.assertNotIn(b"CLI interrupted by Ctrl-C", output)
                    self.assertEqual(initial, termios.tcgetattr(master))
                    with Store(state) as store:
                        goal = store.goal(store.session_summaries()[0]["id"])
                        self.assertEqual(["cancelled"], [run["status"] for run in goal["runs"]])
                        self.assertEqual([], goal["acceptances"])
                finally:
                    server.release.set()
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    os.close(master)
        finally:
            server.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_partial_renderer_initialization_restores_terminal(self):
        import signal
        failures = (
            ("os.fdopen", "OSError", "fdopen denied"),
            ("threading.Thread.start", "RuntimeError", "watcher start denied"),
        )
        for target, error_type, message in failures:
            with self.subTest(target=target):
                script = (
                    "from unittest.mock import patch\n"
                    "from goal_native.tui import PiTerminal\n"
                    "terminal = PiTerminal()\n"
                    f"with patch('goal_native.tui.{target}', "
                    f"side_effect={error_type}({message!r})):\n"
                    "    try:\n"
                    "        terminal.__enter__()\n"
                    f"    except {error_type} as error:\n"
                    f"        assert str(error) == {message!r}, error\n"
                    "    else:\n"
                    "        raise AssertionError('startup unexpectedly succeeded')\n"
                    "assert terminal.process is not None and terminal.process.poll() is not None\n"
                )
                master, slave = pty.openpty()
                original = termios.tcgetattr(slave)
                process = subprocess.Popen(
                    [sys.executable, "-c", script], cwd=ROOT,
                    env={**os.environ, "TERM": "xterm-256color", "TERM_PROGRAM": "xterm"},
                    stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                )
                os.close(slave)
                try:
                    self.assertEqual(0, process.wait(timeout=12))
                    self.assertEqual(original, termios.tcgetattr(master))
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    os.close(master)

if __name__ == "__main__":
    unittest.main()
