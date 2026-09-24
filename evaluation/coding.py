"""Frozen historical coding tasks, isolated checks, and paired harness runs.

All source archives, images, candidates, session files, and raw traces stay
outside Git. Historical public tasks are a development pilot, not a superiority
benchmark or an adversarially unforgeable test system.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

from goal_native import workspace
from goal_native.container_runtime import ContainerRuntime, ContainerRuntimeError
from goal_native.context import ContextBudget
from goal_native.sandbox import Sandbox
from goal_native.store import Store
from goal_native.worker import PiBridge, Worker, tool_schemas
from . import python_tasks, typescript_tasks
from .coding_tasks import registration_document, validate_registration, write_registration
from .native import NativeSessionBridge

PILOT_SCHEMA = "goal-native-coding-pilot-run/v1"
PREPARED_SCHEMA = "goal-native-coding-preparation/v1"
NATIVE_RESULT_SCHEMA = "goal-native-pi-session-result/v1"
CHECK_RESULT_SCHEMA = "goal-native-independent-checker/v1"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
ARMS = ("goal-native", "native-pi")
PHASES = ("cold", "continuation")
USAGE_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens", "cost_usd")


class CodingPilotError(RuntimeError):
    """The requested run cannot produce an honest evidence record."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical(value).encode("utf-8"))


def _external(path: str | Path, name: str) -> Path:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise CodingPilotError(f"{name} must not be a symlink")
    resolved = raw.resolve()
    if resolved == SOURCE_ROOT or SOURCE_ROOT in resolved.parents:
        raise CodingPilotError(f"{name} must be outside the repository")
    return resolved


def _hash_tree(root: Path) -> str:
    root = _external(root, "candidate")
    try:
        files, _ = workspace.snapshot_directory(root, strict=True)
    except (OSError, ValueError) as error:
        raise CodingPilotError(str(error)) from error
    return workspace.snapshot_hash(files)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(canonical(_jsonable(value)) + "\n")


def _harness_identity() -> str:
    files = [SOURCE_ROOT / "package.json", SOURCE_ROOT / "runtime/Dockerfile"]
    for directory, pattern in (("goal_native", "*.py"), ("bridge", "*.mjs"), ("evaluation", "*.py")):
        files.extend((SOURCE_ROOT / directory).glob(pattern))
    return sha256_json({path.relative_to(SOURCE_ROOT).as_posix(): sha256_bytes(path.read_bytes()) for path in sorted(files)})


def normalize_usage(value: Any) -> dict[str, int | float | None]:
    result: dict[str, int | float | None] = {field: None for field in USAGE_FIELDS}
    if isinstance(value, Mapping):
        for field in USAGE_FIELDS:
            number = value.get(field)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                continue
            if field == "cost_usd" or isinstance(number, int):
                result[field] = number
    return result


def _selected(registration: Mapping[str, Any], task_ids: Sequence[str] | None) -> list[dict[str, Any]]:
    validate_registration(registration)
    all_tasks = {task["id"]: task for task in registration["tasks"]}
    selected = list(all_tasks) if task_ids is None else list(task_ids)
    if not selected or len(set(selected)) != len(selected) or any(task_id not in all_tasks for task_id in selected):
        raise CodingPilotError("select unique registered task IDs")
    return [all_tasks[task_id] for task_id in selected]


def _module(task: Mapping[str, Any]) -> Any:
    return typescript_tasks if task["repository"] == "prettier/prettier" else python_tasks


