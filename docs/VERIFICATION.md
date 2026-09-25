# CLI and Codex verification — 2026-09-24

Classification: **INCREMENTAL / EMPIRICAL**. These are implementation and
protocol observations, not model-quality, efficiency or general safety proofs.
This checkpoint remains CLI-first; the browser interface and other providers were not redesigned.

## Exercised environment

Darwin 25.5 / arm64; Python 3.12.12; Node 22.23.2. Pi source is the submodule
at `a7d17e39aaa0091c7573d0790714751956f10bd1`, package version `0.87.1`.
`npm run build:upstream` passed, including the cloned credential-store build.

## Repository tools and interrupted-check recovery

Actual CLI → Node → pinned pi → loopback HTTP exercised discovery, a Unicode-capable
line-range read with complete-file hash, a surgical edit, rejection of a second edit
using the stale hash, regex search, and restricted Python execution. The resulting
script printed `5`; the selected source stayed unchanged and the goal stayed draft.
Two provider requests used **synthetic protocol responses**, not a live model or
performance baseline.

Two additional actual-process probes exposed and repaired continuation failures:

- Ctrl-C initially returned from the controller while its expensive regex helper
  remained alive. The retained before probe killed that own orphan. After cleanup
  was bound to the sandbox execution lifecycle, the same SIGINT probe returned
  with the helper reaped. A cancellation/reuse regression covers this transition.
- A real staged check exited zero and its exact stdout was stored, but interruption
  before a reply/finding left that observation out of compiled continuation context.
  The after probe includes the saved receipt without inventing a finding. Historical
  qualification and original versions remain explicit; this does not establish
  current-candidate correctness or a token-efficiency gain.

The expanded upstream build, including pi-tui, passed. Importing the native SDK
then exposed its actual `createAgentSession` function. Import readiness alone is
not native-session or comparative execution evidence.

## Budget recovery and reviewed coding delivery

**86 application checks passed**, including six installation checks, with
ResourceWarning treated as an error. All **4 evaluator safeguards** and
`python -m evaluation smoke` passed. These are correctness checks, not a
comparative model evaluation.

Actual CLI → Node → pi → loopback HTTP, with explicitly synthetic responses:

- A required request estimated at **26,323 / 16,384** stopped before any provider
  request or invocation. The run reason, rejected estimate and selected files
  survived. A new CLI process raised the ceiling explicitly to 65,536 and
  `/continue` read the retained marker and finished in two turns. The durable
  request count remained one; an invalid `/budget 1` did not poison the ceiling.
- A later admission stop made exactly one HTTP request, then rejected accumulated
  context at **42,146 / 16,384**. Assistant progress and the `context_budget`
  diagnostic both survived. The first probe exposed lost assistant text at this
  boundary; the repair and a permanent real-transport regression passed.
- A one-round stop retained nonempty assistant text **and** the separate
  `round_limit` diagnostic. `/status` reports the last estimate/headroom and
  known rounds, not billed token usage.
- Zero-invocation run history survives format-2 export/import without granting
  local file recovery or effects. Regression checks cover old recovery-schema
  migration, format-1 import and stale-assignment rejection.

### Live multi-file coding journey

Actual **Codex / gpt-6-luna**, macOS staged Python, explicit **98,304** context
ceiling. The selected project was a copy of the real context compiler and its
tests, with an intentionally seeded UTF-8 character-count regression. This is
not a claim that the production compiler had that defect.

| Step | Observed result |
| --- | --- |
| Independent baseline check | Existing Unicode check failed before model execution. |
| Deliberately bounded first run | Two turns, `interrupted / round_limit`; selected files and baseline retained. |
| Changed requirement and new CLI process | Added exact-budget equality versus one-less rejection; reopened without source/artifact flags and used `/continue`. |
| Model completion | Six turns, fresh stage, two durable requests, changed compiler **and** tests. |
| Actual terminal commands | `/status`, `/files`, `/diff`, `/export`, exit 0. |
| Independent delivery check | `git apply --check`, apply to a separate original copy, then all **8 project checks passed**. |
| Additional independent oracle | ASCII and 2-/3-/4-byte Unicode exact-boundary admission checks passed. |
| File export and stale review | ZIP bytes matched the applied candidate; changing original source invalidated the old review and created no output. |

The original selected files stayed unchanged by the worker/export path.
The goal remained **draft**, effects disabled, acceptance empty.
Patch SHA-256: `205b5f5ba0e90f05bf7e8a6e539440dc7aa77d671b6e7ce98bf275ff469e5154`.
Temporary workspaces, live transcripts and generated candidates are not published.

Source-selection checks cover nested ignores/negation, visible exclusions and
unsafe files. Real `git apply` regressions cover additions, modifications,
deletions, executable modes, spaces and missing final newlines. Binary patches
reject rather than silently omit data; reviewed file ZIPs retain exact bytes.
An earlier actual multi-file smoke failed importing staged `totals` from a
descriptor entry point; the fixed isolated bootstrap passed sibling/package
imports and `__file__`-relative data without enabling a shell or host imports.

Integration checks also caught a missing stage-parent initialization; it was
repaired before the passing suite. An incidental global `/dev/fd` count test
was removed rather than re-pinned: unrelated descriptor cleanup changed its
count from 14 to 11. That count is not a deterministic leak or behavior oracle.

### Source installation

A separate Git checkout, initially without pi dependencies/builds or a virtual
environment, completed `python scripts/bootstrap.py`. The first attempts exposed
the wrong upstream hydration script name and missing telemetry/chord build
prerequisites. The corrected shared build pipeline compiled the dependency
closure and installed the editable Python package successfully.

From **outside** that checkout, its `goal` launcher:

- opened/exited a fresh prompt with no state or provider call;
- reported runtime and bridge imports ready while deliberately absent OAuth
  credentials remained separately missing;
- reported the runtime unready with bootstrap remediation when a generated
  dependency directory was temporarily removed, then restored;
- completed an actual CLI → pi → synthetic HTTP → macOS sandbox run in three
  turns, including selected-file reading and `FRESH_INSTALL_RUNTIME_OK` output.
  Relative source/state paths resolved against the caller, not the checkout.

No login or inference was performed by bootstrap/doctor. Bootstrap requires
network access for dependencies and upstream model metadata; the catalog is
not a bit-for-bit frozen build input. Unused upstream dev-dependency engine and
deprecation warnings remained visible on Node 22.23.2. A wheel alone is still
not a standalone distribution.

### Independent-review repairs

Two independent Luna/xhigh reviews covered budget/stream recovery and
source delivery/installation. Both settled-source re-reviews approved the
repaired code at `aad06c5`, using source inspection and retained before/after
evidence without rerunning commands. All scoped findings are resolved:

- Cancelling after visible streamed text but before `turn_end` initially lost
  that text and printed a false empty-response fallback. The actual CLI now
  saves the partial reply, displays it once, and records cancellation separately.
- A synthetic `.venv/bin/python` pointing at an external interpreter was initially
  accepted. Bootstrap now rejects it before pip runs, preserving the existing path.
- Explicit `build/foo.py` was staged but absent from the baseline/export.
  Selection now retains that artifact through a fresh-stage continuation and
  reviewed ZIP export, without including unrelated generated files.
- Doctor initially reported ready for clean pi source at the wrong commit.
  Shared read-only source checks now reject it before bridge imports; bootstrap
  rejects it without changing the checkout and gives exact-pin remediation.
  Restoring the exact pin then completed a full rebuild/editable install in the
  validated existing venv; doctor reported runtime/import readiness and separately
  missing credentials.
- Native execution disproved an initial source-only counterargument about the
  credential regex. Replaying the previous profile allowed writes to nested
  `.eNv`, `credentials.json` and `key.pem`. Seatbelt did not enforce Python-style
  `(?:...)` groups as intended. Native grouping now denies those paths and root
  case variants in actual macOS execution; a permanent behavioral regression
  passes. No case-sensitive APFS volume was exercised.

These repairs close the exercised findings, not the broader isolation limits below.

Limits: source filters are not a secret scanner; same-user host tampering,
abrupt-death task lifetime, runtime disk/memory quotas and non-macOS execution
remain outside the demonstrated boundary. Browser redesign, automatic host
apply, broader providers and comparative quality/efficiency remain deferred.

## Earlier streaming and local-file reopening checkpoint

**66 application tests passed** under
`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v`.
New executed boundaries:

- Actual CLI → pi → HTTP stream showed partial text while the fixture withheld
  completion, then rendered the full answer once. JSON commands remained valid.
