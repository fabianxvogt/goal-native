# Lifecycle evaluation contract

Status: **evaluation protocol implemented; no live comparative run claimed**.
The canonical Store/Worker control exists; a comparable native-session
baseline adapter and authorized complete evaluation remain outstanding.
The evaluator therefore proves only its registration, protocol validation,
deterministic fixture checks, and rejection of incomplete or unfair data. It
does not manufacture a baseline or call a paid provider.
Classification: **INCREMENTAL** — evaluation infrastructure and preregistered
claim gates only; no empirical superiority or novelty result.

The CLI's Codex subscription integration is separate from the current
explicit OpenAI API-profile evaluation driver. Do not pass a Codex model to
that driver or describe subscription fixtures as comparative evidence.
Pi's Codex schema lacks an enforced output-token cap; matched-budget
admission and the native-pi baseline must be resolved before a subscription
comparison can satisfy this contract.

## Scope and acceptance traceability

The executable package is `evaluation/`. It owns the frozen workload,
execution matrix, parent Worker API adapter contract, reporting, and safeguard
tests. No result is claimable unless every row below is satisfied.

| Acceptance requirement | Implementation/evidence | Claim gate |
| --- | --- | --- |
| Four arms, same model/tools/safety | `workloads.py` registers `strong_existing_harness`, `shared_efficiency`, `flat_durable`, and `goal_native`; `evaluator.py` rejects mismatched echoes | All registered arms present and uniform |
| Cold plus continuation | `execute_run()` requests both phases; continuation must link to the cold `continuation_ref` | Complete, unique phase/call matrix |
| Real provider-driven execution | `canonical_worker.py` imports only canonical `Store`/`Worker` and calls `Worker.run`; the evaluator does not import `server` or create a second provider/Worker loop. Other runner commands use the same protocol. | No scripted/fake/mock/stub/synthetic provider |
| Concurrent idempotent reproduction | Two cold/continuation calls share a coding repository and job key; check requires an actual two-worker reproduction plus one idempotency identity | Deterministic check passes for every arm/repetition/phase |
| Supplier quantity 50→100 | Fixture keeps quantity known, omits discount tier/shipping, and changes 50 to 100 on continuation; check validates independent arithmetic and unresolved C shipping | Deterministic check passes |
| Misleading memory explanation | Fixture supplies an old provisional explanation and current permission-denied evidence without a prelabelled conflict; check requires discovery and qualification | Deterministic check passes |
| New file invalidating negative search | Continuation mutation is versioned; check requires a post-mutation search and observed match | Deterministic check passes |
| Effect response loss | Fixture requests one lost commit response; check requires identity reconciliation and one applied operation | Deterministic check passes |
| Usage null handling | `normalize_usage()` retains unknown values as `null`; efficiency coverage excludes unknown samples | Full usage coverage for efficiency comparison |
| Variability and tails | Three preregistered repetitions; report includes median, nearest-rank p95, max, and coverage | Full matrix; p95 is descriptive, not proof of generality |
| Cost and output safety | `run` requires `--max-cost-usd`; output must be outside this repository; key is named, never copied into a request/result | Parent supplies explicit bounded budget |
| No novelty claim | Every report carries `no_novelty_claim: true` | Human review would still be required for any research claim |

A partial run is useful for debugging, but `analyze` exits non-zero and writes a
report with `claim_basis.claimable: false`.

## Frozen registration

`registration_document()` is hashed and written to `preregistration.json` at
run start. The shipped registration ID is
`goal-native-lifecycle-2026-09-24`; it has six scenarios, three repetitions,
and equal cold/continuation phase weights. Scenario weights are:

| Scenario | Weight |
| --- | ---: |
| Concurrent coding reproduction of duplicate job submission | 0.20 |
| Supplier quote reassessment 50→100 with omitted tier/shipping | 0.15 |
| Discovery of a misleading provisional memory explanation | 0.15 |
| New file invalidating a negative search | 0.15 |
| Tiny exact task | 0.10 |
| Effect response loss and reconciliation | 0.25 |

Weights sum to 1. Correctness is a separate gate from provider performance.
Provider requests contain the ordinary fixture input, not evaluator-only
expected answers. The evaluator does not turn a worker's self-reported score
into correctness; it inspects normalized events and fixture state. A worker
cannot hide an unresolved effect behind `status=completed`.

The four registered names are protocol roles, not four fake implementations:

- **strong_existing_harness**: the upstream Pi coding-agent/base harness in a
  native normal session, or an imported native transcript with ordinary
  captured state and digest. A raw forgetful loop is not a baseline.
- **shared_efficiency** and **flat_durable**: explicitly collapsed controls
  using the same canonical `goal_native.store.Store` +
  `goal_native.worker.Worker` capability as `goal_native`. They remain in the
  frozen matrix for traceability, but are not distinct architecture arms.
- **goal_native**: the canonical Worker adapter in
  `evaluation/canonical_worker.py`, which calls `Worker.run`.

The collapsed controls make the CLI executable without inventing variants.
`report.json` records the capability groups and keeps both claim gates false
until distinct arm implementations are supplied. A strong baseline is still
required; the report never relabels a weak loop as one.

If a strong baseline cannot be imported with comparable state and evidence,
there is no four-arm claim. The report says so rather than relabeling a weak
loop as a baseline.

## Running it

Evaluator-only registration/reporting commands use Python 3.9+ stdlib. The
canonical Worker adapter follows the project requirement of Python 3.11+ and
Node.js 22.19+. Output paths must be absolute and outside the repository.

```text
python3 -m evaluation plan
python3 -m evaluation register --out /tmp/goal-native-registration.json
python3 -m unittest -v evaluation.test_evaluator
python3 -m evaluation smoke
```

The retained canonical-control driver below explicitly uses the OpenAI API
profile and requires a separately configured API key. The native-pi
strong-baseline adapter is not implemented; this command cannot establish
the four-arm superiority claim:

```text
python3 -m evaluation run \
  --output /tmp/goal-native-eval-controls-20260924 \
  --model <explicit-model> \
  --api-key-env OPENAI_API_KEY \
  --tool-profile <shared-tool-profile> \
  --safety-profile <shared-safety-profile> \
  --max-cost-usd <parent-approved-bound> \
  --runner 'shared_efficiency=python3 -m evaluation canonical-worker' \
  --runner 'flat_durable=python3 -m evaluation canonical-worker' \
  --runner 'goal_native=python3 -m evaluation canonical-worker'
python3 -m evaluation analyze /tmp/goal-native-eval-controls-20260924
```

That partial run is deliberately non-claimable. A four-arm run remains
blocked until the parent supplies an actual upstream Pi native-session or
imported-native-transcript command adapter with equivalent safe tools, safety,
model and provider accounting. The evaluator will not replace it with a
forgetful loop or a custom provider implementation.

`--api-key-env` is the environment variable name, not a key value. The
executor refuses a missing named variable; it never searches secret files.
`NONE` is allowed only for an explicitly local runner. `--max-cost-usd` is a
parent-approved total run bound; the evaluator allocates it across the planned
matrix and sends both the total and per-cell bound to each driver. The parent
driver must enforce the per-cell bound before the provider request and retain
the total bound for its own accounting. A reported cost after an unbounded
call is not a valid budget proof.

Selecting `--scenario` is allowed for driver development but produces an
incomplete, non-claimable report. There is no command-line override for frozen
weights, thresholds, repetitions, or safety policy.

## Parent Worker API protocol

The evaluator communicates with one runner process per matrix cell through
stdin/stdout. It uses `shell=False`, sends exactly one JSON request, and
requires exactly one JSON response. Parent drivers may be HTTP clients,
Python adapters, or transcript importers, but the evaluator does not guess the
parent's transport.
The canonical goal-native adapter is `python3 -m evaluation
canonical-worker`. It imports the canonical `goal_native.store.Store`,
`goal_native.worker.Worker`, and their existing `goal_native.sandbox.Sandbox`
capability only, creates ordinary fixture state outside the repository, and
calls `Worker.run(goal_id)`. It never imports `server` and does not implement
a provider or worker loop.

Request shape (abridged):

```json
{
  "protocol": "goal-native-worker-request/v1",
  "arm_id": "goal_native",
  "scenario_id": "effect_loss_correctness",
  "repetition": 0,
  "phase": "cold",
  "call_index": 0,
  "concurrency_group": "effect_loss_correctness/0/0",
  "model": "explicit-model",
  "state_dir": "/tmp/goal-native-eval-20260924/worker-state/...",
  "run_cost_bound_usd": 1.0,
  "max_cost_usd": 0.00595,
  "input_digest": "...",
  "scenario": {"...": "frozen ordinary input and controlled fixture state"}
}
```