def _build_image(runtime: ContainerRuntime, context: Path, recipe: str, tag: str) -> str:
    context.mkdir(parents=True, exist_ok=True)
    dockerfile = context / "Dockerfile"
    if dockerfile.exists() and dockerfile.read_text() != recipe:
        raise CodingPilotError("image recipe changed in an existing preparation")
    dockerfile.write_text(recipe, encoding="utf-8")
    with tempfile.NamedTemporaryFile(prefix="build-", suffix=".log", dir=context.parent, delete=False) as output:
        log_path = Path(output.name)
        process = subprocess.Popen(
            [*runtime._docker_call_prefix(), "build", "--pull=false", "--tag", tag, str(context)],
            cwd="/", env=runtime._controller_environment(), stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            if process.wait(timeout=600) != 0:
                raise CodingPilotError(f"dependency image build failed; inspect {log_path}")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
    inspected = runtime._docker_call(["image", "inspect", "--format", "{{.Id}}", tag])
    if inspected.returncode or not inspected.stdout.strip().startswith("sha256:"):
        raise CodingPilotError("built image has no immutable local identity")
    return inspected.stdout.strip()


def _check_candidate(prepared: Mapping[str, Any], task: Mapping[str, Any], candidate: Path, phase: str) -> dict[str, Any]:
    candidate_hash = _hash_tree(candidate)
    checker_hash = prepared["checker_sha256"]
    image = prepared["checker_image"]
    argv = _module(task).check_argv(prepared, phase, candidate_sha256=candidate_hash,
        checker_sha256=checker_hash, environment_identity=image)
    started = time.monotonic()
    observation: dict[str, Any] = {
        "protocol": CHECK_RESULT_SCHEMA, "candidate_sha256": candidate_hash,
        "checker_sha256": checker_hash, "environment_identity": image,
        "status": "error", "observations": {},
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
    try:
        with ContainerRuntime(candidate, image=image) as runtime:
            receipt = runtime.command({"argv": argv, "timeout_seconds": 90, "network": False}, publish=False)
    except (ContainerRuntimeError, OSError) as error:
        observation.update(error=str(error), elapsed_seconds=round(time.monotonic() - started, 6))
        return observation
    observation.update(receipt=receipt, elapsed_seconds=round(time.monotonic() - started, 6))
    if receipt.get("timed_out"):
        observation["status"] = "timeout"
        return observation
    if (receipt.get("exit_code") != 0 or any(receipt.get(key) for key in ("cancelled", "output_limited", "published"))
            or receipt.get("before", {}).get("candidate") != candidate_hash
            or receipt.get("after", {}).get("candidate") != candidate_hash
            or _hash_tree(candidate) != candidate_hash):
        observation["error"] = "checker transport, candidate immutability, or publication boundary failed"
        return observation
    try:
        raw = json.loads(receipt["stdout"])
    except (KeyError, json.JSONDecodeError):
        observation["error"] = "checker emitted no complete JSON report"
        return observation
    schema = typescript_tasks.CHECK_SCHEMA if task["repository"] == "prettier/prettier" else python_tasks.CHECKER_SCHEMA
    if not isinstance(raw, dict) or raw.get("protocol", raw.get("schema")) != schema or raw.get("task_id") != task["id"] or raw.get("phase") != phase:
        observation["error"] = "checker report identity does not match the request"
        return observation
    details = raw.get("observations", raw)
    checks = details.get("checks") if isinstance(details, dict) else None
    if not isinstance(checks, list) or not checks or len(checks) > 64 or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("passed"), bool) for item in checks
    ):
        observation["error"] = "checker returned no bounded behavioral checks"
        return observation
    names = [item["name"] for item in checks]
    expected = prepared.get("expected_checks", {}).get(phase)
    if len(set(names)) != len(names) or (expected is not None and sorted(names) != sorted(expected)):
        observation["error"] = "checker omitted or changed registered behavioral checks"
        return observation
    passed = all(item["passed"] for item in checks)
    if raw.get("status") != ("passed" if passed else "failed"):
        observation["error"] = "checker status contradicts its observations"
        return observation
    observation.update(status=raw["status"], observations=details)
    return observation


