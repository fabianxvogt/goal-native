"""Native Pi sessions over the same Worker admission, tools, and IPC boundary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goal_native.worker import BridgeError, PiBridge


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
        directory = Path(self.metadata["session_dir"]).resolve()
        manifest_path = directory / (self.metadata["session_key"] + ".json")
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("task_id", "registration_sha256", "task_snapshot_sha256",
                    "candidate_identity", "check_identity", "environment_identity",
                    "environment_snapshot_sha256"):
            if manifest.get(key) != self.metadata.get(key):
                raise BridgeError("persisted native session identity changed")
        session_file = Path(manifest["session_file"])
        if session_file.is_symlink() or session_file.resolve().parent != directory or not session_file.is_file():
            return None
        with session_file.open(encoding="utf-8") as source:
            header = json.loads(source.readline(65536))
        identity = manifest.get("session_id")
        if header.get("id") != identity or manifest.get("continuation_ref") != f"pi-session-v1:{identity}":
            raise BridgeError("native session header does not match its manifest")
        return manifest["continuation_ref"]
