from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from evaluation.coding import (
    CodingPilotError,
    _hash_tree,
    _validate_checker_result,
    _validate_native_result,
    analyze_records,
    normalize_usage,
)
from evaluation.coding_tasks import registration_document, validate_registration


class CodingRegistrationTests(unittest.TestCase):
    def test_registration_mutation_is_rejected(self) -> None:
        document = copy.deepcopy(registration_document())
        document["tasks"][0]["prompt"] = "changed after preregistration"
        with self.assertRaises(ValueError):
            validate_registration(document)


class CodingIdentityTests(unittest.TestCase):
    def test_snapshot_hash_changes_and_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            root.mkdir()
            file_path = root / "module.py"
            file_path.write_text("answer = 1\n", encoding="utf-8")
            first = _hash_tree(root)
            file_path.write_text("answer = 2\n", encoding="utf-8")
            self.assertNotEqual(first, _hash_tree(root))
            link = root / "link"
            link.symlink_to(file_path)
            with self.assertRaises(CodingPilotError):
                _hash_tree(root)

    def test_unknown_usage_is_preserved_as_unknown(self) -> None:
        self.assertEqual(
            normalize_usage({"input_tokens": 2}),
            {
                "input_tokens": 2,
                "output_tokens": None,
                "cached_input_tokens": None,
                "total_tokens": None,
                "cost_usd": None,
            },
        )
        self.assertTrue(all(value is None for value in normalize_usage(None).values()))


class CodingEvidenceTests(unittest.TestCase):
    def test_partial_matrix_is_not_claimable(self) -> None:
        report = analyze_records([], registration=registration_document())
        self.assertFalse(report["claim_basis"]["claimable"])
        self.assertFalse(report["claim_basis"]["comparison_claimable"])
        self.assertTrue(report["errors"])

    def test_native_result_keeps_null_cost_and_requires_actual_provider_id_only_for_completion(self) -> None:
        task = copy.deepcopy(registration_document()["tasks"][0])
        task["snapshot_sha256"] = "a" * 64
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        trace = Path(temporary.name) / "native.jsonl"
        trace.write_bytes(b'{"type":"agent_start"}\n')
        execution = {
            "model": "test-model", "tools_sha256": "d" * 64, "max_rounds": 12,
            "max_time_seconds": 180, "trace_path": trace,
            "provider_request_ids": [], "usage": normalize_usage(None),
        }
        result = {
            "protocol": "goal-native-pi-session-result/v1",
            "status": "failed",
            "stop_reason": "provider_error",
            "provider": {
                "kind": "pi-coding-agent-sdk",
                "native_session": True,
                "request_id": None,
                "model": "test-model",
            },
            "identity": {
                "registration_sha256": registration_document()["registration_sha256"],
                "task_snapshot_sha256": task["snapshot_sha256"],
                "candidate_identity": "candidate",
                "check_identity": "checker",
                "environment_identity": "environment",
                "environment_snapshot_sha256": "c" * 64,
            },
            "lifecycle": {"phase": "cold", "continuation_ref": "pi-session-v1:session"},
            "raw_trace": {"path": str(trace), "sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                          "bytes": trace.stat().st_size, "event_count": 1},
            "usage": normalize_usage(None),
            "configuration": {
                "provider": "openai-codex", "tool_profile": "controlled-docker-coding",
                "safety_profile": "offline-isolated-candidate", "extensions": "none",
                "tool_schema_sha256": "d" * 64, "max_rounds": 12, "max_time_seconds": 179,
            },
        }
        observation, error = _validate_native_result(
            result,
            task=task,
            phase="cold",
            registration_hash=registration_document()["registration_sha256"],
            candidate_identity="candidate",
            check_identity="checker",
            environment_identity="environment",
            environment_snapshot_sha256="c" * 64,
            continuation_ref=None,
            execution=execution,
        )
        self.assertIsNone(error)
        self.assertIsNone(observation["usage"]["cost_usd"])
        self.assertEqual(observation["status"], "failed")
        completed_observation, completed_error = _validate_native_result(
            {**result, "status": "completed"},
            task=task,
            phase="cold",
            registration_hash=registration_document()["registration_sha256"],
            candidate_identity="candidate",
            check_identity="checker",
            environment_identity="environment",
            environment_snapshot_sha256="c" * 64,
            continuation_ref=None,
            execution=execution,
        )
        self.assertIsNotNone(completed_error)
        self.assertEqual(completed_observation["status"], "invalid")
        for tampering in ("trace", "model", "usage"):
            with self.subTest(tampering=tampering):
                altered = copy.deepcopy(result)
                if tampering == "trace":
                    altered["raw_trace"]["sha256"] = "z" * 64
                elif tampering == "model":
                    altered["provider"]["model"] = "different-model"
                else:
                    altered["usage"]["input_tokens"] = 1
                audited, audit_error = _validate_native_result(
                    altered, task=task, phase="cold",
                    registration_hash=registration_document()["registration_sha256"],
                    candidate_identity="candidate", check_identity="checker", environment_identity="environment",
                    environment_snapshot_sha256="c" * 64, continuation_ref=None, execution=execution,
                )
                self.assertEqual("invalid", audited["status"])
                self.assertIsNotNone(audit_error)
        trace.write_bytes(b'{"type":"tampered"}\n')
        audited, audit_error = _validate_native_result(
            result, task=task, phase="cold", registration_hash=registration_document()["registration_sha256"],
            candidate_identity="candidate", check_identity="checker", environment_identity="environment",
            environment_snapshot_sha256="c" * 64, continuation_ref=None, execution=execution,
        )
        self.assertEqual("invalid", audited["status"])
        self.assertIsNotNone(audit_error)

    def test_checker_identity_mismatch_is_non_claimable_evidence(self) -> None:
        observation, error = _validate_checker_result(
            {
                "protocol": "goal-native-independent-checker/v1",
                "status": "passed",
                "candidate_sha256": "wrong",
                "checker_sha256": "checker",
                "environment_identity": "environment",
            },
            candidate_hash="candidate",
            checker_hash="checker",
            environment_identity="environment",
        )
        self.assertIsNotNone(error)
        self.assertEqual(observation["status"], "invalid")

    def test_checker_cannot_pass_without_complete_behavioral_observations(self) -> None:
        identity = {
            "protocol": "goal-native-independent-checker/v1", "status": "passed",
            "candidate_sha256": "candidate", "checker_sha256": "checker",
            "environment_identity": "environment",
        }
        for checks in (None, [], [None], [{"name": "behavior", "passed": False}]):
            with self.subTest(checks=checks):
                result, error = _validate_checker_result(
                    {**identity, "observations": {"checks": checks}},
                    candidate_hash="candidate", checker_hash="checker", environment_identity="environment",
                )
                self.assertEqual("invalid", result["status"])
                self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
