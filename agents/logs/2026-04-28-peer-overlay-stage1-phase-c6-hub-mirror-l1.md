# Peer overlay Stage 1 Phase C6 hub mirror migration verification

- Date: 2026-04-28
- Verification level: L1
- Scope: Phase C6 only; migrate Hub active-self overlay mirror fields through presenter APIs.

## Commands

- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_hub_overlay_streaming.py tests/core/test_overlay_presenter.py tests/ui/test_controller_branch_paths.py -q`
  - Result: targeted C6 pytest suite passed.
- PASS: `.venv\Scripts\ruff.exe check src/puripuly_heart/core/orchestrator/hub.py src/puripuly_heart/core/overlay tests/core tests/ui/test_controller_branch_paths.py`
  - Result: `All checks passed!`
- PASS: `git diff --check`
  - Result: no whitespace errors.
- PASS: `git grep -n "_overlay_active_self_" -- src tests`
  - Result: `NO_MATCHES`.
- PASS: `git grep -n "OverlayPresentationState" -- src/puripuly_heart/core/orchestrator/hub.py`
  - Result: `NO_MATCHES`.
- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_hub_low_latency.py::TestResumeEndTimeout::test_resume_confirmed_without_stt_keeps_active_secondary_in_same_call tests/core/test_hub_low_latency.py::TestSpecCommitPaths::test_sync_self_active_overlay_records_overlay_emit_with_merge_id tests/core/test_hub_low_latency.py::TestSpecCommitPaths::test_sync_self_active_overlay_dedupes_only_within_same_logical_turn tests/core/test_hub_low_latency.py::TestSpecCommitPaths::test_sync_self_active_overlay_re_emits_same_preview_when_update_id_changes tests/core/test_hub_branch_coverage.py::test_clear_language_runtime_state_self_preserves_stt_task_and_clears_overlay_preview tests/core/test_hub_branch_coverage.py::test_handle_vad_event_forwards_resume_confirming_chunk_before_overlay_resync -q`
  - Result: adjacent tests touched while removing stale direct mirror-field references passed.

## Notes

- Added `ActiveSelfOverlayMetadata` and `OverlayPresenter.active_self_overlay_metadata()` delegating to the presenter-owned reducer.
- Removed Hub-owned active-self mirror fields and replaced remaining Hub reads with the narrow presenter metadata accessor via `overlay_sink.active_self_overlay_metadata()` when present.
- Hub does not import or own `OverlayPresentationState`; no Hub reducer ownership/import was found.
- HMD QA: required by the plan after C6, but skipped here because HMD hardware/manual GUI environment is unavailable in this implementation session. No HMD pass is claimed.
