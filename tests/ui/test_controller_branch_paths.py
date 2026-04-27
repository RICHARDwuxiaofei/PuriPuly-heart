from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from uuid import uuid4

import flet as ft
import pytest

pytest.importorskip("flet")

from puripuly_heart.config.audio_host_api import (
    WINDOWS_WASAPI_COMPATIBILITY_HOST_API,
    WINDOWS_WASAPI_HOST_API,
)
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterFallbackSelectionAlias,
    OpenRouterLLMModel,
    OpenRouterRoutingMode,
    OpenRouterSelectionAlias,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.managed_openrouter_broker_client import (
    HttpManagedOpenRouterBrokerClient,
)
from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterReleaseBehavior,
    ManagedOpenRouterReleaseDiagnostics,
    ManagedOpenRouterReleaseResult,
    ManagedOpenRouterReleaseService,
    UnavailableManagedOpenRouterReleaseClient,
)
from puripuly_heart.core.openrouter_pkce import OpenRouterPKCEExchangeResult
from puripuly_heart.core.osc.receiver import VrcMicState
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.overlay.sink import (
    PeerTranscriptFinal,
    SelfTranscriptFinal,
    TranslationFinal,
)
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider
from puripuly_heart.providers.stt.deepgram import DeepgramRealtimeSTTBackend
from puripuly_heart.providers.stt.soniox import SonioxRealtimeSTTBackend
from puripuly_heart.ui import controller as controller_module
from puripuly_heart.ui.controller import GuiController
from puripuly_heart.ui.i18n import t


class DummySecrets:
    def __init__(self, values: dict[str, str]):
        self._values = dict(values)
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self._values[key] = value

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self._values.pop(key, None)


class DummyDashboard:
    def __init__(self) -> None:
        self.translation_needs_key: bool | None = None
        self.translation_enabled: bool | None = None
        self.stt_needs_key: bool | None = None
        self.stt_enabled: bool | None = None
        self.local_stt_notice_status: str | None = None
        self.local_stt_notice_percent: int | None = None
        self.languages: tuple[str, str] | None = None
        self.recent_languages: tuple[list[str], list[str]] | None = None
        self.managed_trial_state: dict[str, object] | None = None
        self.managed_trial_calls: list[dict[str, object]] = []
        self.managed_auth_pending: bool | None = None
        self.managed_auth_pending_calls: list[bool] = []
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

    def set_local_stt_notice(self, status: str | None, percent: int | None = None) -> None:
        self.local_stt_notice_status = status
        self.local_stt_notice_percent = percent

    def set_languages_from_codes(
        self,
        source: str,
        target: str,
        peer_source: str = "",
        peer_target: str = "",
    ) -> None:
        self.languages = (source, target, peer_source, peer_target)

    def set_recent_languages(self, source: list[str], target: list[str]) -> None:
        self.recent_languages = (source, target)

    def set_managed_trial_state(self, **state: object) -> None:
        self.managed_trial_calls.append(dict(state))
        self.managed_trial_state = dict(state)

    def set_managed_auth_pending(self, pending: bool) -> None:
        self.managed_auth_pending = bool(pending)
        self.managed_auth_pending_calls.append(self.managed_auth_pending)


class DummySettingsView:
    def __init__(self) -> None:
        self.calls: list[tuple[AppSettings, Path, bool]] = []
        self.managed_trial_usage_state: dict[str, object] | None = None

    def load_from_settings(
        self,
        settings: AppSettings,
        *,
        config_path: Path,
        preserve_custom_vocab_draft: bool = False,
    ) -> None:
        self.calls.append((settings, config_path, preserve_custom_vocab_draft))

    def set_managed_trial_usage_state(
        self, *, visible: bool, remaining_percent: int | None = None
    ) -> None:
        self.managed_trial_usage_state = {
            "visible": visible,
            "remaining_percent": remaining_percent,
        }


class DummyLogsView:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.attach_calls = 0

    def append_log(self, message: str) -> None:
        self.logs.append(message)

    def attach_log_handler(self) -> None:
        self.attach_calls += 1


class RuntimeLoggingSpy:
    def __init__(
        self, *, detailed_enabled: bool = True, basic_error: Exception | None = None
    ) -> None:
        self.mode = SimpleNamespace(value="detailed" if detailed_enabled else "basic")
        self.basic_messages: list[tuple[int, str]] = []
        self.detailed_messages: list[tuple[int, str]] = []
        self.basic_error = basic_error

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        if self.basic_error is not None:
            raise self.basic_error
        self.basic_messages.append((level, message))

    def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        if self.mode.value != "detailed":
            return False
        self.detailed_messages.append((level, message))
        return True

    def attach_realtime_sink(self, sink) -> None:
        _ = sink

    def set_mode(self, mode) -> None:
        self.mode = SimpleNamespace(value=str(mode))


class DummyHub:
    def __init__(
        self,
        *,
        llm: object | None = object(),
        stt: object | None = object(),
        peer_stt: object | None = None,
    ) -> None:
        self.llm = llm
        self.stt = stt
        self.peer_stt = peer_stt
        self.translation_enabled = True
        self.peer_translation_enabled = False
        self.integrated_context_enabled = False
        self.source_language = "ko"
        self.target_language = "en"
        self.system_prompt = ""
        self.low_latency_mode = False
        self.low_latency_merge_gap_ms = 600
        self.low_latency_spec_retry_max = 10
        self.hangover_s = 1.1
        self.clear_context_calls = 0
        self.promo_calls = 0
        self.replace_stt_calls: list[object | None] = []
        self.replace_peer_stt_calls: list[object | None] = []
        self.start_calls: list[bool] = []
        self.stop_calls = 0
        self.submit_calls: list[tuple[str, str]] = []
        self.reset_overlay_preview_calls = 0
        self.clear_language_runtime_state_calls: list[str] = []
        self.clear_language_runtime_state_errors: dict[str, Exception] = {}
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

    async def reset_overlay_preview(self) -> None:
        self.reset_overlay_preview_calls += 1

    async def clear_language_runtime_state(self, *, channel: str) -> None:
        self.clear_language_runtime_state_calls.append(channel)
        if channel in self.clear_language_runtime_state_errors:
            raise self.clear_language_runtime_state_errors[channel]

    async def replace_stt_provider(self, stt: object | None) -> None:
        old_stt = self.stt
        self.replace_stt_calls.append(stt)
        if old_stt is not None and hasattr(old_stt, "close"):
            await old_stt.close()
        self.stt = stt

    async def replace_peer_stt_provider(self, stt: object | None) -> None:
        old_stt = self.peer_stt
        self.replace_peer_stt_calls.append(stt)
        if old_stt is not None and hasattr(old_stt, "close"):
            await old_stt.close()
        self.peer_stt = stt


class DisclosureDummyHub(DummyHub):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.disclosures: list[str] = []

    def enqueue_peer_translation_disclosure(self, text: str) -> None:
        self.disclosures.append(text)


class DummyPeerRuntime:
    def __init__(self) -> None:
        self.policy_calls: list[dict[str, object]] = []
        self.closed = False
        self.warmup_calls = 0

    async def apply_policy(self, *, config, desired_active: bool) -> None:
        self.policy_calls.append({"config": config, "desired_active": desired_active})

    async def warmup(self) -> None:
        self.warmup_calls += 1

    async def close(self) -> None:
        self.closed = True


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


class DummyManagedReleaseService:
    def __init__(self, result: ManagedOpenRouterReleaseResult) -> None:
        self.result = result
        self.prepare_calls = 0
        self.close_calls = 0

    async def prepare_for_translation(self) -> ManagedOpenRouterReleaseResult:
        self.prepare_calls += 1
        return self.result

    async def close(self) -> None:
        self.close_calls += 1


class InspectingManagedReleaseService(DummyManagedReleaseService):
    def __init__(
        self,
        result: ManagedOpenRouterReleaseResult,
        *,
        on_prepare: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(result)
        self.on_prepare = on_prepare

    async def prepare_for_translation(self) -> ManagedOpenRouterReleaseResult:
        self.prepare_calls += 1
        if self.on_prepare is not None:
            prepare_result = self.on_prepare()
            if asyncio.iscoroutine(prepare_result):
                await prepare_result
        return self.result


class FailingManagedReleaseService(DummyManagedReleaseService):
    def __init__(self, exc: Exception) -> None:
        super().__init__(
            ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.RETRY,
                message_key="managed_release.retry",
            )
        )
        self.exc = exc

    async def prepare_for_translation(self) -> ManagedOpenRouterReleaseResult:
        self.prepare_calls += 1
        raise self.exc


class FakeOverlayBridge:
    instances: list["FakeOverlayBridge"] = []

    def __init__(self, *, session_token: str, initial_snapshot=None, **_kwargs) -> None:
        self.session_token = session_token
        self.initial_snapshot = initial_snapshot
        self.current_snapshot = initial_snapshot
        self.messages: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.url = "ws://127.0.0.1:8765"
        self.started = False
        self.stopped = False
        self.snapshots: list[object] = []
        self.shutdown_calls = 0
        self.runtime_control_messages: list[str] = []
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def replace_snapshot(self, snapshot: object) -> None:
        self.current_snapshot = snapshot
        self.snapshots.append(snapshot)

    async def broadcast_shutdown(self) -> None:
        self.shutdown_calls += 1

    async def broadcast_runtime_control(self, *, logging_mode: str) -> None:
        self.runtime_control_messages.append(logging_mode)

    def snapshot(self):
        return self.current_snapshot


class FakeOverlayProcessManager:
    instances: list["FakeOverlayProcessManager"] = []

    def __init__(
        self,
        *,
        bridge_url: str,
        bridge_messages: asyncio.Queue[dict[str, object]],
        session_token: str,
        locale: str,
        startup_timeout_ms: int,
        **_kwargs,
    ) -> None:
        self.bridge_url = bridge_url
        self.bridge_messages = bridge_messages
        self.session_token = session_token
        self.locale = locale
        self.startup_timeout_ms = startup_timeout_ms
        self.state = "off"
        self.failure_reason: str | None = None
        self.restart_scheduled = False
        self.stop_calls = 0
        self._start_gate = asyncio.Event()
        self._start_failure_reason: str | None = None
        self._runtime_failure_reason: str | None = None
        self._monitor_release: asyncio.Event | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.state = "starting"
        await self._start_gate.wait()
        if self._start_failure_reason is not None:
            self.state = "failed"
            self.failure_reason = self._start_failure_reason
            return

        self.state = "connected"
        self.failure_reason = None
        self._monitor_release = asyncio.Event()

        async def _monitor() -> None:
            assert self._monitor_release is not None
            await self._monitor_release.wait()
            if self._runtime_failure_reason is not None:
                self.state = "failed"
                self.failure_reason = self._runtime_failure_reason

        self._monitor_task = asyncio.create_task(_monitor())

    async def stop(self) -> None:
        self.stop_calls += 1
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        self.state = "off"

    def complete_startup(self, *, failure_reason: str | None = None) -> None:
        self._start_failure_reason = failure_reason
        self._start_gate.set()

    def trigger_runtime_failure(self, failure_reason: str) -> None:
        self._runtime_failure_reason = failure_reason
        assert self._monitor_release is not None
        self._monitor_release.set()


def _make_controller(*, app: object) -> GuiController:
    return GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))


async def _wait_until(predicate, *, attempts: int = 20) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not met in time")


def _patch_overlay_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOverlayBridge.instances = []
    FakeOverlayProcessManager.instances = []
    monkeypatch.setattr(controller_module, "OverlayBridge", FakeOverlayBridge)
    monkeypatch.setattr(controller_module, "OverlayProcessManager", FakeOverlayProcessManager)


def _patch_init_pipeline_dependencies(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    created: dict[str, object] = {}

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: "llm")
    monkeypatch.setattr(controller_module, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(
        controller_module, "create_peer_stt_backend", lambda *_a, **_k: "peer-backend"
    )
    monkeypatch.setattr(controller_module, "ManagedSTTProvider", lambda *a, **k: "stt")

    class FakeSender:
        def close(self) -> None:
            return None

    def fake_sender(*_args, **_kwargs):
        sender = FakeSender()
        created["sender"] = sender
        return sender

    def fake_osc(*_args, **_kwargs):
        created["osc_kwargs"] = dict(_kwargs)
        osc = object()
        created["osc"] = osc
        return osc

    def fake_hub(*_args, **kwargs):
        hub = SimpleNamespace(
            llm=kwargs.get("llm"),
            stt=kwargs.get("stt"),
            peer_stt=kwargs.get("peer_stt"),
            peer_translation_enabled=kwargs.get("peer_translation_enabled", False),
            integrated_context_enabled=kwargs.get("integrated_context_enabled", False),
        )
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
    assert getattr(controller, "runtime_logging_mode", None) == "basic"
    assert any("[ERROR]" in line and "bad key" in line for line in logs.logs)


def test_log_error_falls_back_to_standard_logger_without_direct_logs_view_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs = DummyLogsView()
    controller = _make_controller(app=SimpleNamespace(view_logs=logs))

    class BrokenRuntimeLogging:
        def attach_realtime_sink(self, _sink) -> None:
            return None

        def emit_basic(self, _message: str, *, level: int = logging.INFO) -> None:
            _ = level
            raise RuntimeError("emit failed")

    controller._runtime_logging = BrokenRuntimeLogging()
    seen: list[tuple[int, str]] = []
    monkeypatch.setattr(
        controller_module.logger,
        "log",
        lambda level, message: seen.append((level, message)),
    )

    controller._log_error("fallback message")

    assert seen == [(logging.ERROR, "fallback message")]
    assert logs.logs == []


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

    assert dash.languages == ("ko", "en", "", "")
    assert dash.recent_languages == (["ko", "ja"], ["en", "zh"])
    assert dash.on_recent_languages_change is not None
    assert settings_view.calls == [(settings, Path("settings.json"), False)]


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


@pytest.mark.asyncio
async def test_set_translation_enabled_keeps_managed_translation_disabled_until_local_key_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dash, view_settings=settings_view)
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    observed_pending: list[bool | None] = []
    controller._managed_openrouter_release_service = InspectingManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            pending_issue=True,
            local_key_available=False,
        ),
        on_prepare=lambda: observed_pending.append(dash.managed_auth_pending),
    )

    async def fail_fetch_key_metadata(_api_key: str):
        raise AssertionError("fetch_key_metadata should not run without a managed key")

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fail_fetch_key_metadata),
    )

    await controller.set_translation_enabled(True)

    assert controller._managed_openrouter_release_service.prepare_calls == 1
    assert controller.hub.translation_enabled is False
    assert controller.hub.clear_context_calls == 0
    assert observed_pending == [True]
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls == [True, False]
    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": None,
    }
    assert dash.managed_trial_calls == []


@pytest.mark.asyncio
async def test_set_translation_enabled_transitions_pending_true_to_false_after_managed_preissue_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    observed_pending: list[bool | None] = []
    scheduled_refreshes: list[str] = []
    controller._managed_openrouter_release_service = InspectingManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key="managed-key",
            local_key_available=True,
            pending_issue=False,
        ),
        on_prepare=lambda: observed_pending.append(dash.managed_auth_pending),
    )

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(
        GuiController,
        "_schedule_managed_trial_usage_refresh",
        lambda self: scheduled_refreshes.append("scheduled"),
    )

    await controller.set_translation_enabled(True)

    assert controller._managed_openrouter_release_service.prepare_calls == 1
    assert controller.hub.translation_enabled is True
    assert controller.hub.clear_context_calls == 1
    assert observed_pending == [True]
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls == [True, False]
    assert scheduled_refreshes == ["scheduled"]


@pytest.mark.asyncio
async def test_set_translation_enabled_rebuild_path_keeps_success_when_managed_usage_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=None)
    controller._managed_openrouter_release_service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key="managed-key",
            local_key_available=True,
            pending_issue=False,
        )
    )

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(
        GuiController,
        "_create_managed_openrouter_release_service",
        lambda self, *, secrets: None,
    )
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: object())
    monkeypatch.setattr(
        GuiController,
        "_refresh_managed_trial_usage_state",
        lambda self: (_ for _ in ()).throw(RuntimeError("usage refresh boom")),
    )

    await controller.set_translation_enabled(True)

    assert controller.hub.llm is not None
    assert controller.hub.translation_enabled is True
    assert controller.hub.clear_context_calls == 1
    assert dash.managed_auth_pending_calls == [True, False]
    assert (
        logging.WARNING,
        "[ManagedAuth] Usage refresh failed: usage refresh boom",
    ) in controller._runtime_logging.basic_messages
    assert (
        logging.INFO,
        "[Settings] LLM provider rebuilt successfully",
    ) in controller._runtime_logging.basic_messages
    assert (
        logging.INFO,
        "[Translation] Enabled with provider: openrouter",
    ) in controller._runtime_logging.basic_messages


@pytest.mark.asyncio
async def test_set_translation_enabled_rebuild_path_turns_translation_back_off_when_refresh_discovers_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=dash,
            view_settings=settings_view,
            show_founder_letter_dialog=lambda: shown.append("shown"),
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.settings.managed_identity.active_managed_credential_ref = "hash_123"
    controller.hub = DummyHub(llm=None)
    controller._managed_openrouter_release_service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key="managed-key",
            local_key_available=True,
            pending_issue=False,
        )
    )

    metadata_responses = [
        controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.05,
            usage_usd=0.03,
        ),
        controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.0007,
            usage_usd=0.0793,
        ),
    ]

    async def fake_fetch_key_metadata(_api_key: str):
        return metadata_responses.pop(0)

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_a, **_k: DummySecrets({"openrouter_managed_api_key": "managed-key"}),
    )
    monkeypatch.setattr(
        GuiController,
        "_create_managed_openrouter_release_service",
        lambda self, *, secrets: None,
    )
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: object())
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller.set_translation_enabled(True)

    assert shown == ["shown"]
    assert controller.hub.llm is not None
    assert controller.hub.translation_enabled is False
    assert controller.hub.clear_context_calls == 0
    assert dash.translation_enabled is False
    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": 1,
    }


