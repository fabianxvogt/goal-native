"""Session-first terminal and JSON CLI for the canonical Goal Native controller.

The CLI is deliberately thin: Store remains the durable authority boundary and
Worker remains the only model execution loop.  The browser server is imported
only for the optional ``serve`` command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .context import ContextBudget
from .runtime import pi_source_status
from .sandbox import Sandbox, SandboxError, SandboxUnavailable
from .store import Store
from .terminal import Terminal, terminal_text
from .tui import PiTerminal, goal_indicator, interactive_terminal
from .worker import Worker
from .workspace import (
    blocked_component, copy_selected_directory, export_reviewed,
    review_workspace, snapshot_directory,
)


_MAX_TEXT = 250_000
_MAX_IMPORT_BYTES = 64 * 1024 * 1024
_SAFE_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CLICancelled(KeyboardInterrupt):
    """A CLI run was interrupted."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("CLI run cancelled")
        self.payload = payload


class _CLIArgumentError(Exception):
    """An argparse failure that belongs in the CLI's JSON error envelope."""


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive finite number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def _state_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _required_text(positional: str | None, option: str | None, field: str) -> str:
    if positional is not None and option is not None:
        raise ValueError(f"provide {field} either positionally or with --{field}, not both")
    value = positional if positional is not None else option
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    if len(value) > _MAX_TEXT:
        raise ValueError(f"{field} exceeds {_MAX_TEXT} characters")
    return value


def _read_text_file(path_value: str, *, field: str = "file") -> str:
    path = Path(path_value).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"{field} is unavailable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{field} must be a regular, non-symlink file")
    if info.st_size > _MAX_TEXT:
        raise ValueError(f"{field} exceeds {_MAX_TEXT} characters")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{field} must be readable UTF-8 text") from exc


def _json_or_text(value: str | None) -> Any:
    if value is None:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value




def _safe_stage_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("stage path must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("stage path must stay inside the sandbox")
    if any(blocked_component(part) for part in path.parts):
        raise ValueError("stage path names a blocked credential or controller path")
    return path.as_posix()


def _select_artifact(store: Store, goal_id: str, args: argparse.Namespace) -> dict[str, Any] | None:
    refs = [
        value
        for value in (
            getattr(args, "artifact", None),
            getattr(args, "artifact_id", None),
            getattr(args, "artifact_name", None),
        )
        if value is not None
    ]
    if len(refs) > 1:
        raise ValueError("choose only one artifact reference")
    if not refs:
        if getattr(args, "artifact_path", None) is not None:
            raise ValueError("--artifact-path requires an artifact")
        return None
    reference = refs[0]
    artifacts = store.artifacts(goal_id)
    if getattr(args, "artifact_name", None) is not None:
        matches = [item for item in artifacts if item.get("name") == reference]
    elif getattr(args, "artifact", None) is not None:
        matches = [
            item
            for item in artifacts
            if item.get("id") == reference or item.get("name") == reference
        ]
    else:
        matches = [item for item in artifacts if item.get("id") == reference]
    if len(matches) != 1:
        if not matches:
            raise KeyError(f"artifact not found for goal: {reference}")
        raise ValueError(f"artifact reference is ambiguous: {reference}")
    return matches[0]


def _stage_inputs(
    store: Store,
    goal_id: str,
    args: argparse.Namespace,
    sandbox: Sandbox,
    stage_root: Path,
) -> dict[str, Any]:
    source = getattr(args, "source_dir", None)
    artifact = _select_artifact(store, goal_id, args)
    if source is not None and artifact is not None:
        raise ValueError("choose --source-dir or an artifact input, not both")
    if getattr(args, "artifact_path", None) is not None and artifact is None:
        raise ValueError("--artifact-path requires an artifact")
    manifest: dict[str, Any] = {"source": None, "artifact": None}
    recovered = getattr(args, "recovered_stage", None)
    baseline = store.workspace_baseline(goal_id, recovered) if recovered else None
    if source is not None:
        manifest["source"] = copy_selected_directory(
            source, stage_root,
            tracked=baseline["files"] if baseline else (),
            excluded=baseline["selection"].get("exclusions", []) if baseline else (),
            strict=bool(recovered),
        )
    if artifact is not None:
        content = artifact.get("content")
        if not isinstance(content, str):
            raise ValueError("selected artifact content is not text")
        name = getattr(args, "artifact_path", None)
        if name is None:
            candidate = str(artifact.get("name") or "artifact.txt")
            name = candidate if "/" not in candidate and "\\" not in candidate else "artifact.txt"
        path = _safe_stage_path(name)
        destination = stage_root / Path(path)
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"artifact stage path already exists: {path}")
        sandbox.write({"path": path, "content": content})
        manifest["artifact"] = {
            "id": artifact["id"],
            "path": path,
            "bytes": len(content.encode("utf-8")),
        }
    if baseline is None:
        explicit = (manifest["artifact"]["path"],) if artifact is not None else ()
        files, selection = snapshot_directory(stage_root, tracked=explicit, strict=True)
        if manifest["source"] is not None:
            selection = manifest["source"]
        if source is not None and not recovered:
            selection["source_root"] = str(Path(source).expanduser().resolve())
        selection["origin"] = "recovered checkpoint" if recovered else "source" if source else "artifact" if artifact else "empty"
        baseline = {"files": files, "selection": selection}
    store.remember_workspace_baseline(goal_id, stage_root.name, baseline["files"], baseline["selection"])
    return manifest


def _auth_path(args: argparse.Namespace) -> Path:
    value = getattr(args, "auth_file", None)
    path = Path(value).expanduser().absolute() if value else Path.home() / ".config" / "goal-native" / "auth.json"
    resolved = path.resolve()
    if resolved.is_relative_to(_state_path(args.state)):
        raise ValueError("--auth-file must be outside the workspace state directory")
    source = getattr(args, "source_dir", None)
    if source and resolved.is_relative_to(Path(source).expanduser().resolve()):
        raise ValueError("--source-dir must not contain the subscription credential store")
    return path


def _auth_command(args: argparse.Namespace, command: str) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise ValueError("Node.js 22.19+ is required for pi subscription authentication")
    script = Path(__file__).resolve().parents[1] / "bridge" / "auth.mjs"
    arguments = [node, str(script), command, "--auth-file", str(_auth_path(args))]
    if command == "login":
        arguments += ["--method", args.method]
    result = subprocess.run(
        arguments, stdout=subprocess.PIPE, text=True,
        timeout=960 if command == "login" else 15, check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError("pi authentication command failed; run npm run bootstrap:upstream") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid pi authentication response")
    if payload.get("type") == "cancelled":
        raise CLICancelled({"status": "cancelled", "result": "login cancelled",
                            "run_started": False, "exit_code": 130})
    if result.returncode != 0 or "error" in payload:
        raise ValueError(payload.get("error", "pi authentication command failed"))
    return payload


def _credential(args: argparse.Namespace) -> tuple[str, str | None]:
    model = getattr(args, "model", None)
    if not isinstance(model, str) or not model.strip():
        raise ValueError("--model is required for real Worker execution")
    if args.provider == "openai-codex":
        status = _auth_command(args, "status")
        if not status.get("configured"):
            raise ValueError("Codex subscription is not configured; run python -m goal_native login")
        return model, None
    env_name = getattr(args, "api_key_env", "OPENAI_API_KEY")
    if not isinstance(env_name, str) or not _SAFE_ENV_NAME.fullmatch(env_name):
        raise ValueError("--api-key-env must be a valid environment variable name")
    value = os.environ.get(env_name)
    if not value:
        raise ValueError(f"credential environment variable {env_name} is not set")
    return model, value


def _goal_stage_name(goal_id: str) -> str:
    if goal_id not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", goal_id):
        return goal_id
    return hashlib.sha256(goal_id.encode("utf-8")).hexdigest()[:32]


def _stage_parent(state_root: Path, goal_id: str, *, create: bool = False) -> Path:
    stages = state_root / "stages"
    parent = stages / _goal_stage_name(goal_id)
    for directory in (stages, parent):
        if create:
            directory.mkdir(exist_ok=True, mode=0o700)
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise ValueError("stage directories must be real directories, not symlinks")
    return parent


def _local_stage_path(store: Store, goal_id: str) -> Path | None:
    name = store.local_stage(goal_id)
    if name is None:
        return None
    try:
        if not re.fullmatch(r"run-[A-Za-z0-9_-]{1,96}", name):
            raise ValueError("invalid local stage name")
        stage = _stage_parent(store.root, goal_id) / name
        if not stat.S_ISDIR(stage.lstat().st_mode):
            raise ValueError("local stage is not a real directory")
        return stage
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Saved session files are missing or unsafe. Reopen with --source-dir or "
            "--artifact-id to select files, or --fresh to continue with empty files "
            "and saved artifacts. No files were restored."
        ) from exc


