from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.osc.receiver import VrcMicState
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider
from puripuly_heart.providers.stt.deepgram import DeepgramRealtimeSTTBackend
from puripuly_heart.providers.stt.soniox import SonioxRealtimeSTTBackend
from puripuly_heart.ui import controller as controller_module
from puripuly_heart.ui.controller import GuiController


class DummySecrets:
    def __init__(self, values: dict[str, str]):
        self._values = dict(values)
        self.set_calls: list[tuple[str, str]] = []

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self._values[key] = value


class DummyDashboard:
    def __init__(self) -> None:
        self.translation_needs_key: bool | None = None
        self.translation_enabled: bool | None = None
        self.stt_needs_key: bool | None = None
        self.stt_enabled: bool | None = None
        self.languages: tuple[str, str] | None = None
        self.recent_languages: tuple[list[str], list[str]] | None = None
        self.is_translation_on: bool = True
        self.on_recent_languages_change = None

    def set_translation_needs_key(self, value: bool) -> None:
        self.translation_needs_key = value

    def set_translation_enabled(self, value: bool) -> None:
        self.translation_enabled = value

    def set_stt_needs_key(self, value: bool) -> None:
        self.stt_needs_key = value

    def set_stt_enabled(self, value: bool) -> None:
        self.stt_enabled = value

    def set_languages_from_codes(self, source: str, target: str) -> None:
        self.languages = (source, target)

    def set_recent_languages(self, source: list[str], target: list[str]) -> None:
        self.recent_languages = (source, target)


class DummySettingsView:
    def __init__(self) -> None:
        self.calls: list[tuple[AppSettings, Path]] = []

    def load_from_settings(self, settings: AppSettings, *, config_path: Path) -> None:
        self.calls.append((settings, config_path))


class DummyLogsView:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.attach_calls = 0

    def append_log(self, message: str) -> None:
        self.logs.append(message)

    def attach_log_handler(self) -> None:
        self.attach_calls += 1


class DummyHub:
    def __init__(self, *, llm: object | None = object(), stt: object | None = object()) -> None:
        self.llm = llm
        self.stt = stt
        self.translation_enabled = True
        self.clear_context_calls = 0
        self.promo_calls = 0
        self.start_calls: list[bool] = []
        self.stop_calls = 0
        self.submit_calls: list[tuple[str, str]] = []
        self.ui_events: asyncio.Queue[object] = asyncio.Queue()

    def clear_context(self) -> None:
        self.clear_context_calls += 1

    def mark_promo_eligible(self) -> None:
        self.promo_calls += 1

    async def start(self, *, auto_flush_osc: bool) -> None:
        self.start_calls.append(auto_flush_osc)

    async def stop(self) -> None:
        self.stop_calls += 1

    async def submit_text(self, text: str, *, source: str) -> None:
        self.submit_calls.append((text, source))


class DummyGate:
    def __init__(self) -> None:
        self.state = None
        self.enabled_calls: list[bool] = []
        self.receiver_active_calls: list[bool] = []
        self.reset_calls = 0

    def set_enabled(self, enabled: bool) -> None:
        self.enabled_calls.append(enabled)

    def set_receiver_active(self, active: bool) -> None:
        self.receiver_active_calls.append(active)

    def reset(self) -> None:
        self.reset_calls += 1


def _make_controller(*, app: object) -> GuiController:
    return GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))


def _patch_init_pipeline_dependencies(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    created: dict[str, object] = {}

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(controller_module, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(controller_module, "ManagedSTTProvider", lambda *a, **k: "stt")

    class FakeSender:
        def close(self) -> None:
            return None

    def fake_sender(*_args, **_kwargs):
        sender = FakeSender()
        created["sender"] = sender
        return sender

    def fake_osc(*_args, **_kwargs):
        osc = object()
        created["osc"] = osc
        return osc

    def fake_hub(*_args, **kwargs):
        hub = SimpleNamespace(llm=kwargs.get("llm"), stt=kwargs.get("stt"))
        created["hub"] = hub
        return hub

    monkeypatch.setattr(controller_module, "VrchatOscUdpSender", fake_sender)
    monkeypatch.setattr(controller_module, "SmartOscQueue", fake_osc)
    monkeypatch.setattr(controller_module, "ClientHub", fake_hub)

    return created


@pytest.mark.asyncio
async def test_verify_and_update_status_handles_mixed_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.provider.stt = STTProviderName.QWEN_ASR
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_FLASH

    dash = DummyDashboard()
    app = SimpleNamespace(view_dashboard=dash)
    controller = _make_controller(app=app)
    controller.settings = settings
    controller.hub = DummyHub(llm=object(), stt=object())

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"alibaba_api_key": "secret"}),
    )

    models_seen: list[str] = []

    async def fake_verify_qwen(
        self: GuiController,
        api_key: str,
        *,
        base_url: str,
        model: str | None = None,
    ) -> bool:
        _ = (self, api_key, base_url)
        assert model is not None
        models_seen.append(model)
        return model == QwenLLMModel.QWEN_35_PLUS.value

    monkeypatch.setattr(GuiController, "_verify_qwen_llm_api_key", fake_verify_qwen)

    await controller._verify_and_update_status()

    assert models_seen == ["qwen3.5-flash", "qwen3.5-plus"]
    assert dash.translation_needs_key is True
    assert dash.translation_enabled is False
    assert dash.stt_needs_key is False
    assert controller.hub.translation_enabled is False