@pytest.mark.asyncio
async def test_set_translation_enabled_keeps_managed_translation_disabled_on_retry_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snackbar_calls: list[tuple[str, str]] = []
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(
            _show_snackbar=lambda message, color: snackbar_calls.append((message, color)),
            view_dashboard=dash,
            view_settings=settings_view,
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller._managed_openrouter_release_service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.RETRY,
            message_key="managed_release.retry_after_ms",
            message_kwargs={"retry_after_ms": 5000},
            diagnostics=ManagedOpenRouterReleaseDiagnostics(
                operation="issue",
                code="trial_unavailable",
                error_class="retryable",
                subcode="broker_backoff",
                retry_after_ms=5000,
                message="broker is temporarily unavailable",
            ),
            retry_after_ms=5000,
        )
    )

    async def fail_fetch_key_metadata(_api_key: str):
        raise AssertionError("fetch_key_metadata should not run without a managed key")

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fail_fetch_key_metadata),
    )

    await controller.set_translation_enabled(True)

    assert controller._managed_openrouter_release_service.prepare_calls == 1
    assert controller.hub.translation_enabled is False
    assert controller.hub.clear_context_calls == 0
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls == [True, False]
    assert snackbar_calls == [
        (t("managed_release.retry_after_ms", retry_after_ms=5000), ft.Colors.ORANGE_700)
    ]
    assert (
        logging.ERROR,
        "[ManagedAuth] operation=issue code=trial_unavailable class=retryable subcode=broker_backoff retry_after_ms=5000 message=broker is temporarily unavailable",
    ) in controller._runtime_logging.basic_messages
    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": None,
    }
    assert dash.managed_trial_calls == []


@pytest.mark.asyncio
async def test_set_translation_enabled_shows_brake_snackbar_without_dashboard_trial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snackbar_calls: list[tuple[str, str]] = []
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(
            _show_snackbar=lambda message, color: snackbar_calls.append((message, color)),
            view_dashboard=dash,
            view_settings=settings_view,
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller._managed_openrouter_release_service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.RETRY,
            message_key="managed_release.brake",
            message_kwargs={"retry_after_ms": 5000},
            diagnostics=ManagedOpenRouterReleaseDiagnostics(
                operation="issue",
                code="issuance_suspended",
                error_class="retryable",
                subcode="asn_fast_path",
                retry_after_ms=5000,
                message="new entitlement issuance is temporarily suspended",
            ),
            retry_after_ms=5000,
        )
    )

    async def fail_fetch_key_metadata(_api_key: str):
        raise AssertionError("fetch_key_metadata should not run without a managed key")

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fail_fetch_key_metadata),
    )

    await controller.set_translation_enabled(True)

    assert controller._managed_openrouter_release_service.prepare_calls == 1
    assert controller.hub.translation_enabled is False
    assert dash.managed_auth_pending is False
    assert dash.managed_trial_calls == []
    assert dash.managed_trial_state is None
    assert snackbar_calls == [(t("managed_release.brake"), ft.Colors.ORANGE_700)]
    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": None,
    }


@pytest.mark.asyncio
async def test_set_translation_enabled_shows_revoked_snackbar_without_dashboard_trial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snackbar_calls: list[tuple[str, str]] = []
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(
            _show_snackbar=lambda message, color: snackbar_calls.append((message, color)),
            view_dashboard=dash,
            view_settings=settings_view,
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller._managed_openrouter_release_service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.STOP,
            message_key="managed_release.revoked_contact",
            diagnostics=ManagedOpenRouterReleaseDiagnostics(
                operation="issue",
                code="trial_not_eligible",
                error_class="terminal",
                subcode=None,
                retry_after_ms=None,
                message="revoked by policy",
            ),
        )
    )

    async def fail_fetch_key_metadata(_api_key: str):
        raise AssertionError("fetch_key_metadata should not run without a managed key")

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fail_fetch_key_metadata),
    )

    await controller.set_translation_enabled(True)

    assert controller._managed_openrouter_release_service.prepare_calls == 1
    assert controller.hub.translation_enabled is False
    assert dash.managed_auth_pending is False
    assert dash.managed_trial_calls == []
    assert dash.managed_trial_state is None
    assert snackbar_calls == [(t("managed_release.revoked_contact"), ft.Colors.ORANGE_700)]
    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": None,
    }


def test_on_managed_trial_delegate_ready_clears_dashboard_pending_notice() -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))

    controller._managed_trial_pending_auth = True
    controller._sync_managed_auth_dashboard_notice()

    controller._on_managed_trial_delegate_ready()

    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls == [True, False]


@pytest.mark.asyncio
async def test_set_translation_enabled_false_clears_dashboard_managed_auth_pending() -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    controller._managed_trial_pending_auth = True
    controller._sync_managed_auth_dashboard_notice()

    await controller.set_translation_enabled(False)

    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls == [True, False]


@pytest.mark.asyncio
async def test_set_translation_enabled_off_wins_against_inflight_managed_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()

    async def block_prepare() -> None:
        prepare_started.set()
        await release_prepare.wait()

    controller._managed_openrouter_release_service = InspectingManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key="managed-key",
            local_key_available=True,
            pending_issue=False,
        ),
        on_prepare=block_prepare,
    )

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(
        GuiController,
        "_schedule_managed_trial_usage_refresh",
        lambda self: None,
    )

    enable_task = asyncio.create_task(controller.set_translation_enabled(True))
    await prepare_started.wait()

    await controller.set_translation_enabled(False)

    assert controller.hub.translation_enabled is False
    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False

    release_prepare.set()
    await enable_task

    assert controller._managed_openrouter_release_service.prepare_calls == 1
    assert controller.hub.translation_enabled is False
    assert controller.hub.clear_context_calls == 1
    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls[:2] == [True, False]
    assert dash.managed_auth_pending_calls[-1] is False


@pytest.mark.asyncio
async def test_set_translation_enabled_off_wins_before_stale_ready_rebuild_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=None)
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()
    rebuild_calls: list[str] = []

    async def block_prepare() -> None:
        prepare_started.set()
        await release_prepare.wait()

    async def fake_rebuild_llm_provider(self) -> None:
        _ = self
        rebuild_calls.append("rebuild")

    controller._managed_openrouter_release_service = InspectingManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key="managed-key",
            local_key_available=True,
            pending_issue=False,
        ),
        on_prepare=block_prepare,
    )

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)

    enable_task = asyncio.create_task(controller.set_translation_enabled(True))
    await prepare_started.wait()

    await controller.set_translation_enabled(False)

    release_prepare.set()
    await enable_task

    assert rebuild_calls == []
    assert controller.hub.llm is None
    assert controller.hub.translation_enabled is False
    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False


@pytest.mark.asyncio
async def test_set_translation_enabled_off_wins_before_stale_retry_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snackbar_calls: list[tuple[str, str]] = []
    dash = DummyDashboard()
    controller = _make_controller(
        app=SimpleNamespace(
            _show_snackbar=lambda message, color: snackbar_calls.append((message, color)),
            view_dashboard=dash,
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()
    refresh_calls: list[str] = []

    async def block_prepare() -> None:
        prepare_started.set()
        await release_prepare.wait()

    async def fake_refresh_managed_trial_usage_state(self) -> None:
        _ = self
        refresh_calls.append("refresh")

    controller._managed_openrouter_release_service = InspectingManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.RETRY,
            message_key="managed_release.retry_after_ms",
            message_kwargs={"retry_after_ms": 5000},
        ),
        on_prepare=block_prepare,
    )

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setattr(
        GuiController,
        "_refresh_managed_trial_usage_state",
        fake_refresh_managed_trial_usage_state,
    )

    enable_task = asyncio.create_task(controller.set_translation_enabled(True))
    await prepare_started.wait()

    await controller.set_translation_enabled(False)

    release_prepare.set()
    await enable_task

    assert controller.hub.translation_enabled is False
    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False
    assert dash.managed_trial_calls == []
    assert dash.managed_trial_state is None
    assert snackbar_calls == []
    assert refresh_calls == []


@pytest.mark.asyncio
async def test_apply_providers_clears_dashboard_pending_notice_when_switching_away_from_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    controller._managed_trial_pending_auth = True
    controller._sync_managed_auth_dashboard_notice()

    next_settings = AppSettings()
    next_settings.provider.llm = LLMProviderName.GEMINI

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", lambda self: asyncio.sleep(0))

    await controller.apply_providers(next_settings)

    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls == [True, False]


@pytest.mark.asyncio
async def test_set_translation_enabled_clears_dashboard_pending_notice_when_prepare_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    controller._managed_openrouter_release_service = FailingManagedReleaseService(
        RuntimeError("boom")
    )

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await controller.set_translation_enabled(True)

    assert controller._managed_trial_pending_auth is False
    assert dash.managed_auth_pending is False
    assert dash.managed_auth_pending_calls == [True, False]


@pytest.mark.asyncio
async def test_apply_providers_resyncs_dashboard_pending_notice_when_staying_on_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    controller._managed_trial_pending_auth = True
    controller._sync_managed_auth_dashboard_notice()

    next_settings = AppSettings()
    next_settings.provider.llm = LLMProviderName.OPENROUTER
    next_settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", lambda self: asyncio.sleep(0))

    await controller.apply_providers(next_settings)

    assert controller._managed_trial_pending_auth is True
    assert dash.managed_auth_pending is True
    assert dash.managed_auth_pending_calls == [True, True]


@pytest.mark.asyncio
async def test_apply_providers_staying_on_managed_does_not_prepare_managed_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    class TrackingManagedReleaseService:
        def __init__(self) -> None:
            self.prepare_calls = 0
            self.close_calls = 0

        async def prepare_for_translation(self):
            self.prepare_calls += 1
            raise AssertionError("apply_providers must not prepare managed translation")

        async def close(self) -> None:
            self.close_calls += 1

    initial_service = TrackingManagedReleaseService()
    created_services: list[TrackingManagedReleaseService] = []
    controller._managed_openrouter_release_service = initial_service

    updated = copy.deepcopy(controller.settings)
    updated.openrouter.routing_mode = OpenRouterRoutingMode.PARASAIL_FIRST

    async def fake_refresh_managed_usage(self) -> None:
        return None

    def fake_create_managed_release_service(self, *, secrets):
        _ = (self, secrets)
        service = TrackingManagedReleaseService()
        created_services.append(service)
        return service

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: object())
    monkeypatch.setattr(
        GuiController,
        "_create_managed_openrouter_release_service",
        fake_create_managed_release_service,
    )
    monkeypatch.setattr(
        GuiController,
        "_refresh_managed_trial_usage_state_best_effort",
        fake_refresh_managed_usage,
    )

    await controller.apply_providers(updated)

    assert initial_service.close_calls == 1
    assert len(created_services) == 1
    assert created_services[0].prepare_calls == 0
    assert controller.settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED


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


def test_build_llm_provider_signature_tracks_openrouter_fallback_alias_only() -> None:
    controller = _make_controller(app=SimpleNamespace())
    base = AppSettings()
    base.provider.llm = LLMProviderName.OPENROUTER
    base.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    base.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE

    same_runtime_missing_ui_alias = copy.deepcopy(base)
    same_runtime_missing_ui_alias.openrouter.selection_alias = None

    different_fallback = copy.deepcopy(base)
    different_fallback.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.NONE

    assert controller._build_llm_provider_signature(
        base
    ) == controller._build_llm_provider_signature(same_runtime_missing_ui_alias)
    assert controller._build_llm_provider_signature(
        base
    ) != controller._build_llm_provider_signature(different_fallback)


def test_stt_runtime_signature_includes_custom_vocabulary_state() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.stt = STTProviderName.DEEPGRAM
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": [" Puripuly ", "VRChat", "Puripuly"], "en": ["Avatar"]}
    settings.stt.custom_vocabulary_enabled = False

    disabled_signature = controller._build_stt_runtime_signature(settings)

    settings.stt.custom_vocabulary_enabled = True
    enabled_signature = controller._build_stt_runtime_signature(settings)

    assert disabled_signature != enabled_signature
    assert enabled_signature[-2] is True
    assert enabled_signature[-1] == ("Puripuly", "VRChat")


def test_stt_runtime_signature_includes_source_language() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.stt = STTProviderName.DEEPGRAM
    settings.languages.source_language = "ko"

    ko_signature = controller._build_stt_runtime_signature(settings)
    settings.languages.source_language = "en"
    en_signature = controller._build_stt_runtime_signature(settings)

    assert ko_signature != en_signature
    assert ko_signature[0] == "ko"
    assert en_signature[0] == "en"


def test_stt_runtime_signature_differs_between_plain_wasapi_and_compatibility_mode() -> None:
    controller = _make_controller(app=SimpleNamespace())
    plain = AppSettings()
    plain.audio.input_host_api = WINDOWS_WASAPI_HOST_API
    compat = copy.deepcopy(plain)
    compat.audio.input_host_api = WINDOWS_WASAPI_COMPATIBILITY_HOST_API

    assert controller._build_stt_runtime_signature(
        plain
    ) != controller._build_stt_runtime_signature(compat)


def test_stt_runtime_signature_ignores_custom_vocabulary_for_qwen_asr() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.stt = STTProviderName.QWEN_ASR
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly", "VRChat"]}

    disabled_signature = controller._build_stt_runtime_signature(settings)

    settings.stt.custom_vocabulary_enabled = True
    enabled_signature = controller._build_stt_runtime_signature(settings)

    assert disabled_signature == enabled_signature
    assert enabled_signature[-2] is False
    assert enabled_signature[-1] == ()


def test_stt_runtime_signature_uses_capped_custom_vocabulary_for_local_qwen() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": [f"term-{i:02d}" for i in range(20)]}
    settings.stt.custom_vocabulary_enabled = False

    disabled_signature = controller._build_stt_runtime_signature(settings)

    settings.stt.custom_vocabulary_enabled = True
    enabled_signature = controller._build_stt_runtime_signature(settings)

    assert disabled_signature != enabled_signature
    assert enabled_signature[-2] is True
    assert enabled_signature[-1] == tuple(f"term-{i:02d}" for i in range(12))


def test_peer_stt_runtime_custom_vocabulary_signature_is_disabled() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.languages.peer_source_language = "zh-CN"
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": ["Puripuly"],
        "zh-CN": ["airi", "shinano"],
    }

    assert controller._peer_stt_runtime_custom_vocabulary_signature(settings) == (False, ())


def test_self_stt_runtime_signature_ignores_overlay_and_peer_desktop_settings() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()

    baseline = controller._build_self_stt_runtime_signature(settings)

    settings.ui.peer_translation_enabled = True
    controller.overlay_state = "connected"
    settings.desktop_audio.output_device = "Headphones (Loopback)"
    settings.desktop_audio.vad_speech_threshold = 0.72
    settings.desktop_audio.vad_hangover_ms = 950
    settings.desktop_audio.vad_pre_roll_ms = 420
    changed = controller._build_self_stt_runtime_signature(settings)

    assert baseline == changed


def test_peer_stt_runtime_signature_includes_peer_desktop_settings() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()

    baseline = controller._build_peer_stt_runtime_signature(settings)

    settings.ui.peer_translation_enabled = True
    settings.desktop_audio.output_device = "Headphones (Loopback)"
    settings.desktop_audio.vad_speech_threshold = 0.72
    settings.desktop_audio.vad_hangover_ms = 950
    settings.desktop_audio.vad_pre_roll_ms = 420
    changed = controller._build_peer_stt_runtime_signature(settings)

    assert baseline != changed


def test_peer_stt_runtime_signature_includes_peer_source_language() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()

    baseline = controller._build_peer_stt_runtime_signature(settings)

    settings.languages.peer_source_language = "zh-CN"
    changed = controller._build_peer_stt_runtime_signature(settings)

    assert baseline != changed


def test_build_peer_runtime_config_includes_provider_signature_and_desktop_settings() -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.desktop_audio.output_device = "Headphones (Loopback)"
    settings.desktop_audio.vad_speech_threshold = 0.72
    settings.desktop_audio.vad_hangover_ms = 950
    settings.desktop_audio.vad_pre_roll_ms = 420

    config = controller._build_peer_runtime_config(settings)

    assert config.backend.provider == STTProviderName.SONIOX
    assert config.output_device == "Headphones (Loopback)"
    assert config.vad_threshold == 0.72
    assert config.runtime_signature == (
        config.backend.source_language,
        config.output_device,
        config.vad_threshold,
        config.vad_hangover_ms,
        config.vad_pre_roll_ms,
        config.provider_signature,
    )


@pytest.mark.asyncio
async def test_apply_settings_updates_peer_translation_flags_on_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.overlay_state = "connected"
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        controller.settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        controller.settings
    )
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", lambda self: asyncio.sleep(0))

    updated = AppSettings()
    updated.ui.peer_translation_enabled = True
    updated.ui.peer_translation_eula_accepted = True
    updated.ui.integrated_context_enabled = True

    await controller.apply_settings(updated)

    assert controller.hub.peer_translation_enabled is True
    assert controller.hub.integrated_context_enabled is True


@pytest.mark.asyncio
async def test_apply_settings_routes_peer_activation_toggles_through_peer_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    controller.settings = settings
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )

    enabled = AppSettings()
    enabled.ui.peer_translation_enabled = True
    enabled.ui.peer_translation_eula_accepted = True
    await controller.apply_settings(enabled)

    disabled = AppSettings()
    disabled.ui.peer_translation_enabled = False
    await controller.apply_settings(disabled)

    assert [call["desired_active"] for call in controller._peer_runtime.policy_calls] == [
        True,
        False,
    ]
    assert controller.hub.peer_translation_enabled is False


@pytest.mark.asyncio
async def test_apply_settings_keeps_peer_translation_effective_flags_off_until_eula_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    controller.settings = settings
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )

    updated = copy.deepcopy(settings)
    updated.ui.peer_translation_enabled = True
    updated.ui.peer_translation_eula_accepted = False
    updated.ui.integrated_context_enabled = True

    await controller.apply_settings(updated)

    assert controller.hub.peer_translation_enabled is False
    assert controller.hub.integrated_context_enabled is False
    assert [call["desired_active"] for call in controller._peer_runtime.policy_calls] == [False]


@pytest.mark.asyncio
async def test_apply_settings_deactivates_peer_runtime_when_eula_acceptance_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.ui.peer_translation_enabled = True
    settings.ui.peer_translation_eula_accepted = True
    settings.ui.integrated_context_enabled = True
    controller.settings = settings
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.hub.peer_translation_enabled = True
    controller.hub.integrated_context_enabled = True
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )
    controller._last_peer_translation_enabled = settings.ui.peer_translation_enabled
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )

    updated = copy.deepcopy(settings)
    updated.ui.peer_translation_eula_accepted = False

    await controller.apply_settings(updated)

    assert controller.hub.peer_translation_enabled is False
    assert controller.hub.integrated_context_enabled is False
    assert [call["desired_active"] for call in controller._peer_runtime.policy_calls] == [False]


