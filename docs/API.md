# Runtime API contract

All returned objects JSON-compatible dictionaries/lists. IDs strings, timestamps UTC. `Store(root)` creates a state directory and SQLite database containing content-addressed immutable artifacts. Store owns one workspace. Methods below are the integration contract; optional extra fields are permitted. All IDs checked for workspace ownership (one DB). Store is a trusted controller API, never exposed as an arbitrary model/plugin object; untrusted task code runs outside the controller's OS boundary.

- `create_goal(outcome, parent_id=None, kind='outcome', criteria='', constraints='') -> goal` with id, outcome, revision, input_version, authority_version, status, kind, parent_id. Preserve original request.
- `list_goals() -> list[goal]`; `goal(goal_id) -> goal` enriched with requests, artifacts, invocations, runs, effects, events, acceptances.
- `request(goal_id, text, control=None) -> goal`: immediately increments input_version; control one of pause/cancel/draft/resume or None. Never model-authorized. Default draft-only authority; explicit user `control='allow_effects'` grants mock destination only.
- `revise(goal_id, expected_revision, outcome, criteria, constraints) -> goal`: trusted human operation; CAS revision; request/input fence included.
- `assign(goal_id) -> assignment`: fences predecessor assignment, fields id, goal_id, authority_version. Refuse paused/cancelled.
- `invoke(goal_id, assignment_id, context, expected_input_version=None) -> invocation`: snapshot revision/input/authority and context artifact; optional expected input CAS. fields id, goal_id, assignment_id, input_version, revision, authority_version.
- `artifact(goal_id, invocation_id, kind, content, name='', inputs=None, limitations='', trust='worker') -> artifact`: content str; immutable hash/id, goal_id, invocation_id, kind, name, content, inputs, limitations, trust. Input IDs must exist in same workspace; invocation bound to goal. No implicit acceptance. Source trust cannot become policy.
- `artifacts(goal_id) -> list[artifact]` including content, newest last. Old artifacts usable but applicability uncertain after revision/input change; qualifications retained.
- `receipt(invocation_id, tool, parameters, result, note='') -> receipt`: preserves full result, producer and exact parameters, note explicitly worker assertion.
- `usage(invocation_id, usage_dict)`: record provider raw and normalized usage without reasoning/cache double count; unknown remains null, not zero.
- `finish(invocation_id, status, result='')`: status finished/failed/cancelled/interrupted; no acceptance implied.
- `start_run(goal_id, assignment_id, limits) -> run`: durable attempt before context compilation, bound to an active assignment; no invocation required.
- `record_run_admission(run_id, admission)` / `bind_run_invocation(run_id, invocation_id)`: retain controller admission estimates and the exact assignment's invocation.
- `finish_run(run_id, status, *, stop_reason=None, diagnostic=None, assistant_text='', admission=None, invocation_id=None, rounds=None) -> run`: persist the whole-run outcome independently of assistant text; unknown rounds remain null.
- `run_attempts(goal_id) -> list[run]`: ordered durable run history.
- `reopen_for_run(goal_id) -> goal`: trusted explicit draft-only continuation, without appending request text; cancelled goals cannot reopen.
- `pause_for_interruption(goal_id, reason='run interrupted') -> goal`: durable pause/authority fence without manufacturing a user request.
- `local_stage(goal_id) -> str | None`: controller-only local run-directory basename, not a host path or portable artifact.
- `remember_local_stage(assignment_id, stage_name) -> bool`: record stopped work after cleanup, including an attempt with zero invocations. Reject running attempts/invocations and path-valued names; return false for replaced assignments or no stopped work. One assignment-bound recovery table replaces the old invocation-only schema atomically. Local recovery metadata stays out of exports/imports.
- `remember_workspace_baseline(goal_id, stage_name, files, selection)`: controller-only immutable input snapshot binding. Content-addressed snapshot rows are shared across continued stages; the binding cannot be replaced with different bytes/selection.
- `workspace_baseline(goal_id, stage_name) -> dict | None`: local selected bytes and exclusion/source metadata. Neither these tables nor host source paths are accepted from imports.
- `verify(invocation_id, artifact_id, check, passed, details, trusted=False) -> evidence`: bound immutable candidate; trusted only callable by controller/human, NEVER worker dispatch.
- `prepare_effect(invocation_id, artifact_id, target, expected_version, evidence_id) -> effect`: mock record replacement, target string, uses exact candidate content. Require trusted passing current-candidate evidence; policy validates again at commitment. State prepared, id immutable.
- `approve(effect_id) -> effect`: human exact-proposal approval; MUST revalidate original invocation freshness, authority, evidence/candidate/destination before approval. No input increment for unchanged approval.
- `commit(effect_id, lose_response=False) -> effect`: transaction checks all fences and destination CAS, writes local mock application and operation identity atomically. With loss, effect state unresolved but destination operation exists; `reconcile(effect_id)` queries identity and confirms without retry. Repeated commit deduplicated.
- `effects(goal_id) -> list[effect]`; `destination(target) -> {target,version,content,operation_id}` (new version=0).
- `accept(goal_id, artifact_id, evidence_id, expected_revision) -> acceptance`: trusted human, exact candidate, current revision and adequate trusted evidence. Never autoaccept from worker finish.
- `export() -> dict`: format 2 consistent-snapshot workspace export of durable records and artifact content, including run attempts but excluding local recovery/baseline tables and run stage names. `Store.import_data(root, data)` accepts formats 1/2 into EMPTY state only; preserves validated provenance/hashes, fences authority and marks imported unfinished runs interrupted. Imported delivery records remain unverified historical claims, never confirmed local effects. Task content/traces require review before sharing.