@pytest.mark.asyncio
async def test_verify_and_update_status_marks_needs_key_when_secret_store_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.provider.stt = STTProviderName.DEEPGRAM

    dash = DummyDashboard()
    app = SimpleNamespace(view_dashboard=dash)
    controller = _make_controller(app=app)
    controller.settings = settings
    controller.hub = DummyHub(llm=object(), stt=object())

    def raise_secret_store(*_args, **_kwargs):
        raise RuntimeError("secret store broken")

    async def always_false(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(controller_module, "create_secret_store", raise_secret_store)
    monkeypatch.setattr(GeminiLLMProvider, "verify_api_key", staticmethod(always_false))
    monkeypatch.setattr(DeepgramRealtimeSTTBackend, "verify_api_key", staticmethod(always_false))

    await controller._verify_and_update_status()

    assert dash.translation_needs_key is True
    assert dash.translation_enabled is False
    assert dash.stt_needs_key is True
    assert dash.stt_enabled is False


def test_get_qwen_key_and_base_url_migrates_legacy_secret() -> None:
    settings = AppSettings()
    settings.qwen.region = QwenRegion.SINGAPORE
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings

    secrets = DummySecrets({"alibaba_api_key": "legacy"})
    key, base_url = controller._get_qwen_key_and_base_url(secrets)

    assert key == "legacy"
    assert base_url == settings.qwen.get_llm_base_url()
    assert ("alibaba_api_key_singapore", "legacy") in secrets.set_calls


@pytest.mark.parametrize(
    ("result_map", "expected"),
    [
        ({"qwen3.5-flash": True}, (True, "Verification successful")),
        (
            {"qwen3.5-flash": False, "qwen3.5-plus": True},
            (False, "qwen_model_unavailable:qwen3.5-flash"),
        ),
        (
            {"qwen3.5-flash": False, "qwen3.5-plus": False},
            (False, "Verification failed (check logs/console for details)"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_verify_qwen_key_with_model_fallback_paths(
    monkeypatch: pytest.MonkeyPatch,
    result_map: dict[str, bool],
    expected: tuple[bool, str],
) -> None:
    settings = AppSettings()
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_FLASH
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings

    async def fake_verify_qwen(
        self: GuiController,
        api_key: str,
        *,
        base_url: str,
        model: str | None = None,
    ) -> bool:
        _ = (self, api_key, base_url)
        assert model is not None
        return result_map.get(model, False)

    monkeypatch.setattr(GuiController, "_verify_qwen_llm_api_key", fake_verify_qwen)

    result = await controller._verify_qwen_key_with_model_fallback(
        "secret",
        base_url="https://dashscope.aliyuncs.com/api/v1",
    )
    assert result == expected


@pytest.mark.asyncio
async def test_verify_api_key_handles_empty_unknown_and_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs = DummyLogsView()
    controller = _make_controller(app=SimpleNamespace(view_logs=logs))
    controller.settings = AppSettings()

    empty = await controller.verify_api_key("google", "")
    unknown = await controller.verify_api_key("mystery", "x")

    async def raise_error(*_args, **_kwargs) -> bool:
        raise RuntimeError("bad key")

    monkeypatch.setattr(GeminiLLMProvider, "verify_api_key", staticmethod(raise_error))
    errored = await controller.verify_api_key("google", "x")

    assert empty == (False, "API Key is empty")
    assert unknown == (False, "Unknown provider: mystery")
    assert errored == (False, "bad key")
    assert any("bad key" in line for line in logs.logs)


def test_sync_ui_from_settings_updates_dashboard_and_settings_view() -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.languages.target_language = "en"
    settings.languages.recent_source_languages = ["ko", "ja"]
    settings.languages.recent_target_languages = ["en", "zh"]

    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dash, view_settings=settings_view)
    )
    controller.settings = settings

    controller._sync_ui_from_settings()

    assert dash.languages == ("ko", "en")
    assert dash.recent_languages == (["ko", "ja"], ["en", "zh"])
    assert dash.on_recent_languages_change is not None
    assert settings_view.calls == [(settings, Path("settings.json"))]


def test_on_recent_languages_change_persists_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    saves: list[tuple[Path, AppSettings]] = []

    def fake_save(path: Path, incoming: AppSettings) -> None:
        saves.append((path, incoming))

    monkeypatch.setattr(controller_module, "save_settings", fake_save)
    controller._on_recent_languages_change(["ko", "fr"], ["en", "ja"])

    assert settings.languages.recent_source_languages == ["ko", "fr"]
    assert settings.languages.recent_target_languages == ["en", "ja"]
    assert saves == [(Path("settings.json"), settings)]


@pytest.mark.asyncio
async def test_set_translation_enabled_disables_when_llm_missing() -> None:
    logs = DummyLogsView()
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash, view_logs=logs))
    controller.settings = AppSettings()
    controller.hub = DummyHub(llm=None)

    await controller.set_translation_enabled(True)

    assert controller.hub.translation_enabled is False
    assert controller.hub.clear_context_calls == 0
    assert dash.translation_enabled is False
    assert any("Translation is ON" in line for line in logs.logs)


@pytest.mark.asyncio
async def test_set_translation_enabled_warms_supported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.QWEN
    qwen_provider = QwenLLMProvider(api_key="secret")
    controller.hub = DummyHub(
        llm=SemaphoreLLMProvider(inner=qwen_provider, semaphore=asyncio.Semaphore(1))
    )
    called: list[tuple[str, str, str]] = []

    async def fake_verify(
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/api/v1",
        model: str = "qwen3.5-plus",
    ) -> bool:
        called.append((api_key, base_url, model))
        return True

    monkeypatch.setattr(QwenLLMProvider, "verify_api_key", staticmethod(fake_verify))

    await controller.set_translation_enabled(True)

    assert controller.hub.translation_enabled is True
    assert controller.hub.clear_context_calls == 1
    assert called == [("secret", "https://dashscope.aliyuncs.com/api/v1", "qwen3.5-plus")]


def test_verified_key_and_runtime_signature_depend_on_region_and_settings() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    controller.settings = settings

    settings.qwen.region = QwenRegion.BEIJING
    key_beijing = controller._get_alibaba_verified_key()
    settings.qwen.region = QwenRegion.SINGAPORE
    key_singapore = controller._get_alibaba_verified_key()

    baseline = controller._build_stt_runtime_signature(settings)
    settings.audio.input_device = "Microphone 2"
    changed = controller._build_stt_runtime_signature(settings)

    assert key_beijing == "alibaba_beijing"
    assert key_singapore == "alibaba_singapore"
    assert baseline != changed


@pytest.mark.asyncio
async def test_stop_disables_vrc_receiver_before_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    controller = _make_controller(app=SimpleNamespace())

    async def fake_set_stt_enabled(self, enabled: bool) -> None:
        _ = self
        events.append(("stt", enabled))

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = self
        events.append(("receiver", enabled))

    class FakeHub:
        async def stop(self) -> None:
            events.append(("hub_stop", None))

    class FakeSender:
        def close(self) -> None:
            events.append(("sender_close", None))

    monkeypatch.setattr(GuiController, "set_stt_enabled", fake_set_stt_enabled)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    controller.hub = FakeHub()
    controller.sender = FakeSender()
    controller._bridge_task = asyncio.create_task(asyncio.sleep(3600))

    await controller.stop()

    assert events[:2] == [("stt", False), ("receiver", False)]
    assert controller.hub is None
    assert controller.sender is None
    assert controller._bridge_task is None


@pytest.mark.asyncio
async def test_apply_settings_updates_vrc_gate_and_reconfigures_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.system_prompt = settings.system_prompt
    controller.hub.low_latency_mode = settings.stt.low_latency_mode
    controller.hub.low_latency_merge_gap_ms = settings.stt.low_latency_merge_gap_ms
    controller.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
    controller._last_stt_runtime_signature = controller._build_stt_runtime_signature(settings)

    gate = DummyGate()
    configure_calls: list[bool] = []
    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = self
        configure_calls.append(enabled)

    controller.vrc_mic_audio_gate = gate
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )

    settings.osc.vrc_mic_intercept = True
    await controller.apply_settings(settings)
    settings.osc.vrc_mic_intercept = False
    await controller.apply_settings(settings)

    assert gate.enabled_calls == [True, False]
    assert configure_calls == [True, False]


