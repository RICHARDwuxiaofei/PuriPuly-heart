# Peer overlay Stage 1 Phase C5 burst interface verification

- Date: 2026-04-28
- Verification level: L1
- Scope: Formalized peer presentation refresh target/nonce ownership in `OverlayPresentationState` while keeping burst cadence/task ownership in `OverlayPresenter`.

## Commands

- EXPECTED FAIL (TDD red): `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py::test_overlay_presentation_state_peer_refresh_methods_own_target_and_nonce -q`
  - Failed because `OverlayPresentationState.begin_peer_presentation_refresh` was not implemented yet.
- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py::test_overlay_presentation_state_peer_refresh_methods_own_target_and_nonce -q`
- PASS: `.venv\Scripts\python.exe -m pytest tests/ui/test_controller_branch_paths.py::test_overlay_start_syncs_bridge_after_preserved_presenter_cleans_refresh_marker -q`
- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py tests/ui/test_controller_branch_paths.py::test_overlay_start_syncs_bridge_after_preserved_presenter_cleans_refresh_marker -q`
- PASS: `.venv\Scripts\ruff.exe check src/puripuly_heart/core/overlay tests/core/test_overlay_presenter.py tests/ui/test_controller_branch_paths.py`
- PASS: `git diff --check`

## Review fix rerun

- EXPECTED FAIL (TDD red): `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py::test_presenter_peer_presentation_refresh_restart_after_visible_marker_resets_nonce_and_cleans_old_target -q`
  - Failed because restarting after `peer_presentation_refresh=1` did not republish a clean snapshot and inherited the previous nonce/target state.
- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py::test_presenter_peer_presentation_refresh_restart_after_visible_marker_resets_nonce_and_cleans_old_target -q`
- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py tests/ui/test_controller_branch_paths.py::test_overlay_start_syncs_bridge_after_preserved_presenter_cleans_refresh_marker -q`
- PASS: `.venv\Scripts\ruff.exe check src/puripuly_heart/core/overlay tests/core/test_overlay_presenter.py tests/ui/test_controller_branch_paths.py`
- PASS: `git diff --check`

## Notes

- Presenter still owns `_run_peer_presentation_refresh_burst`, `asyncio.create_task`, cancellation, sleeps, and the unchanged 2s / 100ms cadence.
- Reducer now owns peer refresh target identity and `peer_presentation_refresh=<n>` nonce mutation through `begin_peer_presentation_refresh`, `tick_peer_presentation_refresh`, and `end_peer_presentation_refresh`.
- Tick publishes still create fresh snapshot revisions containing `peer_presentation_refresh=1`, `peer_presentation_refresh=2`, then a clean snapshot on natural end/disable.
- Review fix: `begin_peer_presentation_refresh()` now resets any previous nonce/target and reports when a visible refresh marker requires a clean publish before the restarted burst. The next restarted tick begins again at `peer_presentation_refresh=1`.
- The UI bridge restart fixture now uses the normal peer transcript + translation path for the marker, preserving the Phase C4 decision that reserved `active_peer` rows do not start the refresh burst.

## HMD QA

- SKIPPED: Required after C5 by the plan, but unavailable in this environment because no HMD/manual GUI hardware QA access is available. No HMD pass is claimed.
