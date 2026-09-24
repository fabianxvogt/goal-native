"""Bounded public source archives and immutable external corpus snapshots."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from goal_native import workspace

SOURCE_ROOT = Path(__file__).resolve().parents[1]
CORPUS_SCHEMA = "goal-native-issue-corpus/v1"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 40_000
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024


def prepare_sources(task: Mapping[str, Any], root: Path, *, license_path: str) -> dict[str, Any]:
    """Materialize a caller-validated frozen task; never execute downloaded code."""
    corpus_root = _external_root(root)
    task_id = task["id"]
    task_root = corpus_root / "tasks" / task_id
    task_root.mkdir(parents=True, exist_ok=True)
    manifest_path = task_root / "preparation.json"
    previous = None
    if manifest_path.exists():
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("preparation manifest is not a regular file")
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or previous.get("schema") != CORPUS_SCHEMA:
            raise ValueError("unrecognized preparation manifest")
        if previous.get("task_id") != task_id or previous.get("registration_provenance") != task["provenance"]:
            raise ValueError("prepared corpus no longer matches the frozen task")
    provenance = task["provenance"]
    repository = task["repository"]
    source_commit, fix_commit = provenance["source_commit"], provenance["fix_commit"]
    source_url = f"https://github.com/{repository}/archive/{source_commit}.tar.gz"
    reference_url = f"https://github.com/{repository}/archive/{fix_commit}.tar.gz"
    source_archive, source_hash = _archive(source_url, corpus_root / "archives" / f"{task_id}-source.tar.gz")
    reference_archive, reference_hash = _archive(reference_url, corpus_root / "archives" / f"{task_id}-reference.tar.gz")
    if previous is not None:
        for name, actual in (("source_archive_sha256", source_hash), ("reference_archive_sha256", reference_hash)):
            if previous["provenance"].get(name) != actual:
                raise ValueError(f"prepared archive changed: {name}")
    source_dir, reference_dir = task_root / "source", task_root / "reference"
    source_snapshot, source_selection = _materialize(source_archive, source_dir, previous.get("source_snapshot_sha256") if previous else None)
    reference_snapshot, reference_selection = _materialize(reference_archive, reference_dir, previous.get("reference_snapshot_sha256") if previous else None)
    license_info = _license(source_dir, reference_dir, license_path, provenance["license_spdx"])
    license_info["source_url"] = f"https://raw.githubusercontent.com/{repository}/{source_commit}/{license_path}"
    license_info["reference_url"] = f"https://raw.githubusercontent.com/{repository}/{fix_commit}/{license_path}"
    result = {
        "schema": CORPUS_SCHEMA, "task_id": task_id,
        "registration_provenance": dict(provenance),
        "source_dir": source_dir, "reference_dir": reference_dir,
        "source_snapshot_sha256": source_snapshot, "reference_snapshot_sha256": reference_snapshot,
        "source_selection": source_selection, "reference_selection": reference_selection,
        "provenance": {
            **provenance, "repository": repository, "repository_url": f"https://github.com/{repository}",
            "source_archive_url": source_url, "reference_archive_url": reference_url,
            "source_archive_sha256": source_hash, "reference_archive_sha256": reference_hash,
            "license": license_info,
        },
    }
    serialized = json.dumps(_jsonable(result), sort_keys=True, indent=2) + "\n"
    if previous is None:
        with manifest_path.open("x", encoding="utf-8") as output:
            output.write(serialized)
    elif previous != _jsonable(result):
        raise ValueError("prepared corpus metadata changed")
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _external_root(root: Path) -> Path:
    raw = Path(root).expanduser()
    if raw.is_symlink():
        raise ValueError("corpus root must not be a symlink")
    resolved = raw.resolve()
    if resolved == SOURCE_ROOT or SOURCE_ROOT in resolved.parents:
        raise ValueError("corpus root must be outside the Goal Native repository")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def fetch_archive(url: str) -> bytes:
    """Download one bounded public GitHub archive without credentials."""
    request = urllib.request.Request(url, headers={"User-Agent": "goal-native-python-evaluation/1"})
    with urllib.request.urlopen(request, timeout=90) as response:
        content = bytearray()
        while True:
            chunk = response.read(min(1024 * 1024, MAX_ARCHIVE_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_ARCHIVE_BYTES:
                raise ValueError("source archive exceeds the preparation limit")
    return bytes(content)


def _safe_member_parts(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("archive contains an unsafe path")
    path = PurePosixPath(name)
    parts = path.parts
    if path.is_absolute() or not parts or "/".join(parts) != name:
        raise ValueError("archive contains an unsafe path")
    if any(part in {"", ".", ".."} or any(ord(char) < 32 or ord(char) == 127 for char in part) for part in parts):
        raise ValueError("archive contains an unsafe path")
    return parts


def safe_extract_archive(archive_bytes: bytes, destination: Path) -> None:
    """Extract a GitHub tarball, stripping its single generated top directory."""
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("source archive exceeds the preparation limit")
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"extraction destination already exists: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    top_level: str | None = None
    seen: set[str] = set()
    total = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(archive_bytes)) as compressed:
            expanded = compressed.read(MAX_EXTRACTED_BYTES + 1)
        if len(expanded) > MAX_EXTRACTED_BYTES:
            raise ValueError("source archive expands beyond the preparation limit")
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for number, member in enumerate(archive, 1):
                if number > MAX_ARCHIVE_ENTRIES:
                    raise ValueError("source archive contains too many entries")
                parts = _safe_member_parts(member.name)
                if top_level is None:
                    top_level = parts[0]
                if parts[0] != top_level:
                    raise ValueError("source archive has multiple top-level directories")
                relative_parts = parts[1:]
                if not relative_parts:
                    if not member.isdir():
                        raise ValueError("archive top-level entry is not a directory")
                    continue
                relative = "/".join(relative_parts)
                if relative in seen:
                    raise ValueError("source archive contains duplicate paths")
                seen.add(relative)
                if member.issym() or member.islnk() or not (member.isdir() or member.isreg()):
                    raise ValueError("source archive contains a link or non-regular file")
                target = destination.joinpath(*relative_parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=False)
                    continue
                if member.size < 0 or member.size > MAX_EXTRACTED_BYTES or total + member.size > MAX_EXTRACTED_BYTES:
                    raise ValueError("source archive expands beyond the preparation limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("source archive file has no data")
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("source archive file ended early")
                        output.write(chunk)
                        remaining -= len(chunk)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
                total += member.size
    except BaseException:
        _remove_tree(destination)
        raise
    if top_level is None:
        _remove_tree(destination)
        raise ValueError("source archive is empty")


def _remove_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        path.unlink()
        return
    for child in path.iterdir():
        _remove_tree(child)
    path.rmdir()


def _archive(url: str, path: Path) -> tuple[bytes, str]:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"archive path is not a regular file: {path}")
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError(f"archive exceeds the preparation limit: {path}")
        data = path.read_bytes()
    else:
        data = fetch_archive(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
        temporary.write_bytes(data)
        os.replace(temporary, path)
    return data, _sha256(data)


def _snapshot(path: Path) -> tuple[str, dict[str, Any]]:
    files, selection = workspace.snapshot_directory(path, strict=False)
    return workspace.snapshot_hash(files), selection


def _materialize(
    archive_bytes: bytes,
    destination: Path,
    expected_snapshot: str | None,
) -> tuple[str, dict[str, Any]]:
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError(f"source directory is not a real directory: {destination}")
        snapshot, selection = _snapshot(destination)
        if expected_snapshot is None:
            raise ValueError(f"existing unrecorded source directory must not be reused: {destination}")
        if snapshot != expected_snapshot:
            raise ValueError(f"prepared source was modified outside the controller: {destination}")
        return snapshot, selection
    temporary = destination.with_name(destination.name + f".tmp-{uuid.uuid4().hex}")
    safe_extract_archive(archive_bytes, temporary)
    try:
        snapshot, selection = _snapshot(temporary)
        os.replace(temporary, destination)
    except BaseException:
        _remove_tree(temporary)
        raise
    return snapshot, selection
def _license(source_dir: Path, reference_dir: Path, path: str, spdx: str) -> dict[str, Any]:
    source_license = source_dir / path
    reference_license = reference_dir / path
    if not source_license.is_file() or source_license.is_symlink() or not reference_license.is_file() or reference_license.is_symlink():
        raise ValueError(f"pinned license file is missing: {path}")
    source_bytes = source_license.read_bytes()
    reference_bytes = reference_license.read_bytes()
    return {
        "path": path,
        "spdx": spdx,
        "sha256": _sha256(source_bytes),
        "reference_sha256": _sha256(reference_bytes),
        "source_url": None,
        "reference_url": None,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