def prepare_pilot(root: str | Path, *, base_image: str, task_ids: Sequence[str] | None = None) -> dict[str, Any]:
    root = _external(root, "preparation root")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "prepared.json"
    if destination.exists():
        raise CodingPilotError("preparation already frozen; run it or select a new external root")
    registration = registration_document()
    tasks = _selected(registration, task_ids)
    if not (root / "registration.json").exists():
        write_registration(root / "registration.json")
    else:
        validate_registration(json.loads((root / "registration.json").read_text()))
    prepared_tasks = {}
    with tempfile.TemporaryDirectory(prefix="goal-native-image-controller-") as directory:
        with ContainerRuntime(Path(directory).resolve(), image=base_image) as controller:
            base_tag = "goal-native-coding-base:" + controller.image.removeprefix("sha256:")
            tagged = controller._docker_call(["image", "tag", controller.image, base_tag])
            if tagged.returncode:
                raise CodingPilotError("cannot pin the selected base image locally")
            for task in tasks:
                prepared = _module(task).prepare(task, root / "corpus")
                # Corpus archives stay untouched. Only the acknowledged file
                # selection is offered to workers, checkers, and image builds.
                for side in ("source", "reference"):
                    raw = Path(prepared[f"{side}_dir"])
                    selected = raw.parent / f"selected-{side}"
                    if not selected.exists():
                        workspace.copy_selected_directory(str(raw), selected)
                    if _hash_tree(selected) != prepared[f"{side}_snapshot_sha256"]:
                        raise CodingPilotError("selected corpus snapshot changed")
                    prepared[f"raw_{side}_dir"] = raw
                    prepared[f"{side}_dir"] = selected
                recipe = "FROM " + base_tag + "\n" + "\n".join(prepared["setup_commands"]) + "\n"
                dependency_key = sha256_bytes(recipe.encode())
                dependency_tag = "goal-native-coding-deps:" + dependency_key
                worker_image = _build_image(controller, root / "build" / dependency_key, recipe, dependency_tag)
                checker_bytes = Path(prepared["checker_path"]).read_bytes()
                checker_hash = sha256_json({"code": sha256_bytes(checker_bytes), "reference": prepared["reference_snapshot_sha256"]})
                checker_name = "checker.cjs" if task["repository"] == "prettier/prettier" else "checker.py"
                checker_recipe = f"FROM {dependency_tag}\nCOPY {checker_name} /opt/goal-native-checker/{checker_name}\n"
                if task["repository"] != "prettier/prettier":
                    checker_recipe += "COPY reference/ /opt/goal-native-checker/reference/\n"
                checker_recipe += "RUN chmod -R a+rX /opt/goal-native-checker\n"
                checker_key = sha256_json({"worker_image": worker_image, "checker": checker_hash, "recipe": checker_recipe})
                checker_context = root / "build" / checker_key
                checker_context.mkdir(parents=True, exist_ok=True)
                (checker_context / checker_name).write_bytes(checker_bytes)
                if task["repository"] != "prettier/prettier":
                    reference = checker_context / "reference"
                    if not reference.exists():
                        workspace.copy_selected_directory(str(prepared["reference_dir"]), reference)
                    if _hash_tree(reference) != prepared["reference_snapshot_sha256"]:
                        raise CodingPilotError("reference build context changed")
                checker_image = _build_image(controller, checker_context, checker_recipe, "goal-native-coding-check:" + checker_key)
                prepared.update(worker_image=worker_image, checker_image=checker_image, checker_sha256=checker_hash)
                proof = {}
                expected = {}
                for phase in ("cold", "changed"):
                    reference_result = _check_candidate(prepared, task, Path(prepared["reference_dir"]), phase)
                    if reference_result["status"] != "passed":
                        _write_json(root / "failures" / f"{task['id']}-{phase}-reference-{time.time_ns()}.json", reference_result)
                        raise CodingPilotError(f"reference oracle failed for {task['id']}:{phase}")
                    expected[phase] = [item["name"] for item in reference_result["observations"]["checks"]]
                    prepared["expected_checks"] = expected
                    source_result = _check_candidate(prepared, task, Path(prepared["source_dir"]), phase)
                    if source_result["status"] != "failed":
                        _write_json(root / "failures" / f"{task['id']}-{phase}-source-{time.time_ns()}.json", source_result)
                        raise CodingPilotError(f"pre-fix source is not a genuine behavioral failure for {task['id']}:{phase}")
                    proof[phase] = {"before": source_result, "reference": reference_result}
                prepared["oracle_proof"] = proof
                prepared_tasks[task["id"]] = _jsonable(prepared)
                print(f"prepared {task['id']}: pre-fix fails, reference passes both phases", file=sys.stderr, flush=True)
            document = {
                "schema": PREPARED_SCHEMA, "registration": registration,
                "harness_sha256": _harness_identity(), "base_image": controller.image,
                "tools_sha256": sha256_json(tool_schemas(controller)), "tasks": prepared_tasks,
                "limitations": ["historical public tasks may be familiar to the model", "three repositories; overlapping Prettier tasks", "conventional executable checks, not an adversarially unforgeable verifier"],
            }
    document["preparation_sha256"] = sha256_json(document)
    _write_json(destination, document)
    return document


def _load_prepared(path: str | Path) -> dict[str, Any]:
    source = _external(path, "prepared input")
    document = json.loads(source.read_text())
    body = dict(document)
    recorded = body.pop("preparation_sha256", None)
    if document.get("schema") != PREPARED_SCHEMA or recorded != sha256_json(body):
        raise CodingPilotError("preparation identity is invalid")
    validate_registration(document["registration"])
    if document.get("harness_sha256") != _harness_identity():
        raise CodingPilotError("harness changed after preparation; freeze a new preparation before measurement")
    return document