@pytest.mark.asyncio
async def test_init_pipeline_initializes_vrc_state_and_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.osc.vrc_mic_intercept = True
    controller.receiver = object()
    configure_calls: list[bool] = []

    _patch_init_pipeline_dependencies(monkeypatch)

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = self
        configure_calls.append(enabled)

    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )

    await controller._init_pipeline()

    assert isinstance(controller.vrc_mic_state, VrcMicState)
    assert isinstance(controller.vrc_mic_audio_gate, VrcMicAudioGate)
    assert controller.vrc_mic_audio_gate.state is controller.vrc_mic_state
    assert controller.vrc_mic_audio_gate.enabled is True
    assert controller.vrc_mic_audio_gate.receiver_active is True
    assert controller.vrc_mic_audio_gate._sync_deadline is not None
    assert configure_calls == [True]


@pytest.mark.asyncio
async def test_init_pipeline_reuses_existing_gate_and_updates_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.osc.vrc_mic_intercept = True
    controller.receiver = object()
    original_state = VrcMicState(muted=False)
    gate = VrcMicAudioGate(state=original_state, enabled=False)

    _patch_init_pipeline_dependencies(monkeypatch)

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = self
        _ = enabled

    controller.vrc_mic_audio_gate = gate
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )

    await controller._init_pipeline()

    assert controller.vrc_mic_audio_gate is gate
    assert controller.vrc_mic_state is not None
    assert gate.state is controller.vrc_mic_state
    assert gate.state is not original_state
    assert gate.enabled is True
    assert gate.receiver_active is True
    assert gate._sync_deadline is not None