@pytest.mark.asyncio
async def test_apply_settings_deactivates_peer_runtime_when_eula_flag_mutates_current_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.ui.peer_translation_enabled = True
    settings.ui.peer_translation_eula_accepted = True
    controller.settings = settings
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.hub.peer_translation_enabled = True
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()
    controller._sync_signature_caches(settings)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )

    settings.ui.peer_translation_eula_accepted = False

    await controller.apply_settings(settings)

    assert controller.hub.peer_translation_enabled is False
    assert [call["desired_active"] for call in controller._peer_runtime.policy_calls] == [False]


@pytest.mark.asyncio
async def test_set_peer_translation_enabled_routes_through_controller_runtime_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls: list[str] = []
    controller = _make_controller(
        app=SimpleNamespace(refresh_overlay_peer_contract=lambda: refresh_calls.append("refresh"))
    )
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)

    async def fake_begin_overlay_start(self: GuiController) -> None:
        self.overlay_state = "starting"

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_begin_overlay_start", fake_begin_overlay_start)
    monkeypatch.setattr(
        GuiController, "_refresh_overlay_runtime_dependencies", lambda self: asyncio.sleep(0)
    )

    await controller.set_peer_translation_enabled(True)

    assert controller.settings.ui.overlay_enabled is True
    assert controller.settings.ui.peer_translation_enabled is True
    assert controller.settings.ui.integrated_context_enabled is False
    assert controller.settings.ui.integrated_context_bootstrapped is False
    assert controller.overlay_state == "starting"
    assert refresh_calls == ["refresh", "refresh"]

    contract = controller.build_overlay_peer_consumer_contract()
    assert contract is not None
    assert contract.peer.state == "warning"
    assert contract.peer.helper_text == t("settings.peer_translation.warning.overlay_starting")


@pytest.mark.asyncio
async def test_set_peer_translation_enabled_requires_eula_acceptance_before_persisting_or_activating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls: list[str] = []
    begin_calls: list[str] = []
    save_calls: list[str] = []
    controller = _make_controller(
        app=SimpleNamespace(refresh_overlay_peer_contract=lambda: refresh_calls.append("refresh"))
    )
    controller.settings = AppSettings()
    controller.settings.ui.overlay_enabled = False
    controller.settings.ui.peer_translation_eula_accepted = False
    controller.settings.provider.peer_stt = STTProviderName.SONIOX
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.overlay_state = "off"

    async def fake_begin_overlay_start(self: GuiController) -> None:
        _ = self
        begin_calls.append("begin")

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: save_calls.append("save"))
    monkeypatch.setattr(GuiController, "_begin_overlay_start", fake_begin_overlay_start)
    monkeypatch.setattr(
        GuiController,
        "_refresh_overlay_runtime_dependencies",
        lambda self: asyncio.sleep(0),
    )

    await controller.set_peer_translation_enabled(True)

    assert controller.settings.ui.overlay_enabled is False
    assert controller.settings.ui.peer_translation_enabled is False
    assert controller.hub.peer_translation_enabled is False
    assert save_calls == []
    assert begin_calls == []
    assert refresh_calls == ["refresh"]


@pytest.mark.asyncio
async def test_set_peer_translation_enabled_enqueues_peer_disclosure_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(refresh_overlay_peer_contract=lambda: None))
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DisclosureDummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.overlay_state = "connected"
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_refresh_overlay_runtime_dependencies",
        lambda self: asyncio.sleep(0),
    )

    await controller.set_peer_translation_enabled(True)

    assert controller.hub.disclosures == [t("peer_translation.disclosure")]


@pytest.mark.asyncio
async def test_set_peer_translation_enabled_surfaces_local_notice_for_peer_local_qwen_when_runtime_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=dash,
            refresh_overlay_peer_contract=lambda: None,
        )
    )
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)
    controller._local_stt_install_state = controller_module.LocalSTTInstallState(status="missing")

    async def fake_begin_overlay_start(self: GuiController) -> None:
        self.overlay_state = "starting"

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_begin_overlay_start", fake_begin_overlay_start)
    monkeypatch.setattr(
        GuiController, "_refresh_overlay_runtime_dependencies", lambda self: asyncio.sleep(0)
    )

    await controller.set_peer_translation_enabled(True)

    assert controller.settings.ui.peer_translation_enabled is True
    assert dash.local_stt_notice_status == "missing"


@pytest.mark.asyncio
async def test_rebuild_pipeline_closes_previous_peer_runtime_before_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    old_runtime = DummyPeerRuntime()
    controller._peer_runtime = old_runtime

    new_runtime = DummyPeerRuntime()
    new_hub = DummyHub(llm=object(), stt=object(), peer_stt=object())

    class FakeUIEventBridge:
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            self.app = app
            self.event_queue = event_queue

        async def run(self) -> None:
            return None

    async def fake_init_pipeline(self: GuiController) -> None:
        assert old_runtime.closed is True
        controller.hub = new_hub
        controller._peer_runtime = new_runtime

    monkeypatch.setattr(GuiController, "set_stt_enabled", lambda self, value: asyncio.sleep(0))
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        lambda self, enabled: asyncio.sleep(0),
    )
    monkeypatch.setattr(controller_module, "UIEventBridge", FakeUIEventBridge)
    monkeypatch.setattr(GuiController, "_verify_and_update_status", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)

    await controller._rebuild_pipeline(rebuild_stt=True)

    assert controller._peer_runtime is new_runtime
    assert controller._peer_runtime.closed is False


@pytest.mark.asyncio
async def test_rebuild_pipeline_rebinds_overlay_presenter_to_new_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.overlay_state = "connected"

    presenter = OverlayPresenter(
        calibration=controller.overlay_calibration.copy(),
        clock=controller.clock,
    )
    old_hub = DummyHub(llm=object(), stt=object())
    old_hub.overlay_sink = presenter
    controller.hub = old_hub
    controller._overlay_presenter = presenter

    new_hub = DummyHub(llm=object(), stt=object())

    class FakeUIEventBridge:
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            self.app = app
            self.event_queue = event_queue

        async def run(self) -> None:
            return None

    async def fake_init_pipeline(self: GuiController) -> None:
        self.hub = new_hub
        self.sender = object()
        self.osc = object()

    monkeypatch.setattr(GuiController, "set_stt_enabled", lambda self, value: asyncio.sleep(0))
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        lambda self, *, enabled: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        GuiController,
        "_refresh_overlay_runtime_dependencies",
        lambda self: asyncio.sleep(0),
    )
    monkeypatch.setattr(controller_module, "UIEventBridge", FakeUIEventBridge)
    monkeypatch.setattr(GuiController, "_verify_and_update_status", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)

    await controller._rebuild_pipeline(rebuild_stt=True)

    assert getattr(new_hub, "overlay_sink", None) is presenter


@pytest.mark.asyncio
async def test_rebuild_pipeline_refreshes_overlay_dependencies_without_overlay_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.overlay_state = "connected"

    presenter = OverlayPresenter(
        calibration=controller.overlay_calibration.copy(),
        clock=controller.clock,
    )
    old_hub = DummyHub(llm=object(), stt=object())
    old_hub.overlay_sink = presenter
    controller.hub = old_hub
    controller._overlay_presenter = presenter

    new_hub = DummyHub(llm=object(), stt=object())
    events: list[tuple[str, object]] = []

    class FakeUIEventBridge:
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            events.append(("bridge_init", event_queue))

        async def run(self) -> None:
            events.append(("bridge_run", True))

    async def fake_init_pipeline(self: GuiController) -> None:
        self.hub = new_hub
        self.sender = object()
        self.osc = object()
        events.append(("init_pipeline", True))

    async def fail_set_overlay_enabled(self: GuiController, enabled: bool) -> None:
        raise AssertionError(f"unexpected overlay restart: {enabled}")

    async def fake_refresh_overlay_runtime_dependencies(self: GuiController) -> None:
        events.append(("refresh_overlay_dependencies", self.overlay_state))

    monkeypatch.setattr(GuiController, "set_stt_enabled", lambda self, value: asyncio.sleep(0))
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        lambda self, *, enabled: asyncio.sleep(0),
    )
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(GuiController, "set_overlay_enabled", fail_set_overlay_enabled)
    monkeypatch.setattr(
        GuiController,
        "_refresh_overlay_runtime_dependencies",
        fake_refresh_overlay_runtime_dependencies,
    )
    monkeypatch.setattr(GuiController, "_verify_and_update_status", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(controller_module, "UIEventBridge", FakeUIEventBridge)

    await controller._rebuild_pipeline(rebuild_stt=True)
    await asyncio.sleep(0)

    assert events.count(("refresh_overlay_dependencies", "connected")) == 1


@pytest.mark.asyncio
async def test_init_pipeline_keeps_peer_original_runtime_available_without_peer_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _patch_init_pipeline_dependencies(monkeypatch)
    monkeypatch.setattr(
        GuiController, "_configure_vrc_mic_receiver", lambda self, enabled: asyncio.sleep(0)
    )

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.overlay_enabled = True
    controller.overlay_state = "connected"

    await controller._init_pipeline()

    hub = created["hub"]
    assert hub.peer_stt is None
    assert hub.peer_translation_enabled is False
    assert controller._peer_runtime is not None


@pytest.mark.asyncio
async def test_init_pipeline_passes_chatbox_and_peer_language_settings_to_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    llm_create_kwargs: dict[str, object] = {}

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())

    def fake_create_llm_provider(*_args, **kwargs):
        llm_create_kwargs.update(kwargs)
        return "llm"

    monkeypatch.setattr(controller_module, "create_llm_provider", fake_create_llm_provider)
    monkeypatch.setattr(controller_module, "create_stt_backend", lambda *_a, **_k: "backend")
    monkeypatch.setattr(controller_module, "ManagedSTTProvider", lambda *a, **k: "stt")
    monkeypatch.setattr(controller_module, "VrchatOscUdpSender", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "SmartOscQueue", lambda *a, **k: object())

    def fake_hub(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            llm=kwargs.get("llm"),
            stt=kwargs.get("stt"),
            peer_stt=kwargs.get("peer_stt"),
        )

    monkeypatch.setattr(controller_module, "ClientHub", fake_hub)
    monkeypatch.setattr(
        GuiController, "_configure_vrc_mic_receiver", lambda self, enabled: asyncio.sleep(0)
    )

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.osc.chatbox_include_source = False
    controller.settings.languages.peer_source_language = "ja"
    controller.settings.languages.peer_target_language = "en"

    await controller._init_pipeline()

    assert captured["chatbox_include_source"] is False
    assert captured["peer_source_language"] == "ja"
    assert captured["peer_target_language"] == "en"
    assert llm_create_kwargs["runtime_logging"] is controller.runtime_logging


@pytest.mark.asyncio
async def test_refresh_peer_stt_runtime_returns_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()

    await controller._refresh_peer_stt_runtime()

    assert controller.hub.replace_stt_calls == []
    assert controller.hub.replace_peer_stt_calls == []


@pytest.mark.asyncio
async def test_refresh_peer_stt_runtime_does_not_warm_peer_runtime() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()

    await controller._refresh_peer_stt_runtime()

    assert len(controller._peer_runtime.policy_calls) == 1
    assert controller._peer_runtime.policy_calls[0]["desired_active"] is True
    assert controller._peer_runtime.warmup_calls == 0


@pytest.mark.asyncio
async def test_refresh_peer_stt_runtime_blocks_peer_local_qwen_until_local_runtime_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()
    controller._local_stt_install_state = controller_module.LocalSTTInstallState(status="missing")

    download_requests: list[str] = []

    monkeypatch.setattr(
        GuiController,
        "_start_local_stt_download",
        lambda self, *, origin: download_requests.append(origin) or True,
    )

    await controller._refresh_peer_stt_runtime()

    assert len(controller._peer_runtime.policy_calls) == 1
    assert controller._peer_runtime.policy_calls[0]["desired_active"] is False
    assert download_requests == ["manual"]
    assert dash.local_stt_notice_status == "missing"
    assert dash.stt_enabled is None
    assert dash.stt_needs_key is None


@pytest.mark.asyncio
async def test_peer_local_qwen_download_completion_resumes_peer_runtime_after_refresh_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()
    controller._local_stt_install_state = controller_module.LocalSTTInstallState(status="missing")

    download_requests: list[str] = []

    monkeypatch.setattr(
        GuiController,
        "_start_local_stt_download",
        lambda self, *, origin: download_requests.append(origin) or True,
    )

    await controller._refresh_peer_stt_runtime()

    async def fake_install(*, locale: str, on_status, cancel_event) -> object:
        _ = (locale, on_status, cancel_event)
        return object()

    class SuccessfulPeerSession:
        async def close(self) -> None:
            return None

    class SuccessfulPeerBackend:
        async def open_session(self):
            return SuccessfulPeerSession()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(
        controller_module,
        "create_peer_stt_backend",
        lambda *_a, **_k: SuccessfulPeerBackend(),
    )

    await controller._run_local_stt_download(origin="manual")

    assert download_requests == ["manual"]
    assert [call["desired_active"] for call in controller._peer_runtime.policy_calls] == [
        False,
        True,
    ]
    assert dash.local_stt_notice_status is None


@pytest.mark.asyncio
async def test_refresh_peer_stt_runtime_blocks_peer_local_qwen_when_probe_load_fails_despite_ready_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()
    controller._peer_runtime = DummyPeerRuntime()
    controller._local_stt_install_state = controller_module.LocalSTTInstallState(status="ready")

    download_requests: list[str] = []

    class FailingPeerBackend:
        async def open_session(self):
            raise controller_module.LocalQwenSherpaLoadError("bootstrap failed")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(
        controller_module,
        "create_peer_stt_backend",
        lambda *_a, **_k: FailingPeerBackend(),
    )
    monkeypatch.setattr(
        GuiController,
        "_start_local_stt_download",
        lambda self, *, origin: download_requests.append(origin) or True,
    )

    await controller._refresh_peer_stt_runtime()

    assert len(controller._peer_runtime.policy_calls) == 1
    assert controller._peer_runtime.policy_calls[0]["desired_active"] is False
    assert download_requests == ["manual"]
    assert dash.local_stt_notice_status == "invalid"


@pytest.mark.asyncio
async def test_create_peer_audio_source_from_runtime_config_uses_desktop_loopback_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    config = controller._build_peer_runtime_config(AppSettings())
    opened: list[dict[str, object]] = []

    class FakePeerSource:
        pass

    monkeypatch.setattr(
        controller_module,
        "DesktopLoopbackAudioSource",
        lambda *args, **kwargs: opened.append(kwargs) or object(),
    )
    monkeypatch.setattr(
        controller_module,
        "DesktopPeerPipeline",
        lambda *args, **kwargs: FakePeerSource(),
    )

    source = controller._create_peer_audio_source_from_runtime_config(config)

    assert isinstance(source, FakePeerSource)
    assert opened == [{"device_name": config.output_device}]


@pytest.mark.asyncio
async def test_refresh_overlay_runtime_dependencies_applies_peer_runtime_policy() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=None)
    controller.overlay_state = "connected"
    controller._overlay_bridge = object()

    peer_runtime = DummyPeerRuntime()
    controller._peer_runtime = peer_runtime

    await controller._refresh_overlay_runtime_dependencies()

    assert len(peer_runtime.policy_calls) == 1
    assert peer_runtime.policy_calls[0]["desired_active"] is True


@pytest.mark.asyncio
async def test_refresh_overlay_runtime_dependencies_disables_peer_runtime_when_overlay_fails() -> (
    None
):
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.overlay_state = "failed"
    controller._overlay_bridge = None

    peer_runtime = DummyPeerRuntime()
    controller._peer_runtime = peer_runtime

    await controller._refresh_overlay_runtime_dependencies()

    assert peer_runtime.policy_calls[-1]["desired_active"] is False
    assert controller.hub.replace_stt_calls == []


def test_dashboard_stt_needs_key_remains_self_oriented_when_peer_provider_differs() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.LOCAL_QWEN
    controller.settings.provider.peer_stt = STTProviderName.DEEPGRAM

    assert controller._dashboard_stt_needs_key(stt_available=True) is False


@pytest.mark.asyncio
async def test_create_peer_vad_from_runtime_config_uses_shared_peer_vad_policy_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    settings = AppSettings()
    settings.desktop_audio.vad_speech_threshold = 0.72
    settings.desktop_audio.vad_hangover_ms = 950
    settings.desktop_audio.vad_pre_roll_ms = 420
    config = controller._build_peer_runtime_config(settings)

    helper_calls: list[dict[str, object]] = []
    engine = object()

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
        return "peer-vad"

    monkeypatch.setattr(controller_module, "SileroVadOnnx", lambda *args, **kwargs: engine)
    monkeypatch.setattr(controller_module, "create_peer_vad_gating", fake_create_peer_vad_gating)

    vad = controller._create_peer_vad_from_runtime_config(config, Path("vad.onnx"))

    assert vad == "peer-vad"
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
async def test_overlay_toggle_starts_and_stops_overlay_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)

    manager = FakeOverlayProcessManager.instances[0]
    bridge = FakeOverlayBridge.instances[0]

    assert controller.settings.ui.overlay_enabled is True
    assert controller.overlay_state == "starting"
    assert controller.hub.overlay_sink is controller._overlay_presenter
    assert bridge.started is True

    manager.complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    assert controller.failure_reason is None

    await controller.set_overlay_enabled(False)

    assert controller.settings.ui.overlay_enabled is False
    assert controller.overlay_state == "off"
    assert controller.hub.overlay_sink is None
    assert controller.hub.reset_overlay_preview_calls == 1
    assert bridge.stopped is True
    assert manager.stop_calls == 1


@pytest.mark.asyncio
async def test_overlay_toggle_does_not_persist_transient_button_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_calls: list[str] = []
    controller = _make_controller(app=SimpleNamespace(refresh_overlay_peer_contract=lambda: None))
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    async def fake_begin_overlay_start(self: GuiController) -> None:
        _ = self

    async def fake_shutdown_overlay_runtime(
        self: GuiController, *, preserve_failure_reason: bool
    ) -> None:
        _ = (self, preserve_failure_reason)

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: save_calls.append("save"))
    monkeypatch.setattr(GuiController, "_begin_overlay_start", fake_begin_overlay_start)
    monkeypatch.setattr(GuiController, "_shutdown_overlay_runtime", fake_shutdown_overlay_runtime)

    await controller.set_overlay_enabled(True)
    await controller.set_overlay_enabled(False)

    assert save_calls == []
    assert controller.settings.ui.overlay_enabled is False


