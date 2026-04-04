# Overlay Two-Slot Static Caption Design

## Summary

The overlay should stop behaving like a scrolling strip stack and instead behave like two fixed caption slots. The goal is to remove layout motion that hurts readability in the headset, especially enter, exit, and reflow movement during active-self promotion, finalized turnover, and TTL expiry.

This design keeps the existing overlay event names and logical turn model, but changes the visual model and extends the internal presenter-to-native snapshot payload:

- The overlay has exactly two physical slots.
- Slots do not move, compact, slide, or scale.
- Text is replaced in place.
- A left accent bar is the only update signal.
- Slot continuity is driven by explicit occupant metadata rather than text matching heuristics.

This design also assumes the peer subsystem rewrite preserves the current hub-to-overlay event contract for peer turns, even if peer runtime start, stop, and recovery timing changes. Peer runtime policy changes and peer-provider swaps are not themselves overlay clear signals.

## Goals

- Remove perceived flicker caused by strip removal, re-entry, and vertical reflow.
- Keep caption reading stable by preventing slot motion.
- Make `active_self` and finalized self text visually continuous.
- Preserve the existing logical event contract and turn lifecycle semantics where possible.
- Remove heuristic-only active-self promotion from the final design.

## Non-Goals

- No broad damage-band or ghosting investigation beyond the minimum renderer changes needed to clear the new accent chrome correctly.
- No new user-facing settings for animation style or slot count.
- No redesign of translation timing or utterance close semantics.
- No increase beyond two visible slots.

## Existing Constraints

- The presenter currently allows two finalized blocks plus a separate `active_self` block, which can produce three visible rows.
- The native overlay scene currently uses entering, exiting, and reflowing strips with sampled animation progress.
- The overlay layout policy already targets two visible blocks, but the scene model still allows motion and transient exit strips.
- Peer visibility today is publishability-driven: peer finalized transcripts can stay hidden until translation text arrives.
- The presenter currently trims visibility by `last_updated_seq`, which is mutable under translation and close events.
- The current native scene derives continuity from `block.id` and ordering from input list order.

The new design treats the two-block target as a hard visual slot limit rather than a soft layout preference.

## User-Facing Behavior

### Slot Model

- The overlay has `slot 1` and `slot 2`.
- Empty slots fill top to bottom: `slot 1`, then `slot 2`.
- When both slots are occupied, new content replaces the oldest finalized slot in place.
- Slots never shift upward to fill holes.
- If a slot becomes empty because content aged out, it stays empty until new content is assigned to it.
- Top-to-bottom order is physical slot order, not a guaranteed chronological transcript order once holes or replacements occur.

### Active Self And Final Self

- `active_self` and finalized self should look identical to the user.
- When `active_self` first appears, it occupies a slot like any other visible caption.
- While the user keeps speaking, text updates stay in the same slot with no additional signal.
- Promotion from `active_self` to finalized self keeps the same slot and produces no visual transition because both blocks carry the same logical occupant identity.
- Translation attachment and later close events update the same slot in place.

### Peer Turns

- Peer turns also occupy one of the two fixed slots.
- A newly visible peer turn gets the same one-time update signal as a new self turn.
- The first moment a peer turn becomes visible counts as new occupant assignment, even if the peer finalized transcript arrived earlier while the turn was still hidden waiting for translation.
- Later translation attachment or close events do not replay the signal.
- If peer runtime churn causes a peer turn to close before it ever becomes visible, no slot is assigned and no accent pulse is shown.

## Update Signal

The only motion-like feedback is a one-time left accent bar inside the slot.

- Position: inside the slot's left padding area.
- Width: `6px`.
- Motion: none.
- Timing: alpha pulse only, `0.12s`.
- Trigger: once when a new logical occupant is assigned to a slot.
- No replay for partial transcript updates, `active_self -> finalized`, translation attachment, or close.

### Accent Colors

- Self accent: cool off-white (`#C3CEDA`).
- Peer accent: muted amber (`#D2A24F`).

These colors intentionally do not match the current text fill colors exactly. They are meant to act as slot chrome, not content.

## Slot Assignment Rules

### Occupant Identity

The presenter-to-native snapshot payload must carry an explicit logical occupant identity separate from `id`. The identity rules are:

- Every snapshot block carries `occupant_key: str`.
- Every snapshot block carries `appearance_seq: int`, an immutable first-visible order marker.
- Finalized peer occupants use a deterministic key derived from `(channel, utterance_id)`.
- Finalized self occupants without active preview ancestry use a deterministic key derived from `(channel, utterance_id)`.
- Active self occupants use a key derived from the hub merge identity, not the fixed preview block ID.
- When an active self turn finalizes from the current merge path, the finalized block inherits the active block's `occupant_key`.
- Translation updates, close events, and later content updates must not change `occupant_key` or `appearance_seq`.

The native slot manager must use `occupant_key` as the authoritative continuity key. `block.id` remains a presentation/debug identifier and must not be used as the promotion key.

### Assignment Priority

Logical visible-set selection happens in the presenter before the snapshot reaches native state:

1. The presenter owns a capped logical visible set of at most two occupants.
2. `active_self` counts as one visible occupant while it exists.
3. If a new occupant must enter and both visible positions are already full, the presenter displaces the oldest visible finalized occupant.
4. A displaced finalized occupant is tombstoned for overlay purposes and must not re-enter later due only to late translation, close, or other late-arriving updates.
5. `appearance_seq` is assigned only when an occupant first becomes visible.
6. Hidden peer turns do not receive `appearance_seq` until translation makes them visible for the first time.

Native slot assignment happens only for the presenter-selected blocks:

1. Update any slot whose current occupant still exists in the selected logical set.
2. Promote matching `active_self` to finalized self in place.
3. Assign brand-new selected occupants to the first empty slot in order `slot 1 -> slot 2`.
4. Never evict a currently visible `active_self` to make room for a finalized update of that same turn.

Snapshot processing must be deterministic. The presenter must emit selected blocks sorted by `(appearance_seq, occupant_key)`, and native slot mapping must consume brand-new occupants in that order.

### Oldest-Finalized Rule

The replacement target is the oldest visible finalized occupant in the presenter-owned logical visible set. This rule is intentionally visibility-oriented, not strict recency sorting, and it is not affected by later translation or close updates.

## Expiration And Removal

- Explicit scene reset, explicit overlay disable, shutdown, and clear-style events must empty both slots immediately and reset all accent pulse state.
- Overlay runtime restart or reconnect paths that preserve presenter state must not clear the logical visible set. The next bridge session must start from the last presenter snapshot.
- Peer STT provider swap, peer runtime fault recovery, or peer runtime activation changes alone must not clear visible slots.
- Closed finalized turns still respect the existing presenter expiration model.
- Native rendering must not compact slots when a finalized turn expires.
- If a finalized slot expires, that slot becomes empty in place.
- A later incoming turn fills the earliest empty slot before any replacement occurs.

This preserves a stable spatial model even when content ages out.

## Architecture

### Presenter Responsibilities

`src/puripuly_heart/core/overlay/presenter.py`

- Continue to own logical caption entries, tombstones, late arrival handling, translation attachment, and close/expiration semantics.
- Own the capped logical visible set of at most two occupants.
- Decide which visible finalized occupant is displaced when a new occupant must enter a full logical visible set.
- Tombstone displaced finalized occupants so they do not re-enter later from late events.
- Assign and preserve `occupant_key` and `appearance_seq` metadata for every emitted block.
- Preserve the current peer publishability rule: peer entries become visible only once translation text is available.
- Treat first-visible peer translation as the moment a peer occupant enters the slot system.
- Ensure hidden peer turns that are canceled before first visibility never receive `occupant_key`, `appearance_seq`, or a slot assignment.
- Preserve late-arrival and tombstone semantics independently of native physical slot placement.
- Preserve existing finalized-entry semantics and wire event types.

The presenter should remain logical. It should not own physical slot numbers, accent pulse timing, or motion effects between the two fixed slots.

### Native State Responsibilities

`native/overlay/src/state.rs`

- Replace retained strip lifecycle animation with a retained two-slot scene model.
- Track exactly two physical slots with per-slot occupant identity, fixed slot anchor position, slot-entry order, and accent pulse lifecycle.
- Remove exiting-strip retention and vertical reflow behavior.
- Own physical slot continuity, hole retention, and one-shot accent triggering for the presenter-selected blocks only.
- Treat `active_self -> finalized` as an in-place occupant update when `occupant_key` matches.
- Remove text-matching-based promotion heuristics once explicit occupant metadata is available.

