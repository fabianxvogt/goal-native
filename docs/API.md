# Runtime API contract

All returned objects JSON-compatible dictionaries/lists. IDs strings, timestamps UTC. `Store(root)` creates a state directory and SQLite database containing content-addressed immutable artifacts. Store owns one workspace. Methods below are the integration contract; optional extra fields are permitted. All IDs checked for workspace ownership (one DB). Store is a trusted controller API, never exposed as an arbitrary model/plugin object; untrusted task code runs outside the controller's OS boundary.

- `create_goal(outcome, parent_id=None, kind='outcome', criteria='', constraints='') -> goal` with id, outcome, revision, input_version, authority_version, status, kind, parent_id. Preserve original request.
- `list_goals() -> list[goal]`; `goal(goal_id) -> goal` enriched with requests, artifacts, invocations, effects, events, acceptances.
- `request(goal_id, text, control=None) -> goal`: immediately increments input_version; control one of pause/cancel/draft/resume or None. Never model-authorized. Default draft-only authority; explicit user `control='allow_effects'` grants mock destination only.
- `revise(goal_id, expected_revision, outcome, criteria, constraints) -> goal`: trusted human operation; CAS revision; request/input fence included.
- `assign(goal_id) -> assignment`: fences predecessor assignment, fields id, goal_id, authority_version. Refuse paused/cancelled.
- `invoke(goal_id, assignment_id, context, expected_input_version=None) -> invocation`: snapshot revision/input/authority and context artifact; optional expected input CAS. fields id, goal_id, assignment_id, input_version, revision, authority_version.
- `artifact(goal_id, invocation_id, kind, content, name='', inputs=None, limitations='', trust='worker') -> artifact`: content str; immutable hash/id, goal_id, invocation_id, kind, name, content, inputs, limitations, trust. Input IDs must exist in same workspace; invocation bound to goal. No implicit acceptance. Source trust cannot become policy.
- `artifacts(goal_id) -> list[artifact]` including content, newest last. Old artifacts usable but applicability uncertain after revision/input change; qualifications retained.
- `receipt(invocation_id, tool, parameters, result, note='') -> receipt`: preserves full result, producer and exact parameters, note explicitly worker assertion.
- `usage(invocation_id, usage_dict)`: record provider raw and normalized usage without reasoning/cache double count; unknown remains null, not zero.
- `finish(invocation_id, status, result='')`: status finished/failed/cancelled/interrupted; no acceptance implied.
- `verify(invocation_id, artifact_id, check, passed, details, trusted=False) -> evidence`: bound immutable candidate; trusted only callable by controller/human, NEVER worker dispatch.
- `prepare_effect(invocation_id, artifact_id, target, expected_version, evidence_id) -> effect`: mock record replacement, target string, uses exact candidate content. Require trusted passing current-candidate evidence; policy validates again at commitment. State prepared, id immutable.
- `approve(effect_id) -> effect`: human exact-proposal approval; MUST revalidate original invocation freshness, authority, evidence/candidate/destination before approval. No input increment for unchanged approval.
- `commit(effect_id, lose_response=False) -> effect`: transaction checks all fences and destination CAS, writes local mock application and operation identity atomically. With loss, effect state unresolved but destination operation exists; `reconcile(effect_id)` queries identity and confirms without retry. Repeated commit deduplicated.
- `effects(goal_id) -> list[effect]`; `destination(target) -> {target,version,content,operation_id}` (new version=0).
- `accept(goal_id, artifact_id, evidence_id, expected_revision) -> acceptance`: trusted human, exact candidate, current revision and adequate trusted evidence. Never autoaccept from worker finish.
- `export() -> dict`: versioned consistent-snapshot workspace export, all durable records and artifact content. Import via `Store.import_data(root, data)` into EMPTY state only; preserve validated provenance, format and hashes. Imported delivery records are explicitly unverified historical claims, never confirmed local effects, and cannot revive effect permission. Exports contain task content and must be reviewed before sharing.

# Worker integration

`Worker(store, model, api_key=None, max_rounds=12, sandbox=None, *, provider="openai-codex", auth_file=None, ...)`; `run(goal_id) -> dict` creates an assignment and supervises the pinned pi agent/AI runtime. Codex requires an explicit controller-only OAuth store path and rejects an API key. The legacy API profile requires `provider="openai"`; there is no billing fallback. Pi owns streaming, OAuth refresh and tool-loop mechanics; the controller owns context admission, invocation/usage/receipt capture and restricted tool execution. `cancel()` interrupts transport/task subprocesses; durable authority changes belong to Store controls. Each thread owns its Store connection.

Supported tools: staged read/write/search/run, save_artifact and optional finding. No approve/verify trusted/commit/authority tools. Worker may prepare candidate, not authorize it. Tool results untrusted data. Staged files ONLY; reject symlinks, path escape and credential/controller paths. Arbitrary code must execute in verified OS sandbox or fail closed. Context compilation includes faithful request/constraints and known qualifications, hard admission including schema/history/output reserve, no silent required truncation. No provider credentials in task-tool environment. Persist actual model exchange for auditable capture cost. Pi integration must execute the real provider; fake transports are correctness-test fixtures only. No default pi host shell, ambient extension discovery or automatic compaction/model maintenance calls.

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
artifact metadata, historical acceptances/effects and `recorded_runs`.
`matches_current_contract` means revision/input/authority equality only; it
does not certify assignment liveness or acceptance. Provider traces and context
artifact bodies are omitted; tool results/parameters remain untrusted task
content and may be large. Full `show` is unchanged.

After a Worker returns with an invocation ID, CLI `run`/`resume`/`ask` records
the returned outcome as a `controller.cli.run` receipt on that invocation.
Parameters preserve model/provider/context/round/time selections; the result
includes local stage path and input manifest. One finished provider invocation
can precede a failed/interrupted whole run; do not infer run success from it.
Historical/imported receipts are records, not executable authority. Receipt
coverage excludes older runs, Ctrl-C, abrupt process termination and no-invocation
failures. Stage paths survive export as metadata, not portable file contents.

Those execution commands accept `--context-budget` (default 16384), passed to
the existing ContextBudget whole-request ceiling including reserves. Admission
remains conservative UTF-8-byte based, with Worker model-window checks. Raising
the ceiling is explicit user permission for larger input, not an output cap,
automatic retry or permission to omit binding context.

# HTTP integration

Localhost-only stdlib server, same-origin/Host validation, JSON mutation API; no arbitrary host file import. GET /api/goals, /api/goals/{id}, /api/export, /api/config. POST /api/goals; /api/goals/{id}/request, /revise, /run, /cancel, /artifacts, /accept; /api/effects/{id}/approve, /commit, /reconcile; /api/import. UI can make local manual draft artifact via controller invocation (explicitly human producer), then assess/prepare a mock effect through human-only endpoint. All user-supplied text rendered with textContent, never HTML. UI shows separate work/evidence/delivery, provenance, revisions, meaningful changes, full traces, export/reopen, errors. Worker missing key/model fails explicitly, not demonstration fallback.
