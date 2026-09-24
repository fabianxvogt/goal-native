from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .evaluator import EvaluationError, analyze_run, normalize_usage, validate_response
from .workloads import registration_document


class EvaluatorSafeguardTests(unittest.TestCase):
    def test_unknown_usage_is_null_not_zero(self) -> None:
        usage = normalize_usage({"input_tokens": 8})
        self.assertIsNone(usage["output_tokens"])
        self.assertIsNone(usage["total_tokens"])


    def test_scripted_provider_is_rejected(self) -> None:
        request = {
            "model": "explicit-model",
            "tool_profile": "tools-v1",
            "safety_profile": "safety-v1",
            "phase": "cold",
        }
        response = {
            "protocol": "goal-native-worker-trace/v1",
            "trace_schema": "goal-native-worker-trace/v1",
            "provider": {"kind": "scripted", "request_id": "x", "model": "explicit-model"},
            "configuration": {"tool_profile": "tools-v1", "safety_profile": "safety-v1"},
            "lifecycle": {"phase": "cold", "continuation_ref": "c"},
            "status": "completed",
            "events": [],
            "observations": {},
        }
        with self.assertRaises(EvaluationError):
            validate_response(response, request)
    def test_bare_loop_cannot_enter_strong_baseline(self) -> None:
        request = {
            "arm_id": "strong_existing_harness",
            "model": "explicit-model",
            "tool_profile": "tools-v1",
            "safety_profile": "safety-v1",
            "phase": "cold",
        }
        response = {
            "protocol": "goal-native-worker-trace/v1",
            "trace_schema": "goal-native-worker-trace/v1",
            "provider": {
                "kind": "pi",
                "evidence": "provider-request",
                "request_id": "x",
                "model": "explicit-model",
            },
            "configuration": {"tool_profile": "tools-v1", "safety_profile": "safety-v1"},
            "lifecycle": {"phase": "cold", "continuation_ref": "c"},
            "status": "completed",
            "events": [],
            "observations": {},
        }
        with self.assertRaises(EvaluationError):
            validate_response(response, request)


    def test_incomplete_run_is_not_claimable(self) -> None:
        registration = registration_document()
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "preregistration.json").write_text(json.dumps(registration), encoding="utf-8")
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "model": "explicit-model",
                        "selected_scenarios": [registration["scenarios"][0]["id"]],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "results.jsonl").write_text(
                json.dumps(
                    {
                        "request": {
                            "arm_id": "goal_native",
                            "scenario_id": registration["scenarios"][0]["id"],
                            "repetition": 0,
                            "phase": "cold",
                            "call_index": 0,
                        },
                        "error": {"kind": "missing", "message": "provider unavailable"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = analyze_run(run_dir)
            self.assertFalse(report["claim_basis"]["claimable"])
            self.assertFalse(report["completeness"]["passed"])
            self.assertTrue(report["fairness"]["errors"])



if __name__ == "__main__":
    unittest.main()
