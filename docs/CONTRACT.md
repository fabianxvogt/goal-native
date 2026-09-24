# Goal-Native Incremental Harness — acceptance contract

Source: owner-supplied consolidated proposal, 2026-09-24. This is its implementation traceability contract, not a claim that the gates have passed. The original user message remains the authoritative vision; this normalized contract does not replace it.

## Product

Audience: people directing coding, document/data, research and authorized application work across interruptions and changing requirements. The useful result is a persistent outcome with recoverable products, attributable evidence and explicit delivery state—not a reconstructed conversation.

Owner interface direction, 2026-09-24: session-first CLI. Opening the app should
present a request prompt, not goal setup. First requests create goals and
follow-ups update the request stream in the background. Goal/evidence/delivery
details remain inspectable, not mandatory onboarding. Reuse the existing
controller; no bookkeeping-only model stages or separate session database.

One local controller owns a transactional SQLite store, content-addressed artifacts, a bounded context compiler, an owned provider loop and controlled tools. One workspace per state directory. No recurring hosted service or inference subsidy. The default is draft-only; effects are limited to a local mock application. Source publication does not imply production-security certification.

## Required semantics

| Proposal sections | Implementation acceptance |
| --- | --- |
| 2–4: durable intent | Stable goal identities, direction/outcome distinction, parent navigation independent of authority, original requests, revision CAS, immediate input-version advance, explicit pause/cancel/draft. Plans/assignments/runs are not goal revisions. |
| 5–6: simple worker | Owned provider adapter, ordinary tool dispatch, software receipts/usage, optional sparse findings, actual unedited exchanges saved. No bookkeeping-only model stages. No invented semantic handoff. |
| 7–8: useful work | Immutable products retain producer, inputs, versions and limitations. Old products remain navigation/history; current evidence requires exact applicability. Search records declared scope/snapshot/exclusions/completion. Changed quantities require reassessment of omitted tiers; no universal automatic revalidation. |
| 9: context | Current intent and all binding requirements first; coherent bundles with qualifications; deterministic bounded optional selection; whole-context hard admission with output reserve; no silently omitted constraints. |
| 10: commitment | Originating invocation must have current relevant input, goal revision and assignment authority. Exact candidate/evidence/approval/target bound; commit atomically checks destination version. New invocation does not bless old requests. Unchanged human approval requires no model turn. |
| 11: isolation | Worker has staged resources only, no controller state/credentials/host agents/sockets/unrestricted network. Shell and children obey same OS boundary or fail closed. Source text is not policy. Local mock adapter states prepared/authorized/submitted/confirmed/unresolved; identity-based reconciliation, no blind retry. |
| 12: acceptance | Observation, support and acceptance distinct. Trusted checks and worker assertions distinguishable. Human/controller acceptance binds exact candidate and current contract. Historical acceptance is not current health. |
| 13: recovery | Replacement fences prior assignment; budgets include all invocations; reconcile known operations before retry. No uncontrolled parallel implementation or transfer of separate-branch evidence. |
| 14: interface | Goal view with work/evidence/delivery separately, changes, products, provenance, blockers, decisions, requests, traces, search and portable export/reopen. Errors and provider prerequisites visible. |
| 15–17: evaluation | Count capture/retrieval/reassessment/failures/retries and auxiliary work. Freeze workload/thresholds before results. Compare same settings/tools/safety to a strong real baseline and flat persistence; measure aggregate independently assessed quality, total tokens, invocations and rounds. No cherry-picked combination of quality and efficiency suites. |

## Four milestones

1. **Input-to-commit correctness:** stale invocation, replacement, revoked authority, changed target after approval, changed approval intent, immutable candidate mismatch, response loss and every exposed tool bypass.
2. **Worker-produced continuation:** idempotent job submission with concurrent reproduction, interrupted execution and changed requirement/code. Recovery uses only ordinary worker-produced products. Include interruption before finding emission; missing finding remains missing.
3. **Generalization:** supplier quote comparison 50→100 units, incomplete discounts/shipping extraction and deterministic arithmetic; mock application success with lost response and later revocation/replacement.
4. **Interface and lifecycle comparison:** cold and continued tasks, tiny tasks, newly invalid negative searches, misleading uncontradicted old explanation, changed verification configuration and unknown effects. Report false acceptance and unnecessary blocking, tails/variability, simple-task overhead and accounting gaps.

## Boundaries and evidence

No universal semantic dependency engine, taxonomy generator, unrestricted computer use, recursive agent organization, continuously reasoning supervisor, or automatic project-wide refresh. A local correctness demonstration is not a provider performance experiment. An implemented provider adapter is not proof of provider cancellation/accounting behavior until exercised with authorized live access. A real baseline is required; a deliberately forgetful toy baseline is prohibited.

Performance release gate remains Q(harness) > Q(baseline) and T(harness) < T(baseline) on the same frozen distribution, with non-increasing aggregate invocations/decision rounds as objective. Missing credentials, incomplete baseline integration or insufficient independent assessment keep this gate **unestablished**, never assumed passed.