@pytest.mark.asyncio
async def test_init_pipeline_configures_receiver_after_pipeline_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.osc.vrc_mic_intercept = True
    created = _patch_init_pipeline_dependencies(monkeypatch)
    snapshots: list[tuple[bool, bool, bool, bool]] = []

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = self
        snapshots.append(
            (
                controller.sender is created["sender"],
                controller.osc is created["osc"],
                controller.hub is created["hub"],
                enabled,
            )
        )

    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )

    await controller._init_pipeline()

    assert snapshots == [(True, True, True, True)]


@pytest.mark.asyncio
async def test_stop_mic_loop_cancels_task_closes_audio_source_and_resets_gate() -> None:
    controller = _make_controller(app=SimpleNamespace())
    task = asyncio.create_task(asyncio.sleep(3600))
    close_calls: list[str] = []
    gate = DummyGate()

    class FakeAudioSource:
        async def close(self) -> None:
            close_calls.append("closed")

    controller._mic_task = task
    controller._audio_source = FakeAudioSource()
    controller._vad = object()
    controller.vrc_mic_audio_gate = gate

    await controller._stop_mic_loop()

    assert task.cancelled() is True
    assert close_calls == ["closed"]
    assert controller._mic_task is None
    assert controller._audio_source is None
    assert controller._vad is None
    assert gate.reset_calls == 1


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_disabled_stops_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    gate = DummyGate()
    stop_calls: list[str] = []

    def fake_stop_vrc_mic_receiver(self) -> None:
        _ = self
        stop_calls.append("stopped")

    controller.vrc_mic_audio_gate = gate
    monkeypatch.setattr(GuiController, "_stop_vrc_mic_receiver", fake_stop_vrc_mic_receiver)

    await controller._configure_vrc_mic_receiver(enabled=False)

    assert gate.enabled_calls == [False]
    assert stop_calls == ["stopped"]