@pytest.mark.asyncio
async def test_peer_translation_toggle_does_not_persist_transient_button_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_calls: list[str] = []
    controller = _make_controller(app=SimpleNamespace(refresh_overlay_peer_contract=lambda: None))
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller.overlay_state = "connected"

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: save_calls.append("save"))
    monkeypatch.setattr(
        GuiController,
        "_refresh_overlay_runtime_dependencies",
        lambda self: asyncio.sleep(0),
    )

    await controller.set_peer_translation_enabled(True)
    await controller.set_peer_translation_enabled(False)

    assert save_calls == []
    assert controller.settings.ui.peer_translation_enabled is False


@pytest.mark.asyncio
async def test_successful_overlay_start_refreshes_consumers_after_peer_runtime_becomes_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    contracts = []
    app = SimpleNamespace()
    controller = _make_controller(app=app)

    def refresh_overlay_peer_contract() -> None:
        contract = controller.build_overlay_peer_consumer_contract()
        if contract is not None:
            contracts.append(contract)

    def on_overlay_state_changed(*, state: str, failure_reason: str | None = None) -> None:
        app.overlay_state = state
        app.overlay_failure_reason = failure_reason
        refresh_overlay_peer_contract()

    app.refresh_overlay_peer_contract = refresh_overlay_peer_contract
    app.on_overlay_state_changed = on_overlay_state_changed
    controller._ui_event_bridge = SimpleNamespace(
        report_overlay_state=lambda state, failure_reason=None: on_overlay_state_changed(
            state=state,
            failure_reason=failure_reason,
        )
    )
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(peer_stt=None)

    async def fake_refresh_peer_stt_runtime(self: GuiController) -> None:
        self.hub.peer_stt = object()

    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)

    manager = FakeOverlayProcessManager.instances[0]
    manager.complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    assert len(contracts) >= 2
    assert any(contract.peer.warning_reason == "runtime_unavailable" for contract in contracts)
    assert contracts[-1].peer.state == "on"
    assert contracts[-1].peer.helper_text == ""


@pytest.mark.asyncio
async def test_overlay_toggle_off_sends_shutdown_event_before_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.overlay_enabled = True
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)

    manager = FakeOverlayProcessManager.instances[0]
    bridge = FakeOverlayBridge.instances[0]
    manager.complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    assert presenter is not None
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=uuid4(),
            channel="self",
            created_at=10.0,
            text="discard me",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )

    await controller.set_overlay_enabled(False)

    assert bridge.shutdown_calls == 1
    assert bridge.snapshots[-1].blocks == []
    assert manager.stop_calls == 1


@pytest.mark.asyncio
async def test_overlay_restart_reuses_presenter_scene_for_new_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    assert presenter is not None

    utterance_id = uuid4()
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=utterance_id,
            channel="self",
            created_at=10.0,
            text="persist me",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )
    saved_snapshot = presenter.snapshot()

    await controller._teardown_overlay_runtime(preserve_presenter_state=True)

    assert controller._overlay_presenter is presenter
    assert controller.hub.overlay_sink is presenter

    controller.overlay_state = "failed"
    await controller._begin_overlay_start()
    await _wait_until(lambda: len(FakeOverlayBridge.instances) == 2)

    assert FakeOverlayBridge.instances[1].initial_snapshot == saved_snapshot
    assert controller._overlay_presenter is presenter


@pytest.mark.asyncio
async def test_explicit_overlay_disable_resets_presenter_scene_for_next_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    assert presenter is not None
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=uuid4(),
            channel="self",
            created_at=10.0,
            text="discard me",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )

    await controller.set_overlay_enabled(False)

    assert controller._overlay_presenter is None
    assert FakeOverlayBridge.instances[0].snapshots[-1].blocks == []

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayBridge.instances) == 2)

    assert FakeOverlayBridge.instances[1].initial_snapshot.blocks == []


@pytest.mark.asyncio
async def test_refresh_overlay_runtime_dependencies_does_not_clear_overlay_scene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub(peer_stt=object())

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    bridge = FakeOverlayBridge.instances[0]
    assert presenter is not None

    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=uuid4(),
            channel="self",
            created_at=10.0,
            text="stay visible",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )
    saved_snapshot = bridge.snapshots[-1]
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        controller.settings
    )

    await controller._refresh_overlay_runtime_dependencies()

    assert bridge.snapshots[-1] == saved_snapshot


@pytest.mark.asyncio
async def test_explicit_overlay_off_clears_saved_peer_translation_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.overlay_enabled = True
    controller.settings.ui.peer_translation_enabled = True

    await controller.set_overlay_enabled(False)

    assert controller.settings.ui.overlay_enabled is False
    assert controller.settings.ui.peer_translation_enabled is False


def test_effective_context_mode_falls_back_to_local_until_peer_translation_is_effective() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.integrated_context_enabled = True
    controller.hub = DummyHub(peer_stt=object())

    assert controller.effective_context_mode == "local"

    controller.overlay_state = "connected"
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True

    assert controller.effective_context_mode == "integrated"

    controller.settings.ui.peer_translation_enabled = False

    assert controller.effective_context_mode == "local"


@pytest.mark.asyncio
async def test_overlay_start_failure_keeps_saved_preferences_but_effective_state_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)

    manager = FakeOverlayProcessManager.instances[0]
    manager.complete_startup(failure_reason="renderer_init_failed")
    await _wait_until(lambda: controller.overlay_state == "failed")

    assert controller.settings.ui.overlay_enabled is True
    assert controller.failure_reason == "renderer_init_failed"
    assert controller.effective_peer_translation_enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_reason",
    [
        "stale_overlay_build",
        "vendored_openvr_dll_missing",
        "packaged_openvr_dll_missing",
        "openvr_dll_hash_mismatch",
        "steamvr_not_installed",
        "steamvr_not_running",
        "hmd_not_found",
    ],
)
async def test_overlay_start_failure_preserves_specific_preflight_reason(
    monkeypatch: pytest.MonkeyPatch,
    failure_reason: str,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)

    manager = FakeOverlayProcessManager.instances[0]
    manager.complete_startup(failure_reason=failure_reason)
    await _wait_until(lambda: controller.overlay_state == "failed")

    assert controller.settings.ui.overlay_enabled is True
    assert controller.failure_reason == failure_reason


@pytest.mark.asyncio
async def test_overlay_runtime_disconnect_keeps_saved_preferences_without_auto_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_peer_stt_backend", lambda *_a, **_k: "peer")
    monkeypatch.setattr(controller_module, "ManagedSTTProvider", lambda *a, **k: "peer-stt")

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(peer_stt=object())

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    manager = FakeOverlayProcessManager.instances[0]
    manager.complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")
    assert controller.hub.peer_translation_enabled is True

    manager.trigger_runtime_failure("runtime_disconnected")
    await _wait_until(lambda: controller.overlay_state == "failed")

    assert controller.settings.ui.overlay_enabled is True
    assert controller.settings.ui.peer_translation_enabled is True
    assert controller.failure_reason == "runtime_disconnected"
    assert controller.effective_peer_translation_enabled is False
    assert controller.hub.peer_translation_enabled is False
    assert controller.auto_restart_scheduled is False


@pytest.mark.asyncio
async def test_overlay_runtime_crash_keeps_saved_preferences_without_auto_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_peer_stt_backend", lambda *_a, **_k: "peer")
    monkeypatch.setattr(controller_module, "ManagedSTTProvider", lambda *a, **k: "peer-stt")

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(peer_stt=object())

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    manager = FakeOverlayProcessManager.instances[0]
    manager.complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")
    assert controller.hub.peer_translation_enabled is True

    manager.trigger_runtime_failure("runtime_crashed")
    await _wait_until(lambda: controller.overlay_state == "failed")

    assert controller.settings.ui.overlay_enabled is True
    assert controller.settings.ui.peer_translation_enabled is True
    assert controller.failure_reason == "runtime_crashed"
    assert controller.hub.peer_translation_enabled is False
    assert controller.auto_restart_scheduled is False


def test_overlay_runtime_crash_logs_state_transition() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.overlay_state = "connected"
    controller._overlay_manager = SimpleNamespace(state="failed")
    controller._overlay_presenter = object()  # type: ignore[assignment]
    controller._overlay_bridge = object()  # type: ignore[assignment]

    controller.on_overlay_runtime_crashed()

    assert controller.overlay_state == "failed"
    assert controller._runtime_logging.basic_messages == [
        (
            logging.INFO,
            "[Overlay] State transition: connected -> failed failure_reason=runtime_crashed",
        )
    ]
    assert controller._runtime_logging.detailed_messages == [
        (
            logging.INFO,
            "[Overlay] State detail: presenter_attached=True bridge_attached=True manager_state=failed",
        )
    ]


@pytest.mark.asyncio
async def test_run_overlay_start_preserves_traceback_in_detailed_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)

    async def failing_start(self) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(FakeOverlayBridge, "start", failing_start)

    controller = _make_controller(app=SimpleNamespace())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller._run_overlay_start()

    assert controller._runtime_logging.basic_messages == [
        (logging.INFO, "[Overlay] State transition: off -> failed failure_reason=unknown")
    ]
    assert len(controller._runtime_logging.detailed_messages) >= 1
    level, message = controller._runtime_logging.detailed_messages[0]
    assert level == logging.ERROR
    assert "[Overlay] Failed to start overlay runtime" in message
    assert "Traceback (most recent call last):" in message
    assert "RuntimeError: boom" in message


@pytest.mark.asyncio
async def test_overlay_successful_recovery_clears_previous_failure_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", lambda self: asyncio.sleep(0)
    )

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup(failure_reason="bridge_auth_failed")
    await _wait_until(lambda: controller.overlay_state == "failed")

    assert controller.failure_reason == "bridge_auth_failed"

    await controller.set_overlay_enabled(False)
    assert controller.overlay_state == "off"
    assert controller.failure_reason == "bridge_auth_failed"

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 2)
    FakeOverlayProcessManager.instances[1].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    assert controller.failure_reason is None


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
async def test_stop_closes_peer_runtime_without_replacing_self_stt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    controller._peer_runtime = DummyPeerRuntime()

    monkeypatch.setattr(GuiController, "set_stt_enabled", lambda self, value: asyncio.sleep(0))
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        lambda self, enabled: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        GuiController,
        "_shutdown_overlay_runtime",
        lambda self, preserve_failure_reason: asyncio.sleep(0),
    )

    await controller.stop()

    assert controller._peer_runtime is None
    assert controller.hub is None


@pytest.mark.asyncio
async def test_stop_closes_runtime_logging_service(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _make_controller(app=SimpleNamespace())
    events: list[str] = []

    class FakeRuntimeLogging:
        def close(self) -> None:
            events.append("runtime_logging_close")

    monkeypatch.setattr(GuiController, "set_stt_enabled", lambda self, value: asyncio.sleep(0))
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        lambda self, enabled: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        GuiController,
        "_shutdown_overlay_runtime",
        lambda self, preserve_failure_reason: asyncio.sleep(0),
    )

    controller._runtime_logging = FakeRuntimeLogging()

    await controller.stop()

    assert events == ["runtime_logging_close"]
    assert controller._runtime_logging is None


def test_log_error_fallback_does_not_append_duplicate_ui_line(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logs = DummyLogsView()
    controller = _make_controller(app=SimpleNamespace(view_logs=logs))
    controller._runtime_logging = RuntimeLoggingSpy(basic_error=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger=controller_module.logger.name):
        controller._log_error("shared failure")

    assert logs.logs == []
    assert any("shared failure" in message for message in caplog.messages)


def test_overlay_state_transition_routes_snapshot_details_to_detailed_log() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.failure_reason = "runtime_crashed"
    controller._overlay_presenter = object()
    controller._overlay_bridge = object()
    controller._overlay_manager = SimpleNamespace(state="failed")

    controller._log_overlay_state_transition("connected", "failed")

    assert controller._runtime_logging.basic_messages == [
        (
            logging.INFO,
            "[Overlay] State transition: connected -> failed failure_reason=runtime_crashed",
        )
    ]
    assert controller._runtime_logging.detailed_messages == [
        (
            logging.INFO,
            "[Overlay] State detail: presenter_attached=True bridge_attached=True manager_state=failed",
        )
    ]


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
async def test_init_pipeline_passes_runtime_logging_to_smart_osc_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    created = _patch_init_pipeline_dependencies(monkeypatch)

    await controller._init_pipeline()

    assert created["osc_kwargs"]["runtime_logging"] is controller.runtime_logging


@pytest.mark.asyncio
async def test_start_mic_loop_normalizes_wasapi_compatibility_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.audio.input_host_api = WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    controller.settings.audio.input_device = "Compat Mic"
    controller.hub = DummyHub()
    resolve_calls: list[dict[str, object]] = []
    source_calls: list[dict[str, object]] = []

    class FakeSource:
        async def close(self) -> None:
            return None

    def fake_resolve(*, host_api: str, device: str) -> int:
        resolve_calls.append({"host_api": host_api, "device": device})
        return 7

    def fake_source(*_args, **kwargs) -> FakeSource:
        source_calls.append(dict(kwargs))
        return FakeSource()

    async def fake_run_mic_loop(self) -> None:
        _ = self
        return None

    monkeypatch.setattr(controller_module, "ensure_silero_vad_onnx", lambda: Path("vad.onnx"))
    monkeypatch.setattr(controller_module, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "resolve_sounddevice_input_device", fake_resolve)
    monkeypatch.setattr(controller_module, "SoundDeviceAudioSource", fake_source)
    monkeypatch.setattr(GuiController, "_run_mic_loop", fake_run_mic_loop)

    await controller._start_mic_loop()
    await asyncio.sleep(0)

    assert resolve_calls == [{"host_api": WINDOWS_WASAPI_HOST_API, "device": "Compat Mic"}]
    assert source_calls[0]["device"] == 7
    assert source_calls[0].get("wasapi_auto_convert") is True
    assert source_calls[0].get("wasapi_exclusive") is False


@pytest.mark.asyncio
async def test_start_mic_loop_does_not_apply_wasapi_flags_to_name_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.audio.input_host_api = WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    controller.settings.audio.input_device = "Compat Mic"
    controller.hub = DummyHub()
    resolve_calls: list[dict[str, object]] = []
    source_calls: list[dict[str, object]] = []

    class FakeSource:
        async def close(self) -> None:
            return None

    def fake_resolve(*, host_api: str, device: str) -> int:
        resolve_calls.append({"host_api": host_api, "device": device})
        if host_api == WINDOWS_WASAPI_HOST_API:
            return 7
        if host_api == "":
            return 8
        return 99

    def fake_source(*_args, **kwargs) -> FakeSource:
        source_calls.append(dict(kwargs))
        if len(source_calls) == 1:
            raise RuntimeError("first open failed")
        return FakeSource()

    async def fake_run_mic_loop(self) -> None:
        _ = self
        return None

    monkeypatch.setattr(controller_module, "ensure_silero_vad_onnx", lambda: Path("vad.onnx"))
    monkeypatch.setattr(controller_module, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "resolve_sounddevice_input_device", fake_resolve)
    monkeypatch.setattr(controller_module, "SoundDeviceAudioSource", fake_source)
    monkeypatch.setattr(GuiController, "_run_mic_loop", fake_run_mic_loop)

    await controller._start_mic_loop()
    await asyncio.sleep(0)

    assert resolve_calls == [
        {"host_api": WINDOWS_WASAPI_HOST_API, "device": "Compat Mic"},
        {"host_api": "", "device": "Compat Mic"},
    ]
    assert source_calls[0].get("wasapi_auto_convert") is True
    assert source_calls[1]["device"] == 8
    assert source_calls[1].get("wasapi_auto_convert") is False
    assert source_calls[1].get("wasapi_exclusive") is False


@pytest.mark.asyncio
async def test_start_mic_loop_retries_same_device_name_fallback_without_wasapi_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.audio.input_host_api = WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    controller.settings.audio.input_device = "Compat Mic"
    controller.hub = DummyHub()
    resolve_calls: list[dict[str, object]] = []
    source_calls: list[dict[str, object]] = []

    class FakeSource:
        async def close(self) -> None:
            return None

    def fake_resolve(*, host_api: str, device: str) -> int:
        resolve_calls.append({"host_api": host_api, "device": device})
        return 7

    def fake_source(*_args, **kwargs) -> FakeSource:
        source_calls.append(dict(kwargs))
        if len(source_calls) == 1:
            raise RuntimeError("first open failed")
        return FakeSource()

    async def fake_run_mic_loop(self) -> None:
        _ = self
        return None

    monkeypatch.setattr(controller_module, "ensure_silero_vad_onnx", lambda: Path("vad.onnx"))
    monkeypatch.setattr(controller_module, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "resolve_sounddevice_input_device", fake_resolve)
    monkeypatch.setattr(controller_module, "SoundDeviceAudioSource", fake_source)
    monkeypatch.setattr(GuiController, "_run_mic_loop", fake_run_mic_loop)

    await controller._start_mic_loop()
    await asyncio.sleep(0)

    assert resolve_calls == [
        {"host_api": WINDOWS_WASAPI_HOST_API, "device": "Compat Mic"},
        {"host_api": "", "device": "Compat Mic"},
    ]
    assert len(source_calls) == 2
    assert source_calls[0]["device"] == 7
    assert source_calls[0].get("wasapi_auto_convert") is True
    assert source_calls[0].get("wasapi_exclusive") is False
    assert source_calls[1]["device"] == 7
    assert source_calls[1].get("wasapi_auto_convert") is False
    assert source_calls[1].get("wasapi_exclusive") is False


@pytest.mark.asyncio
async def test_start_mic_loop_does_not_apply_wasapi_flags_to_system_default_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.audio.input_host_api = WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    controller.settings.audio.input_device = ""
    controller.hub = DummyHub()
    resolve_calls: list[dict[str, object]] = []
    source_calls: list[dict[str, object]] = []

    class FakeSource:
        async def close(self) -> None:
            return None

    def fake_resolve(*, host_api: str, device: str) -> int:
        resolve_calls.append({"host_api": host_api, "device": device})
        if host_api == WINDOWS_WASAPI_HOST_API:
            return 7
        return 99

    def fake_source(*_args, **kwargs) -> FakeSource:
        source_calls.append(dict(kwargs))
        if len(source_calls) == 1:
            raise RuntimeError("first open failed")
        return FakeSource()

    async def fake_run_mic_loop(self) -> None:
        _ = self
        return None

    monkeypatch.setattr(controller_module, "ensure_silero_vad_onnx", lambda: Path("vad.onnx"))
    monkeypatch.setattr(controller_module, "SileroVadOnnx", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "VadGating", lambda *a, **k: object())
    monkeypatch.setattr(controller_module, "resolve_sounddevice_input_device", fake_resolve)
    monkeypatch.setattr(controller_module, "SoundDeviceAudioSource", fake_source)
    monkeypatch.setattr(GuiController, "_run_mic_loop", fake_run_mic_loop)

    await controller._start_mic_loop()
    await asyncio.sleep(0)

    assert resolve_calls == [{"host_api": WINDOWS_WASAPI_HOST_API, "device": ""}]
    assert source_calls[0].get("wasapi_auto_convert") is True
    assert source_calls[1]["device"] is None
    assert source_calls[1].get("wasapi_auto_convert") is False
    assert source_calls[1].get("wasapi_exclusive") is False


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
    settings.ui.overlay_enabled = False
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
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            bridge_events.append(("init", app, event_queue, runtime_logging))

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

    assert callable(getattr(controller, "set_runtime_logging_mode", None))
    controller.set_runtime_logging_mode("detailed")

    await controller.start()
    await asyncio.sleep(0)

    assert controller.settings is settings
    assert getattr(controller, "runtime_logging", None) is not None
    assert getattr(controller, "runtime_logging_mode", None) == "basic"
    assert sync_calls == ["synced"]
    assert locale_calls == [settings.ui.locale, "apply"]
    assert logs.attach_calls == 0
    assert dash.stt_needs_key is False
    assert dash.translation_needs_key is False
    assert dash.stt_enabled is False
    assert dash.translation_enabled is False
    assert hub.translation_enabled is False
    assert hub.start_calls == [True]
    assert bridge_events[0] == ("init", app, hub.ui_events, controller.runtime_logging)
    assert "run" in bridge_events


@pytest.mark.asyncio
async def test_start_does_not_auto_restore_transient_overlay_or_peer_toggles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.overlay_enabled = True
    settings.ui.peer_translation_enabled = True
    settings.ui.peer_translation_eula_accepted = True

    dash = DummyDashboard()
    logs = DummyLogsView()
    hub = DummyHub(llm=object(), stt=object(), peer_stt=object())
    overlay_calls: list[bool] = []

    async def fake_init_pipeline(self) -> None:
        self.hub = hub

    async def fake_set_overlay_enabled(self: GuiController, enabled: bool) -> None:
        _ = self
        overlay_calls.append(enabled)

    class FakeBridge:
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            _ = (app, event_queue, runtime_logging)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(GuiController, "_load_or_init_settings", lambda self, path: settings)
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(GuiController, "set_overlay_enabled", fake_set_overlay_enabled)
    monkeypatch.setattr(controller_module, "set_locale", lambda _locale: None)
    monkeypatch.setattr(controller_module, "UIEventBridge", FakeBridge)

    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash, view_logs=logs))

    await controller.start()
    await asyncio.sleep(0)

    assert overlay_calls == []
    assert hub.peer_translation_enabled is False


