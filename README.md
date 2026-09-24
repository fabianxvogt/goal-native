# Goal Native

**Persistent intent. Reusable work. Controlled effects.**

A session-first, local AI CLI. Open it and type a request; durable goals, changing requirements and reusable work are handled underneath. An interrupted task can leave useful work behind without silently granting an old agent permission to act.

**Status: session-first terminal with live Luna execution and follow-up continuation exercised.** No manual goal creation is needed. This implements the owner-supplied *Goal-Native, Incremental AI Harness* proposal without claiming a quality, reliability or token-efficiency improvement. [Observed verification](docs/VERIFICATION.md) retains both a failed follow-up and its context-precedence repair. The [acceptance contract](docs/CONTRACT.md) and [evaluation protocol](docs/EVALUATION.md) govern broader claims.

## Run locally

Python 3.11+ and Node.js 22.19+. The controller uses Python's standard library; agent execution builds and reuses the pinned pi source checkout. Clone with `--recurse-submodules`, then:

```sh
git submodule update --init --recursive
npm run bootstrap:upstream
python3 -m goal_native login
python3 -m goal_native
```
If the system Python is older, use the project environment (for example
`.venv/bin/python`) or `uv run --no-project --python 3.12 python -m goal_native`.

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
- `/status`: inspect the backing goal, last recorded run and current local stage.
- `/help`, `/exit`: commands and exit. Ctrl-C stops an active run and returns
  to the prompt; at the prompt it clears input. End a line with `\` for multiline input.

`chat` explicitly opens the same interface. `--model`, `--state`, `--source-dir`,
`--context-budget` and the existing worker limits also work with the bare command
or after `chat`. Source-checkout installations expose the same entry point as
`goal-native` (for example `.venv/bin/goal-native`).

No background planning agent, daemon or extra goal-generation model call:
one session maps to one existing durable goal. Runs stay draft-only; sending
another request also revokes any prior effect grant. Opening a session does not
run, approve or resume paused work until you send a request.

Files carry forward into a **fresh isolated stage** between requests in the same
CLI process. After restarting, saved requests/artifacts remain available, but
local files need explicit `--source-dir` or artifact selection. Imported run
receipts never automatically select host directories. The CLI does not read
your current directory unless selected. Responses currently appear at the end
of a run; streaming/progress is the next UI improvement.

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

Select an artifact ID from the summary, or pass `--source-dir` with a previously
retained stage directory. Resuming always creates a new isolated stage; it does
not silently reuse a mutable directory. Durable artifacts and current requests
also feed the context compiler.

The summary separates `goal_status`, individual invocations, and `recorded_runs`.
A finished invocation is one completed provider turn, not necessarily a
successful whole run or accepted goal. New CLI run receipts retain the returned
status, diagnostic, stage location and selected budgets. Older runs, Ctrl-C,
abrupt termination and failures before the first invocation may have no run receipt;
missing records are not inferred successes.

`matches_current_contract` compares revision/input/authority versions only,
not assignment liveness or acceptance. Historical artifacts retain their trust,
limitations and versions. Summary output omits provider traces, context
artifacts and artifact bodies, but tool receipts can contain source/output:
**it is not a redacted or fixed-size export**. Full `show` retains raw traces.

`--context-budget` applies to interactive sessions, `run`, `resume` and `ask`; default **16384** is
unchanged. It is a conservative UTF-8-byte token ceiling including reserves,
not actual billed tokens. Explicitly raising it permits a larger request;
model-window checks, round/time limits and mandatory-context admission remain.
The live continuation check needed **32768** after exhausting the default.
No silent truncation, automatic retry or Codex output-token cap is introduced.

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
