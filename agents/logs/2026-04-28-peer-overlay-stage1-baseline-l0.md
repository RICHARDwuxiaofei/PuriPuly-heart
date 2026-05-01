# Peer overlay Stage 1 Phase 0 baseline reconciliation

- Date: 2026-04-28
- Verification level: L0 baseline reconciliation with targeted Python/Rust checks and Windows release-build probe
- Worktree: `C:\Users\salee\Documents\dev\puripuly_heart\.worktrees\peer-overlay-stage1-architectural-redesign`
- Branch: `peer-overlay-stage1-architectural-redesign`
- HEAD: `d4a8ce5`
- Source base noted by controller: `peer-overlay-selflike-trigger-parity` at `d4a8ce5`
- Product-code changes: none
- Phase scope: Phase 0 only; Phase A not started

## Baseline context

- Initial `git status --short --branch`: `## peer-overlay-stage1-architectural-redesign` (clean before creating this note).
- `git rev-parse --short HEAD`: `d4a8ce5`.
- `git log -5 --oneline`:
  - `d4a8ce5 Merge branch 'overlay-typography-gap-30-impl' into peer-overlay-selflike-trigger-parity`
  - `9aca4be fix(overlay): rerender peer refresh snapshots`
  - `08a721d fix(overlay): widen caption texture surface`
  - `78c817c fix(overlay): widen caption source gap`
  - `e21f1f1 Merge branch 'peer-overlay-refresh-resubmit-impl' into peer-overlay-selflike-trigger-parity`
- The exact plan/spec paths from the handoff were not present in this worktree during lookup:
  - `docs/superpowers/plans/2026-04-28-peer-overlay-stage1-architectural-redesign.md`
  - `docs/superpowers/specs/2026-04-28-peer-overlay-stage1-architectural-redesign-design.md`
  - Used the controller handoff text as the Phase 0 authority for this pass.

## Static reconciliation findings

- Product submit-only resubmit is absent.
  - Command: `git grep -n "resubmit_current_frame\|ResubmitCurrentFrame\|SubmittedFrameRecord\|frame_resubmit" -- src native tests`
  - Outcome: PASS for absence; no matches.
- TICK/debug forced redraw is absent.
  - Command: `git grep -n "DEBUG_OVERLAY_TICK\|overlay_tick\|TICK" -- src native tests`
  - Outcome: PASS for absence; no matches.
- `peer_presentation_refresh_burst` is present and default-on.
  - Command: `git grep -n "peer_presentation_refresh_burst\|peer_presentation_refresh=" -- src tests`
  - Evidence includes `src/puripuly_heart/core/overlay/presenter.py:110` with `peer_presentation_refresh_burst: bool = True`, controller wiring that forces the presenter setting on, and tests asserting default-on/re-render behavior.
- Burst nonce revision behavior is preserved.
  - Evidence: `_publish_if_changed(force_peer_presentation_refresh=True)` increments `_peer_presentation_refresh_nonce`; `_peer_session_scope_with_presentation_refresh` emits `peer_presentation_refresh=<nonce>` markers; tests assert unchanged visible text still advances revisions and publishes nonce-scoped snapshots, then returns to clean peer snapshots.
- Content-aware `damage_band` behavior is preserved.
  - Evidence: `native/overlay/src/renderer/backend.rs` computes `damage_band` from old/new visible block bounds and layout cache-key changes, then uses `DamageBand::from_bounds(changed_bounds)`.
  - Tests preserve same-peer-slot and same-slot secondary-text movement coverage in `native/overlay/tests/renderer.rs`.
- Normal peer behavior remains translation-only semantics, while `active_peer` remains reserved compatibility/source-live fallback.
  - Evidence: in `OverlayPresenter._block_for_entry`, peer entries with `translated_text` emit `block_variant="finalized"` with translated primary text and optional original secondary text.
  - `active_peer` is still only used for live/source-only peer fallback states when no translated text is present; protocol validation still restricts `active_peer` to channel `peer`.