- A separate CLI process reopened a session after the original selected input
  was deleted; the retained file survived in a new stage. `--fresh` omitted it.
- Missing and symlinked recovery root/parent/stage paths failed before a provider
  invocation. Running invocations/path-valued names could not register recovery;
  replaced assignments could not roll it back. Imported receipts and injected
  recovery records did not select local files.
- Cancelled work retained a local recovery reference without effect authority.
- Review found an interruption window after durable admission but before the
  first event/result. A deterministic regression failed with a missing recovery
  reference before the repair and passes now. Recovery uses the Worker's retained
  assignment identity and a transactional lookup of its stopped invocations,
  not event delivery. Final CLI/HTTP smoke also reopened files in another
  process after the original selected input was removed.

Independent Luna/xhigh review approved the bounded repair after the
failing-before/passing-after evidence and final CLI smoke. Classification:
**INCREMENTAL / EMPIRICAL**, with no general reliability or safety proof.

Actual **Codex / gpt-6-luna** pseudo-terminal, explicit 32768 context ceiling:
the first request announced its work, wrote/executed `squares.py` for squares
1–4, produced stdout `30`, and saved source. After exit and reopening **without
source/artifact flags**, `/resume` plus a changed request produced stdout `55`
for squares 1–5. Two distinct stage directories; the second input manifest
records automatic copying of **1 file / 40 bytes**. The model read the saved
artifact rather than invoking `staged_read`; no live local-file-read claim.

All **9 invocations** reported usage: **12,479 input + 468 output = 12,947 total
tokens**. Both runs exited 0, with draft goal, effects disabled and no acceptance.
The terminal showed streamed text and reading/writing/running/saving activity;
its captured PTY sequence overwrote the compact status line rather than adding
per-token logs. This is an implementation smoke, not an efficiency benchmark.

A separate actual PTY/local HTTP fixture emitted partial text, then received
SIGINT. The prompt returned; a new request in the same session finished.
Invocation states were `cancelled`, then `finished`; the goal stayed draft with
effects disabled and CLI exit 0. This is **synthetic transport** evidence, not a
live-model cancellation claim.

Recovery metadata is local-only in the existing Store, not in portable exports.
It is recorded after cleanup, guarded by assignment liveness, and never grants
acceptance or execution authority. Old pre-feature runs, imports and abrupt
death without a local recovery reference still need explicit file selection.
Same-user host tampering, broad model correctness, browser UI and portable
sandboxing are not established. Next checks: budget-stop recovery without losing
files, and a reviewed staged-diff export workflow.

## Session-first terminal checkpoint

Bare invocation now opens a fresh interactive session; the first request creates
its durable goal. This is controller bookkeeping, not another model call.
Existing JSON lifecycle commands remain available. No Store schema, daemon or
effect adapter was added.

**61 application tests passed** with ResourceWarning treated as an error
(`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v`).
New boundaries cover lazy creation/navigation, cancelled-session
rejection, exact multiline requests retained after setup failure, reopening
paused work without effect authority, terminal-control neutralization, and the
actual CLI → pi → HTTP path for follow-ups versus unrelated sessions.
The initial test run had one new test-envelope error (`export.records`), repaired
before the passing run; it was not an application failure.

A separate option-precedence probe caught subparser defaults replacing explicit
root provider/model choices. Shared subcommand defaults now inherit root values.
The before/after probe changed from `openai-codex / None` to the explicitly
selected `openai / explicit-model`; the installed CLI also reported the selected
missing API credential rather than switching to subscription authentication.
The real HTTP chat regression exercises these options before `chat` under an
isolated home directory. Bare installed `.venv/bin/goal-native` also opened and
exited without creating state.

Actual pseudo-terminal, live **Codex / gpt-6-luna**, macOS sandbox, explicit
32768 context ceiling:

| Attempt | Observed result |
| --- | --- |
| New session, no goal command, 4 invocations | Wrote/executed `total.py` for integers 1–10, stdout `55`, saved exact source. |
| Same-session follow-up, 3 invocations, before context repair | Requested 1–20 and copied the prior stage into a new isolated stage. The faithful new request reached the provider, but the model reran old source and returned `55`. Run status was `finished`: not correctness or acceptance. |
| Reopened session, 5 invocations, after context repair | Explicitly selected the previous local stage and sent the same 1–20 request. Final user-message placement and chronological precedence produced updated source, stdout `210`, and a second immutable artifact. |

All **12 live invocations** reported usage: **15,597 input + 386 output =
15,983 total tokens**, including the incorrect follow-up. No comparative
efficiency or general follow-up reliability inference. The context now repeats
the latest request as the final user message; that input overhead is admitted
and counted, not treated as free.

The actual terminal showed readable replies and exercised `/status`,
`/sessions`, `/new`, `/resume 1` and exit 0. Navigation made no additional goals.
The goal remained **draft**, effects disabled, and acceptance empty.
Separately, an actual pseudo-terminal with a local slow-provider protocol fixture
exercised SIGINT during execution: returned to prompt, goal paused, invocation
cancelled, no running invocation, `/status` available and exit 130. This fixture
is not live-model cancellation evidence.

Limits: no response streaming or full-screen/mouse UI; no browser visual
verification in this pass. Same-process file carry-forward works; after process
restart requests/artifacts reopen but local files still require explicit
selection. Default budget remains 16384; the live scenario deliberately used
32768. Prompt precedence is a model instruction, not a semantic correctness
proof. The latest request does not rewrite the durable original outcome.
Next check: stream a long response, stop it mid-tool, then continue from the
same session without exposing traces or losing local work.

### Independent session review

`GoalNativeSessionReview`, resolved runtime `openai-codex/gpt-5.6-luna` at
`xhigh`, reviewed the settled diff and retained execution evidence. Its
provider/model option-precedence blocker was fixed and separately re-reviewed;
final result: **bounded approval, no remaining identified blocker**.
It ran no tests or live requests. Execution proof belongs to the parent;
the reviewer retained explicit-file-selection and model-correctness limits.
Temporary synthetic workspaces were removed; no owner state or credentials
were added to source publication.

## Live Luna continuation checkpoint

Owner **REPORTED** successful OAuth doctor checks and a three-round live
`gpt-6-luna` Fibonacci run. That observed user success was not rerun merely
to reconfirm it. Subsequent **EMPIRICAL** checks used only `gpt-6-luna`,
through the actual CLI, cloned pi subscription adapter and macOS sandbox:

| Attempt | Observed result |
| --- | --- |
| Initial task, 4 invocations | Wrote/executed `calculate.py`, stdout `55`, exit 0; saved exact worker-produced source artifact. |
| Changed requirement, 4 invocations | Requested sum 1–20 and staged the original artifact. The next request stopped at conservative admission: `17201 > 16384`; CLI failed, not accepted. Partial work remained. |
| Explicit-budget recovery, 5 invocations | `resume --artifact-id ... --artifact-path calculate.py --context-budget 32768`; new isolated stage, source read/updated/saved, stdout `210`, exit 0. Goal remained draft with no acceptance. |
| Round-stop probe, 1 invocation | `--max-rounds 1` stopped a multi-tool task: CLI exit 1, recorded run `interrupted` although its completed provider invocation was `finished`. |

All **14 live invocations** supplied usage: **18,643 input + 703 output =
19,346 total tokens** reported by the provider, including the failed attempt
and stop probe. This is one synthetic arithmetic workflow, not a benchmark
or evidence of comparative efficiency. No automatic retry or budget expansion.

The failed attempt exposed that invocation completion does not record later
run-level budget failure. New CLI outcomes with an invocation ID now persist
as `controller.cli.run` receipts. The actual successful and interrupted
responses exactly matched their reopened `show --summary` run records.
The pre-change failed attempt was retained in this evidence, not fabricated
retroactively as a new receipt. Export/import preserved the new receipt while
effect permission remained disabled. An invalid explicit budget of 2048 was
rejected before a provider request.

**33 model-free tests passed** with ResourceWarning treated as an error:
the new CLI summary stale-contract/completed-turn-versus-failed-run boundary,
existing CLI lifecycle, context, Store, import, lifecycle and sandbox modules.
No full-suite rerun is claimed for this checkpoint. The initial baseline below
is retained separately. Temporary synthetic workspaces were removed after
verification; no owner workspace or credentials were copied into publication.

