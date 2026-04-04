# Overlay Two-Slot Static Caption Design

## Summary

The overlay should stop behaving like a scrolling strip stack and instead behave like two fixed caption slots. The goal is to remove layout motion that hurts readability in the headset, especially enter, exit, and reflow movement during active-self promotion, finalized turnover, and TTL expiry.

This design keeps the existing overlay wire contract and logical turn model, but changes the visual model:

- The overlay has exactly two physical slots.
- Slots do not move, compact, slide, or scale.
- Text is replaced in place.
- A left accent bar is the only update signal.

This design also assumes the peer subsystem rewrite preserves the current hub-to-overlay event contract for peer turns, even if peer runtime start, stop, and recovery timing changes.

## Goals

- Remove perceived flicker caused by strip removal, re-entry, and vertical reflow.
- Keep caption reading stable by preventing slot motion.
- Make `active_self` and finalized self text visually continuous.
- Preserve the existing logical event contract and turn lifecycle semantics where possible.

## Non-Goals

- No damage-band or ghosting investigation in this work.
- No new user-facing settings for animation style or slot count.
- No redesign of translation timing or utterance close semantics.
- No increase beyond two visible slots.

## Existing Constraints

- The presenter currently allows two finalized blocks plus a separate `active_self` block, which can produce three visible rows.
- The native overlay scene currently uses entering, exiting, and reflowing strips with sampled animation progress.
- The overlay layout policy already targets two visible blocks, but the scene model still allows motion and transient exit strips.
- Peer visibility today is publishability-driven: peer finalized transcripts can stay hidden until translation text arrives.

The new design treats the two-block target as a hard visual slot limit rather than a soft layout preference.

## User-Facing Behavior

### Slot Model

- The overlay has `slot 1` and `slot 2`.
- Empty slots fill top to bottom: `slot 1`, then `slot 2`.
- When both slots are occupied, new content replaces the oldest finalized slot in place.
- Slots never shift upward to fill holes.
- If a slot becomes empty because content aged out, it stays empty until new content is assigned to it.

### Active Self And Final Self

- `active_self` and finalized self should look identical to the user.
- When `active_self` first appears, it occupies a slot like any other visible caption.
- While the user keeps speaking, text updates stay in the same slot with no additional signal.
- Promotion from `active_self` to finalized self keeps the same slot and produces no visual transition.
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
- Timing: alpha pulse only, about `120ms`.
- Trigger: once when a new logical occupant is assigned to a slot.
- No replay for partial transcript updates, `active_self -> finalized`, translation attachment, or close.

### Accent Colors

- Self accent: cool off-white (`#C3CEDA`).
- Peer accent: muted amber (`#D2A24F`).

These colors intentionally do not match the current text fill colors exactly. They are meant to act as slot chrome, not content.

## Slot Assignment Rules

### Occupant Identity

The native slot manager should track a logical occupant identity separately from presentation block ID. The identity rules are:

- Finalized turn occupant: `(channel, utterance_id)`.
- Active self occupant: a dedicated active-self identity that can later promote into the finalized self identity for the same utterance without changing slots.

### Assignment Priority

When a snapshot arrives:

1. Update any slot whose current occupant still exists in the new logical set.
2. Promote matching `active_self` to finalized self in place.
3. Assign brand-new occupants to the first empty slot in order `slot 1 -> slot 2`.
4. If no slot is empty, replace the oldest finalized slot.
5. Never evict a currently visible `active_self` to make room for a finalized update of that same turn.
6. If a new `active_self` must enter while both slots are full, it replaces the oldest finalized slot.

### Oldest-Finalized Rule

The replacement target is the visible finalized slot with the earliest slot-entry order. This rule is intentionally physical-slot oriented, not strict visual recency sorting.

## Expiration And Removal

- Explicit scene reset, provider replacement, shutdown, and clear-style events may empty both slots immediately.
- Closed finalized turns still respect the existing presenter expiration model.
- Native rendering must not compact slots when a finalized turn expires.
- If a finalized slot expires, that slot becomes empty in place.
- A later incoming turn fills the earliest empty slot before any replacement occurs.

This preserves a stable spatial model even when content ages out.

## Architecture

### Presenter Responsibilities

`src/puripuly_heart/core/overlay/presenter.py`

- Continue to own logical caption entries, tombstones, late arrival handling, translation attachment, and close/expiration semantics.
- Change visible block selection so the presenter emits at most two logical blocks total.
- Treat `active_self` as part of the same two-slot candidate pool instead of always appending it as an extra row.
- Preserve the current peer publishability rule: peer entries become visible only once translation text is available.
- Treat first-visible peer translation as the moment a peer occupant enters the slot system.
- Preserve existing finalized-entry semantics and wire event types.

