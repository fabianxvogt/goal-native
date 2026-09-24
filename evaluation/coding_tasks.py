"""Frozen public coding-task metadata for the native-pi workflow.

This module contains provenance only. Source checkouts, checker code, model
traces, and raw downloads are supplied by the controller outside this tree.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .workloads import registration_document as lifecycle_registration_document

REGISTRATION_SCHEMA = "goal-native-coding-evaluation/v1"
REGISTRATION_ID = "goal-native-coding-pilot-2026-09-24"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

REPOSITORIES: tuple[dict[str, Any], ...] = (
    {
        "id": "pytest-dev/pytest",
        "url": "https://github.com/pytest-dev/pytest",
        "language_family": "Python",
        "license_spdx": "MIT",
        "license_provenance_url": "https://api.github.com/repos/pytest-dev/pytest/license",
    },
    {
        "id": "pallets/flask",
        "url": "https://github.com/pallets/flask",
        "language_family": "Python",
        "license_spdx": "BSD-3-Clause",
        "license_provenance_url": "https://api.github.com/repos/pallets/flask/license",
    },
    {
        "id": "prettier/prettier",
        "url": "https://github.com/prettier/prettier",
        "language_family": "TypeScript",
        "language_note": "TypeScript parser and printer behavior in a TypeScript ecosystem repository",
        "license_spdx": "MIT",
        "license_provenance_url": "https://api.github.com/repos/prettier/prettier/license",
    },
)


def _task(
    task_id: str,
    repository: str,
    issue_number: int,
    title: str,
    prompt: str,
    changed_requirement: str,
    source_commit: str,
    fix_pr: int,
    fix_commit: str,
    checker_id: str,
    behavior: str,
) -> dict[str, Any]:
    repo = next(item for item in REPOSITORIES if item["id"] == repository)
    return {
        "id": task_id,
        "kind": "public_historical_issue",
        "repository": repository,
        "title": title,
        "prompt": prompt,
        "changed_requirement": changed_requirement,
        "source_material": "external_git_snapshot_required",
        "provenance": {
            "issue_url": f"https://github.com/{repository}/issues/{issue_number}",
            "issue_number": issue_number,
            "source_commit": source_commit,
            "source_tree_url": f"https://github.com/{repository}/tree/{source_commit}",
            "fix_pr_url": f"https://github.com/{repository}/pull/{fix_pr}",
            "fix_commit": fix_commit,
            "fix_commit_url": f"https://github.com/{repository}/commit/{fix_commit}",
            "license_spdx": repo["license_spdx"],
            "license_provenance_url": repo["license_provenance_url"],
        },
        "checker": {
            "id": checker_id,
            "protocol": "goal-native-independent-checker/v1",
            "location": "external_read_only_checker_root",
            "behavior": behavior,
            "candidate_is_input_only": True,
        },
    }


# These are issue/PR-linked tasks, not seeded snippets. Each source revision is
# the first fix commit's parent recorded by the public GitHub commit API.
TASKS: tuple[dict[str, Any], ...] = (
    _task(
        "pytest-12444-approx-formatting",
        "pytest-dev/pytest",
        12444,
        "pytest.approx reports correct values as wrong in its formatting",
        "Repair pytest.approx's comparison and failure presentation so the historical issue's correct values are not reported as mismatches.",
        "Keep the corrected comparison and readable failure output when the assertion is repeated through a parametrized test.",
        "6687d957a715e3c18ab90e282c6a6e0a4bf0144b",
        13815,
        "e95a843a5dbf8af618d0714705bbf97dfdb04044",
        "pytest-12444-regression",
        "Run the externally staged approx regression and assert both comparison truth and diagnostic formatting.",
    ),
    _task(
        "pytest-10839-async-fixture-warning",
        "pytest-dev/pytest",
        10839,
        "Warn when synchronous tests rely on async fixtures",
        "Implement the historical pytest fixture warning/error behavior for synchronous tests that depend on async fixtures.",
        "Preserve the warning boundary for an autouse async fixture without turning an unrelated synchronous fixture into an error.",
        "256203a5c9aae87741ca195c29510414dd5ddc73",
        12930,
        "6728ec5606dd7e2eb70ef64972f97eab061f95e2",
        "pytest-10839-regression",
        "Run the externally staged fixture diagnostics and distinguish the documented warning from unrelated fixture setup.",
    ),
    _task(
        "flask-2984-routing-exception-handler",
        "pallets/flask",
        2984,
        "Do not route internal redirects through HTTPException handlers",
        "Fix Flask so a registered HTTPException handler does not intercept a non-error routing redirect.",
        "Retain the normal handler for a genuine HTTP error while leaving the redirect response and endpoint behavior unchanged.",
        "38a391815b4936e9357834bb4924e09fc4384e9a",
        2986,
        "b92b2e6c743c8b09744c9c9788046c719cc38eab",
        "flask-2984-regression",
        "Run the external Flask routing check for a redirect and a genuine handled HTTP error.",
    ),
    _task(
        "flask-5774-async-stream-context",
        "pallets/flask",
        5774,
        "Make stream_with_context safe for async routes",
        "Repair the historical async stream_with_context context handling failure without replacing the streaming response with a buffered shortcut.",
        "Verify the context remains valid during iteration and that a synchronous streaming route keeps its established behavior.",
        "49b7e7bc8fb69d605719991d1c0a99fcee689053",
        5799,
        "9822a0351574790cb66c652fcc396ad7aa2b09d8",
        "flask-5774-regression",
        "Run the external async and synchronous streaming context checks, including iteration-time context ownership.",
    ),
    _task(
        "flask-5786-redirect-session",
        "pallets/flask",
        5786,
        "Preserve the final session after followed redirects in the test client",
        "Fix the Flask test client context ordering so session state written by the redirect target is visible after follow_redirects.",
        "Keep the final target session state while preserving the order of nested client contexts on a second request.",
        "5addaf833b2e8c7a616f89dd8ad5a44b07d7c000",
        5797,
        "53b8f08218796d95764c5252bccd8a50a173a6fb",
        "flask-5786-regression",
        "Run the external redirect/session check and a nested-context ordering check.",
    ),
    _task(
        "prettier-13-typescript-parser",
        "prettier/prettier",
        13,
        "Add TypeScript parsing and printing support",
        "Implement the historical TypeScript parser/printer issue for the supported declaration forms covered by the linked upstream work.",
        "Preserve formatting of an ordinary JavaScript file while accepting the TypeScript declaration form in the same invocation.",
        "bff2d48aa833696dded4b5e618e7022d03ea77cc",
        1459,
        "0cb827183973338ed3f2c5e1988cc2d325ccf710",
        "prettier-13-typescript-regression",
        "Run the external TypeScript parser/printer behavior checks and compare formatted output to the post-fix oracle.",
    ),
    _task(
        "prettier-1422-typescript-namespace-export",
        "prettier/prettier",
        1422,
        "Format TypeScript namespace export declarations",
        "Add the TypeScript namespace export declaration behavior requested by the historical issue and linked fix.",
        "Keep neighboring TypeScript declarations stable when the namespace export is nested in a larger source file.",
        "bff2d48aa833696dded4b5e618e7022d03ea77cc",
        1459,
        "0cb827183973338ed3f2c5e1988cc2d325ccf710",
        "prettier-1422-regression",
        "Run the external namespace-export formatting check and a neighboring-declaration stability check.",
    ),
    _task(
        "prettier-1306-typescript-module-reference",
        "prettier/prettier",
        1306,
        "Format TypeScript module references",
        "Implement the historical TypeScript module-reference formatting behavior covered by the linked upstream work.",
        "Preserve the module-reference output while formatting a file that also contains an unrelated TypeScript declaration.",
        "bff2d48aa833696dded4b5e618e7022d03ea77cc",
        1459,
        "8245949fd66ea407843640cbdb3aae496fb2a7a8",
        "prettier-1306-regression",
        "Run the external module-reference printer check with an unrelated-declaration control.",
    ),
    _task(
        "prettier-1480-typescript-heritage",
        "prettier/prettier",
        1480,
        "Format TypeScript keywords and class heritage",
        "Implement the TypeScript keyword, namespace-function, and class-heritage formatting behavior linked from the historical issue.",
        "Preserve the existing JavaScript class output while formatting the TypeScript heritage form.",
        "e8a80ca0aa3f54db3cc4c459961aa026c5ad2042",
        1483,
        "863228209f698cde12a690be8fa71c235f710a53",
        "prettier-1480-regression",
        "Run the external TypeScript heritage printer check and the JavaScript class control.",
    ),
    _task(
        "prettier-2593-flow-interface-semicolon",
        "prettier/prettier",
        2593,
        "Emit valid semicolons in Flow interface-like bodies",
        "Fix the historical interface-like body formatting so the generated syntax remains valid under the parser's semicolon rules.",
        "Keep the valid interface-body output while preserving the configured semicolon behavior for an ordinary JavaScript statement.",
        "cd4d4e7273b1732caff2b8006edd9c69336a4504",
        2888,
        "5e146cdabeb25fa1bfb94ac9b0a9efa497bfcf5b",
        "prettier-2593-regression",
        "Run the external interface-body syntax check and the ordinary-statement semicolon control.",
    ),
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_document(document: Mapping[str, Any]) -> str:
    body = dict(document)
    body.pop("registration_sha256", None)
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def registration_document() -> dict[str, Any]:
    lifecycle = lifecycle_registration_document()
    document: dict[str, Any] = {
        "schema": REGISTRATION_SCHEMA,
        "registration_id": REGISTRATION_ID,
        "lifecycle_registration_id": lifecycle["registration_id"],
        "lifecycle_registration_sha256": lifecycle["registration_sha256"],
        "repositories": [copy.deepcopy(repo) for repo in REPOSITORIES],
        "tasks": [copy.deepcopy(task) for task in TASKS],
        "task_count": len(TASKS),
        "repository_count": len(REPOSITORIES),
        "source_policy": {
            "source_checkouts": "external_only",
            "checker_roots": "external_read_only",
            "raw_traces_and_downloads": "external_only",
            "embedded_seeded_snippets": False,
        },
    }
    document["registration_sha256"] = _hash_document(document)
    return document


def validate_registration(document: Mapping[str, Any]) -> None:
    expected = registration_document()
    if dict(document) != expected:
        raise ValueError("coding registration is not the frozen preregistered document")
    if document.get("registration_sha256") != _hash_document(document):
        raise ValueError("coding registration hash is invalid")
    if len(document["repositories"]) != 3 or len(document["tasks"]) != 10:
        raise ValueError("coding registration must contain exactly three repositories and ten tasks")
    if any(repo["license_spdx"] not in {"MIT", "BSD-3-Clause"} for repo in document["repositories"]):
        raise ValueError("coding registration contains an unapproved license")
    repository_ids = {repo["id"] for repo in document["repositories"]}
    if {task["repository"] for task in document["tasks"]} != repository_ids:
        raise ValueError("every selected repository must have a task")
    for task in document["tasks"]:
        provenance = task["provenance"]
        if not COMMIT_RE.fullmatch(provenance["source_commit"]) or not COMMIT_RE.fullmatch(provenance["fix_commit"]):
            raise ValueError(f"task {task['id']} has an invalid pinned commit")
        if not task["provenance"]["issue_url"].endswith(str(provenance["issue_number"])):
            raise ValueError(f"task {task['id']} has invalid issue provenance")
        if task["source_material"] != "external_git_snapshot_required":
            raise ValueError(f"task {task['id']} embeds source material")
        if task["checker"]["location"] != "external_read_only_checker_root":
            raise ValueError(f"task {task['id']} does not require an independent checker")


def load_registration(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if source == SOURCE_ROOT or SOURCE_ROOT in source.parents:
        raise ValueError("coding registration input must be outside the repository")
    document = json.loads(source.read_text(encoding="utf-8"))
    validate_registration(document)
    return document


def write_registration(path: str | Path) -> Path:
    destination = Path(path).expanduser().resolve()
    if destination == SOURCE_ROOT or SOURCE_ROOT in destination.parents:
        raise ValueError("coding registration output must be outside the repository")
    if destination.exists():
        raise FileExistsError(f"registration output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(f"{_canonical(registration_document())}\n", encoding="utf-8", newline="")
    return destination


__all__ = [
    "REGISTRATION_ID",
    "REGISTRATION_SCHEMA",
    "REPOSITORIES",
    "TASKS",
    "load_registration",
    "registration_document",
    "validate_registration",
    "write_registration",
]
