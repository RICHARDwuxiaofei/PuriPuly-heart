# VR Overlay Streaming Translation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional VR overlay runtime that shows self/peer final transcripts plus throttled streaming translation updates, while a concurrent desktop loopback peer pipeline uses local speech enhancement before STT and VRChat chatbox remains self-channel final-only.

**Architecture:** Extend the main app into a dual-input, multi-sink translation pipeline. The self microphone path stays close to the current chatbox flow. A concurrent desktop loopback peer path captures Windows output, enhances it with `DeepFilterNet2`, then feeds VAD and STT for overlay-only peer transcripts. A separate overlay process connects over localhost WebSocket and renders cumulative snapshot-style events. LLM providers gain a streaming surface, but STT remains final-only.

**Tech Stack:** `asyncio`, `websockets`, `sounddevice`/PortAudio loopback capture, `numpy`, existing Flet GUI controller/settings flows, existing orchestrator and OSC pipeline, Python subprocess/process management, OpenVR-backed overlay runtime adapter, `DeepFilterNet2` runtime.

---

## Implementation Notes

- Keep `chatbox` behavior unchanged unless a test proves a required adjustment.
- Self channel remains the only chatbox publisher.
- Peer desktop loopback is a separate concurrent channel, not an input-mode replacement for the microphone path.
- `DeepFilterNet2` is a speech-enhancement stage, not speaker separation.
- Every new user-facing string must go through `src/puripuly_heart/data/i18n/*.json`.
- Keep `to_dict` and `from_dict` synchronized for any new setting.
- Do not block the event loop in overlay lifecycle or LLM streaming code.

### Task 1: Add Overlay and Desktop-Audio Settings Plus Event Protocol

**Files:**
- Create: `src/puripuly_heart/core/overlay/__init__.py`
- Create: `src/puripuly_heart/core/overlay/protocol.py`
- Create: `tests/config/test_overlay_desktop_audio_settings.py`
- Create: `tests/core/test_overlay_protocol.py`
- Modify: `src/puripuly_heart/config/settings.py`
- Modify: `src/puripuly_heart/data/i18n/en.json`
- Modify: `src/puripuly_heart/data/i18n/ko.json`
- Modify: `src/puripuly_heart/data/i18n/zh-CN.json`
- Modify: `src/puripuly_heart/ui/components/settings/audio_settings.py`
- Modify: `src/puripuly_heart/ui/views/settings.py`

**Step 1: Write the failing tests**

```python
def test_overlay_and_desktop_audio_settings_round_trip():
    settings = from_dict({"ui": {"overlay_enabled": True}})
    assert settings.ui.overlay_enabled is True
    assert to_dict(settings)["ui"]["overlay_enabled"] is True
    assert "desktop_audio" in to_dict(settings)


def test_translation_stream_update_serializes_cumulative_text():
    event = TranslationStreamUpdate(text="hello wor", ...)
    payload = event.to_dict()
    assert payload["text"] == "hello wor"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/config/test_overlay_desktop_audio_settings.py tests/core/test_overlay_protocol.py -q`

Expected: FAIL because overlay and desktop-audio settings plus overlay protocol types do not exist yet.

**Step 3: Write the minimal implementation**

- Add `overlay_enabled: bool = False` under `UiSettings`.
- Add desktop loopback settings for:
  - `output_device`
  - `enhancement_enabled`
  - `raw_bypass_on_overload`
  - `limiter_enabled`