- No D3D11 Flush, submit-only resubmit, TICK forced redraw, render-task separation, burst simplification/removal, or damage-band removal was added in Phase 0.

## Command outcomes

| Command | Outcome | Notes |
| --- | --- | --- |
| `git status --short --branch` | PASS | Clean before note creation; branch `peer-overlay-stage1-architectural-redesign`. |
| `git rev-parse --short HEAD` | PASS | `d4a8ce5`. |
| `git log -5 --oneline` | PASS | Recent commits recorded above. |
| `git grep -n "resubmit_current_frame\|ResubmitCurrentFrame\|SubmittedFrameRecord\|frame_resubmit" -- src native tests` | PASS | No matches; confirms absence of product submit-only resubmit identifiers. |
| `git grep -n "DEBUG_OVERLAY_TICK\|overlay_tick\|TICK" -- src native tests` | PASS | No matches; confirms absence of TICK/debug forced redraw identifiers. |
| `git grep -n "peer_presentation_refresh_burst\|peer_presentation_refresh=" -- src tests` | PASS | Matches in presenter/controller/tests confirm burst path and default-on behavior. |
| `.\.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py -q` | PASS | Targeted overlay Python checks passed. |
| `.\.venv\Scripts\ruff.exe check src/puripuly_heart/core/overlay src/puripuly_heart/core/orchestrator/hub.py tests/core` | PASS | `All checks passed!` |
| `cargo test --manifest-path native/overlay/Cargo.toml --lib` | PASS | 56 tests passed. |
| `cargo test --manifest-path native/overlay/Cargo.toml --test runtime` | PASS | 32 passed, 0 failed, 2 ignored. |
| `cargo test --manifest-path native/overlay/Cargo.toml --test renderer` | PASS | 50 passed, 0 failed. |
| `cargo build --manifest-path native/overlay/Cargo.toml --release` | FAIL, environment/path concern | `openvr_sys` CMake compiler probe failed because MSBuild FileTracker could not create `link-cvtres.write.1.tlog`; the failing tlog path measured 260 characters, consistent with a Windows path-length environment issue in this long worktree path. |
| `$env:CARGO_TARGET_DIR="C:\Users\salee\AppData\Local\Temp\ph_overlay_stage1_target"; cargo build --manifest-path native/overlay/Cargo.toml --release` | PASS | Same release build succeeded with a shorter target directory. |
| `git diff --check` | PASS | No output before and after note creation for tracked diff. |

## Baseline full-suite issue carried forward

The controller reported a pre-existing full-suite baseline failure before Phase 0 implementation and approved proceeding while recording it here. Command: `.\.venv\Scripts\python.exe -m pytest`.

Unrelated failing tests reported by controller:

- `tests/core/test_managed_identity.py::test_ensure_managed_identity_bundle_generates_uuid7_and_keeps_secret_boundary`
- `tests/ui/test_dashboard_view_branches.py::test_dashboard_translation_visual_commit_forwards_metadata_and_runtime_log`
- `tests/ui/test_settings_view_branches.py::test_custom_vocabulary_switching_source_language_updates_editor_payload`

This Phase 0 pass did not rerun the full suite; targeted overlay checks passed.

## Skips and manual QA status

- HMD/manual QA: not run in this environment. Requires HMD/runtime hardware and manual observation. Phase 0 preserved the code paths and targeted automated tests covering restored-burst and peer translation/source fallback semantics.
- Broker Node verification: not applicable to this Phase 0 overlay baseline pass.
- Exact plan/spec file consultation: skipped because the requested exact files were absent in this worktree; the handoff included the relevant Phase 0 requirements and plan steps.

## Notes

- The release-build concern is environmental to the long worktree target path: the exact build command failed, while the same build succeeded with only `CARGO_TARGET_DIR` shortened.
- Final status note: this verification note is ignored by repository status rules (`git status --short --ignored -- agents/logs/2026-04-28-peer-overlay-stage1-baseline-l0.md` reports `!!`).
- No product reconciliation changes were necessary.
