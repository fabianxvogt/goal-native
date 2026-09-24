# Goal Native

**Persistent intent. Reusable work. Controlled effects.**

A session-first, local AI CLI. Open it and type a request; durable goals, changing requirements and reusable work are handled underneath. An interrupted task can leave useful work behind without silently granting an old agent permission to act.

**Status: budget-stop recovery, reviewed patch/file export, and a source bootstrap with `./goal`.** No manual goal creation is needed. This implements the owner-supplied *Goal-Native, Incremental AI Harness* proposal without claiming a quality, reliability or token-efficiency improvement. [Observed verification](docs/VERIFICATION.md) separates actual coding journeys from synthetic protocol fixtures and records failures/repairs. The [acceptance contract](docs/CONTRACT.md) and [evaluation protocol](docs/EVALUATION.md) govern broader claims.

## Run locally

Python 3.11+, Git and Node.js 22.19+ with npm. The exercised code-execution
platform is macOS. Clone this repository, then:

```sh
python3 scripts/bootstrap.py
./goal doctor --model gpt-6-luna
./goal login
./goal
```

Bootstrap initializes and checks the exact pinned pi revision, builds its runtime,
creates `.venv`, and installs the Python entry point. It downloads npm dependencies
and upstream model-catalog metadata, but performs no login or model call. It refuses
to reset a dirty/unpinned pi checkout or replace an
incomplete existing environment. Follow its remediation instead of deleting
user work. An older system Python needs an explicitly installed Python 3.11+
(for example `python3.12 scripts/bootstrap.py`).

`doctor` separates runtime prerequisites from credentials and reports missing
steps without printing tokens or calling a model. Before login, credential
readiness is expected to be false. Pi refreshes expired OAuth access tokens on
the next explicit run. `./goal` works from any caller directory using the
checkout's environment; relative `--state` and source paths remain caller-relative.

### Just start a session

The bare command opens a fresh prompt using **Codex / gpt-6-luna**. There is no
goal form, goal ID or model call before the first request. Type what you want;
the CLI saves the exact request, creates the backing goal and runs it. Follow-up
requests stay in the same session and take precedence over conflicting older
requests, while compatible constraints remain in context.

```text
Goal Native  /  gpt-6-luna  /  openai-codex
New session. Just type a request; goals are saved automatically.

> Write a small Python script that totals these numbers…
```

- `/new`: start fresh on the next request; it does not create empty records.
- `/sessions`, then `/resume 1`: reopen saved work without running it yet.
- `/status`: inspect goal state, last stop reason, recorded rounds and context headroom.
- `/budget [N]`: inspect or explicitly set the session's context ceiling; no model call.
- `/continue [--context-budget N --max-rounds N --max-time SECONDS]`: rerun the
  latest request from retained files, without inventing another request. Overrides
  apply to that run; `/budget` changes subsequent runs in the current CLI process.
- `/help`, `/exit`: commands and exit. Ctrl-C stops an active run and returns
  to the prompt; at the prompt it clears input. End a line with `\` for multiline input.

`chat` explicitly opens the same interface. `--model`, `--state`, `--source-dir`,
`--fresh`, `--context-budget` and the existing worker limits work with the bare command
or after `chat`. Source-checkout installations expose the same entry point as
`goal-native` (for example `.venv/bin/goal-native`).

No background planning agent, daemon or extra goal-generation model call:
one session maps to one existing durable goal. Runs stay draft-only; sending
another request also revokes any prior effect grant. Opening a session does not
run, approve or resume paused work until you send a request or explicitly `/continue`.

Assistant replies stream as they arrive. One compact terminal line shows current
tool activity; thinking blocks, raw tool results and traces stay out of the
default view. A completed response is not printed twice.

Files carry forward into a **fresh isolated stage**, including after restarting
the CLI. `/resume` selects the session; a request or `/continue` restores its recorded
local files. Context-budget rejection before the first provider invocation also
retains a stopped run and recovery reference. Recovery metadata stays in this
workspace's Store, outside portable exports. Imported records never select host directories.

Use `--source-dir` or an artifact option to override the initial files, or
`--fresh` to start with empty files while keeping saved requests/artifacts.
In chat these options seed each selected session once; later requests carry its
new work forward. Missing or symlinked recorded files stop with recovery guidance,
not a silent empty workspace. Runs from before this feature, imported workspaces,
and abrupt termination without a recorded local stage still require explicit
file selection. The CLI does not read your current directory unless selected.

### Automation and advanced controls

Explicit lifecycle commands still emit one JSON value on stdout, including
errors. They are optional controls, not onboarding steps. `serve` retains its
startup line. Options may be placed after the command, including `--state`:

```sh
python3 -m goal_native create --state .state "Prepare a local report"
python3 -m goal_native request --state .state GOAL_ID "Keep the work local" --control draft
python3 -m goal_native revise --state .state GOAL_ID --expected-revision 1 \
  --outcome "Prepare the revised local report"