- Update `validate`, `to_dict`, and `from_dict`.
- Add overlay i18n keys for the single overlay toggle and desktop-audio labels/help text.
- Add typed protocol models for:
  - `OverlayStateSnapshot`
  - `SelfTranscriptFinal`
  - `PeerTranscriptFinal`
  - `TranslationStreamUpdate`
  - `TranslationFinal`
  - `UtteranceClosed`
  - `Shutdown`

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/config/test_overlay_desktop_audio_settings.py tests/core/test_overlay_protocol.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/config/settings.py src/puripuly_heart/core/overlay/__init__.py src/puripuly_heart/core/overlay/protocol.py src/puripuly_heart/data/i18n/en.json src/puripuly_heart/data/i18n/ko.json src/puripuly_heart/data/i18n/zh-CN.json src/puripuly_heart/ui/components/settings/audio_settings.py src/puripuly_heart/ui/views/settings.py tests/config/test_overlay_desktop_audio_settings.py tests/core/test_overlay_protocol.py
git commit -m "Add settings for overlay and peer desktop audio"
```

### Task 2: Add Desktop Loopback Capture and Enhancement Primitives

**Files:**
- Create: `src/puripuly_heart/core/audio/desktop_source.py`
- Create: `src/puripuly_heart/core/audio/desktop_enhancement.py`
- Create: `tests/core/test_desktop_audio_source.py`
- Create: `tests/core/test_desktop_enhancement.py`
- Modify: `pyproject.toml`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_desktop_loopback_source_yields_float32_frames():
    frame = await anext(source.frames())
    assert frame.sample_rate_hz > 0
    assert frame.samples.dtype == np.float32


def test_enhancement_processor_reports_overload_and_can_bypass():
    result = processor.process(frame)
    assert result.mode in {"enhanced", "bypass"}
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_desktop_audio_source.py tests/core/test_desktop_enhancement.py -q`

Expected: FAIL because no desktop loopback source or enhancement processor exists.

**Step 3: Write the minimal implementation**

- Add a desktop loopback source that captures Windows output frames.
- Add a `DeepFilterNet2`-backed enhancement processor for 48 kHz mono float32 frames.
- Expose overload/health/bypass status needed by the peer pipeline.
- Add only the dependency surface needed to load the enhancement runtime.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_desktop_audio_source.py tests/core/test_desktop_enhancement.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/core/audio/desktop_source.py src/puripuly_heart/core/audio/desktop_enhancement.py pyproject.toml tests/core/test_desktop_audio_source.py tests/core/test_desktop_enhancement.py
git commit -m "Improve peer loopback STT before overlay routing"
```

### Task 3: Add Desktop Peer Audio Pipeline and Raw-Bypass Fallback

**Files:**
- Create: `src/puripuly_heart/core/audio/desktop_pipeline.py`
- Create: `tests/core/test_desktop_audio_pipeline.py`
- Modify: `src/puripuly_heart/core/audio/format.py`
- Modify: `src/puripuly_heart/core/audio/source.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_desktop_pipeline_outputs_16khz_vad_ready_frames():
    frame = await anext(pipeline.frames())
    assert frame.sample_rate_hz == 16000


@pytest.mark.asyncio
async def test_desktop_pipeline_falls_back_to_raw_when_enhancement_overloads():
    frame = await anext(pipeline.frames())
    assert pipeline.mode == "bypass"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_desktop_audio_pipeline.py -q`

Expected: FAIL because the peer desktop pipeline and bypass behavior do not exist.

**Step 3: Write the minimal implementation**

- Add a pipeline that performs:
  - mono/downmix,
  - 48 kHz alignment for enhancement,
  - enhancement + limiter/postfilter,
  - 16 kHz output for VAD/STT.
- Emit peer-path health data for logging and fallback.
- Switch cleanly to raw bypass when enhancement is unavailable or overloaded.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_desktop_audio_pipeline.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/core/audio/desktop_pipeline.py src/puripuly_heart/core/audio/format.py src/puripuly_heart/core/audio/source.py tests/core/test_desktop_audio_pipeline.py
git commit -m "Keep peer loopback usable when enhancement falls behind"
```

### Task 4: Add Channel-Aware Self and Peer STT Routing

