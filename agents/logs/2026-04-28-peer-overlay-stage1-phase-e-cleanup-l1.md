# Peer overlay Stage 1 Phase E cleanup verification

- Date: 2026-04-28
- Verification level: L1 Phase E targeted checks
- Worktree: `C:\Users\salee\Documents\dev\puripuly_heart\.worktrees\peer-overlay-stage1-architectural-redesign`
- Branch / HEAD: `peer-overlay-stage1-architectural-redesign` / `d4a8ce5`
- Scope: cleanup comments, durable burst design note, and grep/log classification only. No Stage 2 experiments were started.

## Implementation notes

- Added LOAD-BEARING comments near the peer presentation refresh burst cadence constants in `src/puripuly_heart/core/overlay/presenter.py`.
- Added LOAD-BEARING comments at reducer-owned nonce mutation / session-scope marker generation in `src/puripuly_heart/core/overlay/state.py`.
- Created `docs/superpowers/specs/2026-04-28-peer-burst-as-product-permanent.md` documenting the burst and `peer_presentation_refresh=<n>` nonce as product-permanent until Stage 2 HMD QA proves an alternative.
- Added a narrow `.gitignore` exception so the new durable `docs/superpowers/specs/...` note is visible to normal git status even though `docs/superpowers` remains ignored by default.
- Did not move/archive historical `agents/logs/` files. User choice recorded: **이동 보류 / defer moving old logs**.

## Vestigial search classification

Required tracked-file searches:

- PASS: `git grep -n "resubmit_current_frame\|ResubmitCurrentFrame\|SubmittedFrameRecord\|frame_resubmit" -- src native tests docs`
  - Result: no matches.
  - Classification: no tracked product-code submit-only resubmit implementation/reference found.
- PASS: `git grep -n "DEBUG_OVERLAY_TICK\|overlay_tick\|TICK" -- src native tests docs`
  - Result: no matches.
  - Classification: no tracked product-code TICK/debug forced redraw implementation/reference found.
- PASS: `git grep -n "peer lifecycle parity\|self-peer-lifecycle-parity\|retained-peer-lifecycle" -- src native tests docs agents/logs`
  - Result: no matches.
  - Classification: no tracked lifecycle-parity implementation target/reference found.

Extra untracked-inclusive classification after creating the design note:

- INFO: `git grep --untracked -n "resubmit_current_frame\|ResubmitCurrentFrame\|SubmittedFrameRecord\|frame_resubmit" -- src native tests docs`
  - Match only in `docs/superpowers/specs/2026-04-28-peer-burst-as-product-permanent.md` under **Rejected alternatives**.
- INFO: `git grep --untracked -n "DEBUG_OVERLAY_TICK\|overlay_tick\|TICK" -- src native tests docs`
  - Match only in the same design note under **Rejected alternatives**.
- INFO: `git grep --untracked -n "peer lifecycle parity\|self-peer-lifecycle-parity\|retained-peer-lifecycle" -- src native tests docs agents/logs`
  - Match only in the same design note under **Rejected alternatives**.

Conclusion: rejected resubmit/TICK/lifecycle-parity mechanisms are absent from product code. New docs mention them only as rejected alternatives, not implementation guidance.

## Detailed log cleanup audit

Reviewed the detailed logging path around:

- `bridge_snapshot_received`
- `state_snapshot_applied` / `state_snapshot_ignored`
- `snapshot_slot_correlation`
- `caption_blocks_built`
- `overlay_visible_update_applied` / `overlay_visible_update_rendered`
- `frame_rendered`
- `frame_submitted`
- `frame_timing`
- `cache_stats`
- `overlay_sink_emit_duration`

No log cleanup was made in Phase E. The reviewed lines carry distinct revision, slot/session-scope, render, submit, timing, or cache evidence. Removing or combining them was not clearly safe because the parser and HMD diagnosis rely on `frame_timing`/`frame_submitted` rows and burst/session-scope visibility.

## Commands

- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py tests/core/test_hub_overlay_streaming.py tests/ui/test_controller_branch_paths.py -q`
  - Result: exit status 0; all targeted tests passed.
- PASS: `.venv\Scripts\ruff.exe check src/puripuly_heart/core/overlay src/puripuly_heart/core/orchestrator/hub.py tests/core tests/ui/test_controller_branch_paths.py agents/scripts/analyze_frame_timing.py tests/agents/test_analyze_frame_timing.py`
  - Result: `All checks passed!`
- SKIPPED: `cargo test --manifest-path native/overlay/Cargo.toml --test runtime`
  - Reason: Phase E did not touch native runtime/bridge/renderer code or detailed logging implementation. Phase D already verified native runtime after native changes (`33 passed, 0 failed, 2 ignored`), and no native code changed in Phase E.
- PASS: `git diff --check`
  - Result: no whitespace errors.

## HMD QA

- SKIPPED: unavailable in this implementation environment; no HMD/manual GUI session is available here.
- No HMD pass is claimed.
- Phase E did not change product/native behavior or detailed logging semantics, but final Stage 1 still requires HMD QA before overall completion: normal GUI mode, burst enabled, peer translation flow, no peer N-1 lag regression, no source-only peer overlay regression, translated peer rows visible, and detailed logs retaining timing/refresh evidence.

## Follow-up requirements

- Final Stage 1 still needs the required Windows release overlay build because earlier phases modified Rust/native code.
- Final Stage 1 still needs HMD QA before claiming behavioral completion.
