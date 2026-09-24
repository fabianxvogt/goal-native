# Goal Native roadmap

## Now

**State: session-first CLI; automatic background goal bookkeeping.**
Classification: `INCREMENTAL / EMPIRICAL` for exercised behavior, not a
quality, efficiency, novelty or general AI-safety claim.

- Bare CLI opens a clean prompt. First request creates the goal; follow-ups
  reuse it. `/new`, `/sessions`, `/resume` and `/status` hide lifecycle plumbing.
- Keep one Store and Worker, no goal-inference agent, daemon or new session DB.
- Current requests are explicit model input, not buried beneath the original
  outcome. Retain the failed live follow-up and passing repair in
  [verification](docs/VERIFICATION.md); provider completion is not task correctness.

## Next

1. **Terminal feedback:** stream responses and show one compact current-tool/
   working line. Keep traces, goal metadata and acceptance controls out of the
   default view. Preserve a reliable stop/continue path.
2. **Reopen without file bookkeeping:** safe local-stage continuation across CLI
   restarts, with explicit missing-stage recovery. Never follow imported paths
   as authority to read host files. Same-process carry-forward already works.
3. **Budget recovery:** expose remaining headroom and an actionable explicit
   continuation choice. No silent budget expansion or dropped constraints.
4. **Startup:** reliable source install and a short launch command; Python,
   Node and the pinned pi build are still prerequisites. A wheel alone is not
   a complete distribution.
5. **Browser, only after the CLI loop is comfortable:** one composer, session
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

## Release record

- Source destination: [fabianxvogt/goal-native](https://github.com/fabianxvogt/goal-native) — session-first CLI source with bounded live Luna continuation evidence.
- Try it: local only; the controller processes private work and arbitrary code, so a public shared-host service is not appropriate.
- Runtime: Python 3.11+; isolation support and exact tested OS recorded in evidence.
- Persistence/export: SQLite + immutable artifacts; portable versioned JSON, import into empty state only.
- License: MIT.
- Evidence navigation: [docs](docs/README.md).
