# CLI and Codex verification — 2026-09-24

Classification: **INCREMENTAL / EMPIRICAL**. These are implementation and
protocol observations, not model-quality, efficiency or general safety proofs.
This checkpoint remains CLI-first; the browser interface and other providers were not redesigned.

## Exercised environment

Darwin 25.5 / arm64; Python 3.12.12; Node 22.23.2. Pi source is the submodule
at `a7d17e39aaa0091c7573d0790714751956f10bd1`, package version `0.87.1`.
`npm run build:upstream` passed, including the cloned credential-store build.

## Budget recovery and reviewed coding delivery

**82 application checks passed** with ResourceWarning treated as an error.
The five installation checks also passed after the dependency/doctor repair;
all **4 evaluator safeguards** and `python -m evaluation smoke` passed. These
are correctness checks, not a comparative model evaluation.

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
4. The native-pi comparison adapter and matched-budget subscription evaluation
   remain outstanding. Collapsed controls, synthetic protocol responses and
   passing tests establish no comparative efficiency or model-quality gain.
5. The browser still exposes manual goal creation and many controls. The CLI now
   streams and reopens local files. Budget recovery, staged-diff review/export
   and a simpler installation path remain the next product gaps.