# Worker integration

`Worker(store, model, api_key=None, max_rounds=12, sandbox=None, *, provider="openai-codex", auth_file=None, ...)`; `run(goal_id) -> dict` creates an assignment and supervises the pinned pi agent/AI runtime. Codex requires an explicit controller-only OAuth store path and rejects an API key. The legacy API profile requires `provider="openai"`; there is no billing fallback. Pi owns streaming, OAuth refresh and tool-loop mechanics; the controller owns context admission, invocation/usage/receipt capture and restricted tool execution. `cancel()` interrupts transport/task subprocesses; durable authority changes belong to Store controls. Each thread owns its Store connection.

Optional `on_event(invocation_id, event)` receives admitted pi agent events after
their receipt and applicable usage/finish records are persisted. It is a
trusted controller callback, not a worker tool. The terminal renders text
blocks/deltas and fixed tool labels, not thinking blocks or raw tool bodies.
JSON execution commands attach no terminal display.
Read-only `last_assignment_id` survives run cleanup for local recovery, including
rejection before the first provider invocation. It resets when the next run
starts and does not authorize tools or effects.

Supported tools: staged file discovery, range reads, writes, hash-bound edits, literal/regex search, execution, artifact/receipt retrieval, save_artifact and optional finding. No approve/verify trusted/commit/authority tools. Worker may prepare a candidate, not authorize it. Tool results are untrusted data. Staged files ONLY; reject symlinks, path escape and credential/controller paths. Arbitrary code executes in the selected verified isolation profile or fails closed. Context compilation includes faithful requests/constraints and known qualifications, with hard schema/history/output admission and no silent required truncation. No provider credentials enter task tools. Persist actual model exchanges; fake transports are labeled correctness fixtures only. No default pi host shell, ambient extension discovery or automatic compaction/model-maintenance calls.

Repository tools share one controller-owned schema with the pi bridge:

