# Settled cloned-pi bridge review

## Disposition

**Bounded approval for the exercised local transport path. Not live-model, performance, Sandbox, or unrestricted production approval.** The final repair set closes the reviewed bridge findings under an explicitly cooperative cancellation/deadline contract: concurrent ownership is rejected, replacement assignments are fenced, final payloads are admitted and persisted, provider usage is sourced correctly, and process cleanup is finite. Remaining limits are evidence boundaries rather than unresolved bridge blockers.

This review is limited to:

- `goal_native/worker.py`
- `bridge/agent.mjs`
- `package.json`
- `tests/test_worker.py`
- `docs/API.md`
- `docs/ARCHITECTURE.md`
- imported upstream pi Agent/AI declarations and implementation paths needed for lifecycle, conversion, retry, usage, and provider behavior

`SandboxFix`/`sandbox.py` is deliberately not reviewed. Live credentials, live-model behavior, and performance were not exercised.

## Current evidence boundary

- **[REPORTED]** Retained implementer proof built upstream `ai`/`agent` and the prior Python worker path. The removed mocked bridge test and its npm aliases are no longer part of the current verification surface. No test was rerun for this review.
- **[REPORTED]** `tests/test_cli_transport.py` now passes: a fresh real Python CLI reaches the Node bridge, cloned pi SDK, real OpenAI HTTP adapter, and local Responses SSE fixture; performs a real `save_artifact` call, makes a second provider turn, and persists two distinct invocations plus an artifact with limitations/trust. The dummy credential is not exported.
- **[REPORTED]** The earlier no-key CLI smoke reached `Provider is not configured: openai` with an invocation ID. Failed-turn persistence now falls back from assistant text to `message.errorMessage` (`worker.py:797-804`).

- **[REPORTED]** The replacement-assignment regression first allowed an obsolete write, then passed after the new real-Store `assert_invocation_current` path was added. Three real-Store adversarial transitions plus the real HTTP fixture are reported as four combined passing checks. No test was rerun for this review.
- **[SOURCE]** `Worker.cancel()` now calls `sandbox.cancel()` immediately (`worker.py:377-385`); `PiBridge.cancel()` only sets a flag so the supervisor thread remains the sole JSON writer (`worker.py:295-298`). The supervisor sends cancel once and applies a two-second grace deadline (`worker.py:197-203`), and `_send` loops partial writes (`worker.py:312-322`).
- **[REPORTED]** The parent CLI lane now has real SIGINT evidence: exit status 130, durable pause/control state, and no running invocation. No test was rerun for this review.

- **[REPORTED]** The final repair set adds nonblocking Worker/PiBridge run gates, cancellation/deadline checks before all RPC admissions/tools, remaining-run-time clipping for `staged_run`, final serialized-payload admission/receipt, and flattened provider usage accounting. The reported four-check CLI/Store regression passes with two turns and `64` input, `32` output, `96` total, `16` cached, and `8` reasoning tokens per turn. No test was rerun for this review.

- **[REPORTED]** The final real process smoke completed three HTTP/provider turns, performed `staged_write(answer.py)` followed by `staged_run`, returned exit 0 with stdout `42`, and used `macos-sandbox-exec`. Supervisor tests reportedly pass for real Store transitions, late cancellation, overlapping Worker rejection, and finite pipe cleanup.
- **[SOURCE]** The former mocked bridge/configuration test and `test:bridge`/`test` npm aliases were removed because they only pinned forwarding/config wiring. The current command-path evidence is the subprocess-plus-HTTP regression, and the README’s primary test command is Python unittest.
- **[SOURCE]** `bridge/agent.mjs:193-209` validates `OPENAI_BASE_URL` as HTTPS or loopback HTTP and applies it to the catalog model, so the local HTTP fixture uses the actual adapter rather than an injected `streamFn`.

## Resolved command-path blockers

### BRIDGE-1 — missing process RPC forwarding — **BLOCKER, resolved and exercised**

The earlier process path omitted the `rpc` closure even though `createAgent` requires it at `bridge/agent.mjs:224-225`. The current `startRun` passes the process-local RPC at `bridge/agent.mjs:364-375`, line 371. The reported real transport test reaches the provider, performs tool RPC, and completes a second turn, so this startup blocker is resolved.

### BRIDGE-2 — lifecycle events before first request admission — **BLOCKER, resolved and exercised**

Upstream emits `agent_start`, `turn_start`, and system/user `message_start`/`message_end` events before the first `prepareRequest`. The worker buffers exactly those pre-admission lifecycle/input events (`worker.py:671-690`), caps them at 32, and flushes them after the first Store invocation is created (`worker.py:529-545`). Unexpected model/tool events still fail closed before admission.

The reported two-turn real transport test demonstrates that the first invocation is created, pre-admission events are associated with it, and the next request gets a distinct invocation.