python3 -m goal_native artifacts add --state .state GOAL_ID \
  --text "Human-provided source note" --name source-note
python3 -m goal_native artifacts list --state .state GOAL_ID
python3 -m goal_native run --state .state --model gpt-6-luna GOAL_ID
python3 -m goal_native export --state .state --output workspace.json
python3 -m goal_native import --state restored-state workspace.json
```

`run` and `resume` default to **Codex subscription authentication** and require
an explicit model. Run `login` once; pi owns OAuth and automatic token refresh.
Each run gets a unique retained staged capability directory under
`.state/stages/<goal>/run-*`; the JSON result includes `stage_dir` for
recovery. Use `--source-dir` for one deliberate local directory snapshot, or
`--artifact`/`--artifact-name` to stage one named text artifact. Symlinks,
credential/controller names, and secret suffixes are excluded; the selected
source is never executed directly by the controller.
Exclusions are name-based, not a secret scanner: review selected files before
sending their contents to a provider.

### Inspect and continue work

```sh
python3 -m goal_native show GOAL_ID --summary
python3 -m goal_native request GOAL_ID "Update the calculation for 20 items"
python3 -m goal_native resume GOAL_ID --model gpt-6-luna \
  --artifact-id ARTIFACT_ID --artifact-path calculate.py --context-budget 32768