The state layer owns physical slot mapping and one-time accent triggering because those are visual-scene behaviors rather than presenter semantics.

### Runtime And Renderer Responsibilities

`native/overlay/src/runtime.rs`
`native/overlay/src/renderer/*`

- Stop deriving per-block opacity, vertical offset, and height scale from enter, exit, or reflow state.
- Render slots at fixed positions with `opacity = 1`, `offset_y = 0`, `height_scale = 1`.
- Stop deriving vertical placement from input block order. Slot y-position must come from slot state (`slot_index` / fixed anchor top).
- Add left accent bar rendering driven by slot pulse state.
- Ensure damage/clear bounds include the accent chrome so pulse end and slot removal do not leave stale accent pixels behind.
- Keep existing text layout and channel text fill behavior unless needed for slot rendering integration.

## Data And State Changes

### Presenter Snapshot Shape

The public overlay event names stay the same:

- `self_active_update`
- `self_active_clear`
- `self_transcript_final`
- `peer_transcript_final`
- `translation_final`
- `utterance_closed`

No new public event types or settings keys are introduced.
The overlay layer assumes the peer subsystem rewrite continues to express peer visibility through these same events rather than adding peer-runtime-specific overlay events.

The internal snapshot block schema extends to include:

- `occupant_key: str`
- `appearance_seq: int`

This is an internal Python-to-native contract change. Event types stay stable, but snapshot payload metadata becomes richer so slot continuity is explicit rather than heuristic. Because these fields become required for the new slot model, the overlay runtime contract version must increase so mixed Python/native builds fail fast rather than accepting incompatible snapshots.

### Native Slot State

The native scene should move from strip lifecycle state to slot state roughly equivalent to:

- fixed slot index
- fixed slot anchor top
- occupant identity
- channel
- block variant
- current primary and secondary text
- secondary enabled flag
- slot-entry order
- accent pulse progress
- accent pulse active flag

Exact field names are implementation detail, but the model must support in-place updates without visual relocation.

## Edge Cases

### Translation Arrives Late

- If the turn is still visible, update the existing slot in place.
- Do not replay the accent pulse.
- Late translation must not change `appearance_seq`.

### Peer First-Visible Translation

- If a peer finalized transcript was previously hidden and translation makes it visible for the first time, that event is treated as new occupant assignment.
- The slot receives the one-shot accent pulse at first visibility.
- Later peer translation updates for the same visible occupant do not replay the pulse.
- If that hidden peer turn is canceled before first visibility, it never gets `occupant_key`, `appearance_seq`, or an accent pulse.

### Active Self Clears Without Final

- Remove the active self occupant from its slot.
- Do not move the remaining slot.
- Leave the slot empty until future assignment fills it.
- If a later finalized self turn arrives with a different `occupant_key`, it is treated as a new occupant and may trigger the accent pulse.

### TTL Expiry With One Remaining Slot

- Expire the stale slot in place.
- Do not move the surviving slot.

### Multiple New Turns While Slots Are Full

- Each new turn replaces the oldest finalized slot at the moment of assignment.
- The replacement happens in place, one slot at a time.
- When multiple brand-new occupants appear in one snapshot, they are processed in presenter snapshot order, which is already sorted by `(appearance_seq, occupant_key)`.

### Peer Runtime Deactivation Or Restart

- Peer runtime activation changes alone do not clear visible peer slots.
- Peer STT provider swap or peer runtime fault recovery alone do not clear visible slots.
- Only existing overlay events, close handling, explicit overlay teardown, and TTL expiry may remove peer occupants from slots.
- If peer runtime recovery cancels a hidden peer turn before first visibility, that turn must not later surface as an in-place update.
- If peer runtime recovery yields a brand-new utterance with a new `utterance_id`, it is treated as new occupancy even if the text matches a previously shown peer line.
- Warmup completion is not a visibility boundary. Slot continuity is driven only by overlay events after the provider is attached.

### Tombstoned Or Late Events

- Preserve the current tombstone behavior in the presenter.
- If a turn is no longer logically eligible, late events should not resurrect it into a slot.