Limits: `matches_current_contract` is version equality, not assignment liveness
or acceptance. Run receipt coverage excludes older runs, no-invocation
failures, Ctrl-C and abrupt termination. Summaries are not fixed-size/redacted exports;
tool results may contain task content, while raw provider usage/traces remain
available in full `show`. The default conservative budget remains 16384;
continuation can still require explicit additional headroom.

### Independent review

`GoalNativeContinuationReview`, exact runtime `openai-codex/gpt-6-luna` at
`xhigh`, reviewed the settled CLI/test diff and relevant Store, budget and API
contracts. **Bounded approval; no correctness/security blocker.** It performed
no additional tests; execution proof above belongs to the parent. It retained
the non-redacted-output and incomplete receipt-coverage limits. Parent
clarification of review wording: the observed round-budget stop was orderly,
not abrupt termination; its different invocation/run states were correctly
recorded. Review does not establish broad reliability or performance gains.

## Initial protocol baseline checks

```sh
python3.12 -W error::ResourceWarning -m unittest discover -s tests -v
python3.12 -m unittest evaluation.test_evaluator -v
python3.12 -m evaluation smoke
```

- **56 application tests passed** with ResourceWarning promoted to an error;
  no warning was emitted. This includes actual subprocess, HTTP and OS paths.
- **4 evaluation safeguards passed**. The smoke command rejected incomplete
  matrices, unknown-as-zero usage, scripted providers and collapsed-arm claims.
- Actual CLI `models` listed the eight pinned Codex model IDs.
- Before owner authorization, default `auth-status` returned `configured: false`.
  This initial protocol baseline made no live subscription inference.
- The installed `.venv/bin/goal-native` entrypoint and dependency-free
  `uv run --no-project --python 3.12 python -m goal_native --help` passed.
  Plain project-synchronized `uv run ... goal-native --help` timed out twice
  (60/30 seconds); its cause was not established. Direct and no-sync execution
  worked. The documented stdlib source invocation avoids project synchronization;
  no claim of reliable synchronized-uv startup is made.

## Runtime observations

| Path | Observed result |
| --- | --- |
| CLI lifecycle | Create/request/revise, stale acceptance rejection, manual candidate assessment, mock effect commit/reconciliation, export/import and resumed replacement assignment passed. |
| API protocol | Actual Python CLI → Node → cloned pi → loopback Responses HTTP fixture made two turns and saved an immutable, qualified worker artifact. A 503 failure made one request, retained its diagnostic and left usage unknown. |
| Codex subscription protocol | Actual CLI and pi Codex adapter refreshed expired **synthetic** OAuth credentials, persisted rotation with mode `0600`, then completed two SSE turns and saved real Store work. A test-only Node preload intercepted issuer traffic and routed the backend to a loopback protocol fixture. Production has no Codex endpoint override. |
| Credential boundaries | Ambient API key/base URL did not become a Codex billing fallback. Status and exports omitted tokens. Malformed refresh responses did not leak returned tokens or replace existing credentials. Logout preserved unrelated provider entries. |
| Terminal OAuth | The real pi browser flow presented its authorization URL. Ctrl-C returned JSON cancellation and exit `130`. Immediate interruption also terminated without persisting a Codex credential. Browser/device account approval was not completed. |
| Staged execution | A separate actual CLI protocol smoke used three provider turns to write and execute `answer.py`; the macOS sandbox receipt recorded exit `0`, stdout `42`, and no timeout. Temporary smoke state was removed. |
| Interruption and fences | Actual run Ctrl-C paused the goal and left no running invocation. Unread input, replacement assignments and cancellation rejected late tool writes. Busy Worker instances rejected another run without corrupting the first. |
| OS boundary | Twelve sandbox tests exercised host I/O, network, fork/exec denial, credential paths, fixed enforcement, root replacement, output/time bounds, overlap rejection and search/FD behavior. |

Successful fixture usage was `64` input, `32` output and `96` total tokens,
with `16` cached-input and `8` reasoning tokens recorded as subsets. These
are fixture values, not measured model costs.

## Regressions found through execution

- The initial process entrypoint omitted RPC forwarding; expected pi lifecycle
  events arrived before invocation admission; buffered pipe selection hung.
  The real subprocess/HTTP path now passes, rather than only mocked forwarding.
- Replaced assignments originally retained local-write access. A real Store
  adversarial regression failed before the shared freshness guard and passes now.
- OAuth terminal cancellation initially returned exit `1`, and immediate
  interruption could leave the prompt/server waiting. Cancellation now joins
  controller and provider-prompt signals; real terminal regression passes at `130`.
- Review repairs closed export-temp symlink exposure, credential filename
  omissions, unbounded import/copy paths and sandbox execution-admission gaps.

## Docker, language tools, and native sessions

**Classification: INCREMENTAL. Evidence: EMPIRICAL.** These are executed
capability checks, not comparative coding-quality or efficiency results.

- Runtime image `sha256:8dfc62e5b3fd764fd8eb21556ab095a61cca56d61cb2db0cb66f6d5c560458e8`
  ran nested Python code and compiled/executed TypeScript. Both produced `5`.
  Build outputs stayed in the disposable container when publication was disabled.
- Python and TypeScript cross-file definitions/references resolved to the actual
  declaration files. Deliberate type errors produced Pyright
  `reportAssignmentType` and TypeScript `2322`. Astral Unicode before the queried
  symbol preserved 1-based codepoint columns through UTF-16 LSP positions.
- Execution exposed a cold TypeScript server returning incomplete navigation
  and an empty syntax-only diagnostics publication. Queries now use a
  semantic-only server and explicit synchronous diagnostic responses; the
  repeated actual language-tool smoke passed all six operations without source
  mutation. No sleep or empty-result fallback is used as readiness evidence.
- A live isolation probe observed UID `65532`, zero effective capabilities,
  no-new-privileges, a read-only root, no inherited synthetic secret or host
  marker, and no active non-loopback interface. An outbound connection failed
  and an ungranted network request was rejected. Kernel cgroups reported
  512 MiB memory, zero additional swap, one CPU and 128 PIDs; workspace tmpfs
  reported 256 MiB. Output was bounded to 65,536 bytes.
- Timeout and cancellation stopped a task with a detached descendant. Killing
  a separate controller with `SIGKILL`, after observing both live task PIDs,
  removed its container and orphan Docker CLI in `0.24` seconds in this probe.
  Unsafe symlink output was rejected. Empty-directory execution exposed a tar
  finalization bug, now repaired rather than avoided with a seeded file.
- The actual pinned native-pi SDK completed an authored inclusive-sum fix using
  the canonical controlled tools, then reopened the same persistent session in
  a fresh Node process to add negative-input rejection. Independent commands
  checked inclusive positive sums, zero, and the changed exception requirement.
  The selected source remained unchanged and the goal remained `draft`.
  Live provider: `openai-codex/gpt-6-luna`, no API billing fallback.
  Cold: six rounds, 10,640 input / 333 output tokens.
  Continuation: four rounds, 13,404 input / 315 output tokens.
  Reported subscription cost remained **unknown**, not zero.
  Raw traces remain outside Git; their SHA256 values are
  `62d05320d5b3f52614fdab1ee2e8c0377ebe88b54e2cc7a5d6c4c05fe4e439fb`
  and `aff6a380ad0c98cab20c6c7b89e299c6fbb53e90bec4ca55e9f34b7706fa3b3b`.
- Actual CLI → pinned pi → Docker transport smoke used an explicitly synthetic
  loopback provider. It exercised range reads, stale-edit rejection, regex,
  Python output `5`, Node output `42`, and TypeScript diagnostic `2322`;
  source remained unchanged and the goal stayed draft. This is integration
  evidence, not model performance.
- The retained live native session was recovered from its real manifest/session
  header in another process context; a changed checker identity was rejected.
  Recovery does not synthesize a session ID when an interrupted bridge has no result.

## Historical coding oracle qualification

**Classification: INCREMENTAL / EMPIRICAL. Proof status: executed behavioral
observations, not a formal correctness or superiority proof.**

The frozen ten-task preparation completed all 40 source/reference executions:
every pre-fix source failed and every pinned reference passed, for both cold and
changed-request phases. Preparation SHA256:
`cd296f2ef208af03c60d20b2aed282a2f5af15099971ceb805938e76ed321033`.
The selected public sources, licenses, dependency/checker images, named checks
and raw receipts are bound by that external manifest; no source or reference
archives are added to this repository.

