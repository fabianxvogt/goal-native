"""Controller-owned source snapshots and review-bound, export-only code delivery.

No provider tools or host application live here. Snapshots contain selected bytes;
portable code exports never contain controller records or host recovery paths.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

MAX_FILES = 4096
MAX_ENTRIES = 16384
MAX_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
_GENERATED = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
              ".mypy_cache", ".ruff_cache", ".cache", "build", "dist", ".DS_Store"}


def blocked_component(name: str) -> bool:
    lowered = name.casefold()
    credential_name = re.search(
        r"(?:^|[_.-])(api[_-]?key|access[_-]?key|client[_-]?secret|password|passwd|auth|credential|token)(?:$|[_.-])",
        lowered,
    )
    return bool(
        lowered == ".env" or lowered.startswith(".env.")
        or lowered in {".git", ".state", "credentials", "credential", "private", "secret",
                       "secrets", "controller", ".ssh", ".aws", "store.sqlite3",
                       "store.sqlite3-wal", "store.sqlite3-shm", "stages"}
        or "credential" in lowered or "secret" in lowered or credential_name
        or lowered.endswith((".pem", ".key", ".p12", ".pfx", ".secret"))
        or lowered in {"id_rsa", "id_ed25519", ".npmrc"}
    )


def _safe_relative(value: str) -> tuple[str, ...]:
    parts = value.split("/")
    if (not value or any(part in {"", ".", ".."} for part in parts)
            or "\\" in value or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
        raise ValueError("workspace paths must be safe relative filenames")
    return tuple(parts)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_hash(files: dict[str, dict[str, Any]]) -> str:
    manifest = {path: {key: item[key] for key in ("sha256", "mode", "bytes")}
                for path, item in sorted(files.items())}
    return hashlib.sha256(_json(manifest).encode()).hexdigest()


def _file(data: bytes, mode: int) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "mode": "100755" if mode & 0o111 else "100644",
            "data": base64.b64encode(data).decode("ascii")}


def _bytes(item: dict[str, Any]) -> bytes:
    data = base64.b64decode(item["data"], validate=True)
    if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("workspace snapshot content does not match its recorded hash")
    return data


def _ignored_paths(root: Path, candidates: list[str]) -> set[str]:
    if not any(Path(path).name == ".gitignore" for path in candidates):
        return set()
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required to honor .gitignore; install Git before selecting this source")
    # Use Git's exact ignore semantics, without repository/global config, hooks,
    # the source index, or modifying the selected checkout (also works outside Git).
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_TERMINAL_PROMPT": "0"}
    with tempfile.TemporaryDirectory(prefix="goal-native-ignore-") as temporary:
        init = subprocess.run([git, "init", "--bare", "--quiet", "--template=", temporary],
                              env=environment, capture_output=True, timeout=10)
        if init.returncode:
            raise ValueError("Git could not prepare source ignore selection")
        result = subprocess.run(
            [git, "-c", "core.bare=false", f"--git-dir={temporary}", f"--work-tree={root}",
             "check-ignore", "--no-index", "-z", "--stdin"],
            input=b"\0".join(path.encode("utf-8") for path in candidates) + b"\0",
            env=environment, capture_output=True, timeout=10, cwd=root,
        )
        if result.returncode not in {0, 1}:
            raise ValueError("Git could not evaluate source .gitignore rules")
        return {path.decode("utf-8") for path in result.stdout.split(b"\0") if path}


def snapshot_directory(
    root: Path, *, tracked: Iterable[str] = (), excluded: Iterable[dict[str, str]] = (),
    strict: bool = False, only: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read bounded no-follow files. Exclusions are visible, never deletions.

    Tracked baseline files cannot disappear merely because .gitignore changes.
    Strict candidate capture refuses unsafe/unreadable/oversized files rather
    than approving an incomplete export. Generated/ignored paths remain omitted.
    """
    root = Path(root).expanduser().absolute()
    if blocked_component(root.name):
        raise ValueError("source directory names a blocked credential or controller path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, flags)
    pinned = os.fstat(root_fd)
    tracked = set(tracked)
    original_exclusions = {item["path"]: item["reason"] for item in excluded}
    omissions: dict[str, str] = {}
    candidates: list[str] = []
    entries = 0

    def omit(path: str, reason: str, *, unsafe: bool = False) -> None:
        if strict and unsafe:
            raise ValueError(f"cannot review {path}: {reason}")
        omissions[path] = reason

    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal entries
        if depth > 64:
            raise ValueError("source directory exceeds the depth limit")
        with os.scandir(fd) as iterator:
            names = []
            for entry in iterator:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError("source directory exceeds the entry limit; select a smaller source")
                names.append(entry.name)
        for name in sorted(names):
            path = prefix + name
            _safe_relative(path)
            if only is not None and not any(p == path or p.startswith(path + "/") for p in only):
                continue
            if blocked_component(name):
                omit(path, "credential/controller name", unsafe=True)
                continue
            if name in _GENERATED and path not in tracked:
                omit(path, "generated/dependency path")
                continue
            inherited = next((reason for p, reason in original_exclusions.items()
                              if path == p or path.startswith(p + "/")), None)
            if inherited and path not in tracked:
                omit(path, inherited)
                continue
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                omit(path, "symlink", unsafe=True)
            elif stat.S_ISDIR(info.st_mode):
                child = os.open(name, flags, dir_fd=fd)
                try:
                    walk(child, path + "/", depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                candidates.append(path)
            else:
                omit(path, "non-regular file", unsafe=True)

    files: dict[str, dict[str, Any]] = {}
    total = 0
    try:
        walk(root_fd, "", 0)
        ignored = _ignored_paths(root, candidates) if only is None else set()
        for path in candidates:
            if path in ignored and path not in tracked:
                omit(path, ".gitignore")
                continue
            parts = _safe_relative(path)
            parent = os.dup(root_fd)
            file_fd = -1
            try:
                for component in parts[:-1]:
                    child = os.open(component, flags, dir_fd=parent)
                    os.close(parent)
                    parent = child
                file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"source file changed type during selection: {path}")
                if (len(files) >= MAX_FILES or info.st_size > MAX_FILE_BYTES
                        or total + info.st_size > MAX_BYTES):
                    omit(path, "file/byte limit", unsafe=True)
                    continue
                data = bytearray()
                limit = min(MAX_FILE_BYTES, MAX_BYTES - total)
                while len(data) <= limit:
                    chunk = os.read(file_fd, min(65536, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                after = os.fstat(file_fd)
                if len(data) > limit:
                    raise ValueError(f"source file grew beyond the selection limit: {path}")
                if (info.st_mtime_ns, info.st_ctime_ns, info.st_size) != (
                        after.st_mtime_ns, after.st_ctime_ns, after.st_size):
                    raise ValueError(f"source file changed during snapshot: {path}")
                files[path] = _file(bytes(data), info.st_mode)
                total += len(data)
            finally:
                if file_fd != -1:
                    os.close(file_fd)
                os.close(parent)
        current = root.lstat()
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("source root changed during snapshot")
    finally:
        os.close(root_fd)
    exclusions = [{"path": path, "reason": reason} for path, reason in sorted(omissions.items())]
    return files, {"files": len(files), "bytes": total,
                   "symlinks": sum(item["reason"] == "symlink" for item in exclusions),
                   "non_regular": sum(item["reason"] == "non-regular file" for item in exclusions),
                   "excluded": len(exclusions), "selected": sorted(files), "exclusions": exclusions}


def copy_selected_directory(source: str, destination: Path) -> dict[str, Any]:
    files, selection = snapshot_directory(Path(source))
    for path, item in files.items():
        target = destination.joinpath(*_safe_relative(path))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(_bytes(item))
        target.chmod(0o700 if item["mode"] == "100755" else 0o600)
    return selection


def _git_path(prefix: str, path: str) -> str:
    return json.dumps(prefix + path, ensure_ascii=False)


def _blob_hash(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def make_patch(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    patch: list[str] = []
    changes: list[dict[str, Any]] = []
    for path in sorted(before.keys() | after.keys()):
        _safe_relative(path)
        old, new = before.get(path), after.get(path)
        if old and new and (old["sha256"], old["mode"]) == (new["sha256"], new["mode"]):
            continue
        old_bytes, new_bytes = _bytes(old) if old else b"", _bytes(new) if new else b""
        binary = b"\0" in old_bytes or b"\0" in new_bytes
        try:
            old_text, new_text = old_bytes.decode("utf-8"), new_bytes.decode("utf-8")
        except UnicodeDecodeError:
            binary = True
            old_text = new_text = ""
        change = {"path": path, "status": "added" if old is None else "deleted" if new is None else "modified",
                  "before": {k: old[k] for k in ("sha256", "mode", "bytes")} if old else None,
                  "after": {k: new[k] for k in ("sha256", "mode", "bytes")} if new else None,
                  "binary": binary}
        changes.append(change)
        if binary:
            continue
        patch.append(f"diff --git {_git_path('a/', path)} {_git_path('b/', path)}\n")
        if old is None:
            patch.append(f"new file mode {new['mode']}\n")
        elif new is None:
            patch.append(f"deleted file mode {old['mode']}\n")
        elif old["mode"] != new["mode"]:
            patch.extend([f"old mode {old['mode']}\n", f"new mode {new['mode']}\n"])
        patch.append(f"index {_blob_hash(old_bytes) if old else '0' * 40}..{_blob_hash(new_bytes) if new else '0' * 40}\n")
        for line in difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                         fromfile=_git_path("a/", path) if old else "/dev/null",
                                         tofile=_git_path("b/", path) if new else "/dev/null"):
            patch.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(patch), changes


def review_workspace(store: Any, goal_id: str, stage: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    baseline = store.workspace_baseline(goal_id, stage.name)
    if baseline is None:
        raise ValueError("No input baseline exists for these older files. Continue once to establish a baseline; earlier edits cannot be reconstructed.")
    before = baseline["files"]
    after, selection = snapshot_directory(stage, tracked=before,
                                           excluded=baseline["selection"].get("exclusions", []), strict=True)
    patch, changes = make_patch(before, after)
    source = baseline["selection"].get("source_root")
    source_status: dict[str, Any] = {"state": "not_selected", "changed_paths": []}
    source_hash = None
    if source:
        try:
            current, _ = snapshot_directory(Path(source), tracked=before, strict=True,
                                              only=set(before) | {item["path"] for item in changes})
            source_hash = snapshot_hash(current)
            divergent = sorted(path for path in before.keys() | current.keys()
                               if (before.get(path) or {}).get("sha256") != (current.get(path) or {}).get("sha256")
                               or (before.get(path) or {}).get("mode") != (current.get(path) or {}).get("mode"))
            source_status = {"state": "changed" if divergent else "matching", "changed_paths": divergent}
        except (OSError, ValueError):
            source_status = {"state": "unavailable", "changed_paths": []}
    goal = store.goal(goal_id)
    identity = {"goal_id": goal_id, "stage": stage.name,
                "contract": {key: goal[key] for key in ("revision", "input_version", "authority_version")},
                "baseline": snapshot_hash(before), "candidate": snapshot_hash(after),
                "source": source_status, "source_hash": source_hash}
    review_id = hashlib.sha256(_json(identity).encode()).hexdigest()
    report = {"review_id": review_id, "baseline_hash": identity["baseline"], "candidate_hash": identity["candidate"],
              "changes": changes, "patch": patch, "patch_supported": not any(c["binary"] for c in changes),
              "source_status": source_status, "selection": baseline["selection"],
              "candidate_exclusions": selection["exclusions"],
              "note": "Review binds these bytes, not correctness, acceptance, or permission to overwrite a project."}
    return report, after


def export_reviewed(store: Any, goal_id: str, stage: Path, review_id: str,
                    output: Path, format: str = "patch") -> dict[str, Any]:
    if format not in {"patch", "files"}:
        raise ValueError("code export format must be patch or files")
    report, files = review_workspace(store, goal_id, stage)
    if not isinstance(review_id, str) or report["review_id"] != review_id:
        raise ValueError("Reviewed work or source state changed. Run diff again before exporting.")
    if not report["changes"]:
        raise ValueError("No staged changes to export")
    if format == "patch":
        if not report["patch_supported"]:
            raise ValueError("Binary changes require --format files; no partial patch was exported")
        data = report["patch"].encode("utf-8")
    else:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest = {key: report[key] for key in ("review_id", "baseline_hash", "candidate_hash", "changes", "source_status", "note")}
            manifest["layout"] = "files/ contains the complete selected candidate; changes lists deletions relative to the baseline"
            archive.writestr("manifest.json", _json(manifest) + "\n")
            for path, item in sorted(files.items()):
                info = zipfile.ZipInfo("files/" + path)
                info.external_attr = int(item["mode"], 8) << 16
                archive.writestr(info, _bytes(item))
        data = buffer.getvalue()
    output = Path(output).expanduser().absolute()
    protected = [store.root, stage]
    if report["selection"].get("source_root"):
        protected.append(Path(report["selection"]["source_root"]))
    resolved_parent = output.parent.resolve(strict=True)
    destination = resolved_parent / output.name
    if any(destination.is_relative_to(path.resolve()) for path in protected):
        raise ValueError("Export outside the selected source and controller state; host project files are never overwritten")
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {"exported": True, "format": format, "output_path": str(destination),
            "review_id": review_id, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "source_status": report["source_status"]}
