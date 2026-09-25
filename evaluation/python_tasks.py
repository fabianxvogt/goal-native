"""External preparation and behavioral checkers for the frozen Python tasks.

The source and reference trees produced here live outside the repository.  The
checker is written into the external corpus root and is invoked by the
controller in the isolated runtime; it never runs a candidate on the host.
"""
from __future__ import annotations

import hashlib
import os
import textwrap
import uuid
from pathlib import Path
from typing import Any, Mapping


from .coding_tasks import TASKS
from .corpus import prepare_sources

CORPUS_SCHEMA = "goal-native-python-corpus/v1"
CHECKER_SCHEMA = "goal-native-python-checker/v1"

PYTHON_TASK_IDS = tuple(task["id"] for task in TASKS[:5])
_FROZEN_TASKS = {task["id"]: task for task in TASKS[:5]}

_TASK_CONFIG: dict[str, dict[str, Any]] = {
    "pytest-12444-approx-formatting": {
        "source_license_path": "LICENSE",
        "source_import_root": "src",
        "setup_commands": [
            "RUN python3 -m pip install --break-system-packages --no-cache-dir --only-binary=:all: setuptools==77.0.3 setuptools-scm==8.2.0 iniconfig==2.1.0 packaging==25.0 pluggy==1.6.0 pygments==2.19.2",
        ],
    },
    "pytest-10839-async-fixture-warning": {
        "source_license_path": "LICENSE",
        "source_import_root": "src",
        "setup_commands": [
            "RUN python3 -m pip install --break-system-packages --no-cache-dir --only-binary=:all: setuptools==77.0.3 setuptools-scm==8.2.0 iniconfig==2.1.0 packaging==25.0 pluggy==1.6.0 pygments==2.19.2",
        ],
    },
    "flask-2984-routing-exception-handler": {
        "source_license_path": "LICENSE",
        "source_import_root": ".",
        "setup_commands": [
            "RUN python3 -m pip install --break-system-packages --no-cache-dir --only-binary=:all: click==7.1.2 itsdangerous==1.1.0 Jinja2==3.0.3 MarkupSafe==2.1.5 Werkzeug==0.16.1",
        ],
    },
    "flask-5774-async-stream-context": {
        "source_license_path": "LICENSE.txt",
        "source_import_root": "src",
        "setup_commands": [
            "RUN python3 -m pip install --break-system-packages --no-cache-dir --only-binary=:all: asgiref==3.9.1 blinker==1.9.0 click==8.1.8 itsdangerous==2.2.0 Jinja2==3.1.6 MarkupSafe==3.0.2 Werkzeug==3.1.3",
        ],
    },
    "flask-5786-redirect-session": {
        "source_license_path": "LICENSE.txt",
        "source_import_root": "src",
        "setup_commands": [
            "RUN python3 -m pip install --break-system-packages --no-cache-dir --only-binary=:all: blinker==1.9.0 click==8.1.8 itsdangerous==2.2.0 Jinja2==3.1.6 MarkupSafe==3.0.2 Werkzeug==3.1.3",
        ],
    },
}

# This file is deliberately standalone.  It is copied to the external checker
# root, then executed with ``python3 -I``.  No import from Goal Native or from a
# candidate tree is used to select the checker or its dependencies.
_CHECKER_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CHECKER_SCHEMA = "goal-native-python-checker/v1"
TASKS = {
    "pytest-12444-approx-formatting",
    "pytest-10839-async-fixture-warning",
    "flask-2984-routing-exception-handler",
    "flask-5774-async-stream-context",
    "flask-5786-redirect-session",
}
MAX_DETAIL = 600
MAX_CAPTURE = 12_000


def _short(value: Any) -> str:
    text = str(value).replace(chr(0), "")
    return text[:MAX_DETAIL]


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": _short(detail)}


def _source_root(root: Path, import_root: str) -> Path:
    selected = root / import_root if import_root != "." else root
    if not selected.is_dir() or selected.is_symlink():
        raise RuntimeError("candidate source import root is not a regular directory")
    return selected.resolve()


