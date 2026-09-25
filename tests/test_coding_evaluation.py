from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading
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
from evaluation.native import NativeSessionBridge
from goal_native.worker import BridgeError, BridgeRPCError


_NATIVE_BRIDGE_READY = bool(shutil.which("node")) and (
    Path(__file__).resolve().parents[1] / "upstream/pi/packages/coding-agent/dist/index.js"
).is_file()


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


class NativePathTests(unittest.TestCase):
    def _metadata(self, root: Path, phase: str) -> dict[str, object]:
        root = root.resolve()
        candidate = root / "candidate"
        sessions = root / "sessions"
        candidate.mkdir()
        sessions.mkdir()
        auth = root / "auth.json"
        auth.write_text(json.dumps({
            "openai-codex": {
                "type": "oauth",
                "access": "ACCESS_FIXTURE",
                "refresh": "REFRESH_FIXTURE",
                "expires": 4102444800,
            },
        }), encoding="utf-8")
        return {
            "phase": phase,
            "provider": "openai-codex",
            "model": "gpt-6-luna",
            "tool_profile": "controlled-docker-coding",
            "safety_profile": "offline-isolated-candidate",
            "cwd": str(candidate),
            "state_dir": str(root / "state"),
            "session_dir": str(sessions),
            "auth_file": str(auth),
            "raw_trace_path": str(root / "traces" / "native.jsonl"),
            "session_key": "task",
            "task_id": "task",
            "registration_sha256": "a" * 64,
            "task_snapshot_sha256": "snapshot",
            "candidate_identity": "candidate",
            "check_identity": "checker",
            "environment_identity": "environment",
            "environment_snapshot_sha256": "b" * 64,
            "continuation_ref": None,
            "tools": [{
                "name": "staged_read",
                "description": "Read a staged file",
                "parameters": {"type": "object"},
            }],
            "available_tools": ["staged_read"],
            "request_id": "native-path-request",
            "command_runtime": False,
        }

    def _run_rejected(self, metadata: dict[str, object]) -> None:
        bridge = NativeSessionBridge(metadata=metadata, timeout_seconds=15)
        bridge.run(
            {"type": "run", "request_id": metadata["request_id"],
             "run_id": metadata["request_id"],
             "messages": [{"content": "continue"}], "timeout_ms": 12000},
            rpc_handler=lambda _method, _payload: {},
            event_handler=lambda _event: None,
            cancel_event=threading.Event(),
        )

    @unittest.skipUnless(_NATIVE_BRIDGE_READY, "built native Pi bridge is unavailable")
    def test_cwd_symlink_alias_is_rejected_before_state_or_trace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = self._metadata(root, "cold")
            alias = root / "cwd-alias"
            alias.symlink_to(Path(__file__).resolve().parents[1], target_is_directory=True)
            metadata["cwd"] = str(alias)
            with self.assertRaisesRegex(BridgeRPCError, "cwd must be a real directory"):
                self._run_rejected(metadata)
            self.assertFalse(Path(metadata["state_dir"]).exists())
            metadata["cwd"] = os.path.relpath(
                (root / "candidate").resolve(), Path(__file__).resolve().parents[1]
            )
            with self.assertRaisesRegex(BridgeRPCError, "cwd must be absolute"):
                self._run_rejected(metadata)
            self.assertFalse(Path(metadata["state_dir"]).exists())

    @unittest.skipUnless(_NATIVE_BRIDGE_READY, "built native Pi bridge is unavailable")
    def test_state_and_trace_symlink_aliases_are_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Path(__file__).resolve().parents[1]
            metadata = self._metadata(root, "cold")
            state_alias = root / "state-alias"
            state_alias.symlink_to(repository, target_is_directory=True)
            metadata["state_dir"] = str(state_alias)
            with self.assertRaisesRegex(BridgeRPCError, "state_dir must be outside"):
                self._run_rejected(metadata)
            self.assertFalse(Path(metadata["raw_trace_path"]).exists())

            second = root / "second"
            second.mkdir()
            metadata = self._metadata(second, "cold")
            trace_alias = second / "trace-alias"
            trace_alias.symlink_to(repository / "README.md")
            metadata["raw_trace_path"] = str(trace_alias)
            with self.assertRaisesRegex(BridgeRPCError, "raw_trace_path must not be a symlink"):
                self._run_rejected(metadata)
            self.assertFalse(Path(metadata["state_dir"]).exists())

    @unittest.skipUnless(_NATIVE_BRIDGE_READY, "built native Pi bridge is unavailable")
    def test_forged_manifest_session_path_is_rejected_before_open_or_trace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = self._metadata(root, "continuation")
            metadata["continuation_ref"] = "pi-session-v1:forged"
            escaped = (root / "escaped-session.json").resolve()
            escaped.write_text('{"id":"forged"}\n', encoding="utf-8")
            manifest = {
                "continuation_ref": metadata["continuation_ref"],
                "session_id": "forged",
                "session_file": str(escaped),
                "task_id": metadata["task_id"],
                "registration_sha256": metadata["registration_sha256"],
                "task_snapshot_sha256": metadata["task_snapshot_sha256"],
                "candidate_identity": metadata["candidate_identity"],
                "check_identity": metadata["check_identity"],
                "environment_identity": metadata["environment_identity"],
                "environment_snapshot_sha256": metadata["environment_snapshot_sha256"],
            }
            manifest_path = Path(metadata["session_dir"]) / "task.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(BridgeRPCError, "within the authorized session_dir"):
                self._run_rejected(metadata)
            self.assertFalse(Path(metadata["state_dir"]).exists())
            self.assertFalse(Path(metadata["raw_trace_path"]).exists())
            self.assertEqual('{"id":"forged"}\n', escaped.read_text(encoding="utf-8"))
            linked = Path(metadata["session_dir"]) / "linked-session.json"
            linked.symlink_to(escaped)
            manifest["session_file"] = str(linked)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(BridgeRPCError, "must not contain symlink components"):
                self._run_rejected(metadata)
            self.assertFalse(Path(metadata["state_dir"]).exists())
            self.assertFalse(Path(metadata["raw_trace_path"]).exists())

    @unittest.skipUnless(_NATIVE_BRIDGE_READY, "built native Pi bridge is unavailable")
    def test_python_continuation_rejects_session_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = self._metadata(root, "continuation")
            session_dir = Path(metadata["session_dir"])
            session_file = session_dir / "session.json"
            session_file.write_text('{"id":"session"}\n', encoding="utf-8")
            metadata["continuation_ref"] = "pi-session-v1:session"
            manifest_path = session_dir / "task.json"
            manifest = {
                "continuation_ref": metadata["continuation_ref"],
                "session_id": "session",
                "session_file": str(session_file),
                "task_id": metadata["task_id"],
                "registration_sha256": metadata["registration_sha256"],
                "task_snapshot_sha256": metadata["task_snapshot_sha256"],
                "candidate_identity": metadata["candidate_identity"],
                "check_identity": metadata["check_identity"],
                "environment_identity": metadata["environment_identity"],
                "environment_snapshot_sha256": metadata["environment_snapshot_sha256"],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            bridge = NativeSessionBridge(metadata=metadata, timeout_seconds=5)
            self.assertEqual(metadata["continuation_ref"], bridge.continuation_reference())
            outside_manifest = session_dir.parent / "outside.json"
            outside_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            bridge.metadata["session_key"] = "../outside"
            with self.assertRaisesRegex(BridgeError, "session_key must be an explicit safe identifier"):
                bridge.continuation_reference()
            bridge.metadata["session_key"] = "task"
            escaped = root / "escaped-session.json"
            escaped.write_text('{"id":"session"}\n', encoding="utf-8")
            session_file.unlink()
            session_file.symlink_to(escaped)
            manifest["session_file"] = str(session_file)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIsNone(bridge.continuation_reference())

    @unittest.skipUnless(_NATIVE_BRIDGE_READY, "built native Pi bridge is unavailable")
    def test_cold_session_file_is_created_and_reopened_for_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = self._metadata(Path(directory), "cold")
            calls: list[str] = []

            def deny_provider(method: str, _payload: dict[str, object]) -> dict[str, object]:
                calls.append(method)
                raise PermissionError("fixture rejects admission before provider transport")

            def run(phase: dict[str, object]) -> NativeSessionBridge:
                bridge = NativeSessionBridge(metadata=phase, timeout_seconds=15)
                result = bridge.run(
                    {"type": "run", "request_id": phase["request_id"],
                     "run_id": phase["request_id"], "messages": [{"content": "probe"}],
                     "timeout_ms": 12000},
                    rpc_handler=deny_provider, event_handler=lambda _event: None,
                    cancel_event=threading.Event(),
                )
                self.assertEqual("failed", result["status"])
                return bridge

            cold = run(metadata)
            manifest = json.loads((Path(metadata["session_dir"]) / "task.json").read_text(encoding="utf-8"))
            self.assertTrue(Path(manifest["session_file"]).is_file())
            reference = cold.continuation_reference()
            self.assertEqual(manifest["continuation_ref"], reference)
            continuation = {**metadata, "phase": "continuation",
                            "continuation_ref": reference, "request_id": "native-continuation",
                            "raw_trace_path": str(Path(metadata["state_dir"]).parent / "continued-trace.jsonl")}
            reopened = run(continuation)
            self.assertEqual(reference, reopened.continuation_reference())
            self.assertEqual(["prepare_request", "prepare_request"], calls)


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