def _fence_interrupted_run(store: Store, goal_id: str) -> None:
    detail = store.goal(goal_id)
    for invocation in detail.get("invocations", []):
        if invocation.get("status") == "running":
            store.finish(invocation["id"], "cancelled", invocation.get("result", ""))
    for attempt in detail.get("runs", []):
        if attempt.get("status") == "running":
            invocation_id = attempt.get("invocation_id")
            invocation = next(
                (item for item in detail.get("invocations", []) if item.get("id") == invocation_id),
                None,
            )
            store.finish_run(
                attempt["id"],
                "cancelled",
                stop_reason="ctrl_c",
                diagnostic={"kind": "ctrl_c", "message": "CLI interrupted by Ctrl-C"},
                assistant_text=invocation.get("result", "") if invocation else "",
                admission=attempt.get("admission"),
                invocation_id=invocation_id,
            )
    current = store.goal(goal_id)
    if current.get("status") not in {"cancelled", "paused"}:
        store.pause_for_interruption(goal_id, "CLI interrupted by Ctrl-C")


def _run_existing(
    state: str, goal_id: str, args: argparse.Namespace, *,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    state_root: Path | None = None
    stage_root: Path | None = None
    worker: Worker | None = None
    started = False
    command_runtime: Any | None = None
    stage_manifest: dict[str, Any] = {"source": None, "artifact": None}
    try:
        context_budget = ContextBudget(max_context_tokens=args.context_budget)
        execution = getattr(args, "execution", "python")
        if getattr(args, "allow_network", False) and execution != "docker":
            raise ValueError("--allow-network requires --execution docker")
        run_args = argparse.Namespace(**vars(args))
        if hasattr(run_args, "recovered_stage"):
            delattr(run_args, "recovered_stage")
        selected = any(getattr(args, key, None) is not None for key in (
            "source_dir", "artifact", "artifact_id", "artifact_name",
        ))
        if args.fresh and selected:
            raise ValueError("choose --fresh or an explicit source/artifact, not both")
        model, api_key = _credential(args)
        state_root = _state_path(state)
        state_root.mkdir(parents=True, exist_ok=True)
        # Validate the durable identity before allocating a per-run capability.
        with Store(state_root) as store:
            store.goal(goal_id)
            if not selected and not args.fresh:
                source = _local_stage_path(store, goal_id)
                if source is not None:
                    run_args.source_dir = str(source)
                    run_args.recovered_stage = source.name
        # Never reuse a writable root: replacement assignments get independent
        # capability directories, so a late old worker cannot corrupt a new run.
        stage_parent = _stage_parent(state_root, goal_id, create=True)
        stage_root = Path(tempfile.mkdtemp(prefix="run-", dir=stage_parent))
        os.chmod(stage_root, 0o700)
        with Store(state_root) as store:
            store.goal(goal_id)
            sandbox = Sandbox(stage_root, timeout=args.max_time, require_os_sandbox=execution == "python")
            try:
                stage_manifest = _stage_inputs(store, goal_id, run_args, sandbox, stage_root)
                if execution == "docker":
                    from .container_runtime import ContainerRuntime

                    command_runtime = ContainerRuntime(
                        stage_root, image=args.container_image,
                        allow_network=args.allow_network, timeout=args.max_time,
                    )
                worker = Worker(
                    store,
                    model,
                    api_key=api_key,
                    max_rounds=args.max_rounds,
                    sandbox=sandbox,
                    context_budget=context_budget,
                    max_total_seconds=args.max_time,
                    provider=args.provider,
                    auth_file=_auth_path(args) if args.provider == "openai-codex" else None,
                    on_event=on_event,
                    command_runtime=command_runtime,
                )
                started = True
                try:
                    result = worker.run(goal_id)
                    result = dict(result)
                    result["stage_dir"] = str(stage_root)
                    result["stage_inputs"] = stage_manifest
                    result["run_started"] = True
                    if result.get("invocation_id") is not None:
                        store.receipt(
                            result["invocation_id"], "controller.cli.run",
                            {
                                "model": model, "provider": args.provider,
                                "context_budget": args.context_budget,
                                "max_rounds": args.max_rounds, "max_time": args.max_time,
                                "execution": execution,
                                "network_allowed": bool(args.allow_network),
                            },
                            result,
                            note="CLI termination report, not goal acceptance; stage paths are local and not exported file contents.",
                        )
                    return result
                except KeyboardInterrupt as exc:
                    worker.cancel()
                    _fence_interrupted_run(store, goal_id)
                    current = store.goal(goal_id)
                    attempt = current.get("runs", [])[-1] if current.get("runs") else {}
                    raise CLICancelled(
                        {
                            "status": "cancelled",
                            "goal_id": goal_id,
                            "run_id": attempt.get("id"),
                            "stop_reason": attempt.get("stop_reason") or "ctrl_c",
                            "diagnostic": attempt.get("diagnostic") or {
                                "kind": "ctrl_c", "message": "CLI interrupted by Ctrl-C",
                            },
                            "admission": attempt.get("admission"),
                            "stage_dir": str(stage_root),
                            "stage_inputs": stage_manifest,
                            "run_started": True,
                            "result": attempt.get("assistant_text", ""),
                            "exit_code": 130,
                        }
                    ) from exc
            finally:
                try:
                    if command_runtime is not None:
                        command_runtime.close()
                finally:
                    sandbox.close()
                    if worker is not None and worker.last_assignment_id is not None:
                        store.remember_local_stage(worker.last_assignment_id, stage_root.name)
    except CLICancelled:
        raise
    except KeyboardInterrupt as exc:
        if worker is not None:
            worker.cancel()
        if started and state_root is not None:
            with Store(state_root) as store:
                _fence_interrupted_run(store, goal_id)
        payload: dict[str, Any] = {
            "status": "cancelled",
            "goal_id": goal_id,
            "stage_inputs": stage_manifest,
            "run_started": started,
            "result": "CLI interrupted by Ctrl-C"
            if started
            else "CLI interrupted during setup; no run started",
            "exit_code": 130,
        }
        if stage_root is not None:
            payload["stage_dir"] = str(stage_root)
        raise CLICancelled(payload) from exc
    # Keep this unique run snapshot for recovery and inspection. It is outside
    # the SQLite export and never becomes a shared writable capability root.


def _human_artifact(state: str, args: argparse.Namespace) -> dict[str, Any]:
    if args.text is not None and args.file is not None:
        raise ValueError("provide human artifact content with --text or --file, not both")
    if args.text is None and args.file is None:
        raise ValueError("human artifact content requires --text or --file")
    content = args.text if args.text is not None else _read_text_file(args.file)
    if len(content) > _MAX_TEXT:
        raise ValueError(f"artifact content exceeds {_MAX_TEXT} characters")
    with Store(_state_path(state)) as store:
        assignment = store.assign(args.goal_id)
        invocation = store.invoke(
            args.goal_id,
            assignment["id"],
            {"producer": "human", "mode": "manual-artifact"},
        )
        artifact = store.artifact(
            args.goal_id,
            invocation["id"],
            args.kind,
            content,
            name=args.name,
            limitations=args.limitations,
            trust="human",
        )
        store.finish(invocation["id"], "finished", "Human-authored local artifact")
        return artifact


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[1]
    checks: dict[str, Any] = {}

    python_ok = sys.version_info >= (3, 11)
    checks["python"] = {
        "ok": python_ok,
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        "minimum": "3.11",
        **({"remediation": "use Python 3.11+ to launch Goal Native"} if not python_ok else {}),
    }

    node_path = shutil.which("node")
    node_version = None
    node_ok = False
    node_error = None
    if node_path:
        try:
            result = subprocess.run(
                [node_path, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            node_version = result.stdout.strip() or result.stderr.strip()
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", node_version or "")
            node_ok = bool(
                match
                and (int(match.group(1)), int(match.group(2)), int(match.group(3))) >= (22, 19, 0)
                and result.returncode == 0
            )
            if result.returncode != 0:
                node_error = "version command failed"
        except (OSError, subprocess.SubprocessError) as exc:
            node_error = exc.__class__.__name__
    checks["node"] = {
        "ok": node_ok,
        "found": bool(node_path),
        "version": node_version,
        "minimum": "22.19.0",
        **({"error": node_error} if node_error else {}),
        **({"remediation": "install Node.js 22.19+ and ensure node is on PATH"} if not node_ok else {}),
    }

    git_path = shutil.which("git")
    checks["git"] = {
        "ok": bool(git_path),
        "found": bool(git_path),
        **({"remediation": "install Git and ensure git is on PATH"} if not git_path else {}),
    }

    upstream = project_root / "upstream" / "pi"
    source_status = pi_source_status(project_root, git_path)
    package_names = ("chord", "telemetry", "tui", "agent", "ai", "coding-agent")
    dist_files = [
        upstream / "packages" / "chord" / "dist" / "index.js",
        upstream / "packages" / "telemetry" / "dist" / "index.js",
        upstream / "packages" / "tui" / "dist" / "index.js",
        upstream / "packages" / "agent" / "dist" / "index.js",
        upstream / "packages" / "ai" / "dist" / "index.js",
        upstream / "packages" / "coding-agent" / "dist" / "core" / "auth-storage.js",
        upstream / "packages" / "coding-agent" / "dist" / "core" / "sdk.js",
    ]
    versions: dict[str, str | None] = {}
    for name in package_names:
        package_path = upstream / "packages" / name / "package.json"
        try:
            if not source_status["initialized"]:
                versions[name] = None
                continue
            versions[name] = json.loads(package_path.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            versions[name] = None
    expected_versions = {name: "0.87.1" for name in package_names}
    pi_cloned = source_status["initialized"]
    pi_built = pi_cloned and all(path.is_file() for path in dist_files)
    bridge_ok = (project_root / "bridge" / "agent.mjs").is_file()
    pi_importable = False
    if source_status["ok"] and node_ok and pi_built and bridge_ok:
        try:
            probe = subprocess.run(
                [node_path, "--input-type=module", "-e",
                 "await import('./bridge/agent.mjs'); await import('./upstream/pi/packages/coding-agent/dist/core/sdk.js')"],
                cwd=project_root, capture_output=True, text=True, timeout=10, check=False,
            )
            pi_importable = probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    pi_ok = source_status["ok"] and pi_importable and versions == expected_versions
    checks["pi"] = {
        "ok": pi_ok,
        "cloned": pi_cloned,
        "source": source_status,
        "built": pi_built,
        "importable": pi_importable,
        "versions": versions,
        "expected_version": "0.87.1",
        "bridge": bridge_ok,
        **(
            {
                "remediation": (
                    "run ./scripts/bootstrap.py from the source checkout; it initializes and builds "
                    "the pinned pi 0.87.1 submodule without logging in"
                )
            }
            if not pi_ok
            else {}
        ),
    }

    sandbox_ok = False
    sandbox_error = None
    execution = getattr(args, "execution", "python")
    with tempfile.TemporaryDirectory(prefix="goal-native-doctor-") as temporary:
        try:
            sandbox = Sandbox(Path(temporary), require_os_sandbox=execution == "python")
            sandbox.close()
            if execution == "docker":
                from .container_runtime import ContainerRuntime

                runtime = ContainerRuntime(
                    sandbox.root, image=args.container_image,
                    allow_network=args.allow_network,
                )
                runtime.close()
            elif getattr(args, "allow_network", False):
                raise ValueError("--allow-network requires --execution docker")
            sandbox_ok = True
        except (OSError, SandboxError, SandboxUnavailable, ValueError, RuntimeError) as exc:
            sandbox_error = str(exc)
    checks["sandbox"] = {
        "ok": sandbox_ok,
        "execution": execution,
        "macos_enforcement": sandbox_ok and execution == "python",
        "network_allowed": bool(getattr(args, "allow_network", False)) and execution == "docker",
        "platform": sys.platform,
        **({"error": sandbox_error} if sandbox_error else {}),
        **(
            {"remediation": (
                "start a local Linux Docker engine and build the runtime image: "
                "docker build -t goal-native-runtime:local runtime"
                if execution == "docker"
                else "run on macOS with the staged execution support available"
            )}
            if not sandbox_ok
            else {}
        ),
    }

    runtime_names = ("python", "node", "git", "pi", "sandbox")
    runtime_ok = all(bool(checks[name].get("ok")) for name in runtime_names)
    runtime_remediation = [
        str(checks[name]["remediation"])
        for name in runtime_names
        if checks[name].get("remediation")
    ]
    checks["runtime"] = {
        "ok": runtime_ok,
        "prerequisites": {name: checks[name] for name in runtime_names},
        "remediation": runtime_remediation,
    }

    model = getattr(args, "model", None)
    model_selected = isinstance(model, str) and bool(model.strip())
    provider = getattr(args, "provider", "openai-codex")
    if provider == "openai-codex":
        if not runtime_ok:
            credentials = {
                "ok": False,
                "provider": provider,
                "status": "blocked_by_runtime",
                "configured": None,
                "credential_type": None,
                "expired": None,
                "model_selected": model_selected,
                "remediation": ["fix runtime prerequisites, then rerun doctor; no login was attempted"],
            }
        else:
            try:
                status = _auth_command(args, "status")
                configured = bool(status.get("configured"))
                expired = status.get("expired") if isinstance(status.get("expired"), bool) else None
                usable = configured  # Pi refreshes expired access tokens on an explicit run.
                credential_remediation: list[str] = []
                if not configured:
                    credential_remediation.append("run python -m goal_native login explicitly")
                if not model_selected:
                    credential_remediation.append("rerun doctor with an explicit --model value")
                credentials = {
                    "ok": bool(usable and model_selected),
                    "provider": provider,
                    "status": "ready" if usable and model_selected else "missing",
                    "configured": configured,
                    "credential_type": status.get("credential_type"),
                    "expired": expired,
                    "refresh_on_run": bool(configured and expired),
                    "model_selected": model_selected,
                    "remediation": credential_remediation,
                }
            except (ValueError, OSError, subprocess.SubprocessError):
                credentials = {
                    "ok": False,
                    "provider": provider,
                    "status": "unavailable",
                    "configured": None,
                    "credential_type": None,
                    "expired": None,
                    "model_selected": model_selected,
                    "remediation": [
                        "run ./scripts/bootstrap.py from the source checkout, then rerun doctor",
                        "authentication status was not read and no login was attempted",
                    ],
                }
    else:
        env_name = getattr(args, "api_key_env", "OPENAI_API_KEY")
        env_valid = isinstance(env_name, str) and bool(_SAFE_ENV_NAME.fullmatch(env_name))
        credential_present = bool(env_valid and os.environ.get(env_name))
        credential_remediation = []
        if not credential_present:
            credential_remediation.append(f"set the {env_name} environment variable before running")
        if not model_selected:
            credential_remediation.append("rerun doctor with an explicit --model value")
        credentials = {
            "ok": bool(model_selected and credential_present),
            "provider": provider,
            "status": "ready" if model_selected and credential_present else "missing",
            "model_selected": model_selected,
            "api_key_env": env_name,
            "credential_present": credential_present,
            "base_url_present": bool(os.environ.get("OPENAI_BASE_URL")),
            "remediation": credential_remediation,
        }
    checks["credentials"] = credentials
    return {"ok": bool(runtime_ok and credentials["ok"]), "checks": checks}


def _write_export(path_value: str, data: dict[str, Any]) -> str:
    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as output:
            temporary_fd = -1
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary_fd != -1:
            try:
                os.close(temporary_fd)
            except OSError:
                pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return str(path)

def _read_import_json(source: Path | None) -> Any:
    if source is None:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        try:
            raw = stream.read(_MAX_IMPORT_BYTES + 1)
        except (OSError, UnicodeError) as exc:
            raise ValueError("import input could not be read") from exc
    else:
        try:
            info = source.lstat()
        except OSError as exc:
            raise ValueError(f"import file is unavailable: {source}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError(f"import file must be a regular, non-symlink file: {source}")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_descriptor = -1
        try:
            file_descriptor = os.open(source, flags)
            opened = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError(f"import file must be a regular, non-symlink file: {source}")
            with os.fdopen(file_descriptor, "rb") as stream:
                file_descriptor = -1
                raw = stream.read(_MAX_IMPORT_BYTES + 1)
        except ValueError:
            raise
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"import file is not valid JSON: {source}") from exc
        finally:
            if file_descriptor != -1:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("import input could not be read")
    if len(raw) > _MAX_IMPORT_BYTES:
        raise ValueError(f"import input exceeds {_MAX_IMPORT_BYTES} bytes")
    try:
        text = bytes(raw).decode("utf-8")
        return json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as exc:
        location = "stdin" if source is None else str(source)
        raise ValueError(f"import file is not valid JSON: {location}") from exc


def _goal_summary(goal: dict[str, Any]) -> dict[str, Any]:
    """Project recorded work without treating completion as acceptance."""
    invocations = [
        {
            **{key: invocation[key] for key in (
                "id", "assignment_id", "status", "result", "created_at",
                "finished_at", "revision", "input_version", "authority_version",
                "evidence",
            )},
            "usage": invocation["usage"]["normalized"] if invocation["usage"] is not None else None,
            "matches_current_contract": all(
                invocation[key] == goal[key]
                for key in ("revision", "input_version", "authority_version")
            ),
            "tools": [
                receipt for receipt in invocation["receipts"]
                if receipt["tool"].startswith("tool.")
            ],
        }
        for invocation in goal["invocations"]
    ]
    run_attempts = goal.get("runs", [])
    historical_receipts = [
        receipt for invocation in goal["invocations"]
        for receipt in invocation["receipts"]
        if receipt["tool"] == "controller.cli.run"
    ]
    recorded_runs = [
        *historical_receipts,
        *[
            {"tool": "controller.run", "run_id": attempt["id"], "result": attempt}
            for attempt in run_attempts
            if attempt.get("invocation_id") is None
        ],
    ]
    return {
        **{key: goal[key] for key in (
            "id", "outcome", "revision", "input_version", "authority_version",
            "effects_allowed", "authority_mode",
        )},
        "goal_status": goal["status"],
        "latest_invocation_status": invocations[-1]["status"] if invocations else None,
        "invocations": invocations,
        "run_attempts": run_attempts,
        "latest_run": run_attempts[-1] if run_attempts else None,
        "recorded_runs": recorded_runs,
        "artifacts": [
            {key: value for key, value in artifact.items() if key != "content"}
            for artifact in goal["artifacts"]
            if artifact["kind"] != "context"
        ],
        "acceptances": goal["acceptances"],
        "effects": goal["effects"],
    }


def _code_command(state: str, goal_id: str, command: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    with Store(_state_path(state)) as store:
        stage = _local_stage_path(store, goal_id)
        if stage is None:
            raise ValueError("No local session files are recorded. Run with selected files before reviewing changes.")
        if command == "export-code":
            if args is None:
                raise ValueError("code export requires an output path and review ID")
            return export_reviewed(store, goal_id, stage, args.review, Path(args.output), args.format)
        report, files = review_workspace(store, goal_id, stage)
    if command == "files":
        return {
            "source_selection": report["selection"],
            "candidate_files": [{"path": path, **{key: item[key] for key in ("sha256", "bytes", "mode")}}
                                for path, item in sorted(files.items())],
            "candidate_exclusions": report["candidate_exclusions"],
        }
    if command == "changes":
        return {key: value for key, value in report.items() if key not in {"review_id", "patch"}}
    return report


def _print_code_review(command: str, report: dict[str, Any]) -> None:
    terminal = Terminal(sys.stdout)
    terminal.write()
    terminal.write({"files": "Selected files", "changes": "Staged changes", "diff": "Review"}[command], color="cyan")
    if command == "files":
        for item in report["candidate_files"]:
            terminal.write(f"  {item['path']}  ({item['bytes']} bytes)")
        exclusions = {item["path"]: item["reason"] for item in (
            report["source_selection"].get("exclusions", []) + report["candidate_exclusions"]
        )}
        for path, reason in sorted(exclusions.items()):
            terminal.write(f"  excluded: {path} — {reason}")
        terminal.write("Only selected files participate in code export.")
    else:
        for change in report["changes"]:
            suffix = " (binary; files archive only)" if change["binary"] else ""
            terminal.write(f"  {change['status']:8} {change['path']}{suffix}")
        if not report["changes"]:
            terminal.write("No staged changes.")
        terminal.write(f"Original source: {report['source_status']['state']}")
        for path in report["source_status"]["changed_paths"]:
            terminal.write(f"  source diverged: {path}")
        if command == "diff":
            # Patch content stays unstyled and is not reformatted as prose.
            print(terminal_text(report["patch"]))
            terminal.write(f"Review: {report['review_id']}")
            terminal.write("Use /export PATH [--format files]. Changed bytes or source state require a new /diff.")
        terminal.write(report["note"])
    terminal.write()


def _dispatch(args: argparse.Namespace) -> tuple[Any, int]:
    command = args.command
    state = args.state
    if command in {"login", "logout", "auth-status", "models"}:
        result = _auth_command(args, "status" if command == "auth-status" else command)
        return result, 1 if command == "auth-status" and not result.get("configured") else 0
    if command == "create":
        outcome = _required_text(args.outcome, args.outcome_option, "outcome")
        with Store(_state_path(state)) as store:
            return store.create_goal(outcome, args.parent_id, args.kind, args.criteria, args.constraints), 0
    if command == "list":
        with Store(_state_path(state)) as store:
            return {"goals": store.list_goals()}, 0
    if command == "show":
        with Store(_state_path(state)) as store:
            goal = store.goal(args.goal_id)
            return _goal_summary(goal) if args.summary else goal, 0
    if command == "source-preview":
        _, selection = snapshot_directory(Path(args.path))
        return selection, 0
    if command in {"files", "changes", "diff", "export-code"}:
        return _code_command(state, args.goal_id, command, args), 0
    if command == "request":
        text = _required_text(args.text, args.text_option, "text")
        with Store(_state_path(state)) as store:
            return store.request(args.goal_id, text, args.control), 0
    if command == "revise":
        with Store(_state_path(state)) as store:
            return store.revise(
                args.goal_id,
                args.expected_revision,
                args.outcome,
                args.criteria,
                args.constraints,
            ), 0
    if command in {"run", "resume"}:
        result = _run_existing(state, args.goal_id, args)
        return result, 0 if result.get("status") == "finished" else 1
    if command == "ask":
        outcome = _required_text(args.question, None, "question")
        with Store(_state_path(state)) as store:
            goal = store.create_goal(outcome, kind=args.kind, criteria=args.criteria, constraints=args.constraints)
        result = _run_existing(state, goal["id"], args)
        return {"goal_id": goal["id"], "goal": goal, "run": result, "status": result.get("status")}, (
            0 if result.get("status") == "finished" else 1
        )
    if command == "artifacts":
        if args.artifact_command == "add":
            return _human_artifact(state, args), 0
        if args.artifact_command == "list":
            with Store(_state_path(state)) as store:
                return {"artifacts": store.artifacts(args.goal_id)}, 0
        raise ValueError("artifacts requires list or add")
    if command == "assess":
        with Store(_state_path(state)) as store:
            artifact = next((item for item in store.artifacts(args.goal_id) if item["id"] == args.artifact_id), None)
            if artifact is None:
                raise KeyError(f"unknown artifact for goal: {args.artifact_id}")
            return store.verify(
                args.invocation_id or artifact["invocation_id"],
                args.artifact_id,
                args.check,
                args.passed,
                _json_or_text(args.details),
                trusted=True,
            ), 0
    if command == "accept":
        with Store(_state_path(state)) as store:
            return store.accept(args.goal_id, args.artifact_id, args.evidence_id, args.expected_revision), 0
    if command == "prepare":
        with Store(_state_path(state)) as store:
            return store.prepare_effect(
                args.invocation_id,
                args.artifact_id,
                args.target,
                args.expected_version,
                args.evidence_id,
            ), 0
    if command == "approve":
        with Store(_state_path(state)) as store:
            return store.approve(args.effect_id), 0
    if command == "commit":
        with Store(_state_path(state)) as store:
            return store.commit(args.effect_id, args.lose_response), 0
    if command == "reconcile":
        with Store(_state_path(state)) as store:
            return store.reconcile(args.effect_id), 0
    if command == "export":
        with Store(_state_path(state)) as store:
            data = store.export()
        if args.output:
            output_path = _write_export(args.output, data)
            data = dict(data)
            data["output_path"] = output_path
        return data, 0
    if command == "import":
        source = None if args.file == "-" else Path(args.file).expanduser()
        data = _read_import_json(source)
        imported = Store.import_data(_state_path(state), data)
        try:
            return {"imported": True, "goals": imported.list_goals()}, 0
        finally:
            imported.close()
    if command == "doctor":
        result = _doctor(args)
        return result, 0 if result["ok"] else 1
    if command == "serve":
        from .server import serve

        if not 0 <= args.port <= 65_535:
            raise ValueError("--port must be between 0 and 65535")
        serve(args.state, args.port, args.model)
        return {"served": True}, 0
    raise ValueError(f"unknown command: {command}")




class _RunDisplay:
    """Render public assistant text and tool activity, never reasoning or traces."""

    def __init__(self) -> None:
        self.terminal = Terminal(sys.stdout)
        self.stream = self.terminal.stream
        self.line_open = False
        self.seen: dict[int, int] = {}
        self.text_index: int | None = None
        self.last_reply: str | None = None
        self._status("Working…")

    def _end_text(self) -> None:
        if self.line_open:
            if isinstance(self.stream, PiTerminal):
                self.stream.send("assistant_end")
            else:
                self.stream.write("\n\n")
            self.line_open = False

    def _status(self, text: str, color: str = "dim") -> None:
        self._end_text()
        if isinstance(self.stream, PiTerminal):
            self.stream.send("activity", text=text, color=color)
        else:
            self.terminal.write(text, color=color)

    def _append(self, index: int, text: str) -> None:
        if not text:
            return
        if not self.line_open and not isinstance(self.stream, PiTerminal):
            self.terminal.write("Assistant", color="cyan")
        if self.text_index is not None and self.text_index != index:
            if isinstance(self.stream, PiTerminal):
                self.stream.send("assistant", text="\n")
            else:
                self.stream.write("\n")
        self.text_index = index
        self.seen[index] = self.seen.get(index, 0) + len(text)
        if isinstance(self.stream, PiTerminal):
            self.stream.send("assistant", text=terminal_text(text))
        else:
            self.stream.write(terminal_text(text))
        self.stream.flush()
        self.line_open = True

    def event(self, _invocation_id: str, event: dict[str, Any]) -> None:
        kind = event.get("type")
        message = event.get("message", {})
        if kind == "message_start" and message.get("role") == "assistant":
            self.seen.clear()
            self.text_index = None
        elif kind == "message_update":
            delta = event.get("assistantMessageEvent", {})
            if delta.get("type") == "text_delta":
                self._append(delta["contentIndex"], delta["delta"])
        elif kind == "message_end" and message.get("role") == "assistant":
            texts = []
            for index, part in enumerate(message.get("content", [])):
                if part.get("type") == "text":
                    text = part["text"]
                    self._append(index, text[self.seen.get(index, 0):])
                    texts.append(text)
            self.last_reply = "\n".join(texts)
            self._end_text()
        elif kind == "tool_execution_start":
            label = {
                "staged_read": "Reading files…", "staged_write": "Writing files…",
                "staged_search": "Searching files…", "staged_run": "Running Python…",
                "staged_files": "Listing files…", "staged_regex": "Searching code…",
                "staged_edit": "Editing code…", "staged_command": "Running isolated command…",
                "staged_language": "Inspecting symbols…",
                "read_artifact": "Reading saved work…", "save_artifact": "Saving work…",
                "read_receipt": "Reading previous observations…",
                "finding": "Saving a finding…",
            }.get(event.get("toolName"), "Using a tool…")
            self._status("Tool: " + label)
        elif kind == "tool_execution_end":
            failed = bool(event.get("isError"))
            self._status("Tool failed; continuing…" if failed else "Tool complete", "yellow" if failed else "dim")

    def finish(self, result: dict[str, Any] | None = None) -> None:
        if result is not None:
            reply = result.get("result") or ""
            if reply != self.last_reply:
                if self.seen:
                    consumed = sum(self.seen.values()) + max(0, len(self.seen) - 1)
                    self._append(self.text_index or 0, reply[consumed:])
                elif reply:
                    self._append(0, reply)
        self._end_text()


def _chat_sessions(state: str) -> list[dict[str, Any]]:
    with Store(_state_path(state)) as store:
        return store.session_summaries()


def _chat_status(state: str, goal_id: str, args: argparse.Namespace | None = None) -> None:
    terminal = Terminal(sys.stdout)
    with Store(_state_path(state)) as store:
        summary = _goal_summary(store.goal(goal_id))
        stage = _local_stage_path(store, goal_id)
    terminal.write("\nSession status", color="cyan")
    terminal.write(terminal_text(f"Session  {summary['outcome']}\nID       {goal_id}"))
    run = summary.get("latest_run")
    if run is None:
        runs = summary["recorded_runs"]
        run = runs[-1]["result"] if runs else None
    run_status = run.get("status", "not recorded") if isinstance(run, dict) else "not recorded"
    reason = run.get("stop_reason") if isinstance(run, dict) else None
    suffix = f" · {reason}" if reason else ""
    terminal.write(terminal_text(f"Work     last outcome: {run_status}{suffix} · {len(summary['artifacts'])} saved artifacts"))
    diagnostic = run.get("diagnostic") if isinstance(run, dict) else None
    if isinstance(diagnostic, dict) and diagnostic.get("message"):
        terminal.write(terminal_text(f"Detail   {diagnostic['message']}"))
    admission = run.get("admission") if isinstance(run, dict) else None
    if isinstance(admission, dict):
        total = admission.get("total_tokens")
        ceiling = admission.get("max_context_tokens")
        headroom = ceiling - total if isinstance(ceiling, int) and isinstance(total, int) else "unknown"
        terminal.write(terminal_text(f"Budget   estimated {total} / {ceiling} tokens · headroom {headroom} · not billed usage"))
    elif args is not None:
        terminal.write(terminal_text(f"Budget   ceiling {args.context_budget} tokens · no admission recorded"))
    limits = run.get("limits") if isinstance(run, dict) else None
    rounds = limits.get("max_rounds") if isinstance(limits, dict) else (args.max_rounds if args else "unknown")
    seconds = limits.get("max_time") if isinstance(limits, dict) else (args.max_time if args else "unknown")
    used_rounds = run.get("rounds") if isinstance(run, dict) else None
    terminal.write(terminal_text(f"Limits   rounds {used_rounds if used_rounds is not None else 'unknown'} / {rounds} · time {seconds}s"))
    if isinstance(limits, dict) and limits.get("execution"):
        terminal.write(terminal_text(
            f"Runtime  {limits['execution']} · network "
            f"{'explicitly permitted' if limits.get('network_allowed') else 'denied'}"
        ))
    terminal.write(terminal_text(f"Goal     {summary['goal_status']} · run completion is not acceptance"))
    if stage:
        terminal.write(terminal_text(f"Files    {stage}"))
    terminal.write()


def _chat(args: argparse.Namespace, ui: PiTerminal | None = None) -> int:
    if args.model is None and args.provider == "openai-codex":
        args.model = "gpt-6-luna"
    if not args.model:
        raise ValueError("--provider openai requires an explicit --model")
    ContextBudget(max_context_tokens=args.context_budget)
    if args.allow_network and args.execution != "docker":
        raise ValueError("--allow-network requires --execution docker")
    # Readline must not reset terminal modes owned by the Pi editor.
    if ui is None:
        try:
            import readline
        except ImportError:
            pass

    terminal = Terminal(sys.stdout)
    if ui:
        ui.send("header", model=args.model, runtime=args.execution,
                budget=args.context_budget, source=args.source_dir)
    else:
        terminal.write("\nGoal Native", color="cyan")
        terminal.write(f"{args.model} / {args.provider}")
        terminal.write(f"Runtime: {args.execution}")
        if args.execution == "docker":
            terminal.write(f"{args.container_image} / network "
                           f"{'permitted when requested' if args.allow_network else 'denied'}")
        terminal.write(f"Context ceiling: {args.context_budget:,} tokens")
        if args.source_dir:
            terminal.field("Source selected (not sent)", str(args.source_dir))
        terminal.write("/help for commands / Ctrl-C cancels\n", color="dim")
    goal_id: str | None = None
    seeded_goals: set[str] = set()
    listed_ids: list[str] = []
    reviewed: dict[str, str] = {}
    last_status = 0
    def refresh_goals(running: str | None = None) -> None:
        nonlocal listed_ids
        if ui is None:
            return
        sessions = _chat_sessions(args.state)
        listed_ids = [goal["id"] for goal in sessions]
        rows = []
        for number, goal in enumerate(sessions, 1):
            label, color = goal_indicator(goal, goal["id"] == running)
            rows.append({"id": goal["id"], "number": number,
                         "title": terminal_text(goal["outcome"]),
                         "status": goal["status"], "label": label, "color": color})
        ui.send("goals", goals=rows, selected=goal_id, budget=args.context_budget)

    def execute(run_goal_id: str, run_args: argparse.Namespace) -> dict[str, Any]:
        nonlocal last_status
        refresh_goals(run_goal_id)
        terminal.write()
        display = _RunDisplay()
        if ui:
            ui.arm_run()

        def finish_display(result: dict[str, Any] | None = None) -> None:
            try:
                display.finish(result)
            except KeyboardInterrupt:
                if ui is None or not ui.renderer_dead:
                    raise
                ui.abandon_run()
        try:
            try:
                try:
                    result = _run_existing(args.state, run_goal_id, run_args, on_event=display.event)
                except CLICancelled as exc:
                    result = exc.payload
                    if ui is not None and ui.renderer_dead:
                        ui.abandon_run()
                finish_display(result)
            finally:
                finish_display()
        finally:
            if ui:
                ui.disarm_run()
        if result.get("run_started"):
            seeded_goals.add(run_goal_id)
        status = result.get("status", "failed")
        last_status = 0 if status == "finished" else 130 if status == "cancelled" else 1
        if status != "finished":
            terminal.field("Run stopped", str(status), color="yellow")
            terminal.write("Request and completed work are saved.")
            terminal.write("/continue retries the request unchanged.\n/status shows details and limits.\nUse /budget N or /continue --max-rounds N\nto explicitly adjust limits.\n")
        else:
            terminal.write("Run finished (not acceptance).", color="green")
        return result
    while True:
        try:
            refresh_goals()
            text = ui.read() if ui else input(terminal.prompt("you › "))
            # Pi owns multiline editing; the plain interface retains backslash continuation.
            while ui is None and text.endswith("\\"):
                text = text[:-1] + "\n" + input(terminal.prompt("…   "))
        except EOFError:
            terminal.write()
            return last_status
        except KeyboardInterrupt:
            terminal.write("\nInput cleared. /exit to quit.\n")
            continue
        if not text.strip():
            continue
        try:
            if text.startswith("/"):
                command, _, argument = text.partition(" ")
                argument = argument.strip()
                if command == "/exit" and not argument:
                    return last_status
                if command == "/help" and not argument:
                    terminal.write("\nWork", color="cyan")
                    terminal.write("  Type a request or follow-up; it is saved in this session.")
                    terminal.write("  /continue [limits]  Retry unchanged")
                    terminal.write("    --context-budget N / --max-rounds N\n    --max-time SECONDS")
                    terminal.write("  /new               Start a fresh session")
                    terminal.write("\nSessions and limits", color="cyan")
                    terminal.write("  /goals /sessions   Goals, status lights and saved sessions")
                    terminal.write("  /resume N|ID       Resume a listed session or full ID")
                    terminal.write("  /status            Inspect outcome, goal state and budget")
                    terminal.write("  /budget [N]        Inspect or set the context ceiling")
                    terminal.write("\nReview and export", color="cyan")
                    terminal.write("  /files  /changes   Inspect selected files and staged changes")
                    terminal.write("  /diff              Review exact changes before export")
                    terminal.write("  /export PATH [--format patch|files]\n                     Export reviewed changes")
                    terminal.write("\nControls", color="cyan")
                    terminal.write("  Alt-Enter: newline; Tab: commands; Ctrl-C: stop; Ctrl-D: exit."
                                   if ui else '  End a line with \\ for multiline input; Ctrl-C cancels a run.')
                    terminal.write("  /exit              Leave; saved work stays on disk")
                    terminal.write("Draft-only: responses never approve or deliver effects.\n", color="dim")
                    continue
                if command == "/budget":
                    values = shlex.split(argument)
                    if len(values) > 1:
                        raise ValueError("usage: /budget [ceiling]")
                    if values:
                        ceiling = _positive_int(values[0])
                        ContextBudget(max_context_tokens=ceiling)
                        args.context_budget = ceiling
                    terminal.write(terminal_text(f"Context ceiling: {args.context_budget} tokens (no provider call)"))
                    continue
                if command == "/continue":
                    if goal_id is None:
                        raise ValueError("no session is selected")
                    limit_parser = _CLIArgumentParser(add_help=False)
                    limit_parser.add_argument("--context-budget", type=_positive_int, default=argparse.SUPPRESS)
                    limit_parser.add_argument("--max-rounds", type=_positive_int, default=argparse.SUPPRESS)
                    limit_parser.add_argument("--max-time", type=_positive_float, dest="max_time", default=argparse.SUPPRESS)
                    limit_args = limit_parser.parse_args(shlex.split(argument))
                    run_args = argparse.Namespace(**vars(args))
                    if goal_id in seeded_goals:
                        for name in ("source_dir", "artifact", "artifact_id", "artifact_name", "artifact_path"):
                            setattr(run_args, name, None)
                        run_args.fresh = False
                    for name, value in vars(limit_args).items():
                        setattr(run_args, name, value)
                    ContextBudget(max_context_tokens=run_args.context_budget)
                    with Store(_state_path(args.state)) as store:
                        store.reopen_for_run(goal_id)
                    execute(goal_id, run_args)
                    continue
                if command == "/new" and not argument:
                    goal_id = None
                    last_status = 0
                    terminal.write("\nNew session. What would you like to do?\n")
                    continue
                if command in {"/goals", "/sessions"} and not argument:
                    sessions = _chat_sessions(args.state)
                    listed_ids = [goal["id"] for goal in sessions]
                    terminal.write()
                    for number, goal in enumerate(sessions, 1):
                        title = " ".join(terminal_text(goal["outcome"]).split())
                        short_id = goal["id"][:8]
                        label, color = goal_indicator(goal)
                        terminal.write(f"● {number}. {short_id} / {label}", color=color)
                        terminal.write(f"  {title}")
                        terminal.write(f"  Goal: {goal['status']} · run completion is not acceptance")
                    terminal.write("Use /resume <number>." if sessions else "No saved sessions yet.")
                    terminal.write()
                    continue
                if command == "/resume" and argument:
                    selected = argument
                    if argument.isdecimal():
                        number = int(argument)
                        if not 1 <= number <= len(listed_ids):
                            raise ValueError("Use /sessions, then /resume with a listed number.")
                        selected = listed_ids[number - 1]
                    with Store(_state_path(args.state)) as store:
                        goal = store.goal(selected)
                    if goal["status"] == "cancelled":
                        raise ValueError("This session is cancelled. Use /new to start another.")
                    goal_id = selected
                    last_status = 0
                    terminal.write(terminal_text(f"\nResumed: {' '.join(goal['outcome'].split())}"))
                    terminal.write("Saved requests and artifacts are available. Type to continue.")
                    terminal.write("Local session files are recovered automatically when available.")
                    terminal.write()
                    continue
                if command == "/status" and not argument:
                    if goal_id is None:
                        terminal.write("\nNew session; nothing saved until your first request.\n")
                    else:
                        _chat_status(args.state, goal_id, args)
                    continue
                if command in {"/files", "/changes", "/diff"} and not argument:
                    if goal_id is None:
                        raise ValueError("Select a saved session first.")
                    code_command = command[1:]
                    report = _code_command(args.state, goal_id, code_command)
                    _print_code_review(code_command, report)
                    if code_command == "diff":
                        reviewed[goal_id] = report["review_id"]
                    continue
                if command == "/export" and argument:
                    if goal_id is None or goal_id not in reviewed:
                        raise ValueError("Run /diff in this session before exporting.")
                    parser = _CLIArgumentParser(prog="/export", add_help=False)
                    parser.add_argument("output")
                    parser.add_argument("--format", choices=("patch", "files"), default="patch")
                    export_args = parser.parse_args(shlex.split(argument))
                    export_args.review = reviewed[goal_id]
                    exported = _code_command(args.state, goal_id, "export-code", export_args)
                    terminal.write("Export successful", color="green")
                    terminal.write(terminal_text(f"\nExported {exported['format']}: {exported['output_path']}\nSHA-256: {exported['sha256']}\n"))
                    continue
                raise ValueError("Unknown command or arguments. Use /help.")

            text = _required_text(text, None, "request")
            with Store(_state_path(args.state)) as store:
                if goal_id is None:
                    goal_id = store.create_goal(text)["id"]
                else:
                    # A new explicit request resumes paused work, but never
                    # carries an old effect grant into the conversational UI.
                    store.request(goal_id, text, control="draft")
            run_args = argparse.Namespace(**vars(args))
            if goal_id in seeded_goals:
                run_args.source_dir = None
                run_args.fresh = False
                run_args.artifact = run_args.artifact_id = run_args.artifact_name = None
                run_args.artifact_path = None
            result = execute(goal_id, run_args)
        except Exception as exc:
            last_status = 1
            terminal.field("\nError", str(exc), color="red")
            if goal_id is not None:
                terminal.write("/continue retries unchanged; /status for details.\n")


def _add_state_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", dest="state", default=argparse.SUPPRESS, help="workspace state directory")


def _add_provider_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=("openai-codex", "openai"), default="openai-codex",
                        help="Codex subscription (default), or explicit OpenAI API billing")
    parser.add_argument("--auth-file", help="pi-format OAuth store outside workspace/source directories")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="used only with --provider openai")


def _add_execution_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--execution", choices=("python", "docker"), default="python",
        help="restricted macOS Python (default), or disposable isolated repository commands",
    )
    parser.add_argument(
        "--container-image", default="goal-native-runtime:local",
        help="existing local runtime image, resolved to an immutable ID; never pulled automatically",
    )
    parser.add_argument(
        "--allow-network", action="store_true",
        help="permit command-requested container network egress; selected sources may leave the machine",
    )


def _add_worker_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="explicit provider model id")
    _add_provider_options(parser)
    _add_execution_options(parser)
    parser.add_argument("--max-rounds", type=_positive_int, default=12)
    parser.add_argument("--max-time", type=_positive_float, default=300.0, dest="max_time")
    parser.add_argument(
        "--context-budget", type=_positive_int, default=ContextBudget.max_context_tokens,
        help="conservative whole-request token ceiling including reserves (default: %(default)s); not a Codex output cap",
    )
    parser.add_argument("--source-dir", dest="source_dir", help="copy this selected local directory into the staged capability")
    parser.add_argument("--fresh", action="store_true", help="start with empty local files; keep saved requests and artifacts")
    artifact_group = parser.add_mutually_exclusive_group()
    artifact_group.add_argument("--artifact", help="artifact id, or exact artifact name")
    artifact_group.add_argument("--artifact-id", help="named artifact id to stage")
    artifact_group.add_argument("--artifact-name", help="exact artifact name to stage")
    parser.add_argument("--artifact-path", help="relative staged filename for selected artifact text")


def _add_run_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], name: str, help_text: str) -> None:
    parser = subparsers.add_parser(name, help=help_text)
    _add_state_option(parser)
    _add_worker_options(parser)
    parser.add_argument("goal_id")


