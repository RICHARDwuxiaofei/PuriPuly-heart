# ADR: Peer Utterance Chatbox Hard-Deny

- Status: Accepted
- Date: 2026-06-11
- Work ref: `extract-output-runtime-owner`

## Context

The vNext refactoring architecture separates self, peer, and system output
channels. The output boundary specifies that peer-channel utterance segments,
meaning another participant's speech path, must not publish to the VRChat OSC
chatbox. It also states that legacy chatbox preferences such as
`active_chatbox_channel == "peer"` must not override the denial.

Order 36 (`extract-output-runtime-owner`) encountered a conflict between that
architecture and current legacy behavior:

- `ClientHub._should_publish_to_chatbox(...)` currently allows chatbox output
  whenever `runtime.channel == active_chatbox_channel`, including `peer`.
- Existing tests assert peer utterance `OSC_SENT` events when
  `active_chatbox_channel` is set to `"peer"`.

Continuing order 36 therefore requires a durable policy decision before
extracting the output lifecycle owner, because enforcing the architecture changes
existing product routing behavior.

## Decision

Peer utterance output is hard-denied from the VRChat OSC chatbox.

This denial applies to every peer utterance route, including:

- legacy `active_chatbox_channel == "peer"` or equivalent preferences;
- translation-disabled paths;
- translation failure and transcript-only fallback paths;
- cancellation or stale-completion cleanup paths;
- test, debug, or missing-overlay fallback paths.

Peer transcript and translation output may still publish to subtitle overlay,
dashboard, runtime logs, diagnostics, and conversation/output observers according
to their own policies. System disclosure chatbox messages remain separate from
peer utterance output and may use their explicit system-disclosure route.

When a peer utterance attempts a chatbox route, the output boundary must produce
a denied or skipped observer event without transcript text, translation text, or
other user speech payload. The observer event may include diagnostic-safe
metadata such as route, channel, publication kind, and reason.

## Consequences

- Order 36 may update legacy tests that expected peer utterance OSC/chatbox
  output.
- Output lifecycle ownership must reject or skip closed-runtime output without
  leaking user text into denied/skipped observer events.
- `active_chatbox_channel` and related compatibility state must not authorize
  peer utterance chatbox output.
- Later OutputRouter work should preserve this hard-deny as a compatibility and
  privacy invariant rather than reintroducing peer chatbox routing.
- Product behavior changes from any previous peer-chatbox mode: peer speech is
  no longer sent to the VRChat chatbox through the self chatbox path.

## References

- `docs/superpowers/specs/2026-06-08-vnext-refactoring-architecture-design.md`
- `.agents/bundles/vnext-refactoring-architecture.yaml`
- `.agents/evidence/vnext-refactoring-architecture/order-036-extract-output-runtime-owner.md`
- `src/puripuly_heart/core/orchestrator/hub.py`
- `tests/core/test_hub_overlay_streaming.py`