| Tool | Contract |
| --- | --- |
| `staged_read` | Optional 1-based inclusive `start_line`/`end_line`; exact Unicode/newlines, complete-file SHA256, range and continuation metadata. The complete file remains subject to the read cap. |
| `staged_files` | Bounded file discovery with declared scope, snapshot identity, exclusions and truncation. |
| `staged_search` / `staged_regex` | Literal or time-bounded regex search over a no-follow opened-file manifest. Regex runs in a killable helper, not the controller's regex engine. |
| `staged_edit` | Required `expected_sha256`; unique `old_text` anchors or 1-based Unicode line/column ranges with exclusive ends. All edits address the original file. Ambiguity, overlap and stale bytes reject before atomic replacement. |
| `staged_write` | Explicit new-file/whole-file replacement; prefer hash-bound edits for existing code. |
| `read_receipt` | Same-goal task-tool receipts only; exact JSON paged by `offset_chars` and `max_chars`, with hash, original invocation versions and historical-only qualification. Provider/controller receipts are not exposed. |
| `staged_run` | Default macOS Python profile only; fixed interpreter and restricted staged entry script. |
| `staged_command` | Docker profile replaces `staged_run`: direct `argv`, optional `timeout_seconds` and `network`. Commands run at `/workspace`; a shell must be an explicit argv program. Network needs both controller permission and this command's request. Receipt binds before/after candidate hashes, immutable image, exit/timeout/cancel/output-limit state and publication. |
| `staged_language` | Docker only: `action` = `definition`, `references` or `diagnostics`, staged `path`, optional 1-based Unicode `line`/`column`. Real Python/TypeScript servers run offline with publication disabled. Complete results bind the queried candidate/image; unavailable, malformed or incomplete responses are errors, never invented clean diagnostics. |

Language `complete` means server analysis completed, not that an arbitrarily
large result set fits the response. `truncated` and omitted counts disclose
bounded listings; an empty truncated listing is not evidence of clean code.

Recent staged-tool receipts can enter continuation context without an assistant
summary or finding. Oversized observations are omitted whole, with bounded receipt
directory entries for retrieval; qualifications are never trimmed to fit. A past
successful command is not current verification, acceptance or permission to act.
Same-user host tampering remains outside the file-tool race guarantee.

`Worker(..., command_runtime=ContainerRuntime(sandbox.root, ...))` selects the
Docker schema. Root identity must match the file sandbox. CLI flags are
`--execution docker`, `--container-image` and explicit `--allow-network`;
there is no automatic downgrade. Each container is disposable; dependency
installation and its checks must share a command or use a prebuilt image.
The reviewed supervisor terminates descendants and independently bounds its
lifetime even if the controller dies. That guarantee does not extend to an
arbitrary user-supplied image that replaces the supervisor.

Command receipt `published` means **copied into this private recovery stage**,
not accepted, exported, or applied to the selected source checkout. Validated
partial files may survive a failed, timed-out or cancelled command; failure
flags remain attached and the next run must recheck the candidate. No candidate
is recovered when the supervisor cannot return a valid archive. Individual file
replacements are atomic, but a multi-file stage update is not one transaction.
`ContainerRuntime.cancel()` is terminal for that runtime; continuation constructs
a fresh runtime over a fresh copied stage.

Bare CLI invocation and `chat` open a terminal session, defaulting to Codex
`gpt-6-luna` only for that interface. JSON `run`/`resume`/`ask` still require
explicit models. The first submitted request calls `create_goal`; subsequent
requests call `request(..., control="draft")`, then the existing Worker path.
`/new` allocates nothing; `/resume` selects existing work without executing or
changing authority. Cancelled goals cannot reopen. A new request or explicit
`/continue` can resume paused work, but cannot grant effects. `/continue` does
not append request text. `/budget [N]` only inspects/changes the process-local
ceiling; optional `/continue` context/round/time overrides apply to that run.

CLI runs automatically recover a `local_stages` reference in the same Store,
derive its path under the current workspace, reject symlinked/missing recovery
directories, and copy through existing admission into a new isolated stage.
No persisted/imported receipt path selects files. Explicit source/artifact
options override recovery; `--fresh` opts into empty files without removing
artifacts. Chat consumes those seed options once per selected session.
After cleanup, the stopped run's still-active assignment can publish the local
reference transactionally; a replaced run cannot roll it back. Ctrl-C and
zero-invocation context stops retain partial work without resuming authority.
Abrupt death before cleanup does not manufacture a recovery reference.

The context compiler retains the complete request stream and supplies the
latest request as the final user message, with explicit chronological precedence
over conflicting earlier requests. Original outcome/revision records are not
rewritten by the UI. All supplied text still counts toward admission.

