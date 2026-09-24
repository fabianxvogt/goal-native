# Durable runtime correctness/security review

**Disposition: NOT APPROVED.** The store has a sound first-write transaction shape, but the settled implementation has a blocking sequential-commit defect and several authority/import integrity gaps. This review covers only `goal_native/store.py`, `goal_native/__init__.py`, `tests/test_store.py`, `tests/test_lifecycle.py`, `docs/API.md`, and `docs/CONTRACT.md`. No tests or discovery were rerun.

## Evidence boundary

- **REPORTED:** implementation-lane `test_store` completed with 5 passing tests.
- **REPORTED:** lifecycle smoke and concurrent CAS showed exactly one winner.
- **REPORTED:** a fresh-DB sequential destination smoke committed version 1 with `report1`, then a second prepared/approved effect against expected version 1 failed at commit with `PermissionError: destination compare-and-set rejected the effect`.
- **REPORTED:** quantity/revision and missing-semantic lifecycle checks passed. Response-loss content/version/operation-id invariants remain; the incidental final status wording assertion was removed after observing the reconciled state.
- **REVIEW:** findings below are static code/contract analysis, with exact source locations.

## Findings

### RUNTIME-1 — BLOCKER: every destination after the first is permanently unwritable

`commit()` performs the destination CAS at `store.py:1304-1317`, but its `UPDATE` requires `operation_id IS NULL` (`store.py:1306`). The first successful commit necessarily writes a non-null operation ID at `store.py:1305-1308`. The retained fresh-DB smoke confirms the consequence: the first effect committed version 1, while a second effect prepared and approved for expected version 1 failed with `PermissionError: destination compare-and-set rejected the effect`. The mock destination is effectively write-once, contrary to the replacement/versioned-destination contract.

**Required fix:** make the CAS fence the expected version and the intended operation state without requiring a permanently-null operation ID. Preserve operation identity for the committed write. Add the sequential-update scenario to the correctness proof before approval.

The response-loss follow-on also needs an explicit design decision. `reconcile()` only recognizes the **current** destination operation ID (`store.py:1331-1355`). If a later operation overwrites that ID, an earlier unresolved operation cannot be confirmed from the destination row. That conservative result is preferable to falsely confirming by version/content alone, but it leaves the earlier operation unresolved forever unless an operation history/receipt is retained. Do not weaken identity checking while repairing RUNTIME-1.

### RUNTIME-2 — HIGH: controller/human authority is a caller convention, not an enforced boundary

The package exposes `Store` directly (`goal_native/__init__.py:3-5`), and sensitive methods accept ordinary caller-controlled values. Any code holding the store can call `request(..., control='allow_effects')` (`store.py:762-803`), set `trusted=True` in `verify()` (`store.py:1095-1133`), then call `prepare_effect()`, `approve()`, and `commit()` (`store.py:1203-1329`). It can also call `accept()` directly (`store.py:1379-1432`). There is no unforgeable controller capability, actor identity, or separate worker-facing object.

This directly conflicts with the contract's requirement that trusted verification and authority never be worker-authorized. Omitting these methods from a worker tool schema is useful defense-in-depth, but does not make the store boundary enforceable if a worker/plugin receives the `Store` object or can reach this API.

**Required fix:** put sensitive operations behind a controller-only capability/interface that worker code cannot forge; keep the worker facade limited to untrusted records/proposals. Do not use a boolean such as `trusted=True` as the authority proof.

### RUNTIME-3 — HIGH: import can fabricate a confirmed delivery state without a matching destination operation

Artifact IDs and content hashes are checked (`store.py:1735-1805`), but imported effect state is trusted too broadly. If an imported effect says `state == 'committed'`, import preserves that state (`store.py:1893-1934`) without requiring a destination row whose target, version, content, and `operation_id` match the effect. A minimally edited export can therefore retain valid artifact/evidence hashes, change only the effect state to `committed`, omit the destination, and reopen with `_effect_public()` reporting `gateway_state='confirmed'` (`store.py:453-474`). `commit()` then returns immediately for the fabricated committed state (`store.py:1287-1294`) and will not repair or execute anything.

