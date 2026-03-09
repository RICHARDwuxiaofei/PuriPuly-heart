# VR Overlay Streaming Translation + Desktop Loopback Speech Enhancement Design

## Goal

Add an optional VR overlay mode that runs beside the existing chatbox flow so the user can see:

- their own final STT text for recognition confirmation,
- peer final STT text captured from desktop loopback audio,
- streaming translation updates in the overlay,
- while VRChat chatbox output remains enabled for self-channel final text delivery.

The peer desktop path must improve STT quality before recognition by making speech more prominent in the loopback signal. The overlay must still be isolated from the main app so overlay failures do not break STT, LLM, or chatbox output.

## Scope

In scope:

- Keep the current self chatbox output path as the default public output.
- Add a separate overlay process controlled from the main app.
- Capture desktop loopback audio from the active Windows output device for the peer channel.
- Downmix and format-convert loopback audio for enhancement.
- Run `DeepFilterNet2` as a local speech-enhancement stage before peer VAD and STT.
- Support raw bypass when desktop enhancement fails or falls behind.
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
- True speech separation.
- AEC for desktop loopback input.
- AGC-heavy desktop processing UI.
- Mixing self microphone and desktop loopback into one shared STT session.

Speaker diarization may still exist downstream at the STT/provider level, but the enhancement stage should not be framed as speaker isolation.

## Problem Statement

The current runtime is effectively:

`microphone -> VAD -> STT -> LLM -> OSC chatbox`

That is good enough for public self-output, but it does not solve two separate needs:

- users want private in-VR visibility into self and peer speech,
- peer speech from VRChat desktop output needs preprocessing before STT if it is going to be reliable enough to read in an overlay.

For desktop loopback audio, the target is not perfect source separation. The realistic target is a speech-forward signal that reduces ambience, reverb, and some masking so peer STT is usable enough for overlay display and translation.

`DeepFilterNet2` is an enhancement model, not a separation model. It can improve intelligibility, but it will not reliably remove overlapping voices, strong transient game sounds, or all background music/effects.

## User Experience

### Public Output

Chatbox stays on and keeps its current role:

- self-channel final text only,
- existing OSC limits and cooldown preserved,
- no streaming updates,
- no peer-channel forwarding.

Peer-channel text is overlay-only in the MVP.

### Private Output

When overlay is enabled, the user sees:

- self original text,
- peer original text,
- translation text,
- translation updates appearing progressively as the LLM stream arrives.

The overlay display mode is fixed to `Original + Translation` for the MVP. More granular display options may be added later, but they are not part of the initial UI.

### User Setting

Expose only one overlay-specific user-facing setting:

- `Overlay: Off / On`

Everything else is implementation detail or belongs in the general audio settings area.

### Audio Settings

Desktop loopback capture needs its own audio configuration, but this is not overlay-specific. Recommended settings:

- `desktop_audio.output_device`
- `desktop_audio.enhancement_enabled`
- `desktop_audio.raw_bypass_on_overload`
- `desktop_audio.limiter_enabled`

Self microphone settings stay where they already live.

## Input Pipelines

### Self Channel

Keep the current self pipeline unchanged in principle:

`Mic -> normalize/resample -> VAD -> STT final -> LLM -> chatbox final + overlay`

This remains the public-output path.

### Peer Channel

Add a concurrent peer pipeline sourced from desktop loopback audio:

`Desktop loopback capture -> mono/downmix -> 48 kHz alignment -> DeepFilterNet2 enhancement -> optional limiter/postfilter -> 16 kHz resample -> VAD -> STT final -> LLM -> overlay`

This path is private-output only.

### Why This Order

Enhancement should run before VAD so peer speech boundaries are derived from a cleaner signal. This improves speech start detection and reduces false speech decisions caused by constant ambience.

Desktop loopback does not need AEC because it is already the rendered output signal, not microphone capture with a playback reference.

AGC is not recommended for the MVP because it can amplify game audio and ambience together with speech. A light limiter is acceptable as a safety stage after enhancement.

Resampling should happen after enhancement so the model stays close to full-band operating conditions, then the output can be converted to the existing 16 kHz VAD/STT format.

## Architecture

### High-Level Shape

The runtime becomes a dual-input, multi-sink pipeline:

`self mic path + peer desktop path -> orchestrator -> self chatbox sink + overlay sink`

The main app remains responsible for:

- settings,
- self and peer STT session control,
- desktop loopback capture and enhancement lifecycle,
- LLM requests,
- chatbox output,
- overlay process lifecycle.

The overlay process is responsible only for:

- connecting to the local WebSocket bridge,
- maintaining current overlay state,
- rendering VR overlay UI,
- shutting down cleanly when the main app disables overlay.

### New Components

`DesktopLoopbackAudioSource`
- Captures Windows loopback audio from the selected output device.
- Produces float32 frames suitable for the enhancement worker.

`DesktopEnhancementProcessor`
- Owns the `DeepFilterNet2` runtime.
- Accepts 48 kHz mono float32 frames.
- Returns enhanced 48 kHz mono float32 frames.
- Exposes overload and health state for fallback decisions.

`DesktopPeerPipeline`
- Wires loopback capture, enhancement, limiter/postfilter, resampling, VAD, and STT together for the peer channel.
- Keeps buffering and bypass logic isolated from the current microphone path.

`OverlayBridge`
- Publishes state-friendly overlay events from the main app to the overlay runtime.

### Why Separate Process

Keeping the overlay in a separate process gives the best balance between safety and usability:

- OpenVR/runtime crashes are contained.
- Main translation/chatbox behavior survives overlay failure.
- The app can still present a simple `Off / On` UX by starting and stopping the overlay process internally.

### Packaging Direction

The overlay should be launched via the same packaged application using a dedicated runtime entrypoint such as `run-overlay`. This avoids shipping a second unrelated executable and keeps installer/build behavior easier to reason about.

## Peer Desktop Audio Runtime

### Data Flow

1. Open the configured desktop loopback device.
2. Downmix to mono if needed.
3. Resample to 48 kHz if the device format differs.
4. Feed frames into `DesktopEnhancementProcessor`.
5. Optionally apply a light limiter to catch overs.
6. Resample enhanced output to 16 kHz.
7. Chunk the stream for peer VAD.
8. Emit peer `SpeechStart`, `SpeechChunk`, and `SpeechEnd`.
9. Reuse the existing STT controller/session lifecycle semantics with a peer channel label.
10. Forward peer final transcripts to overlay and translation stages only.

### Performance Expectations

Expected peer-path overhead from desktop enhancement:

- light case: about `+20 ms` to `+35 ms`,
- typical case: about `+20 ms` to `+60 ms`,
- bad case under CPU contention: higher, with more jitter and queue buildup.

This overhead applies to the peer overlay path, not the self chatbox path. The existing 16 kHz VAD chunking still dominates the STT boundary after enhancement.

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
- `overlay_enabled` default remains `False` for backward compatibility,
- desktop-audio settings get sane defaults so existing `settings.json` continues loading,
- all new UI strings go through i18n,
- all locale bundles must be updated.

### Controller Responsibilities

The GUI controller must:

- own overlay enabled state,
- own desktop loopback peer-pipeline lifecycle,
- start and stop the overlay bridge/process from async-safe UI flows,
- start and stop the peer desktop audio pipeline without interrupting the self mic path,
- keep overlay lifecycle independent from translation/session lifecycle,
- preserve clean shutdown behavior.

### Orchestrator Responsibilities

The orchestrator must:

- keep self chatbox final-only behavior unchanged,
- publish self and peer final transcript events to the overlay sink,
- publish translation stream updates and final translations to the overlay sink,
- ensure peer-channel outputs never leak into chatbox output,
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

### Enhancement Init Failure

- Log the failure.
- If raw bypass is enabled, switch the peer path to raw desktop audio.
- If raw bypass is disabled, fail only the peer overlay input path and keep the self path alive.

### Enhancement Overload / Queue Buildup

- Track queue depth and processing lag.
- If lag exceeds a threshold, switch the peer path to raw bypass.
- Surface degraded state in logs and UI.

### Desktop Device Loss

- Attempt to reopen the loopback source.
- If reopen fails, stop the peer path cleanly and emit a peer-channel error.

### LLM Stream Failure

- Overlay keeps the latest cumulative partial text it has seen.
- If no final arrives, close the line as incomplete.
- Chatbox still requires final text and therefore emits nothing for a failed self translation.

### Main App Shutdown

- Always close provider connections first.
- Stop peer desktop capture and enhancement tasks cleanly.
- Send overlay shutdown.
- Await cleanup and terminate the process if necessary.

## Testing and Validation

### Automated Validation

At minimum:

- settings round-trip for overlay enabled state,
- settings round-trip for desktop loopback and enhancement fields,
- desktop loopback device selection and capture startup,
- enhancement processor success, bypass, and overload behavior,
- overlay protocol serialization and deserialization,
- WebSocket handshake, token, and snapshot behavior,
- overlay process manager spawn and stop behavior,
- peer-channel routing from desktop path into overlay-only outputs,
- LLM streaming aggregation behavior,
- orchestrator emission of final transcript plus translation stream/final events,
- self chatbox still final-only and peer never sent to chatbox.

### Manual Validation

- Toggle overlay on and off repeatedly.
- Kill overlay process while translation is running.
- Confirm main app and chatbox remain alive.
- Confirm desktop loopback capture opens for the selected output device.
- Compare raw vs enhanced peer transcripts on representative VRChat desktop audio clips.
- Confirm fallback to raw peer path when enhancement fails.
- Confirm overlay receives self final text, peer final text, and streaming translation text.
- Confirm packaged app can launch overlay runtime using the same installation.

## Tradeoffs

### Benefits

- Preserves the current reliable chatbox behavior.
- Gives the user faster perceived feedback through overlay streaming.
- Improves peer STT quality by cleaning desktop loopback audio before recognition.
- Keeps overlay crashes isolated from the main translation loop.

### Costs

- Requires LLM provider interface changes.
- Adds a new desktop capture and enhancement dependency surface.
- Adds a second runtime with lifecycle management.
- Adds CPU load and some latency to the peer path.
- Makes build and packaging more sensitive if OpenVR bindings or enhancement runtime introduce extra dependencies.

### Known Limitation

This does not make translation truly simultaneous with live speech. It improves perceived latency by streaming the LLM response after final STT, while the STT segmentation model remains unchanged.

Desktop enhancement also does not solve overlapping voices completely. It is a speech-forward preprocessing stage, not speaker separation.

## Recommendation

Proceed with a combined design:

1. keep the self microphone/chatbox path stable,
2. add a concurrent desktop loopback peer path with `DeepFilterNet2` enhancement before VAD,
3. stream only LLM results to the overlay,
4. keep chatbox final-only and self-channel only,
5. keep overlay isolated as a separate process controlled from the main app.
