"""Preparation and independent checkers for the frozen Prettier tasks.

Source archives use the shared bounded corpus preparation. The fixed checker
runs against the candidate's actual Prettier entry point in a disposable image;
its code and expected outputs are never placed in the worker's candidate.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .coding_tasks import TASKS
from .corpus import prepare_sources



SCHEMA = "goal-native-typescript-task/v1"
CHECK_SCHEMA = "goal-native-typescript-check/v1"
PHASES = ("cold", "changed")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

_TASKS = {
    task["id"]: task
    for task in TASKS
    if task.get("repository") == "prettier/prettier"
}


# These are the direct production dependencies declared by the pinned public
# Prettier revisions.  Ranged manifest entries are made exact for the image
# setup command; the manifest itself is still checked against the source tree.
_EARLY_DEPS = {
    "ast-types": "0.9.8",
    "babel-code-frame": "6.22.0",
    "babylon": "7.0.0-beta.8",
    "chalk": "1.1.3",
    "esutils": "2.0.2",
    "flow-parser": "0.45.0",
    "get-stdin": "5.0.1",
    "glob": "7.1.1",
    "jest-validate": "19.0.0",
    "minimist": "1.2.0",
    "typescript-eslint-parser": "git://github.com/eslint/typescript-eslint-parser.git#a294afa8158c9c088521eed72b6745eed302361c",
}

_V17_DEPS = {
    "babel-code-frame": "7.0.0-alpha.12",
    "babylon": "7.0.0-beta.23",
    "chalk": "2.1.0",
    "cosmiconfig": "3.0.1",
    "dashify": "0.2.2",
    "diff": "3.2.0",
    "esutils": "2.0.2",
    "flow-parser": "0.51.0",
    "get-stream": "3.0.0",
    "globby": "^6.1.0",
    "graphql": "0.10.1",
    "ignore": "^3.3.3",
    "jest-docblock": "21.1.0",
    "jest-validate": "21.1.0",
    "leven": "2.1.0",
    "mem": "1.1.0",
    "minimatch": "3.0.4",
    "minimist": "1.2.0",
    "parse5": "3.0.2",
    "postcss": "^6.0.1",
    "postcss-less": "^1.0.0",
    "postcss-media-query-parser": "0.2.3",
    "postcss-scss": "1.0.0",
    "postcss-selector-parser": "2.2.3",
    "postcss-values-parser": "git://github.com/lydell/postcss-values-parser.git#af2c80b2bb558a6e7d61540d97f068f9fa162b38",
    "strip-bom": "3.0.0",
    "typescript": "2.5.1",
    "typescript-eslint-parser": "git://github.com/eslint/typescript-eslint-parser.git#5576fb48c89165967557e867b9462d43431dcb10",
}

_DEPENDENCY_GROUPS = {
    "prettier-13-typescript-parser": ("1.2.2", _EARLY_DEPS),
    "prettier-1422-typescript-namespace-export": ("1.2.2", _EARLY_DEPS),
    "prettier-1306-typescript-module-reference": ("1.2.2", _EARLY_DEPS),
    "prettier-1480-typescript-heritage": ("1.3.0", _EARLY_DEPS),
    "prettier-2593-flow-interface-semicolon": ("1.7.0", _V17_DEPS),
}

# Fixed, public package-install commands.  They install only public packages;
# no candidate files or host paths are referenced.  ``typescript`` is included
# because the independent syntax observer uses it, even where it was a
# historical devDependency rather than a Prettier runtime dependency.
def _npm_spec(name: str, version: str) -> str:
    if version.startswith("git://"):
        return name + "@git+https://" + version[len("git://") :]
    return name + "@" + version.lstrip("^")


def _setup_command(task_id: str) -> str:
    version, dependencies = _DEPENDENCY_GROUPS[task_id]
    image_group = "prettier-" + version.replace(".", "-")
    specs = [_npm_spec(name, value) for name, value in dependencies.items()]
    checker_dependency = (
        " typescript@2.3.2"
        if task_id in {
            "prettier-13-typescript-parser",
            "prettier-1422-typescript-namespace-export",
            "prettier-1306-typescript-module-reference",
            "prettier-1480-typescript-heritage",
        }
        else ""
    )
    return (
        "RUN mkdir -p /opt/goal-native-deps/"
        + image_group
        + " && npm --prefix /opt/goal-native-deps/"
        + image_group
        + " install --ignore-scripts --no-package-lock --no-save --no-audit --fund=false "
        + " ".join(specs)
        + checker_dependency
    )


# Inputs are taken from the upstream conformance/issue examples named by the
# pinned commits.  The expected strings are the post-fix observable outputs;
# no candidate result is embedded or accepted as an oracle.
_CASES: dict[str, dict[str, dict[str, Any]]] = {
    "prettier-13-typescript-parser": {
        "cold": {
            "regression": {
                "input": "// issue: https://github.com/Microsoft/TypeScript/issues/11545\n\nexport var X;\nexport as namespace N",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "// issue: https://github.com/Microsoft/TypeScript/issues/11545\n\nexport var X;\nexport as namespace N;\n",
            },
            "control": {
                "input": "const answer={value:1}",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "const answer = { value: 1 };\n",
            },
        },
        "changed": {
            "regression": {
                "input": "// issue: https://github.com/Microsoft/TypeScript/issues/11545\n\nexport var X;\nexport as namespace N",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "// issue: https://github.com/Microsoft/TypeScript/issues/11545\n\nexport var X;\nexport as namespace N;\n",
            },
            "control": {
                "input": "function answer(){return {value:1}}",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "function answer() {\n  return { value: 1 };\n}\n",
            },
        },
    },
    "prettier-1422-typescript-namespace-export": {
        "cold": {
            "regression": {
                "input": "// issue: https://github.com/Microsoft/TypeScript/issues/11545\n\nexport var X;\nexport as namespace N",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "// issue: https://github.com/Microsoft/TypeScript/issues/11545\n\nexport var X;\nexport as namespace N;\n",
            },
            "control": {
                "input": "function answer(){return 1}",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "function answer() {\n  return 1;\n}\n",
            },
        },
        "changed": {
            "regression": {
                "input": "export var before;\nexport as namespace N;\nexport var after;",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "export var before;\nexport as namespace N;\nexport var after;\n",
            },
            "control": {
                "input": "function answer(){return 1}",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "function answer() {\n  return 1;\n}\n",
            },
        },
    },
    "prettier-1306-typescript-module-reference": {
        "cold": {
            "regression": {
                "input": "import glo_m4 = require(\"glo_m4\");",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "import glo_m4 = require(\"glo_m4\");\n",
            },
            "control": {
                "input": "const answer=1",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "const answer = 1;\n",
            },
        },
        "changed": {
            "regression": {
                "input": "import glo_m4 = require(\"glo_m4\");\nvar count: number = 1;",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "import glo_m4 = require(\"glo_m4\");\nvar count: number = 1;\n",
            },
            "control": {
                "input": "const answer=1",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "const answer = 1;\n",
            },
        },
    },
    "prettier-1480-typescript-heritage": {
        "cold": {
            "regression": {
                "input": "module A {\n    export class A {\n    }\n}\n\ndeclare module \"B\" {\n    export class B {\n    }\n}\n\nclass D<T> extends C<T> {\n    bar: string;\n}",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "module A {\n  export class A {}\n}\n\ndeclare module \"B\" {\n  export class B {}\n}\n\nclass D<T> extends C<T> {\n  bar: string;\n}\n",
            },
            "control": {
                "input": "class Derived extends Base {}",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "class Derived extends Base {}\n",
            },
        },
        "changed": {
            "regression": {
                "input": "module A {\n    export class A {\n    }\n}\n\ndeclare module \"B\" {\n    export class B {\n    }\n}",
                "parser": "typescript",
                "options": {"semi": True},
                "expected": "module A {\n  export class A {}\n}\n\ndeclare module \"B\" {\n  export class B {}\n}\n",
            },
            "control": {
                "input": "class Derived extends Base {method(){return 1}}",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "class Derived extends Base {\n  method() {\n    return 1;\n  }\n}\n",
            },
        },
    },
    "prettier-2593-flow-interface-semicolon": {
        "cold": {
            "regression": {
                "input": "// @flow\n\ndeclare class C {\n  bar(n1: number, n2: number): number;\n  bar(s1: string, s2: string): string;\n}\n\nfunction foo(c: C, x: any): string {\n  let y = x.y;\n  return c.bar(0, y); // should be able to select first case and error\n}\n\nvar any_fun1 = require('./nonflowfile');\nfunction bar1(x: mixed) {\n  if (any_fun1(x)) {\n    (x: boolean);\n  }\n}\n\nvar any_fun2 = require('./anyexportflowfile');\nfunction bar2(x: mixed) {\n  if (any_fun2(x)) {\n    (x: boolean);\n  }\n}",
                "parser": "flow",
                "options": {"semi": True},
                "expected": "// @flow\n\ndeclare class C {\n  bar(n1: number, n2: number): number;\n  bar(s1: string, s2: string): string;\n}\n\nfunction foo(c: C, x: any): string {\n  let y = x.y;\n  return c.bar(0, y); // should be able to select first case and error\n}\n\nvar any_fun1 = require(\"./nonflowfile\");\nfunction bar1(x: mixed) {\n  if (any_fun1(x)) {\n    (x: boolean);\n  }\n}\n\nvar any_fun2 = require(\"./anyexportflowfile\");\nfunction bar2(x: mixed) {\n  if (any_fun2(x)) {\n    (x: boolean);\n  }\n}\n",
            },
            "control": {
                "input": "const answer=1",
                "parser": "babylon",
                "options": {"semi": True},
                "expected": "const answer = 1;\n",
            },
        },
        "changed": {
            "regression": {
                "input": "// @flow\ndeclare class Pair { left(): string; right(): number; }",
                "parser": "flow",
                "options": {"semi": False},
                "expected": "// @flow\ndeclare class Pair { left(): string; right(): number }\n",
            },
            "control": {
                "input": "const answer=1",
                "parser": "babylon",
                "options": {"semi": False},
                "expected": "const answer = 1\n",
            },
        },
    },
}


# Self-contained checker, installed only in the controller's checking image.
_CHECKER_SCRIPT = r'''"use strict";
const crypto = require("crypto");
const Module = require("module");
const path = require("path");
process.env.NODE_PATH = ["/opt/goal-native-deps/prettier-1-2-2/node_modules", "/opt/goal-native-deps/prettier-1-3-0/node_modules", "/opt/goal-native-deps/prettier-1-7-0/node_modules", process.env.NODE_PATH].filter(Boolean).join(path.delimiter);
Module._initPaths();

const taskId = process.argv[2];
const phase = process.argv[3];
const cases = __CASES__;
const result = {
  schema: "goal-native-typescript-check/v1",
  task_id: taskId,
  phase,
  status: "failed",
  checks: [],
};

function bounded(value) {
  return String(value || "").replace(/\x00/g, "").slice(0, 512);
}
function digest(value) {
  return crypto.createHash("sha256").update(String(value), "utf8").digest("hex");
}
function observed(text) {
  return { length: text.length, sha256: digest(text) };
}
function add(name, passed, details) {
  result.checks.push({ name, passed: passed === true, ...(details || {}) });
}
function syntax(text, parser) {
  try {
    const ts = require("typescript");
    if (parser === "typescript") {
      const source = ts.createSourceFile("observed.ts", text, ts.ScriptTarget.Latest, true);
      return { valid: Array.isArray(source.parseDiagnostics) && source.parseDiagnostics.length === 0 };
    }
    if (parser === "flow") {
      const source = require("flow-parser").parse(text, { all: true });
      return { valid: Array.isArray(source.errors) && source.errors.length === 0 };
    }
    require("babylon").parse(text, { sourceType: "module", plugins: ["jsx", "flow", "classProperties"] });
    return { valid: true };
  } catch (error) {
    return { valid: false, error: bounded(error && error.message) };
  }
}
function runCase(kind, casePhase, item, prettier) {
  const label = kind + "_" + casePhase;
  let output;
  try {
    output = prettier.format(item.input, { parser: item.parser, ...item.options });
  } catch (error) {
    add(label + "_output", false, { error: bounded(error && error.message) });
    add(label + "_syntax", false, { error: "formatter did not return output" });
    return;
  }
  const info = observed(output);
  add(label + "_output", output === item.expected, { observed: info });
  const parsed = syntax(output, item.parser);
  add(label + "_syntax", parsed.valid, parsed.error ? { error: parsed.error } : { observed: info });
}

try {
  if (!Object.prototype.hasOwnProperty.call(cases, taskId)) throw new Error("unknown TypeScript task");
  if (phase !== "cold" && phase !== "changed") throw new Error("phase must be cold or changed");
  const prettier = require(path.resolve(process.cwd(), "index.js"));
  for (const casePhase of phase === "cold" ? ["cold"] : ["cold", "changed"]) {
    for (const kind of ["regression", "control"]) {
      const item = cases[taskId][casePhase][kind];
      if (casePhase === "changed" && JSON.stringify(item) === JSON.stringify(cases[taskId].cold[kind])) continue;
      runCase(kind, casePhase, item, prettier);
    }
  }
  result.status = result.checks.every((check) => check.passed) ? "passed" : "failed";
} catch (error) {
  add("checker_execution", false, { error: bounded(error && error.message) });
}
process.stdout.write(JSON.stringify(result));
'''.replace("__CASES__", json.dumps(_CASES, sort_keys=True, separators=(",", ":")))


def _validate_task(task: Mapping[str, Any]) -> dict[str, Any]:
    task_id = task.get("id")
    if not isinstance(task_id, str) or task_id not in _TASKS:
        raise ValueError("task is not one of the five frozen Prettier TypeScript tasks")
    expected = _TASKS[task_id]
    for key in ("repository", "title", "provenance"):
        if task.get(key) != expected.get(key):
            raise ValueError(f"task {task_id} is not the frozen provenance record")
    provenance = task["provenance"]
    if not _COMMIT_RE.fullmatch(provenance["source_commit"]) or not _COMMIT_RE.fullmatch(provenance["fix_commit"]):
        raise ValueError(f"task {task_id} has invalid commit provenance")
    return dict(expected)



def _manifest_requirements(task_id: str) -> tuple[str, Mapping[str, str]]:
    return _DEPENDENCY_GROUPS[task_id]


def _verify_checkout(task_id: str, source_dir: Path, reference_dir: Path) -> dict[str, Any]:
    """Check the downloaded trees, including the exact parser/dependency markers."""
    version, expected_dependencies = _manifest_requirements(task_id)
    observed: dict[str, Any] = {"package_version": version, "required_fix_markers": []}
    for label, checkout in (("source", source_dir), ("reference", reference_dir)):
        manifest_path = checkout / "package.json"
        if not manifest_path.is_file():
            raise ValueError(f"{task_id} {label} checkout has no package.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != version:
            raise ValueError(f"{task_id} {label} package version is not {version}")
        production_dependencies = manifest.get("dependencies")
        dev_dependencies = manifest.get("devDependencies")
        if not isinstance(production_dependencies, Mapping) or not isinstance(dev_dependencies, Mapping):
            raise ValueError(f"{task_id} {label} production/development dependencies are missing")
        actual = {**dev_dependencies, **production_dependencies}
        for name, expected in expected_dependencies.items():
            if actual.get(name) != expected:
                raise ValueError(f"{task_id} {label} dependency {name} is not pinned as expected")
        if label == "reference":
            printer = (checkout / "src" / "printer.js").read_text(encoding="utf-8")
            if task_id == "prettier-13-typescript-parser":
                required = (checkout / "src" / "typescript-ast-nodes.js").is_file()
            elif task_id == "prettier-1422-typescript-namespace-export":
                required = "TSNamespaceExportDeclaration" in printer
            elif task_id == "prettier-1306-typescript-module-reference":
                required = "TSImportEqualsDeclaration" in printer and "TSExternalModuleReference" in printer
            elif task_id == "prettier-1480-typescript-heritage":
                required = "TSHeritageClause" in printer and "TSExpressionWithTypeArguments" in printer
            else:
                required = "isFlowInterfaceLikeBody" in printer and "const separator =" in printer
            if not required:
                raise ValueError(f"{task_id} fix commit does not contain its required printer/parser implementation")
    observed["required_fix_markers"].append(task_id)
    return observed


def _expected_observations(task_id: str) -> dict[str, Any]:
    return {
        "schema": CHECK_SCHEMA,
        "status_values": ["passed", "failed"],
        "phase_values": list(PHASES),
        "checks": [
            {"name": "regression_cold_output", "kind": "exact_post_fix_text"},
            {"name": "regression_cold_syntax", "kind": "independent_parser"},
            {"name": "control_cold_output", "kind": "exact_control_text"},
            {"name": "control_cold_syntax", "kind": "independent_parser"},
            {"name": "regression_changed_output", "kind": "exact_post_fix_text"},
            {"name": "regression_changed_syntax", "kind": "independent_parser"},
            {"name": "control_changed_output", "kind": "exact_control_text"},
            {"name": "control_changed_syntax", "kind": "independent_parser"},
        ],
        "bounded": {"max_checks": 8, "max_error_chars": 512, "outputs_are_digests_only": True},
        "task_id": task_id,
    }


def prepare(task: dict[str, Any], root: Path) -> dict[str, Any]:
    """Prepare the same immutable corpus contract as the Python tasks."""
    frozen = _validate_task(task)
    prepared = prepare_sources(frozen, root, license_path="LICENSE")
    verification = _verify_checkout(frozen["id"], prepared["source_dir"], prepared["reference_dir"])
    content = _CHECKER_SCRIPT.encode("utf-8")
    checker = Path(root).expanduser().resolve() / "checker" / ("typescript_checker-" + hashlib.sha256(content).hexdigest() + ".cjs")
    checker.parent.mkdir(parents=True, exist_ok=True)
    if checker.is_symlink():
        raise ValueError("controller checker must not be a symlink")
    if checker.exists():
        if not checker.is_file() or checker.read_bytes() != content:
            raise ValueError("controller checker bytes changed")
    else:
        with checker.open("xb") as output:
            output.write(content)
    return {
        **prepared, "schema": SCHEMA,
        "provenance": {**prepared["provenance"], "checkout_verification": verification},
        "checker_path": checker,
        "setup_commands": [_setup_command(frozen["id"])],
        "expected_observation_schema": _expected_observations(frozen["id"]),
        "measurement_limitations": [
            "prettier-13, prettier-1422, and prettier-1306 share the same pre-PR source and overlap; they are not independent repository samples",
        ],
    }


def check_argv(prepared: Mapping[str, Any], phase: str, **_identities: str) -> list[str]:
    if phase not in PHASES:
        raise ValueError("checker phase must be cold or changed")
    return ["node", "/opt/goal-native-checker/checker.cjs", prepared["task_id"], phase]



__all__ = ["CHECK_SCHEMA", "PHASES", "SCHEMA", "check_argv", "prepare"]
