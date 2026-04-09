from __future__ import annotations

import logging

import pytest

import puripuly_heart.app.headless_mic as headless_mic
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterSettings,
    ProviderSettings,
    STTProviderName,
)
from puripuly_heart.core.storage.secrets import InMemorySecretStore


@pytest.mark.asyncio
async def test_headless_mic_runner_handles_keyboard_interrupt(monkeypatch, tmp_path) -> None:
    settings = AppSettings()
    settings.osc.vrc_mic_intercept = False
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    sender_ref: dict[str, object] = {}

    class FakeSender:
        def __init__(self, *args, **kwargs):
            sender_ref["instance"] = self
            self.closed = False

        def close(self):
            self.closed = True

    class FakeHub:
        def __init__(self, *args, **kwargs):
            return None

        async def start(self, *args, **kwargs):
            return None

        async def stop(self):
            return None

    class FakeSource:
        async def close(self):
            return None

    async def fake_run_audio_vad_loop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(headless_mic, "default_vad_model_path", lambda: vad_path)
    monkeypatch.setattr(headless_mic, "ensure_silero_vad_onnx", lambda target_path: vad_path)
    monkeypatch.setattr(headless_mic, "create_secret_store", lambda *_a, **_k: "secrets")
    monkeypatch.setattr(headless_mic, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(headless_mic, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(headless_mic, "ManagedSTTProvider", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VrchatOscUdpSender", FakeSender)
    monkeypatch.setattr(headless_mic, "SmartOscQueue", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "ClientHub", FakeHub)
    monkeypatch.setattr(headless_mic, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "SoundDeviceAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(headless_mic, "run_audio_vad_loop", fake_run_audio_vad_loop)
    monkeypatch.setattr(headless_mic, "resolve_sounddevice_input_device", lambda *a, **k: None)

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=True,
    )
    result = await runner.run()

    assert result == 0
    assert sender_ref["instance"].closed is True


@pytest.mark.asyncio
async def test_headless_mic_runner_rejects_managed_openrouter_without_release_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.MANAGED),
    )
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(
        headless_mic,
        "create_secret_store",
        lambda *_a, **_k: InMemorySecretStore(),
    )
    monkeypatch.setattr(
        headless_mic,
        "create_stt_backend",
        lambda *_a, **_k: pytest.fail("STT backend should not initialize"),
    )

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=True,
    )

    with pytest.raises(
        headless_mic.HeadlessMicInitializationError, match="managed release service"
    ):
        await runner.run()