```

Without a file option, `run`/`resume` recover recorded local session files.
Override that selection with an artifact ID from the summary or `--source-dir`;
`--fresh` skips file recovery. Resuming always copies into a new isolated stage,
never shares the old writable directory. Durable artifacts and current requests
also feed the context compiler.

The summary separates `goal_status`, individual invocations, durable
`run_attempts`/`latest_run`, and older `recorded_runs` receipts. A finished
invocation is one provider turn, not necessarily a successful whole run or
accepted goal. Run attempts start before context compilation and preserve
assistant text separately from `stop_reason` and `diagnostic`, including a
zero-invocation budget stop. Last admission estimates and selected limits survive
reopening and workspace export; unknown round counts remain unknown.
Abrupt termination may leave an unfinished record, never an inferred success.

`matches_current_contract` compares revision/input/authority versions only,
not assignment liveness or acceptance. Historical artifacts retain their trust,
limitations and versions. Summary output omits provider traces, context
artifacts and artifact bodies, but tool receipts can contain source/output:
**it is not a redacted or fixed-size export**. Full `show` retains raw traces.

`--context-budget` applies to interactive sessions, `run`, `resume` and `ask`; default **16384** is
unchanged. It is a conservative UTF-8-byte token ceiling including reserves,
not actual billed tokens. Explicitly raising it permits a larger request;
model-window checks, round/time limits and mandatory-context admission remain.
Use `/budget` or `/continue --context-budget N` after inspecting the stop;
no automatic budget expansion, retry, constraint truncation or Codex output cap
is introduced. Reopening the CLI uses its selected/default limits, not a silent
restoration of a previous higher ceiling.

### Review and export code

Select a project explicitly; inspect the selection without a model call:

```sh
python3 -m goal_native source-preview /path/to/project
python3 -m goal_native --source-dir /path/to/project --context-budget 32768
```

Source selection honors nested `.gitignore` rules using Git, excludes common
dependency/build directories and credential/controller names, and lists every
omission. It is not a secret scanner. `/files` shows selected files and exclusions;
`/changes` lists changes against the immutable initial selected snapshot.
Ignored inputs never become proposed deletions.

In a session, use `/diff`, then `/export /path/outside/project/change.patch`.
The export must still match the reviewed bytes, goal contract and observed source
state. New edits require another `/diff`. The destination must not already exist
and must be outside the selected source and controller state. No command applies
changes to your project.

For scripts and automation:

```sh
python3 -m goal_native diff GOAL_ID
python3 -m goal_native export-code GOAL_ID --review REVIEW_ID --output /tmp/change.patch
git -C /path/to/matching-checkout apply --check /tmp/change.patch
```

`REVIEW_ID` comes from `diff`. Source divergence is reported, not silently
overwritten; an export still targets its recorded baseline. Review and apply
the patch yourself, then run the project's checks. Export is not acceptance.
Binary changes require `--format files` (also supported by `/export`): a ZIP
with the complete selected candidate under `files/` and a hash/deletion manifest.
It contains no session/provider traces or host recovery paths.

The existing `export` command remains a separate workspace-history archive.
Local baselines and stage recovery references are not imported from it.
Pre-feature stages need one continuation to establish a new baseline; that
checkpoint cannot reconstruct earlier edits.

The optional browser interface still exposes the older goal-centered controls; it has not received the session-first redesign:

```sh
python3 -m goal_native serve --state .state --port 8765
```

Open the loopback URL printed by the server. The UI is local; it is not a shared public service.

### Codex subscription login

Codex is the first subscription target; other subscription providers are
deferred. `login` prints pi's ChatGPT OAuth URL for you to open in your browser.
For a remote terminal use `login --method device_code`, subject to your account's device
login policy. `auth-status` shows non-secret configuration metadata;
`logout` removes only the Codex entry.

Credentials default to `~/.config/goal-native/auth.json`, outside workspace
state and exports. To deliberately reuse an existing pi OAuth store, pass
`--auth-file /path/to/auth.json` to login/status/run. Do not put that file in
the workspace or selected source directory. Account authorization requires
your browser/device confirmation; catalog membership does not guarantee
your subscription's access to a model.

There is **no fallback to API billing**. The separate, explicit
`--provider openai --model gpt-4.1-mini` path uses `OPENAI_API_KEY`;
`OPENAI_BASE_URL` applies only to that API path, never Codex subscriptions.
Task content goes to the selected provider when a worker starts. Credentials
are not passed to task subprocesses or included in workspace exports.
Missing configuration is an error, never a scripted-response fallback.
Pi's Codex endpoint does not expose a request output-token cap. Input
admission, round limits and cooperative timeouts apply, but the configured
output reserve is not an enforced Codex generation limit.

Arbitrary code runs only through the restricted execution adapter. Unsupported or unavailable isolation fails closed; it never falls back to ordinary privileged host execution. The exact exercised boundary and limits belong in verification evidence, not an assertion of perfect sandboxing.

The currently exercised execution target is **macOS, staged Python only**:
no shell, subprocesses, forks or network. Each run owns a separate stage.
Sibling/package imports and paths relative to `__file__` work inside the selected
project. The isolated interpreter reads the entry script through its pinned file
descriptor, with only its staged script directory and project root added to
module search; it does not inherit the controller's `PYTHONPATH`.
This is not a VM: stage admission limits are not runtime disk/memory quotas,
and the controller, interpreter installation and host user remain trusted.
See the [sandbox review](docs/REVIEW-SANDBOX.md) for the exact boundary.

Run from the source checkout. A wheel alone does not contain the pinned
Node/pi runtime and is not a supported standalone distribution.

## What the system separates

- **Goals and revisions:** stable identities, faithful requests, changing requirements and explicit controls.
- **Assignments and invocations:** responsibility and the exact input/revision/authority actually supplied to each invocation.
- **Work products:** immutable content, producer, input relationships and expressed limitations. Old work can remain useful without becoming current evidence.
- **Observations and evidence:** tool output is not automatic proof; a worker assertion is not a trusted acceptance check.
- **Acceptance and effects:** exact candidate, current contract, human approval and expected destination state checked at the controlled boundary.

The first effect adapter is a **local mock application**, not a production publisher. It supports operation identity, destination compare-and-set, lost-response uncertainty and reconciliation. Approval and acceptance are human/controller operations, not tools the model can grant itself.

## Persistence and privacy

Each state directory is an independent workspace containing SQLite records and content-addressed artifacts. Use JSON export to retain a portable copy; imports require an empty workspace and cannot revive executable authority. Exports contain task content and traces, so review them before sharing. No production credentials, personal workspaces or private datasets ship with the repository.
Workspace export format 2 includes run attempts without local stage references;
imports also accept format 1 archives. Local file snapshots require a separate
reviewed code export.

The worker's ordinary artifacts and optional findings provide continuation. The controller never invents a root cause, limitation or next plan that no producer expressed. Search results retain their declared scope; a negative match does not certify the absence of all callers.

## Verification and evaluation

```sh
python3 -m unittest discover -s tests -v
python3 -m evaluation --help
```

Correctness scenarios and mocked transport tests are **not** token-performance evidence. The full claim requires a frozen, cold-to-continuation workload; equivalent model settings, tools and safety; a real strong baseline; independent quality assessment; and complete usage including failures, capture, retries and reassessment. Provider access and baseline availability are explicit prerequisites.

## Project map

| Location | Responsibility |
| --- | --- |
| `goal_native/store.py` | Durable domain state, immutable products, exact acceptance and mock effect gateway |
| `goal_native/context.py` | Bounded faithful assignment/context compilation |
| `bridge/` | Pinned pi agent-core/provider integration, streaming and loop hooks |
| `goal_native/worker.py` | Controller bridge, invocation capture and restricted tool dispatch |
| `goal_native/sandbox.py` | Restricted staged execution |
| `goal_native/cli.py` | Session-first terminal and JSON lifecycle commands over the canonical Store/Worker |
| `goal_native/server.py`, `web/` | Optional localhost API and goal-centered browser interface |
| `evaluation/`, `tests/` | Lifecycle evaluation and behavioral boundaries |

[Runtime API](docs/API.md) · [Documentation](docs/README.md) · [Roadmap](ROADMAP.md) · [Source](https://github.com/fabianxvogt/goal-native)

MIT licensed. General-purpose intent does not imply unrestricted computer autonomy or production effect adapters.
