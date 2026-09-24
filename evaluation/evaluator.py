"""Execution, validation, and analysis for the frozen lifecycle evaluation.

This module deliberately does not manufacture a worker, tool trace, baseline,
or provider result.  A runner command must invoke the real parent Worker API
(or import a captured strong-harness transcript) and return the normalized
protocol documented in ``docs/EVALUATION.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .workloads import ARMS, REGISTRATION_SCHEMA, registration_document, scenario_by_id

SOURCE_ROOT = Path(__file__).resolve().parents[1]
DENIED_PROVIDER_WORDS = ("fake", "mock", "scripted", "stub", "synthetic")
PHASES = ("cold", "continuation")


class EvaluationError(Exception):
    """A configuration or evidence error that must be visible to the caller."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_external_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if is_inside(resolved, SOURCE_ROOT):
        raise EvaluationError("output must be outside the Goal-Native repository: %s" % resolved)
    return resolved


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical(value) + "\n")


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationError("cannot read JSON %s: %s" % (path, exc))
    if not isinstance(value, dict):
        raise EvaluationError("expected JSON object in %s" % path)
    return value


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise EvaluationError("missing result stream: %s" % path)
    rows: List[Dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise EvaluationError("invalid JSON at %s:%d: %s" % (path, number, exc))
        if not isinstance(value, dict):
            raise EvaluationError("result at %s:%d is not an object" % (path, number))
        rows.append(value)
    return rows


def normalize_usage(value: Any) -> Dict[str, Optional[float]]:
    """Normalize usage without changing unknown values into zero.

    ``total_tokens`` is derived only when both input and output counts are
    present.  Cached input is retained separately and is never silently
    subtracted or counted twice.
    """
    usage = value if isinstance(value, dict) else {}

    def number(name: str) -> Optional[float]:
        raw = usage.get(name)
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        if raw < 0 or not math.isfinite(float(raw)):
            return None
        return float(raw)

    input_tokens = number("input_tokens")
    output_tokens = number("output_tokens")
    cached_input_tokens = number("cached_input_tokens")
    supplied_total = number("total_tokens")
    if supplied_total is not None:
        total_tokens = supplied_total
    elif input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    else:
        total_tokens = None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "total_tokens": total_tokens,
        "cost_usd": number("cost_usd"),
    }


def _provider_is_forbidden(provider: Mapping[str, Any]) -> bool:
    kind = str(provider.get("kind", "")).lower()
    evidence = str(provider.get("evidence", "")).lower()
    return any(word in kind or word in evidence for word in DENIED_PROVIDER_WORDS)


