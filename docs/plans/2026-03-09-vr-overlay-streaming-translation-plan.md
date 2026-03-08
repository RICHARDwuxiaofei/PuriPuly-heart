# VR Overlay Streaming Translation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional VR overlay runtime that shows self/peer final transcripts plus throttled streaming translation updates while keeping VRChat chatbox final-only and always enabled.

**Architecture:** Extend the main app into a multi-sink translation pipeline. The main process keeps STT, LLM, settings, and chatbox delivery, while a separate overlay process connects over localhost WebSocket and renders overlay state from cumulative snapshot-style events. LLM providers gain a streaming surface, but STT remains final-only.

**Tech Stack:** `asyncio`, `websockets`, existing Flet GUI controller/settings flows, existing orchestrator and OSC pipeline, Python subprocess/process management, OpenVR-backed overlay runtime adapter.

---

## Implementation Notes

- Keep `chatbox` behavior unchanged unless a test proves a required adjustment.
- Treat peer final transcripts as a first-class protocol channel, even if the peer audio capture pipeline lands from a parallel design stream.
- Every new user-facing string must go through `src/puripuly_heart/data/i18n/*.json`.
- Keep `to_dict` and `from_dict` synchronized for any new setting.
- Do not block the event loop in overlay lifecycle or LLM streaming code.

### Task 1: Add Overlay Settings and Event Protocol

**Files:**
- Create: `src/puripuly_heart/core/overlay/__init__.py`
- Create: `src/puripuly_heart/core/overlay/protocol.py`
- Create: `tests/config/test_overlay_settings.py`
- Create: `tests/core/test_overlay_protocol.py`
- Modify: `src/puripuly_heart/config/settings.py`
- Modify: `src/puripuly_heart/data/i18n/en.json`
- Modify: `src/puripuly_heart/data/i18n/ko.json`
- Modify: `src/puripuly_heart/data/i18n/zh-CN.json`
- Modify: `src/puripuly_heart/ui/views/settings.py`

**Step 1: Write the failing tests**

```python
def test_overlay_enabled_round_trips_in_settings_dict():
    settings = from_dict({"ui": {"overlay_enabled": True}})
    assert settings.ui.overlay_enabled is True
    assert to_dict(settings)["ui"]["overlay_enabled"] is True


def test_translation_stream_update_serializes_cumulative_text():
    event = TranslationStreamUpdate(text="hello wor", ...)
    payload = event.to_dict()
    assert payload["text"] == "hello wor"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/config/test_overlay_settings.py tests/core/test_overlay_protocol.py -q`

Expected: FAIL because overlay setting and overlay protocol types do not exist yet.

**Step 3: Write the minimal implementation**

- Add `overlay_enabled: bool = False` under `UiSettings`.
- Update `validate`, `to_dict`, and `from_dict`.
- Add overlay i18n keys for the single UI toggle.
- Add typed protocol models for:
  - `OverlayStateSnapshot`
  - `SelfTranscriptFinal`
  - `PeerTranscriptFinal`
  - `TranslationStreamUpdate`
  - `TranslationFinal`
  - `UtteranceClosed`
  - `Shutdown`

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/config/test_overlay_settings.py tests/core/test_overlay_protocol.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/config/settings.py src/puripuly_heart/core/overlay/__init__.py src/puripuly_heart/core/overlay/protocol.py src/puripuly_heart/data/i18n/en.json src/puripuly_heart/data/i18n/ko.json src/puripuly_heart/data/i18n/zh-CN.json src/puripuly_heart/ui/views/settings.py tests/config/test_overlay_settings.py tests/core/test_overlay_protocol.py
git commit -m "feat: add overlay settings and protocol models"
```

### Task 2: Add Streaming LLM Provider Support

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
git commit -m "feat: add streaming llm provider support for overlay updates"
```

### Task 3: Add Overlay Bridge Server and Process Manager

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
git commit -m "feat: add overlay bridge server and process lifecycle management"
```

### Task 4: Wire Overlay Sink Into the Orchestrator

**Files:**
- Create: `src/puripuly_heart/core/overlay/sink.py`
- Create: `tests/core/test_hub_overlay_streaming.py`
- Modify: `src/puripuly_heart/domain/events.py`
- Modify: `src/puripuly_heart/core/orchestrator/hub.py`
- Modify: `src/puripuly_heart/app/headless_mic.py`
- Modify: `tests/core/test_hub_low_latency.py`
- Modify: `tests/core/test_orchestrator_pipeline.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_hub_emits_self_final_and_translation_stream_to_overlay_sink():
    ...