def _import_selected(root: Path, import_root: str, package: str) -> tuple[Any, list[dict[str, Any]]]:
    selected = _source_root(root, import_root)
    if package == "pytest":
        build_directory = Path(tempfile.mkdtemp(prefix="goal-native-pytest-build-")).resolve()
        build_root = build_directory / "source"
        target = build_directory / "site"
        build_root.mkdir()
        for name in ("pyproject.toml", "README.rst", "LICENSE"):
            source_file = root / name
            if source_file.is_file():
                shutil.copy2(source_file, build_root / name)
        shutil.copytree(root / "src", build_root / "src")
        build_environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST": "8.4.0+goal_native",
        }
        completed = subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--no-index",
                "--no-deps", "--no-build-isolation", "--target", str(target),
                str(build_root),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            env=build_environment,
        )
        if completed.returncode:
            raise RuntimeError("candidate packaging failed: %s" % (completed.stderr or completed.stdout)[-MAX_DETAIL:])
        selected = target
    sys.path.insert(0, str(selected))
    importlib.invalidate_caches()
    module = importlib.import_module(package)
    location = getattr(module, "__file__", None)
    if not isinstance(location, str):
        raise RuntimeError("selected package has no source file")
    resolved = Path(location).resolve()
    if resolved != selected and selected not in resolved.parents:
        raise RuntimeError("selected package was imported outside the selected source")
    return module, [_check("imports_selected_source", True, str(resolved))]


def _pytest_run(pytest: Any, lines: list[str], *, warnings: bool = False, observations: dict[str, Any] | None = None) -> tuple[int, str]:
    observed = observations if observations is not None else {}
    class Observer:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            if call.excinfo is not None:
                observed.setdefault("exceptions", []).append((call.when, call.excinfo.type.__name__))
            yield

        def pytest_warning_recorded(self, warning_message):
            observed.setdefault("warnings", []).append(warning_message.category.__name__)
    with tempfile.TemporaryDirectory(prefix="goal-native-pytest-check-") as directory:
        test_file = Path(directory) / ("test_behavior_%s.py" % Path(directory).name.replace("-", "_"))
        test_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output = io.StringIO()
        errors = io.StringIO()
        old = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = int(pytest.main([
                    "-q", "--tb=short", "-o", "addopts=",
                    "-o", "filterwarnings=" if warnings else "filterwarnings=ignore",
                    str(test_file),
                ], plugins=[Observer()]))
        finally:
            if old is None:
                os.environ.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
            else:
                os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = old
        text = (output.getvalue() + errors.getvalue())[:MAX_CAPTURE]
        return code, text


def _pytest_approx(pytest: Any, phase: str, checks: list[dict[str, Any]]) -> None:
    equal_code, equal_output = _pytest_run(pytest, [
        "import pytest",
        "def test_unordered_equal():",
        "    expected = {'a': 1, 'c': 3}",
        "    actual = {'c': 3, 'a': 1}",
        "    assert actual == pytest.approx(expected)",
    ])
    checks.append(_check(
        "unordered_mapping_equal_passes",
        equal_code == 0,
        "exit=%s output=%s" % (equal_code, equal_output),
    ))
    mismatch_code, mismatch_output = _pytest_run(pytest, [
        "import pytest",
        "def test_unordered_mismatch():",
        "    expected = {'a': 1, 'c': 3}",
        "    actual = {'c': 5, 'a': 1}",
        "    assert actual == pytest.approx(expected)",
    ])
    one_mismatch = bool(re.search(r"Mismatched elements:\s*1\s*/\s*2", mismatch_output))
    no_false_a = not bool(re.search(r"\n\s*a\s*\|\s*1\s*\|", mismatch_output))
    checks.extend([
        _check("unordered_mapping_mismatch_fails", mismatch_code != 0, "exit=%s" % mismatch_code),
        _check("unordered_mapping_reports_one_mismatch", one_mismatch, mismatch_output),
        _check("unordered_mapping_does_not_report_equal_key", no_false_a, mismatch_output),
    ])
    if phase == "changed":
        parameterized_code, parameterized_output = _pytest_run(pytest, [
            "import pytest",
            "@pytest.mark.parametrize('actual', [{'c': 5, 'a': 1}, {'a': 1, 'c': 5}])",
            "def test_repeated_unordered_mismatch(actual):",
            "    assert actual == pytest.approx({'a': 1, 'c': 3})",
        ])
        count = parameterized_output.count("Mismatched elements: 1 / 2")
        checks.extend([
            _check("parametrized_unordered_mismatch_fails", parameterized_code != 0, "exit=%s" % parameterized_code),
            _check("parametrized_diagnostics_repeat_correctly", count == 2, parameterized_output),
        ])


