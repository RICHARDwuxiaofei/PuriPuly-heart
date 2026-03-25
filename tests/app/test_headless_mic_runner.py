from __future__ import annotations

import logging

import pytest

import puripuly_heart.app.headless_mic as headless_mic
from puripuly_heart.config.settings import AppSettings


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
        raise KeyboardInterrupt

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
        raise KeyboardInterrupt

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
        raise KeyboardInterrupt

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