**Files:**
- Create: `tests/core/test_peer_channel_routing.py`
- Modify: `src/puripuly_heart/domain/events.py`
- Modify: `src/puripuly_heart/domain/models.py`
- Modify: `src/puripuly_heart/core/orchestrator/hub.py`
- Modify: `src/puripuly_heart/app/headless_mic.py`
- Modify: `src/puripuly_heart/ui/controller.py`
- Modify: `tests/app/test_headless_mic_runner.py`
- Modify: `tests/core/test_hub_low_latency.py`
- Modify: `tests/core/test_orchestrator_pipeline.py`
- Modify: `tests/ui/test_controller_branch_paths.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_peer_desktop_transcripts_are_marked_peer_and_never_sent_to_chatbox():
    ...


@pytest.mark.asyncio
async def test_self_and_peer_channels_can_run_concurrently():
    ...
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_peer_channel_routing.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py tests/app/test_headless_mic_runner.py tests/ui/test_controller_branch_paths.py -q`

Expected: FAIL because the runtime is not channel-aware and cannot run self/peer paths concurrently.

**Step 3: Write the minimal implementation**

- Add channel/source labels to transcript and translation event paths.
- Run the self mic path and peer desktop path concurrently.
- Reuse existing STT controller/session semantics per channel where possible.
- Ensure peer-channel outputs are overlay-only.
- Keep self-channel chatbox flow unchanged.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_peer_channel_routing.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py tests/app/test_headless_mic_runner.py tests/ui/test_controller_branch_paths.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/domain/events.py src/puripuly_heart/domain/models.py src/puripuly_heart/core/orchestrator/hub.py src/puripuly_heart/app/headless_mic.py src/puripuly_heart/ui/controller.py tests/core/test_peer_channel_routing.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py tests/app/test_headless_mic_runner.py tests/ui/test_controller_branch_paths.py
git commit -m "Keep peer transcripts private while self chatbox stays stable"
```

### Task 5: Add Streaming LLM Provider Support

**Files:**
- Create: `tests/core/test_llm_streaming_provider.py`
- Modify: `src/puripuly_heart/core/llm/provider.py`
- Modify: `src/puripuly_heart/providers/llm/gemini.py`
- Modify: `src/puripuly_heart/providers/llm/qwen.py`
- Modify: `src/puripuly_heart/app/wiring.py`
- Modify: `tests/providers/test_gemini_provider.py`
- Modify: `tests/providers/test_qwen_provider.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_stream_translate_yields_cumulative_snapshots():
    chunks = []
    async for chunk in provider.stream_translate(...):
        chunks.append(chunk.text)
    assert chunks == ["hel", "hello", "hello world"]


@pytest.mark.asyncio
async def test_translate_aggregates_stream_to_final_translation():
    result = await provider.translate(...)
    assert result.text == "hello world"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_llm_streaming_provider.py tests/providers/test_gemini_provider.py tests/providers/test_qwen_provider.py -q`

Expected: FAIL because providers expose only `translate()`.

**Step 3: Write the minimal implementation**

- Extend `LLMProvider` with a streaming method that yields cumulative translation snapshots.
- Keep `translate()` as the compatibility surface by aggregating the stream when needed.
- Implement provider-specific streaming in `GeminiLLMProvider` and `QwenLLMProvider`.
- Preserve async cleanup behavior in all teardown paths.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_llm_streaming_provider.py tests/providers/test_gemini_provider.py tests/providers/test_qwen_provider.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/core/llm/provider.py src/puripuly_heart/providers/llm/gemini.py src/puripuly_heart/providers/llm/qwen.py src/puripuly_heart/app/wiring.py tests/core/test_llm_streaming_provider.py tests/providers/test_gemini_provider.py tests/providers/test_qwen_provider.py
git commit -m "Stream translation early enough to matter in the overlay"
```

### Task 6: Add Overlay Bridge Server and Process Manager

**Files:**
- Create: `src/puripuly_heart/core/overlay/bridge.py`
- Create: `src/puripuly_heart/core/overlay/process.py`
- Create: `tests/core/test_overlay_bridge.py`
- Create: `tests/app/test_overlay_process_manager.py`
- Modify: `src/puripuly_heart/main.py`
- Modify: `tests/app/test_main_cli.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_overlay_bridge_requires_session_token():
    ...


@pytest.mark.asyncio
async def test_overlay_process_manager_starts_and_stops_child():
    ...
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_overlay_bridge.py tests/app/test_overlay_process_manager.py tests/app/test_main_cli.py -q`