def validate_response(response: Any, request: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the parent Worker API response and return a safe normalized copy."""
    if not isinstance(response, dict):
        raise EvaluationError("runner returned a non-object response")
    if response.get("protocol") != "goal-native-worker-trace/v1":
        raise EvaluationError("runner response has no supported worker trace protocol")
    if response.get("trace_schema") != "goal-native-worker-trace/v1":
        raise EvaluationError("runner response has no normalized trace schema")
    provider = response.get("provider")
    if not isinstance(provider, dict) or _provider_is_forbidden(provider):
        raise EvaluationError("provider evidence is missing or marked synthetic")
    if not provider.get("evidence"):
        raise EvaluationError("provider evidence label is required")
    if not provider.get("request_id"):
        raise EvaluationError("provider request_id is required")
    if provider.get("model") != request.get("model"):
        raise EvaluationError("provider model does not match the explicit evaluation model")
    if request.get("arm_id") == "strong_existing_harness" and not (
        provider.get("transcript_imported") is True or provider.get("native_session") is True
    ):
        raise EvaluationError("strong baseline must preserve an imported transcript or native session")
    if request.get("arm_id") == "strong_existing_harness":
        if provider.get("transcript_imported") is True and not provider.get("transcript_sha256"):
            raise EvaluationError("imported strong baseline requires a transcript digest")
        if provider.get("native_session") is True and not provider.get("session_id"):
            raise EvaluationError("native strong baseline requires a session_id")
    configuration = response.get("configuration")
    if not isinstance(configuration, dict):
        raise EvaluationError("worker configuration echo is required")
    for key in ("tool_profile", "safety_profile"):
        if configuration.get(key) != request.get(key):
            raise EvaluationError("worker %s does not match the shared execution contract" % key)
    phase = response.get("lifecycle", {}).get("phase") if isinstance(response.get("lifecycle"), dict) else None
    if phase != request.get("phase"):
        raise EvaluationError("worker lifecycle phase does not match request")
    if response.get("status") not in ("completed", "interrupted", "failed", "cancelled"):
        raise EvaluationError("invalid worker status")
    if not isinstance(response.get("events"), list):
        raise EvaluationError("normalized worker events are required")
    if not isinstance(response.get("observations"), dict):
        raise EvaluationError("normalized worker observations are required")
    lifecycle = response.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise EvaluationError("worker lifecycle metadata is required")
    if phase == "cold" and not isinstance(lifecycle.get("continuation_ref"), str):
        raise EvaluationError("cold response must provide a continuation_ref")
    if phase == "continuation" and not isinstance(lifecycle.get("resumed_from"), str):
        raise EvaluationError("continuation response must identify resumed_from")
    normalized = dict(response)
    normalized["usage"] = normalize_usage(response.get("usage"))
    reported_cost = normalized["usage"]["cost_usd"]
    request_cost_limit = request.get("max_cost_usd")
    if (
        reported_cost is not None
        and isinstance(request_cost_limit, (int, float))
        and reported_cost > float(request_cost_limit)
    ):
        raise EvaluationError("reported cell cost exceeds the parent-supplied cell bound")
    return normalized


def parse_runner_specs(values: Sequence[str]) -> Dict[str, List[str]]:
    specs: Dict[str, List[str]] = {}
    for value in values:
        if "=" not in value:
            raise EvaluationError("runner must be ARM=COMMAND: %s" % value)
        arm, command = value.split("=", 1)
        arm = arm.strip()
        if not arm or not command.strip():
            raise EvaluationError("runner must contain a non-empty arm and command")
        if arm in specs:
            raise EvaluationError("duplicate runner for arm: %s" % arm)
        try:
            specs[arm] = shlex.split(command)
        except ValueError as exc:
            raise EvaluationError("invalid runner command for %s: %s" % (arm, exc))
        if not specs[arm]:
            raise EvaluationError("empty runner command for %s" % arm)
    unknown = set(specs) - {arm["id"] for arm in ARMS}
    if unknown:
        raise EvaluationError("unknown arm runner(s): %s" % ", ".join(sorted(unknown)))
    return specs


def _request_for(
    run_id: str,
    arm_id: str,
    scenario: Mapping[str, Any],
    repetition: int,
    phase: str,
    call_index: int,
    model: str,
    tool_profile: str,
    safety_profile: str,
    state_dir: str,
    run_cost_bound_usd: float,
    cell_cost_bound_usd: float,
    continuation_ref: Optional[str] = None,
) -> Dict[str, Any]:
    scenario_input = scenario["request"]
    request: Dict[str, Any] = {
        "protocol": "goal-native-worker-request/v1",
        "run_id": run_id,
        "request_id": str(uuid.uuid4()),
        "arm_id": arm_id,
        "scenario_id": scenario["id"],
        "repetition": repetition,
        "phase": phase,
        "call_index": call_index,
        "concurrency_group": "%s/%d/%d" % (scenario["id"], repetition, call_index),
        "model": model,
        "tool_profile": tool_profile,
        "state_dir": state_dir,
        "run_cost_bound_usd": run_cost_bound_usd,
        "max_cost_usd": cell_cost_bound_usd,
        "input_digest": digest({"scenario": scenario_input}),
        "scenario": scenario_input,
    }
    if continuation_ref is not None:
        request["continuation_ref"] = continuation_ref
    return request


def invoke_runner(
    command: Sequence[str],
    request: Mapping[str, Any],
    api_key_env: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    environment = os.environ.copy()
    environment.update(
        {
            "GOAL_NATIVE_EVAL_MODEL": str(request["model"]),
            "GOAL_NATIVE_EVAL_API_KEY_ENV": api_key_env,
            "GOAL_NATIVE_EVAL_ARM": str(request["arm_id"]),
            "GOAL_NATIVE_EVAL_PHASE": str(request["phase"]),
        }
    )
    try:
        completed = subprocess.run(
            list(command),
            input=canonical(request) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvaluationError("runner invocation failed: %s" % exc)
    if completed.returncode != 0:
        raise EvaluationError("runner exited with status %d" % completed.returncode)
    try:
        response = json.loads(completed.stdout)
    except ValueError as exc:
        raise EvaluationError("runner stdout is not one JSON response: %s" % exc)
    return validate_response(response, request)


def _record_error(request: Mapping[str, Any], error: Exception) -> Dict[str, Any]:
    return {"request": dict(request), "error": {"kind": error.__class__.__name__, "message": str(error)}}


def execute_run(
    output: Path,
    runners: Mapping[str, Sequence[str]],
    model: str,
    api_key_env: str,
    tool_profile: str,
    safety_profile: str,
    max_cost_usd: float,
    timeout_seconds: float,
    selected_scenarios: Optional[Sequence[str]] = None,
) -> Path:
    """Execute the registered matrix and preserve incomplete data honestly."""
    if not model.strip():
        raise EvaluationError("--model is required and cannot be empty")
    if not api_key_env.strip():
        raise EvaluationError("--api-key-env is required; use NONE only for an explicitly local runner")
    if max_cost_usd <= 0 or not math.isfinite(max_cost_usd):
        raise EvaluationError("--max-cost-usd must be a positive finite bound")
    if timeout_seconds <= 0:
        raise EvaluationError("--timeout-seconds must be positive")
    if api_key_env != "NONE" and not os.environ.get(api_key_env):
        raise EvaluationError("required API key environment variable is absent: %s" % api_key_env)
    requested_ids = set(selected_scenarios or [scenario["id"] for scenario in registration_document()["scenarios"]])
    known_ids = {scenario["id"] for scenario in registration_document()["scenarios"]}
    unknown = requested_ids - known_ids
    if unknown:
        raise EvaluationError("unknown scenario(s): %s" % ", ".join(sorted(unknown)))
    registration = registration_document()
    planned_requests = len(runners) * sum(
        int(scenario["concurrent_calls"]) * len(scenario["phases"]) * int(registration["repetitions"])
        for scenario in registration["scenarios"]
        if scenario["id"] in requested_ids
    )
    if planned_requests <= 0:
        raise EvaluationError("no runner/scenario cells selected")
    cell_cost_bound = max_cost_usd / float(planned_requests)
    output = require_external_output(output)
    output.mkdir(parents=True, exist_ok=False)
    run_id = output.name
    write_json(output / "preregistration.json", registration)
    write_json(
        output / "run.json",
        {
            "schema": "goal-native-evaluation-run/v1",
            "run_id": run_id,
            "registration_sha256": registration["registration_sha256"],
            "model": model,
            "api_key_env": api_key_env,
            "tool_profile": tool_profile,
            "safety_profile": safety_profile,
            "max_cost_usd": max_cost_usd,
            "planned_requests": planned_requests,
            "cell_cost_bound_usd": cell_cost_bound,
            "selected_scenarios": sorted(requested_ids),
            "runner_arms": sorted(runners),
        },
    )
    records_path = output / "results.jsonl"
    for arm_id, command in runners.items():
        for scenario_id in sorted(requested_ids):
            scenario = scenario_by_id(scenario_id)
            for repetition in range(registration["repetitions"]):
                cold_results: Dict[int, Dict[str, Any]] = {}
                calls = int(scenario["concurrent_calls"])
                state_dir = str(output / "worker-state" / arm_id / scenario_id / str(repetition))
                # A concurrent group is submitted without serializing its calls.
                # The evaluator uses threads only for process invocation; the
                # worker remains responsible for actual state concurrency.
                from concurrent.futures import ThreadPoolExecutor, as_completed

                cold_requests = [
                    _request_for(
                        run_id,
                        arm_id,
                        scenario,
                        repetition,
                        "cold",
                        call_index,
                        model,
                        tool_profile,
                        safety_profile,
                        state_dir,
                        max_cost_usd,
                        cell_cost_bound,
                    )
                    for call_index in range(calls)
                ]
                with ThreadPoolExecutor(max_workers=calls) as pool:
                    futures = {
                        pool.submit(invoke_runner, command, request, api_key_env, timeout_seconds): request
                        for request in cold_requests
                    }
                    for future in as_completed(futures):
                        request = futures[future]
                        try:
                            response = future.result()
                            record = {"request": request, "response": response}
                            cold_results[int(request["call_index"])] = record
                        except Exception as exc:  # preserve the failure for report rejection
                            record = _record_error(request, exc)
                            cold_results[int(request["call_index"])] = record
                        append_jsonl(records_path, record)
                for call_index in range(calls):
                    cold = cold_results[call_index]
                    cold_response = cold.get("response")
                    continuation_ref = None
                    if isinstance(cold_response, dict):
                        continuation_ref = cold_response.get("lifecycle", {}).get("continuation_ref")
                    continuation_request = _request_for(
                        run_id,
                        arm_id,
                        scenario,
                        repetition,
                        "continuation",
                        call_index,
                        model,
                        tool_profile,
                        safety_profile,
                        state_dir,
                        max_cost_usd,
                        cell_cost_bound,
                        continuation_ref=continuation_ref,
                    )
                    if not continuation_ref:
                        append_jsonl(
                            records_path,
                            _record_error(
                                continuation_request,
                                EvaluationError("cold response did not provide a continuation_ref"),
                            ),
                        )
                        continue
                    try:
                        response = invoke_runner(command, continuation_request, api_key_env, timeout_seconds)
                        append_jsonl(records_path, {"request": continuation_request, "response": response})
                    except Exception as exc:
                        append_jsonl(records_path, _record_error(continuation_request, exc))
    return output


def _event_kind(event: Mapping[str, Any]) -> str:
    return str(event.get("kind", event.get("type", ""))).strip().lower().replace("-", "_")


def _event_data(event: Mapping[str, Any]) -> Dict[str, Any]:
    data = event.get("data")
    merged = dict(event)
    if isinstance(data, dict):
        merged.update(data)
    return merged


def _events(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for record in records:
        response = record.get("response")
        if isinstance(response, dict) and isinstance(response.get("events"), list):
            result.extend(item for item in response["events"] if isinstance(item, dict))
    return result


def _observations(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for record in records:
        response = record.get("response")
        if isinstance(response, dict) and isinstance(response.get("observations"), dict):
            result.append(response["observations"])
    return result


def _check_idempotent(records: Sequence[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Tuple[bool, str]:
    events = [_event_data(event) for event in _events(records)]
    expected_job = scenario["request"]["job_key"]
    reproductions = [
        event for event in events
        if _event_kind(event) in ("code_reproduction", "concurrent_reproduction")
    ]
    if not any(
        event.get("path") == "app/submit.py"
        and event.get("job_key") == expected_job
        and event.get("workers") == 2
        and (
            event.get("duplicate_observed") is True
            or (
                isinstance(event.get("duplicate_count"), int)
                and not isinstance(event.get("duplicate_count"), bool)
                and event.get("duplicate_count") >= 2
            )
        )
        for event in reproductions
    ):
        return False, "trace lacks a two-worker reproduction of the supplied coding bug"
    results = [
        event for event in events
        if _event_kind(event) in ("idempotency_result", "idempotent_reproduction")
    ]
    operation_ids = {
        str(event.get("operation_id"))
        for event in results
        if event.get("job_key") == expected_job and event.get("operation_id")
    }
    if len(operation_ids) != 1:
        return False, "concurrent coding reproduction lacks one stable idempotency identity"
    if not any(
        event.get("job_key") == expected_job and event.get("applied_count") == 1
        for event in results
    ):
        return False, "idempotency result did not reduce the job to one applied operation"
    return True, "two-worker code reproduction exposed the duplicate and preserved one identity"


def _check_supplier(records: Sequence[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Tuple[bool, str]:
    phase = str(records[0].get("request", {}).get("phase"))
    expected = scenario["expected"][phase]
    events = [_event_data(event) for event in _events(records)]
    omissions = set()
    for event in events:
        if _event_kind(event) in ("quote_reassessment", "reassessment"):
            values = event.get("omitted", event.get("unresolved", []))
            if isinstance(values, list):
                omissions.update(str(value) for value in values)
    if not {"discount_tier", "shipping"}.issubset(omissions):
        return False, "supplier trace did not preserve omitted discount tier and shipping"
    if "quantity" in omissions:
        return False, "supplier trace incorrectly treated known quantity as omitted"
    calculations = [
        event for event in events
        if _event_kind(event) in ("quote_calculation", "supplier_calculation", "calculation")
    ]
    matching = [
        event for event in calculations
        if event.get("quantity") == expected["quantity"]
        and event.get("totals") == expected["totals"]
        and "C shipping" in [str(item) for item in event.get("unresolved", [])]
    ]
    if not matching:
        return False, "deterministic supplier arithmetic or unresolved C shipping is missing"
    if phase == "continuation" and not any(
        event.get("from_quantity") == expected["from_quantity"]
        and event.get("quantity") == expected["quantity"]
        for event in events
        if _event_kind(event) in ("quote_reassessment", "reassessment")
    ):
        return False, "continuation did not explicitly reassess 50 units to 100"
    return True, "supplier arithmetic reassessed quantity 50 to 100 without inventing omissions"


def _check_memory(records: Sequence[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Tuple[bool, str]:
    expected = scenario["expected"]
    events = [_event_data(event) for event in _events(records)]
    reads = [event for event in events if _event_kind(event) in ("memory_read", "memory_observed")]
    if not any(
        event.get("source") == "prior_run"
        and "APP_MODE" in str(event.get("text", ""))
        and event.get("trust") == "provisional"
        for event in reads
    ):
        return False, "trace did not preserve the old provisional memory explanation"
    evidence = [
        event for event in events
        if _event_kind(event) in ("current_evidence", "evidence_read", "staged_read")
    ]
    if not any(
        event.get("path") == "deployment.log"
        and expected["current_evidence_phrase"] in str(event.get("content", event.get("text", ""))).lower()
        for event in evidence
    ):
        return False, "trace did not discover the current permission-denied evidence"
    qualifications = [
        event for event in events
        if _event_kind(event) in ("memory_qualification", "explanation", "memory_assessment")
    ]
    if any(
        event.get("used_as_current_instruction") is True
        or event.get("memory_used_as_authority") is True
        for event in qualifications
    ):
        return False, "provisional memory was used as current authority"
    if not any(
        event.get("qualified") is True
        or event.get("source_qualified") is True
        or event.get("supported") is False
        for event in qualifications
    ):
        return False, "trace did not qualify the old explanation after discovering current evidence"
    return True, "current evidence exposed the provisional explanation without prelabelled conflict"


def _check_search(records: Sequence[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Tuple[bool, str]:
    events = [_event_data(event) for event in _events(records)]
    mutations = [event for event in events if _event_kind(event) in ("workspace_mutation", "file_added")]
    searches = [event for event in events if _event_kind(event) in ("search", "workspace_search")]
    if not any(event.get("path") == "new-target.txt" and event.get("workspace_version") == 2 for event in mutations):
        return False, "trace lacks the supplied new-file mutation"
    if len(searches) < 2:
        return False, "trace did not perform a cold and post-mutation search"
    post = [event for event in searches if isinstance(event.get("workspace_version"), int) and event.get("workspace_version") >= 2]
    if not post:
        return False, "no search occurred after the workspace version changed"
    if not any("new-target.txt" in [str(item) for item in event.get("matches", [])] for event in post):
        return False, "post-mutation search did not observe the new matching file"
    return True, "negative search was invalidated by a versioned mutation and re-search"


def _check_tiny(records: Sequence[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Tuple[bool, str]:
    expected = scenario["expected_answer"]
    for record in records:
        response = record.get("response", {})
        observations = response.get("observations", {}) if isinstance(response, dict) else {}
        if observations.get("final_answer") != expected:
            return False, "exact tiny-task answer was not returned"
        if response.get("status") != "completed":
            return False, "tiny task did not complete"
    return True, "exact tiny-task answer matched the fixture"


def _check_effect_loss(records: Sequence[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Tuple[bool, str]:
    events = [_event_data(event) for event in _events(records)]
    lost = [event for event in events if _event_kind(event) in ("effect_commit", "effect_commit_attempt") and event.get("response_lost") is True]
    reconciled = [event for event in events if _event_kind(event) in ("reconcile", "effect_reconciled") and event.get("operation_id")]
    commits = [event for event in events if _event_kind(event) in ("effect_commit", "effect_committed")]
    applied = [event for event in commits if event.get("applied") is True or event.get("status") == "applied"]
    operation_ids = {str(event.get("operation_id")) for event in commits + reconciled if event.get("operation_id")}
    if not lost:
        return False, "trace did not show the prescribed lost commit response"
    if not reconciled:
        return False, "trace did not reconcile the lost response"
    if len(operation_ids) != 1 or len(applied) > 1:
        return False, "lost response produced duplicate or ambiguous operations"
    return True, "lost response reconciled to one committed operation"


CHECKS = {
    "idempotent_job": _check_idempotent,
    "supplier_reassessment": _check_supplier,
    "memory_qualification": _check_memory,
    "fresh_negative_search": _check_search,
    "tiny_exact_answer": _check_tiny,
    "effect_loss_reconcile": _check_effect_loss,
}


def _expected_keys(registration: Mapping[str, Any], selected: Set[str], arms: Set[str]) -> Set[Tuple[str, str, int, str, int]]:
    keys: Set[Tuple[str, str, int, str, int]] = set()
    for arm in arms:
        for scenario in registration["scenarios"]:
            if scenario["id"] not in selected:
                continue
            for repetition in range(int(registration["repetitions"])):
                for phase in scenario["phases"]:
                    for call_index in range(int(scenario["concurrent_calls"])):
                        keys.add((arm, scenario["id"], repetition, phase, call_index))
    return keys


def _record_key(record: Mapping[str, Any]) -> Optional[Tuple[str, str, int, str, int]]:
    request = record.get("request")
    if not isinstance(request, dict):
        return None
    try:
        return (
            str(request["arm_id"]),
            str(request["scenario_id"]),
            int(request["repetition"]),
            str(request["phase"]),
            int(request["call_index"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _validate_registration(registration: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if registration.get("schema") != REGISTRATION_SCHEMA:
        errors.append("unsupported registration schema")
    stored_hash = registration.get("registration_sha256")
    body = dict(registration)
    body.pop("registration_sha256", None)
    if not isinstance(stored_hash, str) or digest(body) != stored_hash:
        errors.append("preregistration hash is invalid")
    expected = registration_document()
    if stored_hash != expected["registration_sha256"]:
        errors.append("run does not use the frozen registration shipped with this evaluator")
    if sum(float(item["weight"]) for item in registration.get("scenarios", [])) != 1.0:
        errors.append("scenario weights do not sum to one")
    return errors


def _performance(records: Sequence[Mapping[str, Any]], registration: Mapping[str, Any], arms: Set[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    scenario_lookup = {scenario["id"]: scenario for scenario in registration["scenarios"]}

    def percentile(values: List[float], quantile: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(math.ceil(quantile * len(ordered))) - 1))
        return ordered[index]

    for arm in sorted(arms):
        arm_records = [record for record in records if record.get("request", {}).get("arm_id") == arm]
        metrics: Dict[str, Any] = {}
        for metric in ("latency_ms", "total_tokens", "cost_usd"):
            samples: List[float] = []
            for record in arm_records:
                response = record.get("response")
                if not isinstance(response, dict) or response.get("status") != "completed":
                    continue
                if metric == "latency_ms":
                    raw = response.get("metrics", {}).get("latency_ms") if isinstance(response.get("metrics"), dict) else None
                else:
                    raw = response.get("usage", {}).get(metric) if isinstance(response.get("usage"), dict) else None
                if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(float(raw)):
                    samples.append(float(raw))
            expected = sum(
                int(scenario["concurrent_calls"]) * len(scenario["phases"]) * int(registration["repetitions"])
                for scenario in registration["scenarios"]
                if scenario["id"] in {record.get("request", {}).get("scenario_id") for record in arm_records}
            )
            coverage = float(len(samples)) / float(expected) if expected else 0.0
            metrics[metric] = {
                "samples": len(samples),
                "expected_samples": expected,
                "coverage": coverage,
                "median": percentile(samples, 0.5),
                "p95": percentile(samples, 0.95),
                "max": max(samples) if samples else None,
                "unknown_is_null": True,
            }
        result[arm] = metrics
    # A weighted comparison is deliberately null unless every arm has complete
    # metric coverage.  Partial or unknown usage cannot produce an efficiency
    # ranking.
    for metric in ("latency_ms", "total_tokens", "cost_usd"):
        if not arms or any(result[arm][metric]["coverage"] < 1.0 for arm in arms):
            continue
        weighted: Dict[str, float] = {}
        for arm in arms:
            weighted[arm] = result[arm][metric]["median"] or 0.0
        result.setdefault("weighted_median", {})[metric] = weighted
        baseline = weighted.get("strong_existing_harness")
        if baseline is not None and baseline > 0:
            result.setdefault("relative_to_strong_existing_harness", {})[metric] = {
                arm: value / baseline for arm, value in weighted.items()
            }
    return result


def analyze_run(run_dir: Path) -> Dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    registration = read_json(run_dir / "preregistration.json")
    run_config = read_json(run_dir / "run.json")
    records = read_jsonl(run_dir / "results.jsonl")
    errors = _validate_registration(registration)
    selected = set(run_config.get("selected_scenarios", []))
    registered_arms = {str(item["id"]) for item in registration.get("arms", [])}
    capability_groups = {
        str(item["id"]): str(item.get("capability_group", item["id"]))
        for item in registration.get("arms", [])
    }
    distinct_arm_capabilities = len(set(capability_groups.values())) == len(capability_groups)
    observed_arms = {str(record.get("request", {}).get("arm_id")) for record in records if record.get("request", {}).get("arm_id")}
    expected = _expected_keys(registration, selected, observed_arms)
    seen: Dict[Tuple[str, str, int, str, int], int] = {}
    duplicate_keys: List[str] = []
    for record in records:
        key = _record_key(record)
        if key is None:
            errors.append("record has no complete matrix key")
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            duplicate_keys.append(str(key))
    if duplicate_keys:
        errors.append("duplicate matrix cells: " + ", ".join(sorted(duplicate_keys)))
    missing = expected - set(seen)
    if missing:
        errors.append("missing matrix cells: %d" % len(missing))
    unexpected = set(seen) - expected
    if unexpected:
        errors.append("unexpected matrix cells: %d" % len(unexpected))
    if registered_arms - observed_arms:
        errors.append("required arms absent: " + ", ".join(sorted(registered_arms - observed_arms)))
    if not registered_arms:
        errors.append("registration has no arms")
    valid_responses: List[Dict[str, Any]] = []
    for record in records:
        request = record.get("request")
        response = record.get("response")
        if not isinstance(request, dict):
            continue
        if "error" in record:
            errors.append("runner error in %s" % (str(_record_key(record)),))
            continue
        try:
            normalized = validate_response(response, request)
            valid_responses.append({"request": request, "response": normalized})
        except EvaluationError as exc:
            errors.append("invalid response in %s: %s" % (str(_record_key(record)), exc))
    model_values = {str(run_config.get("model"))} if run_config.get("model") is not None else set()
    model_values.update(str(item.get("request", {}).get("model")) for item in records if item.get("request", {}).get("model") is not None)
    if len(model_values) != 1:
        errors.append("model is not uniform across the run")
    tool_values = {str(item.get("request", {}).get("tool_profile")) for item in records if item.get("request", {}).get("tool_profile") is not None}
    safety_values = {str(item.get("request", {}).get("safety_profile")) for item in records if item.get("request", {}).get("safety_profile") is not None}
    if len(tool_values) != 1 or len(safety_values) != 1:
        errors.append("tool or safety profile is not uniform across the run")
    # Continuation linkage and input identity are checked independently of the
    # worker's self-reported score.
    by_key = {_record_key(record): record for record in records if _record_key(record) is not None}
    for key, record in by_key.items():
        if key is None or key[3] != "continuation":
            continue
        cold_key = (key[0], key[1], key[2], "cold", key[4])
        cold = by_key.get(cold_key, {})
        cold_ref = cold.get("response", {}).get("lifecycle", {}).get("continuation_ref") if isinstance(cold.get("response"), dict) else None
        resumed = record.get("response", {}).get("lifecycle", {}).get("resumed_from") if isinstance(record.get("response"), dict) else None
        if not cold_ref or resumed != cold_ref:
            errors.append("continuation is not linked to its cold response: %s" % (str(key),))
    for scenario_id in selected:
        digests = {
            str(record.get("request", {}).get("input_digest"))
            for record in records
            if record.get("request", {}).get("scenario_id") == scenario_id
            and record.get("request", {}).get("phase") in PHASES
        }
        if len(digests) != 1:
            errors.append("input digest differs across arms or phases for %s" % scenario_id)
    complete_fraction = float(len(expected & set(seen))) / float(len(expected)) if expected else 0.0
    completeness = {
        "complete_cell_fraction": complete_fraction,
        "expected_cells": len(expected),
        "observed_records": len(records),
        "valid_responses": len(valid_responses),
        "passed": complete_fraction >= float(registration["thresholds"]["complete_cell_fraction_min"]) and not any("missing matrix" in error or "runner error" in error for error in errors),
    }
    correctness: Dict[str, Any] = {"scenarios": {}, "weighted_pass_rate": None, "passed": False}
    weighted_sum = 0.0
    weighted_total = 0.0
    for scenario in registration["scenarios"]:
        if scenario["id"] not in selected:
            continue
        scenario_results: Dict[str, Any] = {}
        scenario_passes: List[bool] = []
        for arm in sorted(observed_arms):
            for repetition in range(int(registration["repetitions"])):
                for phase in scenario["phases"]:
                    group = [
                        record for record in valid_responses
                        if record.get("request", {}).get("arm_id") == arm
                        and record.get("request", {}).get("scenario_id") == scenario["id"]
                        and int(record.get("request", {}).get("repetition", -1)) == repetition
                        and record.get("request", {}).get("phase") == phase
                    ]
                    if len(group) != int(scenario["concurrent_calls"]):
                        scenario_passes.append(False)
                        scenario_results["%s/%s/%d/%s" % (arm, scenario["id"], repetition, phase)] = {"passed": False, "details": "incomplete response group"}
                        continue
                    check = CHECKS[scenario["correctness_check"]]
                    passed, details = check(group, scenario)
                    scenario_passes.append(passed)
                    scenario_results["%s/%d/%s" % (arm, repetition, phase)] = {"passed": passed, "details": details}
        rate = float(sum(1 for item in scenario_passes if item)) / float(len(scenario_passes)) if scenario_passes else 0.0
        correctness["scenarios"][scenario["id"]] = {"pass_rate": rate, "groups": len(scenario_passes), "passed": rate >= float(registration["thresholds"]["correctness_pass_rate_min"]), "details": scenario_results}
        weighted_sum += rate * float(scenario["weight"])
        weighted_total += float(scenario["weight"])
    correctness["weighted_pass_rate"] = weighted_sum / weighted_total if weighted_total else None
    correctness["passed"] = bool(correctness["weighted_pass_rate"] is not None and correctness["weighted_pass_rate"] >= float(registration["thresholds"]["correctness_pass_rate_min"]))
    baseline_records = [
        record for record in valid_responses
        if record.get("request", {}).get("arm_id") == "strong_existing_harness"
    ]
    baseline_expected = sum(
        int(scenario["concurrent_calls"]) * len(scenario["phases"]) * int(registration["repetitions"])
        for scenario in registration["scenarios"]
        if scenario["id"] in selected
    ) if "strong_existing_harness" in observed_arms else 0
    baseline_session_supported = bool(baseline_records) and len(baseline_records) == baseline_expected and all(
        isinstance(record.get("response", {}).get("provider"), dict)
        and (
            record.get("response", {}).get("provider", {}).get("transcript_imported") is True
            or record.get("response", {}).get("provider", {}).get("native_session") is True
        )
        for record in baseline_records
    )
    provider_evidence = {
        "fraction": float(len(valid_responses)) / float(len(records)) if records else 0.0,
        "passed": bool(records) and len(valid_responses) == len(records),
        "baseline_session_supported": baseline_session_supported,
    }
    performance = _performance(records, registration, observed_arms)
    known_usage_for_efficiency = all(
        performance.get(arm, {}).get("total_tokens", {}).get("coverage", 0.0) >= 1.0
        for arm in observed_arms
    ) if observed_arms else False
    strong_baseline_available = "strong_existing_harness" in observed_arms and baseline_session_supported
    fairness = {
        "passed": not errors,
        "errors": errors,
        "same_model": len(model_values) == 1,
        "same_tool_profile": len(tool_values) == 1,
        "same_safety_profile": len(safety_values) == 1,
        "required_arms": sorted(registered_arms),
        "observed_arms": sorted(observed_arms),
        "capability_groups": capability_groups,
        "distinct_arm_capabilities": distinct_arm_capabilities,
        "baseline_required": "strong_existing_harness" in registered_arms,
    }
    claim_basis = {
        "claimable": bool(
            fairness["passed"]
            and completeness["passed"]
            and correctness["passed"]
            and provider_evidence["passed"]
            and strong_baseline_available
            and distinct_arm_capabilities
        ),
        "efficiency_claimable": bool(
            fairness["passed"]
            and completeness["passed"]
            and correctness["passed"]
            and provider_evidence["passed"]
            and strong_baseline_available
            and distinct_arm_capabilities
            and known_usage_for_efficiency
        ),
        "no_novelty_claim": True,
        "requirements": {
            "frozen_registration": not any("registration" in error for error in errors),
            "complete_and_unique_matrix": completeness["passed"],
            "same_model_tools_safety": fairness["same_model"] and fairness["same_tool_profile"] and fairness["same_safety_profile"],
            "real_provider_evidence": provider_evidence["passed"],
            "strong_baseline_available": strong_baseline_available,
            "distinct_arm_capabilities": distinct_arm_capabilities,
            "deterministic_correctness": correctness["passed"],
            "known_usage_for_efficiency": known_usage_for_efficiency,
        },
    }
    report = {
        "schema": "goal-native-evaluation-report/v1",
        "registration_sha256": registration.get("registration_sha256"),
        "run": run_config,
        "fairness": fairness,
        "completeness": completeness,
        "provider_evidence": provider_evidence,
        "correctness": correctness,
        "performance": performance,
        "claim_basis": claim_basis,
    }
    write_json(run_dir / "report.json", report)
    return report


def smoke_rejection_checks() -> Dict[str, bool]:
    """Exercise reporting safeguards without contacting a provider."""
    registration = registration_document()
    selected = {registration["scenarios"][0]["id"]}
    expected = _expected_keys(registration, selected, {"goal_native"})
    incomplete = len(expected) > 0 and len(expected - {next(iter(expected))}) == len(expected) - 1
    usage = normalize_usage({"input_tokens": 4})
    unknown_is_null = usage["total_tokens"] is None and usage["output_tokens"] is None
    groups = {item["id"]: item.get("capability_group", item["id"]) for item in registration["arms"]}
    collapsed = len(set(groups.values())) < len(groups)
    return {
        "incomplete_matrix_is_detectable": incomplete,
        "unknown_usage_stays_null": unknown_is_null,
        "scripted_provider_is_rejected": _provider_is_forbidden({"kind": "scripted"}),
        "collapsed_capabilities_are_detectable": collapsed,
    }
