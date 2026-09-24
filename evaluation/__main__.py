"""Command line entry point for the Goal-Native lifecycle evaluator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .evaluator import (
    EvaluationError,
    analyze_run,
    execute_run,
    parse_runner_specs,
    require_external_output,
    smoke_rejection_checks,
)
from .workloads import ARMS, SCENARIOS, registration_document, registration_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evaluation", description="Run the preregistered Goal-Native lifecycle evaluation.")
    subparsers = parser.add_subparsers(dest="command")

    register = subparsers.add_parser("register", help="write the frozen registration outside the repository")
    register.add_argument("--out", required=True, help="new JSON file outside the repository")

    plan = subparsers.add_parser("plan", help="print the frozen matrix without contacting a provider")
    plan.add_argument("--scenario", action="append", dest="scenarios", help="limit the displayed plan; does not change registration")

    run = subparsers.add_parser("run", help="invoke configured real Worker API runners")
    run.add_argument("--output", required=True, help="new run directory outside the repository")
    run.add_argument("--runner", action="append", required=True, help="ARM=COMMAND; command reads one JSON request and writes one JSON response")
    run.add_argument("--model", required=True, help="explicit provider model passed to every arm")
    run.add_argument("--api-key-env", required=True, help="name of the credential environment variable, or NONE for an explicitly local runner")
    run.add_argument("--tool-profile", required=True, help="shared tool profile identifier")
    run.add_argument("--safety-profile", required=True, help="shared safety profile identifier")
    run.add_argument("--max-cost-usd", required=True, type=float, help="hard parent-supplied cost bound for this run")
    run.add_argument("--timeout-seconds", type=float, default=300.0)
    run.add_argument("--scenario", action="append", dest="scenarios", help="run a subset; analysis remains non-claimable until complete")

    analyze = subparsers.add_parser("analyze", help="analyze a preserved run and reject incomplete or unfair data")
    analyze.add_argument("run_dir")

    subparsers.add_parser("smoke", help="exercise reporting safeguards without a provider")
    subparsers.add_parser("canonical-worker", help="invoke canonical goal_native.store.Store and goal_native.worker.Worker")
    return parser


def _plan(scenarios: Optional[Sequence[str]]) -> Dict[str, Any]:
    registration = registration_document()
    selected = set(scenarios or [scenario["id"] for scenario in SCENARIOS])
    known = {scenario["id"] for scenario in SCENARIOS}
    unknown = selected - known
    if unknown:
        raise EvaluationError("unknown scenario(s): " + ", ".join(sorted(unknown)))
    cells = 0
    for arm in ARMS:
        for scenario in SCENARIOS:
            if scenario["id"] in selected:
                cells += int(scenario["concurrent_calls"]) * len(scenario["phases"]) * registration["repetitions"]
    return {
        "registration_sha256": registration["registration_sha256"],
        "arms": [arm["id"] for arm in ARMS],
        "scenarios": sorted(selected),
        "repetitions": registration["repetitions"],
        "expected_cells": cells,
        "paid_execution": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "canonical-worker":
            from .canonical_worker import main as canonical_worker_main

            return canonical_worker_main()
        if args.command == "register":
            destination = require_external_output(Path(args.out))
            if destination.exists():
                raise EvaluationError("registration output already exists: %s" % destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(registration_json(), encoding="utf-8")
            print(str(destination))
            return 0
        if args.command == "plan":
            print(json.dumps(_plan(args.scenarios), sort_keys=True, indent=2))
            return 0
        if args.command == "run":
            runners = parse_runner_specs(args.runner)
            output = execute_run(
                output=Path(args.output),
                runners=runners,
                model=args.model,
                api_key_env=args.api_key_env,
                tool_profile=args.tool_profile,
                safety_profile=args.safety_profile,
                max_cost_usd=args.max_cost_usd,
                timeout_seconds=args.timeout_seconds,
                selected_scenarios=args.scenarios,
            )
            print(str(output))
            return 0
        if args.command == "analyze":
            report = analyze_run(Path(args.run_dir))
            print(json.dumps(report, sort_keys=True, indent=2))
            return 0 if report["claim_basis"]["claimable"] else 2
        if args.command == "smoke":
            result = smoke_rejection_checks()
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0 if all(result.values()) else 1
        raise EvaluationError("a command is required")
    except EvaluationError as exc:
        print("evaluation error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