Preparation exposed checker-image read permissions, a Flask teardown exception
that lost the stable check set, and an incorrect expected Flow formatting case.
These were repaired from actual runtime/reference observations before historical
model measurement. Async-fixture checks observe setup exception and warning
categories, not diagnostic prose. Continuation adds distinct scenarios rather
than double-counting identical cases. Preparation failures remain retained
outside Git; they are not model attempts.

### First live matrix: retained failures

The initial two-arm run used actual `openai-codex/gpt-6-luna`, 12 rounds,
180 seconds and a 65,536 estimated-token context ceiling per phase. All 40
planned phase attempts were recorded, but none finished:

| Arm | Independently passing phases | Stops |
| --- | ---: | --- |
| Goal Native | 0 / 20 | 17 context limits; 3 round limits |
| Native pi | 1 / 20 | 10 context limits; 1 round limit; 9 bridge errors |

Report SHA256:
`0c7cdef9d9dc52be92b731bbbfdfff3868f1fe56d5c9be90f9dbb9e89583893c`.
Those rows remain unchanged outside Git. They are **not a fair ranking**:
pre-request accounting duplicated UI-only tool-result data and some tool
declarations, while native continuation could turn a rejected oversized request
into an unrelated pre-admission event error. Unknown native accounting and
subscription cost remained null. Worker completion was never substituted for
an independent pass.

### Review repairs and after-proof

Two independent read-only reviews covered runtime/LSP boundaries and
repository tools/native sessions/evaluation evidence. Scoped repairs were
accepted after parent-run verification:

- Native admission rejection: copied real persisted history reproduced
  `worker_error` with no provider request. After repair, the same scenario
  returned `interrupted/context_budget`, retained a genuine native result and
  session reference, and still made zero provider requests.
- UI-only tool-result metadata and exact duplicate declarations no longer enter
  hard input accounting. A CLI transport exercise with a 31,500-byte read plus
  repository edits, Python/Node commands and real TypeScript diagnostics
  completed under the same 65,536 ceiling. This is not a token-efficiency claim.
- Pre-start cancellation previously allowed `ran.txt` to be written; the
  terminal cancellation latch now rejects admission. Directory→file replacement
  failed before empty-parent pruning; both topology directions now pass real
  runtime checks. File replacements are atomic; multi-file updates are not.
- LSP responses require an explicit JSON-RPC result/error, Python navigation
  waits for analysis, file-cache/output bounds are enforced, and truncation is
  explicit. All six real Python/TypeScript navigation/diagnostic operations
  passed again with Unicode positions and unchanged candidate bytes.
- Native result validation hashes/counts the known trace independently and
  checks model/profile/schema/limits and controller-recorded response IDs/usage.
  Forged hash/model/usage and changed trace bytes are rejected.
- **118 application tests passed**, including live Docker integration, with
  ResourceWarnings treated as errors. **4 legacy evaluator safeguards passed.**
  The legacy canonical-worker CLI route also reached its real protocol validator.
- Large-diagnostic execution returned 27 of 160 actual TypeScript `2322` errors,
  with `truncated: true`, a 60,173-byte result and unchanged candidate bytes.
  The final isolation probe again verified the same cgroup, network, privilege
  and output limits; controller death removed its container and orphan CLI in
  `0.142` seconds after live task/descendant readiness.
- Commands that wrote a partial file before exit `9` or timeout (exit `-9`)
  retained that file in their private stage, with `published: true` and accurate
  failure/timeout receipts. The separately selected source stayed unchanged.

Validated partial output remains private staged recovery, not delivery or
acceptance. Reviews explicitly retain trusted-host/image/kernel limits and
non-transactional multi-file publication. Broader fresh tasks and external
developer use remain required.

### Repaired-harness replication

The same ten tasks were freshly prepared after the reviewed repairs; all 40
source/reference checks again qualified. Preparation SHA256:
`57e0de2541d90ae2f5aa59124d0972a5168d1b999761f60b1084fe86ccd996bb`.
The paired live run retained the same model, tools and 12-round / 180-second /
65,536 estimated-token bounds. Report SHA256:
`4fec0847f53d1e2e91b69f84d29485eedeb49cb09485f2dbb9210308ee516699`.

| Arm | Independently passing phases | Stops |
| --- | ---: | --- |
| Goal Native | 2 / 20 | 12 round limits; 8 context limits |
| Constrained native pi | 0 / 20 | 7 round limits; 13 context limits |

The two passes are the cold and changed-request phases of
`pytest-12444-approx-formatting`, not two independent tasks. **No phase finished.**
Every phase reported a draft goal. No bridge or evidence-validation error
remained in this run; checks still failed on the other candidates rather than
blessing unfinished work.

Goal Native recorded 1,134,615 input tokens (181,760 cached), 9,278 output tokens
and 576.989894 summed worker seconds. Native pi recorded 461.783129 summed worker
seconds, but its aggregate token usage remains unknown: five continuations
stopped before any provider request and retain unknown empty-trace accounting.
Subscription dollar cost remains unknown for both arms. These are observed
worker receipts, not an efficiency ranking.

Both reports remain retained outside Git under the
`goal-native-coding-measurement-20260924` and
`goal-native-coding-reviewed-measurement-20260924` external run directories.
The repaired run exercises the measurement workflow, **not strong coding
performance**. It is development feedback on already-seen historical tasks;
no fresh-test, generalization or superiority claim follows. Real-task completion
under practical declared limits and multi-day developer use remain acceptance
gates before UI expansion.

## File-scoped search efficiency repair

**Classification: INCREMENTAL / EMPIRICAL.** The previous repaired matrix
recorded 366 tool calls, including 117 literal/regex searches. **58 searches
failed on regular-file paths** because the scanner admitted only directories.
Models retried or used broader searches/reads; this was an implementation
defect, not evidence that the requested paths were unsafe.

Discovery, literal search and regex now share file/directory scope admission.
Paths returned from nested scopes are relative to the stage root and directly
usable by read/edit tools. File scopes avoid unrelated traversal without
weakening no-follow, credential, size or truncation rules.

A frozen replay of all 58 affected calls reproduced 58 failures before the
repair and **58 successful queries, zero errors** afterward. It checked expected
match lines, usable paths, one-file byte accounting and unchanged candidate
hashes. Cases SHA256:
`739943d43bf96d786a207601495d59633815875184394a5960e3cfe11796d231`.
Evidence remains outside Git in `goal-native-scope-efficiency-20260924`.
The replay uses retained **end-of-phase candidates**, not reconstructed
intermediate states; it establishes corrected operations, not saved model
tokens or a latency improvement over fast refusals.

**121 application tests and four legacy evaluator safeguards passed**, including
actual Docker integration. New behavioral regressions cover nested-scope
readback, file-only limits and snapshot changes, symlink/credential/traversal
refusal and non-regular files. ResourceWarnings remained errors.

Independent read-only code and evidence reviews accepted the scoped repair and
mechanistic replay, not a token-efficiency claim. A supplementary resource probe
ran 400 successful searches plus 40 invalid-regex rejections under a 48-descriptor
limit; the same nine descriptors remained before and after. Forced stat/open
replacement with a FIFO or symlink admitted zero files, reported an exclusion
error, did not hang, and left the outside synthetic marker untouched.

The subsequent live comparison was frozen before calls under registration SHA256
`215127f401119942732d19ba294e32b067ac539cb93c3c79ed21bde6695dab1c`
and preparation SHA256
`e6427929da00ad8cf3de0f657dcc2103f367658be1dfcf98a235329a001ebef9`.
All 40 pre-fix/reference qualification checks passed again. Each task's source
and reference snapshots, checker bytes, worker/checker images, setup commands and
expected observations matched the prior preparation. Model, thinking setting,
arm order, network policy and 12-round / 180-second / 65,536 context bounds were
unchanged. This is one additional development run, not held-out validation.

## Reproducer-first workflow — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL for local admission checks; coding
improvement remains SPECULATIVE.** `goal_native/context.py` now instructs the
canonical worker to use the declared environment, run the reproducer early,
inspect its failure before a general fix, and check changed source and
compatibility before claiming completion. Failures, unrun checks and execution
blockers must remain explicit. This is prompt guidance, not enforced ordering
or trusted acceptance. Native Pi, tool authority and run budgets are unchanged.