### Overlay Restart Versus Full Clear

- Overlay runtime restart or reconnect with preserved presenter state must seed the next bridge from the last presenter snapshot and must not blank the scene first.
- Explicit overlay disable, explicit scene reset, and shutdown must clear both slots and clear any in-progress accent pulse.
- After a full clear, any later caption appears as a new occupant assignment.
- Peer-provider replacement or peer runtime fault recovery are not themselves equivalent to full overlay clear.

## Testing

### Presenter Tests

- Presenter owns the capped two-occupant logical visible set rather than trimming by mutable `last_updated_seq`.
- `active_self` and its matching finalized self entry share the same `occupant_key` across promotion.
- `appearance_seq` is assigned on first visibility and does not change on translation or close updates.
- Translation and close events still attach to the correct logical entry.
- Existing tombstone and late-arrival protections remain intact.
- Peer turns remain hidden until translation exists.
- First-visible peer translation is treated as new occupancy rather than a silent in-place update.
- Hidden peer turns canceled before first visibility never receive `appearance_seq` or become visible.
- Displaced finalized occupants are tombstoned and do not re-enter.

### Native State Tests

- Empty slots fill in `slot 1 -> slot 2` order.
- Native maps only the presenter-selected logical visible set and does not invent a third candidate pool.
- `active_self` is not replaced by its own finalization.
- Slot compaction does not occur on expiration or clear.
- `active_self -> finalized` does not retrigger the accent pulse when `occupant_key` matches.
- `active_self -> finalized` falls back to new occupancy when `occupant_key` differs.
- Translation attachment and close do not retrigger the accent pulse.
- No exiting strip remains after snapshots change.
- First-visible peer translation triggers the accent pulse exactly once.
- Peer runtime restart that produces a new `utterance_id` is treated as new occupancy.
- The concrete `self_active_update -> self_transcript_final -> translation_final -> utterance_closed` flow keeps one slot when `occupant_key` is stable.
- Secondary-text changes in one slot do not change the other slot's fixed top position.
- Explicit full clear paths clear slot state and pulse state completely.
- Overlay restart with preserved presenter state rehydrates the same logical scene without a forced intermediate blank frame.

### Runtime And Renderer Tests

- Caption blocks render at fixed slot positions.
- Enter, exit, and reflow visual transforms are absent.
- Accent bar width and channel colors match the design values.
- Accent pulse timing is driven by a constant duration of `0.12s`.
- Damage bounds include accent chrome so pulse end and slot removal do not leave accent remnants behind.
- Runtime timing tests must assert pulse state by explicit delta-time progression, not by refresh-rate-dependent frame counts.
- Pulse completion tests should use a small epsilon tolerance over `0.12s` rather than exact frame count assumptions.

### Controller And Bridge Tests

- Overlay runtime restart with `preserve_presenter_state=True` seeds the next bridge from the last presenter snapshot.
- Explicit overlay disable tears down the presenter state so the next session starts empty.
- Peer runtime policy changes to inactive do not themselves force an overlay full clear.
- Full clear paths publish a monotonic empty snapshot before bridge detach so revision ordering remains valid.

### Manual Acceptance

- Lines never move vertically when the overlay is full.
- Finalization does not create a visible mode switch for self captions.
- Only first appearance of a new self or peer slot occupant produces the accent bar.
- Translation attachment and close do not flicker.
- TTL expiry does not cause the remaining slot to jump.
- Peer reconnect or restart does not surface stale invisible turns as if they were in-place updates.

## Rollout Notes

- This is a readability-first redesign. It intentionally trades strict time-order compaction for spatial stability.
- The existing active-self promotion work remains useful, but it will be absorbed into the broader two-slot slot-assignment model.
- The previous text-matching promotion heuristic is not part of the target design. Once `occupant_key` is available, promotion continuity is presenter-authored and native must stop inferring continuity from text equality.
- Broader ghosting investigation stays out of scope, but the new accent chrome must be included in damage clearing as part of the renderer integration.
- The peer subsystem rewrite is compatible with this design only if peer overlay visibility continues to be expressed through the current hub overlay events. Peer runtime policy, provider swap, warmup, or fault recovery are not themselves overlay clear events. If peer output semantics change, peer slot-assignment and pulse rules must be revisited before implementation.
