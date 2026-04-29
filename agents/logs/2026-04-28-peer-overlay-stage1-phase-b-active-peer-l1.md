# Peer overlay Stage 1 Phase B active_peer audit

- Date: 2026-04-28
- Verification level: L1, with native Rust tests and Windows release overlay build because Phase B touched native comments.
- Outcome: Outcome 1 selected. `active_peer` / `PeerActiveUpdate` remains reserved compatibility/fallback; no Python+Rust protocol removal was attempted.

## Commands

- PASS: `git grep -n "ActivePeer\|active_peer\|PeerActiveUpdate\|_apply_peer_active_update\|_live_peer_entry_is_drawable\|_peer_active_occupant_key" -- src native tests`
- RED/PASS (expected failing characterization before production change): `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py::test_presenter_reserved_peer_active_update_does_not_start_refresh_burst_without_translation -q`
  - Failed because direct reserved `PeerActiveUpdate` source-only rows still started `_peer_presentation_refresh_burst_task`.
- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py tests/core/test_overlay_protocol.py -q`
- PASS: `.venv\Scripts\ruff.exe check src/puripuly_heart/core/overlay tests/core/test_overlay_presenter.py tests/core/test_overlay_protocol.py`
- PASS: `cargo test --manifest-path native/overlay/Cargo.toml --lib`
- PASS: `cargo test --manifest-path native/overlay/Cargo.toml --test renderer`
- FAIL (known Windows long-path/FileTracker issue): `cargo build --manifest-path native/overlay/Cargo.toml --release`
  - Failure included `FileTracker : error FTK1011: could not create the new file tracking log file` under the worktree `target\release\build\openvr_sys...\CMakeScratch\TryCompile...` path.
- PASS (short target-dir fallback): `$env:CARGO_TARGET_DIR = "$env:TEMP\ph-overlay-target"; cargo build --manifest-path native/overlay/Cargo.toml --release; $exit = $LASTEXITCODE; Remove-Item Env:CARGO_TARGET_DIR; exit $exit`
- PASS: `git diff --check`

## HMD QA

- Not run in this implementation environment: no HMD/manual GUI session is available to the agent.
- Reason recorded because Phase B changed presenter refresh gating for source-only reserved peer rows. Automated tests lock the intended behavior, but manual HMD QA can still be run later if desired before release.

## Behavior locks added/verified

- Peer final/source transcript normal flow does not place source text in peer primary text and never uses an `active_peer` row.
- Peer translation arrival creates finalized peer primary text.
- Peer original/source text remains secondary-only when configured visible.
- Peer partial/interim direct `PeerActiveUpdate` remains reserved compatibility/fallback, not normal product flow.
- Peer presentation refresh burst starts only after a translated finalized peer primary row is visible; source-only transcript/final rows and reserved `active_peer` rows do not start the burst.
- Self active/source rows remain allowed before translation.
- `SELF_TRANSLATION_MIN_VISIBLE_SECONDS` remains self-only; peer translation entries do not get a translation min-visible deadline.
- Retained-hidden self entry late translation behavior remains characterized.
- `_retired_preview_self_seqs` remains self-only and does not block a peer fallback turn with the same UUID.

## active_peer audit

| Symbol | Location | Normal product path? | Action |
|---|---|---:|---|
| `ActivePeer` | `native/overlay/src/renderer/layout.rs:911` | No | Keep renderer layout compatibility for reserved/native snapshots. |
| `active_peer` | `native/overlay/src/renderer/types.rs:62` | No | Added reserved-compatibility comment. |
| `ActivePeer` | `native/overlay/src/renderer/types.rs:63` | No | Keep renderer enum variant for protocol compatibility. |
| `ActivePeer` | `native/overlay/src/runtime.rs:843` | No | Keep native visibility/debug handling for reserved snapshots. |
| `ActivePeer` | `native/overlay/src/runtime.rs:867` | No | Keep native drawable variant handling. |
| `ActivePeer` / `active_peer` | `native/overlay/src/runtime.rs:891` | No | Keep native string conversion for protocol/debug compatibility. |
| `ActivePeer` / `active_peer` | `native/overlay/src/runtime.rs:899` | No | Keep caption variant debug conversion. |
| `active_peer` | `native/overlay/src/runtime.rs:1237` | No | Keep debug-watermark active-peer scan. |
| `ActivePeer` | `native/overlay/src/runtime.rs:1239` | No | Keep debug-watermark reserved variant detection. |
| `active_peer` | `native/overlay/src/runtime.rs:1243` | No | Keep debug label tail extraction. |
| `active_peer` | `native/overlay/src/runtime.rs:1247` | No | Keep debug label hash input. |
| `active_peer` | `native/overlay/src/runtime.rs:1269` | No | Keep debug label output field. |
| `ActivePeer` | `native/overlay/src/runtime.rs:1666` | No | Keep snapshot-to-caption conversion. |
| `ActivePeer` | `native/overlay/src/runtime.rs:1667` | No | Keep conversion target. |
| `ActivePeer` | `native/overlay/src/runtime.rs:1835` | No | Keep runtime unit-test fixture. |
| `active_peer` | `native/overlay/src/runtime.rs:1939` | No | Keep runtime compatibility test. |
| `active_peer` | `native/overlay/src/runtime.rs:1940` | No | Keep runtime compatibility fixture variable. |
| `ActivePeer` | `native/overlay/src/runtime.rs:1941` | No | Keep runtime compatibility fixture variant. |
| `active_peer` | `native/overlay/src/runtime.rs:1942` | No | Keep runtime test fixture. |
| `active_peer` | `native/overlay/src/runtime.rs:1943` | No | Keep runtime test fixture. |
| `active_peer` | `native/overlay/src/runtime.rs:1947` | No | Keep runtime test fixture. |
| `ActivePeer` | `native/overlay/src/runtime.rs:1953` | No | Keep runtime conversion assertion. |
| `active_peer` | `native/overlay/src/runtime.rs:1978` | No | Keep runtime detection test. |
| `active_peer` | `native/overlay/src/runtime.rs:1979` | No | Keep runtime test fixture variable. |
| `ActivePeer` | `native/overlay/src/runtime.rs:1980` | No | Keep runtime test fixture variant. |
| `active_peer` | `native/overlay/src/runtime.rs:1981` | No | Keep runtime test fixture. |
| `active_peer` | `native/overlay/src/runtime.rs:1982` | No | Keep runtime test fixture. |
| `active_peer` | `native/overlay/src/runtime.rs:1986` | No | Keep runtime test fixture. |
| `active_peer` | `native/overlay/src/runtime.rs:2045` | No | Keep runtime first-render reserved-path test. |
| `ActivePeer` | `native/overlay/src/runtime.rs:2050` | No | Keep runtime first-render reserved fixture. |
| `ActivePeer` | `native/overlay/src/runtime.rs:2053` | No | Keep runtime first-render reserved fixture. |
| `active_peer` | `native/overlay/src/runtime.rs:2079` | No | Keep debug-watermark test. |
| `ActivePeer` | `native/overlay/src/runtime.rs:2083` | No | Keep debug-watermark fixture. |
| `ActivePeer` | `native/overlay/src/runtime.rs:2107` | No | Keep debug-watermark fixture. |
| `active_peer` | `native/overlay/src/state.rs:46` | No | Added reserved-compatibility protocol comment. |
| `ActivePeer` | `native/overlay/src/state.rs:47` | No | Keep serde-compatible protocol variant. |
| `active_peer` | `native/overlay/tests/renderer.rs:324` | No | Keep native renderer reserved-layout test. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:335` | No | Keep native renderer fixture. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:372` | No | Keep test conversion mapping. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:396` | No | Keep native renderer assertion. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:461` | No | Keep test conversion mapping. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:530` | No | Keep renderer fixture. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:613` | No | Keep renderer fixture. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:622` | No | Keep renderer fixture. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:665` | No | Keep renderer fixture. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:688` | No | Keep renderer fixture. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:783` | No | Keep renderer fixture. |
| `ActivePeer` | `native/overlay/tests/renderer.rs:1028` | No | Keep renderer fixture. |
| `active_peer` | `native/overlay/tests/state.rs:90` | No | Keep native serde compatibility test. |
| `active_peer` | `native/overlay/tests/state.rs:99` | No | Keep native serde fixture. |
| `ActivePeer` | `native/overlay/tests/state.rs:109` | No | Keep native serde assertion. |
| `active_peer` | `native/overlay/tests/state.rs:290` | No | Keep state slot-promotion compatibility test. |
| `ActivePeer` | `native/overlay/tests/state.rs:301` | No | Keep state test fixture. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/presenter.py:24` | No | Keep import for reserved fallback event. |
| `active_peer` | `src/puripuly_heart/core/overlay/presenter.py:302` | No | Keep diagnostics/rendered pair-state classification. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/presenter.py:523` | No | Keep reserved event dispatch with comment. |
| `_apply_peer_active_update` | `src/puripuly_heart/core/overlay/presenter.py:527` | No | Keep reserved fallback reducer path. |
| `_apply_peer_active_update` / `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/presenter.py:699` | No | Keep reserved fallback implementation. |
| `_live_peer_entry_is_drawable` | `src/puripuly_heart/core/overlay/presenter.py:1062` | No | Keep reserved live-peer drawable helper. |
| `active_peer` | `src/puripuly_heart/core/overlay/presenter.py:1227` | No | Keep reserved protected live-peer key variable. |
| `_live_peer_entry_is_drawable` | `src/puripuly_heart/core/overlay/presenter.py:1229` | No | Keep reserved live-peer selection guard. |
| `active_peer` | `src/puripuly_heart/core/overlay/presenter.py:1232` | No | Keep reserved protected-key list. |
| `active_peer` | `src/puripuly_heart/core/overlay/presenter.py:1478` | No | Keep reserved compatibility block emission only for direct peer active updates. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/presenter.py:1921` | No | Keep refresh event key recognition, now gated by translated finalized snapshot before burst starts. |
| `active_peer` | `src/puripuly_heart/core/overlay/presenter.py:1937` | No | Added comment: reserved rows must not start refresh burst. |
| `active_peer` | `src/puripuly_heart/core/overlay/protocol.py:7` | No | Added reserved-compatibility protocol comment. |
| `active_peer` | `src/puripuly_heart/core/overlay/protocol.py:9` | No | Keep protocol literal for compatibility. |
| `active_peer` | `src/puripuly_heart/core/overlay/protocol.py:93` | No | Keep deserialization validation. |
| `active_peer` | `src/puripuly_heart/core/overlay/protocol.py:97` | No | Keep channel validation. |
| `active_peer` | `src/puripuly_heart/core/overlay/protocol.py:98` | No | Keep validation error text. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/sink.py:79` | No | Keep reserved event class with docstring. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/sink.py:89` | No | Keep constructor validation. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/sink.py:91` | No | Keep constructor validation. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/sink.py:93` | No | Keep constructor validation. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/sink.py:139` | No | Keep union compatibility. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/sink.py:323` | No | Keep adapter return annotation for reserved fallback. |
| `PeerActiveUpdate` | `src/puripuly_heart/core/overlay/sink.py:324` | No | Keep adapter factory. |
| `active_peer` | `tests/core/test_hub_overlay_streaming.py:1508` | No | Keep chatbox fallback test; not peer overlay normal flow. |
| `active_peer` | `tests/core/test_overlay_presenter.py:219` | No | Reserved compatibility characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:262` | No | Reserved compatibility characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:373` | No | Normal-flow behavior lock asserts no active peer row. |
| `active_peer` | `tests/core/test_overlay_presenter.py:392` | No | Normal-flow behavior lock asserts no active peer row after translation. |
| `active_peer` | `tests/core/test_overlay_presenter.py:635` | No | Self-only retired-preview test uses peer fallback to prove self-only registry. |
| `active_peer` | `tests/core/test_overlay_presenter.py:785` | No | Reserved compatibility characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:984` | No | Reserved fallback no-burst characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:1449` | No | Reserved active-turn replacement characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:1491` | No | Reserved live-peer replacement characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:2791` | No | Reserved channel role mapping characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:3740` | No | Reserved appearance sequence characterization. |
| `active_peer` | `tests/core/test_overlay_presenter.py:4284` | No | Detailed logging characterization. |
| `PeerActiveUpdate` | `tests/core/test_overlay_protocol.py:14` | No | Keep protocol validation tests. |
| `active_peer` | `tests/core/test_overlay_protocol.py:138` | No | Keep snapshot round-trip compatibility test. |
| `active_peer` | `tests/core/test_overlay_protocol.py:144` | No | Keep protocol fixture. |
| `active_peer` | `tests/core/test_overlay_protocol.py:156` | No | Keep invalid channel test. |
| `active_peer` | `tests/core/test_overlay_protocol.py:157` | No | Keep invalid channel assertion. |
| `active_peer` | `tests/core/test_overlay_protocol.py:164` | No | Keep invalid channel fixture. |
| `PeerActiveUpdate` | `tests/core/test_overlay_protocol.py:193` | No | Keep event validation assertion. |
| `PeerActiveUpdate` | `tests/core/test_overlay_protocol.py:194` | No | Keep event validation fixture. |
| `PeerActiveUpdate` | `tests/core/test_overlay_protocol.py:204` | No | Keep event validation assertion. |
| `PeerActiveUpdate` | `tests/core/test_overlay_protocol.py:205` | No | Keep event validation fixture. |

## Assumptions and mismatch resolutions

- Interpreted Phase B's post-`c846eee` product lock as: peer source/original may be present only as configured secondary text, while peer primary overlay text appears with translation arrival.
- Kept direct `PeerActiveUpdate` / `active_peer` behavior available for compatibility/fallback, but changed refresh-burst eligibility so source-only reserved rows do not start peer presentation refresh. The burst still starts for translated finalized peer rows.
- Did not remove `_apply_peer_active_update`, `_live_peer_entry_is_drawable`, native `ActivePeer`, protocol literals, or native tests because they are compatibility/protocol paths and no approved Python+Rust protocol migration exists.
- Did not start Phase C/D/E work.
