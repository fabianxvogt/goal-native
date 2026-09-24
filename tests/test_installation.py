from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.py"
LAUNCHER = ROOT / "goal"


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class InstallationBoundaryTests(unittest.TestCase):
    def test_launcher_keeps_caller_cwd_and_forwards_arguments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-launcher-") as temporary:
            root = Path(temporary) / "checkout"
            entrypoint = root / ".venv" / "bin" / "goal-native"
            caller = Path(temporary) / "caller"
            entrypoint.parent.mkdir(parents=True)
            caller.mkdir()
            shutil.copy2(LAUNCHER, root / "goal")
            (root / "goal").chmod((root / "goal").stat().st_mode | stat.S_IXUSR)
            _executable(
                entrypoint,
                "#!/bin/sh\nprintf 'cwd=%s\\n' \"$PWD\"\nprintf 'args=%s\\n' \"$*\"\n",
            )

            result = subprocess.run(
                [str(root / "goal"), "--state", "relative-state", "chat"],
                cwd=caller,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode)
            self.assertEqual(f"cwd={caller}", result.stdout.splitlines()[0])
            self.assertEqual("args=--state relative-state chat", result.stdout.splitlines()[1])

    def test_bootstrap_reports_missing_node_without_touching_checkout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-bootstrap-prerequisite-") as temporary:
            root = Path(temporary) / "checkout"
            fake_bin = Path(temporary) / "bin"
            root_scripts = root / "scripts"
            fake_bin.mkdir()
            root_scripts.mkdir(parents=True)
            shutil.copy2(BOOTSTRAP, root_scripts / "bootstrap.py")
            _executable(fake_bin / "git", "#!/bin/sh\nexit 0\n")

            result = subprocess.run(
                [sys.executable, str(root_scripts / "bootstrap.py")],
                cwd=Path(temporary),
                env={"PATH": str(fake_bin)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(1, result.returncode)
            self.assertIn("node is required but was not found", result.stderr)
            self.assertIn("Node.js 22.19+", result.stderr)
            self.assertFalse((root / ".venv").exists())

    def test_bootstrap_does_not_replace_an_incomplete_existing_venv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-bootstrap-preserve-") as temporary:
            root = Path(temporary) / "checkout"
            fake_bin = Path(temporary) / "bin"
            root_scripts = root / "scripts"
            submodule = root / "upstream" / "pi"
            fake_bin.mkdir()
            root_scripts.mkdir(parents=True)
            submodule.mkdir(parents=True)
            shutil.copy2(BOOTSTRAP, root_scripts / "bootstrap.py")
            (submodule / "package.json").write_text("{}\n", encoding="utf-8")
            (submodule / ".git").write_text("gitdir: unused\n", encoding="utf-8")
            _executable(
                fake_bin / "git",
                f"#!/bin/sh\ncase \"$*\" in\n  *rev-parse*) printf '%s\\n' {shlex_quote(str(root))} ;;\nesac\n",
            )
            _executable(fake_bin / "node", "#!/bin/sh\nprintf 'v22.19.0\\n'\n")
            _executable(fake_bin / "npm", "#!/bin/sh\nexit 0\n")
            venv = root / ".venv"
            venv.mkdir()
            marker = venv / "preserve-me"
            marker.write_text("user installation state\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(root_scripts / "bootstrap.py")],
                cwd=Path(temporary),
                env={"PATH": str(fake_bin)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(1, result.returncode)
            self.assertIn("was not replaced", result.stderr)
            self.assertEqual("user installation state\n", marker.read_text(encoding="utf-8"))

    def test_doctor_separates_runtime_failure_from_credentials_without_secret_output(self) -> None:
        from goal_native import cli as cli_module

        args = Namespace(
            provider="openai",
            api_key_env="INSTALL_BOUNDARY_SECRET",
            model="fixture-model",
        )
        with patch.dict(os.environ, {"INSTALL_BOUNDARY_SECRET": "do-not-print-this"}), patch.object(
            cli_module.shutil, "which", return_value=None
        ), patch.object(cli_module.sys, "platform", "linux"):
            result = cli_module._doctor(args)

        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["runtime"]["ok"])
        self.assertTrue(result["checks"]["credentials"]["credential_present"])
        self.assertNotIn("do-not-print-this", json.dumps(result))
        self.assertIn("install Git", " ".join(result["checks"]["runtime"]["remediation"]))


def shlex_quote(value: str) -> str:
    """Quote a temporary path for the tiny test-only POSIX command."""
    return "'" + value.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    unittest.main()
