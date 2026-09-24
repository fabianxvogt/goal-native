# CLI and Codex verification — 2026-09-24

Classification: **INCREMENTAL / EMPIRICAL**. These are implementation and
protocol observations, not model-quality, efficiency or general safety proofs.
This checkpoint remains CLI-first; browser UI and other providers were not changed.

## Exercised environment

Darwin 25.5 / arm64; Python 3.12.12; Node 22.23.2. Pi source is the submodule
at `a7d17e39aaa0091c7573d0790714751956f10bd1`, package version `0.87.1`.
`npm run build:upstream` passed, including the cloned credential-store build.

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
5. The preserved browser interface is not a finished UI release. Its next
   implementation/verification pass is no longer blocked on basic live login.