### BRIDGE-3 — `TextIOWrapper`/`readline()` hang — **BLOCKER, resolved and exercised**

`PiBridge` now starts binary pipes with `bufsize=0` (`worker.py:175-182`), reads raw descriptors under `select` (`worker.py:203-227`), caps an unterminated stdout frame at 4 MiB (`worker.py:218-219`), and drains stderr while retaining only a 2 KiB tail (`worker.py:209-217`, `279-290`). The real CLI transport regression passes without the previous buffered-line hang.

The previous synthetic failure assistant message had empty text and an `errorMessage`, while the worker only persisted assistant text. Current `worker.py:797-804` uses `errorMessage` when assistant text is empty. The parent reports that the previous missing-key failure now persists the diagnostic instead of an empty result. This review accepts the source repair and reported proof; no independent rerun was performed.

## Settled findings and residual limits

### BRIDGE-5 — provider usage accounting preserves unknown failures — **RESOLVED in source and reported regression**

The worker now takes raw usage only from provider-reported Responses data (`worker.py:763-768`, `787-795`). Synthetic SDK error/abort counters are not used for accounting; the exact event remains available in the ordinary pi event receipt. Normalized keys are flattened with inclusive input/total plus cached/reasoning subsets (`worker.py:769-786`). Missing provider usage produces `raw: None` and `normalized: None`.

The reported HTTP 503 regression asserts one provider request, no retry, durable failure diagnostics, null raw usage, and all normalized usage fields null. The reported successful three-turn HTTP regression asserts `64` input, `32` output, `96` total, `16` cached, and `8` reasoning per turn. No independent rerun was performed.

### BRIDGE-6 — shared Worker/PiBridge state has a concurrent-run fence — **RESOLVED in source and reported supervisor regression**

`Worker.run` now acquires a nonblocking `_run_gate` and rejects reuse while a run is active (`worker.py:398-418`). `PiBridge.run` has the same ownership gate (`worker.py:159-176`). This prevents a second call from overwriting the first run’s shared goal, invocation, cancellation, pending-event, or process state. The Node child’s one-active-run check remains an additional boundary (`bridge/agent.mjs:424-430`).

The parent reports overlapping Worker rejection in the real supervisor checks. Callers must handle the explicit `BridgeError` rather than retrying through the same live instance.

### BRIDGE-7 — cancellation and in-flight controller semantics are explicitly cooperative — **RESOLVED under documented contract**

`Worker.cancel()` immediately calls the sandbox cancellation hook (`worker.py:402-410`). `PiBridge.cancel()` only sets `_cancel_requested`, leaving the supervisor as sole JSON writer (`worker.py:317-319`). The supervisor sends one cancel frame, applies a two-second grace deadline (`worker.py:214-221`), and `_send` handles partial writes (`worker.py:334-344`).

`_rpc` checks cancellation/deadline before every admission/tool/payload method (`worker.py:528-538`), and `staged_run` checks again and clips its timeout to remaining run time (`worker.py:657-664`). The architecture contract now explicitly permits an already-entered controller transaction to finish, while cancellation rejects later work (`docs/ARCHITECTURE.md:38-46`). The reported late-cancellation supervisor regression and real SIGINT CLI evidence show no running invocation remains after pause.

This is not a claim of atomic preemption: an already-entered Store/sandbox transaction may complete and remains untrusted historical work. That boundary is now explicit and is not an open bridge defect.

### BRIDGE-6a — assignment liveness is present in the local tool fence — **RESOLVED in source and adversarial regression**

`Worker.run` captures an assignment ID after `Store.assign` (`worker.py:401-409`), and `prepare_request` passes that ID into `Store.invoke` (`worker.py:530-539`). The current `_fenced_goal` additionally calls `Store.assert_invocation_current` for the active invocation (`worker.py:757-773`), and `_tool_call` invokes that fence before dispatch (`worker.py:569-573`).

The parent reports that the real-Store replacement-assignment regression failed before this assertion (an obsolete write succeeded) and passes after it, alongside three real-Store adversarial transitions. This closes the prior authorization finding: old artifact retention is not execution permission, and an old invocation cannot dispatch local tools after assignment replacement when the Store freshness assertion rejects it.

Keep unique per-assignment stage directories and explicit resource lifetimes as defense-in-depth. The Store freshness assertion is the authority gate for staged and durable local tools; stage isolation limits stale OS-side effects and prevents resource reuse even when a process is slow to terminate.

### BRIDGE-8 — wallclock enforcement is cooperative, not hard preemptive — **RESOLVED as an explicit contract limit**