class _RecordingBridge:
    def __init__(self, bridge: Any, path: Path) -> None:
        self.bridge = bridge
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.output = path.open("x", encoding="utf-8")
        self.digest = hashlib.sha256()
        self.bytes = 0
        self.payload_count = 0
        self.responses: dict[str, Any] = {}

    def _record(self, value: Mapping[str, Any]) -> None:
        line = canonical(value) + "\n"
        data = line.encode("utf-8")
        if self.bytes + len(data) > 50_000_000:
            raise CodingPilotError("controller trace byte bound exceeded")
        self.output.write(line)
        self.output.flush()
        self.digest.update(data)
        self.bytes += len(data)

    def run(self, request: dict[str, Any], *, rpc_handler: Any, event_handler: Any, cancel_event: Any) -> dict[str, Any]:
        def rpc(method: str, payload: dict[str, Any]) -> Any:
            self._record({"type": "rpc", "method": method, "payload": payload})
            if method == "provider_payload":
                self.payload_count += 1
            return rpc_handler(method, payload)

        def event(value: dict[str, Any]) -> None:
            self._record({"type": "event", "event": value})
            response = value.get("data", {}).get("response") if value.get("type") == "provider_stream_event" else None
            if isinstance(response, dict) and isinstance(response.get("id"), str):
                self.responses[response["id"]] = response.get("usage")
            event_handler(value)
        return self.bridge.run(request, rpc_handler=rpc, event_handler=event, cancel_event=cancel_event)

    def cancel(self) -> None:
        self.bridge.cancel()

    def close(self) -> None:
        self.output.close()

    def usage(self) -> dict[str, int | float | None]:
        result = normalize_usage(None)
        if not self.responses or len(self.responses) != self.payload_count:
            return result
        for name in USAGE_FIELDS[:-1]:
            values = []
            for raw in self.responses.values():
                if not isinstance(raw, dict):
                    values.append(None)
                elif name == "cached_input_tokens":
                    values.append(raw.get("input_tokens_details", {}).get("cached_tokens"))
                else:
                    values.append(raw.get(name))
            if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
                result[name] = sum(values)
        return result


