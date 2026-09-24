# syntax=docker/dockerfile:1.7
FROM node@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
       ca-certificates git python3 python3-pip python3-venv \
    && npm install --global --omit=dev --ignore-scripts \
       typescript@6.0.3 \
       typescript-language-server@6.0.0 \
       pyright@1.1.414 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.npm

RUN groupadd --system --gid 65532 goalnative \
    && useradd --system --uid 65532 --gid 65532 --no-create-home goalnative

RUN mkdir -p /usr/local/libexec
RUN cat > /usr/local/libexec/goal-native-container.py <<'PY'
import argparse
import io
import json
import os
import selectors
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath

MAGIC = b"GOAL-NATIVE-CONTAINER/1\n"
U64 = struct.Struct(">Q")
WORKSPACE = Path("/workspace")
UID = 65532
GID = 65532
MAX_ENTRIES = 16384
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_STAGE_BYTES = 64 * 1024 * 1024
GENERATED = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "build",
    "dist",
    ".DS_Store",
}


def exact(stream, amount):
    output = bytearray()
    while len(output) < amount:
        chunk = stream.read(amount - len(output))
        if not chunk:
            return None
        output.extend(chunk)
    return bytes(output)


def safe_parts(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("unsafe archive path")
    path = PurePosixPath(name)
    parts = path.parts
    if (
        path.is_absolute()
        or not parts
        or "/".join(parts) != name
        or any(
            part in {"", ".", ".."} or any(ord(char) < 32 or ord(char) == 127 for char in part)
            for part in parts
        )
    ):
        raise ValueError("unsafe archive path")
    return parts


def mkdirs(parts):
    current = WORKSPACE
    for part in parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("archive path is not a directory")
    return current


def extract_input(payload, archive_limit):
    if len(payload) > archive_limit:
        raise ValueError("input archive is too large")
    seen = set()
    total = 0
    entries = 0
    WORKSPACE.mkdir(mode=0o700, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            entries += 1
            if entries > MAX_ENTRIES:
                raise ValueError("input archive has too many entries")
            parts = safe_parts(member.name)
            name = "/".join(parts)
            if name in seen:
                raise ValueError("input archive contains duplicate paths")
            seen.add(name)
            if member.isdir():
                mkdirs(parts)
                continue
            if not member.isreg() or member.size < 0 or member.size > MAX_FILE_BYTES:
                raise ValueError("input archive contains a non-regular file")
            total += member.size
            if total > MAX_STAGE_BYTES:
                raise ValueError("input snapshot is too large")
            parent = mkdirs(parts[:-1])
            target = parent / parts[-1]
            if target.exists() or target.is_symlink():
                raise ValueError("input archive path collision")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("input archive file has no data")
            fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o700 if member.mode & 0o111 else 0o600,
            )
            try:
                with os.fdopen(fd, "wb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("input archive file ended early")
                        output.write(chunk)
                        remaining -= len(chunk)
            except BaseException:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise
            target.chmod(0o700 if member.mode & 0o111 else 0o600)
    return seen


def walk_error(error):
    raise error


def chown_workspace():
    for current, directories, files in os.walk(WORKSPACE, topdown=False, followlinks=False, onerror=walk_error):
        current_path = Path(current)
        for name in directories + files:
            target = current_path / name
            if target.is_symlink():
                raise ValueError("input snapshot contains a symlink")
            os.chown(target, UID, GID)
    os.chown(WORKSPACE, UID, GID)
    home = Path("/tmp/home")
    home.mkdir(mode=0o700, exist_ok=True)
    os.chown(home, UID, GID)


def archive_workspace(limit, tracked):
    output = io.BytesIO()
    entries = 0
    # All task processes have stopped. Reclaim ownership rather than granting
    # the supervisor DAC-bypass capabilities to read private task files.
    os.chown(WORKSPACE, 0, 0)
    WORKSPACE.chmod(0o700)

    def keep(relative):
        return relative in tracked or not any(part in GENERATED for part in relative.split("/"))

    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for current, directories, files in os.walk(WORKSPACE, topdown=True, followlinks=False, onerror=walk_error):
            current_path = Path(current)
            directories.sort()
            files.sort()
            kept_directories = []
            for name in directories:
                target = current_path / name
                relative = target.relative_to(WORKSPACE).as_posix()
                safe_parts(relative)
                has_tracked_child = any(path.startswith(relative + "/") for path in tracked)
                if not keep(relative) and not has_tracked_child:
                    continue
                info = target.lstat()
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError("candidate has too many entries")
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise ValueError("candidate contains an unsafe directory")
                os.chown(target, 0, 0)
                target.chmod(0o700)
                kept_directories.append(name)
            directories[:] = kept_directories
            for name in files:
                target = current_path / name
                relative = target.relative_to(WORKSPACE).as_posix()
                safe_parts(relative)
                if not keep(relative):
                    continue
                info = target.lstat()
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError("candidate has too many entries")
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise ValueError("candidate contains an unsafe non-regular path")
                if info.st_size > MAX_FILE_BYTES:
                    raise ValueError("candidate file is too large")
                os.chown(target, 0, 0)
                target.chmod(0o700 if info.st_mode & 0o111 else 0o600)
                with target.open("rb") as source:
                    data = source.read(MAX_FILE_BYTES + 1)
                if len(data) != info.st_size or len(data) > MAX_FILE_BYTES:
                    raise ValueError("candidate file changed during archive")
                member = tarfile.TarInfo(relative)
                member.size = len(data)
                member.mode = 0o755 if info.st_mode & 0o111 else 0o644
                member.uid = member.gid = 0
                member.mtime = 0
                archive.addfile(member, io.BytesIO(data))
                if output.tell() > limit:
                    raise ValueError("candidate archive is too large")
    return output.getvalue()


def emit(header, stdout, stderr, archive):
    header = dict(header)
    header["version"] = 1
    header["stdout_bytes"] = len(stdout)
    header["stderr_bytes"] = len(stderr)
    header["archive_bytes"] = len(archive)
    stream = sys.stdout.buffer
    stream.write(MAGIC)
    stream.write(json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    stream.write(b"\n")
    for value in (stdout, stderr, archive):
        stream.write(U64.pack(len(value)))
        stream.write(value)
    stream.flush()


def main():
    separator = sys.argv.index("--") if "--" in sys.argv else -1
    if separator < 0 or separator == len(sys.argv) - 1:
        raise ValueError("container command is missing")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--deadline-seconds", type=float, required=True)
    parser.add_argument("--max-output-bytes", type=int, required=True)
    parser.add_argument("--max-archive-bytes", type=int, required=True)
    options = parser.parse_args(sys.argv[1:separator])
    command = sys.argv[separator + 1:]
    if (
        not command
        or not all(isinstance(value, str) and value and "\x00" not in value for value in command)
        or not (0 < options.deadline_seconds <= 300)
        or options.max_output_bytes <= 0
        or options.max_archive_bytes <= 0
    ):
        raise ValueError("container command limits are invalid")

    def hard_deadline(_signal, _frame):
        os._exit(124)

    # Cover input transfer, execution, descendant cleanup, and output transfer,
    # even if the host controller disappears. PID 1 exit kills its PID namespace.
    signal.signal(signal.SIGALRM, hard_deadline)
    signal.setitimer(signal.ITIMER_REAL, options.deadline_seconds + 2.0)

    prefix = exact(sys.stdin.buffer, len(MAGIC))
    if prefix != MAGIC:
        return 125
    length_data = exact(sys.stdin.buffer, U64.size)
    if length_data is None:
        return 125
    (length,) = U64.unpack(length_data)
    if length > options.max_archive_bytes:
        raise ValueError("input archive is too large")
    payload = exact(sys.stdin.buffer, length)
    if payload is None:
        return 125
    tracked_names = extract_input(payload, options.max_archive_bytes)
    chown_workspace()

    task_environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/tmp/home",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C",
        "PYTHONNOUSERSITE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    child = None
    child_lock = threading.Lock()
    controller_lost = threading.Event()
    finished = threading.Event()

    def kill_child():
        # The command may create new sessions or exit before its descendants.
        # Linux kill(-1) excludes this supervisor and namespace PID 1.
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def guardian():
        try:
            sys.stdin.buffer.read(1)
        except OSError:
            pass
        if not finished.is_set():
            controller_lost.set()
            kill_child()

    try:
        def drop_privileges():
            os.setgroups([])
            os.setgid(GID)
            os.setuid(UID)
            os.umask(0o077)

        with child_lock:
            child = subprocess.Popen(
                command,
                cwd=WORKSPACE,
                env=task_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                preexec_fn=drop_privileges,
            )
        threading.Thread(target=guardian, name="goal-native-stdin-guardian", daemon=True).start()
        assert child.stdout is not None and child.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ, "stdout")
        selector.register(child.stderr, selectors.EVENT_READ, "stderr")
        outputs = {"stdout": bytearray(), "stderr": bytearray()}
        output_limited = False
        timed_out = False
        deadline = time.monotonic() + options.deadline_seconds
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                kill_child()
                break
            for key, _ in selector.select(min(0.1, remaining)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = outputs[key.data]
                room = options.max_output_bytes - len(outputs["stdout"]) - len(outputs["stderr"])
                if room <= 0:
                    output_limited = True
                    kill_child()
                    break
                buffer.extend(chunk[:room])
                if len(chunk) > room:
                    output_limited = True
                    kill_child()
                    break
            if output_limited:
                break
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            kill_child()
            child.wait(timeout=2)
        kill_child()
        while True:
            try:
                os.waitpid(-1, 0)
            except ChildProcessError:
                break
        finished.set()
        selector.close()
        archive = archive_workspace(options.max_archive_bytes, tracked_names)
        emit(
            {
                "status": "ok",
                "exit_code": child.returncode,
                "timed_out": timed_out,
                "controller_lost": controller_lost.is_set(),
                "output_limited": output_limited,
            },
            bytes(outputs["stdout"]),
            bytes(outputs["stderr"]),
            archive,
        )
        return 0
    except Exception as exc:
        finished.set()
        kill_child()
        emit(
            {"status": "output_error", "error": str(exc)[:1000]},
            b"",
            b"",
            b"",
        )
        return 125


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        sys.stderr.write("goal-native-container: " + str(error)[:1000] + "\n")
        raise SystemExit(125)
PY
RUN cat > /usr/local/bin/goal-native-container <<'SH'
#!/bin/sh
exec /usr/bin/python3 /usr/local/libexec/goal-native-container.py "$@"
SH
RUN chmod 0755 /usr/local/bin/goal-native-container /usr/local/libexec/goal-native-container.py

WORKDIR /workspace
