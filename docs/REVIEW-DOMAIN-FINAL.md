# Final domain review

**Disposition: BOUNDED APPROVAL.** R1/R3/R4/R5 are closed for the settled local runtime and mock-destination paths. The parent context compiler preserves binding intent and qualification closure under its documented complete-artifact input. Approval is bounded by the stated trusted Python controller / OS-isolated worker model, and does not treat imported delivery data as verified local execution.

## Evidence boundary

- **REPORTED, not rerun:** RuntimeFix14 store/lifecycle/server tests passed; an actual `Store` smoke committed expected versions 0 then 1 and produced destination version 2; an earlier lost-response operation reconciled after overwrite with confirmed gateway state; export/import represented both effects as imported/unverified; Context5 regressions passed; an interrupted handoff retained source, finding, limitation and constraint. The owner also observed the browser export/import of an accepted+committed workflow: effects were imported/unverified, accepted rows and trusted evidence were preserved, and the UI displayed the human-check/accepted state with an explicit imported-provenance notice. This is UI evidence, not proof of import authenticity or an action-bypass test.
- **REVIEW:** current source and contracts in the requested files only. `docs/REVIEW-RUNTIME.md` is the historical pre-fix review and its NOT APPROVED disposition is superseded by this bounded review. No discovery tests were run.

## Finding closure

- **R1 — sequential destination replacement:** closed. `commit()` uses the expected-version CAS without a permanently-null operation condition (`goal_native/store.py:1357-1388`), records every successful write (`1372-1384`), and the history table is immutable (`208-225`). The retained sequential scenario is `tests/test_store.py:136-156`.
- **R2 — authority boundary:** accepted under the explicit threat model, not as same-process Python capability security. The Store module identifies the trusted controller and excludes Store/authority APIs from untrusted code (`goal_native/store.py:1-8`); the API and architecture require OS isolation, no arbitrary plugins, and no Store capability in worker/task code (`docs/API.md:3,26-28`; `docs/ARCHITECTURE.md:18-24,30`). No meaningless same-process capability requirement is imposed.
- **R3 — imported delivery claims:** closed for executable effects, but not an authenticity claim. Import forces every effect to `state='imported'` (`goal_native/store.py:2195-2217`), while public output marks it imported and unverified (`498-523`); history/effect identity is checked when history is present (`2219-2249`). Accepted rows and evidence are preserved as valid chains, not reverified facts. The retained tampered-claim proof is `tests/test_store.py:183-203`.
- **R4 — provenance:** materially closed for chain integrity. Invocation/assignment snapshots, artifact applicability and hashes, context-artifact binding, evidence chains, effect chains and acceptance chains are checked before insertion (`goal_native/store.py:1862-2000,2042-2077,2166-2297`). Runtime preparation also rechecks the exact invocation/evidence/candidate chain (`1273-1297`). Imported `passed`/`trusted` bits are validated as booleans and relationships, not authenticated as having been produced by the claimed human.
- **R5 — snapshots/history:** closed for enriched goal reads and export. One SQLite read transaction covers the full read (`goal_native/store.py:72-82`), including `goal()` (`781-820`) and `export()` (`1627-1635`). Operation history remains append-only and supports reconciliation after overwrite (`208-225,1398-1458`).

## Context compiler assessment

Binding request fields are always constructed before optional material (`goal_native/context.py:142-147,183-217`). Required assumptions/blockers/open questions recursively retain inputs and recorded qualifications/contradictions (`153-185`); oversized optional bundles are omitted as bundles, while required material fails visibly. UTF-8-byte estimation and the complete input/schema/output/reserve admission run before return (`53-80,117-125,215-254`). Candidate, history and directory bounds are explicit (`227-252`). The regressions cover intent preservation, paired contradiction handling, required-budget failure, Unicode accounting and schema admission (`tests/test_context.py:17-64`).

## Imported evidence, acceptance and currentness

Import preserves `passed` and `trusted` on evidence after checking only their types and the invocation/artifact chain (`goal_native/store.py:2042-2077`). It also preserves acceptance rows after checking the candidate chain (`2251-2297`). That is valid structural provenance, not proof that an imported human actually performed the check. Import itself does not call `accept()` or create a new effect.

The effect path is fenced after either kind of change: every request increments `input_version` (`goal_native/store.py:830-851`), and `_effect_context()` requires a fresh revision/input/authority snapshot plus an active assignment (`1201-1244`). The same input-version check prevents `accept()` from newly accepting the old evidence after a request (`1492-1512`), although preserved acceptance rows remain historical records rather than being deleted. Imported effects are not approvable or committable because their state is forced to `imported` (`2199-2217`).

There is one acceptance-specific gap below: `accept()` checks revision and input version, but not assignment activity (`goal_native/store.py:1492-1512`). Thus a later `assign()` can fence the old assignment (`907-915`) while leaving the goal snapshot unchanged; a controlled caller could still accept an imported trusted check from that fenced invocation. This is not an untrusted-worker bypass under R2, but it is not an explicit reassessment/adoption marker either.

