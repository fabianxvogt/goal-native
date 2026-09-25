from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.python_tasks import PYTHON_TASK_IDS, _CHECKER_SOURCE


MATRICES = {
    "pytest-12444-approx-formatting": {
        "cold": ["imports_selected_source", "unordered_mapping_equal_passes", "unordered_mapping_mismatch_fails", "unordered_mapping_reports_one_mismatch", "unordered_mapping_does_not_report_equal_key"],
        "changed": ["imports_selected_source", "unordered_mapping_equal_passes", "unordered_mapping_mismatch_fails", "unordered_mapping_reports_one_mismatch", "unordered_mapping_does_not_report_equal_key", "parametrized_unordered_mismatch_fails", "parametrized_diagnostics_repeat_correctly"],
    },
    "pytest-10839-async-fixture-warning": {
        "cold": ["imports_selected_source", "sync_test_async_fixture_is_error", "sync_test_async_fixture_reports_boundary", "unrelated_sync_fixture_remains_valid"],
        "changed": ["imports_selected_source", "sync_test_async_fixture_is_error", "sync_test_async_fixture_reports_boundary", "unrelated_sync_fixture_remains_valid", "autouse_async_fixture_test_runs", "autouse_async_fixture_warns"],
    },
    "flask-2984-routing-exception-handler": {
        "cold": ["imports_selected_source", "routing_redirect_response_is_preserved", "routing_redirect_skips_http_exception_handler", "genuine_http_error_uses_handler"],
        "changed": ["imports_selected_source", "routing_redirect_response_is_preserved", "routing_redirect_skips_http_exception_handler", "genuine_http_error_uses_handler", "explicit_http_error_uses_handler", "redirect_target_remains_callable"],
    },
    "flask-5774-async-stream-context": {
        "cold": ["imports_selected_source", "async_stream_response_remains_streamed", "async_stream_iteration_keeps_context", "async_stream_generator_is_not_buffered", "async_stream_context_cleanup", "sync_stream_context_control"],
        "changed": ["imports_selected_source", "async_stream_response_remains_streamed", "async_stream_iteration_keeps_context", "async_stream_generator_is_not_buffered", "async_stream_context_cleanup", "sync_stream_context_control"],
    },
    "flask-5786-redirect-session": {
        "cold": ["imports_selected_source", "follow_redirects_returns_target", "follow_redirects_preserves_final_session"],
        "changed": ["imports_selected_source", "follow_redirects_returns_target", "follow_redirects_preserves_final_session", "second_request_preserves_nested_context_order"],
    },
}


# Minimal importable candidate packages: their behavior, not checker internals,
# controls where the standalone subprocess fails.
FLASK = """
from types import SimpleNamespace
session = {'redirect': 'yes', 'target': 'yes'}
def redirect(value): return value
class Flask:
    def __init__(self, *args): pass
    def get(self, *args): return lambda fn: fn
    def test_client(self): return Client()
class Client:
    def __enter__(self): return self
    def __exit__(self, *args):
        if MODE == 'teardown': raise RuntimeError('teardown failed')
    def get(self, *args, **kwargs):
        return SimpleNamespace(status_code=200, data=b'target')
if MODE == 'session':
    class BrokenSession:
        def keys(self): raise RuntimeError('Working outside of request context')
    session = BrokenSession()
"""

# A dependency-free PEP 517 backend lets pip exercise the real selected-source
# build/import path without requiring setuptools or installed pytest.
BACKEND = """
from pathlib import Path
import zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = 'pytest-1.0-py3-none-any.whl'
    with zipfile.ZipFile(Path(wheel_directory) / name, 'w') as wheel:
        wheel.write('src/pytest/__init__.py', 'pytest/__init__.py')
        wheel.writestr('pytest-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\\nName: pytest\\nVersion: 1.0\\n')
        wheel.writestr('pytest-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        wheel.writestr('pytest-1.0.dist-info/RECORD', '')
    return name
"""


class StandaloneCheckerFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.script = self.base / 'checker.py'
        self.script.write_text(_CHECKER_SOURCE, encoding='utf-8')

    def run_side(self, task_id, phase, source, *, buildable=True):
        root = self.base / (task_id + '-' + phase)
        package = 'pytest' if task_id.startswith('pytest-') else 'flask'
        selected = root if task_id.startswith('flask-2984') else root / 'src'
        directory = selected / package
        directory.mkdir(parents=True)
        (directory / '__init__.py').write_text(source, encoding='utf-8')
        if task_id.startswith('flask-2984'):
            werkzeug = selected / 'werkzeug'
            werkzeug.mkdir()
            (werkzeug / '__init__.py').write_text('')
            (werkzeug / 'exceptions.py').write_text('class HTTPException(Exception): pass\n')
        if package == 'pytest' and buildable:
            (root / 'pyproject.toml').write_text(
                '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["src"]\n')
            (root / 'src' / 'backend.py').write_text(BACKEND)
        completed = subprocess.run(
            [sys.executable, '-I', str(self.script), '--side', task_id, str(root), phase],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual([c['name'] for c in result['checks']], MATRICES[task_id][phase])
        self.assertTrue(all(len(c['detail']) <= 600 for c in result['checks']))
        return result

    def test_import_failure_all_five_tasks_and_phases(self):
        for task in PYTHON_TASK_IDS:
            for phase in ('cold', 'changed'):
                with self.subTest(task=task, phase=phase):
                    result = self.run_side(task, phase, "raise RuntimeError('broken import' + 'x' * 1000)\n")
                    self.assertFalse(result['all_passed'])
                    for check in result['checks']:
                        self.assertFalse(check['passed'])
                        self.assertIn('RuntimeError: broken import', check['detail'])

    def test_pytest_packaging_failure_is_not_invented_import_failure(self):
        for task in PYTHON_TASK_IDS:
            if not task.startswith('pytest-'): continue
            for phase in ('cold', 'changed'):
                with self.subTest(task=task, phase=phase):
                    result = self.run_side(task, phase, "raise RuntimeError('never imported')", buildable=False)
                    for check in result['checks']:
                        self.assertFalse(check['passed'])
                        self.assertIn('candidate packaging failed', check['detail'])
                        self.assertNotIn('never imported', check['detail'])

    def test_late_session_failure_retains_target_response(self):
        task = 'flask-5786-redirect-session'
        for phase in ('cold', 'changed'):
            with self.subTest(phase=phase):
                result = self.run_side(task, phase, "MODE = 'session'\n" + FLASK)
                self.assertEqual([c['passed'] for c in result['checks']], [True, True] + [False] * (len(result['checks']) - 2))
                for check in result['checks'][2:]:
                    self.assertIn('Working outside of request context', check['detail'])
                self.assertFalse(result['all_passed'])

    def test_teardown_failure_invalidates_final_check_only(self):
        task = 'flask-5786-redirect-session'
        for phase in ('cold', 'changed'):
            with self.subTest(phase=phase):
                result = self.run_side(task, phase, "MODE = 'teardown'\n" + FLASK)
                self.assertEqual([c['passed'] for c in result['checks']], [True] * (len(result['checks']) - 1) + [False])
                self.assertIn('teardown failed', result['checks'][-1]['detail'])
                self.assertFalse(result['all_passed'])

    def test_routing_second_request_failure_retains_redirect_checks(self):
        source = """
from types import SimpleNamespace
def abort(code): pass
class Flask:
    def __init__(self, *args): pass
    def route(self, *args): return lambda fn: fn
    def errorhandler(self, *args): return lambda fn: fn
    def test_client(self): return Client()
class Client:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, path):
        if path == '/slash':
            return SimpleNamespace(status_code=308, headers={'Location': '/slash/'})
        raise RuntimeError('second request failed')
"""
        task = 'flask-2984-routing-exception-handler'
        for phase in ('cold', 'changed'):
            with self.subTest(phase=phase):
                result = self.run_side(task, phase, source)
                self.assertEqual([c['passed'] for c in result['checks']], [True] * 3 + [False] * (len(result['checks']) - 3))
                for check in result['checks'][3:]:
                    self.assertIn('second request failed', check['detail'])
                self.assertFalse(result['all_passed'])

    def test_stream_runtime_failure_does_not_invent_cleanup_success(self):
        source = """
Response = g = request = session = None
def stream_with_context(value): return value
class Flask:
    def __init__(self, *args): pass
    def get(self, *args): return lambda fn: fn
    def test_client(self): raise RuntimeError('Working outside of request context')
"""
        task = 'flask-5774-async-stream-context'
        for phase in ('cold', 'changed'):
            with self.subTest(phase=phase):
                result = self.run_side(task, phase, source)
                self.assertTrue(result['checks'][0]['passed'])
                for check in result['checks'][1:]:
                    self.assertFalse(check['passed'])
                    self.assertIn('Working outside of request context', check['detail'])
                self.assertFalse(result['all_passed'])

    def test_stream_close_failure_retains_completed_iteration(self):
        source = """
Response = g = request = session = None
def stream_with_context(value): return value
class Flask:
    def __init__(self, *args): pass
    def get(self, *args): return lambda fn: fn
    def test_client(self): return Client()
class Client:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, path, **kwargs): return Stream()
class Stream:
    status_code = 200
    is_streamed = True
    response = iter([BODY])
    def close(self): raise RuntimeError('stream close failed')
"""
        task = 'flask-5774-async-stream-context'
        for phase in ('cold', 'changed'):
            with self.subTest(phase=phase):
                body = b'async' if phase == 'cold' else b'async:/async:kept'
                result = self.run_side(task, phase, source.replace('BODY', repr(body)))
                checks = {c['name']: c for c in result['checks']}
                self.assertTrue(checks['imports_selected_source']['passed'])
                self.assertTrue(checks['async_stream_response_remains_streamed']['passed'])
                self.assertTrue(checks['async_stream_iteration_keeps_context']['passed'])
                self.assertFalse(checks['async_stream_context_cleanup']['passed'])
                self.assertIn('stream close failed', checks['async_stream_context_cleanup']['detail'])
                self.assertFalse(result['all_passed'])

    def test_process_exit_remains_protocol_failure(self):
        task = 'flask-5786-redirect-session'
        for phase in ('cold', 'changed'):
            with self.subTest(phase=phase):
                result = self.run_side(task, phase, "MODE = 'ok'\n" + FLASK)
                self.assertTrue(result['all_passed'])
                root = self.base / (task + '-' + phase)
                (root / 'src/flask/__init__.py').write_text('import os\nos._exit(7)\n')
                completed = subprocess.run(
                    [sys.executable, '-I', str(self.script), task, str(root), str(root), phase],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result['status'], 'failed')
                checks = result['observations']['checks']
                self.assertTrue(all(not c['passed'] for c in checks))
                self.assertTrue(all(c['name'].endswith('.checker_process') for c in checks))
                self.assertNotEqual([c['name'] for c in checks], ['candidate.' + n for n in MATRICES[task][phase]])

    def test_pytest_late_runtime_failure_retains_completed_probe(self):
        source = """
calls = 0
def hookimpl(**kwargs): return lambda fn: fn
def main(*args, **kwargs):
    global calls
    calls += 1
    if calls > 1: raise RuntimeError('later pytest invocation failed')
    return FIRST_CODE
"""
        for task in PYTHON_TASK_IDS:
            if not task.startswith('pytest-'): continue
            for phase in ('cold', 'changed'):
                with self.subTest(task=task, phase=phase):
                    fixture = '10839' in task
                    result = self.run_side(task, phase, source.replace('FIRST_CODE', '1' if fixture else '0'))
                    checks = result['checks']
                    self.assertTrue(checks[0]['passed'])
                    self.assertTrue(checks[1]['passed'])
                    offset = 3 if fixture else 2
                    if fixture:
                        self.assertFalse(checks[2]['passed'])  # no boundary exception observed
                        self.assertNotIn('later pytest invocation', checks[2]['detail'])
                    for check in checks[offset:]:
                        self.assertFalse(check['passed'])
                        self.assertIn('later pytest invocation failed', check['detail'])
                    self.assertFalse(result['all_passed'])


if __name__ == '__main__':
    unittest.main()
