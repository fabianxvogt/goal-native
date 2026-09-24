from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from goal_native.store import Store
from goal_native import workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="goal-native-workspace-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.store = Store(self.root / "state")
        self.addCleanup(self.store.close)
        self.goal = self.store.create_goal("Improve the selected project")
        self.stage = self.store.root / "stages" / self.goal["id"] / "run-first"
        self.stage.mkdir(parents=True)

    def stage_source(self) -> None:
        selection = workspace.copy_selected_directory(str(self.source), self.stage)
        files, _ = workspace.snapshot_directory(self.stage, strict=True)
        selection["source_root"] = str(self.source)
        self.store.remember_workspace_baseline(self.goal["id"], self.stage.name, files, selection)

    def review(self):
        return workspace.review_workspace(self.store, self.goal["id"], self.stage)[0]

    def test_source_selection_honors_nested_ignore_and_names_without_reading_excluded_content(self) -> None:
        (self.source / ".gitignore").write_text("*.log\n!keep.log\n")
        (self.source / "skip.log").write_text("excluded")
        (self.source / "keep.log").write_text("selected")
        nested = self.source / "lib"
        nested.mkdir()
        (nested / ".gitignore").write_text("generated.py\n")
        (nested / "generated.py").write_text("excluded")
        (nested / "main.py").write_text("print(7)\n")
        (self.source / "node_modules").mkdir()
        (self.source / "node_modules" / "large.js").write_text("excluded")
        (self.source / ".env").write_text("EXCLUDED_SYNTHETIC_VALUE")
        (self.source / "linked.py").symlink_to(nested / "main.py")
        selection = workspace.copy_selected_directory(str(self.source), self.stage)
        self.assertEqual({".gitignore", "keep.log", "lib/.gitignore", "lib/main.py"}, set(selection["selected"]))
        omitted = {item["path"]: item["reason"] for item in selection["exclusions"]}
        self.assertEqual(".gitignore", omitted["skip.log"])
        self.assertEqual(".gitignore", omitted["lib/generated.py"])
        self.assertEqual("symlink", omitted["linked.py"])
        self.assertFalse((self.stage / ".env").exists())
        self.assertFalse((self.stage / "node_modules").exists())
        self.assertEqual("print(7)\n", (self.stage / "lib/main.py").read_text())

    def test_patch_roundtrip_covers_add_modify_delete_modes_and_missing_newline(self) -> None:
        (self.source / "change.py").write_text("value = 1\n")
        (self.source / "remove.txt").write_text("obsolete")
        (self.source / "unchanged.txt").write_text("keep\n")
        self.stage_source()
        (self.stage / "change.py").write_text("value = 2\n")
        (self.stage / "change.py").chmod(0o700)
        (self.stage / "remove.txt").unlink()
        (self.stage / "new file.txt").write_text("new without newline")
        (self.stage / "empty.txt").write_bytes(b"")
        report = self.review()
        self.assertEqual("matching", report["source_status"]["state"])
        output = self.root / "changes.patch"
        workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], output)
        target = self.root / "target"
        shutil.copytree(self.source, target)
        result = subprocess.run(["git", "apply", "--check", str(output)], cwd=target, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result = subprocess.run(["git", "apply", str(output)], cwd=target, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("value = 2\n", (target / "change.py").read_text())
        self.assertTrue((target / "change.py").stat().st_mode & 0o111)
        self.assertFalse((target / "remove.txt").exists())
        self.assertEqual("new without newline", (target / "new file.txt").read_text())
        self.assertEqual(b"", (target / "empty.txt").read_bytes())
        self.assertEqual("keep\n", (target / "unchanged.txt").read_text())
        self.assertEqual("value = 1\n", (self.source / "change.py").read_text())

    def test_review_rejects_changed_candidate_contract_source_and_output_overwrite(self) -> None:
        (self.source / "main.py").write_text("print(1)\n")
        self.stage_source()
        (self.stage / "main.py").write_text("print(2)\n")
        report = self.review()
        (self.stage / "main.py").write_text("print(3)\n")
        output = self.root / "changes.patch"
        with self.assertRaisesRegex(ValueError, "changed"):
            workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], output)
        self.assertFalse(output.exists())
        report = self.review()
        self.store.request(self.goal["id"], "Also preserve CLI behavior", control="draft")
        with self.assertRaisesRegex(ValueError, "changed"):
            workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], output)
        report = self.review()
        (self.source / "main.py").write_text("human concurrent edit\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], output)
        report = self.review()
        self.assertEqual({"state": "changed", "changed_paths": ["main.py"]}, report["source_status"])
        output.write_text("existing user file")
        with self.assertRaises(FileExistsError):
            workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], output)
        self.assertEqual("existing user file", output.read_text())
        with self.assertRaisesRegex(ValueError, "outside"):
            workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], self.source / "changes.patch")

    def test_ignored_inputs_never_become_deletions_and_new_ignores_do_not_hide_baseline_files(self) -> None:
        (self.source / ".gitignore").write_text("ignored.txt\n")
        (self.source / "ignored.txt").write_text("unselected")
        (self.source / "main.py").write_text("print(1)\n")
        self.stage_source()
        (self.stage / ".gitignore").write_text("ignored.txt\nmain.py\n")
        (self.stage / "main.py").write_text("print(2)\n")
        (self.stage / "ignored.txt").write_text("not a selected change")
        report = self.review()
        self.assertEqual({".gitignore", "main.py"}, {item["path"] for item in report["changes"]})
        self.assertEqual("modified", next(c for c in report["changes"] if c["path"] == "main.py")["status"])

    def test_binary_archive_is_exact_and_contains_no_host_recovery_metadata(self) -> None:
        (self.source / "image.bin").write_bytes(b"\x00\x01")
        self.stage_source()
        (self.stage / "image.bin").write_bytes(b"\x00\x02")
        report = self.review()
        self.assertFalse(report["patch_supported"])
        patch_path = self.root / "unsupported.patch"
        with self.assertRaisesRegex(ValueError, "Binary"):
            workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], patch_path)
        self.assertFalse(patch_path.exists())
        archive_path = self.root / "files.zip"
        workspace.export_reviewed(self.store, self.goal["id"], self.stage, report["review_id"], archive_path, "files")
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual({"manifest.json", "files/image.bin"}, set(archive.namelist()))
            self.assertEqual(b"\x00\x02", archive.read("files/image.bin"))
            manifest = json.loads(archive.read("manifest.json"))
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertEqual(report["candidate_hash"], manifest["candidate_hash"])

    def test_local_baseline_is_immutable_not_imported_and_unsafe_candidate_fails_closed(self) -> None:
        (self.source / "main.py").write_text("print(1)\n")
        self.stage_source()
        original = self.store.workspace_baseline(self.goal["id"], self.stage.name)
        with self.assertRaises(ValueError):
            self.store.remember_workspace_baseline(self.goal["id"], self.stage.name, {}, {})
        self.assertEqual(original, self.store.workspace_baseline(self.goal["id"], self.stage.name))
        imported = Store.import_data(self.root / "imported", self.store.export())
        try:
            self.assertIsNone(imported.workspace_baseline(self.goal["id"], self.stage.name))
        finally:
            imported.close()
        (self.stage / "main.py").unlink()
        (self.stage / "main.py").symlink_to(self.source / "main.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.review()

    def test_staging_rechecks_opened_sizes_and_records_actual_copied_bytes(self) -> None:
        growing, shrinking = self.source / "growing.txt", self.source / "shrinking.txt"
        growing.write_bytes(b"safe")
        shrinking.write_bytes(b"safe")
        real_open = os.open
        def mutate(path, flags, mode=0o777, *, dir_fd=None):
            if path == "growing.txt":
                growing.write_bytes(b"grown")
            elif path == "shrinking.txt":
                shrinking.write_bytes(b"ok")
            return real_open(path, flags, mode, dir_fd=dir_fd)
        with patch.object(workspace, "MAX_FILE_BYTES", 4), patch.object(workspace.os, "open", side_effect=mutate):
            selection = workspace.copy_selected_directory(str(self.source), self.stage)
        self.assertEqual(["shrinking.txt"], selection["selected"])
        self.assertEqual(2, selection["bytes"])
        self.assertEqual(b"ok", (self.stage / "shrinking.txt").read_bytes())
        self.assertFalse((self.stage / "growing.txt").exists())
