# Peer overlay Stage 1 final verification

- Date: 2026-04-28
- Verification level: L2 automated verification plus Windows release overlay build probe
- Worktree: `C:\Users\salee\Documents\dev\puripuly_heart\.worktrees\peer-overlay-stage1-architectural-redesign`
- Branch: `peer-overlay-stage1-architectural-redesign`
- HMD QA: SKIPPED in this environment; no HMD pass claimed. Final behavioral approval still requires normal GUI/HMD QA with restored burst enabled.

## Commands

| Outcome | Command | Notes |
| --- | --- | --- |
| FAIL | `.venv\Scripts\python.exe -m pytest tests/ -q` | Full suite failed with the same 3 baseline failures recorded before Stage 1 implementation: managed identity persisted shape extra keys, dashboard metadata extra `debug_prefix`, and settings helper locale mismatch. |
| PASS | `.venv\Scripts\ruff.exe check src/ tests/ agents/scripts/analyze_frame_timing.py` | Direct shell run was required because the test-run subagent policy blocked Ruff execution. Output: `All checks passed!` |
| PASS | `cargo test --manifest-path native/overlay/Cargo.toml --lib` | 63 tests passed. |
| PASS | `cargo test --manifest-path native/overlay/Cargo.toml --test runtime` | 33 passed, 0 failed, 2 ignored. |
| PASS | `cargo test --manifest-path native/overlay/Cargo.toml --test renderer` | 50 renderer integration tests passed. |
| FAIL | `cargo build --manifest-path native/overlay/Cargo.toml --release` | Failed with known Windows `FileTracker : error FTK1011` long-path issue under the long worktree target path. |
| PASS | `$env:CARGO_TARGET_DIR="C:\ph-overlay-target"; cargo build --manifest-path native/overlay/Cargo.toml --release` | Same release build succeeded with shortened target directory. |
| PASS | `git diff --check` | No whitespace errors. |

## Full pytest failure details

The final full Python test command failed with 3 failures that match the Phase 0 baseline issue carried forward with user approval:

- `tests/core/test_managed_identity.py::test_ensure_managed_identity_bundle_generates_uuid7_and_keeps_secret_boundary`
  - `persisted["managed_identity"]` includes extra keys: `active_managed_credential_ref`, `active_managed_expires_at`, `founder_letter_seen_credential_ref`.
- `tests/ui/test_dashboard_view_branches.py::test_dashboard_translation_visual_commit_forwards_metadata_and_runtime_log`
  - Translation metadata includes extra `debug_prefix: None`.
- `tests/ui/test_settings_view_branches.py::test_custom_vocabulary_switching_source_language_updates_editor_payload`
  - Helper text is localized Korean rather than the expected English string.

These failures were present before Stage 1 implementation and were not fixed in this work item.

## Skips / risks

- HMD QA was not run because the current environment lacks the required HMD/manual GUI setup. This is an explicit risk/waiver, not a pass.
- Exact release build under the long worktree path fails due Windows path/FileTracker constraints; the required Windows release overlay build succeeds when `CARGO_TARGET_DIR` is shortened.

## Notes

- Stage 1 automated overlay-focused Python/Rust checks from each phase passed in their phase logs.
- The full-suite Python failure keeps the overall final verification status at **DONE_WITH_CONCERNS**, not fully green.
