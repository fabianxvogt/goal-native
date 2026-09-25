# Architecture decision: build on pi

Owner direction, 2026-09-24: reuse an existing base harness rather than rebuilding it. The initial bespoke provider/tool-loop implementation is superseded before release.

## Decision

Clone upstream pi as the pinned Git submodule `upstream/pi` and build its **pi-agent-core** and **pi-ai** packages. Use the actual cloned implementation through a narrow Node bridge from the Python controller: pi owns model streaming and agent-loop mechanics; the controller owns durable domain state and policy. The submodule commit records the source version, and upstream's lockfile records dependencies. Keep project-specific additions outside upstream until an actual missing hook justifies a maintained patch. There is no parallel bespoke provider/tool loop and no silent fallback to a different package version.

Initial cloned revision: `a7d17e39aaa0091c7573d0790714751956f10bd1`. Clone the project with `--recurse-submodules`, or run `git submodule update --init --recursive` before building. Upstream remains a separate MIT-licensed checkout; project changes do not silently rewrite its history.

Upstream [agent-core documentation](https://github.com/badlogic/pi-mono/tree/main/packages/agent) exposes context transformation, request preparation, pre-tool interception, awaited lifecycle events and cancellation. The [coding-agent SDK](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md) offers fuller sessions/discovery, but those defaults would introduce ambient resource discovery and a second durable state authority. Reuse the lower-level core for the CLI deliberately, not a reimplementation of its loop.

Subscription authentication reuses pi-ai's provider-owned OAuth flow and
automatic refresh plus coding-agent's `AuthStorage` (including cross-process
locking). The CLI imports only that storage module; no coding-agent session,
extension or ambient project discovery becomes its second goal-state authority.
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

Pre-request admission excludes tool-result `details` and tool-execution `usage`,
which are UI/accounting metadata rather than provider input. Exact receipts and
raw events retain them. Tool declarations already carried in pi system messages
are not counted a second time as shorthand schemas. The final provider payload
is still independently admitted and captured without dropping task content.

Worker and bridge instances reject concurrent runs. Cancellation rejects
later admission/tool RPCs and terminates staged execution; each CLI run owns
a distinct stage directory. Script timeouts are clipped to the remaining
run budget. Controller transactions and cleanup are bounded operations, not
preemptively interrupted at an exact wall-clock deadline.
An already-entered controller transaction may finish during cancellation.
Validated partial command output can enter the private recovery stage, carrying
its failure/cancellation receipt; `published` means stage recovery only, not
acceptance or source-checkout publication. CLI Ctrl-C also records a
pause/control-version fence; transport abort alone is not durable authority.

The terminal consumes a controller-only event callback after durable event
capture. Interactive TTYs reuse the pinned Pi TUI's main-screen renderer and
multiline editor in a separate Node process. Dedicated pipes transport only
display events and submitted text; Python retains the Store, Worker, credentials
and cancellation authority. The renderer receives only a presentation-oriented
environment allowlist, not the controller's provider keys or Node preload options;
it remains a same-user process, not a credential isolation boundary. Node signals
Python on cooperative busy shutdown; Python also watches renderer death and
interrupts active work if Node cannot signal (such as SIGKILL). A broken UI
event pipe during work enters the same run-cancellation fence; after cancellation,
display finalization no longer writes to the dead renderer. Python restores its
saved POSIX TTY mode on teardown.
The UI renders public text and fixed tool labels, not thinking blocks, raw
tool output or provider traces. Non-interactive sessions use the existing
line-oriented interface; JSON commands have no TUI.

Local file recovery uses one non-exported table in the existing Store, not
portable receipt paths or a second session database. A stopped run's assignment
must still be active to update its goal's run-directory basename, even with zero invocations.
Replaced runs cannot overwrite recovery metadata. The CLI derives and checks
the workspace path, then copies files into a new isolated stage. Missing or
symlinked recovery paths fail visibly; `--fresh` is an explicit empty-file choice.
Same-user host tampering and abrupt-death task lifetime remain outside the
claimed recovery guarantee.

Run attempts are recorded before context compilation, independently of provider
invocations. Stop reasons/diagnostics do not overwrite assistant text. Context
admission failures retain the rejected estimate, including failures during the
pi RPC loop; no string matching or fabricated token usage determines the reason.
Explicit continuation reopens draft authority without adding a fake user request.
Portable run history excludes the local recovery reference.

Code delivery uses content-addressed input snapshots and per-stage bindings in
the same Store. Local source selection/provenance is not portable authority.
Continued stages inherit their original baseline; explicit source/artifact/fresh
selection starts a new baseline. Diff review binds content hashes, modes,
contract and observed source state. Export serializes those exact bytes to a
new external file, never applies them to the selected checkout, and excludes
controller/provider records from the code bundle.