The presenter should remain logical. It should not own physical slot numbers, accent pulse timing, or visual replacement effects.

### Native State Responsibilities

`native/overlay/src/state.rs`

- Replace retained strip lifecycle animation with a retained two-slot scene model.
- Track exactly two physical slots with per-slot occupant identity, stable content, slot-entry order, and accent pulse lifecycle.
- Remove exiting-strip retention and vertical reflow behavior.
- Keep `active_self -> finalized` as an in-place occupant update when the logical turn matches.

The state layer owns slot assignment and one-time accent triggering because those are visual-scene behaviors rather than presenter semantics.

### Runtime And Renderer Responsibilities

`native/overlay/src/runtime.rs`
`native/overlay/src/renderer/*`

- Stop deriving per-block opacity, vertical offset, and height scale from enter, exit, or reflow state.
- Render slots at fixed positions with `opacity = 1`, `offset_y = 0`, `height_scale = 1`.
- Add left accent bar rendering driven by slot pulse state.
- Keep existing text layout and channel text fill behavior unless needed for slot rendering integration.

## Data And State Changes

### Presenter Snapshot Shape

The public snapshot schema stays the same:

- `self_active_update`
- `self_active_clear`
- `self_transcript_final`
- `peer_transcript_final`
- `translation_final`
- `utterance_closed`

No new public event types or settings keys are introduced.
The overlay layer assumes the peer subsystem rewrite continues to express peer visibility through these same events rather than adding peer-runtime-specific overlay events.

### Native Slot State

The native scene should move from strip lifecycle state to slot state roughly equivalent to:

- fixed slot index
- occupant identity
- channel
- block variant
- current primary and secondary text
- secondary enabled flag
- slot-entry order
- accent pulse progress

Exact field names are implementation detail, but the model must support in-place updates without visual relocation.

## Edge Cases

### Translation Arrives Late

- If the turn is still visible, update the existing slot in place.
- Do not replay the accent pulse.

### Peer First-Visible Translation

- If a peer finalized transcript was previously hidden and translation makes it visible for the first time, that event is treated as new occupant assignment.
- The slot receives the one-shot accent pulse at first visibility.
- Later peer translation updates for the same visible occupant do not replay the pulse.

### Active Self Clears Without Final

- Remove the active self occupant from its slot.
- Do not move the remaining slot.
- Leave the slot empty until future assignment fills it.

### TTL Expiry With One Remaining Slot

- Expire the stale slot in place.
- Do not move the surviving slot.

### Multiple New Turns While Slots Are Full

- Each new turn replaces the oldest finalized slot at the moment of assignment.
- The replacement happens in place, one slot at a time.

### Peer Runtime Deactivation Or Restart

- Peer runtime activation changes alone do not clear visible peer slots.
- Only existing overlay events, close handling, and TTL expiry may remove peer occupants from slots.
- If peer runtime recovery yields a brand-new utterance with a new `utterance_id`, it is treated as a new occupant even if the text matches a previously shown peer line.

### Tombstoned Or Late Events

- Preserve the current tombstone behavior in the presenter.
- If a turn is no longer logically eligible, late events should not resurrect it into a slot.

## Testing

### Presenter Tests

- Snapshot never exceeds two blocks.
- `active_self` no longer appears as a third appended block.
- `active_self` can occupy a slot and promote to finalized without duplicate visibility.
- Translation and close events still attach to the correct logical entry.
- Existing tombstone and late-arrival protections remain intact.
- Peer turns remain hidden until translation exists.
- First-visible peer translation is treated as new occupancy rather than a silent in-place update.

### Native State Tests

- Empty slots fill in `slot 1 -> slot 2` order.
- New occupants replace the oldest finalized slot when full.
- `active_self` is not replaced by its own finalization.
- Slot compaction does not occur on expiration or clear.
- `active_self -> finalized` does not retrigger the accent pulse.
- Translation attachment and close do not retrigger the accent pulse.
- No exiting strip remains after snapshots change.
- First-visible peer translation triggers the accent pulse exactly once.
- Peer runtime restart that produces a new `utterance_id` is treated as new occupancy.

### Runtime And Renderer Tests

- Caption blocks render at fixed slot positions.
- Enter, exit, and reflow visual transforms are absent.
- Accent bar width and channel colors match the design values.
- Accent pulse timing is one-shot and bounded.

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
- Ghosting and damage-band cleanup stay out of scope and should be evaluated after the motion model is simplified.
- The peer subsystem rewrite is compatible with this design only if peer overlay visibility continues to be expressed through the current hub overlay events. If peer output semantics change, peer slot-assignment and pulse rules must be revisited before implementation.
