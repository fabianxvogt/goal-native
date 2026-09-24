#!/usr/bin/env python3
"""Bootstrap a supported Goal Native source checkout without touching user work."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


MIN_PYTHON = (3, 11)
MIN_NODE = (22, 19, 0)
ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / "bin" / "python"
ENTRYPOINT = VENV / "bin" / "goal-native"


class BootstrapError(RuntimeError):
    """A prerequisite or non-destructive setup step failed."""


def _command_display(command: list[str]) -> str:
    return shlex.join(command)


def _run(
    command: list[str],
    *,
    label: str,
    remediation: str,
    cwd: Path = ROOT,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            check=False,
            capture_output=capture_output,
        )
    except OSError as exc:
        raise BootstrapError(f"{label} could not start ({exc}). Remediation: {remediation}") from exc
    if result.returncode != 0:
        raise BootstrapError(
            f"{label} failed with exit code {result.returncode}: {_command_display(command)}. "
            f"Remediation: {remediation}"
        )
    return result


def _find_command(name: str, remediation: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BootstrapError(f"{name} is required but was not found on PATH. Remediation: {remediation}")
    return path


def _node_version(node: str) -> tuple[int, int, int]:
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError(
            f"Node.js could not report its version ({exc.__class__.__name__}). "
            "Remediation: install Node.js 22.19+ and ensure node is on PATH."
        ) from exc
    version = (result.stdout.strip() or result.stderr.strip()).removeprefix("v")
    parts = version.split(".")
    try:
        parsed = tuple(int(part) for part in parts[:3])
    except ValueError as exc:
        parsed = ()
        error = exc
    else:
        error = None
    if result.returncode != 0 or len(parsed) != 3 or error is not None:
        raise BootstrapError(
            f"Node.js version could not be validated (reported {version or 'nothing'}). "
            "Remediation: install Node.js 22.19+ and ensure node is on PATH."
        )
    return parsed  # type: ignore[return-value]


def _ensure_source_checkout(git: str) -> None:
    result = _run(
        [git, "-C", str(ROOT), "rev-parse", "--show-toplevel"],
        label="Git checkout detection",
        remediation="run this command from a fresh Git checkout of goal-native",
        capture_output=True,
    )
    detected = Path(result.stdout.strip()).resolve()
    if detected != ROOT:
        raise BootstrapError(
            f"bootstrap script is not inside its Git checkout (detected {detected}). "
            "Remediation: run the script from the checkout that contains it."
        )


def _ensure_pi_submodule(git: str) -> None:
    submodule = ROOT / "upstream" / "pi"
    package = submodule / "package.json"
    git_marker = submodule / ".git"
    remediation = "run git submodule update --init --recursive in the checkout, then rerun bootstrap"
    if submodule.is_symlink() or (submodule.exists() and not submodule.is_dir()):
        raise BootstrapError(
            f"{submodule} is not a safe submodule directory; it was not changed. "
            f"Remediation: move that path aside yourself, then {remediation}."
        )
    if package.is_file() and git_marker.exists():
        status = _run(
            [git, "-C", str(submodule), "status", "--porcelain"],
            label="pi submodule safety check",
            remediation="repair the pi submodule Git metadata, then rerun bootstrap",
            capture_output=True,
        )
        if status.stdout.strip():
            raise BootstrapError(
                f"{submodule} has uncommitted changes; the checkout was not changed. "
                "Remediation: commit or stash those changes yourself, then rerun bootstrap."
            )
    else:
        if submodule.exists() and any(submodule.iterdir()):
            raise BootstrapError(
                f"{submodule} exists but is not an initialized pinned submodule; it was not changed. "
                f"Remediation: move that directory aside yourself, then {remediation}."
            )
        _run(
            [git, "-C", str(ROOT), "submodule", "update", "--init", "--recursive"],
            label="pi submodule initialization",
            remediation="verify Git access to the pinned pi submodule, then rerun bootstrap",
        )
        if not package.is_file() or not git_marker.exists():
            raise BootstrapError(f"the pinned pi submodule is unavailable. Remediation: {remediation}.")
    expected = _run(
        [git, "-C", str(ROOT), "rev-parse", "HEAD:upstream/pi"],
        label="pi Git pin lookup", remediation=remediation, capture_output=True,
    ).stdout.strip()
    actual = _run(
        [git, "-C", str(submodule), "rev-parse", "HEAD"],
        label="pi revision lookup", remediation=remediation, capture_output=True,
    ).stdout.strip()
    if actual != expected:
        raise BootstrapError(
            f"pi is at {actual}, but this checkout pins {expected}; it was not changed. "
            f"Remediation: {remediation}."
        )


def _ensure_venv(python: str) -> str:
    if VENV.is_symlink():
        raise BootstrapError(
            f"existing {VENV} is a symlink; it was not followed or replaced. "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap with Python 3.11+."
        )
    if not VENV.exists():
        _run(
            [python, "-m", "venv", str(VENV)],
            label="Python virtual-environment creation",
            remediation="install Python 3.11+ with venv support, then rerun bootstrap",
        )
    elif not VENV_PYTHON.is_file():
        raise BootstrapError(
            f"existing {VENV} is incomplete; it was not replaced. "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap with Python 3.11+."
        )

    try:
        version = subprocess.run(
            [str(VENV_PYTHON), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError(
            f"the existing virtual-environment Python could not run ({exc.__class__.__name__}). "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap."
        ) from exc
    if version.returncode != 0:
        raise BootstrapError(
            f"the virtual-environment Python failed ({version.stderr.strip() or 'unknown error'}). "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap."
        )
    try:
        venv_major, venv_minor = (int(value) for value in version.stdout.split())
    except (TypeError, ValueError) as exc:
        raise BootstrapError(
            "the virtual-environment Python version could not be validated. "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap with Python 3.11+."
        ) from exc
    if (venv_major, venv_minor) < MIN_PYTHON:
        raise BootstrapError(
            f"the virtual environment uses Python {venv_major}.{venv_minor}; Python 3.11+ is required. "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap with Python 3.11+."
        )
    return str(VENV_PYTHON)


def _install_source_package(venv_python: str) -> None:
    _run(
        [
            venv_python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--editable",
            str(ROOT),
        ],
        label="Goal Native source installation",
        remediation="ensure the virtual environment has pip and rerun bootstrap; do not use a wheel-only install",
    )
    if not ENTRYPOINT.is_file() or not os.access(ENTRYPOINT, os.X_OK):
        raise BootstrapError(
            f"source installation did not produce {ENTRYPOINT}. "
            "Remediation: repair pip in .venv or remove only .venv yourself, then rerun bootstrap."
        )


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(
            "goal-native bootstrap: Python 3.11+ is required; "
            "rerun with a supported Python interpreter.",
            file=sys.stderr,
        )
        return 2

    try:
        git = _find_command("git", "install Git and ensure git is on PATH")
        node = _find_command("node", "install Node.js 22.19+ and ensure node is on PATH")
        npm = _find_command("npm", "install npm with Node.js 22.19+ and ensure npm is on PATH")
        node_version = _node_version(node)
        if node_version < MIN_NODE:
            raise BootstrapError(
                f"Node.js {node_version[0]}.{node_version[1]}.{node_version[2]} is too old; "
                "Node.js 22.19+ is required. Remediation: upgrade Node.js and rerun bootstrap."
            )
        _ensure_source_checkout(git)
        venv_python = _ensure_venv(sys.executable)
        _ensure_pi_submodule(git)
        _run(
            [npm, "run", "bootstrap:upstream"],
            label="pinned pi build/bootstrap",
            remediation="keep the checkout and pinned pi submodule intact, resolve the reported npm error, and rerun bootstrap",
        )
        _install_source_package(venv_python)
    except BootstrapError as exc:
        print(f"goal-native bootstrap: {exc}", file=sys.stderr)
        return 1

    print(f"Goal Native is ready. Launch with {ROOT / 'goal'}")
    print("No login or model call was performed; authenticate explicitly when you choose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
