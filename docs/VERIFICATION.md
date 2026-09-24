# CLI and Codex verification — 2026-09-24

Classification: **INCREMENTAL / EMPIRICAL**. These are implementation and
protocol observations, not model-quality, efficiency or general safety proofs.
UI development and additional subscription providers remain deferred.

## Exercised environment

Darwin 25.5 / arm64; Python 3.12.12; Node 22.23.2. Pi source is the submodule
at `a7d17e39aaa0091c7573d0790714751956f10bd1`, package version `0.87.1`.
`npm run build:upstream` passed, including the cloned credential-store build.

## Executed checks

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
- Actual default `auth-status` returned `configured: false`. No live account
  authorization or subscription inference was performed.
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

1. **Owner action:** run `python -m goal_native login`, open its URL, then use
   `auth-status` and an explicitly selected catalog model for live CLI acceptance.
   An existing pi-format store can be selected with `--auth-file`; no secret
   should be pasted into chat. Catalog membership does not prove entitlement.
2. Codex's pinned request schema has no enforced output-token cap. Receipts
   disclose that capability; input admission and cooperative time/round limits
   are not a substitute for an output cap or complete resource isolation.
3. The trusted-host macOS Python sandbox is not a VM. Same-user host tampering,
   runtime disk/memory quotas and content-immutable search snapshots are outside
   the reviewed guarantee. See [sandbox review](REVIEW-SANDBOX.md).
4. The native-pi comparison adapter and matched-budget subscription evaluation
   remain outstanding. Collapsed controls, synthetic protocol responses and
   passing tests establish no comparative efficiency or model-quality gain.
5. The preserved browser interface is not the current release gate. No further
   UI work resumes until the owner-required live CLI gate is satisfied.