The forced `effects_allowed = 0` on imported goals (`store.py:1608-1628`) prevents this from reviving permission to execute, which is good, but it does not prevent false delivery/audit evidence. The export/import contract requires provenance validation, not merely candidate-content validation.

**Required fix:** when importing a committed effect, require and validate the matching destination operation atomically; otherwise import it as a non-confirmed historical state with an explicit unverified marker. Never accept a free-form committed state as proof of delivery.

### RUNTIME-4 — HIGH: imported provenance relationships are under-validated

Import relies on individual foreign keys but does not validate several required relationships. Invocation insertion does not check that the assignment belongs to the same goal or that its snapshot matches (`store.py:1713-1733`). Evidence insertion does not check that its invocation and artifact belong to the same invocation/goal (`store.py:1857-1877`). Effect import checks artifact/evidence invocation binding but does not verify the effect goal against the artifact goal or validate the destination relation (`store.py:1893-1935`). Acceptance rows are inserted without checking that goal, artifact, and evidence form one exact current-candidate chain (`store.py:1937-1949`).

An attacker can therefore produce a syntactically valid import with individually existing IDs but contradictory provenance, and the reopened workspace will display those records as if they were one chain. Some later effect paths reject the contradiction, but durable audit/history is already polluted.

**Required fix:** validate every cross-record invariant before insertion (or use composite foreign keys where appropriate), including goal/assignment/invocation snapshots, evidence invocation/artifact equality, effect goal/destination consistency, and acceptance candidate/evidence/goal equality. Keep the import transaction all-or-nothing, as it currently does.

### RUNTIME-5 — MEDIUM: export and enriched goal reads are not point-in-time snapshots

`export()` holds only the per-instance Python lock and runs many independent SELECTs (`store.py:1434-1514`, `1516-1524`). The contract explicitly permits separate `Store` instances per thread, so another connection can commit between those reads. The result can combine different revisions, requests, invocations, effects, and destinations rather than representing one durable workspace state. `goal()` has the same multi-query shape (`store.py:721-760`). This is a TOCTOU consistency problem even though the commit CAS itself is transactionally serialized by `BEGIN IMMEDIATE` (`store.py:58-68`).

**Required fix:** read export and enriched views from a consistent SQLite read transaction/snapshot. Retain the existing write-side CAS; a Python `RLock` alone cannot coordinate independent store connections.

## What is presently sound

- The write transaction uses `BEGIN IMMEDIATE` and rolls back on failure (`store.py:58-68`).
- Preparation and commit recheck goal revision, input version, authority version, assignment status, trusted passing evidence, candidate content, and destination version (`store.py:1135-1192`, `1278-1317`).
- Candidate IDs include provenance and qualifications, and import recomputes artifact IDs/content hashes (`store.py:268-289`, `528-574`, `1774-1781`).
- Response-loss reconciliation checks operation identity plus version/content before confirming (`store.py:1331-1355`); it does not blind-retry.
- Goal parent-cycle checks and revision/input fences are present (`store.py:687-713`, `813-845`).

These strengths do not offset RUNTIME-1 through RUNTIME-4. Approval should wait for the sequential destination proof, enforced authority boundary, and import integrity fixes; then rerun the response-loss scenario with a subsequent authorized destination update and an overwritten-operation recovery case.

## Maintainability notes

The 1,968-line `Store` combines schema, public domain operations, serialization, export, and import validation. RUNTIME-4 is a concrete consequence of that duplicated manual relationship logic. Also, `artifact_dir` is created at `store.py:34-35` but artifact content is stored only in SQLite (`store.py:123-137`); either remove the unused directory or document and implement its intended invariant so backup/cleanup tooling does not infer a file-backed artifact store.