@pytest.mark.asyncio
async def test_chatbox_remains_final_only_while_overlay_receives_stream_updates():
    ...
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_hub_overlay_streaming.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py -q`

Expected: FAIL because the orchestrator has no overlay sink or streaming translation event path.

**Step 3: Write the minimal implementation**

- Add an overlay sink interface that accepts protocol events.
- Publish self final transcript events as soon as final STT arrives.
- Accept peer final transcript events through a channel-aware overlay sink API, even if peer capture is injected later.
- Consume LLM stream updates, throttle them, and emit cumulative `translation_stream_update`.
- Emit `translation_final` for overlay and keep existing chatbox send on final text only.
- Ensure overlay sink failure is logged and isolated.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_hub_overlay_streaming.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/core/overlay/sink.py src/puripuly_heart/domain/events.py src/puripuly_heart/core/orchestrator/hub.py src/puripuly_heart/app/headless_mic.py tests/core/test_hub_overlay_streaming.py tests/core/test_hub_low_latency.py tests/core/test_orchestrator_pipeline.py
git commit -m "feat: stream overlay updates from orchestrator without changing chatbox behavior"
```

### Task 5: Add Overlay Runtime Client and State Store

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
git commit -m "feat: add overlay runtime client and state management"
```

### Task 6: Connect GUI Toggle to Overlay Lifecycle

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
async def test_overlay_toggle_starts_and_stops_process_without_interrupting_chatbox():
    ...


def test_settings_view_persists_overlay_toggle():
    ...
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_settings_view_branches.py -q`

Expected: FAIL because the GUI does not manage overlay lifecycle or persist the toggle.

**Step 3: Write the minimal implementation**

- Add async-safe controller methods to enable and disable overlay.
- Use `page.run_task` or equivalent UI-safe async hooks where needed.
- Persist the overlay toggle through existing settings flows.
- Surface overlay status in existing logs/UI without blocking the main translation controls.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_settings_view_branches.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/puripuly_heart/ui/controller.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/ui/event_bridge.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_settings_view_branches.py
git commit -m "feat: manage overlay lifecycle from gui settings"
```

### Task 7: Package, Document, and Verify the Feature

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
python -m pytest tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py tests/app/test_overlay_process_manager.py tests/app/test_headless_overlay.py tests/providers/test_gemini_provider.py tests/providers/test_qwen_provider.py -q
python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_settings_view_branches.py tests/app/test_main_cli.py -q
```

Expected: At least one packaging or documentation gap remains until runtime wiring and docs are aligned.

**Step 2: Update packaging and docs**

- Ensure overlay runtime dependencies are included.
- Ensure packaged app can launch `run-overlay`.
- Document `Overlay: Off / On`, chatbox always-on behavior, and current limitations.
- Add verification evidence under `agents/logs/`.

**Step 3: Run verification**

Run:

```bash
python -m pytest tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py tests/app/test_overlay_process_manager.py tests/app/test_headless_overlay.py tests/providers/test_gemini_provider.py tests/providers/test_qwen_provider.py -q
python -m pytest tests/ui/test_controller_branch_paths.py tests/ui/test_settings_view_branches.py tests/app/test_main_cli.py -q
python -m pytest tests/core/test_orchestrator_pipeline.py tests/core/test_hub_low_latency.py tests/ui/test_event_bridge.py -q
```

Expected: PASS.

**Step 4: Run build/installer validation if dependencies changed**

Run the project build flow from the project `.venv` and confirm the packaged app can spawn the overlay runtime.

Expected: PASS, or explicitly document any environment skip in `agents/logs/20260309-vr-overlay-streaming-translation.md`.

**Step 5: Commit**

```bash
git add pyproject.toml build.spec installer.iss README.md README.ja.md README.ko.md README.zh-CN.md agents/logs/20260309-vr-overlay-streaming-translation.md
git commit -m "docs: document and package overlay streaming translation mode"
```

## Dependency Note

Peer transcript production depends on the desktop/loopback STT pipeline described in:

- `docs/plans/2026-03-08-vrchat-desktop-audio-speech-enhancement-design.md`

This plan still implements peer-aware protocol and overlay state handling now so that the peer producer can plug in without redesigning the overlay channel later.
