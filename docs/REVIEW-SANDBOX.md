# Sandbox review checkpoints

## Native path-policy correction — 2026-09-24

Actual execution found a profile-level name-policy gap in the earlier assessment:
replaying the previous rules allowed writes to nested `.eNv`, `credentials.json`
and `key.pem`. Python-style non-capturing groups were not enforced as intended by
Seatbelt. Native grouping now denies those paths and root environment-file case
variants in actual macOS execution. The full 86-check suite passes, including
the new behavioral regression; no case-sensitive APFS volume was exercised.

This supersedes the earlier credential/controller-path disposition below.
It is not a host-I/O escape finding or a general sandbox-safety proof. The
same-user, runtime-directory and resource-isolation limits remain.
See [current verification](VERIFICATION.md#independent-review-repairs).

## Earlier bounded source review

The retained review below predates this correction; its line numbers and test
counts describe that earlier snapshot.

**Scope:** Re-review of the repaired `goal_native/sandbox.py` and `tests/test_sandbox.py` against the prior findings. CLI execution only; no UI/browser or unrelated audit.

**Evidence:** retained repair-lane proof reports 12 actual macOS sandbox tests and the full 45-test project suite passing with required Python 3.12. The retained probes cover host read/write, symlink/path escape, network, fork/detached child, arbitrary exec, credential environment, stage credential/controller/env paths including case, timeout/output, overlap/cancellation, limits, snapshot behavior, and strict `ResourceWarning`. No test or probe was rerun.

## Bounded decision

**Approved for the restricted Python CLI capability, with the limits below.** The prior HIGH findings are repaired sufficiently for the stated threat model: trusted fixed `/usr/bin/sandbox-exec`, only the controller's Python interpreter, root identity checks, stage admission bounds, SBPL path denies, single-flight execution, cancellation publication handling, hard argument/path/time/output limits, and exceptional search-FD cleanup. This is not approval as a universal hostile same-user or resource-isolated sandbox.

## Finding disposition

- **Enforcement binary — RESOLVED.** The enforcer is a fixed absolute path and is validated before use (`sandbox.py:59`, `127`, `144-151`); the PATH substitution test is explicit (`test_sandbox.py:22-45`).
- **Interpreter — RESOLVED.** The selected interpreter must resolve to `sys.executable` (`sandbox.py:128-140`); `/bin/sh` rejection is tested (`test_sandbox.py:35-38`).
- **Root lifetime — MITIGATED, with one residual race below.** Identity is checked at construction, stage admission, and immediately before `Popen` (`sandbox.py:117-123`, `171-186`, `428`, `443`); replacement-before-run fails closed (`test_sandbox.py:47-60`).
- **Credential/controller paths — RESOLVED for the staged root.** Case-insensitive SBPL deny regexes cover blocked components, suffixes, credentials, secrets, and `.env` (`sandbox.py:611-651`); actual read/write denial is exercised (`test_sandbox.py:253-325`).
- **Overlap/cancellation — RESOLVED for single-flight runs.** Admission of `_run_active`, publication cancellation, process-group kill, and cleanup are coordinated (`sandbox.py:430-510`); overlap rejection is tested (`test_sandbox.py:212-247`).
- **Bounds — RESOLVED for admission and declared I/O controls.** Constructor, stage, path, write, argument, timeout, and output limits are bounded (`sandbox.py:93-102`, `188-246`, `270-280`, `689-717`); boundary tests cover them (`test_sandbox.py:188-210`, `333-349`).
- **Exceptional search FD — RESOLVED.** Opened descriptors are retained only on the successful path and closed otherwise (`sandbox.py:875-904`); injected `fstat` failure checks descriptor balance (`test_sandbox.py:162-185`).

## Remaining material risks

1. **Same-user root pathname TOCTOU (bounded residual):** `_assert_root_stable()` is a check before `Popen(cwd=self.root)` rather than an atomic descriptor-backed `cwd` operation (`sandbox.py:443-464`). A separate trusted same-user actor that replaces the path after line 443 could still cause execution to fail or use a replacement directory. The CLI approval therefore requires the stage owner to keep the root path immutable for the duration of a run. If hostile same-user rename resistance is required, this remains a release blocker.
2. **Stage limits are admission limits, not a disk quota:** `_validate_stage_for_run()` caps the initial stage at 64 MiB/4096 entries and rejects initial symlinks/non-regular files, while SBPL still permits `file-write*` below the root (`sandbox.py:188-246`, `584`). A script can grow the stage until the wall-time or host-disk boundary. Do not claim memory, CPU, or disk isolation.
3. **Search is an opened-file manifest, not a content-immutable snapshot:** concurrent content mutation after opening is not content-hashed or reported (`sandbox.py:335-416`, `776-910`). Results remain path/descriptor-bounded and the documented snapshot label is truthful.
4. **Runtime-directory trust remains an operational invariant:** the profile allows reads from the trusted interpreter/runtime directories (`sandbox.py:575-607`, `653-668`). Controller databases and credentials must remain outside those directories; the stage-root deny rules do not make arbitrary runtime directories a general credential deny zone.

## Acceptance

Release the CLI boundary with the bounded assumptions above recorded: macOS enforcement is required; Python is the controller's trusted interpreter; stage roots are immutable, owned, and kept separate from controller/runtime credentials; one run is active per `Sandbox`; and no universal resource-isolation claim is made. Classification: **INCREMENTAL source review; no breakthrough claim**.