Expected: FAIL because no overlay bridge, no overlay runtime entrypoint, and no process manager exist.

**Step 3: Write the minimal implementation**

- Add a localhost WebSocket server bound to `127.0.0.1`.
- Generate and validate one-time session token.
- Add heartbeat and immediate snapshot send on successful connection.
- Add process manager that launches the same app with a `run-overlay` entrypoint and shuts it down cleanly.
- Extend CLI parser with `run-overlay`.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_overlay_bridge.py tests/app/test_overlay_process_manager.py tests/app/test_main_cli.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/core/overlay/bridge.py src/puripuly_heart/core/overlay/process.py src/puripuly_heart/main.py tests/core/test_overlay_bridge.py tests/app/test_overlay_process_manager.py tests/app/test_main_cli.py
git commit -m "Isolate overlay failures from the main translation loop"
```

### Task 7: Wire Overlay Sink Into the Orchestrator

**Files:**
- Create: `src/puripuly_heart/core/overlay/sink.py`
- Create: `tests/core/test_hub_overlay_streaming.py`
- Modify: `src/puripuly_heart/core/orchestrator/hub.py`
- Modify: `tests/core/test_hub_low_latency.py`
- Modify: `tests/core/test_orchestrator_pipeline.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_hub_emits_self_and_peer_finals_to_overlay_sink():
    ...


@pytest.mark.asyncio
async def test_chatbox_stays_self_final_only_while_overlay_receives_stream_updates():
    ...
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_hub_overlay_streaming.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py -q`

Expected: FAIL because the orchestrator has no overlay sink or streaming translation event path.

**Step 3: Write the minimal implementation**

- Add an overlay sink interface that accepts protocol events.
- Publish self and peer final transcript events as soon as final STT arrives.
- Consume LLM stream updates, throttle them, and emit cumulative `translation_stream_update`.
- Emit `translation_final` for overlay.
- Keep chatbox send restricted to self-channel final text only.
- Ensure overlay sink failure is logged and isolated.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_hub_overlay_streaming.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/core/overlay/sink.py src/puripuly_heart/core/orchestrator/hub.py tests/core/test_hub_overlay_streaming.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py
git commit -m "Route self and peer overlays without changing public output"
```

### Task 8: Add Overlay Runtime Client and State Store

**Files:**
- Create: `src/puripuly_heart/app/headless_overlay.py`
- Create: `src/puripuly_heart/overlay/__init__.py`
- Create: `src/puripuly_heart/overlay/client.py`
- Create: `src/puripuly_heart/overlay/state.py`
- Create: `src/puripuly_heart/overlay/renderer.py`
- Create: `tests/app/test_headless_overlay.py`
- Create: `tests/overlay/test_overlay_state.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_overlay_client_applies_snapshot_then_stream_updates():
    ...


def test_overlay_state_keeps_original_and_translation_rows_separate():
    ...
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/app/test_headless_overlay.py tests/overlay/test_overlay_state.py -q`

Expected: FAIL because no overlay runtime/client/state store exists.

**Step 3: Write the minimal implementation**

- Add a headless overlay runner used by `run-overlay`.
- Add overlay client that connects to the bridge, authenticates, and listens for protocol events.
- Add a state store keyed by `utterance_id` and `channel`.
- Add a renderer abstraction so OpenVR-specific rendering stays behind one boundary.
- Implement clean shutdown on `shutdown` event or connection loss.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/app/test_headless_overlay.py tests/overlay/test_overlay_state.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/app/headless_overlay.py src/puripuly_heart/overlay/__init__.py src/puripuly_heart/overlay/client.py src/puripuly_heart/overlay/state.py src/puripuly_heart/overlay/renderer.py tests/app/test_headless_overlay.py tests/overlay/test_overlay_state.py
git commit -m "Make the overlay a separate runtime that can fail safely"
```

### Task 9: Connect GUI Toggle and Peer Audio Controls to Runtime Lifecycle

**Files:**
- Modify: `src/puripuly_heart/ui/controller.py`
- Modify: `src/puripuly_heart/ui/app.py`
- Modify: `src/puripuly_heart/ui/views/settings.py`
- Modify: `src/puripuly_heart/ui/event_bridge.py`
- Modify: `tests/ui/test_controller_branch_paths.py`
- Modify: `tests/ui/test_app_branches.py`
- Modify: `tests/ui/test_settings_view_branches.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_overlay_toggle_starts_and_stops_overlay_and_peer_audio_without_interrupting_chatbox():
    ...