The completed post-search-fix report in
`/tmp/goal-native-coding-file-scope-measurement-20260924/report.json` has SHA256
`7406b51081e7ed7756cd65bcf222961c06e14f9c576176accd940b183b88ab02`.
Goal Native passed 2/20 phase checks; native Pi passed 4/20; neither finished
any phase. Stops were respectively 10/5 round limits, 3/7 context limits and
7/8 provider usage-limit errors. All 15 error traces contain the subscription
usage-limit response. Zero search-tool errors remained, but lower call counts
and elapsed time cannot establish efficiency with quota-denied attempts.
All 80 before/after retained trace hashes and byte counts were verified.

Trace diagnosis selected `flask-5786-redirect-session` and
`prettier-1422-typescript-namespace-export`. Flask's cold phase made speculative
edits without executing its reproducer; continuation omitted the documented
source import path. TypeScript's cold phase spent all 12 calls on discovery
without an edit or command; continuation initially omitted its dependency path
and still made no edits. These motivate the guidance, not a claim it works.

Local verification: **7 context and 9 worker checks passed** with Python 3.12.
The initial system-Python worker run was rejected by the existing Python 3.11+
runtime requirement. An initially longer prompt also exceeded the existing
small-task context check; the final concise version passes without changing
tests or budgets. A throwaway direct `compile_context` smoke admitted both
selected tasks' cold/continuation requests and preserved each latest request.
This establishes request construction, not live model compliance.

### Live two-task follow-on

On explicit continuation, actual Codex/Luna access worked again. One Flask cold
phase was registered first as a provider-access/workflow probe; the remaining
phases were registered before their calls. This is Goal Native only, not a
new paired comparison. Model and 12-round / 180-second / 65,536 context limits
were unchanged; no retries, network grant or provider substitution occurred.

| Task | Cold | Continuation | Independent outcome |
| --- | --- | --- | --- |
| Flask redirect/session | round limit | round limit | checker error in both phases; raw candidate execution raises request-context error |
| TypeScript namespace export | context limit | round limit | failed in both phases |

**Zero of four phases finished or earned an independent pass.** TypeScript
executed the correctly configured reproducer on call 3 and attempted an edit,
unlike the previous discovery-only cold phase, but still failed its checks.
Flask still tried unavailable `python`, omitted the source import path, and
edited without running the requested reproducer. Its candidate now raises
`RuntimeError: Working outside of request context`; reference checks pass.
The checker wrapper reports `error` because the exceptional candidate path
omits registered behavioral check names. That is not a candidate pass or a
proven checker-environment failure.

Evidence is retained under `/tmp/goal-native-reproducer-first-access-20260925`:
`result.json`, `cold-check.json`, and per-task phase `result.json`/`trace.jsonl`.
Initial registration SHA256:
`5d22ad4d01f610c7134012729c67e402b81b1213235cc76b36b91c095ca4db6c`;
follow-on registration SHA256:
`d2b8a1d1a6021cf97630c5142218832f165e784e39797e96d4cf3a725f97028f`.
All four trace hashes/byte counts match recorded results; selected source
snapshots stayed unchanged. An initial pre-provider setup attempt rejected
the `/tmp` symlink path; resolving it to the real directory allowed execution.

The live acceptance gate failed. Prompt guidance alone did not establish
reliable reproduction or completion; quota is no longer the observed blocker.
Next experiment should distinguish environment/setup friction from insufficient
working budget on these retained failures before spending on a full matrix.
Fresh-task promotion and independent review remain open.

## Setup, budget and fresh CLI gate — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** This closes bounded execution and
delivery checks, not general reliability, comparative efficiency, external
developer acceptance or scientific novelty. All attempts were retained.
No additional harness implementation defect was isolated; no default budget,
provider, reasoning setting, tool authority or sandbox boundary was changed.

### Controlled historical diagnosis

Executable reproductions were frozen outside the checkout and verified with
the same dependency images: both selected pre-fix sources fail and both
references pass. The commands explicitly name `python3`/`PYTHONPATH=src` and
Node/`NODE_PATH`. The setup-only run retained 12 rounds / 180 seconds / 65,536:
Flask passed both independent checks but neither phase finished; TypeScript
failed both independent checks despite a finished continuation response. Its
local reproduction omitted the original comment-bearing case, illustrating
why worker success claims do not replace the independent checker.

A separate control changed only working limits to 32 rounds / 300 seconds /
131,072 while retaining the setup prompts and model. All four independent
phase checks passed; three phases finished directly. Flask continuation hit
the context ceiling after 25 rounds. Explicitly reopening its unchanged request
in a fresh stage with the same limits finished in 16 further rounds and passed
the changed-phase checker. This is retained recovery, not a replacement success
row or evidence of single-run completion. Provider payloads report reasoning
effort `none`; that setting was held fixed throughout these controls.

Evidence roots and report SHA256:

- `/tmp/goal-native-coding-setup-control-20260925`: frozen commands and
  source/reference reproduction receipts.
- `/tmp/goal-native-coding-setup-only-20260925/report.json`:
  `b732111ffac5508b588d93be74e8590fd281b933608d6e26ca862aa28f2efe3b`.
- `/tmp/goal-native-coding-budget-control-20260925/report.json`:
  `d9cdcb1c03e645c060cd80691a4f2696acb3c0ececcef09d200f3d3d6f47abfd`.
- That root's `flask-5786-redirect-session/resume/result.json`:
  `4311648bd35736de0ff6c01dae36ff9b97788aefe5c02256e99a523d6290b976`.

### Fresh small-project CLI delivery

Two author-designed tasks, not external benchmark samples, were frozen with
named independent checks before model calls. Python repairs an exact-cent CSV
ledger and then adds an inclusive minimum-total filter. TypeScript repairs a
dependency-layer scheduler and then adds completed-job handling with full-graph
validation. The original sources pass only 1/9 and 1/10 cold checks respectively.
Both use actual `python3.12 -m goal_native` processes with Docker, Luna and
the explicit 32 / 300 / 131,072 limits.

| Workflow | Cold | Changed request after process restart |
| --- | --- | --- |
| Python CSV ledger | finished, 9/9 checks | finished, 11/11 checks |
| TypeScript scheduler | finished, 10/10 checks | finished, 15/15 checks |

Before Python's cold run, a deliberately declared 4,000 ceiling stopped before
the first invocation (zero rounds). A new CLI process resumed without a source
flag, recovered selected files into a fresh stage, and completed the request.
Each changed request likewise ran in a new CLI process using saved files.

Parent review then found an uncovered scheduler bug: completing a non-root job
could allow it to reappear in a later batch. The original candidate failed a
direct execution probe. A new explicit review request through the harness
repaired it, added `schedule.test.ts`, removed four scratch outputs, and finished.
The same probe passed afterward. Original passing rows are retained, not
retroactively described as a complete bug-free implementation.

Actual `diff` reviews bound final candidates; `export-code` emitted patches.
Both patches passed `git apply --check`, applied to disposable copies of the
original selected sources, and passed the complete continuation checks there:
Python 11/11; TypeScript 15/15 plus its added scheduler regression script.
Original selected sources remained unchanged. No manual candidate code repair
or automatic host-source apply was used.

Evidence under `/tmp/goal-native-fresh-gate-20260925`:

- Frozen `registration.json` SHA256:
  `69a7bba7f46bf3dd6d555951515dd83775e64f813163c023f4940117323c798d`.
- Original four-phase `report.json` SHA256:
  `726cd836b2884098e49c5860e3f6dc95406b3bb74306f679c03e50456151f568`.
- Final `export-proof.json` SHA256:
  `7fdb4201177f465f50fb8e618616d599c25c911efaa9342c83303060e0ae600b`.
- `python/change.patch` SHA256:
  `7bc63d3782203af051ba43f663b21e860082eb29316fecfdff688523baa6d41f`.
- `typescript/change.patch` SHA256:
  `a17637d748a200575258d2b8bfd55db2a9a2a6647d170a89695a446579b17582`.
- Per-language CLI outputs, checker receipts, reviews and export receipts;
  TypeScript retains `review-before.json`, `review-after.json` and review-fix output.

Independent specialist review is complete. The managed launcher first failed
because this checkout has no `scripts/fleet.mjs`; no managed worker started and
no launcher code was created. The owner then explicitly authorized two native
read-only review agents for this task. Both returned scoped approval with no
blocking findings. Python review also assessed the canonical prompt guidance;
TypeScript review distinguished the initial missed edge from its final repair.
`independent-reviews.json` retains their conclusions and limitations.

The bounded coding execution/recovery/reviewed-delivery gate is complete.
These reviews inspect settled diffs and retained executed proof; they do not
substitute for new execution or establish exhaustive correctness. Multi-day
external developer use, larger unseen repositories, repeated matched runs and
general reliability remain open, separate acceptance requirements.

