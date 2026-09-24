# Goal Native roadmap

## Now

**State: session-first CLI with recoverable budget stops and reviewed code delivery.**
Classification: `INCREMENTAL / EMPIRICAL` for exercised behavior, not a
quality, efficiency, novelty or general AI-safety claim.

- One Store and Worker; no daemon, extra planning agent or automatic host writes.
- Explicit `/budget` and `/continue`; even pre-invocation context stops retain
  their run outcome and selected files in a fresh-stage continuation workflow.
- Visible source selection, immutable baselines, `/diff`, and review-bound
  patch/ZIP export. Source divergence and stale reviews fail visibly.
- Source bootstrap plus `./goal`; doctor checks safe, clean, pinned source and
  real pi imports separately from credentials.
- [Verification](docs/VERIFICATION.md): 86 application checks, 4 evaluator
  safeguards, fresh source installation, and live multi-file Luna coding through
  interruption/restart/changed requirements/export with independent checks.

## Next

1. **Owner dogfooding:** use the complete CLI loop on selected Python projects;
   assess source-selection and review ergonomics within the documented limits.
2. **Browser, only after the CLI loop is comfortable:** one composer, session
   list and result view; move goal/evidence/effect forms behind details.

Keep wider provider coverage, goal taxonomies, parallel-agent orchestration
and new effect adapters out of this UI pass. The native-pi comparison baseline
and independent lifecycle assessment remain required before efficiency claims,
not prerequisites for a useful CLI.

## Later

- Additional effect adapters only after receiver-side atomicity, operation identity, revocation and recovery contracts are explicit and tested.
- Parallel implementation only with isolated resource ownership and exact combined-candidate checks.
- Portable OS isolation only with equivalent bypass tests; never silently fall back to privileged local execution.

## Done

- Repository initialized; API/module ownership and [acceptance contract](docs/CONTRACT.md) established.
- Canonical durable goals, revisions, immutable artifacts, invocation fences,
  acceptance, local mock effects, reconciliation and non-authoritative import.
- Actual cloned pi provider/agent runtime with bounded context admission,
  exact payload capture, per-turn receipts and unknown-preserving usage.
- CLI lifecycle, resume/export/import, retained per-run staging and Ctrl-C
  control fencing; local HTTP transport and macOS staged execution exercised.
- Codex-first OAuth login/status/logout/model discovery through pi; refresh,
  subscription transport, token isolation and terminal cancellation verified
  with real runtime paths and explicitly synthetic protocol credentials.
- [56 application checks and 4 evaluator safeguards](docs/VERIFICATION.md)
  passed; independent reviews retain explicit trusted-host and live-account limits.
- Owner-reported live Luna Fibonacci run; independently exercised changed
  requirement 55→210 with saved source, isolated replacement stage and explicit
  32768 context budget after a documented default-budget stop.
- `show --summary` separates goal status, invocation completion, recorded CLI
  outcomes, normalized usage and qualified artifact metadata. Completed turns
  do not imply successful runs or acceptance.
- CLI `--context-budget` reuses canonical admission; run outcome receipts
  preserve stage recovery metadata. 33 focused model-free checks passed.
- Session-first CLI; lazy creation, persisted request/artifact reopening,
  draft-only follow-ups, multiline input, terminal-control filtering and
  same-process file carry-forward. 61 application checks passed.
- Actual terminal and live Luna follow-up: original `55`, a retained stale
  `55` failure, then `210` after latest-request precedence repair. Synthetic
  provider cancellation returned to the prompt with paused/fenced work.
- Streamed assistant replies with nonduplicated completion and compact tool
  progress; raw traces/thinking blocks hidden from the default terminal.
- Automatic cross-process local file recovery, fresh isolated copies, explicit
  `--fresh`, missing/symlinked-path rejection, and no recovery metadata import.
  Replaced assignments cannot roll recovery back. 66 application checks passed.
- Actual Luna terminal: script output `30`, exit, reopen without file flags,
  automatic file copy and revised output `55`; goal remained draft.
- Budget-stop recovery before the first invocation; separate assistant text,
  structured diagnostics, admission headroom and explicit request-preserving continuation.
- Immutable selected-source baselines, nested ignore handling, stale-review
  rejection, patch/file export and independent apply/check proof after live coding.
- Multi-file Python imports within the existing sandbox; no shell/host-write cutover.
- Clean-checkout bootstrap, exact pi pin checks, `./goal`, and actionable
  runtime-versus-credential diagnostics; installed workflow exercised outside checkout.
- Independent-review repairs: persisted partial-stream cancellation, explicit
  generated-path artifacts, external-interpreter refusal, shared pi source checks,
  and native credential-path regex enforcement at root and nested paths.

## Release record

- Source destination: [fabianxvogt/goal-native](https://github.com/fabianxvogt/goal-native) — session-first CLI source with bounded live Luna continuation evidence.
- Try it: local only; the controller processes private work and arbitrary code, so a public shared-host service is not appropriate.
- Runtime: Python 3.11+; isolation support and exact tested OS recorded in evidence.
- Persistence/export: SQLite + immutable artifacts; portable versioned JSON, import into empty state only.
- License: MIT.
- Evidence navigation: [docs](docs/README.md).