## Remaining actionable findings

### F1 — MEDIUM: a versioned imported destination can lack operation history

The destination import validates rows at `goal_native/store.py:2080-2109`, but current-destination/history consistency is guarded by `if history_rows` (`2139`). A tampered export containing a versioned destination and no history therefore imports; `destination()` exposes it without an unverified marker (`1471-1481`). The effect itself remains imported/unverified, but the destination can still seed later local CAS after explicit authority is granted. **Required action:** require matching immutable history for every version greater than zero, or model imported destinations as explicitly unverified and exclude them from local delivery state. **Acceptance:** the tampered shape is rejected or remains visibly and operationally quarantined.

### F2 — LOW/MEDIUM: import does not enforce the active-assignment invariant

Normal assignment creation fences all active assignments (`goal_native/store.py:907-934`), but the schema permits multiple active rows (`115-123`) and import validates only each row independently (`1827-1860`). Two active assignments for one goal can therefore be imported and both invoked. **Required action:** enforce one active assignment per goal with an import check or a database invariant. **Acceptance:** a two-active-row import is rejected (or deterministically fences one before invocation).

### F3 — LOW: qualification closure is silent when supplied artifacts are incomplete

The compiler builds `by_id` only from supplied products (`goal_native/context.py:149-152`), and `bundle()` silently skips absent input IDs (`169-180`). A required artifact can thus be admitted with an unresolved recorded input link when the caller supplies a partial artifact set. **Required action:** reject incomplete required/qualification closure or emit an explicit omitted-dependency marker. **Acceptance:** partial-input compilation cannot present an incomplete bundle as closed.

### F4 — MEDIUM: assignment fencing does not invalidate imported evidence for acceptance

`accept()` requires the current goal revision/input version and `passed`/`trusted` evidence (`goal_native/store.py:1492-1512`), but it does not require the evidence's assignment to remain active. Import preserves a `trusted=True` bit as a structurally validated claim (`2042-2077`), and `assign()` fences the prior assignment without changing the goal snapshot (`907-929`). After that assignment replacement, a controlled caller can still accept the old imported check; the effect path correctly rejects it through `_assert_invocation_fresh()` (`1201-1244`). **Required action:** make acceptance require an active/current assignment, or add an explicit controller adoption operation that records why imported evidence is accepted without reassessment. **Acceptance:** after import plus assignment replacement, old imported evidence cannot create a current acceptance; a new invocation must receive a fresh controlled assessment, or an explicit adoption record must be visible.

## Bounded approval

Approve the settled R1/R2/R3/R4/R5 repairs and context behavior for the exercised local/mock workflow and the stated OS isolation boundary. F1/F2/F3/F4 are now closed by the focused source changes and retained proof documented below. Do not treat preserved imported evidence or acceptance rows as authenticated human work; they remain valid chains and historical claims. The parent’s final actual-application smoke after all lanes is still required before release-wide approval.

## Closure update — settled parent-lane changes

**REPORTED, not rerun:** `tests/test_import_boundaries.py` covered all three closed findings: each failed before the fix because the expected exception was absent, and all three passed after the fix. The combined Store/context/lifecycle proof reported 19 passing tests. The new sixth context regression also passed. This is retained behavioral proof only; it does not authenticate imported records or replace the pending final application smoke.

- **F1 closed:** destination-history validation now runs unconditionally for every imported versioned destination (`goal_native/store.py:2140-2151`). A versioned destination without matching target/version/content/operation history is rejected. The focused proof is `tests/test_import_boundaries.py:24-32`.
- **F2 closed:** import writes every assignment as `fenced`, regardless of the exported status (`goal_native/store.py:1828-1860`). Imported assignments cannot revive worker execution; the duplicate-assignment proof attempts invocation through both identities and expects `PermissionError` (`tests/test_import_boundaries.py:34-43`).
- **F3 closed:** context products now carry `unavailable_inputs` and an explicit `bundle_limitation` when a recorded dependency is absent (`goal_native/context.py:162-175`). The sixth regression verifies the missing ID is visible without changing the recorded limitation (`tests/test_context.py:65-71`).
- **F4 closed:** `accept()` now invokes `_assert_invocation_fresh()` (`goal_native/store.py:1505-1513`), so assignment fencing blocks acceptance of imported evidence from the replaced invocation. The focused proof is `tests/test_import_boundaries.py:45-49`.

The import boundary still validates chains and preserves claims; it does not prove that imported `trusted=True` evidence was actually produced by the claimed human. Rejection of old exports whose history lacks receipts is intentional pre-release behavior, with no compatibility shim.

**Closure disposition:** F1/F2/F3/F4 are closed for the reviewed source and retained focused proof. Domain approval remains bounded to the trusted-controller, OS-isolated worker and local/mock workflow; the parent’s final actual-application smoke after all lanes is still required before release-wide approval.
