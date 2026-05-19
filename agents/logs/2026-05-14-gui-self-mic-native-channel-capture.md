# Verification: GUI self mic native channel capture

- Date: 2026-05-15
- Verification level: L2

## Commands run

- `& ".\.venv\Scripts\python.exe" -m pytest tests/core/test_audio_source.py tests/ui/test_controller_branch_paths.py tests/core/test_streaming_resampler.py::test_resample_chunk_mixes_down_before_streaming_soxr_with_mq_quality tests/core/test_streaming_resampler.py::test_16khz_noop_path_mixdowns_without_building_soxr_stream tests/core/test_streaming_resampler.py::test_flush_uses_last_true_and_rejects_future_chunks`
- `& ".\.venv\Scripts\python.exe" -m pytest tests/core/test_stt_controller.py`
- `& ".\.venv\Scripts\python.exe" -m ruff check src/puripuly_heart/core/audio/source.py src/puripuly_heart/ui/controller.py tests/core/test_audio_source.py tests/ui/test_controller_branch_paths.py`
- `git status --short`
- `git diff -- src/puripuly_heart/app/headless_mic.py`
- `git diff -- src/puripuly_heart/core/audio/desktop_pipeline.py src/puripuly_heart/core/audio/desktop_source.py`
- `git diff -- src/puripuly_heart/config/settings.py src/puripuly_heart/ui/i18n.py src/puripuly_heart/data/i18n`

## Outcome

- PASS: targeted audio/source/GUI/resampler tests (`266 passed`)
- PASS: STT controller tests (`18 passed`)
- PASS: ruff check (`All checks passed!`)
- PASS: `git status --short` showed only planned source/test files before this evidence file was written
- PASS: headless microphone runner has no diff
- PASS: peer loopback files have no diff
- PASS: settings and i18n files have no diff

## Manual QA

- Matching hardware available: no
- Skipped because matching Windows/WASAPI microphone hardware for the motivating compatibility issue was unavailable in this environment.

## Notes

- Headless microphone runner was not modified.
- Peer loopback implementation was not modified.
- No settings schema or i18n changes were made.
- Support interpretation: `preferred_capture_channels=2 requested_channels=1` with a `_mono_retry` attempt means stereo open failed and mono compatibility was preserved; `metadata_status=query_failed` or `unavailable` means the 2ch path was not exercised for that attempt.
- Tooling note: the test-run subagent executed pytest successfully but was blocked by tool policy before running ruff; ruff was rerun directly in the worktree and passed.
