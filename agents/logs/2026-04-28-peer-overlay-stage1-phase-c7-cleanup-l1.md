# Peer overlay Stage 1 Phase C7 cleanup verification

- Date: 2026-04-28
- Verification level: L1
- Scope: Phase C7 reducer migration cleanup only

## Cleanup findings

- Removed `replace_snapshot_for_compat()` compatibility shim from `OverlayPresentationState` and the presenter-side `_replace_snapshot_for_compat()` mirror.
- Removed the presenter-owned `_snapshot` mirror; `OverlayPresenter.snapshot()` now reads the reducer-owned snapshot.
- Removed dead presenter migration helpers that were superseded by reducer ownership, including old event reduction helpers and live-pointer clearing helpers.
- Kept `_publish_if_changed()` because grep shows it is still the active presenter publish boundary for bridge I/O, expiration publishes, display/calibration updates, and refresh-burst ticks.
- Code-quality follow-up: reducer `visible_block_selection()` now returns retained-hidden entry keys so presenter-owned diagnostics can format and record `visible_window.retained_hidden` without the dead presenter `_retained_hidden_labels()` helper.
- Code-quality follow-up: publishability/selectability now has one source of truth in `OverlayPresentationState.entry_is_publishable()` / `entry_is_selectable()`. Presenter diagnostics and pruning delegate to those reducer methods instead of duplicating the logic.

## TDD checks for code-quality follow-up

- RED then GREEN: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py::test_presenter_visible_window_diagnostics_include_reducer_retained_hidden_entries tests/core/test_overlay_presenter.py::test_presenter_visible_selection_uses_reducer_selectability_source -q`
  - Initial result before production changes: FAIL for missing retained-hidden diagnostic label and zero reducer selectability calls.
  - Result after production changes: PASS.

## Grep checks

- PASS: `git grep -n "_clear_live_self_pointer\|_clear_live_peer_pointer\|_visible_block_entries\|_publish_if_changed" -- src/puripuly_heart/core/overlay/presenter.py tests`
  - Findings: `_clear_live_self_pointer`, `_clear_live_peer_pointer`, and `_visible_block_entries` had no remaining matches. `_publish_if_changed` remains active and was intentionally kept.
- PASS: `git grep -n "_replace_snapshot_for_compat\|replace_snapshot_for_compat" -- src tests`
  - Findings: no matches.
- PASS: `git grep -n "OverlayPresentationState" -- src/puripuly_heart/core/orchestrator/hub.py`
  - Findings: no matches; Hub direct reducer ownership was not reintroduced.
- PASS: content grep for `_retained_hidden_labels` under `src` and `tests`
  - Findings: no matches; the dead presenter helper is removed.
- PASS: content grep for `entry_is_publishable` / `entry_is_selectable` under `src/puripuly_heart/core/overlay`
  - Findings: definitions exist only in `state.py`; `presenter.py` has delegation call sites only.

## Verification commands

- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py tests/core/test_overlay_protocol.py tests/core/test_hub_overlay_streaming.py tests/ui/test_controller_branch_paths.py -q`
- PASS: `.venv\Scripts\ruff.exe check src/puripuly_heart/core/overlay src/puripuly_heart/core/orchestrator/hub.py tests/core tests/ui/test_controller_branch_paths.py`
- PASS: `git diff --check`

## HMD QA

- SKIPPED / RISK WAIVER REQUIRED: Required by the Phase C7 plan, but unavailable in this implementation environment because no Windows HMD/manual GUI QA hardware path is exposed. No HMD pass is claimed. Moving past C7 without HMD QA is an explicit behavioral regression risk that needs human/environment waiver or later manual HMD validation before claiming full Phase C confidence.

## Notes

- Stage 1 non-goals preserved: no `Flush`, burst/default/cadence changes, submit-only resubmit, TICK forced redraw, render task split, `damage_band` changes, lifecycle parity product change, or nonce normalization.
- C7 did not start Phase D/E work.