## Existing-repository deliveries and terminal UX — 2026-09-25

Classification: **INCREMENTAL / EMPIRICAL**. Three existing-repository tasks
ran through actual Goal Native CLI processes, isolated Docker execution,
persisted requests/stages, review and patch export. Parent-applied source changes
were independently exercised. These are assisted maintainer deliveries, not an
autonomous-success rate, external acceptance or a model comparison.

| Delivery | Provider runs | Follow-up requests | Summed run seconds | Reported input / output tokens | Invocations without usage |
| --- | ---: | ---: | ---: | ---: | ---: |
| Python checker exception handling | 4 | 2 | 1,083.77 | 1,948,367 / 23,237 | 1 |
| Repair Atlas supplied case tracks | 3 | 2 | 839.97 | 1,451,089 / 16,507 | 0 |
| Multi-file CLI UX | 6 | 4 | 1,355.49 | 2,182,768 / 22,266 | 2 |

Times sum individual run processes; tasks overlapped. Setup, review and parent
verification are excluded, so these are not end-to-end wall-clock times.
Token figures are reported subtotals, not estimates for missing invocations.
Reported cached tokens were 678,016 / 495,232 / 845,952 respectively; do not add
them to reported totals or infer billing cost. Cost remains unknown.
The CLI's final run was a one-round closing report after parent verification,
not another implementation run.

All initial Luna attempts stopped at context or time limits. Explicit
Luna-to-Astra repair runs, detailed parent feedback and further continuation
were required; the failures remain in the records. The declared working limits
were 48 rounds / 300 seconds / 196,608 conservative context tokens, not defaults.
An initial invalid 600-second declaration and Docker-daemon failure occurred
before provider execution. The owner authorized a Docker Desktop restart.
The frontend dependency image used the existing lockfile and no worker network.
Its 512 MiB/read-only environment blocked normal bundling; reviewed source was
built independently on the host instead. No sandbox boundary was relaxed.

### Delivered behavior and independent proof

- **Checker:** complete registered names survive candidate import/probe failures;
  earlier observed results survive later failures, including stream close.
  Teardown exceptions cannot report all-passed. The unchanged independent
  validator classified the retained real broken Flask candidate as **failed**
  rather than a protocol error in both cold/changed phases, preserving successful
  import/target checks. The real post-fix reference passed both phases.
- **Frontend:** supplied annotations/variants render through JBrowse's existing
  `FromConfigAdapter`; safe integer, half-open coordinates, wrong-locus filtering
  and stable distinct/deduplicated identities are checked. `npm test` and the
  production build passed. Actual browser screenshots showed synthetic A→B
  feature replacement at the same locus and removal of empty case tracks.
  The fixture was corrected from `chr1` to the existing public reference's `1`;
  contig aliases, biological validation and private/imported datasets were not
  exercised. The generated `tsconfig.tsbuildinfo` hunk was not applied.
- **CLI:** grouped help, short session IDs/states, selected-not-sent source
  metadata, assistant/tool labels, errors/recovery and restrained TTY colors.
  Real 48-column PTYs confirmed SGR only in the color-enabled case, no readline
  marker leakage, `NO_COLOR`/`TERM=dumb` suppression and plain automation JSON.
  Live Astra wrote/read/SHA-edited/executed a script yielding `3`; a changed
  request stopped at a 3,500 ceiling. After process exit/reopen, `/resume 1` and
  explicit `/continue --context-budget 65536` recovered fresh staged files and
  yielded `4`. Exactly two requests remained, no task-tool failures occurred,
  and the goal stayed draft with no acceptance.

Parent integration changes are explicit: canonicalize the checker's temporary
build root for macOS `/var` aliases; remove a test of unchanged JSON forwarding;
remove an invented empty “Assistant” reply exposed by a real budget stop.
The parent also clarified ranged-read guidance, the complete-file meaning of
`max_bytes`, and copying the full 64-hex edit digest. A subsequent live read/edit/
run/restart flow succeeded without tool errors. This repairs misleading guidance
and the observed empty-reply UX; it does not establish that recurrent model
editing/build failures are eliminated.

Final focused command:
`python3.12 -m unittest tests.test_python_checker_failures tests.test_cli_terminal tests.test_cli_transport tests.test_coding_evaluation -v`
passed **26 tests**. Context and Worker suites separately passed **7** and **9**.
The final two-line empty-response correction was subsequently exercised in the
actual stopped/reopened PTY. Two owner-authorized read-only specialist reviews
found no actionable defects; no review reran tests or substituted for execution.

**The full suite is not green.** Its earlier 135-test run recorded nine failures,
one error and eight skips. Four failing new pytest subcases were repaired by
canonical temporary paths and passed in the focused rerun. Five macOS sandbox
process-launch failures (`posix_spawn` at the Homebrew Python framework path)
and the login Ctrl-C termination/cleanup error remain unresolved. They are not
hidden by the passing Docker proofs. Vite's large-chunk warning also remains.

Evidence root: `/tmp/goal-native-real-deliveries-20260925` (local, not committed):
`scorecard.json`, `independent-reviews.json`, `focused-verification.json`,
`checker/upstream-proof-canonical.json`, each task's registrations/run records/
exported patch, `typescript/browser-proof.json` and `browser-{1,2}.webp`,
`terminal-gates.json`, `terminal-live-proof.json` and terminal transcripts.
Original failed attempts and historical checker registrations were not rewritten.
`goal-delivery-integrated.patch` includes the disclosed parent source/test fixes
and passes apply-check against the preserved original snapshot; raw harness
exports remain separate. The frontend's initial nested-directory apply skipped
paths; root-relative `--directory=apps/web` application corrected it, followed by
direct execution of the applied mapping checks. No push was made: Repair Atlas
has no configured origin, and Goal Native retains mixed pre-existing work with
an unresolved full-suite gate.

### External-developer handoff — still open

No external participant has been nominated or observed. A separate developer
must use their own non-sensitive Python/TypeScript task, not these maintainer
fixtures. First bootstrap and run `doctor`; use the documented Docker profile
while default-host failures remain unresolved. Select a reviewed source directory
explicitly; freeze the task, environment, limits and acceptance command before
starting. Record elapsed time, provider-reported usage/unknowns, feedback and any
manual edits. Exit/reopen once, introduce a genuine requirement change, inspect
`/status`, and use explicit continuation rather than silent retry.

Finish with `/diff`, `/export`, independent patch inspection/application and
the task's acceptance command in the developer's repository. Report failures,
not just the final passing attempt. Multi-day external acceptance remains open;
this handoff and the three assisted deliveries do not satisfy it.

## Limits and next acceptance

1. **Basic live CLI gate satisfied:** owner authorization and Luna task execution
   plus changed-requirement recovery were exercised. Broader reliability,
   interruption-heavy continuation and other models/providers remain unproven.
2. Codex's pinned request schema has no enforced output-token cap. Receipts
   disclose that capability; input admission and cooperative time/round limits
   are not a substitute for an output cap or complete resource isolation.
3. The trusted-host macOS Python sandbox is not a VM. Same-user host tampering,
   runtime disk/memory quotas and content-immutable search snapshots are outside
   the reviewed guarantee. See [sandbox review](REVIEW-SANDBOX.md).
4. Native-pi persisted-session execution and a complete historical matrix are
   exercised, but the measured coding outcomes remain weak. Collapsed controls,
   synthetic responses, passing tests and these correlated historical tasks
   establish no comparative efficiency or model-quality gain.
5. The browser still exposes manual goal creation and many controls. Browser
   simplification stays behind developer use of the CLI, not ahead of it.
   Multi-day Python/TypeScript dogfooding, fresh broader benchmarks, independent
   success-claim labeling and additional host platforms remain open gates.

## Pi terminal, persistent command and default-host repairs — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** No comparative quality, token
efficiency, external-developer acceptance or general safety claim follows.

The interactive TTY now uses the pinned Pi TUI's main-screen differential
renderer and multiline editor through `bridge/terminal.mjs`. Python retains
the Store, Worker and all tool/credential authority; dedicated pipes exchange
only submitted text and presentation events. A compact goal panel displays
the current goal and recent goals with numbered, labeled status lights.
`running` refers only to the active CLI process, while a persisted incomplete
attempt is `unfinished`; a finished run says `run finished / review`, never
accepted. JSON commands remain plain; non-TTY input retains the line interface.
Opt-in `bootstrap.py --install-command` installs a checkout-targeting `goal`
wrapper without replacing unrelated commands or editing shell startup files.

