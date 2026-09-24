import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from goal_native import Store


class StoreBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(self.tempdir.name)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _invocation_with_artifact(self, goal_id: str, content: str = "candidate") -> tuple[dict, dict, dict]:
        assignment = self.store.assign(goal_id)
        invocation = self.store.invoke(goal_id, assignment["id"], {"goal": goal_id})
        artifact = self.store.artifact(
            goal_id,
            invocation["id"],
            "answer",
            content,
            limitations="requires current review",
        )
        evidence = self.store.verify(
            invocation["id"],
            artifact["id"],
            "controller review",
            True,
            {"reason": "boundary test", "observations": ["complete"]},
            trusted=True,
        )
        return invocation, artifact, evidence

    def test_goal_hierarchy_is_navigation_not_authority(self) -> None:
        parent = self.store.create_goal("parent outcome")
        child = self.store.create_goal("child outcome")
        self.store.link_goal(parent["id"], child["id"], "prerequisite")
        before = self.store.goal(child["id"])

        moved = self.store.move_goal(child["id"], parent["id"])

        self.assertEqual(moved["parent_id"], parent["id"])
        self.assertEqual(moved["revision"], before["revision"])
        self.assertEqual(moved["input_version"], before["input_version"])
        self.assertEqual(moved["authority_version"], before["authority_version"])
        self.assertEqual(self.store.links(parent["id"])[0]["relationship"], "prerequisite")

    def test_input_revision_assignment_and_artifact_applicability_fences(self) -> None:
        goal = self.store.create_goal(
            "deliver report",
            kind="outcome",
            criteria="all claims cited",
            constraints="local data only",
        )
        self.assertEqual(goal["original_request"]["constraints"], "local data only")
        assignment = self.store.assign(goal["id"])
        goal = self.store.request(goal["id"], "add the new requirement")
        with self.assertRaises(PermissionError):
            self.store.invoke(goal["id"], assignment["id"], "stale", expected_input_version=goal["input_version"])

        goal = self.store.revise(goal["id"], 1, "deliver revised report", "all claims cited", "local data only")
        self.assertEqual(goal["revision"], 2)
        with self.assertRaises(PermissionError):
            self.store.revise(goal["id"], 1, "wrong CAS", "", "")

        assignment = self.store.assign(goal["id"])
        invocation, artifact, _evidence = self._invocation_with_artifact(goal["id"], "versioned")
        self.assertEqual(artifact["revision"], invocation["revision"])
        self.assertEqual(artifact["input_version"], invocation["input_version"])
        self.assertEqual(artifact["applicability"]["authority_version"], invocation["authority_version"])
        self.assertNotEqual(assignment["id"], invocation["assignment_id"])

    def test_receipts_usage_and_exact_trusted_evidence(self) -> None:
        goal = self.store.create_goal("inspect input")
        invocation, artifact, evidence = self._invocation_with_artifact(goal["id"])
        receipt = self.store.receipt(
            invocation["id"],
            "read",
            {"path": "staged/input.txt"},
            {"exit_code": 1, "stderr": "missing", "stdout": ""},
            note="worker assertion, not policy",
        )
        self.assertEqual(receipt["result"]["stderr"], "missing")
        usage = self.store.usage(invocation["id"], {"input_tokens": 11, "output_tokens": 4, "cached_tokens": 2})
        self.assertEqual(usage["normalized"]["total_tokens"], 15)
        self.assertEqual(usage["normalized"]["reasoning_tokens"], None)
        finished = self.store.finish(invocation["id"], "finished", "complete")
        self.assertEqual(finished["result"], "complete")
        acceptance = self.store.accept(goal["id"], artifact["id"], evidence["id"], 1)
        self.assertEqual(acceptance["artifact_id"], artifact["id"])

        revised = self.store.revise(goal["id"], 1, "changed inspection", "same evidence", "")
        with self.assertRaises(PermissionError):
            self.store.accept(revised["id"], artifact["id"], evidence["id"], revised["revision"])

    def test_local_stage_recovery_cannot_be_rewound_by_a_replaced_assignment(self) -> None:
        goal = self.store.create_goal("Continue local files")
        first, _, _ = self._invocation_with_artifact(goal["id"])
        with self.assertRaises(PermissionError):
            self.store.remember_local_stage(first["assignment_id"], "run-first")
        self.store.finish(first["id"], "finished")
        self.assertTrue(self.store.remember_local_stage(first["assignment_id"], "run-first"))
        second, _, _ = self._invocation_with_artifact(goal["id"])
        self.store.finish(second["id"], "cancelled")
        self.store.request(goal["id"], "Stop for now", control="pause")
        self.assertTrue(self.store.remember_local_stage(second["assignment_id"], "run-second"))
        self.assertFalse(self.store.remember_local_stage(first["assignment_id"], "run-first"))
        self.assertEqual("run-second", self.store.local_stage(goal["id"]))
        for path in ("../run-outside", "/tmp/run-outside", "run-a/../../outside"):
            with self.assertRaises(ValueError):
                self.store.remember_local_stage(second["assignment_id"], path)
        self.assertEqual("run-second", self.store.local_stage(goal["id"]))

    def test_run_attempt_recovers_zero_invocation_and_rejects_stale_stage(self) -> None:
        goal = self.store.create_goal("Recover a stopped staged request")
        assignment = self.store.assign(goal["id"])
        attempt = self.store.start_run(
            goal["id"],
            assignment["id"],
            {"context_budget": 16_384, "max_rounds": 1},
        )
        self.store.finish_run(
            attempt["id"],
            "interrupted",
            stop_reason="context_budget",
            diagnostic={"kind": "context_budget", "message": "26400 estimated tokens > 16384"},
            admission={"total_tokens": 26400, "max_context_tokens": 16384},
        )
        self.assertTrue(self.store.remember_local_stage(assignment["id"], "run-stopped"))
        self.assertEqual("run-stopped", self.store.local_stage(goal["id"]))
        saved = self.store.goal(goal["id"])
        self.assertEqual([], saved["invocations"])
        self.assertEqual("context_budget", saved["runs"][0]["stop_reason"])
        self.assertEqual(26400, saved["runs"][0]["admission"]["total_tokens"])
        replacement = self.store.assign(goal["id"])
        self.assertFalse(self.store.remember_local_stage(assignment["id"], "run-old"))
        self.assertEqual("run-stopped", self.store.local_stage(goal["id"]))
        self.assertEqual("active", replacement["status"])

    def test_imported_receipts_and_records_cannot_select_local_files(self) -> None:
        goal = self.store.create_goal("Portable artifacts, not host file access")
        invocation, _, _ = self._invocation_with_artifact(goal["id"])
        self.store.finish(invocation["id"], "finished")
        self.store.remember_local_stage(invocation["assignment_id"], "run-local")
        self.store.receipt(invocation["id"], "controller.cli.run", {},
                           {"stage_dir": "/untrusted/host/path", "status": "finished"})
        exported = self.store.export()
        self.assertNotIn("local_stages", exported["records"])
        exported["records"]["local_stages"] = [{
            "goal_id": goal["id"], "invocation_id": invocation["id"], "stage_name": "run-injected",
        }]
        with Store.import_data(Path(self.tempdir.name) / "restored", exported) as restored:
            self.assertIsNone(restored.local_stage(goal["id"]))
            self.assertFalse(restored.goal(goal["id"])["effects_allowed"])

    def test_effect_gateway_authority_cas_response_loss_and_idempotency(self) -> None:
        goal = self.store.create_goal("publish local draft")
        goal = self.store.request(goal["id"], "human permits mock effects", control="allow_effects")
        invocation, artifact, evidence = self._invocation_with_artifact(goal["id"], "draft-v1")
        effect = self.store.prepare_effect(
            invocation["id"], artifact["id"], "mock/document", 0, evidence["id"]
        )
        approved = self.store.approve(effect["id"])
        self.store.request(goal["id"], "require a new input")
        with self.assertRaises(PermissionError):
            self.store.commit(approved["id"])
        self.assertEqual(self.store.destination("mock/document")["version"], 0)

        goal = self.store.goal(goal["id"])
        invocation, artifact, evidence = self._invocation_with_artifact(goal["id"], "draft-v2")
        effect = self.store.prepare_effect(
            invocation["id"], artifact["id"], "mock/document", 0, evidence["id"]
        )
        self.store.approve(effect["id"])
        unresolved = self.store.commit(effect["id"], lose_response=True)
        self.assertEqual(unresolved["state"], "unresolved")
        destination = self.store.destination("mock/document")
        self.assertEqual(destination["version"], 1)
        self.assertEqual(destination["operation_id"], unresolved["operation_id"])

        _invocation, artifact, evidence = self._invocation_with_artifact(goal["id"], "draft-v3")
        replacement = self.store.prepare_effect(
            _invocation["id"], artifact["id"], "mock/document", 1, evidence["id"]
        )
        self.store.approve(replacement["id"])
        committed = self.store.commit(replacement["id"])
        self.assertEqual(committed["state"], "committed")
        self.assertEqual(self.store.destination("mock/document")["version"], 2)
        self.assertEqual(self.store.reconcile(unresolved["id"])["state"], "committed")
        self.assertEqual(self.store.commit(unresolved["id"])["state"], "committed")
        self.assertEqual(len(self.store.export()["records"]["destination_history"]), 2)
    def test_sequential_destination_replacement_uses_version_cas(self) -> None:
        goal = self.store.create_goal("replace local draft")
        self.store.request(goal["id"], "human permits mock effects", control="allow_effects")

        invocation, artifact, evidence = self._invocation_with_artifact(goal["id"], "draft-v1")
        first = self.store.prepare_effect(
            invocation["id"], artifact["id"], "mock/sequential", 0, evidence["id"]
        )
        self.store.approve(first["id"])
        self.assertEqual(self.store.commit(first["id"])["state"], "committed")

        invocation, artifact, evidence = self._invocation_with_artifact(goal["id"], "draft-v2")
        second = self.store.prepare_effect(
            invocation["id"], artifact["id"], "mock/sequential", 1, evidence["id"]
        )
        self.assertEqual(self.store.approve(second["id"])["state"], "approved")
        self.assertEqual(self.store.commit(second["id"])["state"], "committed")
        destination = self.store.destination("mock/sequential")
        self.assertEqual(destination["version"], 2)
        self.assertEqual(destination["content"], "draft-v2")
        self.assertEqual(len(self.store.export()["records"]["destination_history"]), 2)

    def test_export_import_preserves_provenance_but_not_executable_authority(self) -> None:
        goal = self.store.create_goal("portable work")
        self.store.request(goal["id"], "allow local mock", control="allow_effects")
        invocation, artifact, evidence = self._invocation_with_artifact(goal["id"], "portable")
        effect = self.store.prepare_effect(
            invocation["id"], artifact["id"], "mock/portable", 0, evidence["id"]
        )
        self.store.approve(effect["id"])
        self.store.accept(goal["id"], artifact["id"], evidence["id"], 1)
        exported = self.store.export()
        imported_root = Path(self.tempdir.name).with_name("goal-native-import")
        try:
            imported = Store.import_data(imported_root, exported)
            imported_goal = imported.goal(goal["id"])
            self.assertFalse(imported_goal["effects_allowed"])
            self.assertEqual(imported_goal["artifacts"][1]["content"], "portable")
            imported_effect = imported.effects(goal["id"])[0]
            self.assertEqual(len(imported_goal["acceptances"]), 1)
            self.assertEqual(imported_effect["state"], "imported")
            with self.assertRaises(PermissionError):
                imported.commit(imported_effect["id"])
            imported.close()
        finally:
            if imported_root.exists():
                shutil.rmtree(imported_root)
    def test_import_rejects_tampered_chain_and_unverifies_delivery_claims(self) -> None:
        goal = self.store.create_goal("verify imported facts")
        self.store.request(goal["id"], "human permits mock effects", control="allow_effects")
        invocation, artifact, evidence = self._invocation_with_artifact(goal["id"], "portable")
        effect = self.store.prepare_effect(
            invocation["id"], artifact["id"], "mock/import", 0, evidence["id"]
        )
        self.store.approve(effect["id"])
        approved_export = self.store.export()

        claimed = copy.deepcopy(approved_export)
        claimed["records"]["effects"][0]["state"] = "committed"
        claimed_root = Path(self.tempdir.name).with_name("goal-native-claimed-import")
        imported = None
        try:
            imported = Store.import_data(claimed_root, claimed)
            imported_effect = imported.effects(goal["id"])[0]
            self.assertEqual(imported_effect["state"], "imported")
            self.assertEqual(imported_effect["gateway_state"], "imported")
            self.assertEqual(imported_effect["delivery_verification"], "unverified")
        finally:
            if imported is not None:
                imported.close()
            if claimed_root.exists():
                shutil.rmtree(claimed_root)

        tampered = copy.deepcopy(approved_export)
        context_id = next(
            item["id"] for item in tampered["records"]["artifacts"] if item["kind"] == "context"
        )
        tampered["records"]["evidence"][0]["artifact_id"] = context_id
        tampered_root = Path(self.tempdir.name).with_name("goal-native-tampered-import")
        try:
            with self.assertRaises(ValueError):
                Store.import_data(tampered_root, tampered)
        finally:
            if tampered_root.exists():
                shutil.rmtree(tampered_root)


if __name__ == "__main__":
    unittest.main()