def _pytest_fixture(pytest: Any, phase: str, checks: list[dict[str, Any]]) -> None:
    observed: dict[str, Any] = {}
    error_code, error_output = _pytest_run(pytest, [
        "import pytest",
        "@pytest.fixture",
        "async def async_fixture():",
        "    return 42",
        "def test_sync_requests_async(async_fixture):",
        "    assert async_fixture == 42",
    ], warnings=True, observations=observed)
    checks.extend([
        _check("sync_test_async_fixture_is_error", error_code != 0, "exit=%s output=%s" % (error_code, error_output)),
        _check("sync_test_async_fixture_reports_boundary", ("setup", "FixtureLookupError") in observed.get("exceptions", []), observed),
    ])
    control: dict[str, Any] = {}
    control_code, control_output = _pytest_run(pytest, [
        "import pytest",
        "@pytest.fixture",
        "def sync_fixture():",
        "    return 42",
        "def test_sync_fixture(sync_fixture):",
        "    assert sync_fixture == 42",
    ], warnings=True, observations=control)
    checks.append(_check("unrelated_sync_fixture_remains_valid", control_code == 0 and "PytestRemovedIn9Warning" not in control.get("warnings", []), control_output))
    if phase == "changed":
        autouse: dict[str, Any] = {}
        warning_code, warning_output = _pytest_run(pytest, [
            "import pytest",
            "@pytest.fixture(autouse=True)",
            "async def async_fixture():",
            "    return None",
            "def test_sync_autouse(async_fixture):",
            "    try:",
            "        async_fixture.send(None)",
            "    except StopIteration:",
            "        pass",
        ], warnings=True, observations=autouse)
        checks.extend([
            _check("autouse_async_fixture_test_runs", warning_code == 0, warning_output),
            _check("autouse_async_fixture_warns", "PytestRemovedIn9Warning" in autouse.get("warnings", []), autouse),
        ])


def _flask_2984(flask: Any, phase: str, checks: list[dict[str, Any]]) -> None:
    from werkzeug.exceptions import HTTPException
    from flask import abort

    app = flask.Flask("goal-native-flask-2984")
    calls: list[str] = []

    @app.errorhandler(HTTPException)
    def handle_error(error: Any) -> Any:
        calls.append(type(error).__name__)
        return "handled", error.code or 500

    @app.route("/slash/")
    def slash() -> str:
        return "slash"

    @app.route("/bad")
    def bad() -> Any:
        abort(400)

    with app.test_client() as client:
        redirect_response = client.get("/slash")
        redirect_ok = redirect_response.status_code in (301, 308) and str(redirect_response.headers.get("Location", "")).endswith("/slash/")
        checks.extend([
            _check("routing_redirect_response_is_preserved", redirect_ok, "status=%s location=%s" % (redirect_response.status_code, redirect_response.headers.get("Location"))),
            _check("routing_redirect_skips_http_exception_handler", not calls, "handler_calls=%s" % calls),
        ])
        error_response = client.get("/does-not-exist")
        checks.append(_check("genuine_http_error_uses_handler", error_response.status_code == 404 and error_response.data == b"handled" and calls and calls[-1] == "NotFound", "status=%s data=%r calls=%s" % (error_response.status_code, error_response.data, calls)))
        if phase == "changed":
            bad_response = client.get("/bad")
            checks.append(_check("explicit_http_error_uses_handler", bad_response.status_code == 400 and bad_response.data == b"handled" and calls[-1] == "BadRequest", calls))
            target = client.get("/slash/")
            checks.append(_check("redirect_target_remains_callable", target.status_code == 200 and target.data == b"slash", target.status_code))


