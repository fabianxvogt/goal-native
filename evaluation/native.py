"""Native Pi sessions over the same Worker admission, tools, and IPC boundary."""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

from goal_native.worker import BridgeError, PiBridge


def _regular_file_without_symlinks(path: Path | str) -> Path | None:
    if not isinstance(path, (Path, str)):
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        return None
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current /= component
        try:
            details = current.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(details.st_mode):
            return None
        if current != candidate and not stat.S_ISDIR(details.st_mode):
            return None
    try:
        details = candidate.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(details.st_mode):
        return None
    return candidate.resolve(strict=True)


class NativeSessionBridge(PiBridge):
    """Keep native history, but never add built-in host tools or Goal Native context."""

    def __init__(self, *, metadata: dict[str, Any], **options: Any) -> None:
        super().__init__(bridge_path=Path(__file__).resolve().parents[1] / "bridge/native_pi.mjs", **options)
        self.metadata = dict(metadata)
        self.observation: dict[str, Any] | None = None

    def run(self, request: dict[str, Any], **callbacks: Any) -> dict[str, Any]:
        self.observation = None
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[-1].get("content"), str):
            raise BridgeError("native session requires the current user request")
        # Native Pi retains its ordinary SessionManager transcript. Giving it the
        # Goal Native work-bundle compiler's context would collapse the comparison.
        native_request = {
            **self.metadata,
            **{key: value for key, value in request.items() if key != "messages"},
            "prompt": messages[-1]["content"],
            "max_time_seconds": request["timeout_ms"] / 1000,
        }
        result = super().run(native_request, **callbacks)
        if result.get("protocol") != "goal-native-pi-session-result/v1":
            raise BridgeError("native session returned an invalid result protocol")
        statuses = {"completed": "finished", "failed": "failed", "cancelled": "cancelled", "interrupted": "interrupted"}
        if result.get("status") not in statuses:
            raise BridgeError("native session returned an invalid status")
        self.observation = result
        return {
            "status": statuses[result["status"]],
            "stop_reason": result.get("stop_reason"),
            "result": result["observations"]["assistant_text"],
            "rounds": result["metrics"]["rounds"],
        }

    def continuation_reference(self) -> str | None:
        """Recover only a real persisted session, including after an interrupted bridge."""
        key = self.metadata.get("session_key")
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", key) is None:
            raise BridgeError("session_key must be an explicit safe identifier")
        directory = Path(self.metadata["session_dir"]).resolve()
        manifest_path = directory / f"{key}.json"
        try:
            manifest_details = manifest_path.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(manifest_details.st_mode) or not stat.S_ISREG(manifest_details.st_mode):
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("task_id", "registration_sha256", "task_snapshot_sha256",
                    "candidate_identity", "check_identity", "environment_identity",
                    "environment_snapshot_sha256"):
            if manifest.get(key) != self.metadata.get(key):
                raise BridgeError("persisted native session identity changed")
        session_file = _regular_file_without_symlinks(manifest.get("session_file"))
        if session_file is None or session_file == directory:
            return None
        try:
            session_file.relative_to(directory)
        except ValueError:
            return None
        with session_file.open(encoding="utf-8") as source:
            header = json.loads(source.readline(65536))
        identity = manifest.get("session_id")
        if header.get("id") != identity or manifest.get("continuation_ref") != f"pi-session-v1:{identity}":
            raise BridgeError("native session header does not match its manifest")
        return manifest["continuation_ref"]