@pytest.mark.parametrize(
    ("receiver", "state", "expected_active"),
    [
        (object(), VrcMicState(), True),
        (None, None, False),
    ],
)
@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_no_state_or_existing_receiver_only_syncs_gate(
    receiver: object | None,
    state: VrcMicState | None,
    expected_active: bool,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    gate = DummyGate()
    controller.receiver = receiver
    controller.vrc_mic_state = state
    controller.vrc_mic_audio_gate = gate

    await controller._configure_vrc_mic_receiver(enabled=True)

    assert gate.enabled_calls == [True]
    assert gate.receiver_active_calls == [expected_active]
    assert controller.receiver is receiver


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_start_failure_logs_and_clears_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.vrc_mic_state = VrcMicState()
    gate = DummyGate()
    errors: list[str] = []

    class FailingReceiver:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def start(self) -> None:
            raise OSError("busy")

    monkeypatch.setattr(controller_module, "VrcOscReceiver", FailingReceiver)
    controller.vrc_mic_audio_gate = gate
    monkeypatch.setattr(GuiController, "_log_error", lambda self, message: errors.append(message))

    await controller._configure_vrc_mic_receiver(enabled=True)

    assert gate.enabled_calls == [True]
    assert gate.receiver_active_calls == [False]
    assert controller.receiver is None
    assert len(errors) == 1
    assert "127.0.0.1:9001" in errors[0]
    assert "busy" in errors[0]


@pytest.mark.asyncio
async def test_configure_vrc_mic_receiver_start_success_stores_receiver_and_resets_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.vrc_mic_state = VrcMicState()
    gate = DummyGate()
    receiver_starts: list[str] = []

    class FakeReceiver:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def start(self) -> None:
            receiver_starts.append("started")

    monkeypatch.setattr(controller_module, "VrcOscReceiver", FakeReceiver)
    controller.vrc_mic_audio_gate = gate

    await controller._configure_vrc_mic_receiver(enabled=True)

    assert receiver_starts == ["started"]
    assert isinstance(controller.receiver, FakeReceiver)
    assert gate.enabled_calls == [True]
    assert gate.receiver_active_calls == [True]
    assert gate.reset_calls == 1


def test_stop_vrc_mic_receiver_stops_receiver_and_marks_gate_inactive() -> None:
    controller = _make_controller(app=SimpleNamespace())
    gate = DummyGate()
    stop_calls: list[str] = []

    class FakeReceiver:
        def stop(self) -> None:
            stop_calls.append("stopped")

    controller.receiver = FakeReceiver()
    controller.vrc_mic_audio_gate = gate

    controller._stop_vrc_mic_receiver()

    assert stop_calls == ["stopped"]
    assert controller.receiver is None
    assert gate.receiver_active_calls == [False]


@pytest.mark.asyncio
async def test_start_initializes_dashboard_and_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.provider.stt = STTProviderName.QWEN_ASR
    settings.qwen.region = QwenRegion.SINGAPORE
    settings.api_key_verified.alibaba_singapore = True

    dash = DummyDashboard()
    logs = DummyLogsView()
    locale_calls: list[str] = []
    sync_calls: list[str] = []
    bridge_events: list[object] = []
    hub = DummyHub(llm=object(), stt=object())

    class FakeBridge:
        def __init__(self, *, app, event_queue) -> None:
            bridge_events.append(("init", app, event_queue))

        async def run(self) -> None:
            bridge_events.append("run")

    async def fake_init_pipeline(self) -> None:
        self.hub = hub

    monkeypatch.setattr(GuiController, "_load_or_init_settings", lambda self, path: settings)
    monkeypatch.setattr(
        GuiController,
        "_sync_ui_from_settings",
        lambda self: sync_calls.append("synced"),
    )
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(controller_module, "set_locale", lambda locale: locale_calls.append(locale))
    monkeypatch.setattr(controller_module, "UIEventBridge", FakeBridge)

    app = SimpleNamespace(
        view_dashboard=dash,
        view_logs=logs,
        apply_locale=lambda: locale_calls.append("apply"),
    )
    controller = _make_controller(app=app)

    await controller.start()
    await asyncio.sleep(0)

    assert controller.settings is settings
    assert sync_calls == ["synced"]
    assert locale_calls == [settings.ui.locale, "apply"]
    assert logs.attach_calls == 1
    assert dash.stt_needs_key is False
    assert dash.translation_needs_key is False
    assert dash.stt_enabled is False
    assert dash.translation_enabled is False
    assert hub.translation_enabled is False
    assert hub.start_calls == [True]
    assert bridge_events[0] == ("init", app, hub.ui_events)
    assert "run" in bridge_events


@pytest.mark.asyncio
async def test_set_translation_enabled_returns_when_hub_missing() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()

    await controller.set_translation_enabled(True)


@pytest.mark.asyncio
async def test_set_translation_enabled_logs_non_qwen_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.GEMINI
    controller.hub = DummyHub(llm=object())

    with caplog.at_level("INFO", logger="puripuly_heart.ui.controller"):
        await controller.set_translation_enabled(True)

    assert controller.hub.translation_enabled is True
    assert controller.hub.clear_context_calls == 1
    assert any("Enabled with provider: gemini" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_set_stt_enabled_marks_promo_and_runs_switch(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.DEEPGRAM
    controller.hub = DummyHub()
    switch_calls: list[bool] = []

    async def fake_ensure_stt_switch(self) -> None:
        switch_calls.append(self._stt_desired)

    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_ensure_stt_switch)

    with caplog.at_level("INFO", logger="puripuly_heart.ui.controller"):
        await controller.set_stt_enabled(True)

    assert controller._stt_desired is True
    assert controller.hub.promo_calls == 1
    assert switch_calls == [True]
    assert any("Enabled with provider: deepgram" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_ensure_stt_switch_creates_task_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    run_calls: list[str] = []

    async def fake_run_stt_switch(self) -> None:
        _ = self
        run_calls.append("run")

    monkeypatch.setattr(GuiController, "_run_stt_switch", fake_run_stt_switch)

    await controller._ensure_stt_switch()

    assert run_calls == ["run"]
    assert controller._stt_switch_task is not None
    assert controller._stt_switch_task.done() is True


@pytest.mark.asyncio
async def test_run_stt_switch_stop_path_closes_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._stt_desired = False
    stop_calls: list[str] = []
    backend_calls: list[str] = []

    class FakeStt:
        async def close(self) -> None:
            backend_calls.append("close")

    async def fake_stop_mic_loop(self) -> None:
        _ = self
        stop_calls.append("stop_mic")

    monkeypatch.setattr(GuiController, "_stop_mic_loop", fake_stop_mic_loop)
    controller.hub = DummyHub(stt=FakeStt())

    await controller._run_stt_switch()

    assert stop_calls == ["stop_mic"]
    assert backend_calls == ["close"]


@pytest.mark.asyncio
async def test_run_stt_switch_warns_when_hub_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._stt_desired = True
    controller.hub = None

    with caplog.at_level("WARNING", logger="puripuly_heart.ui.controller"):
        await controller._run_stt_switch()

    assert any("Enable requested before hub is ready" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_run_stt_switch_restart_path_closes_and_warms_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._stt_desired = True
    controller._stt_restart_requested = True
    calls: list[str] = []

    class FakeStt:
        async def close(self) -> None:
            calls.append("close")

        async def warmup(self) -> None:
            calls.append("warmup")

    async def fake_stop_mic_loop(self) -> None:
        _ = self
        calls.append("stop_mic")

    async def fake_start_mic_loop(self) -> None:
        _ = self
        calls.append("start_mic")

    monkeypatch.setattr(GuiController, "_stop_mic_loop", fake_stop_mic_loop)
    monkeypatch.setattr(GuiController, "_start_mic_loop", fake_start_mic_loop)
    controller.hub = DummyHub(stt=FakeStt())

    await controller._run_stt_switch()

    assert calls == ["stop_mic", "close", "start_mic", "warmup"]


@pytest.mark.asyncio
async def test_submit_text_returns_without_hub_and_logs_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    errors: list[str] = []

    await controller.submit_text("hello")

    class FailingHub:
        async def submit_text(self, text: str, *, source: str) -> None:
            _ = (text, source)
            raise RuntimeError("submit boom")

    monkeypatch.setattr(GuiController, "_log_error", lambda self, message: errors.append(message))
    controller.hub = FailingHub()

    await controller.submit_text("hello")

    assert errors == ["Submit failed: submit boom"]


@pytest.mark.asyncio
async def test_apply_settings_rebuilds_pipeline_when_source_language_changes_and_applies_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.ui.locale = "ja"
    controller = _make_controller(app=SimpleNamespace(apply_locale=lambda: None))
    controller.settings = settings
    controller.hub = SimpleNamespace(source_language="en", low_latency_mode=False)
    saved: list[str] = []
    rebuild_calls: list[bool] = []
    locale_calls: list[str] = []

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        rebuild_calls.append(rebuild_stt)

    monkeypatch.setattr(controller_module, "get_locale", lambda: "en")
    monkeypatch.setattr(controller_module, "set_locale", lambda locale: locale_calls.append(locale))
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: saved.append("saved"))
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_settings(settings)

    assert saved == ["saved"]
    assert rebuild_calls == [True]
    assert locale_calls == ["ja"]


@pytest.mark.asyncio
async def test_apply_settings_restarts_stt_and_reports_locale_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.stt.low_latency_mode = True
    settings.ui.locale = "ko"
    settings.osc.vrc_mic_intercept = True

    errors: list[str] = []
    rebuild_llm_calls: list[str] = []
    receiver_calls: list[bool] = []
    switch_calls: list[str] = []
    locale_calls: list[str] = []

    app = SimpleNamespace(apply_locale=lambda: (_ for _ in ()).throw(RuntimeError("locale boom")))
    controller = _make_controller(app=app)
    controller.settings = settings
    controller.hub = SimpleNamespace(
        source_language=settings.languages.source_language,
        target_language=settings.languages.target_language,
        system_prompt=settings.system_prompt,
        low_latency_mode=False,
        low_latency_merge_gap_ms=settings.stt.low_latency_merge_gap_ms,
        low_latency_spec_retry_max=settings.stt.low_latency_spec_retry_max,
        hangover_s=1.1,
    )
    controller._last_stt_runtime_signature = ("old",)
    controller._mic_task = object()
    controller._stt_desired = True

    async def fake_rebuild_llm_provider(self) -> None:
        rebuild_llm_calls.append("rebuild_llm")

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        receiver_calls.append(enabled)

    async def fake_ensure_stt_switch(self) -> None:
        switch_calls.append("switch")

    monkeypatch.setattr(controller_module, "get_locale", lambda: "en")
    monkeypatch.setattr(controller_module, "set_locale", lambda locale: locale_calls.append(locale))
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_ensure_stt_switch)
    monkeypatch.setattr(GuiController, "_log_error", lambda self, message: errors.append(message))

    await controller.apply_settings(settings)

    assert rebuild_llm_calls == ["rebuild_llm"]
    assert receiver_calls == [True]
    assert controller._stt_restart_requested is True
    assert switch_calls == ["switch"]
    assert locale_calls == ["ko"]
    assert controller.hub.low_latency_mode is True
    assert any("Failed to apply locale: locale boom" in message for message in errors)


@pytest.mark.parametrize(
    ("provider", "result", "expected"),
    [
        ("deepgram", True, (True, "Verification successful")),
        ("deepgram", False, (False, "Verification failed (check logs/console for details)")),
        ("soniox", True, (True, "Verification successful")),
    ],
)
@pytest.mark.asyncio
async def test_verify_api_key_success_and_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    result: bool,
    expected: tuple[bool, str],
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()

    async def fake_verify(_key: str) -> bool:
        return result

    monkeypatch.setattr(DeepgramRealtimeSTTBackend, "verify_api_key", staticmethod(fake_verify))
    monkeypatch.setattr(SonioxRealtimeSTTBackend, "verify_api_key", staticmethod(fake_verify))

    outcome = await controller.verify_api_key(provider, "secret")

    assert outcome == expected


@pytest.mark.asyncio
async def test_verify_api_key_routes_alibaba_singapore_to_qwen_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    calls: list[tuple[str, str]] = []

    async def fake_verify(self, key: str, *, base_url: str) -> tuple[bool, str]:
        _ = self
        calls.append((key, base_url))
        return True, "Verification successful"

    monkeypatch.setattr(GuiController, "_verify_qwen_key_with_model_fallback", fake_verify)

    outcome = await controller.verify_api_key("alibaba_singapore", "secret")

    assert outcome == (True, "Verification successful")
    assert calls == [("secret", "https://dashscope-intl.aliyuncs.com/api/v1")]


@pytest.mark.asyncio
async def test_apply_providers_routes_based_on_rebuild_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    calls: list[str] = []

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)

    await controller.apply_providers()

    controller.settings = AppSettings()
    await controller.apply_providers(rebuild_stt=True)
    await controller.apply_providers(rebuild_stt=False)

    assert calls == ["pipeline:True", "llm"]


def test_load_or_init_settings_loads_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()

    monkeypatch.setattr(controller_module, "load_settings", lambda incoming: settings)

    loaded = controller._load_or_init_settings(path)

    assert loaded is settings


def test_load_or_init_settings_creates_default_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "nested" / "settings.json"
    controller = _make_controller(app=SimpleNamespace())
    saves: list[tuple[Path, AppSettings]] = []

    def fake_save(incoming_path: Path, incoming_settings: AppSettings) -> None:
        saves.append((incoming_path, incoming_settings))

    monkeypatch.setattr(controller_module, "save_settings", fake_save)

    loaded = controller._load_or_init_settings(path)

    assert isinstance(loaded, AppSettings)
    assert path.parent.exists() is True
    assert saves == [(path, loaded)]


@pytest.mark.asyncio
async def test_rebuild_llm_provider_closes_existing_provider_and_updates_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    close_calls: list[str] = []

    class FakeLlm:
        async def close(self) -> None:
            close_calls.append("close")

    new_llm = object()
    controller.hub = DummyHub(llm=FakeLlm())

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: new_llm)

    await controller._rebuild_llm_provider()

    assert close_calls == ["close"]
    assert controller.hub.llm is new_llm
    assert dash.translation_needs_key is False