def _flask_5774(flask: Any, phase: str, checks: list[dict[str, Any]]) -> None:
    from flask import Response, g, request, session, stream_with_context

    app = flask.Flask("goal-native-flask-5774")
    app.secret_key = "goal-native-checker-secret"
    progress: list[str] = []
    expected_body = b"async" if phase == "cold" else b"async:/async:kept"

    @app.get("/async")
    async def async_route() -> Any:
        session["value"] = "async"
        g.value = "kept"

        @stream_with_context
        def generate() -> Any:
            progress.append("started")
            yield session["value"].encode("ascii")
            if phase == "changed":
                yield (":" + request.path + ":" + g.value).encode("ascii")
            progress.append("finished")

        return Response(generate())

    @app.get("/sync")
    def sync_route() -> Any:
        g.value = "sync"

        @stream_with_context
        def generate() -> Any:
            yield (request.path + ":" + g.value).encode("ascii")

        return Response(generate())

    observed = {
        "async_stream_response_remains_streamed": False,
        "async_stream_iteration_keeps_context": False,
        "async_stream_generator_is_not_buffered": False,
        "async_stream_context_cleanup": False,
    }
    detail = ""
    try:
        with app.test_client() as client:
            async_response = client.get("/async", buffered=False)
            observed["async_stream_response_remains_streamed"] = async_response.status_code == 200 and bool(async_response.is_streamed)
            lazy = "finished" not in progress
            async_body = b"".join(async_response.response)
            observed["async_stream_iteration_keeps_context"] = async_body == expected_body
            observed["async_stream_generator_is_not_buffered"] = lazy and progress == ["started", "finished"]
            detail = "body=%r progress=%r" % (async_body, progress)
            async_response.close()
        observed["async_stream_context_cleanup"] = True
    except BaseException as error:
        detail += " cleanup_or_iteration_error=%r" % error
    checks.extend(_check(name, passed, detail) for name, passed in observed.items())
    sync_ok = False
    try:
        with app.test_client() as client:
            sync_response = client.get("/sync", buffered=False)
            sync_body = b"".join(sync_response.response)
            sync_response.close()
            sync_ok = sync_response.status_code == 200 and sync_body == b"/sync:sync"
            sync_detail = "status=%s body=%r" % (sync_response.status_code, sync_body)
    except BaseException as error:
        sync_ok, sync_detail = False, repr(error)
    checks.append(_check("sync_stream_context_control", sync_ok, sync_detail))


def _flask_5786(flask: Any, phase: str, checks: list[dict[str, Any]]) -> None:
    from flask import redirect, session

    app = flask.Flask("goal-native-flask-5786")
    app.secret_key = "goal-native-checker-secret"

    @app.get("/redirect")
    def redirect_route() -> Any:
        session["redirect"] = "yes"
        return redirect("/target")

    @app.get("/target")
    def target_route() -> str:
        session["target"] = "yes"
        return "target"

    with app.test_client() as client:
        response = client.get("/redirect", follow_redirects=True)
        checks.append(_check("follow_redirects_returns_target", response.status_code == 200 and response.data == b"target", "status=%s data=%r" % (response.status_code, response.data)))
        first_state = dict(session)
        checks.append(_check("follow_redirects_preserves_final_session", first_state.get("redirect") == "yes" and first_state.get("target") == "yes", "session=%s" % first_state))
        if phase == "changed":
            second_response = client.get("/target")
            second_state = dict(session)
            checks.append(_check("second_request_preserves_nested_context_order", second_response.status_code == 200 and second_state.get("target") == "yes" and second_state.get("redirect") == "yes", "status=%s session=%s" % (second_response.status_code, second_state)))


