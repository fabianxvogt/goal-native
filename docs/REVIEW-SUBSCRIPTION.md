# Codex subscription and supervisor review

## Decision

**Bounded approval for the CLI-first Codex subscription path and the settled
supervisor repairs.** The source and retained proof support the claims that
Goal Native selects an explicit pi-catalog Codex model, delegates OAuth and
refresh to pi, keeps the credential store outside workspace state, does not
fall back to API billing, captures the provider payload before submission, and
preserves provider-reported usage without inventing values.

This is `INCREMENTAL / EMPIRICAL` evidence. It is not approval of a live
account, subscription entitlement, live-model quality, latency/performance,
matched output-budget comparisons, other providers, or the paused UI.

## Reviewed surface and evidence

- `bridge/auth.mjs`, `bridge/agent.mjs`, `goal_native/cli.py`,
  `goal_native/worker.py`, `package.json`, `README.md`, `docs/API.md`, and
  `docs/ARCHITECTURE.md` were inspected. No source or test was changed by this
  review.
- **[REPORTED]** The current `tests/test_subscription.py` four-test set passed,
  including a real controlling-PTY browser-login cancellation regression. The
  retained subscription transport regressions cover Python CLI → Node → pi
  Codex adapter transport with synthetic expired-OAuth refresh, persistence,
  two SSE turns, a saved artifact, and usage.
- **[REPORTED]** The malformed-refresh fixture passed without token output or
  credential replacement. Its test-only Node preload intercepts OAuth HTTP and
  redirects Codex HTTP to a loopback fixture; production has no endpoint
  override. A targeted actual-CLI regression now compares the entire parsed
  credential store (`access`, `refresh`, `expires`, and `type`), not incidental
  byte serialization.
- **[REPORTED]** Default `auth-status` was `configured: false`; `models`
  listed eight upstream Codex IDs. TTY browser-login cancellation was observed.
- **[REPORTED]** The final settled suite passed 56/56 under Python 3.12 with
  `-W error::ResourceWarning`; the flag was enabled and produced zero warnings
  or errors. It includes six subscription tests covering expired OAuth refresh,
  Codex SSE two-turn transport, token redaction, and controlling-PTY early
  Ctrl-C. The five retained supervisor checks also passed.
- **[REPORTED]** Parent CLI staged-write plus staged-run completed three
  provider turns with exit 0 and stdout `42` through the OS sandbox. No check
  was rerun for this review.
- **[REPORTED]** `doctor` now checks all three pinned pi packages plus
  `auth-storage.js`; the actual doctor run reported Python, Node, pi, and the
  sandbox healthy, with only Codex authentication `configured: false`.

## Settled checks

- **Credential ownership — bounded pass.** `auth.mjs:6-8,73-109` uses pi's
  `AuthStorage` and provider model registry; `agent.mjs:238-250` uses the same
  store for Codex execution. No project OAuth implementation or token-bearing
  Python result exists. `AuthStorage` remains the source of refresh and its
  cross-process locking contract (`docs/ARCHITECTURE.md:13-18`).
- **Location and stage boundary — bounded pass.** `cli.py:329-338` rejects
  auth paths resolved inside state or the selected source; `worker.py:321-333`
  passes credentials only to the controller bridge environment for the explicit
  API provider. Codex receives an auth-file path, not tokens, and API-key/base
  URL variables are not forwarded to its bridge. Existing staging evidence and
  README exclusions remain name-based, not a general secret scanner.
- **No API-billing fallback — pass.** `cli.py:364-379` requires configured
  Codex OAuth or an explicitly selected `openai` API credential. `worker.py:381-386`
  rejects a Codex API key; `agent.mjs:231-264` rejects Codex base-URL override
  and resolves only the requested provider/model. The retained ambient-key
  test exercises the refusal before invocation creation.
- **Exact model/provider and payload — bounded pass.** `agent.mjs:223-250`
  performs explicit catalog lookup; `agent.mjs:291-323` forces Codex SSE,
  disables retries, and installs the real pi payload hook. `worker.py:551-565`
  fences and receipts the JSON-safe serialized body before submission. The
  fixture decoded `gpt-6-astra` and completed two actual adapter turns.
- **Usage semantics — bounded pass.** `worker.py:775-807` sources raw usage
  from provider stream data, stores normalized input/output/total plus cached
  and reasoning subsets, and leaves raw/normalized usage unknown when the
  provider reports none. The retained success and provider-error fixtures
  cover both cases. Codex's missing output-token wire cap is explicitly
  documented in `docs/API.md:35-40` and `docs/ARCHITECTURE.md:33-38`.
- **Redaction and JSON errors — bounded pass.** Auth status/logout return
  metadata only; `auth.mjs:122-127,240-247` redacts known token forms and
  normalizes unexpected auth failures; `agent.mjs:74-100` sanitizes wire
  objects and turns authentication failures into generic errors. The retained
  malformed-refresh and export assertions found no fixture credential.
- **Malformed refresh integrity — resolved in targeted regression.** The
  parsed credential store remains unchanged across the rejected refresh
  response, while token material remains absent from CLI output and exports.
  This intentionally asserts contract fields rather than serialization format.
- **Auth CLI cancellation — resolved in source and regression.** `cli.py:359-363`
  maps Node's `{type: "cancelled"}` result to `CLICancelled`, and `main()` emits
  the JSON cancellation envelope with exit `130`. `auth.mjs:145-164` combines
  the top-level controller with each provider prompt signal and closes the
  pending readline; `auth.mjs:192-195` truthfully tells users to open the URL
  manually. **[REPORTED]** The PTY regression failed before this repair and now
  exits `130`, returns JSON `status: "cancelled"`, and leaves no Codex
  credential, including the immediate-at-URL cancellation case.
- **Supervisor repairs — accepted under the existing contract.** The source
  gates concurrent Worker/bridge runs, fences assignments and invocations,
  closes pipes, clips staged-run timeouts, and records provider usage. Abort
  remains cooperative: an already-entered controller transaction may finish
  as untrusted historical work (`docs/ARCHITECTURE.md:52-60`).

## Prioritized residuals

1. **P1 — locking is source-backed, not concurrency-tested here.** The
   integration correctly delegates to pinned pi `AuthStorage`; no retained
   two-process refresh race proves that lock under this project. Keep the
   upstream pin and add that proof before making stronger concurrent-refresh
   claims. This is an evidence gap, not an observed token-loss defect.
2. **P2 — retain documented privacy limits.** Full task content and provider
   payloads are intentionally durable; name-based staging exclusions are not
   secret detection, and generic error sanitization is not proof against every
   provider-specific diagnostic format. No credential leak was observed in
   the retained paths.
3. **P2 — live acceptance remains open by design.** Owner-authorized live
   login/model execution, entitlement, provider behavior, performance, and
   stronger sandbox claims remain outside this fixture review.

`docs/REVIEW-BRIDGE.md` residual statuses remain accurate: this review adds
subscription evidence but does not supersede its cooperative-cancellation,
excluded-Sandbox, or live-provider boundaries. No bridge status update is
warranted.
