# Peer overlay Stage 1 Phase D cache eviction verification

- Date: 2026-04-28
- Verification level: L1 Phase D automated checks plus required Windows release overlay build
- Worktree: `C:\Users\salee\Documents\dev\puripuly_heart\.worktrees\peer-overlay-stage1-architectural-redesign`
- Scope: renderer cache caps/eviction only; Phase E not started.

## Cache inventory

Before Phase D, the renderer caches were unbounded `HashMap` instances or wrappers around one:

| Cache | Key type | Value type | Previous growth behavior | Phase D cap / behavior |
|---|---|---|---|---|
| `text_format_cache` | `(TextScriptBucket, u32 font_size_key)` | `IDWriteTextFormat` | Inserted on text-format miss in `create_text_format`; grew for every distinct script/font-size bucket with no eviction. | 32 entries, internal LRU. |
| `LayoutCache` | `LayoutCacheKey` | `CachedBlockLayoutTemplate` | Inserted by DirectWrite cached layout resolution for every distinct visible text/layout-affecting key; no eviction. | 512 entries, internal LRU. |
| `line_cache` | `LineCacheKey` | `CachedLineVisual` | Inserted for every distinct rendered line visual key; no eviction. | 2048 entries, internal LRU. |
| `block_cache` | `BlockCacheKey` | `CachedBlockVisual` | Inserted for every cacheable finalized block visual key; no eviction. | 1024 entries, internal LRU. |

LRU choice: internal `BoundedLruCache` using `HashMap` plus a recency deque. Cache semantics are simple, caps are small, and this avoids adding a dependency. Cache hits call `get()` and update recency.

## Implementation notes

- Added cache cap constants near cache definitions: `TEXT_FORMAT_CACHE_CAP = 32`, `LAYOUT_CACHE_CAP = 512`, `LINE_CACHE_CAP = 2048`, `BLOCK_CACHE_CAP = 1024`.
- Replaced renderer cache maps with bounded LRU containers while preserving text-affecting cache key types.
- Preserved `damage_band` computation and render/submit flow; no D3D11 `Flush`, burst cadence/default change, submit-only resubmit, TICK forced redraw, render task split, damage-band bypass, or nonce normalization was added.
- Added detailed-mode `cache_stats text_format_size=<n> layout_size=<n> line_size=<n> block_size=<n> line_hits=<n> line_misses=<n> block_hits=<n> block_misses=<n>` logging after detailed `frame_submitted` logging. Existing burst/timing evidence rows remain present.
- Added tests for oldest-entry eviction, hit recency, stress size bound, cache diagnostics formatting, renderer diagnostic size caps, and same-target peer refresh nonce render/submit behavior.

## Commands

- RED/PASS expected failure: `cargo test --manifest-path native/overlay/Cargo.toml --lib`
  - Initial TDD red check failed from missing `BoundedLruCache`, cap constants, `LayoutCache::len`, cache-size diagnostics fields, and `format_cache_stats_log`.
- PASS: `cargo test --manifest-path native/overlay/Cargo.toml --lib`
  - Result: 63 passed, 0 failed.
- PASS: `cargo test --manifest-path native/overlay/Cargo.toml --test renderer`
  - Result: 50 passed, 0 failed.
- PASS: `cargo test --manifest-path native/overlay/Cargo.toml --test runtime`
  - Result: 33 passed, 0 failed, 2 ignored (pre-existing ignored child-process timing race tests covered by unit tests).
- FAIL (known long-path/FileTracker): `cargo build --manifest-path native/overlay/Cargo.toml --release`
  - Failed in `openvr_sys` CMake compiler check with `FileTracker : error FTK1011` under the long worktree target path.
- PASS (fallback): `$env:CARGO_TARGET_DIR = Join-Path $env:TEMP 'ph-overlay-stage1-d-target'; cargo build --manifest-path native/overlay/Cargo.toml --release`
  - Result: release build finished successfully with shortened `CARGO_TARGET_DIR`.
- PASS: `git diff --check`
  - Result: no whitespace errors.

## HMD QA

- SKIPPED: required after Phase D by the plan, but unavailable in this implementation environment because no HMD/manual GUI QA session is available here.
- No HMD pass is claimed. Required follow-up: run normal GUI mode with restored burst enabled and verify no peer N-1 lag regression, no source-only peer overlay regression, translated peer rows still appear, and detailed logs retain timing/refresh/cache evidence.

## Notes

- Exact release build failure matched the known Windows long worktree path/FileTracker FTK1011 issue; shortened target dir fallback passed.
- Runtime peer refresh nonce test confirmed revision 2 with the same peer target and `session_scope=peer_presentation_refresh=2` still rendered/submitted (`submit:text`) and emitted detailed visible-update/cache diagnostics instead of being silently skipped.
