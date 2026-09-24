# Architecture decision: build on pi

Owner direction, 2026-09-24: reuse an existing base harness rather than rebuilding it. The initial bespoke provider/tool-loop implementation is superseded before release.

## Decision

Clone upstream pi as the pinned Git submodule `upstream/pi` and build its **pi-agent-core** and **pi-ai** packages. Use the actual cloned implementation through a narrow Node bridge from the Python controller: pi owns model streaming and agent-loop mechanics; the controller owns durable domain state and policy. The submodule commit records the source version, and upstream's lockfile records dependencies. Keep project-specific additions outside upstream until an actual missing hook justifies a maintained patch. There is no parallel bespoke provider/tool loop and no silent fallback to a different package version.

Initial cloned revision: `a7d17e39aaa0091c7573d0790714751956f10bd1`. Clone the project with `--recurse-submodules`, or run `git submodule update --init --recursive` before building. Upstream remains a separate MIT-licensed checkout; project changes do not silently rewrite its history.

Upstream [agent-core documentation](https://github.com/badlogic/pi-mono/tree/main/packages/agent) exposes context transformation, request preparation, pre-tool interception, awaited lifecycle events and cancellation. The [coding-agent SDK](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md) offers fuller sessions/discovery, but those defaults would introduce ambient resource discovery and a second durable state authority. Reuse the lower-level core for the CLI deliberately, not a reimplementation of its loop.

Subscription authentication reuses pi-ai's provider-owned OAuth flow and
automatic refresh plus coding-agent's `AuthStorage` (including cross-process
locking). Only that storage module is imported; no coding-agent session,
extension or ambient project discovery becomes a second goal-state authority.
The CLI defaults to Codex and an app-private credential file outside workspace
state. Explicit `--auth-file` opts into an existing pi-format store.

## Ownership

| Reuse from pi | Goal Native owns |
| --- | --- |
| Model/provider protocol and streaming | Goal contracts, revisions, original requests |
| Assistant/tool-loop scheduling | Invocation-specific input/authority fences |
| Typed tool execution interface | Restricted tool dispatch and OS isolation |
| Abort and event machinery | Exact candidate/evidence/approval commitment |
| Usage-bearing assistant messages | Durable accounting, artifacts and receipts |
| Context/request hooks | Faithful bounded context and qualified reuse |

No default host shell, credential-bearing tools, arbitrary extensions, ambient project instructions, automatic compaction or hidden administrative model calls. The controller's sandbox remains necessary; a pi tool hook is not an OS security boundary.

Codex uses explicit SSE transport to avoid implicit WebSocket fallback and
reconnect accounting. It never consumes the API profile's environment key or
base-URL override. The pinned Codex request schema does not implement an
output-token cap: output reserves remain admission estimates, not enforced
generation limits. This capability difference blocks an undisclosed
matched-output-budget performance comparison.

## Integration guarantees to exercise

Before each actual model request, persist its admitted context and current input/contract/assignment identity. Associate every resulting tool request with that invocation—not a later global acknowledgment. Capture raw completed messages and usage, preserve unknown usage, and count every turn. A concurrent request invalidates old effects even if pi is still streaming. Abort is best-effort transport cancellation; runtime fencing is the authority guarantee.

The bridge must fail closed on process death, protocol mismatch, missing packages or model errors. Its protocol is not exposed to arbitrary worker code. Task subprocesses cannot read controller DB, credentials, bridge handles or host network. Worker execution receives an explicit model; the interactive CLI selects its documented Codex/Luna default unless overridden. No model substitution or billing fallback.

The final serialized provider payload is admitted and recorded through pi's
`onPayload` hook before HTTP submission. Normalized accounting comes from
provider-reported Responses usage, not the SDK's default zero counters on
errors. Input includes cached input; cache and reasoning counts are subsets,
not extra tokens. Missing provider usage remains unknown.

Worker and bridge instances reject concurrent runs. Cancellation rejects
later admission/tool RPCs and terminates staged execution; each CLI run owns
a distinct stage directory. Script timeouts are clipped to the remaining
run budget. Controller transactions and cleanup are bounded operations, not
preemptively interrupted at an exact wall-clock deadline.
An already-entered controller transaction may finish during cancellation.
Its output remains untrusted historical work, not automatic acceptance or
publication authority. CLI Ctrl-C also records a pause/control-version fence;
transport abort alone is not a durable authority change.

The terminal consumes a controller-only event callback after durable event
capture. It renders public text and fixed tool labels, not thinking blocks,
raw tool output or provider traces. No second provider loop or renderer thread.

Local file recovery uses one non-exported table in the existing Store, not
portable receipt paths or a second session database. A stopped invocation's
assignment must still be active to update its goal's run-directory basename.
Replaced runs cannot overwrite recovery metadata. The CLI derives and checks
the workspace path, then copies files into a new isolated stage. Missing or
symlinked recovery paths fail visibly; `--fresh` is an explicit empty-file choice.
Same-user host tampering and abrupt-death task lifetime remain outside the
claimed recovery guarantee.

## Baseline and limits

The full pi coding-agent is a natural operational baseline, retaining its normal memory/session behavior. Sharing pi internals improves comparison fidelity but does not itself prove fairness or superiority. Match capabilities, safety and model settings; disclose differences. Hook documentation is not executed proof; verification records must state what was actually exercised, especially live cancellation, accounting and isolation.

## Context selection

The compiler includes the faithful current contract/request stream and explicitly recorded open questions, blockers and assumptions before optional material. It selects at most 64 recent candidate bundles and two historical invocation observations, retaining input links and recorded qualifications/contradictions together. Oversized optional bundles are omitted, not stripped of caveats; a bounded artifact directory and `read_artifact` support retrieval. Prior compiled-context artifacts are not recursively copied into new prompts.

The latest request also appears as the final user message, with explicit
precedence over conflicting older requests/original outcome. Compatible
constraints remain binding. Its repeated text is included in admission;
this improves request salience, not semantic verification or automatic
rewriting of the persisted goal. The terminal's session is simply the selected
goal, with no second transcript database or administrative model stage.

The soft task-material target is 8,000 conservative estimated tokens, not a quota. Admission uses UTF-8 byte length rather than an optimistic characters-per-token average, plus output/schema/framing reserves, on every actual request. This text-only profile does not claim exact provider tokenization or multimodal admission. Required material exceeding the hard profile fails visibly.
