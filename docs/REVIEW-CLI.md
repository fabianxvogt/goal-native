# CLI implementation review

## Final bounded decision

**Approved for the seven-repair, pre-subscription CLI scope, with documented limitations.** No concrete release-blocking residual remains against the seven findings in the prior review of `goal_native/cli.py` and `tests/test_cli.py`. This is not approval of later subscription/auth additions, live-provider behavior, sandbox completeness, model quality, performance, or the paused browser/UI release gate.

The source/test line references in the repair findings below refer to the settled repair snapshot only. They are not a review of subsequent `cli.py` additions.

Classification remains **`INCREMENTAL / EMPIRICAL`** for the exercised CLI/controller lifecycle and transport protocol.

## Explicitly excluded follow-on

The parent subsequently added `login`, `logout`, `auth-status`, and `models` commands, a pi Node auth helper, and default `--provider openai-codex`. Those subscription/auth and provider-selection additions are **not reviewed here**. Their integration, credential lifecycle, error handling, persistence, and user-visible behavior require the planned combined subscription review after Node integration settles.

## Prior findings — repair verification

1. **Staged-source credential filtering — resolved.** `_secret_component()` now excludes dotenv variants and the documented credential-like names/suffixes (`goal_native/cli.py:109-139`). The cancellation staging fixture covers `.env`, `.env.local`, `.env.production`, `OPENAI_API_KEY`, `SERVICE_TOKEN`, `secret.txt`, and symlinks (`tests/test_cli.py:315-366`). The README now explicitly says exclusions are name-based, not a secret scanner (`README.md:43-48`).

2. **Export temporary-file exposure — resolved.** Export uses `tempfile.mkstemp()` in the destination directory, writes and fsyncs through the private descriptor, then atomically replaces the destination (`goal_native/cli.py:557-581`). Retained smoke/test evidence confirms mode `0600` and no predictable temporary file remains (`tests/test_cli.py:388-396`).

3. **Non-JSON parser failures — resolved.** The custom parser converts argparse failures to `_CLIArgumentError`, and `main()` emits the JSON `ArgumentError` envelope with status `2` (`goal_native/cli.py:766-772,901-909`). Invalid command, missing argument, unknown option, and non-finite timeout cases are covered (`tests/test_cli.py:372-386`).

4. **Non-finite `--max-time` — resolved.** `_positive_float()` requires a positive finite value (`goal_native/cli.py:59-66`).

5. **Setup-time Ctrl-C ambiguity — resolved.** Setup interruption returns `run_started: false`, does not fence an unstarted goal, and clearly reports that no run started; run-time interruption remains fenced (`goal_native/cli.py:358-434`, `tests/test_cli.py:398-431`).

6. **Opened-file staging size race — resolved.** The copier rechecks the opened descriptor, bounds reads against the remaining allowance, detects growth, removes partial output, and records copied bytes (`goal_native/cli.py:182-240`). The retained mutation test covers growing and shrinking files (`tests/test_cli.py:433-464`).

7. **Unsafe/unbounded import input — resolved.** File imports require a regular non-symlink file opened with `O_NOFOLLOW`; file and stdin reads are bounded before JSON parsing (`goal_native/cli.py:583-630`). Both paths and the symlink rejection are covered (`tests/test_cli.py:466-495`).

## Residual boundaries — not release blockers for this scope

- Exclusion is deliberately **name-based**, not a secret scanner. Users must review selected files before provider submission; this is documented and is not a claim that arbitrary secret content can be detected.
- Staging admission limits are not runtime disk/memory quotas, and retained per-run stages can consume host disk. The controller, interpreter installation, and host user remain trusted (`README.md:64-68`).
- The exercised target is macOS with staged Python only. No live provider or production effect is claimed; the browser/UI gate remains paused until the CLI live-provider gate is satisfied (`README.md:50-60`).
- Exports contain task content and traces and must be reviewed before sharing (`README.md:83-85`).

## Retained evidence

The repair packet reported **8 CLI tests passed**, **50 full-project tests passed**, and actual CLI create/export/parser smoke coverage with export mode `0600`. Parent verification then passed all **50 tests with `-W error::ResourceWarning` enabled**; no ResourceWarning was emitted. This corrects the reviewer's misinterpretation of the warning-policy flag as an observed warning. The real Python → Node → cloned pi fixture remains protocol/usage evidence only (`64` input, `32` output, `96` total, `16` cached, `8` reasoning), not live-provider evidence. The reviewer did not rerun commands or tests.