A separate temporary `bin/goal` installed by `_install_command()` was found
by `PATH` from another caller directory and executed real `goal doctor` with
exit 0, without creating the caller-relative trial state; repeat installation
reused the identical wrapper. The installer regression checks also reject
conflicting files and symlinks without changing them. This was an isolated
command smoke, not a new bootstrap run or shell configuration change.

An actual 100-column PTY displayed two recorded goals with paused/draft lights;
`/resume 2` selected the recorded draft. Its Pi editor ran in raw mode, slash
completion submitted `/status`, resize and `/exit` restored terminal modes.
An actual 48-column PTY rendered multiline input, a running goal, streamed
assistant text and the subsequent paused goal on Ctrl-C. An isolated loopback
provider fixture exercised multiline submission, partial output cancellation,
exit/reopen, `/resume 1`, unchanged `/continue`, one retained user request,
finished run, draft goal and zero acceptances; this is **synthetic provider**
evidence, not a live-model claim.

Framework-Python `Python.app` exec paths were added to the macOS profile
without permitting arbitrary process execution. After-proof on macOS Python
3.12: the real multi-file Python sandbox check passed, as did host read/write,
network, fork and unrelated exec denial. OAuth login Ctrl-C with an isolated
auth path exited `130` with no credential saved after closing its readline
loop. Neither probe used an account login.

After the review repairs, `python3.12 -W error::ResourceWarning -m unittest
discover -s tests -v` ran **140 tests: 132 passed, eight skipped**.
The earlier pre-review full suite ran 138: 130 passed, eight skipped.
`python3.12 -m unittest -v evaluation.test_evaluator`: four safeguards passed;
the four-arm comparison remains incomplete and non-claimable.

`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v`
ran 138 and failed eight Python-checker cases because this existing venv
lacks `pip` (`No module named pip`); its other tests passed, eight skipped.
The previous five sandbox-launch failures and login cancellation error did
not recur in the supported-Python full suite. Installation still requires
bootstrap with a pip-capable Python environment; this run did not perform
a new source installation or real OAuth authorization. Multi-day external
use, broader held-out comparisons and provider cancellation/accounting under
this new TUI remain separate gates.

Independent read-only code review identified three actionable edge cases:
OAuth/provider-supplied terminal controls in login text; a renderer SIGTERM
while the Worker remained active; and a partial user command after a failed
installer write. The fixes sanitize login display values, cancel/fence on
renderer loss (the actual process fixture now exits 130 with a paused goal,
cancelled attempt and no live invocation), and install the wrapper atomically
without replacing a concurrent command. The focused Pi terminal, installation
and subscription suites ran **16 checks, all passed** after these changes.
An account-authenticated login and model-driven task with the new Pi UI
remain untested; external developer acceptance remains open.

## Validated virtualenv pip recovery — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** This is setup reliability, not a
coding-quality or provider-token efficiency result. The existing checkout
`.venv` had no `pip`: the earlier full-suite run above failed eight checker
cases on `No module named pip`. Python 3.12's bundled `ensurepip` was available.
Bootstrap now checks for `pip` only **after** `_ensure_venv()` verifies the
selected interpreter's version and exact checkout prefix, and after the
pinned pi checkout check. If absent, it runs offline `ensurepip --upgrade`
inside that environment; a present `pip` is not upgraded, and failure to
run `pip --version` stops setup before editable installation.

Before proof: a fresh disposable `venv --without-pip` could not run
`python -I -m pip --version`. After the new bootstrap function, its isolated
`pip --version` succeeded from inside that venv and repeat provision left the
version unchanged. The same path installed bundled `pip 25.0.1` into the
validated Goal Native checkout `.venv` without touching system Python.
Previously failing checker suite:
`.venv/bin/python -W error::ResourceWarning -m unittest
tests.test_python_checker_failures -v` — nine tests passed.
The full checkout-environment command
`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v`
ran **141 tests: 133 passed, eight skipped**. This does not retrofit a
historical failed run or count it as passing.

`./goal doctor --model gpt-6-luna` then reported `ok: true` for the
default macOS Python runtime, pinned built pi, and existing Codex OAuth
readiness; it made no provider call.

Independent read-only bootstrap review found no actionable defect in the
validated-prefix, offline provision and idempotence paths. It retained two
untested host assumptions: same-user replacement of `.venv` between prefix
validation and provisioning, and virtual environments deliberately created
with system-site packages, where an external `pip` module may be importable.
Neither mode was used for the observed recovery; no safety claim for those
configurations follows.

Trace selection check before this fix: the retained post-search-fix coding
matrix had 62 Goal Native `staged_read` calls, 53 explicitly ranged and nine
unbounded; returned read-result JSON totaled about 429 KB. A synthetic
64-artifact compiler admission took a median 24 ms. Those observations did
not support changing read semantics or context admission to claim token
savings. Next gate remains real external use and independent matched coding
quality/efficiency evaluation.

## Live Pi terminal coding and renderer-death recovery — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** The actual interactive Pi
terminal used existing Codex OAuth readiness with `gpt-6-luna`, no source
directory or network grant, and explicit `4` rounds / `90` seconds / `32768`
conservative context per run. A disposable state directory kept task files
and session history outside the checkout. The first request asked for
`total.py` summing integers 1–10, executed inside staged Python and reported
stdout `55`. Actual run: finished in three invocations; provider-reported
usage 4,392 input / 86 output tokens. `/status`, `/diff` and `/export` ran
through the TUI. The patch applied in a separate empty directory, whose
independent `python3.12 total.py` printed `55`.

After process exit and reopening the same state, `/sessions` and `/resume 1`
selected the saved goal. A genuine new request changed the bound to 1–20
without supplying a source path; the new isolated stage contained
`print(sum(range(1, 21)))`. The actual run finished in four invocations and
reported 9,091 input / 189 output tokens; independent execution printed
`210`. Exactly two requests and two finished run records remained, goal
status `draft`, no acceptances. No comparison arm was run and subscription
cost remains unknown. This is one author-directed small task, not a coding
success-rate or token-efficiency benchmark.

The first real-model PTY teardown exposed a cleanup defect: a terminal
sometimes retained raw-mode flags after the CLI exited. Explicitly killing
the Pi renderer with `SIGKILL` reproduced it deterministically: the
controller exited 130 but `ICANON` stayed off. Python now saves the exact
POSIX TTY attributes before launching Pi and restores them after child
cleanup, even if the renderer cannot run its own shutdown handler.
The same SIGKILL probe then exited 130 with exact mode restoration and
no goal created. A separate actual PTY fault injection made `os.fdopen`
fail after Pi spawned; this exposed a startup-only interrupt from the
renderer masking the original error. Python now retains raw-descriptor
ownership through initialization, reaps failed startup promptly, preserves
an active exception through cleanup, and Node only interrupts an active
controller after its first prompt. Six focused terminal tests passed, including
active-run cancellation and exact mode restoration after both renderer loss
and partial startup. The checkout environment's complete command
`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v`
ran **144 tests: 136 passed, eight skipped** after the startup fix.
External multi-day use, larger nonoverlapping coding tasks and matched
native-Pi efficiency remain open.

## Renderer SIGKILL during active work — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** In an actual macOS PTY, a
loopback streaming provider deliberately withheld its final response. After
the first assistant text, killing the Pi renderer with `SIGKILL` left the
Python controller alive beyond five seconds with a running provider turn:
Node could not execute its cooperative SIGINT handler. Previously tested
`SIGTERM` did cancel but was not proof for uncatchable renderer death.

The controller now watches renderer exit, arms interruption only while a
CLI run is active and sends one SIGINT for abrupt nonzero exits. Node still
sends an immediate SIGINT for cooperative busy shutdown and exits `130` as
an acknowledgement so Python does not interrupt cleanup a second time.
Output-channel failure during an armed run also enters the existing
`KeyboardInterrupt` cancellation fence. With the provider response still
withheld, the same SIGKILL PTY test exited `130` in about four seconds,
retained a cancelled run and no acceptance, and restored exact terminal
settings. All seven focused Pi terminal tests passed, including SIGTERM,
idle SIGKILL, partial initialization and normal reopen.