@pytest.mark.asyncio
async def test_rebuild_pipeline_restarts_runtime_and_schedules_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    dash.is_translation_on = False
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    events: list[object] = []

    class OldSender:
        def close(self) -> None:
            events.append("old_sender_close")

    class NewHub(DummyHub):
        async def start(self, *, auto_flush_osc: bool) -> None:
            events.append(("new_hub_start", auto_flush_osc))

    class FakeBridge:
        def __init__(self, *, app, event_queue) -> None:
            events.append(("bridge_init", app, event_queue))

        async def run(self) -> None:
            events.append("bridge_run")

    old_bridge_task = asyncio.create_task(asyncio.sleep(3600))
    controller._bridge_task = old_bridge_task
    controller.hub = DummyHub(llm=object(), stt=object())
    controller.sender = OldSender()
    new_hub = NewHub(llm=object(), stt=object())

    async def fake_set_stt_enabled(self, enabled: bool) -> None:
        _ = self
        events.append(("set_stt", enabled))

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        events.append(("configure_receiver", enabled))

    async def fake_init_pipeline(self) -> None:
        self.hub = new_hub
        self.sender = object()
        self.osc = object()
        events.append("init_pipeline")

    async def fake_verify_and_update_status(self) -> None:
        events.append("verify_run")

    original_create_task = asyncio.create_task

    def wrapped_create_task(coro):
        return original_create_task(coro)

    monkeypatch.setattr(GuiController, "set_stt_enabled", fake_set_stt_enabled)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(GuiController, "_verify_and_update_status", fake_verify_and_update_status)
    monkeypatch.setattr(controller_module, "UIEventBridge", FakeBridge)
    monkeypatch.setattr(controller_module.asyncio, "create_task", wrapped_create_task)

    await controller._rebuild_pipeline(rebuild_stt=True)
    await asyncio.sleep(0)

    assert ("set_stt", False) in events
    assert ("configure_receiver", False) in events
    assert "old_sender_close" in events
    assert "init_pipeline" in events
    assert dash.translation_needs_key is False
    assert dash.stt_needs_key is False
    assert dash.translation_enabled is False
    assert ("new_hub_start", True) in events
    assert any(item[0] == "bridge_init" for item in events if isinstance(item, tuple))
    assert "bridge_run" in events
    assert "verify_run" in events


