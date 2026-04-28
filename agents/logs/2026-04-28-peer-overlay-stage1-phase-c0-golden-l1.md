# Peer overlay Stage 1 Phase C0 golden characterization verification

- Date: 2026-04-28
- Verification level: L1 scoped characterization
- Scope: Phase C0 only; reducer ownership decision plus golden tests before reducer migration

## Reducer ownership decision

- Decision: `OverlayPresentationState` is owned by `OverlayPresenter`.
- `OverlayPresenter` remains the async/IO shell: bridge publishing, sleeps, cancellation, shutdown, and burst cadence.
- `OverlayPresentationState` owns deterministic presentation state: entries, visibility, snapshot generation, revision-worthy changes, and peer refresh nonce mutation.
- `OrchestratorHub` does not own or directly mutate the reducer.
- Hub mirror fields will be removed or replaced through narrow presenter APIs in C6.

## Golden locks added/verified

- Burst nonce/revision sequence is locked in `tests/core/test_overlay_presenter.py`: peer translation snapshot revision N, first refresh tick N+1 with `peer_presentation_refresh=1`, second refresh tick N+2 with `peer_presentation_refresh=2`, and natural burst end clean snapshot without the marker.
- Bridge restart/attach during a refresh marker is locked in `tests/core/test_overlay_presenter.py`: a restarted bridge initialized from the presenter snapshot observes the current marker snapshot and later receives the clean burst-end snapshot.
- No-peer-source-primary behavior is verified by existing/strengthened presenter tests: peer source/final transcript without translation keeps peer primary text empty while preserving source as secondary.
- Hub active-self mirror metadata is characterized in `tests/core/test_hub_overlay_streaming.py`: current text, secondary text, occupant key, utterance id, update id, origin wall-clock, session scope, source hash/len, and logical turn key.

## Commands

- PASS: `.venv\Scripts\python.exe -m pytest tests/core/test_overlay_presenter.py tests/core/test_hub_overlay_streaming.py -q`
  - Targeted pytest suite passed.
- PASS: `.venv\Scripts\ruff.exe check tests/core/test_overlay_presenter.py tests/core/test_hub_overlay_streaming.py`
  - Output: `All checks passed!`
- PASS: `git diff --check`

## Skipped items and HMD QA

- HMD QA: not run; C0 changed characterization tests and verification documentation only. No production overlay, native, burst cadence/default/nonce, renderer, `damage_band`, or hub runtime behavior was changed.
- Broader Python/Rust/build checks: not run; Phase C0 required the scoped presenter/hub pytest suite, ruff on touched test files, and `git diff --check`.

## Key assumptions / mismatch resolutions

- C0 is characterization only. Production behavior/code was not intentionally changed.
- Existing Phase B tests already covered portions of peer translation-only semantics; C0 strengthened/referenced them rather than duplicating every assertion.
- The target worktree already contained uncommitted Phase A/B changes. C0 only added/strengthened tests in `tests/core/test_overlay_presenter.py`, added a hub mirror metadata characterization in `tests/core/test_hub_overlay_streaming.py`, and created this verification note.
- Stage 1 non-goals remain deferred: no `Flush`, no burst/default/cadence/nonce changes, no submit-only resubmit, no TICK forced redraw, no render task split, no `damage_band` removal/bypass, and no lifecycle parity product change.