def _side(task_id: str, root: Path, phase: str) -> dict[str, Any]:
    config = {
        "pytest-12444-approx-formatting": ("pytest", "src"),
        "pytest-10839-async-fixture-warning": ("pytest", "src"),
        "flask-2984-routing-exception-handler": ("flask", "."),
        "flask-5774-async-stream-context": ("flask", "src"),
        "flask-5786-redirect-session": ("flask", "src"),
    }
    package, import_root = config[task_id]
    names = {
        "pytest-12444-approx-formatting": {
            "cold": ["imports_selected_source", "unordered_mapping_equal_passes",
                     "unordered_mapping_mismatch_fails", "unordered_mapping_reports_one_mismatch",
                     "unordered_mapping_does_not_report_equal_key"],
            "changed": ["imports_selected_source", "unordered_mapping_equal_passes",
                        "unordered_mapping_mismatch_fails", "unordered_mapping_reports_one_mismatch",
                        "unordered_mapping_does_not_report_equal_key",
                        "parametrized_unordered_mismatch_fails", "parametrized_diagnostics_repeat_correctly"],
        },
        "pytest-10839-async-fixture-warning": {
            "cold": ["imports_selected_source", "sync_test_async_fixture_is_error",
                     "sync_test_async_fixture_reports_boundary", "unrelated_sync_fixture_remains_valid"],
            "changed": ["imports_selected_source", "sync_test_async_fixture_is_error",
                        "sync_test_async_fixture_reports_boundary", "unrelated_sync_fixture_remains_valid",
                        "autouse_async_fixture_test_runs", "autouse_async_fixture_warns"],
        },
        "flask-2984-routing-exception-handler": {
            "cold": ["imports_selected_source", "routing_redirect_response_is_preserved",
                     "routing_redirect_skips_http_exception_handler", "genuine_http_error_uses_handler"],
            "changed": ["imports_selected_source", "routing_redirect_response_is_preserved",
                         "routing_redirect_skips_http_exception_handler", "genuine_http_error_uses_handler",
                         "explicit_http_error_uses_handler", "redirect_target_remains_callable"],
        },
        "flask-5774-async-stream-context": {
            "cold": ["imports_selected_source", "async_stream_response_remains_streamed",
                     "async_stream_iteration_keeps_context", "async_stream_generator_is_not_buffered",
                     "async_stream_context_cleanup", "sync_stream_context_control"],
            "changed": ["imports_selected_source", "async_stream_response_remains_streamed",
                        "async_stream_iteration_keeps_context", "async_stream_generator_is_not_buffered",
                        "async_stream_context_cleanup", "sync_stream_context_control"],
        },
        "flask-5786-redirect-session": {
            "cold": ["imports_selected_source", "follow_redirects_returns_target",
                     "follow_redirects_preserves_final_session"],
            "changed": ["imports_selected_source", "follow_redirects_returns_target",
                        "follow_redirects_preserves_final_session",
                        "second_request_preserves_nested_context_order"],
        },
    }

    observed: dict[str, dict[str, Any]] = {}

    def retain(items: list[dict[str, Any]]) -> None:
        for item in items:
            observed[item["name"]] = item

    checks: list[dict[str, Any]] = []
    try:
        module, imports = _import_selected(root, import_root, package)
        retain(imports)
        if task_id == "pytest-12444-approx-formatting":
            _pytest_approx(module, phase, checks)
        elif task_id == "pytest-10839-async-fixture-warning":
            _pytest_fixture(module, phase, checks)
        elif task_id == "flask-2984-routing-exception-handler":
            _flask_2984(module, phase, checks)
        elif task_id == "flask-5774-async-stream-context":
            _flask_5774(module, phase, checks)
        else:
            _flask_5786(module, phase, checks)
        retain(checks)
    except BaseException as error:
        retain(checks)
        detail = _short("%s: %s" % (type(error).__name__, error))
        # A completed body does not prove successful context teardown. When
        # nothing remains unobserved, conservatively invalidate the final check.
        if all(name in observed for name in names[task_id][phase]):
            final_name = names[task_id][phase][-1]
            observed[final_name] = _check(final_name, False, detail)
        for name in names[task_id][phase]:
            if name not in observed:
                observed[name] = _check(name, False, detail)
    checks = [observed[name] for name in names[task_id][phase]]
    return {"all_passed": all(item["passed"] for item in checks), "checks": checks}


def _run_side(script: Path, task_id: str, side: str, root: Path, phase: str) -> dict[str, Any]:
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(script), "--side", task_id, str(root), phase],
            cwd=str(script.parent),
            env=environment,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except BaseException as error:
        return {"all_passed": False, "checks": [_check(side + ".checker_process", False, repr(error))]}
    if completed.returncode != 0:
        return {"all_passed": False, "checks": [_check(side + ".checker_process", False, "checker child exited unsuccessfully")]}
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {"all_passed": False, "checks": [_check(side + ".checker_process", False, "no JSON output: %s" % completed.stderr[:MAX_DETAIL])]}
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        return {"all_passed": False, "checks": [_check(side + ".checker_process", False, "invalid JSON: %s" % error)]}
    if not isinstance(result, dict) or not isinstance(result.get("checks"), list):
        return {"all_passed": False, "checks": [_check(side + ".checker_process", False, "malformed side result")]}
    return result