class _CLIArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CLIArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _CLIArgumentParser(prog="python -m goal_native", description="Goal Native: open a session, or use a JSON command")
    parser.add_argument("--state", default=".state", help="workspace state directory")
    _add_worker_options(parser)
    subparsers = parser.add_subparsers(dest="command")
    chat = subparsers.add_parser("chat", help="open a session; goals are handled automatically")
    _add_state_option(chat)
    _add_worker_options(chat)
    for command in ("login", "logout", "auth-status", "models"):
        auth_parser = subparsers.add_parser(command, help=f"Codex subscription {command} through pi")
        auth_parser.add_argument("--auth-file", help="pi-format OAuth store; default ~/.config/goal-native/auth.json")
        if command == "login":
            auth_parser.add_argument("--method", choices=("browser", "device_code"), default="browser")


    create = subparsers.add_parser("create", help="create a durable goal")
    _add_state_option(create)
    create.add_argument("outcome", nargs="?")
    create.add_argument("--outcome", dest="outcome_option")
    create.add_argument("--parent-id")
    create.add_argument("--kind", default="outcome")
    create.add_argument("--criteria", default="")
    create.add_argument("--constraints", default="")

    listing = subparsers.add_parser("list", help="list durable goals")
    _add_state_option(listing)

    show = subparsers.add_parser("show", help="show one goal and its durable records")
    _add_state_option(show)
    show.add_argument("goal_id")
    show.add_argument(
        "--summary", action="store_true",
        help="show recorded work, tool receipts and delivery without provider traces or artifact bodies",
    )

    request = subparsers.add_parser("request", help="record a request or pause/cancel/draft/resume control")
    _add_state_option(request)
    request.add_argument("goal_id")
    request.add_argument("text", nargs="?")
    request.add_argument("--text", dest="text_option")
    request.add_argument("--control", choices=("pause", "cancel", "draft", "resume", "allow_effects"))

    revise = subparsers.add_parser("revise", help="CAS-revise a goal")
    _add_state_option(revise)
    revise.add_argument("goal_id")
    revise.add_argument("--expected-revision", type=_positive_int, required=True)
    revise.add_argument("--outcome", required=True)
    revise.add_argument("--criteria", default="")
    revise.add_argument("--constraints", default="")

    _add_run_parser(subparsers, "run", "run an existing goal through the canonical Worker")
    _add_run_parser(subparsers, "resume", "resume an existing goal with a replacement assignment")

    ask = subparsers.add_parser("ask", help="create a minimal goal and run it once")
    _add_state_option(ask)
    _add_worker_options(ask)
    ask.add_argument("question")
    ask.add_argument("--kind", default="question")
    ask.add_argument("--criteria", default="")
    ask.add_argument("--constraints", default="")

    artifacts = subparsers.add_parser("artifacts", help="list or add goal artifacts")
    _add_state_option(artifacts)
    artifact_subparsers = artifacts.add_subparsers(dest="artifact_command", required=True)
    artifact_list = artifact_subparsers.add_parser("list", help="list artifacts for a goal")
    _add_state_option(artifact_list)
    artifact_list.add_argument("goal_id")
    artifact_add = artifact_subparsers.add_parser("add", help="add a human-authored artifact")
    _add_state_option(artifact_add)
    artifact_add.add_argument("goal_id")
    content_group = artifact_add.add_mutually_exclusive_group()
    content_group.add_argument("--text")
    content_group.add_argument("--file")
    artifact_add.add_argument("--kind", default="draft")
    artifact_add.add_argument("--name", default="Human draft")
    artifact_add.add_argument("--limitations", default="")

    assess = subparsers.add_parser("assess", help="record a trusted human/controller assessment")
    _add_state_option(assess)
    assess.add_argument("goal_id")
    assess.add_argument("--artifact-id", required=True)
    assess.add_argument("--invocation-id")
    assess.add_argument("--check", required=True)
    passed = assess.add_mutually_exclusive_group(required=True)
    passed.add_argument("--passed", dest="passed", action="store_true")
    passed.add_argument("--failed", dest="passed", action="store_false")
    assess.add_argument("--details", default="")

    accept = subparsers.add_parser("accept", help="accept an exact current candidate")
    _add_state_option(accept)
    accept.add_argument("goal_id")
    accept.add_argument("--artifact-id", required=True)
    accept.add_argument("--evidence-id", required=True)
    accept.add_argument("--expected-revision", type=_positive_int, required=True)

    prepare = subparsers.add_parser("prepare", help="prepare an exact mock effect")
    _add_state_option(prepare)
    prepare.add_argument("--invocation-id", required=True)
    prepare.add_argument("--artifact-id", required=True)
    prepare.add_argument("--target", required=True)
    prepare.add_argument("--expected-version", type=lambda value: _nonnegative_int(value), required=True)
    prepare.add_argument("--evidence-id", required=True)

    for name, help_text in (("approve", "approve a prepared effect"), ("commit", "commit an approved effect"), ("reconcile", "reconcile an effect by operation identity")):
        effect_parser = subparsers.add_parser(name, help=help_text)
        _add_state_option(effect_parser)
        effect_parser.add_argument("effect_id")
        if name == "commit":
            effect_parser.add_argument("--lose-response", action="store_true")

    source_preview = subparsers.add_parser("source-preview", help="inspect source selection and exclusions without a model call")
    source_preview.add_argument("path")
    for name in ("files", "changes", "diff"):
        code_parser = subparsers.add_parser(name, help=f"{name} for the current local staged candidate")
        _add_state_option(code_parser)
        code_parser.add_argument("goal_id")
    export_code = subparsers.add_parser("export-code", help="export exact reviewed code without modifying the host project")
    _add_state_option(export_code)
    export_code.add_argument("goal_id")
    export_code.add_argument("--review", required=True, help="review ID returned by diff")
    export_code.add_argument("--output", required=True, help="new file outside source/state; never overwrites")
    export_code.add_argument("--format", choices=("patch", "files"), default="patch")

    export = subparsers.add_parser("export", help="export the complete portable workspace")
    _add_state_option(export)
    export.add_argument("--output")

    import_parser = subparsers.add_parser("import", help="import a portable workspace into an empty state")
    _add_state_option(import_parser)
    import_parser.add_argument("file")

    doctor = subparsers.add_parser("doctor", help="check runtime prerequisites without exposing secrets")
    _add_state_option(doctor)
    doctor.add_argument("--model")
    _add_provider_options(doctor)
    _add_execution_options(doctor)

    serve = subparsers.add_parser("serve", help="lazily start the optional localhost browser interface")
    _add_state_option(serve)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--model", default=None)
    # Shared options before a command must not be replaced by that command's
    # defaults (especially an explicit provider/model/auth-file selection).
    shared = {action.dest for action in parser._actions} - {"command", "help"}
    for command_parser in subparsers.choices.values():
        for action in command_parser._actions:
            if action.dest in shared:
                action.default = argparse.SUPPRESS
    return parser


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    interactive = False
    using_tui = False
    try:
        args = build_parser().parse_args(argv)
        interactive = args.command in {None, "chat"}
        if interactive:
            if interactive_terminal():
                using_tui = True
                with PiTerminal() as ui:
                    return _chat(args, ui)
            return _chat(args)
        payload, status = _dispatch(args)
        _emit(payload)
        return status
    except _CLIArgumentError as exc:
        _emit({"error": str(exc), "type": "ArgumentError"})
        return 2
    except CLICancelled as exc:
        _emit(exc.payload)
        return 130
    except KeyboardInterrupt:
        _emit({"status": "cancelled", "result": "CLI interrupted by Ctrl-C", "exit_code": 130})
        return 130
    except BrokenPipeError as exc:
        if using_tui:
            print("Interactive terminal disconnected; run cancelled.", file=sys.stderr)
            return 130
        _emit({"error": str(exc), "type": "BrokenPipeError"})
        return 1
    except Exception as exc:
        if interactive:
            print(terminal_text(f"Error: {exc}"), file=sys.stderr)
        else:
            _emit({"error": str(exc), "type": exc.__class__.__name__})
        return 1


__all__ = ["build_parser", "main"]
