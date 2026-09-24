"""Frozen workload registration and scenario inputs.

The registration is intentionally independent of provider output.  Expected
results are not sent to a worker; the evaluator applies deterministic checks
against the worker's normalized trace after execution.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

REGISTRATION_SCHEMA = "goal-native-evaluation/v1"
REGISTRATION_ID = "goal-native-lifecycle-2026-09-24"
REPETITIONS = 3

ARMS = (
    {
        "id": "strong_existing_harness",
        "label": "strong Pi native harness or imported native transcript",
        "kind": "native_pi_baseline_required",
        "capability_group": "external_pi_native",
        "required": True,
    },
    {
        "id": "shared_efficiency",
        "label": "canonical Worker control (shared-efficiency name; collapsed)",
        "kind": "canonical_worker_collapsed_control",
        "capability_group": "canonical_worker",
        "required": True,
    },
    {
        "id": "flat_durable",
        "label": "canonical Worker control (flat-durable name; collapsed)",
        "kind": "canonical_worker_collapsed_control",
        "capability_group": "canonical_worker",
        "required": True,
    },
    {
        "id": "goal_native",
        "label": "canonical goal-native Worker",
        "kind": "canonical_worker",
        "capability_group": "canonical_worker",
        "required": True,
    },
)

# Weights are frozen before provider execution and sum to one. A continuation
# is a separate phase with equal phase weight; concurrent reproductions are
# repeated observations of the same challenge, not extra workload weight.
SCENARIOS = (
    {
        "id": "idempotent_job_concurrent_reproduction",
        "title": "Concurrent coding reproduction of duplicate job submission",
        "weight": 0.20,
        "phases": ("cold", "continuation"),
        "concurrent_calls": 2,
        "correctness_check": "idempotent_job",
        "performance_metrics": ("latency_ms", "total_tokens", "cost_usd"),
        "request": {
            "prompt": (
                "Reproduce the supplied coding bug under two concurrent submissions. "
                "Use the ordinary staged repository, run the reproduction, and "
                "preserve one idempotency identity for the same job rather than "
                "pretending an arbitrary effect job is the test."
            ),
            "job_key": "eval-code-reproduction-001",
            "repository": {
                "files": {
                    "app/submit.py": (
                        "import time\n\n"
                        "def submit(job_key, applied):\n"
                        "    if job_key in applied:\n"
                        "        return False\n"
                        "    time.sleep(0.01)\n"
                        "    applied.append(job_key)\n"
                        "    return True\n"
                    ),
                    "tests/test_submit.py": (
                        "from concurrent.futures import ThreadPoolExecutor\n"
                        "from app.submit import submit\n\n"
                        "def reproduce():\n"
                        "    applied = []\n"
                        "    with ThreadPoolExecutor(max_workers=2) as pool:\n"
                        "        list(pool.map(lambda _: submit('eval-code-reproduction-001', applied), range(2)))\n"
                        "    return applied\n"
                    ),
                }
            },
            "faults": {},
        },
    },
    {
        "id": "quantity_omitted_tier_shipping",
        "title": "Supplier quote reassessment from 50 to 100 with omitted tier/shipping",
        "weight": 0.15,
        "phases": ("cold", "continuation"),
        "concurrent_calls": 1,
        "correctness_check": "supplier_reassessment",
        "performance_metrics": ("latency_ms", "total_tokens", "cost_usd"),
        "expected": {
            "cold": {"quantity": 50, "totals": {"A": 530, "B": 500}, "unresolved": ["C shipping"]},
            "continuation": {
                "from_quantity": 50,
                "quantity": 100,
                "totals": {"A": 830, "B": 950},
                "unresolved": ["C shipping"],
            },
        },
        "request": {
            "prompt": (
                "Compare the supplied supplier quotes for 50 licensed widgets. "
                "The order omits discount-tier and shipping fields; extract what "
                "the sources actually support, preserve unresolved costs, and do "
                "not invent a universal tier or shipping value."
            ),
            "continuation_prompt": (
                "The required quantity changed from 50 to 100. Reassess the same "
                "supplier sources and arithmetic; do not reuse the 50-unit result "
                "as current evidence."
            ),
            "order": {
                "product": "licensed-widget",
                "quantity": 50,
                "discount_tier": None,
                "shipping": None,
            },
            "supplier_sources": [
                {
                    "supplier": "A",
                    "text": "EUR 10/unit; EUR 8/unit at 100 units; shipping EUR 30.",
                },
                {"supplier": "B", "text": "EUR 9/unit; shipping EUR 50."},
                {"supplier": "C", "text": "EUR 7/unit; shipping not stated."},
            ],
            "faults": {},
        },
    },
    {
        "id": "misleading_memory_explanation",
        "title": "Discovery of a misleading provisional memory explanation",
        "weight": 0.15,
        "phases": ("cold", "continuation"),
        "concurrent_calls": 1,
        "correctness_check": "memory_qualification",
        "performance_metrics": ("latency_ms", "total_tokens", "cost_usd"),
        "expected": {
            "memory_phrase": "APP_MODE",
            "current_evidence_phrase": "permission denied",
            "must_not_assert_memory_as_cause": True,
        },
        "request": {
            "prompt": (
                "Explain the deployment failure using current workspace evidence "
                "and an older useful but provisional explanation. Investigate before "
                "presenting a cause, and distinguish supported evidence from an "
                "unconfirmed prior note."
            ),
            "memory": [
                {
                    "id": "memory-001",
                    "text": "Prior run explanation: startup likely failed because APP_MODE was missing.",
                    "source": "prior_run",
                    "trust": "provisional",
                    "recorded_at": "2026-09-01T12:00:00Z",
                }
            ],
            "current_request": "Explain the deployment failure from current evidence.",
            "workspace": {
                "files": {
                    "deployment.log": "startup failed: permission denied opening /var/run/app.sock\n"
                }
            },
            "faults": {},
        },
    },
    {
        "id": "new_file_invalidating_negative_search",
        "title": "New file invalidates a negative search",
        "weight": 0.15,
        "phases": ("cold", "continuation"),
        "concurrent_calls": 1,
        "correctness_check": "fresh_negative_search",
        "performance_metrics": ("latency_ms", "total_tokens", "cost_usd"),
        "request": {
            "prompt": (
                "Search the workspace for the requested marker. A negative result "
                "from the cold phase must not survive a later workspace mutation; "
                "re-check before claiming that the marker is absent."
            ),
            "query": "EVAL_MARKER_42",
            "workspace": {
                "initial_files": {"README.txt": "ordinary documentation\n"},
                "continuation_mutation": {
                    "path": "new-target.txt",
                    "content": "EVAL_MARKER_42\n",
                    "workspace_version": 2,
                },
            },
            "faults": {},
        },
    },
    {
        "id": "tiny_tasks",
        "title": "Tiny exact task",
        "weight": 0.10,
        "phases": ("cold", "continuation"),
        "concurrent_calls": 1,
        "correctness_check": "tiny_exact_answer",
        "performance_metrics": ("latency_ms", "total_tokens", "cost_usd"),
        "expected_answer": "OK\n",
        "request": {
            "prompt": "Read the supplied file and return its content exactly, without a preamble.",
            "workspace": {"files": {"answer.txt": "OK\n"}},
            "faults": {},
        },
    },
    {
        "id": "effect_loss_correctness",
        "title": "Effect response loss and reconciliation",
        "weight": 0.25,
        "phases": ("cold", "continuation"),
        "concurrent_calls": 1,
        "correctness_check": "effect_loss_reconcile",
        "performance_metrics": ("latency_ms", "total_tokens", "cost_usd"),
        "request": {
            "prompt": (
                "Commit the exact supplied candidate to the mock destination. The "
                "first commit response is lost after the destination transaction; "
                "reconcile by operation identity and never issue a duplicate effect."
            ),
            "candidate": {"content": "approved-eval-candidate", "hash": "fixture-candidate-001"},
            "destination": {"target": "mock://eval/lost-response", "expected_version": 0},
            "faults": {"commit_response": "lose_once"},
        },
    },
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def registration_document() -> Dict[str, Any]:
    scenarios: List[Dict[str, Any]] = []
    for scenario in SCENARIOS:
        copied = dict(scenario)
        copied["phases"] = list(scenario["phases"])
        copied["performance_metrics"] = list(scenario["performance_metrics"])
        scenarios.append(copied)
    document: Dict[str, Any] = {
        "schema": REGISTRATION_SCHEMA,
        "registration_id": REGISTRATION_ID,
        "arms": [dict(arm) for arm in ARMS],
        "repetitions": REPETITIONS,
        "phase_weights": {"cold": 0.5, "continuation": 0.5},
        "scenarios": scenarios,
        "weights": {scenario["id"]: scenario["weight"] for scenario in SCENARIOS},
        "shared_execution": {
            "same_model_required": True,
            "same_tool_profile_required": True,
            "same_safety_profile_required": True,
            "provider_evidence_required": True,
            "scripted_or_fake_provider_forbidden": True,
            "unknown_usage_is_null": True,
            "unknown_usage_is_not_zero": True,
            "correctness_is_scored_separately": True,
            "full_run_requires_explicit_cost_bound": True,
        },
        "thresholds": {
            "correctness_pass_rate_min": 1.0,
            "complete_cell_fraction_min": 1.0,
            "required_arm_fraction_min": 1.0,
            "provider_evidence_fraction_min": 1.0,
            "usage_reporting_fraction_for_efficiency": 1.0,
            "tail_percentile": 0.95,
            "efficiency_margin": 0.10,
        },
    }
    document["registration_sha256"] = hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()
    return document


def registration_json() -> str:
    return _canonical(registration_document()) + "\n"


def scenario_by_id(scenario_id: str) -> Dict[str, Any]:
    for scenario in SCENARIOS:
        if scenario["id"] == scenario_id:
            return dict(scenario)
    raise KeyError("unknown scenario: %s" % scenario_id)