@pytest.mark.asyncio
async def test_set_runtime_logging_mode_updates_overlay_runtime_contract() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.tasks: list[object] = []

        def run_task(self, coro_fn) -> None:
            self.tasks.append(coro_fn)

    class OverlayManagerSpy:
        def __init__(self) -> None:
            self.modes: list[str] = []

        def set_logging_mode(self, mode: str) -> None:
            self.modes.append(mode)

    page = FakePage()
    controller = GuiController(page=page, app=SimpleNamespace(), config_path=Path("settings.json"))
    controller._runtime_logging = RuntimeLoggingSpy(detailed_enabled=False)
    controller._overlay_bridge = FakeOverlayBridge(session_token="token")
    manager = OverlayManagerSpy()
    controller._overlay_manager = manager  # type: ignore[assignment]

    controller.set_runtime_logging_mode("detailed")

    assert controller.runtime_logging_mode == "detailed"
    assert manager.modes == ["detailed"]
    assert len(page.tasks) == 1

    await page.tasks[0]()

    assert controller._overlay_bridge.runtime_control_messages == ["detailed"]


@pytest.mark.asyncio
async def test_start_keeps_managed_openrouter_dashboard_toggle_available_without_local_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.overlay_enabled = False
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    settings.api_key_verified.openrouter = False

    dash = DummyDashboard()
    logs = DummyLogsView()
    settings_view = DummySettingsView()
    hub = DummyHub(llm=object(), stt=object())

    async def fake_init_pipeline(self) -> None:
        self.hub = hub

    monkeypatch.setattr(GuiController, "_load_or_init_settings", lambda self, path: settings)
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(controller_module, "set_locale", lambda _locale: None)
    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )

    async def fail_fetch_key_metadata(_api_key: str):
        raise AssertionError("fetch_key_metadata should not run without a managed key")

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fail_fetch_key_metadata),
    )

    class FakeBridge:
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            _ = (app, event_queue)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(controller_module, "UIEventBridge", FakeBridge)

    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dash, view_logs=logs, view_settings=settings_view)
    )

    await controller.start()
    await asyncio.sleep(0)

    assert dash.translation_needs_key is False
    assert dash.translation_enabled is False
    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": None,
    }
    assert dash.managed_trial_calls == []


@pytest.mark.asyncio
async def test_exhausted_managed_start_and_background_verify_do_not_auto_show_founder_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.overlay_enabled = False
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    settings.managed_identity.active_managed_credential_ref = "hash_123"

    shown: list[str] = []
    dash = DummyDashboard()
    logs = DummyLogsView()
    settings_view = DummySettingsView()
    hub = DummyHub(llm=object(), stt=object())

    async def fake_init_pipeline(self) -> None:
        self.hub = hub

    monkeypatch.setattr(GuiController, "_load_or_init_settings", lambda self, path: settings)
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(controller_module, "set_locale", lambda _locale: None)
    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"openrouter_managed_api_key": "managed-key"}),
    )

    async def fake_fetch_key_metadata(_api_key: str):
        return controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.0007,
            usage_usd=0.0793,
        )

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    class FakeBridge:
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            _ = (app, event_queue, runtime_logging)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(controller_module, "UIEventBridge", FakeBridge)

    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=dash,
            view_logs=logs,
            view_settings=settings_view,
            show_founder_letter_dialog=lambda: shown.append("shown"),
        )
    )

    await controller.start()
    await asyncio.sleep(0)
    await controller._verify_and_update_status()

    assert shown == []


@pytest.mark.asyncio
async def test_refresh_managed_trial_usage_state_uses_settings_view_live_openrouter_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dash, view_settings=settings_view)
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    class DummySecretsForTrial:
        def get(self, key: str) -> str | None:
            if key == "openrouter_managed_api_key":
                return "managed-key"
            return None

    async def fake_fetch_key_metadata(_api_key: str):
        return controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.05,
            usage_usd=0.02,
        )

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecretsForTrial(),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller._refresh_managed_trial_usage_state()

    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": 62,
    }
    assert dash.managed_trial_calls == []


@pytest.mark.asyncio
async def test_refresh_managed_trial_usage_state_computes_remaining_percent_without_usage_usd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dash, view_settings=settings_view)
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    class DummySecretsForTrial:
        def get(self, key: str) -> str | None:
            if key == "openrouter_managed_api_key":
                return "managed-key"
            return None

    async def fake_fetch_key_metadata(_api_key: str):
        return controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.05,
            usage_usd=None,
        )

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecretsForTrial(),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller._refresh_managed_trial_usage_state()

    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": 62,
    }
    assert dash.managed_trial_calls == []


@pytest.mark.asyncio
async def test_refresh_managed_trial_usage_state_marks_usage_unavailable_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dash, view_settings=settings_view)
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    class DummySecretsForTrial:
        def get(self, key: str) -> str | None:
            if key == "openrouter_managed_api_key":
                return "managed-key"
            return None

    metadata_responses = [
        controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.05,
            usage_usd=0.02,
        ),
        None,
    ]

    async def fake_fetch_key_metadata(_api_key: str):
        return metadata_responses.pop(0)

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecretsForTrial(),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller._refresh_managed_trial_usage_state()
    await controller._refresh_managed_trial_usage_state()

    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": None,
    }
    assert dash.managed_trial_calls == []


@pytest.mark.asyncio
async def test_refresh_managed_trial_usage_state_marks_usage_unavailable_when_limit_or_remaining_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dash, view_settings=settings_view)
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())

    class DummySecretsForTrial:
        def get(self, key: str) -> str | None:
            if key == "openrouter_managed_api_key":
                return "managed-key"
            return None

    metadata_responses = [
        controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.05,
            usage_usd=0.02,
        ),
        controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=None,
            usage_usd=0.02,
        ),
    ]

    async def fake_fetch_key_metadata(_api_key: str):
        return metadata_responses.pop(0)

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecretsForTrial(),
    )
    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller._refresh_managed_trial_usage_state()
    await controller._refresh_managed_trial_usage_state()

    assert settings_view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": None,
    }
    assert dash.managed_trial_calls == []


@pytest.mark.asyncio
async def test_refresh_managed_trial_usage_state_auto_shows_founder_letter_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    dash = DummyDashboard()
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=dash,
            view_settings=settings_view,
            show_founder_letter_dialog=lambda: shown.append("shown"),
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    controller.settings.managed_identity.active_managed_credential_ref = "hash_123"
    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_a, **_k: DummySecrets({"openrouter_managed_api_key": "managed-key"}),
    )

    async def fake_fetch_key_metadata(_api_key: str):
        return controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.0007,
            usage_usd=0.0793,
        )

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller._refresh_managed_trial_usage_state()
    await controller._refresh_managed_trial_usage_state()

    assert shown == ["shown"]


@pytest.mark.asyncio
async def test_set_translation_enabled_reopens_founder_letter_on_exhausted_managed_trans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=DummyDashboard(),
            view_settings=DummySettingsView(),
            show_founder_letter_dialog=lambda: shown.append("shown"),
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_a, **_k: DummySecrets({"openrouter_managed_api_key": "managed-key"}),
    )

    async def fake_fetch_key_metadata(_api_key: str):
        return controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.0007,
            usage_usd=0.0793,
        )

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller.set_translation_enabled(True)

    assert shown == ["shown"]
    assert controller.hub.translation_enabled is False


@pytest.mark.asyncio
async def test_set_translation_enabled_exhausted_managed_does_not_prepare_release_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=DummyDashboard(),
            view_settings=DummySettingsView(),
            show_founder_letter_dialog=lambda: None,
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_a, **_k: DummySecrets({"openrouter_managed_api_key": "managed-key"}),
    )

    class DummyService:
        def __init__(self) -> None:
            self.calls = 0

        async def prepare_for_translation(self):
            self.calls += 1
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.READY,
                message_key="managed_release.ready",
                api_key="managed-key",
                local_key_available=True,
            )

    service = DummyService()
    controller._managed_openrouter_release_service = service

    async def fake_fetch_key_metadata(_api_key: str):
        return controller_module.OpenRouterKeyMetadata(
            limit_usd=0.08,
            remaining_usd=0.0007,
            usage_usd=0.0793,
        )

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller.set_translation_enabled(True)

    assert service.calls == 0


@pytest.mark.asyncio
async def test_set_translation_enabled_does_not_route_stale_exhausted_metadata_across_entitlements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=DummyDashboard(),
            view_settings=DummySettingsView(),
            show_founder_letter_dialog=lambda: shown.append("shown"),
        )
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    controller.settings.managed_identity.active_managed_credential_ref = "hash_old"
    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_a, **_k: DummySecrets({"openrouter_managed_api_key": "managed-key"}),
    )

    metadata_calls = 0

    async def fake_fetch_key_metadata(_api_key: str):
        nonlocal metadata_calls
        metadata_calls += 1
        if metadata_calls == 1:
            return controller_module.OpenRouterKeyMetadata(
                limit_usd=0.08,
                remaining_usd=0.0007,
                usage_usd=0.0793,
            )
        raise RuntimeError("metadata boom")

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "fetch_key_metadata",
        staticmethod(fake_fetch_key_metadata),
    )

    await controller._refresh_managed_trial_usage_state()
    assert shown == ["shown"]

    shown.clear()
    controller.settings.managed_identity.active_managed_credential_ref = "hash_new"

    class DummyService:
        def __init__(self) -> None:
            self.calls = 0

        async def prepare_for_translation(self):
            self.calls += 1
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.READY,
                message_key="managed_release.ready",
                api_key="managed-key",
                local_key_available=True,
            )

    service = DummyService()
    controller._managed_openrouter_release_service = service
    monkeypatch.setattr(GuiController, "_schedule_managed_trial_usage_refresh", lambda self: None)

    await controller.set_translation_enabled(True)

    assert shown == []
    assert service.calls == 1
    assert controller.hub.translation_enabled is True


@pytest.mark.asyncio
async def test_set_translation_enabled_returns_when_hub_missing() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()

    await controller.set_translation_enabled(True)


@pytest.mark.asyncio
async def test_set_translation_enabled_logs_non_qwen_provider() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.GEMINI
    controller.hub = DummyHub(llm=object())

    await controller.set_translation_enabled(True)

    assert controller.hub.translation_enabled is True
    assert controller.hub.clear_context_calls == 1
    assert controller._runtime_logging.basic_messages == [
        (logging.INFO, "[Translation] Toggle request: enabled=True"),
        (logging.INFO, "[Translation] Enabled with provider: gemini"),
    ]
    assert controller._runtime_logging.detailed_messages == [
        (
            logging.INFO,
            "[Translation] Toggle detail: current_enabled=True llm_available=True",
        )
    ]


@pytest.mark.asyncio
async def test_set_stt_enabled_marks_promo_and_runs_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.DEEPGRAM
    controller.hub = DummyHub()
    switch_calls: list[bool] = []

    async def fake_ensure_stt_switch(self) -> None:
        switch_calls.append(self._stt_desired)

    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_ensure_stt_switch)

    await controller.set_stt_enabled(True)

    assert controller._stt_desired is True
    assert controller.hub.promo_calls == 1
    assert switch_calls == [True]
    assert controller._runtime_logging.basic_messages == [
        (logging.INFO, "[STT] Toggle request: enabled=True"),
        (logging.INFO, "[STT] Enabled with provider: deepgram"),
    ]
    assert controller._runtime_logging.detailed_messages == [
        (
            logging.INFO,
            "[STT] Toggle detail: desired_before=False overlay_state=off",
        )
    ]


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
    peer_calls: list[str] = []

    class FakeStt:
        async def close(self) -> None:
            backend_calls.append("close")

    class FakePeerStt:
        async def close(self) -> None:
            peer_calls.append("close")

    async def fake_stop_mic_loop(self) -> None:
        _ = self
        stop_calls.append("stop_mic")

    monkeypatch.setattr(GuiController, "_stop_mic_loop", fake_stop_mic_loop)
    controller.hub = DummyHub(stt=FakeStt(), peer_stt=FakePeerStt())

    await controller._run_stt_switch()

    assert stop_calls == ["stop_mic"]
    assert backend_calls == ["close"]
    assert peer_calls == []


@pytest.mark.asyncio
async def test_run_stt_switch_warns_when_hub_missing() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._runtime_logging = RuntimeLoggingSpy()
    controller._stt_desired = True
    controller.hub = None

    await controller._run_stt_switch()

    assert controller._runtime_logging.detailed_messages == [
        (logging.WARNING, "[STT] Enable requested before hub is ready")
    ]


@pytest.mark.asyncio
async def test_run_stt_switch_restart_path_closes_and_warms_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller._stt_desired = True
    controller._stt_restart_requested = True
    calls: list[str] = []
    peer_calls: list[str] = []

    class FakeStt:
        async def close(self) -> None:
            calls.append("close")

        async def warmup(self) -> None:
            calls.append("warmup")

    class FakePeerStt:
        async def close(self) -> None:
            peer_calls.append("close")

        async def warmup(self) -> None:
            peer_calls.append("warmup")

    async def fake_stop_mic_loop(self) -> None:
        _ = self
        calls.append("stop_mic")

    async def fake_start_mic_loop(self) -> None:
        _ = self
        calls.append("start_mic")

    monkeypatch.setattr(GuiController, "_stop_mic_loop", fake_stop_mic_loop)
    monkeypatch.setattr(GuiController, "_start_mic_loop", fake_start_mic_loop)
    controller.hub = DummyHub(stt=FakeStt(), peer_stt=FakePeerStt())

    await controller._run_stt_switch()

    assert calls == ["stop_mic", "close", "start_mic", "warmup"]
    assert peer_calls == []


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
async def test_apply_settings_replaces_stt_provider_when_source_language_changes_and_applies_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.ui.locale = "ja"
    controller = _make_controller(app=SimpleNamespace(apply_locale=lambda: None))
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = "en"
    saved: list[str] = []
    replace_calls: list[str] = []
    pipeline_calls: list[bool] = []
    locale_calls: list[str] = []

    async def fake_replace_runtime_stt_provider(self) -> None:
        _ = self
        replace_calls.append("replace")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        pipeline_calls.append(rebuild_stt)

    monkeypatch.setattr(controller_module, "get_locale", lambda: "en")
    monkeypatch.setattr(controller_module, "set_locale", lambda locale: locale_calls.append(locale))
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: saved.append("saved"))
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)
    controller._last_stt_runtime_signature = ("old",)

    await controller.apply_settings(settings)

    assert saved == ["saved"]
    assert replace_calls == ["replace"]
    assert pipeline_calls == []
    assert locale_calls == ["ja"]


@pytest.mark.asyncio
async def test_apply_settings_source_language_change_reloads_settings_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"], "en": ["Avatar"]}
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_settings=settings_view, apply_locale=lambda: None)
    )
    controller.settings = settings
    controller.overlay_calibration = settings.overlay.calibration.copy()
    controller.hub = DummyHub()
    controller.hub.source_language = "en"
    replace_calls: list[str] = []

    async def fake_replace_runtime_stt_provider(self) -> None:
        _ = self
        replace_calls.append("replace")

    monkeypatch.setattr(controller_module, "get_locale", lambda: settings.ui.locale)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    controller._last_stt_runtime_signature = ("old",)

    await controller.apply_settings(settings)

    assert replace_calls == ["replace"]
    assert settings_view.calls == [(settings, Path("settings.json"), True)]