def test_settings_view_persists_overlay_toggle_and_peer_audio_fields():
    ...
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_settings_view_branches.py -q`

Expected: FAIL because the GUI does not manage overlay lifecycle or desktop peer-audio lifecycle together.

**Step 3: Write the minimal implementation**

- Add async-safe controller methods to enable and disable overlay.
- Start and stop the overlay bridge/process and peer desktop pipeline together where required by UX.
- Use `page.run_task` or equivalent UI-safe async hooks where needed.
- Persist the overlay toggle and peer-audio settings through existing settings flows.
- Surface overlay and peer-path degraded state in existing logs/UI without blocking the main translation controls.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_settings_view_branches.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/ui/controller.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/ui/event_bridge.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_settings_view_branches.py
git commit -m "Keep overlay control simple while the peer path stays configurable"
```

### Task 10: Package, Document, and Verify the Feature

**Files:**
- Modify: `pyproject.toml`
- Modify: `build.spec`
- Modify: `installer.iss`
- Modify: `README.md`
- Modify: `README.ja.md`
- Modify: `README.ko.md`
- Modify: `README.zh-CN.md`
- Create: `agents/logs/20260309-vr-overlay-streaming-translation.md`

**Step 1: Write the failing checks**

```bash
python -m pytest tests/core/test_desktop_audio_source.py tests/core/test_desktop_enhancement.py tests/core/test_desktop_audio_pipeline.py tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py tests/app/test_overlay_process_manager.py tests/app/test_headless_overlay.py tests/providers/test_gemini_provider.py tests/providers/test_qwen_provider.py -q
python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_settings_view_branches.py tests/app/test_main_cli.py tests/app/test_headless_mic_runner.py -q
```

Expected: At least one packaging or documentation gap remains until runtime wiring, enhancement dependency inclusion, and docs are aligned.

**Step 2: Update packaging and docs**

- Ensure overlay runtime and enhancement dependencies are included.
- Ensure packaged app can launch `run-overlay`.
- Ensure the packaged app can initialize the desktop loopback + enhancement path.
- Document `Overlay: Off / On`, chatbox self-only behavior, peer desktop-audio preprocessing, and current limitations.
- Add verification evidence under `agents/logs/`.

**Step 3: Run verification**

Run:

```bash
python -m pytest tests/core/test_desktop_audio_source.py tests/core/test_desktop_enhancement.py tests/core/test_desktop_audio_pipeline.py tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py tests/app/test_overlay_process_manager.py tests/app/test_headless_overlay.py tests/providers/test_gemini_provider.py tests/providers/test_qwen_provider.py -q
python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_settings_view_branches.py tests/app/test_main_cli.py tests/app/test_headless_mic_runner.py -q
python -m pytest tests/core/test_orchestrator_pipeline.py tests/core/test_hub_low_latency.py tests/ui/test_event_bridge.py tests/core/test_peer_channel_routing.py -q
```

Expected: PASS.

**Step 4: Run build/installer validation if dependencies changed**

Run the project build flow from the project `.venv` and confirm the packaged app can spawn the overlay runtime and initialize the desktop loopback enhancement path.

Expected: PASS, or explicitly document any environment skip in `agents/logs/20260309-vr-overlay-streaming-translation.md`.

**Step 5: Commit**

```bash
git add pyproject.toml build.spec installer.iss README.md README.ja.md README.ko.md README.zh-CN.md agents/logs/20260309-vr-overlay-streaming-translation.md
git commit -m "Document the overlay mode as a full peer-listening path"
```