Discovery, literal search and regex share one file/directory scope admission
path. A file scope opens only that file; directory scopes retain bounded
no-follow traversal. Every result path is stage-relative, so narrower searches
do not require callers to reconstruct paths. Both modes share descriptor,
size-limit and manifest accounting.

Multi-file Python execution still uses the fixed interpreter under the same
macOS profile. Isolated mode removes ambient Python import configuration.
A small in-process launcher reads the pinned entry descriptor and supplies its
staged filename, script directory and project root so imports, arguments and
`__file__`-relative data work without a host shell or another process.

Opt-in Docker execution replaces the restricted Python command with a disposable
Linux process tree. Snapshot transfer uses bounded archives, not host mounts;
only validated selected outputs can return to the stage after a divergence
check. The local engine and immutable selected image are trusted. The reviewed
supervisor uses a read-only root, non-root task identity, process/resource limits,
stdin-death monitoring and its own deadline. It kills detached descendants too.
Per-file replacement is atomic, and file↔directory transitions prune only empty
ancestors of deleted selected files. Multi-file stage updates are not one
transaction against disk failure or hostile same-user writers. They never write
the original selected project.

Language tools use this same runtime with offline, nonpublishing commands.
Definitions/references and diagnostics carry the before-snapshot/image identity.
Python uses Pyright; TypeScript uses a semantic-only tsserver with explicit
synchronous diagnostic requests so an early empty syntax publication cannot
masquerade as a clean result. Position conversion is Unicode codepoints ↔ UTF-16.
Missing or incomplete server responses remain visible errors.
Python navigation also waits for an actual analysis publication. Result-file
cache input is bounded to 8 MiB (decoded structures consume additional heap);
encoded helper output stays below the standard 64 KiB runtime limit and marks
omitted results as truncated.

## Baseline and limits

The full pi coding-agent is a natural operational baseline, retaining its normal memory/session behavior. Sharing pi internals improves comparison fidelity but does not itself prove fairness or superiority. Match capabilities, safety and model settings; disclose differences. Hook documentation is not executed proof; verification records must state what was actually exercised, especially live cancellation, accounting and isolation.

`bridge/native_pi.mjs` now uses the real pinned coding-agent SDK and
SessionManager for the separate historical coding pilot. Its normal transcript
and system prompt replace Goal Native's compiled work bundle; its host tools,
extensions, ambient instructions, compaction and retries are disabled. Both
arms use the same controller-owned tool schemas, invocation admission and
Docker images. A fresh bridge process reopens a real persisted native session,
bound to the task/checker/environment manifest; no replayed summary is called
native memory. Raw traces and provider-reported IDs/usage are retained outside
Git. This constrained native-pi baseline is not unrestricted stock pi.
Native SDK events before controller admission stay in the raw trace but are not
forwarded as admitted model/tool events. Rejected oversized requests return
controller interruption state with recoverable session identity, not a fabricated
provider response or zero-usage success.

`evaluation/coding.py` prepares public source/fix snapshots, verifies source
failure/reference success, and freezes executable checker and image identities
before model calls. Worker images contain dependencies, not the reference fix
or checker. Check-only images execute independent behavioral observations with
publication disabled. The older four-arm lifecycle protocol stays separate;
neither shared infrastructure nor the historical pilot opens its claim gates.

## Context selection

The compiler includes the faithful current contract/request stream and explicitly recorded open questions, blockers and assumptions before optional material. It selects at most 64 recent candidate bundles and two historical invocation observations, retaining input links and recorded qualifications/contradictions together. Up to four recent staged-tool receipts from each of those invocations remain available as exact, historical-only observations even if interruption prevented a finding or reply. Oversized optional bundles/receipts are omitted whole, not stripped of caveats; bounded directories plus `read_artifact` and paged `read_receipt` support retrieval. Provider/controller receipts are not model retrieval capabilities. Prior compiled-context artifacts are not recursively copied into new prompts.

The latest request also appears as the final user message, with explicit
precedence over conflicting older requests/original outcome. Compatible
constraints remain binding. Its repeated text is included in admission;
this improves request salience, not semantic verification or automatic
rewriting of the persisted goal. The terminal's session is simply the selected
goal, with no second transcript database or administrative model stage.

The soft task-material target is 8,000 conservative estimated tokens, not a quota. Admission uses UTF-8 byte length rather than an optimistic characters-per-token average, plus output/schema/framing reserves, on every actual request. This text-only profile does not claim exact provider tokenization or multimodal admission. Required material exceeding the hard profile fails visibly.
