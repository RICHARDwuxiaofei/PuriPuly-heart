from __future__ import annotations

import pytest

import puripuly_heart.app.headless_mic as headless_mic
from puripuly_heart.config.settings import AppSettings


@pytest.mark.asyncio
async def test_headless_mic_runner_handles_keyboard_interrupt(monkeypatch, tmp_path) -> None:
    settings = AppSettings()
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