The full checkout suite command
`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v`
ran **145 tests with eight skips and one error**: an existing OAuth browser
login Ctrl-C PTY test failed to observe process termination within its
20-second window, then its cleanup raised `PermissionError` on process-group
kill. The focused renderer tests passed; the full-suite result is **not
green**. A separate isolated OAuth diagnostic did present the login URL,
had `ISIG` off (Node readline owns the key), and completed with cancellation
status `130` within six seconds after Ctrl-C; that does not erase the
full-suite failure. The fixture diagnosis and subsequent rerun are recorded
below; neither matched coding efficiency nor external developer acceptance
is established.

## OAuth Ctrl-C PTY fixture reliability — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** The full-suite error above
occurred in a test that used `pty.fork()` after the long-running test process
had loaded other runtimes; whether inherited post-fork state caused that
timeout is **unproven**. Its cleanup attempted only process-group `SIGKILL`,
then raised `PermissionError`, masking the first assertion. A separately
spawned real PTY diagnostic presented the OAuth URL, accepted the Ctrl-C
byte with Node readline in raw mode, exited `130`, and kept credentials
unwritten.

The integration test now uses `pty.openpty()` plus a fresh `subprocess.Popen`
session, as the interactive terminal fixture already does, rather than
forking the entire loaded test process. It still sends the actual Ctrl-C
byte, checks the CLI's cancelled JSON/exit `130`, and verifies the isolated
auth file has no Codex credential. If group teardown is denied after a test
failure, it kills and reaps the direct child instead of hiding the primary
error. The focused test passed in **0.587 seconds**. The complete checkout
command `.venv/bin/python -W error::ResourceWarning -m unittest discover
-s tests -v` then ran **145 tests: 137 passed, eight skipped**. One green
suite does not establish that OAuth/network or multi-day login reliability
is solved; browser/device authorization was not completed.

## Empty-root discovery scope consistency — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** The retained post-search-fix
coding report (`report.json` SHA256
`7406b51081e7ed7756cd65bcf222961c06e14f9c576176accd940b183b88ab02`)
contains eleven `staged_files` calls with explicit `path: ""` in eleven of
twenty Goal Native phases. All eleven failed with “a relative staged path is
required”; `path: "."` on the same tool already meant the stage root. A
disposable real Sandbox also refused empty-root discovery, literal search
and regex search while the corresponding dot-root operations succeeded.

The shared staged path parser now treats the empty string as the root
**only for root-allowing discovery/search/regex**. File read/write/edit
paths remain nonempty and protected. Replaying all eleven recorded call
arguments against their corresponding retained **end-of-phase** candidates
produced eleven successful discoveries with exact results equal to dot-root
calls; ten reported truncation at the caller's declared file limit.
This establishes an API mismatch removed, not recovered historical
provider tokens, faster model completion or complete coverage of those
large repositories. Eleven focused repository-tool tests passed, including
one behavioral root/credential/file-denial regression.

The checkout's complete command `.venv/bin/python -W error::ResourceWarning
-m unittest discover -s tests -v` then ran **146 tests: 138 passed, eight
skipped**. This verifies the code path and existing safety tests, not a
matched provider-efficiency improvement.

## Renderer watcher startup cleanup — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL.** The Python controller previously
started its renderer-watcher thread **after** the guarded Pi startup block
and after redirecting `sys.stdout`. A real disposable PTY with injected
`threading.Thread.start` failure showed the initialization error surfaced,
but the Node renderer remained alive and the original TTY mode was not
restored even after Python exited. This is a rare resource-failure edge, not
evidence of ordinary TUI startup latency or coding efficiency.

Watcher startup is now inside the same guarded initialization that handles
`fdopen` and handshake errors. The caught error drives renderer cleanup
and exact terminal restoration before propagation. The PTY fault-injection
test exercises both failed `fdopen` and failed watcher startup, checks that
the renderer was reaped and that the exact original TTY attributes survived;
the watcher-start case failed those checks before the change and passed
afterward. All seven focused Pi terminal tests passed, including normal
reopen, active SIGTERM/SIGKILL cancellation and teardown exceptions.

The complete checkout command `.venv/bin/python -W error::ResourceWarning
-m unittest discover -s tests -v` then ran **146 tests: 138 passed, eight
skipped**. The watcher-start fault is synthetic; the PTY and Node child
were real.

## Independent publication audit and boundary repairs — 2026-09-25

**Classification: INCREMENTAL / EMPIRICAL for the previously exercised
work; these new repairs require their own executed checks below.** Three
read-only reviewers inspected the unpublished runtime, controlled-tool/native
evaluation code, and public source/privacy claims. The privacy review found
no credential values, private data or generated traces in the changed paths
but did not approve runtime security by itself. The source reviewers identified:

- The Pi renderer inherited the controller's environment, including potential
  provider keys and `NODE_OPTIONS`. It now starts with a presentation-only
  allowlist. This removes environment forwarding, not same-user filesystem
  access or a general process isolation guarantee.
- A renderer lost during work could trigger another interrupt while finalizing
  the persisted cancelled run's display. The controller now drops UI events
  after that renderer-loss cancellation instead of writing to the dead pipe.
- A completed invocation could pass the goal/assignment snapshot fence and
  start another staged tool call. New tool dispatch now requires a running
  invocation; trusted review/acceptance of historical evidence is separate.
- The native-pi adapter accepted lexical path comparisons and opened a
  manifest-supplied session file before its session-directory confinement.
  Canonical path and symlink checks now precede writes and session opening;
  same-user concurrent path replacement remains outside this pre-open guard.

The first post-review transport suite exposed a necessary distinction: two
existing real pi-provider loopback tests failed with `invocation is not
running` because the **next** provider request checked the preceding,
already-finished invocation as if it were another tool dispatch. The Worker
now requires that preceding turn to be terminal, then admits the next turn
through `Store.invoke`'s atomic current goal/assignment checks. Controlled
tools still require their **own** invocation to be running. The same six
focused CLI transport tests then passed.

The new PTY output assertions initially stalled the seven-test renderer suite:
after the CLI exited, `select` reported a readable EOF forever and two test
drain loops kept reading empty bytes. A timed stack dump identified each loop;
both now stop on EOF. All **seven** Pi terminal tests passed with real PTYs
and Node, including environment filtering, SIGTERM/SIGKILL active cancellation,
saved cancelled outcomes and exact TTY restoration.

The first native-path test run also exposed incomplete new fixtures; those
were corrected to send the actual bridge `run` protocol with a run ID and
canonical disposable paths. A separate real Node cold-session probe then
failed with `native session file is missing`: Pi chooses a fresh session path
*before* it creates the transcript file. Cold creation now confines that
reserved output path, while continuation requires an existing regular
non-symlink file before `SessionManager.open`. A disposable cold-to-continuation
probe reopened the same persisted reference, with controller admission
deliberately denied before either provider request. The permanent native
regression covers that successful reopen alongside forged paths; all **12**
coding-evaluation tests passed. It does not prove provider-backed model
completion after this repair.

The complete checkout command `.venv/bin/python -W error::ResourceWarning
-m unittest discover -s tests -q` ran **152 tests: 144 passed, eight skipped**.
The checks establish the repaired local boundaries, not general same-user
host isolation, external developer acceptance, or comparative efficiency.

A second independent read-only review found three further correctable edges:
the watcher could signal again after a broken event pipe had already raised
`KeyboardInterrupt`; Python continuation could follow a traversal-bearing
`session_key` to an outside manifest before the Node validator ran; and
`path.resolve()` had made the native adapter's absolute-path check vacuous.
The event pipe and watcher now elect one interrupt under the same lock;
Python validates the manifest key before reading it, and Node rejects relative
paths before resolution. Focused real-PTY and native suites then passed
**eight** and **12** checks respectively. The reviewer withdrew a separate
tool-dispatch TOCTOU objection after examining the documented rule that an
already-entered operation may finish inside an isolated stage; revocation
fences *new* dispatch, and trusted acceptance/effects have separate checks.

One complete-suite run subsequently caught an intermittent multiline PTY
test submission that left text in the editor without starting a request.
The synthetic `xterm` PTY had inherited the host's
`TERM_PROGRAM=Apple_Terminal`; Pi can reinterpret `\r` as Shift-Enter using
the real host's modifier state in that mode. The fixture now declares
`TERM_PROGRAM=xterm` to match its synthetic terminal. This is a plausible
environmental cause, **not** proof that the intermittent behavior is solved
for real Apple Terminal sessions. The final complete checkout command
`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -q`
ran **153 tests: 145 passed, eight skipped**. No provider-backed run was
repeated after the native-path repair; its positive proof is the persisted
cold-to-continuation session with deliberate pre-provider admission denial.
