"""Presentation-only Pi TUI transport; the Python controller retains all authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import shutil
import subprocess
import sys
import threading
from typing import Any

from .terminal import terminal_text

_RENDERER_ENV_KEYS = (
    # Node is launched by absolute path; PATH is retained for normal macOS
    # process startup, while no Node or provider configuration is inherited.
    "PATH",
    "HOME",
    "TMPDIR",
    "__CF_USER_TEXT_ENCODING",
    # Terminal presentation.
    "TERM",
    "COLORTERM",
    "NO_COLOR",
    "FORCE_COLOR",
    "CLICOLOR",
    "CLICOLOR_FORCE",
    "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION",
    # Locale-sensitive rendering.
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_COLLATE",
    "LC_MESSAGES",
    "LC_MONETARY",
    "LC_NUMERIC",
    "LC_TIME",
)


def _renderer_environment() -> dict[str, str]:
    return {
        key: value
        for key in _RENDERER_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }


if os.name == "posix":
    import termios
else:
    termios = None


def interactive_terminal() -> bool:
    return (sys.stdin.isatty() and sys.stdout.isatty()
            and os.environ.get("TERM", "") not in {"", "dumb", "unknown"})


def goal_indicator(goal: dict[str, Any], running: bool = False) -> tuple[str, str]:
    """A finished invocation is explicitly not an accepted goal."""
    status = goal.get("status", "unknown")
    if status == "cancelled":
        return "cancelled", "dim"
    if running:
        return "running", "cyan"
    if status == "paused":
        return "paused", "yellow"
    last = goal.get("last_run_status")
    if last == "finished":
        return "run finished / review", "green"
    if last == "failed":
        return "failed", "red"
    if last in {"interrupted", "cancelled"}:
        return "stopped", "yellow"
    if last == "running":
        # A persisted unfinished attempt does not prove a live worker exists.
        return "unfinished", "yellow"
    return status, "dim"


class PiTerminal:
    """Dedicated pipes carry UI events, never executable controller requests.

    Node owns the real terminal; Python keeps its normal synchronous chat loop.
    The only return messages are editor submissions. Ctrl-C signals Python so
    the existing Worker cancellation path remains the authority boundary.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.process: subprocess.Popen[bytes] | None = None
        self._events = None
        self._commands = None
        self.original = sys.stdout
        self.encoding = "utf-8"
        self._tty_fd: int | None = None
        self._tty_state: list[Any] | None = None
        self._watch_lock = threading.Lock()
        self._run_active = False
        self._renderer_dead = False
        self._disconnect_signalled = False
        self._drop_events = False

    def __enter__(self) -> "PiTerminal":
        node = shutil.which("node")
        if not node:
            raise ValueError("Node.js is required for the interactive interface; run bootstrap.")
        # Pi normally resets raw mode. Keep the exact original TTY state too:
        # a killed renderer cannot run its own shutdown handler.
        if termios is not None:
            self._tty_fd = sys.stdin.fileno()
            self._tty_state = termios.tcgetattr(self._tty_fd)
        incoming, events = os.pipe()
        try:
            commands, outgoing = os.pipe()
        except BaseException:
            for fd in (incoming, events):
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise
        try:
            try:
                self.process = subprocess.Popen(
                    [node, str(Path(__file__).resolve().parents[1] / "bridge" / "terminal.mjs"),
                     str(incoming), str(outgoing), str(os.getpid())],
                    env=_renderer_environment(),
                    pass_fds=(incoming, outgoing),
                )
            finally:
                for fd in (incoming, outgoing):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self._events = os.fdopen(events, "w", encoding="utf-8", buffering=1)
            events = None
            self._commands = os.fdopen(commands, "r", encoding="utf-8")
            commands = None
            if not select.select([self._commands], [], [], 15)[0]:
                raise ValueError("Pi terminal did not start; run python3 scripts/bootstrap.py.")
            if self._commands.readline().strip() != '{"type":"ready"}':
                raise ValueError("Pi terminal is unavailable; run python3 scripts/bootstrap.py.")
            sys.stdout = self
            threading.Thread(target=self._watch_renderer, daemon=True).start()
        except BaseException as error:
            for fd in (events, commands):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def arm_run(self) -> None:
        """A missing UI must interrupt work even when Node cannot signal us."""
        with self._watch_lock:
            self._run_active = True
            self._interrupt_on_loss()

    def disarm_run(self) -> None:
        with self._watch_lock:
            self._run_active = False

    @property
    def renderer_dead(self) -> bool:
        with self._watch_lock:
            return self._renderer_dead

    def abandon_run(self) -> None:
        """Fence all later UI writes after cancellation caused by renderer loss."""
        with self._watch_lock:
            self._run_active = False
            self._drop_events = True

    def _interrupt_on_loss(self) -> None:
        # Called under _watch_lock; send only once for a renderer lifetime.
        if self._renderer_dead and self._run_active and not self._disconnect_signalled:
            self._disconnect_signalled = True
            os.kill(os.getpid(), signal.SIGINT)

    def _watch_renderer(self) -> None:
        status = self.process.wait()
        with self._watch_lock:
            self._renderer_dead = True
            # The renderer already signalled on its cooperative busy-close path.
            if status == 130:
                self._disconnect_signalled = True
            elif status != 0:
                self._interrupt_on_loss()

    def send(self, kind: str, **payload: Any) -> None:
        with self._lock:
            with self._watch_lock:
                if self._drop_events:
                    return
                if self._renderer_dead:
                    if self._run_active and not self._disconnect_signalled:
                        self._disconnect_signalled = True
                        raise KeyboardInterrupt("Pi renderer event pipe disconnected")
                    return
            try:
                self._events.write(json.dumps({"type": kind, **payload}, ensure_ascii=True) + "\n")
                self._events.flush()
            except (OSError, ValueError) as error:
                with self._watch_lock:
                    self._renderer_dead = True
                    interrupt = self._run_active and not self._disconnect_signalled
                    if interrupt:
                        self._disconnect_signalled = True
                    dropped = self._drop_events or self._run_active
                if interrupt:
                    raise KeyboardInterrupt("Pi renderer event pipe disconnected") from error
                if dropped:
                    return
                raise

    def write(self, text: str) -> int:
        if text:
            self.send("output", text=terminal_text(text))
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        # Terminal's plain-text adapter must not inject ANSI into our protocol.
        return False

    def read(self) -> str:
        self.send("prompt")
        line = self._commands.readline()
        if not line:
            raise EOFError
        message = json.loads(line)
        if message.get("type") != "submit" or not isinstance(message.get("text"), str):
            raise ValueError("invalid terminal submission")
        return message["text"]

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, _traceback: Any) -> None:
        sys.stdout = self.original
        self.disarm_run()
        cleanup_errors: list[BaseException] = []
        if self._events:
            try:
                self.send("close")
            except (OSError, ValueError):
                pass  # A dead or already-closed renderer cannot receive events.
            try:
                self._events.close()
            except BrokenPipeError:
                pass
            except OSError as error:
                cleanup_errors.append(error)
        if self.process:
            try:
                if self._events is None and self.process.poll() is None:
                    self.process.terminate()  # Startup failed before the close pipe was wrapped.
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
            except (OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                cleanup_errors.append(error)
        if self._commands:
            try:
                self._commands.close()
            except OSError as error:
                cleanup_errors.append(error)
        if self._tty_state is not None and self._tty_fd is not None:
            try:
                termios.tcsetattr(self._tty_fd, termios.TCSANOW, self._tty_state)
            except (OSError, termios.error) as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            if exc_type is not None and exc is not None:
                exc.add_note(f"Terminal cleanup also failed: {cleanup_errors[0]}")
            else:
                raise cleanup_errors[0]
