"""Canonical Worker runner for evaluation matrix cells.

This is an adapter, not a second worker loop.  It imports only the canonical
``goal_native.store.Store`` and ``goal_native.worker.Worker`` APIs, prepares
ordinary fixture state, calls ``Worker.run`` once, and projects durable records
into the evaluator trace protocol.  Provider execution remains in the
canonical Worker -> pi bridge.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

from goal_native.sandbox import Sandbox
from goal_native.store import Store
from goal_native.worker import Worker

SOURCE_ROOT = Path(__file__).resolve().parents[1]


class DriverError(RuntimeError):
    """The canonical Worker could not be invoked or projected honestly."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _safe_child(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise DriverError("fixture path must be a relative non-empty string")
    candidate = (root / relative).resolve()
    if _inside(candidate, root.resolve()) is False:
        raise DriverError("fixture path escapes the staged workspace")
    return candidate


def _prepare_fixture(request: Mapping[str, Any], state_dir: Path) -> Path:
    scenario = request.get("scenario")
    if not isinstance(scenario, dict):
        raise DriverError("scenario input must be an object")
    workspace = scenario.get("workspace")
    repository = scenario.get("repository")
    staged = state_dir / "staged"
    staged.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(state_dir / "fixture.lock"):
        if not (state_dir / "initial-fixture.json").exists():
            initial_files: Dict[str, Any] = {}
            for container in (workspace, repository):
                if isinstance(container, dict) and isinstance(container.get("initial_files"), dict):
                    initial_files.update(container["initial_files"])
                if isinstance(container, dict) and isinstance(container.get("files"), dict):
                    initial_files.update(container["files"])
            for relative, content in initial_files.items():
                if not isinstance(content, str):
                    raise DriverError("fixture file content must be text")
                target = _safe_child(staged, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            (state_dir / "initial-fixture.json").write_text(
                _canonical({"files": sorted(initial_files)}), encoding="utf-8"
            )
        if request.get("phase") == "continuation" and isinstance(workspace, dict):
            mutation = workspace.get("continuation_mutation")
            if isinstance(mutation, dict) and not (state_dir / "continuation-fixture.json").exists():
                relative = mutation.get("path")
                content = mutation.get("content")
                if not isinstance(content, str):
                    raise DriverError("continuation fixture content must be text")
                target = _safe_child(staged, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                (state_dir / "continuation-fixture.json").write_text(
                    _canonical({"path": relative, "workspace_version": mutation.get("workspace_version")}),
                    encoding="utf-8",
                )
    return staged


def _goal_for(request: Mapping[str, Any], store: Store, state_dir: Path) -> str:
    scenario_id = request.get("scenario_id")
    scenario = request.get("scenario")
    if not isinstance(scenario_id, str) or not isinstance(scenario, dict):
        raise DriverError("scenario identity and input are required")
    metadata_path = state_dir / "goal.json"
    with _exclusive_lock(state_dir / "goal.lock"):
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise DriverError("canonical goal metadata is invalid") from exc
            goal_id = metadata.get("goal_id") if isinstance(metadata, dict) else None
            if not isinstance(goal_id, str):
                raise DriverError("canonical goal metadata has no goal_id")
            store.goal(goal_id)
            return goal_id
        goals = store.list_goals()
        if goals:
            goal_id = goals[0].get("id")
            if not isinstance(goal_id, str):
                raise DriverError("existing canonical goal has no id")
        else:
            prompt = scenario.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                raise DriverError("scenario prompt is required")
            goal = store.create_goal(
                prompt,
                criteria="Evaluation fixture: preserve unknowns and provenance.",
                constraints=_canonical({"scenario_id": scenario_id, "fixture": scenario}),
            )
            goal_id = goal["id"]
        metadata_path.write_text(_canonical({"goal_id": goal_id, "scenario_id": scenario_id}), encoding="utf-8")
        return goal_id


def _apply_continuation_request(request: Mapping[str, Any], store: Store, goal_id: str, state_dir: Path) -> None:
    if request.get("phase") != "continuation":
        return
    scenario = request.get("scenario")
    prompt = scenario.get("continuation_prompt") if isinstance(scenario, dict) else None
    if not isinstance(prompt, str) or not prompt:
        return
    marker = state_dir / "continuation-request.json"
    with _exclusive_lock(state_dir / "continuation-request.lock"):
        if not marker.exists():
            store.request(goal_id, prompt)
            marker.write_text(_canonical({"prompt": prompt}), encoding="utf-8")




def _api_key() -> Optional[str]:
    name = os.environ.get("GOAL_NATIVE_EVAL_API_KEY_ENV")
    if not name:
        raise DriverError("GOAL_NATIVE_EVAL_API_KEY_ENV is required")
    if name == "NONE":
        return None
    value = os.environ.get(name)
    if not value:
        raise DriverError("the explicitly named API key environment variable is absent")
    return value


def _provider_request_id(invocation: Mapping[str, Any]) -> Optional[str]:
    for receipt in invocation.get("receipts", []):
        if not isinstance(receipt, dict):
            continue
        for container in (receipt.get("parameters"), receipt.get("result")):
            if not isinstance(container, dict):
                continue
            for key in ("provider_request_id", "request_id", "requestId"):
                value = container.get(key)
                if isinstance(value, str) and value:
                    return value
            if receipt.get("tool") == "pi.event.provider_response":
                response = container.get("response")
                if isinstance(response, dict):
                    value = response.get("id")
                    if isinstance(value, str) and value:
                        return value
            message = container.get("message")
            if isinstance(message, dict):
                for key in ("provider_request_id", "request_id", "requestId"):
                    value = message.get(key)
                    if isinstance(value, str) and value:
                        return value
    return None


def _events(invocation: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for receipt in invocation.get("receipts", []):
        if not isinstance(receipt, dict):
            continue
        tool = receipt.get("tool")
        result = receipt.get("result")
        if not isinstance(tool, str):
            continue
        if tool.startswith("pi.event.") and isinstance(result, dict):
            kind = result.get("type", "pi_event")
            events.append({"kind": str(kind), "data": result, "receipt": receipt})
        else:
            events.append(
                {
                    "kind": "receipt",
                    "data": {"tool": tool, "parameters": receipt.get("parameters"), "result": result},
                    "receipt": receipt,
                }
            )
    return events


def _usage(invocation: Mapping[str, Any]) -> Dict[str, Any]:
    usage = invocation.get("usage")
    normalized = usage.get("normalized") if isinstance(usage, dict) else None
    if not isinstance(normalized, dict):
        return {}
    return {
        "input_tokens": normalized.get("input_tokens"),
        "output_tokens": normalized.get("output_tokens"),
        "cached_input_tokens": normalized.get("cached_tokens"),
        "total_tokens": normalized.get("total_tokens"),
        "cost_usd": None,
    }


def run_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    if request.get("protocol") != "goal-native-worker-request/v1":
        raise DriverError("unsupported evaluator request protocol")
    model = request.get("model")
    tool_profile = request.get("tool_profile")
    safety_profile = request.get("safety_profile")
    state_value = request.get("state_dir")
    if not isinstance(model, str) or not model.strip():
        raise DriverError("explicit model is required")
    if not isinstance(tool_profile, str) or not tool_profile:
        raise DriverError("tool profile is required")
    if not isinstance(safety_profile, str) or not safety_profile:
        raise DriverError("safety profile is required")
    if not isinstance(state_value, str) or not Path(state_value).is_absolute():
        raise DriverError("state_dir must be absolute")
    state_dir = Path(state_value).resolve()
    if _inside(state_dir, SOURCE_ROOT):
        raise DriverError("canonical Worker state must be outside the repository")
    state_dir.mkdir(parents=True, exist_ok=True)
    staged = _prepare_fixture(request, state_dir)
    goal_id: Optional[str] = None
    started = time.monotonic()
    with Store(state_dir) as store:
        goal_id = _goal_for(request, store, state_dir)
        continuation_ref = "goal:" + goal_id
        supplied_ref = request.get("continuation_ref")
        if request.get("phase") == "continuation" and supplied_ref != continuation_ref:
            raise DriverError("continuation_ref does not identify the persisted canonical goal")
        _apply_continuation_request(request, store, goal_id, state_dir)
        worker = Worker(
            store,
            model,
            api_key=_api_key(),
            provider="openai",
            sandbox=Sandbox(staged),
            max_total_seconds=float(request.get("worker_timeout_seconds", 300.0)),
        )
        outcome = worker.run(goal_id)
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)
        goal = store.goal(goal_id)
        invocation_id = outcome.get("invocation_id")
        invocation = next(
            (item for item in goal.get("invocations", []) if item.get("id") == invocation_id),
            None,
        )
        if not isinstance(invocation, dict):
            raise DriverError("canonical Worker did not leave a readable invocation")
        provider_request_id = _provider_request_id(invocation)
        status = outcome.get("status")
        if status == "finished":
            status = "completed"
        response: Dict[str, Any] = {
            "protocol": "goal-native-worker-trace/v1",
            "trace_schema": "goal-native-worker-trace/v1",
            "status": status,
            "provider": {
                "kind": "pi-agent-core",
                "evidence": "canonical goal_native.worker.Worker",
                "request_id": provider_request_id,
                "model": model,
                "native_session": False,
                "transcript_imported": False,
                "transcript_sha256": None,
            },
            "configuration": {"tool_profile": tool_profile, "safety_profile": safety_profile},
            "lifecycle": {
                "phase": request.get("phase"),
                "continuation_ref": continuation_ref,
                "resumed_from": continuation_ref if request.get("phase") == "continuation" else None,
            },
            "usage": _usage(invocation),
            "metrics": {"latency_ms": elapsed_ms},
            "events": _events(invocation),
            "observations": {
                "goal_id": goal_id,
                "invocation_id": invocation_id,
                "final_answer": outcome.get("result", ""),
                "effects": goal.get("effects", []),
                "artifacts": goal.get("artifacts", []),
            },
        }
        return response


def main() -> int:
    try:
        raw = sys.stdin.read()
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise DriverError("request must be one JSON object")
        print(_canonical(run_request(request)))
        return 0
    except Exception as exc:
        print("canonical Worker driver error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