def _validate_native_result(result: Mapping[str, Any] | None, *, task: Mapping[str, Any], phase: str,
        registration_hash: str, candidate_identity: str, check_identity: str, environment_identity: str,
        environment_snapshot_sha256: str, continuation_ref: str | None,
        execution: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    if result is None:
        return {"status": "missing", "usage": normalize_usage(None)}, "native session did not return a result"
    expected = {"registration_sha256": registration_hash, "task_snapshot_sha256": task["snapshot_sha256"],
        "candidate_identity": candidate_identity, "check_identity": check_identity,
        "environment_identity": environment_identity, "environment_snapshot_sha256": environment_snapshot_sha256}
    identity, lifecycle, provider, configuration, trace = (result.get(key, {}) for key in (
        "identity", "lifecycle", "provider", "configuration", "raw_trace"))
    error = None
    if any(not isinstance(value, Mapping) for value in (identity, lifecycle, provider, configuration, trace)):
        return {**result, "status": "invalid"}, "native result sections are malformed"
    if result.get("protocol") != NATIVE_RESULT_SCHEMA or any(identity.get(key) != value for key, value in expected.items()):
        error = "native result protocol or identity mismatch"
    elif lifecycle.get("phase") != phase or (phase == "continuation" and lifecycle.get("resumed_from") != continuation_ref):
        error = "native continuation linkage is not proven"
    elif provider.get("native_session") is not True or provider.get("kind") != "pi-coding-agent-sdk":
        error = "native persisted-session evidence is missing"
    expected_configuration = {
        "provider": "openai-codex", "tool_profile": "controlled-docker-coding",
        "safety_profile": "offline-isolated-candidate", "extensions": "none",
        "tool_schema_sha256": execution["tools_sha256"], "max_rounds": execution["max_rounds"],
    }
    seconds = configuration.get("max_time_seconds")
    if (provider.get("model") != execution["model"]
            or any(configuration.get(key) != value for key, value in expected_configuration.items())
            or not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
            or not math.isfinite(seconds) or not 0 < seconds <= execution["max_time_seconds"]):
        error = error or "native execution configuration mismatch"
    provider_ids = execution["provider_request_ids"]
    request_id = provider.get("request_id")
    if ((provider_ids and request_id not in provider_ids)
            or (not provider_ids and request_id is not None)
            or (result.get("status") == "completed" and not provider_ids)):
        error = error or "native result has no matching actual provider request ID"
    if normalize_usage(result.get("usage")) != execution["usage"]:
        error = error or "native usage disagrees with controller-recorded responses"
    trace_path = Path(execution["trace_path"])
    trace_hash = trace.get("sha256")
    if (not isinstance(trace_hash, str) or len(trace_hash) != 64
            or any(character not in "0123456789abcdef" for character in trace_hash)
            or trace.get("path") != str(trace_path)):
        error = error or "native raw trace identity is malformed"
    else:
        try:
            if trace_path.is_symlink() or not trace_path.is_file() or trace_path.stat().st_size > 50_000_000:
                raise ValueError("native trace is missing, linked or oversized")
            digest, size, events = hashlib.sha256(), 0, 0
            with trace_path.open("rb") as source:
                while chunk := source.read(65536):
                    size += len(chunk)
                    if size > 50_000_000:
                        raise ValueError("native trace grew beyond its bound")
                    digest.update(chunk)
                    events += chunk.count(b"\n")
            if digest.hexdigest() != trace_hash or trace.get("bytes") != size or trace.get("event_count") != events:
                raise ValueError("native trace bytes or event count disagree with the result")
        except (OSError, ValueError) as failure:
            error = error or str(failure)
    return {**result, "status": "invalid" if error else result.get("status"), "usage": normalize_usage(result.get("usage"))}, error


def _validate_checker_result(result: Mapping[str, Any] | None, *, candidate_hash: str,
        checker_hash: str, environment_identity: str) -> tuple[dict[str, Any], str | None]:
    if result is None:
        return {"status": "missing"}, "independent checker did not return a result"
    expected = {"protocol": CHECK_RESULT_SCHEMA, "candidate_sha256": candidate_hash,
        "checker_sha256": checker_hash, "environment_identity": environment_identity}
    error = "checker identity mismatch" if any(result.get(key) != value for key, value in expected.items()) else None
    details = result.get("observations")
    checks = details.get("checks") if isinstance(details, Mapping) else None
    if result.get("status") == "passed" and (not isinstance(checks, list) or not checks or any(not isinstance(item, Mapping) or item.get("passed") is not True for item in checks)):
        error = error or "checker has no complete passing behavioral observations"
    return {**result, "status": "invalid" if error else result.get("status")}, error


def _prompt(task: Mapping[str, Any], phase: str) -> str:
    request = task["prompt"] if phase == "cold" else task["changed_requirement"]
    environment = "Python 3.11 and Node 24; no network. Staged file paths are relative; commands run in /workspace inside Linux, not at the controller's session directory. Work on staged source, preserve compatible behavior, execute relevant checks, and distinguish observed results from claims."
    if task["repository"] == "prettier/prettier":
        version = typescript_tasks._DEPENDENCY_GROUPS[task["id"]][0].replace(".", "-")
        environment += f" Production dependencies are preinstalled; use NODE_PATH=/opt/goal-native-deps/prettier-{version}/node_modules when invoking Node."
        case = typescript_tasks._CASES[task["id"]]["cold" if phase == "cold" else "changed"]["regression"]
        request += "\n\nReproducer: " + canonical({"parser": case["parser"], "options": case["options"], "input": case["input"], "expected_output": case["expected"]})
    elif task["repository"] == "pytest-dev/pytest":
        environment += " This is an unbuilt source archive. For local checks, copy pyproject.toml, README.rst, LICENSE and src to a temporary build directory; use SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST=8.4.0+goal_native python3 -m pip install --no-index --no-deps --no-build-isolation --target /tmp/pytest-site BUILD_DIR, then PYTHONPATH=/tmp/pytest-site python3 -m pytest."
    else:
        environment += " Import this checkout, not an installed Flask; use PYTHONPATH=src for src-layout revisions."
    reproductions = {
        "pytest-12444-approx-formatting": "Compare {'c': 3, 'a': 1} with pytest.approx({'a': 1, 'c': 3}): it must pass. Comparing {'c': 5, 'a': 1} must fail with one of two elements mismatched and no false mismatch for a.",
        "pytest-10839-async-fixture-warning": "A synchronous test requesting a non-autouse async fixture must fail during fixture setup with FixtureLookupError, not merely receive a coroutine and fail its assertion. Normal synchronous fixtures must still work.",
        "flask-2984-routing-exception-handler": "Register an HTTPException handler. A route /slash/ must still redirect GET /slash rather than pass that internal redirect to the handler. Genuine 404 errors remain handled.",
        "flask-5774-async-stream-context": "An async view returns Response(generate()), where generate is a synchronous generator decorated with stream_with_context and reads session during iteration. It must stay lazy, finish without a context error, and preserve sync streaming.",
        "flask-5786-redirect-session": "Within a test_client context, /redirect writes session['redirect'] and redirects to /target, which writes session['target']. After get('/redirect', follow_redirects=True), both values must be visible.",
    }
    changes = {
        "pytest-10839-async-fixture-warning": "An autouse async fixture must produce PytestRemovedIn9Warning rather than the explicit-fixture setup error.",
        "flask-2984-routing-exception-handler": "Also check a genuine 400 error and that the redirect target still runs.",
        "flask-5774-async-stream-context": "Also read request.path and g in a second yield; both must remain valid during iteration.",
        "flask-5786-redirect-session": "Make a second request with the same client and retain both session values.",
    }
    if task["id"] in reproductions:
        request += "\n\nReproducer and compatibility: " + reproductions[task["id"]]
    if phase == "continuation" and task["id"] in changes:
        request += "\n" + changes[task["id"]]
    return request + "\n\nEnvironment and scope: " + environment + " Make a general fix, not an input-specific shortcut. Do not edit benchmark infrastructure or claim controller acceptance."


def run_pilot(*, prepared_path: str | Path, output_root: str | Path, auth_file: str | Path,
        model: str, max_rounds: int = 12, max_time_seconds: float = 180,
        context_tokens: int = 65536, selected_task_ids: Sequence[str] | None = None) -> dict[str, Any]:
    preparation = _load_prepared(prepared_path)
    tasks = _selected(preparation["registration"], selected_task_ids or list(preparation["tasks"]))
    if any(task["id"] not in preparation["tasks"] for task in tasks):
        raise CodingPilotError("selected task was not prepared")
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or not 1 <= max_rounds <= 64 or not math.isfinite(max_time_seconds) or not 0 < max_time_seconds <= 300:
        raise CodingPilotError("round/time budget is outside the shared profile")
    budget = ContextBudget(max_context_tokens=context_tokens)
    output = _external(output_root, "run output")
    if output.exists() and any(output.iterdir()):
        raise CodingPilotError("run output is not empty; failed attempts are never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    auth = _external(auth_file, "explicit subscription credential")
    run_contract = {"preparation_sha256": preparation["preparation_sha256"], "tasks": [task["id"] for task in tasks],
        "arms": list(ARMS), "phases": list(PHASES), "model": model, "provider": "openai-codex",
        "max_rounds": max_rounds, "max_time_seconds": max_time_seconds, "context_tokens": context_tokens,
        "network": False, "automatic_retries": False, "cost_cap": None,
        "arm_order": "counterbalanced by frozen selected-task index"}
    run_contract["prompts"] = {task["id"]: {phase: _prompt(task, phase) for phase in PHASES} for task in tasks}
    run_contract["run_registration_sha256"] = sha256_json(run_contract)
    _write_json(output / "run-registration.json", run_contract)
    records = []
    for index, task in enumerate(tasks):
        prepared = preparation["tasks"][task["id"]]
        source = Path(prepared["source_dir"])
        if _hash_tree(source) != prepared["source_snapshot_sha256"]:
            raise CodingPilotError("frozen source changed before execution")
        for arm in (ARMS if index % 2 == 0 else tuple(reversed(ARMS))):
            arm_root = output / task["id"] / arm
            store_path = arm_root / "controller"
            with closing(Store(store_path)) as store:
                goal_id = store.create_goal(run_contract["prompts"][task["id"]]["cold"])["id"]
            previous, continuation = source, None
            for phase in PHASES:
                stage = arm_root / phase / "candidate"
                workspace.copy_selected_directory(str(previous), stage, strict=True)
                before = _hash_tree(stage)
                environment_hash = sha256_json({"worker_image": prepared["worker_image"], "checker_image": prepared["checker_image"], "tools": preparation["tools_sha256"]})
                native_bridge = None
                if arm == "native-pi":
                    native_bridge = NativeSessionBridge(metadata={
                        "phase": phase, "task_id": task["id"], "session_key": task["id"],
                        "cwd": str(stage), "state_dir": str(arm_root / "native-state"), "session_dir": str(arm_root / "sessions"),
                        "raw_trace_path": str(arm_root / phase / "native-trace.jsonl"),
                        "tool_profile": "controlled-docker-coding", "safety_profile": "offline-isolated-candidate",
                        "registration_sha256": preparation["registration"]["registration_sha256"],
                        "task_snapshot_sha256": prepared["source_snapshot_sha256"], "candidate_identity": prepared["source_snapshot_sha256"],
                        "check_identity": prepared["checker_sha256"], "environment_identity": prepared["worker_image"],
                        "environment_snapshot_sha256": environment_hash, "continuation_ref": continuation,
                    }, timeout_seconds=max_time_seconds + 10)
                bridge = native_bridge or PiBridge(timeout_seconds=max_time_seconds + 10)
                started = time.monotonic()
                with closing(Store(store_path)) as store, closing(Sandbox(stage, require_os_sandbox=False)) as sandbox, ContainerRuntime(stage, image=prepared["worker_image"]) as runtime, closing(_RecordingBridge(bridge, arm_root / phase / "controller-trace.jsonl")) as recorder:
                    if phase == "continuation":
                        store.request(goal_id, run_contract["prompts"][task["id"]][phase])
                    worker = Worker(store, model, max_rounds=max_rounds, max_total_seconds=max_time_seconds,
                        context_budget=budget, provider="openai-codex", auth_file=auth,
                        sandbox=sandbox, command_runtime=runtime, bridge=recorder)
                    outcome = worker.run(goal_id)
                    goal_status = store.goal(goal_id)["status"]
                    usage = recorder.usage()
                    provider_ids = list(recorder.responses)
                    trace = {"path": str(recorder.path), "sha256": recorder.digest.hexdigest(), "bytes": recorder.bytes}
                elapsed = time.monotonic() - started
                after = _hash_tree(stage)
                checked = _check_candidate(prepared, task, stage, "cold" if phase == "cold" else "changed")
                checked, checker_error = _validate_checker_result(checked, candidate_hash=after,
                    checker_hash=prepared["checker_sha256"], environment_identity=prepared["checker_image"])
                native, native_error = None, None
                if native_bridge:
                    native, native_error = _validate_native_result(native_bridge.observation,
                        task={**task, "snapshot_sha256": prepared["source_snapshot_sha256"]}, phase=phase,
                        registration_hash=preparation["registration"]["registration_sha256"], candidate_identity=prepared["source_snapshot_sha256"],
                        check_identity=prepared["checker_sha256"], environment_identity=prepared["worker_image"],
                        environment_snapshot_sha256=environment_hash, continuation_ref=continuation,
                        execution={"model": model, "tools_sha256": preparation["tools_sha256"],
                            "max_rounds": max_rounds, "max_time_seconds": max_time_seconds,
                            "trace_path": arm_root / phase / "native-trace.jsonl",
                            "provider_request_ids": provider_ids, "usage": usage})
                    continuation = native_bridge.continuation_reference()
                record = {
                    "schema": PILOT_SCHEMA, "registration_sha256": preparation["registration"]["registration_sha256"],
                    "run_registration_sha256": run_contract["run_registration_sha256"], "task_id": task["id"], "arm": arm, "phase": phase,
                    "candidate": {"identity": prepared["source_snapshot_sha256"], "before_sha256": before, "after_sha256": after, "root": str(stage)},
                    "checker_identity": prepared["checker_sha256"], "environment_identity": environment_hash,
                    "worker_image": prepared["worker_image"], "checker_image": prepared["checker_image"],
                    "outcome": outcome, "goal_id": goal_id, "goal_status": goal_status,
                    "checker": checked, "native": native, "usage": usage, "provider_request_ids": provider_ids,
                    "trace": trace, "elapsed_seconds": round(elapsed, 6), "evidence_error": checker_error or native_error,
                }
                _write_json(arm_root / phase / "record.json", record)
                with (output / "results.jsonl").open("a", encoding="utf-8") as log:
                    log.write(canonical(record) + "\n")
                records.append(record)
                previous = stage
                print(f"{task['id']} {arm} {phase}: {outcome['status']}; independent checks {checked['status']}", file=sys.stderr, flush=True)
        if _hash_tree(source) != prepared["source_snapshot_sha256"]:
            raise CodingPilotError("original frozen source was modified")
    report = {"schema": PILOT_SCHEMA, "contract": run_contract, "preparation_sha256": preparation["preparation_sha256"],
        "records": records, "analysis": analyze_records(records, registration=preparation["registration"], selected_task_ids=[task["id"] for task in tasks])}
    _write_json(output / "report.json", report)
    return report


def analyze_records(records: Sequence[Mapping[str, Any]], *, registration: Mapping[str, Any] | None = None,
        selected_task_ids: Sequence[str] | None = None) -> dict[str, Any]:
    frozen = registration_document() if registration is None else dict(registration)
    tasks = _selected(frozen, selected_task_ids)
    expected = {(task["id"], arm, phase) for task in tasks for arm in ARMS for phase in PHASES}
    seen: dict[tuple[Any, Any, Any], Mapping[str, Any]] = {}
    errors = []
    for record in records:
        key = (record.get("task_id"), record.get("arm"), record.get("phase"))
        if key not in expected or key in seen:
            errors.append(f"unexpected or duplicate record: {key}")
        seen[key] = record
        if record.get("registration_sha256") != frozen["registration_sha256"] or record.get("evidence_error"):
            errors.append(f"invalid record evidence: {key}")
    errors.extend(f"missing record: {key}" for key in sorted(expected - set(seen)))
    for task in tasks:
        for arm in ARMS:
            cold, changed = (seen.get((task["id"], arm, phase)) for phase in PHASES)
            if cold and changed:
                if cold["candidate"]["after_sha256"] != changed["candidate"]["before_sha256"]:
                    errors.append(f"candidate continuation mismatch: {task['id']}:{arm}")
                if any(cold.get(field) != changed.get(field) for field in ("checker_identity", "environment_identity", "goal_id")):
                    errors.append(f"continuation identity mismatch: {task['id']}:{arm}")
    summaries = {}
    for arm in ARMS:
        rows = [record for record in records if record.get("arm") == arm]
        usage = {}
        for field in USAGE_FIELDS:
            values = [record.get("usage", {}).get(field) for record in rows]
            usage[field] = sum(values) if values and all(value is not None for value in values) else None
        summaries[arm] = {"attempted_phases": len(rows), "independently_passing_phases": sum(record.get("checker", {}).get("status") == "passed" for record in rows),
            "unfinished_phases": sum(record.get("outcome", {}).get("status") != "finished" for record in rows), "usage": usage,
            "elapsed_seconds": round(sum(record.get("elapsed_seconds", 0) for record in rows), 6),
            "unverified_success_claims": None, "success_claim_note": "requires separate labeling of actual assistant claims; finished is not a success claim"}
    return {"record_count": len(records), "errors": errors, "arms": summaries,
        "claim_basis": {"selected_matrix_complete": not errors and set(seen) == expected,
            "complete_ten_task_matrix": not errors and len(tasks) == len(frozen["tasks"]),
            "claimable": False, "comparison_claimable": False, "no_generalization_claim": True,
            "reason": "development pilot only; historical contamination, correlated tasks, one attempt, and broader developer-use gates remain"}}


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and run paired historical coding tasks with independent isolated checks")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--out", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--base-image", default="goal-native-runtime:local")
    prepare.add_argument("--task", action="append")
    run = commands.add_parser("run")
    run.add_argument("--prepared", required=True)
    run.add_argument("--output-root", required=True)
    run.add_argument("--auth-file", default=str(Path.home() / ".config/goal-native/auth.json"))
    run.add_argument("--model", required=True)
    run.add_argument("--task", action="append")
    run.add_argument("--max-rounds", type=int, default=12)
    run.add_argument("--max-time-seconds", type=float, default=180)
    run.add_argument("--context-tokens", type=int, default=65536)
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            print(write_registration(args.out))
        elif args.command == "prepare":
            prepared = prepare_pilot(args.root, base_image=args.base_image, task_ids=args.task)
            print(canonical({"prepared": str(Path(args.root).resolve() / "prepared.json"), "tasks": list(prepared["tasks"]), "sha256": prepared["preparation_sha256"]}))
        else:
            report = run_pilot(prepared_path=args.prepared, output_root=args.output_root, auth_file=args.auth_file, model=args.model,
                max_rounds=args.max_rounds, max_time_seconds=args.max_time_seconds, context_tokens=args.context_tokens, selected_task_ids=args.task)
            print(canonical(report["analysis"]))
        return 0
    except (CodingPilotError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"coding pilot: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("coding pilot interrupted; completed records and raw traces remain outside Git", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(_cli())