@pytest.mark.asyncio
async def test_apply_settings_reloads_settings_view_for_target_only_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "en"
    settings.languages.target_language = "ja"
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_settings=settings_view, apply_locale=lambda: None)
    )
    controller.settings = settings
    controller.overlay_calibration = settings.overlay.calibration.copy()
    controller.hub = DummyHub()
    controller.hub.source_language = "en"
    controller.hub.target_language = "ko"

    async def fake_refresh_peer_stt_runtime(self) -> None:
        _ = self

    monkeypatch.setattr(controller_module, "get_locale", lambda: settings.ui.locale)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )

    await controller.apply_settings(settings)

    assert settings_view.calls == [(settings, Path("settings.json"), True)]


@pytest.mark.asyncio
async def test_apply_settings_target_only_change_clears_self_language_runtime_state_without_restarting_stt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )
    controller._last_peer_translation_enabled = settings.ui.peer_translation_enabled
    controller._last_vrc_mic_sync_enabled = settings.osc.vrc_mic_intercept

    replace_calls: list[str] = []
    refresh_peer_calls: list[str] = []

    updated = copy.deepcopy(settings)
    updated.languages.target_language = "ja"

    async def fake_replace_runtime_stt_provider(self) -> None:
        _ = self
        replace_calls.append("replace")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        _ = self
        refresh_peer_calls.append("peer")

    monkeypatch.setattr(controller_module, "get_locale", lambda: settings.ui.locale)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_refresh_local_stt_runtime_state", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_clear_local_stt_pending_enable_if_provider_switched_away",
        lambda self: None,
    )
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)

    await controller.apply_settings(updated)

    assert controller.hub.clear_language_runtime_state_calls == ["self"]
    assert replace_calls == []
    assert refresh_peer_calls == []
    assert controller.hub.target_language == "ja"


@pytest.mark.asyncio
async def test_apply_settings_self_target_change_clears_peer_runtime_when_peer_target_follows_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.peer_source_language = settings.languages.peer_source_language
    controller.hub.peer_target_language = settings.languages.peer_target_language
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )
    controller._last_peer_translation_enabled = settings.ui.peer_translation_enabled
    controller._last_vrc_mic_sync_enabled = settings.osc.vrc_mic_intercept

    refresh_peer_calls: list[str] = []

    updated = copy.deepcopy(settings)
    updated.languages.target_language = "ja"

    async def fake_replace_runtime_stt_provider(self) -> None:
        raise AssertionError("self STT runtime should not restart for target-only change")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        _ = self
        refresh_peer_calls.append("peer")

    monkeypatch.setattr(controller_module, "get_locale", lambda: settings.ui.locale)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_refresh_local_stt_runtime_state", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_clear_local_stt_pending_enable_if_provider_switched_away",
        lambda self: None,
    )
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)

    await controller.apply_settings(updated)

    assert controller.hub.clear_language_runtime_state_calls == ["self", "peer"]
    assert refresh_peer_calls == []


@pytest.mark.asyncio
async def test_apply_settings_self_source_change_clears_peer_runtime_when_peer_source_follows_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.peer_source_language = settings.languages.peer_source_language
    controller.hub.peer_target_language = settings.languages.peer_target_language
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )
    controller._last_peer_translation_enabled = settings.ui.peer_translation_enabled
    controller._last_vrc_mic_sync_enabled = settings.osc.vrc_mic_intercept

    replace_calls: list[str] = []
    refresh_peer_calls: list[str] = []

    updated = copy.deepcopy(settings)
    updated.languages.source_language = "ja"

    async def fake_replace_runtime_stt_provider(self) -> None:
        _ = self
        replace_calls.append("replace")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        _ = self
        refresh_peer_calls.append("peer")

    monkeypatch.setattr(controller_module, "get_locale", lambda: settings.ui.locale)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_refresh_local_stt_runtime_state", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_clear_local_stt_pending_enable_if_provider_switched_away",
        lambda self: None,
    )
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)

    await controller.apply_settings(updated)

    assert controller.hub.clear_language_runtime_state_calls == ["self", "peer"]
    assert replace_calls == ["replace"]
    assert refresh_peer_calls == ["peer"]


@pytest.mark.asyncio
async def test_apply_settings_logs_and_continues_when_language_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.peer_target_language = "fr"
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.peer_source_language = settings.languages.peer_source_language
    controller.hub.peer_target_language = settings.languages.peer_target_language
    controller.hub.clear_language_runtime_state_errors["self"] = RuntimeError("cleanup boom")
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        settings
    )
    controller._last_peer_translation_enabled = settings.ui.peer_translation_enabled
    controller._last_vrc_mic_sync_enabled = settings.osc.vrc_mic_intercept

    errors: list[str] = []

    updated = copy.deepcopy(settings)
    updated.languages.target_language = "ja"

    async def fake_replace_runtime_stt_provider(self) -> None:
        raise AssertionError("self STT runtime should not restart for target-only change")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        raise AssertionError("peer runtime should not refresh for explicit peer target")

    monkeypatch.setattr(controller_module, "get_locale", lambda: settings.ui.locale)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_refresh_local_stt_runtime_state", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_clear_local_stt_pending_enable_if_provider_switched_away",
        lambda self: None,
    )
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(GuiController, "_log_error", lambda self, message: errors.append(message))

    await controller.apply_settings(updated)

    assert controller.hub.clear_language_runtime_state_calls == ["self"]
    assert controller.hub.target_language == "ja"
    assert any("cleanup boom" in message for message in errors)
    assert any("language runtime state" in message for message in errors)


@pytest.mark.asyncio
async def test_apply_settings_reload_updates_overlay_calibration_baseline_without_clobbering_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.overlay.calibration.distance = 0.9
    settings_view = DummySettingsView()
    controller = _make_controller(
        app=SimpleNamespace(view_settings=settings_view, apply_locale=lambda: None)
    )
    controller.settings = settings
    controller.overlay_calibration = settings.overlay.calibration.copy()
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language

    async def fake_replace_runtime_stt_provider(self) -> None:
        _ = self

    async def fake_refresh_peer_stt_runtime(self) -> None:
        _ = self

    monkeypatch.setattr(controller_module, "get_locale", lambda: settings.ui.locale)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)

    controller.begin_overlay_calibration_for_test()
    controller.set_overlay_calibration_field_for_test("distance", 1.2)

    updated = AppSettings()
    updated.languages.source_language = "ja"
    updated.overlay.calibration.distance = 0.8

    await controller.apply_settings(updated)

    assert settings_view.calls == [(updated, Path("settings.json"), True)]
    assert controller.overlay_calibration.distance == 0.8
    assert controller.begin_overlay_calibration().distance == 1.2

    canceled = controller.cancel_overlay_calibration()

    assert canceled.distance == 0.8


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
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.system_prompt = settings.system_prompt
    controller.hub.low_latency_mode = False
    controller.hub.low_latency_merge_gap_ms = settings.stt.low_latency_merge_gap_ms
    controller.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
    controller.hub.hangover_s = 1.1
    controller._last_stt_runtime_signature = ("old",)
    controller._mic_task = object()
    controller._stt_desired = True

    async def fake_rebuild_llm_provider(self) -> None:
        rebuild_llm_calls.append("rebuild_llm")

    async def fake_stop_mic_loop(self) -> None:
        _ = self
        switch_calls.append("stop_mic")

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        receiver_calls.append(enabled)

    async def fake_rebuild_stt_provider(self) -> None:
        _ = self
        switch_calls.append("rebuild_stt")

    async def fake_ensure_stt_switch(self) -> None:
        _ = self
        switch_calls.append("switch")

    monkeypatch.setattr(controller_module, "get_locale", lambda: "en")
    monkeypatch.setattr(controller_module, "set_locale", lambda locale: locale_calls.append(locale))
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(GuiController, "_stop_mic_loop", fake_stop_mic_loop)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild_stt_provider)
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_ensure_stt_switch)
    monkeypatch.setattr(GuiController, "_log_error", lambda self, message: errors.append(message))

    await controller.apply_settings(settings)

    assert rebuild_llm_calls == ["rebuild_llm"]
    assert receiver_calls == [True]
    assert controller._stt_restart_requested is False
    assert switch_calls == ["stop_mic", "rebuild_stt", "switch"]
    assert locale_calls == ["ko"]
    assert controller.hub.low_latency_mode is True
    assert any("Failed to apply locale: locale boom" in message for message in errors)


@pytest.mark.asyncio
async def test_apply_settings_rebuilds_stt_provider_when_runtime_changes_while_stt_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.DEEPGRAM

    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = settings

    close_calls: list[str] = []
    switch_calls: list[str] = []
    backend_calls: list[str] = []
    new_stt = object()

    class OldStt:
        async def close(self) -> None:
            close_calls.append("close")

    controller.hub = DummyHub(stt=OldStt())
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.system_prompt = settings.system_prompt
    controller.hub.low_latency_mode = settings.stt.low_latency_mode
    controller.hub.low_latency_merge_gap_ms = settings.stt.low_latency_merge_gap_ms
    controller.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
    controller.hub.hangover_s = 1.1
    controller._last_stt_runtime_signature = controller._build_stt_runtime_signature(settings)
    controller._stt_desired = False
    controller._mic_task = None

    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {"ko": ["Puripuly"]}

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = (self, enabled)

    async def fake_ensure_stt_switch(self) -> None:
        _ = self
        switch_calls.append("switch")

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_ensure_stt_switch)
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(
        controller_module,
        "create_stt_backend",
        lambda current_settings, **_kwargs: backend_calls.append(
            current_settings.languages.source_language
        )
        or "backend",
    )
    monkeypatch.setattr(controller_module, "ManagedSTTProvider", lambda *a, **k: new_stt)

    await controller.apply_settings(settings)

    assert close_calls == ["close"]
    assert backend_calls == ["ko"]
    assert controller.hub.stt is new_stt
    assert controller.hub.replace_stt_calls == [new_stt]
    assert switch_calls == []
    assert dash.stt_needs_key is False


@pytest.mark.asyncio
async def test_apply_settings_replaces_running_stt_provider_for_custom_vocabulary_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.DEEPGRAM

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = DummyHub(stt=object())
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.system_prompt = settings.system_prompt
    controller.hub.low_latency_mode = settings.stt.low_latency_mode
    controller.hub.low_latency_merge_gap_ms = settings.stt.low_latency_merge_gap_ms
    controller.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
    controller.hub.hangover_s = 1.1
    controller._last_stt_runtime_signature = controller._build_stt_runtime_signature(settings)
    controller._stt_desired = True
    controller._mic_task = object()

    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {"ko": ["Puripuly", "VRChat"]}

    calls: list[str] = []

    async def fake_stop_mic_loop(self) -> None:
        _ = self
        calls.append("stop_mic")

    async def fake_rebuild_stt_provider(self) -> None:
        _ = self
        calls.append("rebuild_stt")

    async def fake_ensure_stt_switch(self) -> None:
        _ = self
        calls.append("switch")

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = (self, enabled)

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_stop_mic_loop", fake_stop_mic_loop)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild_stt_provider)
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_ensure_stt_switch)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )

    await controller.apply_settings(settings)

    assert calls == ["stop_mic", "rebuild_stt", "switch"]
    assert controller._stt_restart_requested is False


@pytest.mark.asyncio
async def test_apply_settings_does_not_restart_stt_for_qwen_custom_vocabulary_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.QWEN_ASR

    replace_calls: list[str] = []

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.system_prompt = settings.system_prompt
    controller.hub.low_latency_mode = settings.stt.low_latency_mode
    controller.hub.low_latency_merge_gap_ms = settings.stt.low_latency_merge_gap_ms
    controller.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
    controller.hub.hangover_s = 1.1
    controller._last_stt_runtime_signature = controller._build_stt_runtime_signature(settings)
    controller._stt_desired = True
    controller._mic_task = object()

    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {"ko": ["Puripuly", "VRChat"]}

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = (self, enabled)

    async def fake_replace_runtime_stt_provider(self) -> None:
        _ = self
        replace_calls.append("replace")

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )

    await controller.apply_settings(settings)

    assert controller._stt_restart_requested is False
    assert replace_calls == []


@pytest.mark.asyncio
async def test_apply_settings_restarts_stt_for_local_qwen_custom_vocabulary_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN

    replace_calls: list[str] = []

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = DummyHub()
    controller.hub.source_language = settings.languages.source_language
    controller.hub.target_language = settings.languages.target_language
    controller.hub.system_prompt = settings.system_prompt
    controller.hub.low_latency_mode = settings.stt.low_latency_mode
    controller.hub.low_latency_merge_gap_ms = settings.stt.low_latency_merge_gap_ms
    controller.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
    controller.hub.hangover_s = 1.1
    controller._last_stt_runtime_signature = controller._build_stt_runtime_signature(settings)
    controller._stt_desired = True
    controller._mic_task = object()

    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {"ko": ["Puripuly", "VRChat"]}

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = (self, enabled)

    async def fake_replace_runtime_stt_provider(self) -> None:
        _ = self
        replace_calls.append("replace")

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )

    await controller.apply_settings(settings)

    assert controller._stt_restart_requested is False
    assert replace_calls == ["replace"]


@pytest.mark.asyncio
async def test_apply_settings_skips_vrc_sync_when_setting_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.QWEN_ASR

    receiver_calls: list[bool] = []

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = settings
    controller.hub = SimpleNamespace(
        source_language=settings.languages.source_language,
        target_language=settings.languages.target_language,
        system_prompt=settings.system_prompt,
        low_latency_mode=settings.stt.low_latency_mode,
        low_latency_merge_gap_ms=settings.stt.low_latency_merge_gap_ms,
        low_latency_spec_retry_max=settings.stt.low_latency_spec_retry_max,
        hangover_s=1.1,
        peer_stt=None,
    )
    controller._last_stt_runtime_signature = controller._build_stt_runtime_signature(settings)
    controller._last_vrc_mic_sync_enabled = settings.osc.vrc_mic_intercept

    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {"ko": ["Puripuly"]}

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = self
        receiver_calls.append(enabled)

    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )

    with caplog.at_level(logging.INFO, logger=controller_module.logger.name):
        await controller.apply_settings(settings)

    assert receiver_calls == []
    assert all("VRC mic sync enabled" not in record.message for record in caplog.records)


@pytest.mark.parametrize(
    ("provider", "result", "expected"),
    [
        ("deepgram", True, (True, "Verification successful")),
        ("deepgram", False, (False, "Verification failed (check logs/console for details)")),
        ("openrouter", True, (True, "Verification successful")),
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
    monkeypatch.setattr(OpenRouterLLMProvider, "verify_api_key", staticmethod(fake_verify))
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
async def test_create_openrouter_pkce_client_uses_openrouter_documented_localhost_port() -> None:
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=DummyDashboard(), view_settings=DummySettingsView())
    )

    client = controller._create_openrouter_pkce_client()
    session = client.build_session()

    assert client.callback_origin == "http://localhost:3000"
    assert "callback_url=http%3A%2F%2Flocalhost%3A3000%2Fcallback" in session.authorization_url


@pytest.mark.asyncio
async def test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=DummyDashboard(), view_settings=DummySettingsView())
    )
    controller.settings = AppSettings()
    target_settings = copy.deepcopy(controller.settings)
    target_settings.provider.llm = LLMProviderName.OPENROUTER
    target_settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    target_settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    target_settings.openrouter.llm_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    store = DummySecrets({"openrouter_api_key": "legacy-key"})

    class DummyPKCEClient:
        async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
            return OpenRouterPKCEExchangeResult(api_key="sk-or-v1-user", user_id="user_123")

    monkeypatch.setattr(
        GuiController,
        "_create_openrouter_pkce_client",
        lambda self: DummyPKCEClient(),
    )
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: store)
    verify_calls: list[str] = []

    async def fake_verify_openrouter_api_key(api_key: str) -> bool:
        verify_calls.append(api_key)
        return True

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "verify_api_key",
        fake_verify_openrouter_api_key,
    )
    applied: list[AppSettings] = []

    async def fake_apply_providers(
        self,
        settings: AppSettings | None = None,
        *,
        force_rebuild_llm: bool = False,
    ) -> None:
        _ = self
        assert force_rebuild_llm is True
        assert settings is not None
        applied.append(copy.deepcopy(settings))

    monkeypatch.setattr(GuiController, "apply_providers", fake_apply_providers)

    ok = await controller.connect_openrouter_via_pkce(
        target_settings=target_settings,
        launch_source="settings",
    )

    assert ok is True
    assert store.set_calls[-1] == ("openrouter_api_key", "sk-or-v1-user")
    assert verify_calls == ["sk-or-v1-user"]
    assert applied[-1].openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_BYOK
    assert applied[-1].openrouter.selected_source == OpenRouterCredentialSource.BYOK
    assert applied[-1].api_key_verified.openrouter is True


