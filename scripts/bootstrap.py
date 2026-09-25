#!/usr/bin/env python3
"""Bootstrap a supported Goal Native source checkout without touching user work."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


MIN_PYTHON = (3, 11)
MIN_NODE = (22, 19, 0)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
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
    if (ROOT / "upstream").is_symlink() or submodule.is_symlink() or (submodule.exists() and not submodule.is_dir()):
        raise BootstrapError(
            f"{submodule} is not a safe submodule directory; it was not changed. "
            f"Remediation: move that path aside yourself, then {remediation}."
        )
    if not (package.is_file() and git_marker.exists()):
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
    from goal_native.runtime import pi_source_status
    source = pi_source_status(ROOT, git)
    if not source["ok"]:
        raise BootstrapError(f"{source['error']}; the checkout was not changed. Remediation: {remediation}.")


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
            [str(VENV_PYTHON), "-c", "import json, sys; print(json.dumps({'version': list(sys.version_info[:2]), 'prefix': sys.prefix, 'base_prefix': sys.base_prefix}))"],
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
        identity = json.loads(version.stdout)
        venv_major, venv_minor = identity["version"]
        prefix, base_prefix = Path(identity["prefix"]).resolve(), Path(identity["base_prefix"]).resolve()
    except (KeyError, TypeError, ValueError) as exc:
        raise BootstrapError(
            "the virtual-environment Python version could not be validated. "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap with Python 3.11+."
        ) from exc
    if (venv_major, venv_minor) < MIN_PYTHON:
        raise BootstrapError(
            f"the virtual environment uses Python {venv_major}.{venv_minor}; Python 3.11+ is required. "
            f"Remediation: remove only {VENV} yourself and rerun bootstrap with Python 3.11+."
        )
    if prefix != VENV.resolve() or prefix == base_prefix:
        raise BootstrapError(
            "the selected Python does not belong to this .venv; no package was installed. "
            f"Remediation: move {VENV} aside yourself and rerun bootstrap."
        )
    return str(VENV_PYTHON)


def _ensure_pip(venv_python: str) -> None:
    """Provision pip offline only inside the validated checkout environment."""
    present = _run(
        [venv_python, "-I", "-c",
         "import importlib.util; print(int(importlib.util.find_spec('pip') is not None))"],
        label="virtual-environment pip detection",
        remediation="repair pip in the checkout virtual environment and rerun bootstrap",
        capture_output=True,
    )
    if present.stdout.strip() == "0":
        _run(
            [venv_python, "-I", "-m", "ensurepip", "--upgrade"],
            label="offline virtual-environment pip installation",
            remediation="use a Python 3.11+ installation with ensurepip, or install pip into this .venv",
        )
    elif present.stdout.strip() != "1":
        raise BootstrapError("virtual-environment pip detection returned an invalid result; no package was installed.")
    _run(
        [venv_python, "-I", "-m", "pip", "--version"],
        label="virtual-environment pip check",
        remediation="repair pip in the checkout virtual environment and rerun bootstrap",
        capture_output=True,
    )

def _install_command(bin_dir: Path) -> Path:
    bin_dir = bin_dir.expanduser()
    if not bin_dir.is_absolute():
        bin_dir = Path.cwd() / bin_dir
    if bin_dir.is_symlink():
        raise BootstrapError(f"{bin_dir} is a symlink; it was not changed.")
    if bin_dir.exists() and not bin_dir.is_dir():
        raise BootstrapError(f"{bin_dir} is not a directory; it was not changed.")
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BootstrapError(f"could not create command directory {bin_dir} ({exc}); it was not changed.") from exc

    command = bin_dir / "goal"
    wrapper = f'#!/bin/sh\nset -eu\nexec {shlex.quote(str((ROOT / "goal").resolve()))} "$@"\n'
    if command.is_symlink():
        raise BootstrapError(f"{command} is a symlink; it was not changed.")
    if command.exists():
        if not command.is_file():
            raise BootstrapError(f"{command} already exists and is not a regular file; it was not changed.")
        try:
            existing = command.read_text(encoding="utf-8")
            mode = command.stat().st_mode
        except (OSError, UnicodeError) as exc:
            raise BootstrapError(f"could not inspect existing {command} ({exc}); it was not changed.") from exc
        if existing != wrapper:
            raise BootstrapError(f"{command} already exists and is not a Goal Native managed command; it was not changed.")
        if not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            try:
                command.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError as exc:
                raise BootstrapError(f"managed command {command} is not executable ({exc}); it was not changed.") from exc
        return command

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".goal-", dir=bin_dir, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(wrapper)
            stream.flush()
            os.fchmod(stream.fileno(), 0o755)
        # Same-filesystem hard link is atomic and refuses an existing command;
        # rename/replace would silently overwrite a concurrent user install.
        os.link(temporary, command, follow_symlinks=False)
    except FileExistsError as exc:
        raise BootstrapError(f"{command} appeared during installation; it was not changed.") from exc
    except OSError as exc:
        raise BootstrapError(f"could not install command at {command} ({exc}); it was not changed.") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return command


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

def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print(
            "goal-native bootstrap: Python 3.11+ is required; "
            "rerun with a supported Python interpreter.",
            file=sys.stderr,
        )
        return 2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-command",
        action="store_true",
        help="install a persistent bare 'goal' command in the user bin directory",
    )
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=Path.home() / ".local" / "bin",
        metavar="DIR",
        help="directory for --install-command (default: ~/.local/bin)",
    )
    args = parser.parse_args(argv)

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
        _ensure_pip(venv_python)
        _run(
            [npm, "run", "bootstrap:upstream"],
            label="pinned pi build/bootstrap",
            remediation="keep the checkout and pinned pi submodule intact, resolve the reported npm error, and rerun bootstrap",
        )
        _install_source_package(venv_python)
        command = _install_command(args.bin_dir) if args.install_command else None
    except BootstrapError as exc:
        print(f"goal-native bootstrap: {exc}", file=sys.stderr)
        return 1

    print(f"Goal Native is ready. Launch with {ROOT / 'goal'}")
    if command is not None:
        print(f"Persistent bare command available at {command}")
        print(f"If {command.parent} is not already on PATH, add it with:")
        print(f'  export PATH={shlex.quote(str(command.parent))}:"$PATH"')
        print("No shell configuration files were changed.")
    print("No login or model call was performed; authenticate explicitly when you choose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
