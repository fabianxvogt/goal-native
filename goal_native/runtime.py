"""Read-only source-runtime prerequisites shared by bootstrap and doctor."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def pi_source_status(root: Path, git: str | None) -> dict[str, Any]:
    parent = root / "upstream"
    source = parent / "pi"
    safe = parent.is_dir() and not parent.is_symlink() and source.is_dir() and not source.is_symlink()
    initialized = safe and not (source / ".git").is_symlink() and (source / ".git").exists() and (source / "package.json").is_file()
    status: dict[str, Any] = {
        "ok": False, "safe_directory": safe, "initialized": initialized,
        "pinned": None, "clean": None, "expected_revision": None, "actual_revision": None,
    }
    if not initialized:
        status["error"] = "pi must be an initialized real source directory, not a symlink"
        return status
    if git is None:
        status["error"] = "Git is required to verify the pinned pi source"
        return status
    try:
        def inspect(directory: Path, *arguments: str) -> str:
            return subprocess.run(
                [git, "-C", str(directory), *arguments], check=True,
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()

        status["expected_revision"] = inspect(root, "rev-parse", "--verify", "HEAD:upstream/pi")
        status["actual_revision"] = inspect(source, "rev-parse", "--verify", "HEAD")
        status["pinned"] = status["actual_revision"] == status["expected_revision"]
        status["clean"] = not inspect(source, "status", "--porcelain")
    except (OSError, subprocess.SubprocessError):
        status["error"] = "pi Git metadata could not be verified"
        return status
    if not status["pinned"]:
        status["error"] = "pi HEAD does not match the checkout's recorded submodule revision"
    elif not status["clean"]:
        status["error"] = "pi has uncommitted changes; preserve them before restoring the pinned source"
    else:
        status["ok"] = True
    return status
