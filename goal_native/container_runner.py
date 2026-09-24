"""Disposable Docker command execution over validated staged snapshots."""
from __future__ import annotations

import dataclasses
import errno
import json
import math
import os
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import workspace
from .container_runtime import (
    ARCHIVE_LENGTH,
    ARCHIVE_MAGIC,
    CONTAINER_ENTRYPOINT,
    DEFAULT_IMAGE_REFERENCE,
    ContainerLimits,
    ContainerRuntimeError,
    ContainerUnavailable,
    decode_snapshot_item,
    extract_candidate_archive,
    snapshot_archive,
    snapshot_changes,
    validate_argv,
    validate_image_id,
)


class ContainerRuntime:
    """Run direct commands in disposable, local Linux Docker containers.

    The only public task operation is ``command``.  Its argument dictionary is
    intentionally small: ``argv``, optional ``timeout_seconds`` and optional
    ``network``.  ``publish=False`` is a controller-only keyword for read-only
    checkers; it is not part of the worker tool schema.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        image: str = DEFAULT_IMAGE_REFERENCE,
        allow_network: bool = False,
        timeout: float = 300.0,
        max_output_bytes: int = 64 * 1024,
        docker: str | os.PathLike[str] | None = None,
        tracked: Any = (),
        excluded: Any = (),
    ) -> None:
        if not isinstance(allow_network, bool):
            raise ValueError("allow_network must be boolean")
        root_path = Path(root)
        try:
            resolved_root = root_path.resolve(strict=True)
        except OSError as exc:
            raise ContainerRuntimeError("container root must be a real directory") from exc
        if not root_path.is_absolute() or root_path.is_symlink() or resolved_root != root_path:
            raise ContainerRuntimeError("container root must be an absolute real directory")
        try:
            root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ContainerRuntimeError("container root must be a real directory") from exc
        try:
            info = os.fstat(root_fd)
            if not stat.S_ISDIR(info.st_mode):
                raise ContainerRuntimeError("container root must be a real directory")
        except BaseException:
            os.close(root_fd)
            raise

        selected_docker = os.fspath(docker) if docker is not None else shutil.which("docker")
        if not selected_docker:
            os.close(root_fd)
            raise ContainerUnavailable("Docker CLI is unavailable")
        selected_docker = os.path.abspath(selected_docker)
        if not os.path.isfile(selected_docker) or not os.access(selected_docker, os.X_OK):
            os.close(root_fd)
            raise ContainerUnavailable("Docker CLI is not executable")

        try:
            limits = ContainerLimits(
                max_timeout_seconds=timeout,
                max_output_bytes=max_output_bytes,
            )
        except (TypeError, ValueError) as exc:
            os.close(root_fd)
            raise ValueError("container timeout or output limit is invalid") from exc

        self.root = root_path.absolute()
        self._root_fd = root_fd
        self._root_identity = (info.st_dev, info.st_ino)
        self.docker = selected_docker
        self.limits = limits
        self.timeout = limits.max_timeout_seconds
        self.allow_network = allow_network
        self.image_reference = image
        self._tracked = tuple(tracked)
        self._excluded = tuple(excluded)
        self._lock = threading.RLock()
        self._active: dict[str, Any] | None = None
        self._command_active = False
        self._cancel_requested = threading.Event()
        self._closed = False
        self._docker_config = tempfile.TemporaryDirectory(prefix="goal-native-docker-config-")
        try:
            self.endpoint = self._resolve_local_endpoint()
            self.engine = self._inspect_engine()
            self.image = self._resolve_local_image(image)
        except BaseException:
            self._docker_config.cleanup()
            os.close(self._root_fd)
            self._root_fd = -1
            raise

    @property
    def metadata(self) -> dict[str, Any]:
        """Safe controller metadata for doctor/receipts; no host config is exposed."""

        return {
            "image": self.image,
            "image_reference": self.image_reference,
            "endpoint": self.endpoint,
            "engine": {
                "ostype": self.engine.get("OSType"),
                "architecture": self.engine.get("Architecture"),
                "server_version": self.engine.get("ServerVersion"),
            },
            "limits": dataclasses.asdict(self.limits),
            "allow_network": self.allow_network,
            "environment_inherited": False,
        }

    def _controller_environment(self) -> dict[str, str]:
        """Only fixed Docker-client settings; never proxy/DOCKER_HOST inheritance."""

        return {
            "PATH": f"{os.path.dirname(self.docker)}:/usr/bin:/bin",
            "HOME": str(Path.home()),
            "LANG": "C",
            "LC_ALL": "C",
        }

    def _bootstrap(self, arguments: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
            raise ContainerUnavailable("DOCKER_HOST and DOCKER_CONTEXT overrides are refused")
        try:
            return subprocess.run(
                [self.docker, *arguments],
                cwd="/",
                env=self._controller_environment(),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContainerUnavailable("Docker context inspection failed") from exc

    def _docker_call(self, arguments: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        command = [
            self.docker,
            "--config",
            self._docker_config.name,
            "--host",
            self.endpoint,
            *arguments,
        ]
        try:
            return subprocess.run(
                command,
                cwd="/",
                env=self._controller_environment(),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContainerUnavailable("Docker local-engine request failed") from exc

    def _resolve_local_endpoint(self) -> str:
        context_result = self._bootstrap(["context", "show"])
        context = context_result.stdout.strip()
        if (
            context_result.returncode != 0
            or not context
            or context.startswith("-")
            or "\x00" in context
            or any(character.isspace() for character in context)
        ):
            raise ContainerUnavailable("Docker context could not be determined")
        inspected = self._bootstrap([
            "context",
            "inspect",
            "--format",
            "{{json .Endpoints}}",
            context,
        ])
        if inspected.returncode != 0:
            raise ContainerUnavailable("Docker context could not be inspected")
        try:
            endpoints = json.loads(inspected.stdout.strip())
        except json.JSONDecodeError as exc:
            raise ContainerUnavailable("Docker context endpoint metadata is invalid") from exc
        endpoint = None
        if isinstance(endpoints, dict):
            docker_endpoint = endpoints.get("docker")
            if isinstance(docker_endpoint, dict):
                endpoint = docker_endpoint.get("Host")
        if endpoint is None and context == "default":
            endpoint = "unix:///var/run/docker.sock"
        if not isinstance(endpoint, str) or not endpoint.startswith("unix://"):
            raise ContainerUnavailable("remote Docker endpoints and non-unix contexts are refused")
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "unix"
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
            or "\x00" in endpoint
        ):
            raise ContainerUnavailable("Docker endpoint is not a local absolute unix socket")
        return endpoint

    def _inspect_engine(self) -> dict[str, Any]:
        result = self._docker_call(["info", "--format", "{{json .}}"])
        if result.returncode != 0:
            raise ContainerUnavailable("a local Docker Linux engine is not ready")
        try:
            info = json.loads(result.stdout.strip())
        except json.JSONDecodeError as exc:
            raise ContainerUnavailable("Docker engine metadata is invalid") from exc
        if not isinstance(info, dict) or str(info.get("OSType", "")).lower() != "linux":
            raise ContainerUnavailable("the selected Docker engine is not Linux")
        return info

    def _resolve_local_image(self, image: str) -> str:
        if not isinstance(image, str) or not image or "\x00" in image or image.startswith("-"):
            raise ValueError("runtime image reference is invalid")
        result = self._docker_call(["image", "inspect", "--format", "{{.Id}}", image])
        if result.returncode != 0:
            raise ContainerUnavailable("runtime image is not present locally; refusing an image pull")
        resolved = result.stdout.strip()
        try:
            return validate_image_id(resolved)
        except ValueError as exc:
            raise ContainerUnavailable("Docker did not return an immutable local image ID") from exc

    def _assert_root_stable(self) -> None:
        if self._root_fd == -1:
            raise ContainerRuntimeError("container root is closed")
        try:
            descriptor = os.fstat(self._root_fd)
            current = os.stat(self.root, follow_symlinks=False)
        except OSError as exc:
            raise ContainerRuntimeError("container root is no longer stable") from exc
        if (
            not stat.S_ISDIR(descriptor.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (descriptor.st_dev, descriptor.st_ino) != self._root_identity
            or (current.st_dev, current.st_ino) != self._root_identity
        ):
            raise ContainerRuntimeError("container root changed during execution")

    def _docker_run_args(
        self,
        name: str,
        argv: list[str],
        timeout: float,
        network: bool,
    ) -> list[str]:
        disk = str(self.limits.disk_bytes)
        tmp = str(self.limits.tmp_bytes)
        memory = str(self.limits.memory_bytes)
        return [
            "run",
            "--rm",
            "--interactive",
            "--log-driver=none",
            "--pull=never",
            "--name",
            name,
            "--read-only",
            "--network=bridge" if network else "--network=none",
            "--ipc=private",
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--cpus",
            f"{self.limits.cpus:.3f}",
            "--pids-limit",
            str(self.limits.pids),
            "--tmpfs",
            f"/workspace:rw,nosuid,nodev,size={disk}",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={tmp}",
            "--shm-size",
            "16m",
            "--ulimit",
            f"fsize={disk}:{disk}",
            "--ulimit",
            f"nproc={self.limits.pids}:{self.limits.pids}",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "KILL",
            "--cap-add",
            "CHOWN",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "0:0",
            "--entrypoint",
            CONTAINER_ENTRYPOINT,
            self.image,
            "--deadline-seconds",
            f"{timeout:.6f}",
            "--max-output-bytes",
            str(self.limits.max_output_bytes),
            "--max-archive-bytes",
            str(self.limits.max_archive_bytes),
            "--",
            *argv,
        ]

    def _close_stdin(self, process: subprocess.Popen[bytes]) -> None:
        stream = process.stdin
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def _force_kill(self, active: dict[str, Any] | None = None) -> None:
        with self._lock:
            current = active or self._active
        if not current:
            return
        process = current.get("process")
        name = current.get("name")
        if isinstance(name, str):
            try:
                self._docker_call(["kill", "--signal", "KILL", name], timeout=2.0)
            except ContainerRuntimeError:
                pass
        if isinstance(process, subprocess.Popen) and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass
        if isinstance(process, subprocess.Popen):
            self._close_stdin(process)

    def cancel(self) -> None:
        """Request cancellation; the container guardian kills the task on EOF."""

        with self._lock:
            self._cancel_requested.set()

    def _read_process(
        self,
        process: subprocess.Popen[bytes],
        *,
        name: str,
        timeout: float,
        frame: bytes,
    ) -> tuple[bytes | None, bytes, bool, bool]:
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        assert process.stdin is not None
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        input_offset = 0
        stdout = bytearray()
        stderr = bytearray()
        max_protocol = self.limits.max_archive_bytes + self.limits.max_output_bytes + 256 * 1024
        started = time.monotonic()
        cancelled = False
        hard_killed = False
        cancel_deadline: float | None = None
        try:
            while selector.get_map():
                if self._cancel_requested.is_set() and not cancelled:
                    cancelled = True
                    if process.stdin in (item.fileobj for item in selector.get_map().values()):
                        selector.unregister(process.stdin)
                    self._close_stdin(process)
                    cancel_deadline = time.monotonic() + self.limits.cancel_grace_seconds
                deadline = cancel_deadline or (started + timeout + self.limits.cancel_grace_seconds + 2.0)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    hard_killed = True
                    self._force_kill({"process": process, "name": name})
                    break
                events = selector.select(min(0.1, remaining))
                for key, _ in events:
                    if key.data == "stdin":
                        try:
                            count = os.write(key.fd, memoryview(frame)[input_offset:input_offset + 65536])
                            input_offset += count
                        except BlockingIOError:
                            continue
                        except OSError:
                            selector.unregister(key.fileobj)
                            continue
                        if input_offset == len(frame):
                            selector.unregister(key.fileobj)
                        continue
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        try:
                            selector.unregister(key.fileobj)
                        except Exception:
                            pass
                        continue
                    target = stdout if key.data == "stdout" else stderr
                    if len(target) + len(chunk) > (max_protocol if key.data == "stdout" else 1024 * 1024):
                        hard_killed = True
                        self._force_kill({"process": process, "name": name})
                        break
                    target.extend(chunk)
                if hard_killed:
                    break
            if process.poll() is None:
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._force_kill({"process": process, "name": name})
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
            # A killed Docker CLI can leave readable buffered bytes; drain only
            # what is already available, never waiting on a new workload.
            for stream, target, limit in (
                (process.stdout, stdout, max_protocol),
                (process.stderr, stderr, 1024 * 1024),
            ):
                if stream is None:
                    continue
                try:
                    os.set_blocking(stream.fileno(), False)
                    while len(target) < limit:
                        chunk = os.read(stream.fileno(), min(65536, limit - len(target)))
                        if not chunk:
                            break
                        target.extend(chunk)
                except OSError:
                    pass
            return (bytes(stdout) if stdout else None), bytes(stderr), cancelled, hard_killed
        finally:
            selector.close()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def _execute(
        self,
        archive: bytes,
        argv: list[str],
        timeout: float,
        network: bool,
    ) -> tuple[dict[str, Any] | None, bytes, bool, bool]:
        name = f"goal-native-{uuid.uuid4().hex}"
        command = [
            *self._docker_call_prefix(),
            *self._docker_run_args(name, argv, timeout, network),
        ]
        with self._lock:
            if self._closed or self._cancel_requested.is_set():
                return None, b"", True, False
            try:
                process = subprocess.Popen(
                    command,
                    cwd="/",
                    env=self._controller_environment(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                raise ContainerUnavailable("could not start the local Docker run") from exc
            self._active = {"process": process, "name": name}
        frame = ARCHIVE_MAGIC + ARCHIVE_LENGTH.pack(len(archive)) + archive

        try:
            raw, docker_stderr, cancelled, hard_killed = self._read_process(
                process,
                name=name,
                timeout=timeout,
                frame=frame,
            )
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._force_kill({"process": process, "name": name})
            if raw is None:
                return None, docker_stderr, cancelled, hard_killed
            return self._decode_receipt(raw), docker_stderr, cancelled, hard_killed
        finally:
            if process.poll() is None:
                self._force_kill({"process": process, "name": name})
                process.wait(timeout=2.0)
            with self._lock:
                if self._active and self._active.get("process") is process:
                    self._active = None
            self._close_stdin(process)

    def _docker_call_prefix(self) -> list[str]:
        return [
            self.docker,
            "--config",
            self._docker_config.name,
            "--host",
            self.endpoint,
        ]

    def _decode_receipt(self, raw: bytes) -> dict[str, Any]:
        if not raw.startswith(ARCHIVE_MAGIC):
            raise ContainerRuntimeError("container returned no trusted receipt")
        header_start = len(ARCHIVE_MAGIC)
        header_end = raw.find(b"\n", header_start)
        if header_end < 0 or header_end - header_start > 64 * 1024:
            raise ContainerRuntimeError("container receipt header is invalid")
        try:
            header = json.loads(raw[header_start:header_end].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContainerRuntimeError("container receipt header is invalid") from exc
        if not isinstance(header, dict) or header.get("version") != 1:
            raise ContainerRuntimeError("container receipt version is invalid")
        offset = header_end + 1
        values: dict[str, bytes] = {}
        for key in ("stdout", "stderr", "archive"):
            if offset + ARCHIVE_LENGTH.size > len(raw):
                raise ContainerRuntimeError("container receipt is incomplete")
            (length,) = ARCHIVE_LENGTH.unpack(raw[offset:offset + ARCHIVE_LENGTH.size])
            offset += ARCHIVE_LENGTH.size
            limit = self.limits.max_archive_bytes if key == "archive" else self.limits.max_output_bytes
            if length > limit or offset + length > len(raw):
                raise ContainerRuntimeError("container receipt payload exceeds its limit")
            values[key] = raw[offset:offset + length]
            offset += length
        if offset != len(raw):
            raise ContainerRuntimeError("container receipt has trailing bytes")
        header["stdout_bytes"] = len(values["stdout"])
        header["stderr_bytes"] = len(values["stderr"])
        header["archive_bytes"] = len(values["archive"])
        header["stdout_data"] = values["stdout"]
        header["stderr_data"] = values["stderr"]
        header["archive_data"] = values["archive"]
        if header.get("status") != "ok":
            message = str(header.get("error", "container rejected its output"))
            raise ContainerRuntimeError(message)
        return header

    def _open_parent(self, parts: tuple[str, ...], *, create: bool) -> int:
        fd = os.dup(self._root_fd)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            for part in parts:
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, 0o700, dir_fd=fd)
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise ContainerRuntimeError("stage path is not a safe directory")

    def _remove_file(self, path: str) -> None:
        parts = tuple(workspace._safe_relative(path))  # type: ignore[attr-defined]
        parent = self._open_parent(parts[:-1], create=False)
        try:
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ContainerRuntimeError("stage deletion target is not a regular file")
            os.unlink(parts[-1], dir_fd=parent)
        finally:
            os.close(parent)
        for depth in range(len(parts) - 1, 0, -1):
            parent = self._open_parent(parts[:depth - 1], create=False)
            try:
                os.rmdir(parts[depth - 1], dir_fd=parent)
            except OSError as error:
                if error.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                    break
                raise
            finally:
                os.close(parent)

    def _write_file(self, path: str, item: dict[str, Any]) -> None:
        parts = tuple(workspace._safe_relative(path))  # type: ignore[attr-defined]
        parent = self._open_parent(parts[:-1], create=True)
        fd = -1
        temporary_name = ".__goal_native_publish_" + uuid.uuid4().hex
        try:
            mode = 0o700 if item.get("mode") == "100755" else 0o600
            fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent,
            )
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ContainerRuntimeError("stage publication target is not regular")
            data = decode_snapshot_item(item)
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise ContainerRuntimeError("stage publication made no progress")
                offset += written
            os.fchmod(fd, mode)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(temporary_name, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
        except OSError as exc:
            raise ContainerRuntimeError("stage publication target is unsafe") from exc
        finally:
            if fd != -1:
                os.close(fd)
            try:
                os.unlink(temporary_name, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def _publish(
        self,
        before: dict[str, dict[str, Any]],
        after: dict[str, dict[str, Any]],
    ) -> None:
        self._assert_root_stable()
        for path in sorted(set(before) - set(after)):
            self._remove_file(path)
        for path in sorted(after):
            if before.get(path) != after[path]:
                self._write_file(path, after[path])
        self._assert_root_stable()

    def _receipt_without_candidate(
        self,
        *,
        argv: list[str],
        network: bool,
        before_id: str,
        before: dict[str, dict[str, Any]],
        timed_out: bool,
        cancelled: bool,
        docker_stderr: bytes,
    ) -> dict[str, Any]:
        return {
            "argv": argv,
            "exit_code": None,
            "stdout": "",
            "stderr": docker_stderr.decode("utf-8", "replace"),
            "stdout_bytes": 0,
            "stderr_bytes": len(docker_stderr),
            "timed_out": timed_out,
            "cancelled": cancelled,
            "controller_lost": False,
            "output_limited": False,
            "published": False,
            "network": network,
            "enforcement": "docker-local-linux",
            "environment": self._environment_receipt(network),
            "limits": dataclasses.asdict(self.limits),
            "image": self.image,
            "image_reference": self.image_reference,
            "before": {"candidate": before_id, "image": self.image},
            "after": {"candidate": before_id, "image": self.image},
            "before_snapshot": self._snapshot_metadata(before, before_id),
            "after_snapshot": self._snapshot_metadata(before, before_id),
            "candidate": {"before": before_id, "after": before_id, "changes": []},
        }

    def _environment_receipt(self, network: bool) -> dict[str, Any]:
        return {
            "inherited": False,
            "host_mounts": False,
            "docker_socket": False,
            "credentials": False,
            "network": "bridge" if network else "none",
            "dependency_cache": "none; each command owns a disposable container",
            "variables": {
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": "/tmp/home",
                "TMPDIR": "/tmp",
                "LANG": "C.UTF-8",
                "LC_ALL": "C",
                "PYTHONNOUSERSITE": "1",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
            },
        }

    @staticmethod
    def _snapshot_metadata(files: dict[str, dict[str, Any]], identity: str) -> dict[str, Any]:
        return {
            "id": identity,
            "files": len(files),
            "bytes": sum(int(item["bytes"]) for item in files.values()),
        }

    def command(self, arguments: dict[str, Any], *, publish: bool = True) -> dict[str, Any]:
        """Execute one command and optionally publish its validated candidate."""

        if not isinstance(arguments, dict):
            raise ValueError("container command arguments must be an object")
        if set(arguments) - {"argv", "timeout_seconds", "network"}:
            raise ValueError("container command has unsupported arguments")
        argv = validate_argv(arguments.get("argv"))
        requested_timeout = arguments.get("timeout_seconds", self.timeout)
        try:
            timeout = float(requested_timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("container command timeout is invalid") from exc
        if not math.isfinite(timeout) or timeout <= 0 or timeout > self.timeout:
            raise ValueError("container command timeout exceeds the runtime limit")
        network = arguments.get("network", False)
        if not isinstance(network, bool):
            raise ValueError("container command network must be boolean")
        if network and not self.allow_network:
            raise ContainerRuntimeError("network requires the parent allow_network grant")
        if not isinstance(publish, bool):
            raise ValueError("publish must be boolean")
        with self._lock:
            if self._closed:
                raise ContainerRuntimeError("container runtime is closed")
            if self._command_active:
                raise ContainerRuntimeError("container runtime already has an active command")
            if self._cancel_requested.is_set():
                raise ContainerRuntimeError("container runtime was cancelled; use a fresh runtime")
            self._command_active = True
        try:
            return self._command(argv, timeout, network, publish=publish)
        finally:
            with self._lock:
                self._command_active = False

    def _command(
        self, argv: list[str], timeout: float, network: bool, *, publish: bool,
    ) -> dict[str, Any]:

        self._assert_root_stable()
        try:
            before, selection = workspace.snapshot_directory(
                self.root,
                tracked=self._tracked,
                excluded=self._excluded,
                strict=True,
            )
        except (OSError, ValueError) as exc:
            raise ContainerRuntimeError(f"host stage snapshot rejected: {exc}") from exc
        before_id = workspace.snapshot_hash(before)
        archive = snapshot_archive(before, max_bytes=self.limits.max_archive_bytes)
        receipt, docker_stderr, cancelled, hard_killed = self._execute(
            archive,
            argv,
            timeout,
            network,
        )
        if receipt is None:
            return self._receipt_without_candidate(
                argv=argv,
                network=network,
                before_id=before_id,
                before=before,
                timed_out=hard_killed,
                cancelled=cancelled,
                docker_stderr=docker_stderr,
            )

        candidate_archive = receipt.pop("archive_data")
        stdout_data = receipt.pop("stdout_data")
        stderr_data = receipt.pop("stderr_data")
        with tempfile.TemporaryDirectory(prefix="goal-native-candidate-", dir=str(self.root.parent)) as directory:
            try:
                after, _ = extract_candidate_archive(
                    candidate_archive,
                    Path(directory),
                    max_bytes=self.limits.max_archive_bytes,
                    tracked=before,
                    excluded=selection.get("exclusions", self._excluded),
                )
            except ContainerRuntimeError:
                raise
            after_id = workspace.snapshot_hash(after)
            try:
                current, _ = workspace.snapshot_directory(
                    self.root,
                    tracked=self._tracked,
                    excluded=self._excluded,
                    strict=True,
                )
            except (OSError, ValueError) as exc:
                raise ContainerRuntimeError(f"host stage changed during container execution: {exc}") from exc
            if workspace.snapshot_hash(current) != before_id:
                raise ContainerRuntimeError("host stage changed during container execution")
            if publish:
                self._publish(before, after)

        receipt.update({
            "argv": argv,
            "stdout": stdout_data.decode("utf-8", "replace"),
            "stderr": stderr_data.decode("utf-8", "replace"),
            "stdout_bytes": len(stdout_data),
            "stderr_bytes": len(stderr_data),
            "timed_out": bool(receipt.get("timed_out", False)),
            "cancelled": cancelled or bool(receipt.get("controller_lost", False)),
            "published": publish,
            "network": network,
            "enforcement": "docker-local-linux",
            "environment": self._environment_receipt(network),
            "limits": dataclasses.asdict(self.limits),
            "before": {"candidate": before_id, "image": self.image},
            "after": {"candidate": after_id, "image": self.image},
            "image": self.image,
            "image_reference": self.image_reference,
            "before_snapshot": self._snapshot_metadata(before, before_id),
            "after_snapshot": self._snapshot_metadata(after, after_id),
            "candidate": {
                "before": before_id,
                "after": after_id,
                "changes": snapshot_changes(before, after),
            },
        })
        return receipt

    def close(self) -> None:
        """Cancel active work and close controller-only resources."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = self._active
        self.cancel()
        if active:
            self._force_kill(active)
        if self._root_fd != -1:
            try:
                os.close(self._root_fd)
            except OSError:
                pass
            self._root_fd = -1
        self._docker_config.cleanup()

    def __enter__(self) -> "ContainerRuntime":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


__all__ = ["ContainerRuntime"]
