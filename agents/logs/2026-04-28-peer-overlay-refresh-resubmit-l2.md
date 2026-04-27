# Peer Overlay Refresh Resubmit Verification

Date: 2026-04-28
Worktree: `.worktrees/peer-overlay-refresh-resubmit-impl`
Verification level: L2 plus Windows overlay rebuild

## Commands

- `$env:PYTHONPATH='.'; uv run pytest tests/core/test_overlay_presenter.py tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py tests/ui/test_controller_branch_paths.py::test_overlay_start_preserves_clean_presenter_snapshot_and_refresh_resubmits -q` — PASS
- `uv run ruff check src/puripuly_heart/core/overlay/presenter.py src/puripuly_heart/core/overlay/bridge.py tests/core/test_overlay_presenter.py tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py tests/ui/test_controller_branch_paths.py` — PASS
- `cargo test --manifest-path "native/overlay/Cargo.toml" --lib` — PASS
- `cargo test --manifest-path "native/overlay/Cargo.toml" --test renderer` — PASS
- `cargo test --manifest-path "native/overlay/Cargo.toml" --test runtime` — PASS
- `cargo build --manifest-path "native/overlay/Cargo.toml" --release` — PASS
- `git diff --check` — PASS

## Notes

- Peer refresh burst now sends `resubmit_current_frame` messages instead of mutating snapshot metadata.
- `OverlayBridge` no longer emits the unconditional `[OverlayBridge] Snapshot updated` info log.
- Native runtime stores the last successfully submitted frame and handles guarded submit-only resubmits.
- Windows renderer now produces independent per-render textures so a later failed submit cannot alias the stored successful frame.
- `test-run` subagent could not execute Ruff, release build, or diff-check due tool policy; those commands were rerun directly in the implementation worktree and passed.
- HMD QA is still required before claiming user-visible HMD delay/flicker behavior is fixed.

## Skipped

- HMD manual QA not run in automated verification.
