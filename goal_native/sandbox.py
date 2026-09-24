"""Staged file tools and fail-closed macOS process isolation.

The direct file tools use a pinned root directory descriptor and no-follow
``openat``-style traversal.  Search is deliberately bounded literal search,
not controller-side regular-expression evaluation.  Code runs only under
macOS ``sandbox-exec``; the profile denies process creation and allows exec
only for the configured Python interpreter.  Consequently sandboxed Python
cannot launch shells, subprocesses, or detached children.  There is no
unsandboxed fallback.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path
from typing import Any


class SandboxError(PermissionError):
    """A staged operation was denied or could not be safely enforced."""


class SandboxUnavailable(SandboxError):
    """The required operating-system enforcement is unavailable."""


_BLOCKED_COMPONENTS = {
    ".env",
    ".git",
    ".state",
    "credentials",
    "credential",
    "private",
    "secret",
    "secrets",
    "controller",
}
_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".secret")
_DEFAULT_FILE_LIMIT = 1 * 1024 * 1024
_DEFAULT_SEARCH_FILES = 256
_DEFAULT_SEARCH_BYTES = 8 * 1024 * 1024
_DEFAULT_SEARCH_ENTRIES = 4096
_DEFAULT_SEARCH_DEPTH = 64
_MAX_QUERY_BYTES = 4096
_MAX_MATCH_TEXT_CHARS = 4096
_SANDBOX_EXECUTABLE = "/usr/bin/sandbox-exec"
_MAX_TIMEOUT_SECONDS = 300.0
_MAX_OUTPUT_BYTES = 1 * 1024 * 1024
_MAX_STAGE_BYTES = 64 * 1024 * 1024
_MAX_STAGE_ENTRIES = _DEFAULT_SEARCH_ENTRIES
_MAX_PATH_CHARS = 4096
_MAX_RUN_ARGS = 64
_MAX_RUN_ARGUMENT_CHARS = 4096
_MAX_RUN_ARGUMENT_BYTES = 16 * 1024
_PYTHON_RUNNER = """
import os, sys
descriptor_path, __file__ = sys.argv[1:3]
sys.argv = sys.argv[2:]
sys.path[:0] = list(dict.fromkeys([os.path.dirname(__file__), os.getcwd()]))
with open(descriptor_path, "rb") as source:
    code = compile(source.read(), __file__, "exec")