def _main(argv: list[str]) -> int:
    if argv and argv[0] == "--side":
        if len(argv) != 4 or argv[1] not in TASKS or argv[3] not in {"cold", "changed"}:
            return 2
        result = _side(argv[1], Path(argv[2]).resolve(), argv[3])
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if len(argv) not in {4, 7} or argv[0] not in TASKS or argv[-1] not in {"cold", "changed"}:
        print(json.dumps({"protocol": CHECKER_SCHEMA, "status": "error", "observations": {"checks": [_check("request_is_valid", False, "expected task, candidate, reference, optional identities, and phase cold|changed")]}}, sort_keys=True, separators=(",", ":")))
        return 0
    task_id, candidate_arg, reference_arg = argv[:3]
    candidate_sha256 = checker_sha256 = environment_identity = None
    if len(argv) == 7:
        candidate_sha256, checker_sha256, environment_identity = argv[3:6]
    phase = argv[-1]
    candidate = Path(candidate_arg).resolve()
    reference = Path(reference_arg).resolve()
    script = Path(__file__).resolve()
    candidate_result = _run_side(script, task_id, "candidate", candidate, phase)
    reference_result = _run_side(script, task_id, "reference", reference, phase)
    candidate_checks = candidate_result.get("checks", [])
    reference_checks = reference_result.get("checks", [])
    checks = []
    for item in candidate_checks:
        checks.append({"name": "candidate." + str(item.get("name", "unknown")), "passed": bool(item.get("passed")), "detail": _short(item.get("detail", ""))})
    for item in reference_checks:
        checks.append({"name": "reference." + str(item.get("name", "unknown")), "passed": bool(item.get("passed")), "detail": _short(item.get("detail", ""))})
    passed = bool(candidate_result.get("all_passed")) and bool(reference_result.get("all_passed"))
    result = {
        "protocol": CHECKER_SCHEMA,
        "task_id": task_id,
        "phase": phase,
        "status": "passed" if passed else "failed",
        "candidate_sha256": candidate_sha256,
        "checker_sha256": checker_sha256,
        "environment_identity": environment_identity,
        "observations": {
            "checks": checks[:64],
            "candidate_all_passed": bool(candidate_result.get("all_passed")),
            "reference_all_passed": bool(reference_result.get("all_passed")),
            "reference_is_post_fix_oracle": True,
        },
    }
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    print(encoded[:60_000])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
'''




def _verify_task(task: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    task_id = task.get("id")
    if not isinstance(task_id, str) or task_id not in _FROZEN_TASKS:
        raise ValueError("prepare accepts one of the five frozen Python task IDs")
    frozen = _FROZEN_TASKS[task_id]
    expected = frozen["provenance"]
    supplied = task.get("provenance")
    if not isinstance(supplied, Mapping):
        raise ValueError(f"task {task_id} has no provenance")
    for field, value in expected.items():
        if supplied.get(field) != value:
            raise ValueError(f"frozen provenance mismatch for {task_id}: {field}")
    if task.get("repository") != frozen["repository"] or task.get("checker") != frozen["checker"]:
        raise ValueError(f"frozen task metadata mismatch for {task_id}")
    return task_id, dict(frozen), dict(_TASK_CONFIG[task_id])


def _checker_path(root: Path) -> Path:
    directory = root / "checker"
    directory.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent(_CHECKER_SOURCE).lstrip().encode("utf-8")
    path = directory / ("python_checker-" + hashlib.sha256(content).hexdigest() + ".py")
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"controller checker already exists with different bytes: {path}")
    else:
        temporary = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
        temporary.write_bytes(content)
        temporary.chmod(0o755)
        os.replace(temporary, path)
    return path




def _observation_schema() -> dict[str, Any]:
    return {
        "candidate_sha256": "64-hex string or null",
        "checker_sha256": "64-hex string or null",
        "environment_identity": "string or null",
        "observations": {
            "checks": [{"name": "string", "passed": "boolean", "detail": "bounded string"}],
            "candidate_all_passed": "boolean",
            "reference_all_passed": "boolean",
            "reference_is_post_fix_oracle": True,
        },
        "acceptance_rule": "status=passed and every candidate and reference check has passed; transport exit code is not sufficient",
    }


def check_argv(
    prepared: Mapping[str, Any], phase: str, *,
    candidate_sha256: str, checker_sha256: str, environment_identity: str,
) -> list[str]:
    """Invoke the controller-owned checker and reference in its read-only image."""
    if prepared["task_id"] not in PYTHON_TASK_IDS or phase not in {"cold", "changed"}:
        raise ValueError("unknown Python task or checker phase")
    return [
        "python3", "-I", "/opt/goal-native-checker/checker.py",
        prepared["task_id"], "/workspace", "/opt/goal-native-checker/reference",
        candidate_sha256, checker_sha256, environment_identity, phase,
    ]


def prepare(task: dict[str, Any], root: Path) -> dict[str, Any]:
    """Prepare immutable source snapshots and the externally owned checker."""
    task_id, frozen, config = _verify_task(task)
    result = prepare_sources(frozen, root, license_path=config["source_license_path"])
    checker = _checker_path(Path(root).expanduser().resolve())
    return {
        **result, "schema": CORPUS_SCHEMA, "task_id": task_id,
        "checker_path": checker, "setup_commands": list(config["setup_commands"]),
        "expected_observation_schema": _observation_schema(),
    }


__all__ = ["CHECKER_SCHEMA", "CORPUS_SCHEMA", "PYTHON_TASK_IDS", "check_argv", "prepare"]