@pytest.mark.asyncio
async def test_connect_openrouter_via_pkce_rejects_unverified_exchanged_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=DummyDashboard(), view_settings=DummySettingsView())
    )
    controller.settings = AppSettings()
    previous_settings = copy.deepcopy(controller.settings)
    target_settings = copy.deepcopy(controller.settings)
    target_settings.provider.llm = LLMProviderName.OPENROUTER
    target_settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    target_settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    target_settings.openrouter.llm_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    store = DummySecrets({"openrouter_api_key": "legacy-key"})

    class DummyPKCEClient:
        async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
            return OpenRouterPKCEExchangeResult(api_key="sk-or-v1-user", user_id="user_123")

    monkeypatch.setattr(
        GuiController,
        "_create_openrouter_pkce_client",
        lambda self: DummyPKCEClient(),
    )
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: store)
    verify_calls: list[str] = []

    async def fake_verify_openrouter_api_key(api_key: str) -> bool:
        verify_calls.append(api_key)
        return False

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "verify_api_key",
        fake_verify_openrouter_api_key,
    )
    applied: list[AppSettings] = []

    async def fake_apply_providers(
        self,
        settings: AppSettings | None = None,
        *,
        force_rebuild_llm: bool = False,
    ) -> None:
        _ = self
        _ = force_rebuild_llm
        assert settings is not None
        applied.append(copy.deepcopy(settings))

    monkeypatch.setattr(GuiController, "apply_providers", fake_apply_providers)

    ok = await controller.connect_openrouter_via_pkce(
        target_settings=target_settings,
        launch_source="settings",
    )

    assert ok is False
    assert verify_calls == ["sk-or-v1-user"]
    assert applied == []
    assert controller.settings == previous_settings
    assert store.get("openrouter_api_key") == "legacy-key"
    assert store.set_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_connect_openrouter_via_pkce_rebuilds_llm_when_signature_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = DummyDashboard()
    dashboard.translation_needs_key = True
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=dashboard, view_settings=DummySettingsView())
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    controller.settings.openrouter.llm_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    controller.hub = DummyHub(llm=None)
    controller._sync_signature_caches(controller.settings)
    target_settings = copy.deepcopy(controller.settings)
    store = DummySecrets({})

    class DummyPKCEClient:
        async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
            return OpenRouterPKCEExchangeResult(api_key="sk-or-v1-user", user_id="user_123")

    monkeypatch.setattr(
        GuiController,
        "_create_openrouter_pkce_client",
        lambda self: DummyPKCEClient(),
    )
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: store)

    class DummyManagedReleaseService:
        async def close(self) -> None:
            return None

    def fake_create_managed_release_service(self, *, secrets):
        _ = (self, secrets)
        return DummyManagedReleaseService()

    monkeypatch.setattr(
        GuiController,
        "_create_managed_openrouter_release_service",
        fake_create_managed_release_service,
    )
    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_verify_openrouter_api_key(_api_key: str) -> bool:
        return True

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "verify_api_key",
        fake_verify_openrouter_api_key,
    )
    created_llm: list[str] = []

    def fake_create_llm_provider(*_args, **_kwargs):
        created_llm.append(store.get("openrouter_api_key") or "")
        return "rebuilt-llm"

    monkeypatch.setattr(controller_module, "create_llm_provider", fake_create_llm_provider)

    async def fake_refresh_managed_trial_usage_state_best_effort(self) -> None:
        _ = self

    monkeypatch.setattr(
        GuiController,
        "_refresh_managed_trial_usage_state_best_effort",
        fake_refresh_managed_trial_usage_state_best_effort,
    )

    ok = await controller.connect_openrouter_via_pkce(
        target_settings=target_settings,
        launch_source="settings",
    )

    assert ok is True
    assert created_llm == ["sk-or-v1-user"]
    assert controller.hub.llm == "rebuilt-llm"
    assert controller.settings.api_key_verified.openrouter is True
    assert dashboard.translation_needs_key is False


def test_reopen_openrouter_pkce_authorization_url_delegates_to_active_client() -> None:
    reopen_calls: list[str] = []
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=DummyDashboard(), view_settings=DummySettingsView())
    )
    controller._openrouter_pkce_client = SimpleNamespace(
        reopen_authorization_url=lambda: reopen_calls.append("reopen") or True
    )

    assert controller.reopen_openrouter_pkce_authorization_url() is True
    assert reopen_calls == ["reopen"]


@pytest.mark.asyncio
async def test_connect_openrouter_via_pkce_leaves_settings_unchanged_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=DummyDashboard(), view_settings=DummySettingsView())
    )
    controller.settings = AppSettings()
    controller.settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_MANAGED
    target_settings = copy.deepcopy(controller.settings)
    target_settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    target_settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    target_settings.openrouter.llm_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    store = DummySecrets({})

    class DummyPKCEClient:
        async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
            raise RuntimeError("browser failed")

    monkeypatch.setattr(
        GuiController,
        "_create_openrouter_pkce_client",
        lambda self: DummyPKCEClient(),
    )
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: store)

    ok = await controller.connect_openrouter_via_pkce(
        target_settings=target_settings,
        launch_source="settings",
    )

    assert ok is False
    assert controller.settings.openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_MANAGED
    assert store.set_calls == []


@pytest.mark.asyncio
async def test_connect_openrouter_via_pkce_reopens_letter_context_on_letter_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    controller = _make_controller(
        app=SimpleNamespace(
            view_dashboard=DummyDashboard(),
            view_settings=DummySettingsView(),
            show_founder_letter_dialog=lambda: shown.append("shown"),
        )
    )
    controller.settings = AppSettings()
    target_settings = copy.deepcopy(controller.settings)
    target_settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    target_settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    target_settings.openrouter.llm_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT

    class DummyPKCEClient:
        async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
            raise RuntimeError("browser failed")

    monkeypatch.setattr(
        GuiController,
        "_create_openrouter_pkce_client",
        lambda self: DummyPKCEClient(),
    )

    ok = await controller.connect_openrouter_via_pkce(
        target_settings=target_settings,
        launch_source="letter",
    )

    assert ok is False
    assert shown == ["shown"]


@pytest.mark.asyncio
async def test_connect_openrouter_via_pkce_rolls_back_secret_and_settings_on_apply_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(
        app=SimpleNamespace(view_dashboard=DummyDashboard(), view_settings=DummySettingsView())
    )
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_MANAGED
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    previous_settings = copy.deepcopy(controller.settings)
    target_settings = copy.deepcopy(controller.settings)
    target_settings.provider.llm = LLMProviderName.OPENROUTER
    target_settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    target_settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    target_settings.openrouter.llm_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    store = DummySecrets({"openrouter_api_key": "legacy-key"})

    class DummyPKCEClient:
        async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
            return OpenRouterPKCEExchangeResult(api_key="sk-or-v1-user", user_id="user_123")

    monkeypatch.setattr(
        GuiController,
        "_create_openrouter_pkce_client",
        lambda self: DummyPKCEClient(),
    )
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: store)

    async def fake_verify_openrouter_api_key(_api_key: str) -> bool:
        return True

    monkeypatch.setattr(
        OpenRouterLLMProvider,
        "verify_api_key",
        fake_verify_openrouter_api_key,
    )

    async def fake_apply_providers(
        self,
        settings: AppSettings | None = None,
        *,
        force_rebuild_llm: bool = False,
    ) -> None:
        assert settings is not None
        assert force_rebuild_llm is True
        self.settings = copy.deepcopy(settings)
        raise RuntimeError("apply failed after mutation")

    monkeypatch.setattr(GuiController, "apply_providers", fake_apply_providers)

    ok = await controller.connect_openrouter_via_pkce(
        target_settings=target_settings,
        launch_source="settings",
    )

    assert ok is False
    assert controller.settings == previous_settings
    assert store.get("openrouter_api_key") == "legacy-key"
    assert store.set_calls == [
        ("openrouter_api_key", "sk-or-v1-user"),
        ("openrouter_api_key", "legacy-key"),
    ]
    assert store.delete_calls == []


def test_merge_settings_tab_apply_with_current_languages_preserves_all_language_fields() -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    controller.settings.openrouter.fallback_selection_alias = (
        OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE
    )
    controller.settings.languages.source_language = "fr"
    controller.settings.languages.target_language = "de"
    controller.settings.languages.peer_source_language = "ja"
    controller.settings.languages.peer_target_language = "it"
    controller.settings.languages.recent_source_languages = ["fr", "ko"]
    controller.settings.languages.recent_target_languages = ["de", "en"]
    controller.hub = DummyHub()
    controller.hub.source_language = "es"
    controller.hub.target_language = "pt"
    controller.hub.peer_source_language = "zh-CN"
    controller.hub.peer_target_language = "nl"

    pending = AppSettings()
    pending.languages.source_language = "ko"
    pending.languages.target_language = "en"
    pending.languages.peer_source_language = ""
    pending.languages.peer_target_language = "ja"
    pending.provider.stt = STTProviderName.SONIOX
    pending.provider.peer_stt = STTProviderName.SONIOX
    pending.provider.llm = LLMProviderName.OPENROUTER
    pending.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    pending.openrouter.selection_alias = OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED
    pending.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.QWEN35_FLASH
    pending.openrouter.routing_mode = OpenRouterRoutingMode.NOVITA_FIRST
    pending.qwen.llm_model = QwenLLMModel.QWEN_35_FLASH
    pending.qwen.region = QwenRegion.SINGAPORE
    pending.managed_identity.verified_hardware_hash = "pending-hash"
    pending.managed_identity.verified_hardware_hash_salt_version = 7
    pending.system_prompt = "draft prompt"
    pending.system_prompts = {"openrouter": "draft prompt"}

    merged = controller.merge_settings_tab_apply_with_current_languages(pending)

    assert merged is not controller.settings
    assert merged is not pending
    assert merged.languages.source_language == "es"
    assert merged.languages.target_language == "pt"
    assert merged.languages.peer_source_language == "zh-CN"
    assert merged.languages.peer_target_language == "nl"
    assert merged.languages.recent_source_languages == ["fr", "ko"]
    assert merged.languages.recent_target_languages == ["de", "en"]
    assert merged.provider.stt == STTProviderName.SONIOX
    assert merged.provider.peer_stt == STTProviderName.SONIOX
    assert merged.provider.llm == LLMProviderName.OPENROUTER
    assert merged.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert merged.openrouter.selection_alias == OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED
    assert (
        merged.openrouter.fallback_selection_alias == OpenRouterFallbackSelectionAlias.QWEN35_FLASH
    )
    assert merged.openrouter.routing_mode == OpenRouterRoutingMode.NOVITA_FIRST
    assert merged.qwen.llm_model == QwenLLMModel.QWEN_35_FLASH
    assert merged.qwen.region == QwenRegion.SINGAPORE
    assert merged.managed_identity.verified_hardware_hash == "pending-hash"
    assert merged.managed_identity.verified_hardware_hash_salt_version == 7
    assert merged.system_prompt == "draft prompt"
    assert merged.system_prompts == {"openrouter": "draft prompt"}
    assert merged.system_prompts is not pending.system_prompts


@pytest.mark.asyncio
async def test_apply_providers_preserves_current_languages_while_applying_provider_and_prompt_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    controller.settings.openrouter.fallback_selection_alias = (
        OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE
    )
    controller.settings.languages.source_language = "fr"
    controller.settings.languages.target_language = "de"
    controller.settings.languages.peer_source_language = "ja"
    controller.settings.languages.peer_target_language = "it"
    controller.settings.languages.recent_source_languages = ["fr", "ko"]
    controller.settings.languages.recent_target_languages = ["de", "en"]
    controller.hub = DummyHub()
    controller.hub.source_language = "es"
    controller.hub.target_language = "pt"
    controller.hub.peer_source_language = "zh-CN"
    controller.hub.peer_target_language = "nl"
    controller._stt_desired = False
    controller._last_self_stt_provider_signature = controller._build_self_stt_provider_signature(
        controller.settings
    )
    controller._last_peer_stt_provider_signature = controller._build_peer_stt_provider_signature(
        controller.settings
    )
    controller._last_llm_provider_signature = controller._build_llm_provider_signature(
        controller.settings
    )
    calls: list[str] = []

    pending = AppSettings()
    pending.languages.source_language = "ko"
    pending.languages.target_language = "en"
    pending.languages.peer_source_language = ""
    pending.languages.peer_target_language = "ja"
    pending.provider.stt = STTProviderName.SONIOX
    pending.provider.peer_stt = STTProviderName.SONIOX
    pending.provider.llm = LLMProviderName.OPENROUTER
    pending.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    pending.openrouter.selection_alias = OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED
    pending.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.QWEN35_FLASH
    pending.openrouter.routing_mode = OpenRouterRoutingMode.NOVITA_FIRST
    pending.managed_identity.verified_hardware_hash = "pending-hash"
    pending.managed_identity.verified_hardware_hash_salt_version = 5
    pending.system_prompt = "draft prompt"
    pending.system_prompts = {"openrouter": "draft prompt"}

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_rebuild_stt_provider(self) -> None:
        calls.append("rebuild_stt")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        calls.append("peer")

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild_stt_provider)
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)

    await controller.apply_providers(pending)

    assert controller.settings.languages.source_language == "es"
    assert controller.settings.languages.target_language == "pt"
    assert controller.settings.languages.peer_source_language == "zh-CN"
    assert controller.settings.languages.peer_target_language == "nl"
    assert controller.settings.languages.recent_source_languages == ["fr", "ko"]
    assert controller.settings.languages.recent_target_languages == ["de", "en"]
    assert controller.hub.source_language == "es"
    assert controller.hub.target_language == "pt"
    assert controller.hub.peer_source_language == "zh-CN"
    assert controller.hub.peer_target_language == "nl"
    assert controller.settings.provider.stt == STTProviderName.SONIOX
    assert controller.settings.provider.peer_stt == STTProviderName.SONIOX
    assert controller.settings.provider.llm == LLMProviderName.OPENROUTER
    assert controller.settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert (
        controller.settings.openrouter.selection_alias
        == OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED
    )
    assert (
        controller.settings.openrouter.fallback_selection_alias
        == OpenRouterFallbackSelectionAlias.QWEN35_FLASH
    )
    assert controller.settings.openrouter.routing_mode == OpenRouterRoutingMode.NOVITA_FIRST
    assert controller.settings.managed_identity.verified_hardware_hash == "pending-hash"
    assert controller.settings.managed_identity.verified_hardware_hash_salt_version == 5
    assert controller.settings.system_prompt == "draft prompt"
    assert controller.settings.system_prompts == {"openrouter": "draft prompt"}
    assert calls == ["llm", "peer", "rebuild_stt"]


@pytest.mark.asyncio
async def test_apply_providers_rebuilds_only_llm_for_openrouter_fallback_alias_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_MANAGED
    controller.settings.openrouter.fallback_selection_alias = (
        OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE
    )
    controller.hub = DummyHub()
    controller._last_self_stt_provider_signature = controller._build_self_stt_provider_signature(
        controller.settings
    )
    controller._last_peer_stt_provider_signature = controller._build_peer_stt_provider_signature(
        controller.settings
    )
    controller._last_llm_provider_signature = controller._build_llm_provider_signature(
        controller.settings
    )
    calls: list[str] = []

    updated = copy.deepcopy(controller.settings)
    updated.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.QWEN35_FLASH

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        calls.append("peer")

    async def fake_replace_runtime_stt_provider(self) -> None:
        calls.append("replace")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_providers(updated)

    assert controller.settings.openrouter.fallback_selection_alias == (
        OpenRouterFallbackSelectionAlias.QWEN35_FLASH
    )
    assert calls == ["llm"]


@pytest.mark.asyncio
async def test_apply_providers_replaces_runtime_self_stt_once_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.hub = DummyHub()
    controller._stt_desired = True
    calls: list[str] = []

    updated = AppSettings()
    updated.provider.stt = STTProviderName.SONIOX

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_replace_runtime_stt_provider(self) -> None:
        calls.append("replace")

    async def fake_rebuild_stt_provider(self) -> None:
        calls.append("rebuild_stt")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        calls.append("peer")

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild_stt_provider)
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_providers(updated)

    assert controller.settings.provider.stt == STTProviderName.SONIOX
    assert calls == ["replace"]


@pytest.mark.asyncio
async def test_on_dashboard_language_change_routes_self_and_peer_updates_through_shared_controller_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.languages.peer_source_language = "zh-CN"
    controller.settings.languages.peer_target_language = "ja"
    captured: list[AppSettings] = []

    async def fake_apply_settings(self, settings: AppSettings) -> None:
        captured.append(settings)

    monkeypatch.setattr(GuiController, "apply_settings", fake_apply_settings)

    await controller.on_dashboard_language_change(
        source_code="fr",
        target_code="de",
        peer_source_code="",
        peer_target_code="it",
    )

    assert controller.settings.languages.source_language == "ko"
    assert controller.settings.languages.target_language == "en"
    assert controller.settings.languages.peer_source_language == "zh-CN"
    assert controller.settings.languages.peer_target_language == "ja"
    assert len(captured) == 1
    assert captured[0].languages.source_language == "fr"
    assert captured[0].languages.target_language == "de"
    assert captured[0].languages.peer_source_language == ""
    assert captured[0].languages.peer_target_language == "it"


@pytest.mark.asyncio
async def test_on_dashboard_language_change_preserves_explicit_peer_override_when_self_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.languages.peer_source_language = "ja"
    controller.settings.languages.peer_target_language = "fr"
    captured: list[AppSettings] = []

    async def fake_apply_settings(self, settings: AppSettings) -> None:
        captured.append(settings)

    monkeypatch.setattr(GuiController, "apply_settings", fake_apply_settings)

    await controller.on_dashboard_language_change(
        source_code="ja",
        target_code="en",
        peer_source_code="ja",
        peer_target_code="fr",
    )

    assert len(captured) == 1
    assert captured[0].languages.source_language == "ja"
    assert captured[0].languages.target_language == "en"
    assert captured[0].languages.peer_source_language == "ja"
    assert captured[0].languages.peer_target_language == "fr"


@pytest.mark.asyncio
async def test_dashboard_peer_language_change_refreshes_peer_translation_pipeline_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.hub = DummyHub()
    controller._last_self_stt_runtime_signature = controller._build_self_stt_runtime_signature(
        controller.settings
    )
    controller._last_peer_stt_runtime_signature = controller._build_peer_stt_runtime_signature(
        controller.settings
    )
    controller._last_peer_translation_enabled = controller.settings.ui.peer_translation_enabled
    refreshed: list[str] = []

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(GuiController, "_refresh_local_stt_runtime_state", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_clear_local_stt_pending_enable_if_provider_switched_away",
        lambda self: None,
    )

    async def fake_refresh_peer_stt_runtime(self) -> None:
        refreshed.append("peer")

    async def fake_replace_runtime_stt_provider(self) -> None:
        raise AssertionError("self STT runtime should not restart for peer-only change")

    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )

    await controller.on_dashboard_language_change(
        source_code="ko",
        target_code="en",
        peer_source_code="ja",
        peer_target_code="fr",
    )

    assert refreshed == ["peer"]
    assert controller.hub.peer_source_language == "ja"
    assert controller.hub.peer_target_language == "fr"


@pytest.mark.asyncio
async def test_apply_providers_rebuilds_self_stt_only_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.hub = DummyHub()
    controller._stt_desired = False
    calls: list[str] = []

    updated = AppSettings()
    updated.provider.stt = STTProviderName.SONIOX

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_replace_runtime_stt_provider(self) -> None:
        calls.append("replace")

    async def fake_rebuild_stt_provider(self) -> None:
        calls.append("rebuild_stt")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild_stt_provider)
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_providers(updated)

    assert calls == ["rebuild_stt"]


@pytest.mark.asyncio
async def test_apply_providers_refreshes_only_peer_runtime_for_peer_provider_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.hub = DummyHub()
    calls: list[str] = []

    updated = AppSettings()
    updated.provider.peer_stt = STTProviderName.SONIOX

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_refresh_peer_stt_runtime(self) -> None:
        calls.append("peer")

    async def fake_replace_runtime_stt_provider(self) -> None:
        calls.append("replace")

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_providers(updated)

    assert calls == ["peer"]


