"""Policy, limits, and bounded snapshot exchange for the Docker runtime.

The controller never mounts a stage into a task container.  It sends a
validated regular-file snapshot and receives another validated snapshot over
pipes.  Stage publication is deliberately owned by ``container_runner``.
"""
from __future__ import annotations
import base64
import hashlib
import io
import math
import os
import re
import stat
import struct
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import workspace


# The base image is immutable.  The parent builds the runtime Dockerfile from
# this exact base and passes the resulting local image ID (sha256:<64 hex>) to
# ContainerRuntime.  The default tag is resolved locally to that ID before any
# task starts; it is never passed to ``docker run``.
PINNED_BASE_IMAGE = (
    "node@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6"
)
CONTAINER_ENTRYPOINT = "/usr/local/bin/goal-native-container"
ARCHIVE_MAGIC = b"GOAL-NATIVE-CONTAINER/1\n"
ARCHIVE_LENGTH = struct.Struct(">Q")

MAX_TIMEOUT_SECONDS = 300.0
MAX_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
MAX_CPUS = 8.0
MAX_PIDS = 1024
MAX_DISK_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 1 * 1024 * 1024
MAX_ARCHIVE_BYTES = 96 * 1024 * 1024
DEFAULT_IMAGE_REFERENCE = "goal-native-runtime:local"

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContainerRuntimeError(RuntimeError):
    """A Docker run or candidate snapshot was rejected."""


class ContainerUnavailable(ContainerRuntimeError):
    """Docker or the required local image is unavailable."""


@dataclass(frozen=True)
class ContainerLimits:
    """Hard upper bounds exposed to the fixed Docker command builder."""

    max_timeout_seconds: float = MAX_TIMEOUT_SECONDS
    memory_bytes: int = 512 * 1024 * 1024
    cpus: float = 1.0
    pids: int = 128
    disk_bytes: int = MAX_DISK_BYTES
    tmp_bytes: int = 16 * 1024 * 1024
    max_output_bytes: int = MAX_OUTPUT_BYTES
    max_archive_bytes: int = MAX_ARCHIVE_BYTES
    cancel_grace_seconds: float = 2.0

    def __post_init__(self) -> None:
        timeout = float(self.max_timeout_seconds)
        memory = int(self.memory_bytes)
        cpus = float(self.cpus)
        pids = int(self.pids)
        disk = int(self.disk_bytes)
        tmp = int(self.tmp_bytes)
        output = int(self.max_output_bytes)
        archive = int(self.max_archive_bytes)
        grace = float(self.cancel_grace_seconds)
        if (
            not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS
            or memory <= 0 or memory > MAX_MEMORY_BYTES
            or not math.isfinite(cpus) or cpus <= 0 or cpus > MAX_CPUS
            or pids <= 0 or pids > MAX_PIDS
            or disk <= 0 or disk > MAX_DISK_BYTES
            or tmp <= 0 or tmp > disk
            or output <= 0 or output > MAX_OUTPUT_BYTES
            or archive <= 0 or archive > MAX_ARCHIVE_BYTES
            or not math.isfinite(grace) or grace <= 0 or grace > 10.0
        ):
            raise ValueError("container limits are outside the fixed safe bounds")
        if any(isinstance(value, bool) for value in (self.memory_bytes, self.pids)):
            raise ValueError("container numeric limits must not be booleans")


def validate_image_id(image: str) -> str:
    """Accept only a local immutable Docker image ID, never a tag or digest ref."""

    if not isinstance(image, str) or _IMAGE_ID.fullmatch(image) is None:
        raise ValueError("runtime image must be a local immutable sha256 image ID")
    return image


def validate_argv(argv: Iterable[str]) -> list[str]:
    """Validate direct-exec arguments without adding a shell or Docker options."""

    if isinstance(argv, (str, bytes)):
        raise ValueError("command argv must be a sequence of strings")
    try:
        values = list(argv)
    except TypeError as exc:
        raise ValueError("command argv must be a sequence of strings") from exc
    if not values or len(values) > 64:
        raise ValueError("command argv must contain 1 to 64 arguments")
    if any(not isinstance(value, str) or not value or "\x00" in value for value in values):
        raise ValueError("command argv contains an invalid argument")
    if any(len(value) > 32 * 1024 for value in values):
        raise ValueError("command argument is too long")
    try:
        byte_count = sum(len(value.encode("utf-8")) for value in values)
    except UnicodeEncodeError as exc:
        raise ValueError("command argv must be valid UTF-8") from exc
    if byte_count > 64 * 1024:
        raise ValueError("command argv exceeds the size limit")
    return values