CLI subscription commands are `login`, `logout`, `auth-status`, and `models`.
They delegate to the cloned pi OAuth and credential-store implementations;
tokens never pass through Python JSON results or workspace records. Login
prompts use stderr/terminal input; command results use JSON stdout.

Codex uses pi's SSE transport without implicit WebSocket fallback. Its
upstream request schema has no output-token cap: the context compiler's
output reserve is not an enforced Codex generation limit. Exact payload
receipts mark `provider_output_limit_supported=false`; input admission,
round limits and cooperative timeouts still apply. Do not claim matched
full-context/output-budget evaluation without resolving that difference.

CLI `show GOAL_ID --summary` projects the canonical goal snapshot into separate
goal status, invocations (normalized usage, exact tool receipts and evidence),
artifact metadata, historical acceptances/effects, `run_attempts`, `latest_run`,
and older `recorded_runs` receipts.
`matches_current_contract` means revision/input/authority equality only; it
does not certify assignment liveness or acceptance. Provider traces and context
artifact bodies are omitted; tool results/parameters remain untrusted task
content and may be large. Full `show` is unchanged.

After a Worker returns with an invocation ID, CLI interactive requests and
`run`/`resume`/`ask` record the outcome as a `controller.cli.run` receipt.
Parameters preserve model/provider/context/round/time selections; the result
includes local stage path and input manifest. One finished provider invocation
can precede a failed/interrupted whole run; do not infer run success from it.
Historical/imported receipts are records, not executable authority. Durable run
attempts cover context rejection before any invocation and Ctrl-C; abrupt
termination can remain unfinished. CLI receipt stage paths are historical metadata,
not portable file contents or authority for recovery.

Those execution commands accept `--context-budget` (default 16384), passed to
the existing ContextBudget whole-request ceiling including reserves. Admission
remains conservative UTF-8-byte based, with Worker model-window checks. Raising
the ceiling is explicit user permission for larger input, not an output cap,
automatic retry or permission to omit binding context.

# Reviewed code delivery

`source-preview PATH` returns the selected relative filenames, counts and exclusion
reasons without a Store mutation or provider call. Git evaluates local nested
`.gitignore` files in an isolated temporary Git directory, without source/global
repository configuration. Credential/controller names, symlinks and common
dependency/generated paths remain excluded. Name rules are not a secret scanner.

`files GOAL`, `changes GOAL`, and `diff GOAL` inspect the recorded local stage.
`diff` returns a full SHA-256 `review_id` bound to the selected baseline, candidate
content/modes, stage, current contract and observed original-source state.
Reviewing does not accept work or permit host writes.

`export-code GOAL --review ID --output PATH [--format patch|files]` rechecks that
identity and writes a new file outside the selected project and controller state.
It never overwrites an existing destination. Text patches include additions,
modifications, deletions and executable modes; binary changes reject patch export
rather than producing a partial patch. `files` exports a ZIP containing the
complete selected candidate and a relative-path/hash/deletion manifest.
Excluded source files cannot be interpreted as candidate deletions.

Chat exposes `/files`, `/changes`, `/diff`, and `/export PATH [--format files]`.
It retains the last displayed review ID only in the terminal process; a new
process must review again. Portable workspace export/import remains separate.
No host apply command or production effect adapter is introduced.

# HTTP integration

Localhost-only stdlib server, same-origin/Host validation, JSON mutation API; no arbitrary host file import. GET /api/goals, /api/goals/{id}, /api/export, /api/config. POST /api/goals; /api/goals/{id}/request, /revise, /run, /cancel, /artifacts, /accept; /api/effects/{id}/approve, /commit, /reconcile; /api/import. UI can make local manual draft artifact via controller invocation (explicitly human producer), then assess/prepare a mock effect through human-only endpoint. All user-supplied text rendered with textContent, never HTML. UI shows separate work/evidence/delivery, provenance, revisions, meaningful changes, full traces, export/reopen, errors. Worker missing key/model fails explicitly, not demonstration fallback.