@pytest.mark.asyncio
async def test_headless_mic_runner_starts_and_stops_vrc_receiver_when_enabled(
    monkeypatch, tmp_path
) -> None:
    settings = AppSettings()
    settings.osc.vrc_mic_intercept = True
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    receiver_events: list[str] = []
    run_kwargs: dict[str, object] = {}

    class FakeReceiver:
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)

        async def start(self):
            receiver_events.append("start")

        def stop(self):
            receiver_events.append("stop")

    class FakeSender:
        def close(self):
            return None

    class FakeHub:
        def __init__(self, *args, **kwargs):
            return None

        async def start(self, *args, **kwargs):
            return None

        async def stop(self):
            return None

    class FakeSource:
        async def close(self):
            return None

    async def fake_run_audio_vad_loop(*_args, **_kwargs):
        run_kwargs.update(_kwargs)
        return None

    monkeypatch.setattr(headless_mic, "default_vad_model_path", lambda: vad_path)
    monkeypatch.setattr(headless_mic, "ensure_silero_vad_onnx", lambda target_path: vad_path)
    monkeypatch.setattr(headless_mic, "create_secret_store", lambda *_a, **_k: "secrets")
    monkeypatch.setattr(headless_mic, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(headless_mic, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(headless_mic, "ManagedSTTProvider", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VrchatOscUdpSender", lambda *a, **k: FakeSender())
    monkeypatch.setattr(headless_mic, "SmartOscQueue", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "ClientHub", FakeHub)
    monkeypatch.setattr(headless_mic, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "SoundDeviceAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(headless_mic, "run_audio_vad_loop", fake_run_audio_vad_loop)
    monkeypatch.setattr(headless_mic, "resolve_sounddevice_input_device", lambda *a, **k: None)
    monkeypatch.setattr(headless_mic, "VrcOscReceiver", FakeReceiver)

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=True,
    )
    result = await runner.run()

    assert result == 0
    assert receiver_events == ["start", "stop"]
    assert run_kwargs["audio_gate"] is not None


@pytest.mark.asyncio
async def test_headless_mic_runner_continues_when_vrc_receiver_start_raises_oserror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = AppSettings()
    settings.osc.vrc_mic_intercept = True
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    receiver_events: list[str] = []
    run_kwargs: dict[str, object] = {}

    class FakeReceiver:
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)

        async def start(self):
            receiver_events.append("start")
            raise OSError("busy")

        def stop(self):
            receiver_events.append("stop")

    class FakeSender:
        def close(self):
            return None

    class FakeHub:
        def __init__(self, *args, **kwargs):
            return None

        async def start(self, *args, **kwargs):
            return None

        async def stop(self):
            return None

    class FakeSource:
        async def close(self):
            return None

    async def fake_run_audio_vad_loop(*_args, **_kwargs):
        run_kwargs.update(_kwargs)
        return None

    monkeypatch.setattr(headless_mic, "default_vad_model_path", lambda: vad_path)
    monkeypatch.setattr(headless_mic, "ensure_silero_vad_onnx", lambda target_path: vad_path)
    monkeypatch.setattr(headless_mic, "create_secret_store", lambda *_a, **_k: "secrets")
    monkeypatch.setattr(headless_mic, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(headless_mic, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(headless_mic, "ManagedSTTProvider", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VrchatOscUdpSender", lambda *a, **k: FakeSender())
    monkeypatch.setattr(headless_mic, "SmartOscQueue", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "ClientHub", FakeHub)
    monkeypatch.setattr(headless_mic, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "SoundDeviceAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(headless_mic, "run_audio_vad_loop", fake_run_audio_vad_loop)
    monkeypatch.setattr(headless_mic, "resolve_sounddevice_input_device", lambda *a, **k: None)
    monkeypatch.setattr(headless_mic, "VrcOscReceiver", FakeReceiver)

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=True,
    )

    with caplog.at_level(logging.WARNING, logger="puripuly_heart.app.headless_mic"):
        result = await runner.run()

    gate = run_kwargs["audio_gate"]
    assert result == 0
    assert receiver_events == ["start"]
    assert gate.enabled is True
    assert gate.receiver_active is False
    assert any("VRChat mic sync receiver unavailable" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_headless_mic_runner_starts_peer_desktop_loop_when_peer_translation_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    settings = AppSettings()
    settings.ui.peer_translation_enabled = True
    settings.ui.integrated_context_enabled = True
    settings.desktop_audio.output_device = "Headphones (Loopback)"
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    created_hub: dict[str, object] = {}
    run_calls: list[dict[str, object]] = []

    class FakeSender:
        def close(self):
            return None

    class FakeHub:
        def __init__(self, *args, **kwargs):
            created_hub.update(kwargs)
            self.peer_stt = kwargs.get("peer_stt")

        async def start(self, *args, **kwargs):
            return None

        async def stop(self):
            return None

    class FakeSource:
        async def close(self):
            return None

    class FakeDesktopSource(FakeSource):
        pass

    async def fake_run_audio_vad_loop(*_args, **kwargs):
        run_calls.append(kwargs)
        return None

    monkeypatch.setattr(headless_mic, "default_vad_model_path", lambda: vad_path)
    monkeypatch.setattr(headless_mic, "ensure_silero_vad_onnx", lambda target_path: vad_path)
    monkeypatch.setattr(headless_mic, "create_secret_store", lambda *_a, **_k: "secrets")
    monkeypatch.setattr(headless_mic, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(headless_mic, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(headless_mic, "create_peer_stt_backend", lambda *_a, **_k: "peer-backend")
    monkeypatch.setattr(headless_mic, "ManagedSTTProvider", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VrchatOscUdpSender", lambda *a, **k: FakeSender())
    monkeypatch.setattr(headless_mic, "SmartOscQueue", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "ClientHub", FakeHub)
    monkeypatch.setattr(headless_mic, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "SoundDeviceAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(
        headless_mic,
        "DesktopLoopbackAudioSource",
        lambda *a, **k: FakeDesktopSource(),
    )
    monkeypatch.setattr(
        headless_mic,
        "DesktopPeerPipeline",
        lambda *a, **k: FakeDesktopSource(),
    )
    monkeypatch.setattr(headless_mic, "run_audio_vad_loop", fake_run_audio_vad_loop)
    monkeypatch.setattr(headless_mic, "resolve_sounddevice_input_device", lambda *a, **k: None)

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=True,
    )
    result = await runner.run()

    assert result == 0
    assert created_hub["peer_stt"] is not None
    assert created_hub["peer_translation_enabled"] is True
    assert created_hub["integrated_context_enabled"] is True
    assert len(run_calls) == 2
    assert {call["sink"].channel for call in run_calls} == {"self", "peer"}


@pytest.mark.asyncio
async def test_headless_mic_runner_isolates_peer_loop_runtime_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = AppSettings()
    settings.ui.peer_translation_enabled = True
    settings.desktop_audio.output_device = "Headphones (Loopback)"
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    class FakeSender:
        def close(self):
            return None

    class FakeHub:
        def __init__(self, *args, **kwargs):
            self.peer_stt = kwargs.get("peer_stt")

        async def start(self, *args, **kwargs):
            return None

        async def stop(self):
            return None

    class FakeSource:
        async def close(self):
            return None

    class FakeDesktopSource(FakeSource):
        pass

    async def fake_run_audio_vad_loop(*_args, **kwargs):
        if kwargs["sink"].channel == "peer":
            raise RuntimeError("peer loop boom")
        return None

    monkeypatch.setattr(headless_mic, "default_vad_model_path", lambda: vad_path)
    monkeypatch.setattr(headless_mic, "ensure_silero_vad_onnx", lambda target_path: vad_path)
    monkeypatch.setattr(headless_mic, "create_secret_store", lambda *_a, **_k: "secrets")
    monkeypatch.setattr(headless_mic, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(headless_mic, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(headless_mic, "create_peer_stt_backend", lambda *_a, **_k: "peer-backend")
    monkeypatch.setattr(headless_mic, "ManagedSTTProvider", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VrchatOscUdpSender", lambda *a, **k: FakeSender())
    monkeypatch.setattr(headless_mic, "SmartOscQueue", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "ClientHub", FakeHub)
    monkeypatch.setattr(headless_mic, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "SoundDeviceAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(
        headless_mic,
        "DesktopLoopbackAudioSource",
        lambda *a, **k: FakeDesktopSource(),
    )
    monkeypatch.setattr(
        headless_mic,
        "DesktopPeerPipeline",
        lambda *a, **k: FakeDesktopSource(),
    )
    monkeypatch.setattr(headless_mic, "run_audio_vad_loop", fake_run_audio_vad_loop)
    monkeypatch.setattr(headless_mic, "resolve_sounddevice_input_device", lambda *a, **k: None)

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=True,
    )

    with caplog.at_level(logging.ERROR, logger="puripuly_heart.app.headless_mic"):
        result = await runner.run()

    assert result == 0
    assert any("Peer desktop loop failed" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_headless_mic_runner_uses_shared_peer_vad_policy_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    settings = AppSettings()
    settings.ui.peer_translation_enabled = True
    settings.desktop_audio.output_device = "Headphones (Loopback)"
    settings.desktop_audio.vad_speech_threshold = 0.72
    settings.desktop_audio.vad_hangover_ms = 950
    settings.desktop_audio.vad_pre_roll_ms = 420
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    helper_calls: list[dict[str, object]] = []
    engine = object()

    class FakeSender:
        def close(self):
            return None

    class FakeHub:
        def __init__(self, *args, **kwargs):
            self.peer_stt = kwargs.get("peer_stt")

        async def start(self, *args, **kwargs):
            return None

        async def stop(self):
            return None

    class FakeSource:
        async def close(self):
            return None

    class FakeDesktopSource(FakeSource):
        pass

    async def fake_run_audio_vad_loop(*_args, **_kwargs):
        return None

    def fake_create_peer_vad_gating(
        *, engine, sample_rate_hz, ring_buffer_ms, speech_threshold, hangover_ms
    ):
        helper_calls.append(
            {
                "engine": engine,
                "sample_rate_hz": sample_rate_hz,
                "ring_buffer_ms": ring_buffer_ms,
                "speech_threshold": speech_threshold,
                "hangover_ms": hangover_ms,
            }
        )
        return object()

    monkeypatch.setattr(headless_mic, "default_vad_model_path", lambda: vad_path)
    monkeypatch.setattr(headless_mic, "ensure_silero_vad_onnx", lambda target_path: vad_path)
    monkeypatch.setattr(headless_mic, "create_secret_store", lambda *_a, **_k: "secrets")
    monkeypatch.setattr(headless_mic, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(headless_mic, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(headless_mic, "create_peer_stt_backend", lambda *_a, **_k: "peer-backend")
    monkeypatch.setattr(headless_mic, "ManagedSTTProvider", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VrchatOscUdpSender", lambda *a, **k: FakeSender())
    monkeypatch.setattr(headless_mic, "SmartOscQueue", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "ClientHub", FakeHub)
    monkeypatch.setattr(headless_mic, "SileroVadOnnx", lambda *a, **k: engine)
    monkeypatch.setattr(headless_mic, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "create_peer_vad_gating", fake_create_peer_vad_gating)
    monkeypatch.setattr(headless_mic, "SoundDeviceAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(
        headless_mic,
        "DesktopLoopbackAudioSource",
        lambda *a, **k: FakeDesktopSource(),
    )
    monkeypatch.setattr(
        headless_mic,
        "DesktopPeerPipeline",
        lambda *a, **k: FakeDesktopSource(),
    )
    monkeypatch.setattr(headless_mic, "run_audio_vad_loop", fake_run_audio_vad_loop)
    monkeypatch.setattr(headless_mic, "resolve_sounddevice_input_device", lambda *a, **k: None)

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=True,
    )
    result = await runner.run()

    assert result == 0
    assert helper_calls == [
        {
            "engine": engine,
            "sample_rate_hz": 16000,
            "ring_buffer_ms": 420,
            "speech_threshold": 0.72,
            "hangover_ms": 950,
        }
    ]


@pytest.mark.asyncio
async def test_headless_runner_uses_selected_peer_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    settings = AppSettings()
    settings.ui.peer_translation_enabled = True
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.peer_soniox_stt.model = "peer-soniox"
    settings.peer_soniox_stt.endpoint = "wss://peer-soniox.example/realtime"
    settings.peer_soniox_stt.keepalive_interval_s = 8.0
    settings.peer_soniox_stt.trailing_silence_ms = 300
    calls: list[AppSettings] = []
    created_hub: dict[str, object] = {}
    peer_backend = object()
    config_path = tmp_path / "settings.json"
    vad_path = tmp_path / "vad.onnx"
    vad_path.write_text("dummy", encoding="utf-8")

    class FakeManagedSTTProvider:
        def __init__(self, *, backend, sample_rate_hz, channel=None, **kwargs):
            self.backend = backend
            self.sample_rate_hz = sample_rate_hz
            self.channel = channel
            self.kwargs = kwargs

    def fake_create_peer_stt_backend(settings: AppSettings, *, secrets):
        _ = secrets
        calls.append(settings)
        return peer_backend

    class FakeHub:
        def __init__(self, *args, **kwargs):
            created_hub.update(kwargs)
            self.peer_stt = kwargs.get("peer_stt")

        async def start(self, *args, **kwargs):
            return None

        async def stop(self):
            return None

    class FakeSender:
        def close(self):
            return None

    class FakeSource:
        async def close(self):
            return None

    async def fake_run_audio_vad_loop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(headless_mic, "create_peer_stt_backend", fake_create_peer_stt_backend)
    monkeypatch.setattr(headless_mic, "create_secret_store", lambda *_a, **_k: "secrets")
    monkeypatch.setattr(headless_mic, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(headless_mic, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(headless_mic, "ManagedSTTProvider", FakeManagedSTTProvider)
    monkeypatch.setattr(headless_mic, "VrchatOscUdpSender", lambda *a, **k: FakeSender())
    monkeypatch.setattr(headless_mic, "ClientHub", FakeHub)
    monkeypatch.setattr(headless_mic, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(headless_mic, "SoundDeviceAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(headless_mic, "DesktopLoopbackAudioSource", lambda *a, **k: FakeSource())
    monkeypatch.setattr(headless_mic, "DesktopPeerPipeline", lambda *a, **k: FakeSource())
    monkeypatch.setattr(headless_mic, "run_audio_vad_loop", fake_run_audio_vad_loop)
    monkeypatch.setattr(headless_mic, "resolve_sounddevice_input_device", lambda *a, **k: None)

    runner = headless_mic.HeadlessMicRunner(
        settings=settings,
        config_path=config_path,
        vad_model_path=vad_path,
        use_llm=False,
    )

    result = await runner.run()

    assert result == 0
    assert len(calls) == 1
    peer_settings = calls[0]
    assert peer_settings.provider.peer_stt == STTProviderName.SONIOX
    assert peer_settings.peer_soniox_stt.model == "peer-soniox"
    assert peer_settings.peer_soniox_stt.endpoint == "wss://peer-soniox.example/realtime"
    assert peer_settings.peer_soniox_stt.keepalive_interval_s == 8.0
    assert peer_settings.peer_soniox_stt.trailing_silence_ms == 300
    assert isinstance(created_hub["peer_stt"], FakeManagedSTTProvider)
    assert created_hub["peer_stt"].backend is peer_backend
    assert created_hub["peer_stt"].channel == "peer"