def decode_snapshot_item(item: dict[str, Any]) -> bytes:
    """Decode a workspace item only after checking its recorded identity."""

    try:
        data = base64.b64decode(item["data"], validate=True)
        size = int(item["bytes"])
        digest = str(item["sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContainerRuntimeError("malformed workspace snapshot item") from exc
    if size != len(data) or hashlib.sha256(data).hexdigest() != digest:
        raise ContainerRuntimeError("workspace snapshot item hash mismatch")
    return data


def snapshot_archive(files: dict[str, dict[str, Any]], *, max_bytes: int) -> bytes:
    """Encode selected regular files as a deterministic, bounded tar stream."""

    output = io.BytesIO()
    try:
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(files):
                try:
                    workspace._safe_relative(path)  # type: ignore[attr-defined]
                except (AttributeError, ValueError) as exc:
                    raise ContainerRuntimeError("snapshot contains an unsafe path") from exc
                item = files[path]
                data = decode_snapshot_item(item)
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mode = 0o755 if item.get("mode") == "100755" else 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
                if output.tell() > max_bytes:
                    raise ContainerRuntimeError("workspace snapshot archive exceeds the limit")
        payload = output.getvalue()
    except (tarfile.TarError, OSError) as exc:
        if isinstance(exc, ContainerRuntimeError):
            raise
        raise ContainerRuntimeError("could not encode workspace snapshot") from exc
    if len(payload) > max_bytes:
        raise ContainerRuntimeError("workspace snapshot archive exceeds the limit")
    return payload


def _archive_parts(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ContainerRuntimeError("candidate archive contains an unsafe path")
    try:
        parts = workspace._safe_relative(name)  # type: ignore[attr-defined]
    except (AttributeError, ValueError) as exc:
        raise ContainerRuntimeError("candidate archive contains an unsafe path") from exc
    return parts


def _ensure_directory(root: Path, parts: tuple[str, ...]) -> Path:
    current = root
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContainerRuntimeError("candidate archive path is not a safe directory")
    return current


def extract_candidate_archive(
    payload: bytes,
    destination: Path,
    *,
    max_bytes: int,
    tracked: Iterable[str] = (),
    excluded: Iterable[dict[str, str]] = (),
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Safely extract regular-file output, then apply canonical workspace policy."""

    if len(payload) > max_bytes:
        raise ContainerRuntimeError("candidate archive exceeds the limit")
    destination.mkdir(mode=0o700, exist_ok=True)
    seen: set[str] = set()
    total_bytes = 0
    total_entries = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            for member in archive:
                total_entries += 1
                if total_entries > workspace.MAX_ENTRIES:
                    raise ContainerRuntimeError("candidate archive has too many entries")
                parts = _archive_parts(member.name)
                name = "/".join(parts)
                if name in seen:
                    raise ContainerRuntimeError("candidate archive contains duplicate paths")
                seen.add(name)
                if member.isdir():
                    _ensure_directory(destination, parts)
                    continue
                if not member.isreg() or member.size < 0 or member.size > workspace.MAX_FILE_BYTES:
                    raise ContainerRuntimeError("candidate archive contains a non-regular file")
                total_bytes += member.size
                if total_bytes > workspace.MAX_BYTES:
                    raise ContainerRuntimeError("candidate snapshot exceeds the size limit")
                parent = _ensure_directory(destination, parts[:-1])
                target = parent / parts[-1]
                if target.exists() or target.is_symlink():
                    raise ContainerRuntimeError("candidate archive path collides with an existing entry")
                source = archive.extractfile(member)
                if source is None:
                    raise ContainerRuntimeError("candidate archive file has no data")
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
                                raise ContainerRuntimeError("candidate archive file ended early")
                            output.write(chunk)
                            remaining -= len(chunk)
                except BaseException:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    raise
                target.chmod(0o700 if member.mode & 0o111 else 0o600)
    except (tarfile.TarError, OSError) as exc:
        if isinstance(exc, ContainerRuntimeError):
            raise
        raise ContainerRuntimeError("candidate archive is not valid") from exc

    try:
        files, selection = workspace.snapshot_directory(
            destination,
            tracked=tracked,
            excluded=excluded,
            strict=True,
        )
    except (OSError, ValueError) as exc:
        raise ContainerRuntimeError(f"candidate snapshot rejected: {exc}") from exc
    return files, selection


def snapshot_changes(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return deterministic file additions, modifications, mode changes, and deletions."""

    changes: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old is None:
            changes.append({"path": path, "status": "added", "sha256": new["sha256"]})
        elif new is None:
            changes.append({"path": path, "status": "deleted", "sha256": old["sha256"]})
        elif any(old[key] != new[key] for key in ("sha256", "bytes", "mode")):
            changes.append({
                "path": path,
                "status": "modified",
                "before_sha256": old["sha256"],
                "after_sha256": new["sha256"],
                "before_mode": old["mode"],
                "after_mode": new["mode"],
            })
    return changes


__all__ = [
    "ARCHIVE_LENGTH",
    "ARCHIVE_MAGIC",
    "CONTAINER_ENTRYPOINT",
    "ContainerLimits",
    "ContainerRuntime",
    "ContainerRuntimeError",
    "ContainerUnavailable",
    "DEFAULT_IMAGE_REFERENCE",
    "MAX_ARCHIVE_BYTES",
    "MAX_DISK_BYTES",
    "MAX_OUTPUT_BYTES",
    "PINNED_BASE_IMAGE",
    "decode_snapshot_item",
    "extract_candidate_archive",
    "snapshot_archive",
    "snapshot_changes",
    "validate_argv",
    "validate_image_id",
]

def __getattr__(name: str) -> Any:
    if name == "ContainerRuntime":
        from .container_runner import ContainerRuntime
        return ContainerRuntime
    raise AttributeError(name)