The continuation request carries the cold `continuation_ref`. The parent must
resume the durable state represented by that reference; replaying the prompt
from scratch is not continuation evidence. Concurrent calls with the same
scenario/repetition share the fixture job key and group and are submitted
concurrently by the evaluator.

Response requirements:

```json
{
  "protocol": "goal-native-worker-trace/v1",
  "trace_schema": "goal-native-worker-trace/v1",
  "status": "completed",
  "provider": {
    "kind": "actual-provider-or-imported-harness",
    "request_id": "provider-request-id",
    "model": "explicit-model",
    "evidence": "provider_reported_or_imported_transcript",
    "native_session": false,
    "transcript_imported": false,
    "session_id": null,
    "transcript_sha256": null
  },
  "configuration": {"tool_profile": "shared-tool-profile", "safety_profile": "shared-safety-profile"},
  "lifecycle": {"phase": "cold", "continuation_ref": "durable-ref"},
  "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cost_usd": null},
  "metrics": {"latency_ms": 123.0},
  "events": [{"kind": "...", "data": {"...": "..."}}],
  "observations": {"...": "normalized final state"}
}
```
For `strong_existing_harness`, the response must set either
`native_session=true` with a native `session_id`, or
`transcript_imported=true` with `transcript_sha256`. This prevents a bare
forgetful loop from entering the baseline arm.

A continuation response uses `lifecycle.resumed_from` instead of creating an
unrelated cold state. Usage fields may be absent or null. Absent output tokens
are not zero; absent total usage is null even if that makes a comparison
impossible. Provider evidence is provenance, not correctness: deterministic
checks still inspect event facts.
The canonical adapter does not substitute the evaluator request ID for a
provider request ID. If `goal_native.worker.Worker`/the pi bridge does not
expose the real provider request ID, that cell is rejected as incomplete
evidence rather than being promoted by inference.

The event vocabulary needed by the checks is deliberately small and explicit:
`code_reproduction`, `idempotency_result`, `quote_reassessment`,
`quote_calculation`, `memory_read`, `current_evidence`,
`memory_qualification`, `workspace_mutation`, `search`,
`effect_commit`/`effect_commit_attempt`, `reconcile`, and
`effect_reconciled`. Parent drivers may include additional events, but must not
rewrite a tool result as a provider fact or omit a safety-relevant effect
transition.

The canonical adapter is real Worker execution, not a correctness simulator.
If the canonical Worker/bridge does not expose a provider request ID or the
actual event needed by a fixture, the row is rejected. In particular, effect
loss and idempotency checks cannot be made to pass by writing evaluator-side
effects; they require the canonical Worker and its controlled tool/effect
surface to produce the evidence.

## Reporting and comparison

`report.json` contains four separate sections:

1. **fairness** — frozen registration, unique matrix keys, same model/tool/
   safety profile, continuation linkage, capability groups, and no synthetic
   provider evidence;
2. **completeness** — every arm/scenario/repetition/phase/call cell;
3. **correctness** — independently applied fixture checks and weighted pass
   rate;
4. **performance** — latency, total-token, and cost samples with coverage,
   median, p95, and max.

Efficiency ranking is null unless all observed arms have complete known usage.
Where available, relative values are ratios to the imported strong harness, not
proof that any architecture is generally superior. A result can be
correctness-complete but still not be efficiency-comparable when a provider
omits usage.

## Critical review of claim basis

These assumptions are limits, not results:

- The normalized trace is supplied by a parent adapter. The evaluator checks
  structure and cross-event invariants, but an adapter could still omit a
  relevant event. Independent raw trace capture or audit is needed before
  treating the evidence as strong.
- Exact fixture checks are intentionally conservative. They catch the listed
  state/effect failures but do not prove broad language quality, helpfulness,
  or safety outside these challenges.
- The six weights are frozen bookkeeping, not a population model. They encode
  owner-selected lifecycle risks and should not be generalized to all tasks.
- Three repetitions can expose obvious variability but cannot estimate stable
  tail behavior. p95 over this small preregistered sample is descriptive.
- An imported strong-harness transcript is comparable only if its model,
  tools, safety policy, ordinary captured state, and provider accounting are
  genuinely matched. Otherwise the fairness gate must remain closed.
- Provider/model drift, rate limits, and context-cache accounting can change
  performance. Unknown accounting remains null rather than being imputed.
- No result here establishes novelty, a theorem, or superiority. A future
  claim would require a complete report, an independently reviewed baseline,
  reproducible raw evidence, and human classification outside this evaluator.