@pytest.mark.asyncio
async def test_verify_qwen_llm_api_key_returns_false_without_settings() -> None:
    controller = _make_controller(app=SimpleNamespace())

    result = await controller._verify_qwen_llm_api_key(
        "secret",
        base_url="https://dashscope.aliyuncs.com/api/v1",
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_qwen_llm_api_key_uses_async_provider_in_low_latency_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.stt.low_latency_mode = True
    calls: list[tuple[str, str, str]] = []

    async def fake_verify(api_key: str, *, base_url: str, model: str) -> bool:
        calls.append((api_key, base_url, model))
        return True

    monkeypatch.setattr(AsyncQwenLLMProvider, "verify_api_key", staticmethod(fake_verify))

    result = await controller._verify_qwen_llm_api_key(
        "secret",
        base_url="https://dashscope.aliyuncs.com/api/v1",
    )

    assert result is True
    assert calls == [
        ("secret", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.5-plus")
    ]


@pytest.mark.asyncio
async def test_verify_qwen_llm_api_key_uses_sync_provider_when_low_latency_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.stt.low_latency_mode = False
    calls: list[tuple[str, str, str]] = []

    async def fake_verify(api_key: str, *, base_url: str, model: str) -> bool:
        calls.append((api_key, base_url, model))
        return True

    monkeypatch.setattr(QwenLLMProvider, "verify_api_key", staticmethod(fake_verify))

    result = await controller._verify_qwen_llm_api_key(
        "secret",
        base_url="https://dashscope.aliyuncs.com/api/v1",
        model="qwen3.5-flash",
    )

    assert result is True
    assert calls == [("secret", "https://dashscope.aliyuncs.com/api/v1", "qwen3.5-flash")]
