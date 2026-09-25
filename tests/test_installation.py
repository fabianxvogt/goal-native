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
    def test_launcher_creates_real_state_relative_to_caller(self) -> None:
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
                f"#!/bin/sh\nexec {shlex_quote(sys.executable)} -m goal_native \"$@\"\n",
            )

            result = subprocess.run(
                [str(root / "goal"), "--state", "relative-state", "create", "Persist in caller directory"],
                cwd=caller,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((caller / "relative-state").is_dir())
            self.assertFalse((root / "relative-state").exists())
            from goal_native import Store
            with Store(caller / "relative-state") as store:
                self.assertEqual("Persist in caller directory", store.export()["records"]["goals"][0]["outcome"])
    def test_install_command_is_executable_idempotent_and_caller_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-command-") as temporary:
            checkout = Path(temporary) / "checkout"
            bin_dir = Path(temporary) / "bin"
            caller = Path(temporary) / "caller"
            entrypoint = checkout / ".venv" / "bin" / "goal-native"
            entrypoint.parent.mkdir(parents=True)
            caller.mkdir()
            shutil.copy2(LAUNCHER, checkout / "goal")
            (checkout / "goal").chmod((checkout / "goal").stat().st_mode | stat.S_IXUSR)
            _executable(
                entrypoint,
                f"#!/bin/sh\nexec {shlex_quote(sys.executable)} -m goal_native \"$@\"\n",
            )

            from scripts import bootstrap

            with patch.object(bootstrap, "ROOT", checkout):
                command = bootstrap._install_command(bin_dir)
                first_content = command.read_bytes()
                first_mode = stat.S_IMODE(command.stat().st_mode)
                self.assertEqual(command, bootstrap._install_command(bin_dir))
                self.assertEqual(first_content, command.read_bytes())
                self.assertEqual(first_mode, stat.S_IMODE(command.stat().st_mode))

            result = subprocess.run(
                [str(command), "--state", "relative-state", "create", "Persist through installed command"],
                cwd=caller,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((caller / "relative-state").is_dir())
            self.assertFalse((checkout / "relative-state").exists())
            self.assertIn(str(checkout / "goal"), first_content.decode("utf-8"))
            self.assertTrue(first_mode & stat.S_IXUSR)

    def test_install_command_rejects_conflicts_and_symlinks_untouched(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-command-conflict-") as temporary:
            checkout = Path(temporary) / "checkout"
            bin_dir = Path(temporary) / "bin"
            checkout.mkdir()
            bin_dir.mkdir()
            conflict = bin_dir / "goal"
            conflict.write_bytes(b"user command\n")
            conflict.chmod(0o640)
            before = (conflict.read_bytes(), stat.S_IMODE(conflict.stat().st_mode))

            from scripts import bootstrap

            with patch.object(bootstrap, "ROOT", checkout):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._install_command(bin_dir)
            self.assertEqual(before[0], conflict.read_bytes())
            self.assertEqual(before[1], stat.S_IMODE(conflict.stat().st_mode))

            conflict.unlink()
            target = Path(temporary) / "target"
            target.write_bytes(b"preserve me\n")
            conflict.symlink_to(target)
            with patch.object(bootstrap, "ROOT", checkout):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._install_command(bin_dir)
            self.assertTrue(conflict.is_symlink())
            self.assertEqual(b"preserve me\n", target.read_bytes())


    def test_failed_atomic_install_does_not_leave_command_or_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-command-write-failure-") as temporary:
            from scripts import bootstrap
            bin_dir = Path(temporary) / "bin"
            with patch.object(bootstrap.os, "link", side_effect=OSError("link unavailable")):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._install_command(bin_dir)
            self.assertEqual([], list(bin_dir.iterdir()))

    def test_bootstrap_provisions_missing_pip_inside_validated_venv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="goal-native-offline-pip-") as temporary:
            from scripts import bootstrap
            venv = Path(temporary) / ".venv"
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                           check=True, capture_output=True, text=True, timeout=30)
            python = venv / "bin" / "python"
            missing = subprocess.run([python, "-I", "-m", "pip", "--version"],
                                     capture_output=True, text=True, timeout=10)
            self.assertNotEqual(0, missing.returncode)
            with patch.object(bootstrap, "VENV", venv), patch.object(bootstrap, "VENV_PYTHON", python):
                validated = bootstrap._ensure_venv(sys.executable)
                bootstrap._ensure_pip(validated)
                working = subprocess.run([python, "-I", "-m", "pip", "--version"],
                                         capture_output=True, text=True, timeout=10)
                self.assertEqual(0, working.returncode, working.stderr)
                self.assertIn(str(venv), working.stdout)
                bootstrap._ensure_pip(validated)
                repeated = subprocess.run([python, "-I", "-m", "pip", "--version"],
                                          capture_output=True, text=True, timeout=10)
                self.assertEqual(working.stdout, repeated.stdout)

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
            self.assertEqual("user installation state\n", marker.read_text(encoding="utf-8"))

    def test_bootstrap_rejects_an_external_interpreter_without_replacing_it(self) -> None:
        from scripts import bootstrap

        with tempfile.TemporaryDirectory(prefix="goal-native-external-python-") as temporary:
            venv = Path(temporary) / ".venv"
            interpreter = venv / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(Path(sys.executable).resolve())
            with patch.multiple(bootstrap, VENV=venv, VENV_PYTHON=interpreter):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._ensure_venv(sys.executable)
            self.assertTrue(interpreter.is_symlink())
            self.assertEqual(Path(sys.executable).resolve(), interpreter.resolve())
            self.assertFalse((venv / "lib").exists())

    @unittest.skipUnless(shutil.which("git"), "Git is required for the pin boundary")
    def test_bootstrap_rejects_a_clean_but_unpinned_pi_without_resetting_it(self) -> None:
        from scripts import bootstrap

        with tempfile.TemporaryDirectory(prefix="goal-native-pin-") as temporary:
            root = Path(temporary)
            pi = root / "upstream" / "pi"
            pi.mkdir(parents=True)

            def git(cwd: Path, *args: str) -> str:
                return subprocess.run(
                    ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgsign=false", *args],
                    cwd=cwd, check=True, capture_output=True, text=True,
                ).stdout.strip()

            git(root, "init", "--quiet", "--template=")
            git(pi, "init", "--quiet", "--template=")
            (pi / "package.json").write_text("{}\n", encoding="utf-8")
            git(pi, "add", "package.json")
            git(pi, "commit", "--quiet", "-m", "Pinned fixture")
            pinned = git(pi, "rev-parse", "HEAD")
            git(root, "update-index", "--add", "--cacheinfo", f"160000,{pinned},upstream/pi")
            git(root, "commit", "--quiet", "-m", "Pin dependency")
            (pi / "package.json").write_text('{"changed":true}\n', encoding="utf-8")
            git(pi, "commit", "--quiet", "-am", "Different clean dependency")
            other = git(pi, "rev-parse", "HEAD")
            from goal_native.runtime import pi_source_status
            self.assertFalse(pi_source_status(root, shutil.which("git"))["ok"])
            with patch.object(bootstrap, "ROOT", root), self.assertRaises(bootstrap.BootstrapError):
                bootstrap._ensure_pi_submodule(shutil.which("git"))
            self.assertEqual(other, git(pi, "rev-parse", "HEAD"))
            self.assertEqual('{"changed":true}\n', (pi / "package.json").read_text(encoding="utf-8"))
            git(pi, "checkout", "--quiet", "--detach", pinned)
            self.assertTrue(pi_source_status(root, shutil.which("git"))["ok"])
            (pi / "package.json").write_text('{"local":true}\n', encoding="utf-8")
            self.assertFalse(pi_source_status(root, shutil.which("git"))["ok"])
            (pi / "package.json").write_text("{}\n", encoding="utf-8")
            real_pi = root / "saved-pi"
            pi.rename(real_pi)
            pi.symlink_to(real_pi, target_is_directory=True)
            self.assertFalse(pi_source_status(root, shutil.which("git"))["initialized"])

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


def shlex_quote(value: str) -> str:
    """Quote a temporary path for the tiny test-only POSIX command."""
    return "'" + value.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    unittest.main()