The source checks cancellation and elapsed time before RPC operations (`worker.py:528-538`), clips `staged_run` to remaining time (`worker.py:657-664`), validates finite run/bridge budgets (`worker.py:155-156`, `worker.py:376-377`), gives cancelled bridges two seconds before cleanup (`worker.py:214-221`), and closes all child pipes in `finally` (`worker.py:302-315`).

These are bounded synchronous controller-cleanup controls, not preemptive interruption of a Store call, receipt write, sandbox method, or `_send` already executing. `docs/ARCHITECTURE.md:41-46` now states that contract directly. The parent reports finite supervisor timeout checks and successful staged execution; no hard wallclock guarantee is claimed, so this is a documented limitation rather than a blocker.

### BRIDGE-9 — final serialized provider payload is admitted and persisted — **RESOLVED in source and reported regression**

The real provider path installs `onPayload` (`bridge/agent.mjs:240-248`). The callback sends the exact JSON-safe payload with the current invocation; Python gates it with `_fenced_goal`, admits it, and records it before returning to the adapter (`worker.py:540-554`). The reported three-turn HTTP regression exercises this path, while the dummy credential remains outside the payload/export.

This closes the prior logical-context-versus-wire-payload gap for the serialized request body. It does not claim capture of transport headers or credentials, which are intentionally excluded, nor live-provider behavior.

## Bounded passes and limitations

- **IPC identity:** result `request_id` is checked (`worker.py:286-295`), RPC payloads carry `run_id`, and `_rpc` rejects mismatches (`worker.py:528-538`). Binary framing is bounded, stderr is drained, partial writes are completed, the supervisor is the sole stdin writer, and child pipes close in `finally`.
- **Ownership/cancellation:** Worker and PiBridge reject concurrent reuse (`worker.py:398-418`, `worker.py:159-176`); controller RPCs reject later cancellation/deadline work, while documented in-flight transactions may finish.
- **Per-invocation admission:** every actual Agent request calls `prepare_request`, checks context and model limits, creates a Store invocation with expected input version, and updates invocation metadata (`worker.py:556-621`). The reported transport regression proves distinct persisted invocations across real adapter turns.

- **Usage:** provider-reported Responses usage is the accounting source (`worker.py:763-795`); synthetic SDK error counters remain receipt diagnostics only.
- **Hard context and rounds:** admission occurs per request (`worker.py:508-524`); Node enforces `maxRounds` in `finishTurn` (`bridge/agent.mjs:288-292`) and reports exhaustion (`307-339`). Required context refusal remains fail-closed.
- **Retries and hidden calls:** provider options force `maxRetries: 0` and `maxRetryDelayMs: 0` (`bridge/agent.mjs:233-239`), and Agent retry delay is zero (`258-267`). The low-level cloned Agent is used directly, with no coding-agent session, plugin discovery, compaction, approval, or administrative model call.
- **Tool/source authority:** exactly seven controlled tools are constructed (`bridge/agent.mjs:7-15`, `100-190`), sequentially executed, and name-checked (`258-271`). No request-supplied tools or extensions are used. The controller must still ensure untrusted source text cannot arrive as an initial `developer` message, because normalization maps that role to `system` (`bridge/agent.mjs:39-50`).
- **Credentials/task environment:** the bridge process environment is allowlisted to `PATH`, `NODE_NO_WARNINGS`, and explicit OpenAI variables (`worker.py:301-312`); the key is not placed in RPC/tool arguments and the reported dummy credential was not exported. Full task-environment isolation remains unverified because SandboxFix is excluded.
- **Cloned source/no fallback:** imports point directly to `upstream/pi` (`bridge/agent.mjs:3-5`), model lookup is explicit OpenAI catalog lookup (`193-201`), and package scripts check version `0.87.1` before build/test (`package.json:9-13`). The local HTTP regression uses the actual adapter through the bounded base-URL override, not a fake injected stream.
- **Live-model/performance boundary:** the local HTTP/SSE fixtures and staged smoke prove protocol, persistence, and sandbox command-path behavior only. They are not evidence for live model quality, latency, provider performance, or billing semantics.

## Acceptance decision

Approve the bounded claim that the command path reaches the cloned pi SDK and actual HTTP adapter, persists per-request invocations, performs real controlled tool calls across multiple turns, preserves artifact qualifications/trust, buffers expected pre-admission lifecycle events, uses bounded binary IPC, applies controller admission, exposes only controlled tools, and disables implicit retries.

Do **not** infer live-model quality, provider performance, hard preemptive wallclock behavior, or full Sandbox isolation from this review. Those are explicit scope/evidence limits, not remaining bridge blockers.

No additional bridge finding remains open within the documented cooperative cancellation/deadline contract. The remaining release evidence is external to this review: live-provider/performance validation and the excluded Sandbox implementation.

The standalone CLI lifecycle work (`create/request/show/export`) is outside this worker/bridge review and is not assessed here.