@pytest.mark.asyncio
async def test_apply_providers_republishes_overlay_peer_contract_after_peer_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = SimpleNamespace()
    controller = _make_controller(app=app)
    controller.settings = AppSettings()
    controller.settings.ui.overlay_enabled = True
    controller.settings.ui.peer_translation_enabled = True
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.hub = DummyHub(peer_stt=None)
    controller.overlay_state = "connected"
    contracts = []

    def refresh_overlay_peer_contract() -> None:
        contract = controller.build_overlay_peer_consumer_contract()
        if contract is not None:
            contracts.append(contract)

    app.refresh_overlay_peer_contract = refresh_overlay_peer_contract

    updated = AppSettings()
    updated.ui.overlay_enabled = True
    updated.ui.peer_translation_enabled = True
    updated.ui.peer_translation_eula_accepted = True
    updated.provider.peer_stt = STTProviderName.SONIOX

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_refresh_peer_stt_runtime(self) -> None:
        assert self.hub is not None
        self.hub.peer_stt = object()

    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)

    await controller.apply_providers(updated)

    assert contracts
    assert contracts[-1].peer.state == "on"
    assert contracts[-1].peer.warning_reason is None


@pytest.mark.asyncio
async def test_apply_providers_rebuilds_only_llm_for_openrouter_routing_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.hub = DummyHub()
    calls: list[str] = []

    updated = AppSettings()
    updated.provider.llm = LLMProviderName.OPENROUTER
    updated.openrouter.routing_mode = OpenRouterRoutingMode.PARASAIL_FIRST

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        calls.append("peer")

    async def fake_replace_runtime_stt_provider(self) -> None:
        calls.append("replace")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_providers(updated)

    assert calls == ["llm"]


@pytest.mark.asyncio
async def test_apply_providers_rebuilds_only_llm_for_openrouter_selected_source_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    controller.hub = DummyHub()
    calls: list[str] = []

    updated = AppSettings()
    updated.provider.llm = LLMProviderName.OPENROUTER
    updated.openrouter.selected_source = OpenRouterCredentialSource.MANAGED

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        calls.append("peer")

    async def fake_replace_runtime_stt_provider(self) -> None:
        calls.append("replace")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_providers(updated)

    assert calls == ["llm"]


@pytest.mark.asyncio
async def test_apply_providers_clears_local_qwen_pending_enable_after_switch_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.LOCAL_QWEN
    controller.hub = DummyHub()
    controller._local_stt_pending_enable_after_install = True
    controller._local_stt_runtime_status = "downloading"

    updated = AppSettings()
    updated.provider.stt = STTProviderName.DEEPGRAM

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", lambda self: asyncio.sleep(0))

    await controller.apply_providers(updated)

    assert controller._local_stt_pending_enable_after_install is False


@pytest.mark.asyncio
async def test_apply_providers_switch_to_managed_blocks_concurrent_toggle_from_using_old_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK

    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class SlowClosingLlm:
        async def close(self) -> None:
            close_started.set()
            await release_close.wait()

    controller.hub = DummyHub(llm=SlowClosingLlm())

    updated = AppSettings()
    updated.provider.llm = LLMProviderName.OPENROUTER
    updated.openrouter.selected_source = OpenRouterCredentialSource.MANAGED

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: object())

    apply_task = asyncio.create_task(controller.apply_providers(updated))
    await close_started.wait()

    await controller.set_translation_enabled(True)

    assert controller.hub.translation_enabled is False
    assert controller.hub.clear_context_calls == 0
    assert dash.translation_enabled is False

    release_close.set()
    await apply_task


@pytest.mark.asyncio
async def test_apply_providers_splits_qwen_region_refresh_by_active_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(app=SimpleNamespace(view_dashboard=DummyDashboard()))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.QWEN
    controller.settings.provider.stt = STTProviderName.QWEN_ASR
    controller.settings.provider.peer_stt = STTProviderName.QWEN_ASR
    controller.hub = DummyHub()
    controller._stt_desired = True
    calls: list[str] = []

    updated = AppSettings()
    updated.provider.llm = LLMProviderName.QWEN
    updated.provider.stt = STTProviderName.QWEN_ASR
    updated.provider.peer_stt = STTProviderName.QWEN_ASR
    updated.qwen.region = QwenRegion.SINGAPORE

    monkeypatch.setattr(controller_module, "save_settings", lambda *_args, **_kwargs: None)

    async def fake_rebuild_llm_provider(self) -> None:
        calls.append("llm")

    async def fake_refresh_peer_stt_runtime(self) -> None:
        calls.append("peer")

    async def fake_replace_runtime_stt_provider(self) -> None:
        calls.append("replace")

    async def fake_rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        calls.append(f"pipeline:{rebuild_stt}")

    monkeypatch.setattr(GuiController, "_rebuild_llm_provider", fake_rebuild_llm_provider)
    monkeypatch.setattr(GuiController, "_refresh_peer_stt_runtime", fake_refresh_peer_stt_runtime)
    monkeypatch.setattr(
        GuiController, "_replace_runtime_stt_provider", fake_replace_runtime_stt_provider
    )
    monkeypatch.setattr(GuiController, "_rebuild_pipeline", fake_rebuild_pipeline)

    await controller.apply_providers(updated)

    assert calls.count("llm") == 1
    assert calls.count("peer") == 1
    assert calls.count("replace") == 1
    assert not any(call.startswith("pipeline:") for call in calls)


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
    assert loaded.ui.overlay_enabled is False
    assert path.parent.exists() is True
    assert saves == [(path, loaded)]
    assert saves[0][1].ui.overlay_enabled is False


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
    llm_create_kwargs: dict[str, object] = {}
    controller.hub = DummyHub(llm=FakeLlm())

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())

    def fake_create_llm_provider(*_args, **kwargs):
        llm_create_kwargs.update(kwargs)
        return new_llm

    monkeypatch.setattr(controller_module, "create_llm_provider", fake_create_llm_provider)

    await controller._rebuild_llm_provider()

    assert close_calls == ["close"]
    assert controller.hub.llm is new_llm
    assert dash.translation_needs_key is False
    assert llm_create_kwargs["runtime_logging"] is controller.runtime_logging


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "expected_message"),
    [
        (lambda *_a, **_k: None, "LLM provider not available"),
        (
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
            "LLM provider not available: boom",
        ),
    ],
)
async def test_rebuild_llm_provider_logs_basic_failure_when_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    factory,
    expected_message: str,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.hub = DummyHub(llm=object())

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(controller_module, "create_llm_provider", factory)

    await controller._rebuild_llm_provider()

    assert controller.hub.llm is None
    assert dash.translation_needs_key is True
    assert controller._runtime_logging.basic_messages == [(logging.ERROR, expected_message)]


@pytest.mark.asyncio
async def test_rebuild_llm_provider_logs_basic_failure_when_secret_store_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.hub = DummyHub(llm=object())

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await controller._rebuild_llm_provider()

    assert controller.hub.llm is None
    assert dash.translation_needs_key is True
    assert controller._runtime_logging.basic_messages == [
        (logging.ERROR, "LLM provider not available: boom")
    ]


def test_create_managed_openrouter_release_service_uses_http_broker_client_and_raw_fingerprint_provider() -> (
    None
):
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.settings.openrouter.broker_base_url = "https://broker.example.test/"

    service = controller._create_managed_openrouter_release_service(secrets=DummySecrets({}))

    assert isinstance(service, ManagedOpenRouterReleaseService)
    assert isinstance(service.client, HttpManagedOpenRouterBrokerClient)
    assert service.client.base_url == "https://broker.example.test"
    assert (
        service.raw_hardware_fingerprint_provider is controller_module.get_raw_hardware_fingerprint
    )


def test_create_managed_openrouter_release_service_degrades_to_unavailable_client_for_invalid_broker_base_url() -> (
    None
):
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.settings.openrouter.broker_base_url = "https://broker.example.test/prefix"

    service = controller._create_managed_openrouter_release_service(secrets=DummySecrets({}))

    assert isinstance(service, ManagedOpenRouterReleaseService)
    assert isinstance(service.client, UnavailableManagedOpenRouterReleaseClient)


@pytest.mark.asyncio
async def test_rebuild_llm_provider_closes_previous_managed_release_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.settings.provider.llm = LLMProviderName.OPENROUTER
    controller.settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    controller.hub = DummyHub(llm=object())
    old_service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
        )
    )
    new_service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
        )
    )
    controller._managed_openrouter_release_service = old_service

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(
        GuiController,
        "_create_managed_openrouter_release_service",
        lambda self, *, secrets: new_service,
    )
    monkeypatch.setattr(controller_module, "create_llm_provider", lambda *_a, **_k: object())

    await controller._rebuild_llm_provider()

    assert old_service.close_calls == 1
    assert controller._managed_openrouter_release_service is new_service


@pytest.mark.asyncio
async def test_rebuild_stt_provider_logs_only_failure_when_backend_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.DEEPGRAM
    controller.hub = DummyHub(stt=object())

    monkeypatch.setattr(controller_module, "create_secret_store", lambda *_a, **_k: object())
    monkeypatch.setattr(
        controller_module,
        "create_stt_backend",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await controller._rebuild_stt_provider()

    assert controller.hub.stt is None
    assert dash.stt_needs_key is True
    assert dash.stt_enabled is False
    assert controller._runtime_logging.basic_messages == [
        (logging.ERROR, "STT backend not available: boom")
    ]


@pytest.mark.asyncio
async def test_rebuild_stt_provider_logs_basic_failure_when_secret_store_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller._runtime_logging = RuntimeLoggingSpy()
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.DEEPGRAM
    controller.hub = DummyHub(stt=object())

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await controller._rebuild_stt_provider()

    assert controller.hub.stt is None
    assert dash.stt_needs_key is True
    assert dash.stt_enabled is False
    assert controller._runtime_logging.basic_messages == [
        (logging.ERROR, "STT backend not available: boom")
    ]


@pytest.mark.asyncio
async def test_stop_closes_managed_openrouter_release_service() -> None:
    controller = _make_controller(app=SimpleNamespace())
    service = DummyManagedReleaseService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
        )
    )
    controller._managed_openrouter_release_service = service

    await controller.stop()

    assert service.close_calls == 1
    assert controller._managed_openrouter_release_service is None


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
        def __init__(self, *, app, event_queue, runtime_logging=None) -> None:
            events.append(("bridge_init", app, event_queue, runtime_logging))

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
async def test_rebuild_pipeline_restores_stt_when_it_was_previously_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dash = DummyDashboard()
    controller = _make_controller(app=SimpleNamespace(view_dashboard=dash))
    controller.settings = AppSettings()
    controller.hub = DummyHub(llm=object(), stt=object())
    controller.sender = object()
    controller.osc = object()
    controller._stt_desired = True
    calls: list[bool] = []

    async def fake_set_stt_enabled(self, enabled: bool) -> None:
        self._stt_desired = enabled
        calls.append(enabled)

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = (self, enabled)

    async def fake_init_pipeline(self) -> None:
        self.hub = DummyHub(llm=object(), stt=object())
        self.sender = object()
        self.osc = object()

    monkeypatch.setattr(GuiController, "set_stt_enabled", fake_set_stt_enabled)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(GuiController, "_verify_and_update_status", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(
        controller_module,
        "UIEventBridge",
        lambda **kwargs: SimpleNamespace(run=lambda: asyncio.sleep(0)),
    )

    await controller._rebuild_pipeline(rebuild_stt=True)

    assert calls == [False, True]


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


def test_overlay_calibration_controls_follow_apply_cancel_contract() -> None:
    controller = _make_controller(app=SimpleNamespace())

    controller.begin_overlay_calibration_for_test()
    controller.set_overlay_calibration_field_for_test("distance", 1.2)
    controller.cancel_overlay_calibration_for_test()

    assert controller.overlay_calibration.distance != 1.2

    controller.begin_overlay_calibration_for_test()
    controller.set_overlay_calibration_field_for_test("distance", 1.2)
    controller.apply_overlay_calibration_for_test()

    assert controller.overlay_calibration.distance == 1.2


def test_apply_overlay_calibration_uses_page_run_task_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[Path, AppSettings]] = []

    def fake_save(path: Path, settings: AppSettings) -> None:
        saved.append((path, settings))

    class FakePage:
        def __init__(self) -> None:
            self.tasks: list[object] = []

        def run_task(self, coro_fn) -> None:
            self.tasks.append(coro_fn)

    monkeypatch.setattr(controller_module, "save_settings", fake_save)

    page = FakePage()
    controller = GuiController(page=page, app=SimpleNamespace(), config_path=Path("settings.json"))
    controller.settings = AppSettings()
    controller._overlay_bridge = FakeOverlayBridge(session_token="token")
    controller._overlay_presenter = OverlayPresenter(
        bridge=controller._overlay_bridge,
        calibration=controller.overlay_calibration.copy(),
        clock=controller.clock,
    )

    controller.begin_overlay_calibration_for_test()
    controller.set_overlay_calibration_field_for_test("offset_x", 0.25)
    controller.apply_overlay_calibration_for_test()

    assert controller.settings.overlay.calibration.offset_x == 0.25
    assert saved == [(Path("settings.json"), controller.settings)]
    assert len(page.tasks) == 1

    asyncio.run(page.tasks[0]())

    assert controller._overlay_bridge.snapshots[-1].calibration.offset_x == 0.25


def test_schedule_overlay_calibration_emit_preserves_traceback_in_detailed_log() -> None:
    class FailingPage:
        def run_task(self, coro_fn) -> None:
            _ = coro_fn
            raise RuntimeError("boom")

    controller = GuiController(
        page=FailingPage(),
        app=SimpleNamespace(),
        config_path=Path("settings.json"),
    )
    controller._runtime_logging = RuntimeLoggingSpy()
    controller._overlay_presenter = object()  # type: ignore[assignment]

    controller._schedule_overlay_calibration_emit()

    assert controller._runtime_logging.basic_messages == []
    assert len(controller._runtime_logging.detailed_messages) == 1
    level, message = controller._runtime_logging.detailed_messages[0]
    assert level == logging.WARNING
    assert "[Overlay] Failed to schedule calibration update via page.run_task" in message
    assert "Traceback (most recent call last):" in message
    assert "RuntimeError: boom" in message


@pytest.mark.asyncio
async def test_apply_overlay_calibration_persists_settings_and_emits_overlay_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[Path, AppSettings]] = []

    def fake_save(path: Path, settings: AppSettings) -> None:
        saved.append((path, settings))

    monkeypatch.setattr(controller_module, "save_settings", fake_save)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller._overlay_bridge = FakeOverlayBridge(session_token="token")
    controller._overlay_presenter = OverlayPresenter(
        bridge=controller._overlay_bridge,
        calibration=controller.overlay_calibration.copy(),
        clock=controller.clock,
    )

    controller.begin_overlay_calibration_for_test()
    controller.set_overlay_calibration_field_for_test("distance", 1.2)
    controller.apply_overlay_calibration_for_test()
    await asyncio.sleep(0)

    assert controller.settings.overlay.calibration.distance == 1.2
    assert saved == [(Path("settings.json"), controller.settings)]
    assert controller._overlay_bridge.snapshots[-1].calibration.distance == 1.2


@pytest.mark.asyncio
async def test_apply_settings_updates_overlay_presenter_display_preferences() -> None:
    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()
    controller._overlay_bridge = FakeOverlayBridge(session_token="token")
    controller._overlay_presenter = OverlayPresenter(
        bridge=controller._overlay_bridge,
        calibration=controller.overlay_calibration.copy(),
        clock=controller.clock,
    )

    updated = AppSettings()
    updated.overlay.show_translation = False
    updated.overlay.show_peer_original = False

    await controller.apply_settings(updated)

    assert controller._overlay_presenter.show_translation is False
    assert controller._overlay_presenter.show_peer_original is False


@pytest.mark.asyncio
async def test_apply_settings_pushes_updated_overlay_snapshot_to_bridge_and_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.overlay_enabled = True
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayBridge.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    assert presenter is not None

    utterance_id = uuid4()
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=utterance_id,
            channel="self",
            created_at=10.0,
            text="persist me",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )

    initial_bridge = FakeOverlayBridge.instances[0]
    assert initial_bridge.snapshots[-1].blocks[0].secondary_enabled is True

    updated = AppSettings()
    updated.ui.overlay_enabled = True
    updated.overlay.show_translation = False
    updated.overlay.show_peer_original = False

    await controller.apply_settings(updated)

    assert initial_bridge.snapshots[-1].blocks[0].secondary_enabled is False

    await controller._teardown_overlay_runtime(preserve_presenter_state=True)
    controller.overlay_state = "failed"
    await controller._begin_overlay_start()
    await _wait_until(lambda: len(FakeOverlayBridge.instances) == 2)

    restarted_bridge = FakeOverlayBridge.instances[1]
    assert restarted_bridge.initial_snapshot.blocks[0].secondary_enabled is False


@pytest.mark.asyncio
async def test_apply_settings_pushes_peer_overlay_snapshot_preferences_to_bridge_and_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.settings.ui.overlay_enabled = True
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayBridge.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    assert presenter is not None

    utterance_id = uuid4()
    await presenter.emit(
        PeerTranscriptFinal(
            event_id="peer-final",
            seq=1,
            utterance_id=utterance_id,
            channel="peer",
            created_at=10.0,
            text="peer original",
            source_language="en",
            target_language="ko",
            is_final=True,
        )
    )
    await presenter.emit(
        TranslationFinal(
            event_id="peer-translation",
            seq=2,
            utterance_id=utterance_id,
            channel="peer",
            created_at=10.1,
            text="상대 번역",
            source_language="en",
            target_language="ko",
            is_final=True,
            applied_context_mode=None,
        )
    )

    initial_bridge = FakeOverlayBridge.instances[0]
    assert initial_bridge.snapshots[-1].blocks[0].secondary_enabled is True

    updated = AppSettings()
    updated.ui.overlay_enabled = True
    updated.overlay.show_translation = True
    updated.overlay.show_peer_original = False

    await controller.apply_settings(updated)

    assert initial_bridge.snapshots[-1].blocks[0].secondary_enabled is False

    await controller._teardown_overlay_runtime(preserve_presenter_state=True)
    controller.overlay_state = "failed"
    await controller._begin_overlay_start()
    await _wait_until(lambda: len(FakeOverlayBridge.instances) == 2)

    restarted_bridge = FakeOverlayBridge.instances[1]
    assert restarted_bridge.initial_snapshot.blocks[0].secondary_enabled is False
