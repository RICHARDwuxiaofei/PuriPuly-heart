# VR Overlay Streaming Translation Design

## Goal

Add an optional VR overlay mode that runs beside the existing chatbox flow so the user can see:

- their own final STT text for recognition confirmation,
- peer final STT text from a separate speaker/loopback pipeline,
- streaming translation updates in the overlay,
- while VRChat chatbox output remains enabled for final text delivery.

The overlay must be isolated from the main app so overlay failures do not break STT, LLM, or chatbox output.

## Scope

In scope:

- Keep the current chatbox output path as the default public output.
- Add a separate overlay process controlled from the main app.
- Keep STT final-only for both self and peer channels.
- Keep LLM request timing utterance-based and single-shot.
- Stream only the LLM response to the overlay.
- Throttle overlay update frequency internally instead of repainting every token.
- Use localhost WebSocket for main-app to overlay IPC.
- Support `Overlay: Off / On` from the existing app UI.

Out of scope:

- Partial STT display.
- Interactive overlay controls.
- Warm standby overlay mode.
- Token-level chatbox streaming.
- Detailed peer audio capture redesign in this document.

Peer audio capture remains a dependency on the separate desktop/loopback STT work. This design only defines how peer final transcripts enter the overlay pipeline once available.

## User Experience

### Public Output

Chatbox stays on and keeps its current role:

- final text only,
- existing OSC limits/cooldown preserved,
- no streaming updates.

### Private Output

When overlay is enabled, the user sees:

- self original text,
- peer original text,
- translation text,
- translation updates appearing progressively as the LLM stream arrives.

The overlay display mode is fixed to `Original + Translation` for the MVP. More granular display options may be added later, but they are not part of the initial UI.

### User Setting

Expose only one user-facing setting:

- `Overlay: Off / On`

Everything else is implementation detail and should remain hidden in the MVP.

## Architecture

### High-Level Shape

The current runtime becomes a multi-sink pipeline:

`audio/STT -> orchestrator -> chatbox sink + overlay sink`

The main app remains responsible for:

- settings,
- STT session control,
- LLM requests,
- chatbox output,
- overlay process lifecycle.

The overlay process is responsible only for:

- connecting to the local WebSocket bridge,
- maintaining current overlay state,
- rendering VR overlay UI,
- shutting down cleanly when the main app disables overlay.

### Why Separate Process

Keeping the overlay in a separate process gives the best balance between safety and usability:

- OpenVR/runtime crashes are contained.
- Main translation/chatbox behavior survives overlay failure.
- The app can still present a simple `Off / On` UX by starting and stopping the overlay process internally.

### Packaging Direction

The overlay should be launched via the same packaged application using a dedicated runtime entrypoint such as `run-overlay`. This avoids shipping a second unrelated executable and keeps installer/build behavior easier to reason about.

## Streaming Model

### STT

STT remains unchanged in principle:

- self channel: final-only,
- peer channel: final-only,
- no partial transcript display in the overlay MVP.

### LLM

Each utterance still triggers one translation request. The difference is that providers must support a streaming response surface so the main app can:

1. receive partial translation text,
2. batch updates on a short timer,
3. send cumulative snapshots to the overlay,
4. still produce the final text for chatbox and history.

This means the major contract change is on the LLM provider side, not on the STT side.

### Overlay Update Policy

The overlay should not repaint on every token. Instead:

- the provider stream is consumed continuously,
- the main app coalesces updates,
- overlay receives cumulative translation snapshots on a fixed short interval,
- recommended initial interval: about `200 ms`.

This keeps the overlay feeling responsive without turning UI updates into the hot path.

## Event Model

The overlay bridge should prefer state-friendly events over token deltas.

Recommended events:

- `overlay_state_snapshot`
- `self_transcript_final`
- `peer_transcript_final`
- `translation_stream_update`
- `translation_final`
- `utterance_closed`
- `shutdown`

Common fields:

- `event_id`
- `seq`
- `utterance_id`
- `channel` (`self` or `peer`)
- `speaker_id` for peer channel
- `source_language`
- `target_language`
- `text`
- `is_final`
- `created_at`

`translation_stream_update` should carry the full accumulated text so far, not a token delta. That makes reconnect and state recovery much simpler.

## IPC and Process Lifecycle

### Transport

Use a localhost WebSocket bridge:

- bind only to `127.0.0.1`,
- create a one-time session token on startup,
- require the overlay client to present the token on connect,
- send a state snapshot immediately after connection,
- maintain heartbeat so both sides can detect stale peers.

### Lifecycle

When overlay is toggled on:

1. main app starts the WebSocket bridge,
2. main app launches overlay runtime,
3. overlay connects with token,
4. main app sends snapshot and live events.

When overlay is toggled off:

1. main app sends `shutdown`,
2. wait briefly for clean exit,
3. force-terminate only if needed,
4. keep chatbox path alive throughout.

If the overlay crashes:

- main app marks overlay disconnected,
- main app does not auto-restart in the MVP,
- user can recover by toggling `Off -> On`.

## Main-App Integration

### Settings and UI

Add one persisted setting for overlay enabled state and surface it through existing settings UI.

Requirements:

- `to_dict` and `from_dict` stay synchronized,
- default remains `False` for backward compatibility,
- all new UI strings go through i18n,
- all locale bundles must be updated.

### Controller Responsibilities

The GUI controller must:

- own overlay enabled state,
- start and stop the overlay bridge/process from async-safe UI flows,
- keep overlay lifecycle independent from translation/session lifecycle,
- preserve clean shutdown behavior.

### Orchestrator Responsibilities

The orchestrator must:

- keep chatbox final-only behavior unchanged,
- publish self and peer final transcript events to the overlay sink,
- publish translation stream updates and final translations to the overlay sink,
- tolerate overlay sink failure without taking down the utterance pipeline.

## Error Handling

### Overlay Startup Failure

- Main app stays alive.
- STT, LLM, and chatbox continue working.
- UI shows overlay start failure status.
- Overlay setting falls back to `Off`.

### Bridge Disconnect

- Overlay is marked disconnected.
- No retry storm in the MVP.
- Chatbox and history continue unaffected.

### LLM Stream Failure

- Overlay keeps the latest cumulative partial text it has seen.
- If no final arrives, close the line as incomplete.
- Chatbox still requires final text and therefore emits nothing for a failed translation.

### Main App Shutdown

- Always close provider connections first.
- Send overlay shutdown.
- Await cleanup and terminate the process if necessary.

## Testing and Validation

### Automated Validation

At minimum:

- settings round-trip for overlay enabled state,
- overlay protocol serialization/deserialization,
- WebSocket handshake/token/snapshot behavior,
- overlay process manager spawn/stop behavior,
- LLM streaming aggregation behavior,
- orchestrator emission of final transcript + translation stream/final events,
- chatbox still final-only.

### Manual Validation

- Toggle overlay on and off repeatedly.
- Kill overlay process while translation is running.
- Confirm main app and chatbox remain alive.
- Confirm overlay receives self final text, peer final text, and streaming translation text.
- Confirm packaged app can launch overlay runtime using the same installation.

## Tradeoffs

### Benefits

- Preserves the current reliable chatbox behavior.
- Gives the user faster perceived feedback through overlay streaming.
- Keeps overlay crashes isolated from the main translation loop.

### Costs

- Requires LLM provider interface changes.
- Adds a second runtime with lifecycle management.
- Makes build/packaging more sensitive if OpenVR bindings introduce extra runtime requirements.

### Known Limitation

This does not make translation truly simultaneous with live speech. It improves perceived latency by streaming the LLM response after final STT, while the STT segmentation model remains unchanged.