exec(code, globals())
"""


class Sandbox:
    """A capability rooted at one trusted staged-copy directory.

    Direct operations are anchored to a descriptor for ``root`` and never
    follow path symlinks.  Search captures an opened-file manifest before
    reading, so files added later are outside that operation's receipt.
    Execution is accepted only through the configured interpreter and macOS
    ``sandbox-exec``.  The enforced profile denies forks and arbitrary exec;
    this is an intentional restriction of the Python execution capability.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        interpreter: str | os.PathLike[str] | None = None,
        timeout: float = 10.0,
        max_output_bytes: int = 64 * 1024,
        require_os_sandbox: bool = True,
    ) -> None:
        root_path = Path(root)
        if not root_path.is_absolute():
            raise SandboxError("sandbox root must be absolute")
        timeout_value = float(timeout)
        max_output_value = int(max_output_bytes)
        if (
            not math.isfinite(timeout_value)
            or timeout_value <= 0
            or timeout_value > _MAX_TIMEOUT_SECONDS
            or max_output_value <= 0
            or max_output_value > _MAX_OUTPUT_BYTES
        ):
            raise ValueError("sandbox limits must be positive and bounded")
        if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
            raise SandboxUnavailable("secure descriptor-relative traversal is unavailable")
        root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            root_fd = os.open(root_path, root_flags)
        except OSError as exc:
            raise SandboxError("sandbox root must be a real directory") from exc
        try:
            if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
                raise SandboxError("sandbox root must be a real directory")
        except BaseException:
            os.close(root_fd)
            raise

        self.root = Path(os.path.realpath(root_path))
        self._root_fd = root_fd
        try:
            self._assert_root_stable()
        except BaseException:
            self.close()
            raise
        self.timeout = timeout_value
        self.max_output_bytes = max_output_value
        self.require_os_sandbox = bool(require_os_sandbox)
        self.sandbox_exec = _SANDBOX_EXECUTABLE
        selected = os.fspath(interpreter) if interpreter is not None else sys.executable
        if not os.path.isabs(selected):
            self.close()
            raise SandboxError("interpreter must be an absolute path")
        trusted_interpreter = os.path.realpath(sys.executable)
        selected_interpreter = os.path.realpath(selected)
        if selected_interpreter != trusted_interpreter:
            self.close()
            raise SandboxError("only the controller's Python interpreter is trusted")
        self.interpreter = trusted_interpreter
        if not os.path.isfile(self.interpreter) or not os.access(self.interpreter, os.X_OK):
            self.close()
            raise SandboxError("trusted Python interpreter does not exist or is not executable")
        if sys.version_info < (3, 11):
            self.close()
            raise SandboxUnavailable("worker requires the supported Python 3.11+ runtime")
        if self.require_os_sandbox and (
            sys.platform != "darwin"
            or not os.path.isabs(self.sandbox_exec)
            or not os.path.isfile(self.sandbox_exec)
            or not os.access(self.sandbox_exec, os.X_OK)
        ):
            self.close()
            raise SandboxUnavailable("macOS sandbox-exec enforcement is unavailable")
        self._lock = threading.Lock()
        self._active: subprocess.Popen[bytes] | None = None
        self._run_active = False
        self._cancel_requested = False

    def close(self) -> None:
        """Close the trusted root descriptor; repeated calls are harmless."""

        root_fd = getattr(self, "_root_fd", -1)
        if root_fd != -1:
            self._root_fd = -1
            try:
                os.close(root_fd)
            except OSError:
                pass

    def __del__(self) -> None:
        self.close()

    def _assert_root_stable(self) -> None:
        root_fd = getattr(self, "_root_fd", -1)
        if root_fd == -1:
            raise SandboxError("sandbox root is closed")
        try:
            descriptor_stat = os.fstat(root_fd)
            path_stat = os.stat(self.root, follow_symlinks=False)
        except OSError as exc:
            raise SandboxError("sandbox root path is no longer stable") from exc
        if (
            not stat.S_ISDIR(descriptor_stat.st_mode)
            or not stat.S_ISDIR(path_stat.st_mode)
            or descriptor_stat.st_dev != path_stat.st_dev
            or descriptor_stat.st_ino != path_stat.st_ino
        ):
            raise SandboxError("sandbox root path no longer names the opened directory")

    def _validate_stage_for_run(self) -> None:
        self._assert_root_stable()
        try:
            pending = [(os.dup(self._root_fd), 0)]
        except OSError as exc:
            raise SandboxError("sandbox root cannot be inspected") from exc
        total_bytes = 0
        total_entries = 0
        try:
            while pending:
                directory_fd, depth = pending.pop()
                try:
                    try:
                        entries = os.scandir(directory_fd)
                    except OSError as exc:
                        raise SandboxError("sandbox stage cannot be inspected") from exc
                    with entries:
                        for entry in entries:
                            total_entries += 1
                            if total_entries > _MAX_STAGE_ENTRIES:
                                raise SandboxError("sandbox stage has too many entries")
                            try:
                                child_stat = os.stat(
                                    entry.name,
                                    dir_fd=directory_fd,
                                    follow_symlinks=False,
                                )
                            except OSError as exc:
                                raise SandboxError("sandbox stage changed during admission") from exc
                            mode = child_stat.st_mode
                            if stat.S_ISLNK(mode):
                                raise SandboxError("sandbox stage contains a symlink")
                            if stat.S_ISDIR(mode):
                                if depth >= _DEFAULT_SEARCH_DEPTH:
                                    raise SandboxError("sandbox stage is too deep")
                                try:
                                    child_fd = self._open_regular_directory(
                                        directory_fd, entry.name
                                    )
                                except OSError as exc:
                                    raise SandboxError(
                                        "sandbox stage changed during admission"
                                    ) from exc
                                pending.append((child_fd, depth + 1))
                                continue
                            if not stat.S_ISREG(mode):
                                raise SandboxError("sandbox stage contains a non-regular file")
                            total_bytes += max(0, int(child_stat.st_size))
                            if total_bytes > _MAX_STAGE_BYTES:
                                raise SandboxError("sandbox stage exceeds the size limit")
                finally:
                    os.close(directory_fd)
        finally:
            for directory_fd, _ in pending:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass
        self._assert_root_stable()

    def read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        parts = self._parts(arguments.get("path"))
        limit = self._bounded_limit(arguments.get("max_bytes"), _DEFAULT_FILE_LIMIT)
        parent_fd = self._open_directory(parts[:-1])
        fd = -1
        try:
            fd = self._open_regular(parent_fd, parts[-1], os.O_RDONLY)
            content = self._read_fd(fd, limit)
        finally:
            if fd != -1:
                os.close(fd)
            os.close(parent_fd)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise
        return {
            "path": self._relative_parts(parts),
            "content": text,
            "bytes": len(content),
        }

    def write(self, arguments: dict[str, Any]) -> dict[str, Any]:
        parts = self._parts(arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("staged write content must be text")
        limit = self._bounded_limit(arguments.get("max_bytes"), _DEFAULT_FILE_LIMIT)
        if len(content) > limit:
            raise SandboxError(f"staged write exceeds limit: > {limit}")
        data = content.encode("utf-8")
        if len(data) > limit:
            raise SandboxError(f"staged write exceeds limit: {len(data)} > {limit}")

        parent_fd = self._open_directory(parts[:-1], create=True)
        fd = -1
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
            fd = os.open(parts[-1], flags, 0o600, dir_fd=parent_fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise SandboxError("staged write target is not a regular file")
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError("staged write made no progress")
                offset += written
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise SandboxError("staged write target is not a safe regular file") from exc
            raise
        finally:
            if fd != -1:
                os.close(fd)
            os.close(parent_fd)
        return {"path": self._relative_parts(parts), "bytes": len(data), "written": True}

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query", arguments.get("pattern"))
        if not isinstance(query, str) or not query:
            raise ValueError("staged search query is required")
        if len(query.encode("utf-8")) > _MAX_QUERY_BYTES:
            raise SandboxError("staged search query exceeds the search limit")

        scope_parts = self._parts(arguments.get("path", "."), allow_root=True)
        max_results = self._bounded_limit(arguments.get("max_results"), 100)
        max_files = self._bounded_limit(arguments.get("max_files"), _DEFAULT_SEARCH_FILES)
        max_scan_bytes = self._bounded_limit(
            arguments.get("max_scan_bytes"), _DEFAULT_SEARCH_BYTES
        )
        max_file_bytes = self._bounded_limit(
            arguments.get("max_file_bytes"), _DEFAULT_FILE_LIMIT
        )
        exclusions = {
            "blocked": 0,
            "symlinks": 0,
            "non_regular": 0,
            "depth": 0,
            "entry_limit": 0,
            "file_limit": 0,
            "file_size_limit": 0,
            "scan_bytes_limit": 0,
            "races": 0,
            "errors": 0,
            "invalid_utf8": 0,
            "result_limit": 0,
        }
        snapshot_hash = hashlib.sha256()
        files: list[tuple[str, int, int]] = []
        state = {
            "entries": 0,
            "scan_bytes": 0,
            "truncated": False,
        }
        result_truncated = False
        start_fd = self._open_directory(scope_parts)
        try:
            self._snapshot_directory(
                start_fd,
                "",
                0,
                files,
                snapshot_hash,
                exclusions,
                state,
                max_files=max_files,
                max_scan_bytes=max_scan_bytes,
                max_file_bytes=max_file_bytes,
            )
            matches: list[dict[str, Any]] = []
            for relative, fd, _ in files:
                try:
                    raw = self._read_fd(fd, max_file_bytes)
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    exclusions["invalid_utf8"] += 1
                    continue
                except OSError:
                    exclusions["races"] += 1
                    continue
                for line_number, line in enumerate(text.splitlines(), 1):
                    if query not in line:
                        continue
                    shown = line[:_MAX_MATCH_TEXT_CHARS]
                    match: dict[str, Any] = {
                        "path": relative,
                        "line": line_number,
                        "text": shown,
                    }
                    if len(shown) < len(line):
                        match["text_truncated"] = True
                    matches.append(match)
                    if len(matches) >= max_results:
                        exclusions["result_limit"] += 1
                        result_truncated = True
                        break
                if result_truncated:
                    break
            matches.sort(key=lambda item: (str(item["path"]), int(item["line"])))
        finally:
            for _, fd, _ in files:
                try:
                    os.close(fd)
                except OSError:
                    pass
            os.close(start_fd)

        return {
            "query": query,
            "matches": matches,
            "truncated": bool(state["truncated"] or result_truncated),
            "scope": {
                "path": self._relative_parts(scope_parts),
                "literal": True,
                "max_results": max_results,
                "max_files": max_files,
                "max_scan_bytes": max_scan_bytes,
                "max_file_bytes": max_file_bytes,
            },
            "snapshot": {
                "kind": "opened-file-manifest",
                "id": snapshot_hash.hexdigest(),
                "files": len(files),
                "bytes": state["scan_bytes"],
                "entries": state["entries"],
                "truncated": bool(state["truncated"]),
            },
            "exclusions": exclusions,
        }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "darwin" or not self.sandbox_exec:
            raise SandboxUnavailable("refusing execution without macOS sandbox-exec")
        path_value, args = self._script_and_args(arguments)
        parts = self._parts(path_value)
        if not parts[-1].endswith(".py"):
            raise SandboxError("only an existing staged .py script may run")
        timeout = float(arguments.get("timeout_seconds", self.timeout))
        if not math.isfinite(timeout) or timeout <= 0 or timeout > self.timeout:
            raise SandboxError("run timeout exceeds sandbox limit")
        self._validate_stage_for_run()

        with self._lock:
            if self._run_active:
                raise SandboxError("sandbox already has an active run")
            self._run_active = True
            self._active = None
            self._cancel_requested = False

        parent_fd = -1
        script_fd = -1
        process: subprocess.Popen[bytes] | None = None
        try:
            parent_fd = self._open_directory(parts[:-1])
            script_fd = self._open_regular(parent_fd, parts[-1], os.O_RDONLY)
            self._assert_root_stable()
            fd_path = f"/dev/fd/{script_fd}"
            command = [
                self.sandbox_exec,
                "-p",
                self._profile(fd_path),
                self.interpreter,
                "-I",
                "-B",
                "-c",
                _PYTHON_RUNNER,
                fd_path,
                str(self.root.joinpath(*parts)),
                *args,
            ]
            environment = {
                "PATH": "/usr/bin:/bin",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "LC_ALL": "C",
            }
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    pass_fds=(script_fd,),
                )
            except OSError as exc:
                raise SandboxError(f"could not start enforced sandbox: {exc}") from exc
            with self._lock:
                self._active = process
                cancelled_before_start = self._cancel_requested
            if cancelled_before_start:
                self._kill(process)
            stdout, stderr, timed_out, cancelled, output_limited = self._collect(
                process, timeout
            )
            return {
                "path": self._relative_parts(parts),
                "stdout": stdout.decode("utf-8", "replace"),
                "stderr": stderr.decode("utf-8", "replace"),
                "exit_code": process.returncode,
                "timed_out": timed_out,
                "cancelled": cancelled,
                "output_limited": output_limited,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "enforcement": "macos-sandbox-exec",
            }
        finally:
            with self._lock:
                if process is not None and self._active is process:
                    self._active = None
                self._run_active = False
                self._cancel_requested = False
            if script_fd != -1:
                os.close(script_fd)
            if parent_fd != -1:
                os.close(parent_fd)

    def cancel(self) -> None:
        with self._lock:
            if not self._run_active:
                return
            self._cancel_requested = True
            process = self._active
        if process is not None:
            self._kill(process)

    def _collect(
        self,
        process: subprocess.Popen[bytes],
        timeout: float,
    ) -> tuple[bytes, bytes, bool, bool, bool]:
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        timed_out = False
        output_limited = False
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    self._kill(process)
                    break
                events = selector.select(min(0.1, remaining))
                if not events:
                    continue
                for key, _ in events:
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    buffer = buffers[key.data]
                    room = self.max_output_bytes - len(buffers["stdout"]) - len(buffers["stderr"])
                    if room <= 0:
                        output_limited = True
                        self._kill(process)
                        break
                    buffer.extend(chunk[:room])
                    if len(chunk) > room:
                        output_limited = True
                        self._kill(process)
                        break
                if output_limited:
                    break
                if process.poll() is not None and not selector.get_map():
                    break
            if process.poll() is None:
                self._kill(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._kill(process)
                process.wait(timeout=1)
            with self._lock:
                cancelled = self._cancel_requested
        finally:
            selector.close()
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        return bytes(buffers["stdout"]), bytes(buffers["stderr"]), timed_out, cancelled, output_limited

    def _profile(self, script_fd_path: str | None = None) -> str:
        allowed_dirs = self._runtime_directories()
        read_dirs = [str(self.root), *allowed_dirs, "/System/Library", "/usr/lib", "/dev/fd"]
        lines = [
            "(version 1)",
            "(deny default)",
            "(deny process-fork)",
            "(allow process-exec (literal " + _sbpl(self.interpreter) + "))",
            "(allow sysctl-read)",
            "(allow file-write* (subpath " + _sbpl(str(self.root)) + "))",
            "(allow file-write* (literal \"/dev/fd/1\"))",
            "(allow file-write* (literal \"/dev/fd/2\"))",
        ]
        seen_dirs: set[str] = set()
        seen_literals: set[str] = set()
        for directory in read_dirs:
            if not directory or directory in seen_dirs or not os.path.isdir(directory):
                continue
            seen_dirs.add(directory)
            lines.append("(allow file-read* (subpath " + _sbpl(directory) + "))")
            if directory != "/dev/fd":
                lines.append("(allow file-map-executable (subpath " + _sbpl(directory) + "))")
            current = os.path.normpath(directory)
            while True:
                if current not in seen_literals:
                    lines.append("(allow file-read* (literal " + _sbpl(current) + "))")
                    seen_literals.add(current)
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent
        if script_fd_path is not None:
            lines.append("(allow file-read* (literal " + _sbpl(script_fd_path) + "))")
        for device in ("/dev/null", "/dev/urandom", "/dev/random"):
            lines.append("(allow file-read* (literal " + _sbpl(device) + "))")
            lines.append("(allow file-write* (literal " + _sbpl(device) + "))")
        blocked_pattern = self._blocked_stage_pattern()
        lines.append("(deny file-read* (regex " + _sbpl(blocked_pattern) + "))")
        lines.append("(deny file-write* (regex " + _sbpl(blocked_pattern) + "))")
        lines.append("(deny network*)")
        return "".join(lines)

    @staticmethod
    def _casefold_regex(value: str) -> str:
        pieces: list[str] = []
        for character in value:
            if character == ".":
                pieces.append("[.]")
            elif character.isalpha():
                pieces.append(f"[{character.lower()}{character.upper()}]")
            else:
                pieces.append(re.escape(character))
        return "".join(pieces)

    def _blocked_stage_pattern(self) -> str:
        exact_names = "|".join(
            self._casefold_regex(component) for component in sorted(_BLOCKED_COMPONENTS)
        )
        suffixes = "|".join(
            self._casefold_regex(suffix.removeprefix("."))
            for suffix in _BLOCKED_SUFFIXES
        )
        env_prefix = self._casefold_regex(".env.")
        credential = self._casefold_regex("credential")
        secret = self._casefold_regex("secret")
        # Seatbelt does not implement Python's (?:...) non-capturing groups.
        component = (
            rf"({exact_names}|{env_prefix}[^/]*|[^/]*[.]({suffixes})"
            rf"|[^/]*{credential}[^/]*|{secret}[^/]*)"
        )
        return (
            rf"^{re.escape(str(self.root))}/([^/]+/)*"
            rf"{component}(/|$)"
        )

    def _runtime_directories(self) -> list[str]:
        candidates = {
            os.path.dirname(self.interpreter),
            os.path.dirname(os.path.dirname(self.interpreter)),
            sys.prefix,
            sys.base_prefix,
            sysconfig.get_path("stdlib"),
            sysconfig.get_path("platstdlib"),
        }
        result: list[str] = []
        for candidate in candidates:
            if candidate and os.path.isdir(candidate):
                real = os.path.realpath(candidate)
                if real not in result:
                    result.append(real)
        return result

    def _script_and_args(self, arguments: dict[str, Any]) -> tuple[str, list[str]]:
        path = arguments.get("path", arguments.get("script"))
        args = arguments.get("args", [])
        command = arguments.get("command")
        if command is not None:
            if path is not None:
                raise ValueError("use path or command, not both")
            if not isinstance(command, list) or len(command) < 2:
                raise SandboxError("run command must contain the allowlisted interpreter and script")
            if any(not isinstance(item, str) for item in command):
                raise ValueError("run command must contain strings")
            if os.path.realpath(command[0]) != self.interpreter:
                raise SandboxError("run command interpreter is not allowlisted")
            path = command[1]
            args = command[2:]
        if not isinstance(path, str) or not path:
            raise ValueError("staged script path is required")
        if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
            raise ValueError("run args must be strings")
        if len(args) > _MAX_RUN_ARGS:
            raise SandboxError("run has too many arguments")
        values = [path, *args]
        if any("\x00" in item for item in values):
            raise ValueError("NUL is not allowed in run arguments")
        if any(len(item) > _MAX_RUN_ARGUMENT_CHARS for item in values):
            raise SandboxError("run argument is too long")
        try:
            argument_bytes = sum(len(item.encode("utf-8")) for item in values)
        except UnicodeEncodeError as exc:
            raise ValueError("run arguments must be valid UTF-8") from exc
        if argument_bytes > _MAX_RUN_ARGUMENT_BYTES:
            raise SandboxError("run arguments exceed the size limit")
        return path, args

    def _parts(self, value: Any, *, allow_root: bool = False) -> tuple[str, ...]:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise SandboxError("a relative staged path is required")
        if len(value) > _MAX_PATH_CHARS:
            raise SandboxError("staged path exceeds the path limit")
        candidate = Path(value)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise SandboxError("path escapes staged root")
        parts = tuple(part for part in candidate.parts if part not in ("", "."))
        if not parts and not allow_root:
            raise SandboxError("a staged file path is required")
        if any(self._blocked_component(part) for part in parts):
            raise SandboxError("credential or controller path is not staged capability")
        return parts

    def _open_directory(
        self,
        parts: tuple[str, ...],
        *,
        create: bool = False,
    ) -> int:
        fd = os.dup(self._root_fd)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            for part in parts:
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise SandboxError("staged path contains a symlink or non-directory") from exc
            raise

    @staticmethod
    def _open_regular(parent_fd: int, name: str, flags: int) -> int:
        try:
            fd = os.open(name, flags | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise SandboxError("staged path is not a safe regular file") from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise SandboxError("staged path is not a regular file")
        except BaseException:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _read_fd(fd: int, limit: int) -> bytes:
        chunks = bytearray()
        while len(chunks) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise SandboxError(f"staged file exceeds read limit: > {limit}")
        return bytes(chunks)

    def _snapshot_directory(
        self,
        directory_fd: int,
        relative: str,
        depth: int,
        files: list[tuple[str, int, int]],
        snapshot_hash: Any,
        exclusions: dict[str, int],
        state: dict[str, Any],
        *,
        max_files: int,
        max_scan_bytes: int,
        max_file_bytes: int,
    ) -> None:
        if state["truncated"]:
            return
        try:
            names: list[str] = []
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    state["entries"] += 1
                    if state["entries"] > _DEFAULT_SEARCH_ENTRIES:
                        exclusions["entry_limit"] += 1
                        state["truncated"] = True
                        break
                    names.append(entry.name)
        except OSError:
            exclusions["errors"] += 1
            state["truncated"] = True
            return

        for name in sorted(names):
            if state["truncated"]:
                return
            child_relative = name if not relative else f"{relative}/{name}"
            if self._blocked_component(name):
                exclusions["blocked"] += 1
                continue
            try:
                child_stat = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                exclusions["races"] += 1
                continue
            except OSError:
                exclusions["errors"] += 1
                continue
            mode = child_stat.st_mode
            if stat.S_ISLNK(mode):
                exclusions["symlinks"] += 1
                continue
            if stat.S_ISDIR(mode):
                if depth >= _DEFAULT_SEARCH_DEPTH:
                    exclusions["depth"] += 1
                    state["truncated"] = True
                    continue
                try:
                    child_fd = self._open_regular_directory(directory_fd, name)
                except FileNotFoundError:
                    exclusions["races"] += 1
                    continue
                except OSError:
                    exclusions["errors"] += 1
                    continue
                try:
                    self._snapshot_directory(
                        child_fd,
                        child_relative,
                        depth + 1,
                        files,
                        snapshot_hash,
                        exclusions,
                        state,
                        max_files=max_files,
                        max_scan_bytes=max_scan_bytes,
                        max_file_bytes=max_file_bytes,
                    )
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(mode):
                exclusions["non_regular"] += 1
                continue
            if len(files) >= max_files:
                exclusions["file_limit"] += 1
                state["truncated"] = True
                continue
            size = int(child_stat.st_size)
            if size > max_file_bytes:
                exclusions["file_size_limit"] += 1
                state["truncated"] = True
                continue
            if state["scan_bytes"] + size > max_scan_bytes:
                exclusions["scan_bytes_limit"] += 1
                state["truncated"] = True
                continue
            fd = -1
            keep_fd = False
            try:
                fd = self._open_regular(directory_fd, name, os.O_RDONLY)
                opened_stat = os.fstat(fd)
                if not stat.S_ISREG(opened_stat.st_mode):
                    exclusions["races"] += 1
                    continue
                opened_size = int(opened_stat.st_size)
                if opened_size > max_file_bytes:
                    exclusions["file_size_limit"] += 1
                    state["truncated"] = True
                    continue
                if state["scan_bytes"] + opened_size > max_scan_bytes:
                    exclusions["scan_bytes_limit"] += 1
                    state["truncated"] = True
                    continue
                keep_fd = True
            except FileNotFoundError:
                exclusions["races"] += 1
            except OSError:
                exclusions["errors"] += 1
            finally:
                if fd != -1 and not keep_fd:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if not keep_fd:
                continue
            snapshot_hash.update(
                f"{child_relative}\0{opened_stat.st_dev}:{opened_stat.st_ino}:"
                f"{opened_stat.st_size}:{opened_stat.st_mtime_ns}\n".encode("utf-8")
            )
            files.append((child_relative, fd, opened_size))
            state["scan_bytes"] += opened_size

    @staticmethod
    def _open_regular_directory(parent_fd: int, name: str) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        return os.open(name, flags, dir_fd=parent_fd)

    def _relative_parts(self, parts: tuple[str, ...]) -> str:
        return Path(*parts).as_posix() if parts else "."

    @staticmethod
    def _blocked_component(component: str) -> bool:
        lowered = component.lower()
        return (
            lowered in _BLOCKED_COMPONENTS
            or lowered.endswith(_BLOCKED_SUFFIXES)
            or lowered.startswith(".env.")
            or "credential" in lowered
            or lowered.startswith("secret")
        )

    @staticmethod
    def _bounded_limit(value: Any, default: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("limit must be a positive integer")
        return min(value, default)

    @staticmethod
    def _kill(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        except PermissionError:
            try:
                process.kill()
            except ProcessLookupError:
                pass


def _sbpl(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


__all__ = ["Sandbox", "SandboxError", "SandboxUnavailable"]
