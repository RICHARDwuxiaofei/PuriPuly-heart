# Peer overlay Stage 1 Phase A diagnostics verification

- Date: 2026-04-28
- Verification level: L1 + required Windows native overlay release build
- Branch: `peer-overlay-stage1-architectural-redesign`
- Scope: Phase A passive diagnostics infrastructure only

## Existing timing path

- `sample_frame_timing()` location:
  - Trait default: `native/overlay/src/openvr.rs:201`
  - `OpenVrOverlay` delegation: `native/overlay/src/openvr.rs:285`
  - Backend dispatch: `native/overlay/src/openvr.rs:399`
  - Windows OpenVR `GetFrameTiming` call: `native/overlay/src/openvr.rs:556`
- Called from: `native/overlay/src/runtime.rs:631`, after successful `submit_frame()` and after detailed-mode `frame_submitted` logging.
- Sampling cadence: unchanged; `native/overlay/src/runtime.rs:801` keeps the existing 1 second `SAMPLE_INTERVAL` throttle via `last_frame_timing_sampled_at`.
- Current sampled OpenVR fields in `FrameTimingSample`: `frame_index`, `num_frame_presents`, `num_mis_presented`, `num_dropped_frames`, `system_time_seconds`, `client_frame_interval_ms`, `present_call_cpu_ms`, `wait_for_present_cpu_ms`, `compositor_render_cpu_ms`, `total_render_gpu_ms`, `post_submit_gpu_ms`.
- Canonical detailed timing log now emitted from `native/overlay/src/runtime.rs:1342` as: `frame_timing revision=<revision> dropped_frames=<n> post_submit_gpu_ms=<float> total_render_gpu_ms=<float> submit_duration_us=<n-or-none>`.
- Existing `frame_submitted` log remains at `native/overlay/src/runtime.rs:1317` and includes `revision` plus `submit_duration_us` when detailed submit timing is available.

## Parser mapping notes

- Canonical `frame_timing` fields map directly to parser summary fields.
- The parser also accepts legacy `openvr_frame_timing` lines for older logs:
  - `num_dropped_frames` -> `dropped_frames`
  - `post_submit_gpu_ms` -> `post_submit_gpu_ms`
  - `total_render_gpu_ms` -> `total_render_gpu_ms`
  - `revision` and `submit_duration_us` are inferred from the immediately preceding parsed `frame_submitted` line when present.
- `frame_submitted` lines are parsed for revision grouping and submit duration stats, but do not increment `timing_rows`.
- Logs with no timing rows exit successfully and expose `timing_rows=0`.

## TDD evidence

- RED: `.venv\Scripts\python.exe -m pytest tests/agents/test_analyze_frame_timing.py -q` failed with all 6 tests blocked by missing `agents/scripts/analyze_frame_timing.py`.
- RED: `cargo test --manifest-path native/overlay/Cargo.toml --lib frame_timing_summary_reports_revision_gpu_and_submit_duration_fields` failed with unresolved `format_frame_timing_log`.
- GREEN: parser and Rust timing-log tests passed after implementation.
- Review fix RED: after adding `test_analyze_frame_timing_requires_gpu_fields_but_allows_explicit_none`, `.venv\Scripts\python.exe -m pytest tests/agents/test_analyze_frame_timing.py -q` failed because the truncated canonical line was incorrectly counted (`assert 2 == 1`).
- Review fix GREEN: missing canonical GPU fields now parse as malformed while explicit `none` remains valid.
- Review fix 2 RED: after adding `test_analyze_frame_timing_requires_submit_duration_field_but_allows_explicit_none`, `.venv\Scripts\python.exe -m pytest tests/agents/test_analyze_frame_timing.py -q` failed because missing/invalid canonical `submit_duration_us` rows were incorrectly counted (`assert 3 == 1`).
- Review fix 2 GREEN: missing or invalid canonical `submit_duration_us` now parses as malformed, while explicit `submit_duration_us=none` remains valid.

## Commands

- PASS: `.venv\Scripts\python.exe -m pytest tests/agents/test_analyze_frame_timing.py -q` (`6 passed`)
- PASS: `cargo test --manifest-path native/overlay/Cargo.toml --lib` (`57 passed`)
- PASS: `cargo test --manifest-path native/overlay/Cargo.toml --test runtime` (`32 passed`, `2 ignored`)
- FAIL (known long-path/FileTracker issue): `cargo build --manifest-path native/overlay/Cargo.toml --release`
  - Evidence: MSBuild/FileTracker `FTK1011` while creating `link-cvtres.write.1.tlog` under the long worktree `target\release\build\openvr_sys...\CMakeScratch...` path.
- PASS: `$env:CARGO_TARGET_DIR='C:\ph-overlay-target'; cargo build --manifest-path native/overlay/Cargo.toml --release`
- PASS: `.venv\Scripts\ruff.exe check agents/scripts/analyze_frame_timing.py tests/agents/test_analyze_frame_timing.py`
- PASS: `git diff --check`

### Review fix rerun

- PASS: `.venv\Scripts\python.exe -m pytest tests/agents/test_analyze_frame_timing.py -q` (`7 passed`)
- PASS: `.venv\Scripts\ruff.exe check agents/scripts/analyze_frame_timing.py tests/agents/test_analyze_frame_timing.py`
- PASS: `git diff --check`

### Review fix 2 rerun

- PASS: `.venv\Scripts\python.exe -m pytest tests/agents/test_analyze_frame_timing.py -q` (all parser tests passed)
- PASS: `.venv\Scripts\ruff.exe check agents/scripts/analyze_frame_timing.py tests/agents/test_analyze_frame_timing.py`
- PASS: `git diff --check`

## Skipped items and HMD QA

- HMD QA: not run; Phase A only adds passive detailed logging and offline parsing. No render cadence, submit cadence, burst cadence/default/nonce, dedup, `damage_band`, D3D11 `Flush`, stored-frame resubmit, or periodic redraw behavior was changed.
- Full Python suite: not run for Phase A; scoped verification used the new parser tests plus required native tests/build per plan. Baseline full-suite issues, if any, were intentionally not addressed.

## Key assumptions / mismatch resolutions

- Kept the existing timing sample cadence unchanged to preserve passive-only diagnostics.
- Replaced the sampled detailed timing payload with the concise canonical `frame_timing` line required by Phase A; the offline parser still accepts the previous `openvr_frame_timing` name for older logs.
- `revisions_seen` is emitted as a sorted list of numeric revisions observed across timing and submitted records.
- Submit duration statistics are grouped by revision to avoid double-counting when both `frame_submitted` and `frame_timing` lines contain the same revision.
