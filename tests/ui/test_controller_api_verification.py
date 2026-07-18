from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.core.local_stt_assets import (
    LOCAL_STT_MODEL_ID,
    PARAKEET_JAPANESE_MODEL_ID,
    PARAKEET_V3_MODEL_ID,
    LocalSTTInstallState,
    load_local_stt_asset_manifest,
)
from puripuly_heart.core.local_stt_catalog import (
    LocalCPUInstallSnapshot,
    LocalCPUModelInstall,
)
from puripuly_heart.core.local_stt_runtime_installer import LocalSTTRuntimeInstallError
from puripuly_heart.providers.llm.deepseek import DeepSeekLLMProvider
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider
from puripuly_heart.providers.stt.local_cpu import LocalCPUAutoUnavailableError
from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend
from puripuly_heart.ui import controller as controller_module
from puripuly_heart.ui import i18n as i18n_module
from puripuly_heart.ui.controller import GuiController


class DummySecrets:
    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, key: str) -> str | None:
        return self._values.get(key)


class DummyDashboard:
    def __init__(self) -> None:
        self.translation_needs_key: bool | None = None
        self.translation_enabled: bool | None = None
        self.stt_needs_key: bool | None = None
        self.stt_enabled: bool | None = None
        self.local_stt_notice_status: str | None = None
        self.local_stt_notice_percent: int | None = None
        self.local_stt_notice_model_id: str | None = None

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

    def set_local_stt_notice_model(self, model_id: str | None) -> None:
        self.local_stt_notice_model_id = model_id


class DummyOutputRuntime:
    def __init__(self) -> None:
        self.started_bridges: list[object] = []
        self.bridge_tasks: list[asyncio.Task[object]] = []

    def start_ui_event_bridge(self, bridge: object) -> asyncio.Task[object]:
        self.started_bridges.append(bridge)
        task = asyncio.create_task(bridge.run())  # type: ignore[attr-defined]
        self.bridge_tasks.append(task)
        return task


class DummyHub:
    def __init__(self, *, llm: object | None = object(), stt: object | None = object()) -> None:
        self.llm = llm
        self.stt = stt
        self.translation_enabled = True
        self.ui_events: asyncio.Queue[object] = asyncio.Queue()
        self.output_runtime = DummyOutputRuntime()
        self.start_calls: list[bool] = []
        self.replace_llm_calls: list[object | None] = []

    async def start(self, *, auto_flush_osc: bool) -> None:
        self.start_calls.append(auto_flush_osc)

    async def replace_llm_provider(self, llm: object | None) -> None:
        old_llm = self.llm
        self.replace_llm_calls.append(llm)
        self.llm = llm
        if old_llm is not None and old_llm is not llm and hasattr(old_llm, "close"):
            await old_llm.close()


def _local_stt_download_task(controller: GuiController) -> asyncio.Task[object] | None:
    runtime = controller._local_stt_download_runtime
    return runtime.download_task if runtime is not None else None


def _cpu_snapshot(
    *,
    parakeet_v3: str = "ready",
    parakeet_ja: str = "ready",
    qwen: str = "ready",
) -> LocalCPUInstallSnapshot:
    return LocalCPUInstallSnapshot(
        models=(
            LocalCPUModelInstall(
                model_id=PARAKEET_V3_MODEL_ID,
                state=LocalSTTInstallState(status=parakeet_v3),
            ),
            LocalCPUModelInstall(
                model_id=PARAKEET_JAPANESE_MODEL_ID,
                state=LocalSTTInstallState(status=parakeet_ja),
            ),
            LocalCPUModelInstall(
                model_id=LOCAL_STT_MODEL_ID,
                state=LocalSTTInstallState(status=qwen),
            ),
        )
    )


def _cpu_ready_snapshot() -> LocalCPUInstallSnapshot:
    return LocalCPUInstallSnapshot(
        models=tuple(
            LocalCPUModelInstall(
                model_id=model_id,
                state=LocalSTTInstallState(
                    status="ready",
                    installed_manifest=SimpleNamespace(model_id=model_id),
                ),
            )
            for model_id in (
                PARAKEET_V3_MODEL_ID,
                PARAKEET_JAPANESE_MODEL_ID,
                LOCAL_STT_MODEL_ID,
            )
        )
    )


async def _start_controller_with_inspected_stt_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config_path: Path,
    provider: STTProviderName,
    install_state: LocalSTTInstallState,
    hub_stt: object | None = object(),
) -> tuple[GuiController, DummyDashboard, list[str], list[str]]:
    settings = AppSettings()
    settings.provider.stt = provider
    dash = DummyDashboard()
    hub = DummyHub(stt=hub_stt)
    inspect_calls: list[str] = []
    install_calls: list[str] = []

    class FakeBridge:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def run(self) -> None:
            await asyncio.sleep(0)

        def report_overlay_state(
            self,
            overlay_state: str,
            *,
            failure_reason: str | None = None,
        ) -> None:
            _ = (overlay_state, failure_reason)

    async def fake_init_pipeline(self) -> None:
        self.hub = hub

    async def fake_verify_and_update_status(self) -> None:
        return None

    async def fake_install(**kwargs):
        _ = kwargs
        install_calls.append("install")
        return object()

    def fake_inspect(*_args, **_kwargs):
        inspect_calls.append("inspect")
        return install_state

    monkeypatch.setattr(GuiController, "_load_or_init_settings", lambda self, path: settings)
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(GuiController, "_init_pipeline", fake_init_pipeline)
    monkeypatch.setattr(GuiController, "_verify_and_update_status", fake_verify_and_update_status)
    monkeypatch.setattr(controller_module, "set_locale", lambda _locale: None)
    monkeypatch.setattr(controller_module, "UIEventBridge", FakeBridge)
    monkeypatch.setattr(controller_module, "inspect_local_stt_install_state", fake_inspect)
    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)

    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=dash),
        config_path=config_path,
    )

    await controller.start()
    await asyncio.sleep(0)
    return controller, dash, inspect_calls, install_calls


def test_local_stt_download_prompt_helpers_removed() -> None:
    assert not hasattr(GuiController, "_show_local_stt_download_prompt")
    assert not hasattr(GuiController, "_on_local_stt_download_action")


def test_manual_local_asr_mismatches_persist_qwen_for_self_and_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_PARAKEET_V3
    settings.provider.peer_stt = STTProviderName.LOCAL_PARAKEET_JAPANESE
    settings.languages.source_language = "ko"
    settings.languages.peer_source_language = "en"
    saved: list[AppSettings] = []
    messages: list[str] = []
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(
            _show_snackbar=lambda message, _color: messages.append(message),
        ),
        config_path=Path("settings.json"),
    )
    controller.settings = settings

    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_save_settings",
        lambda self: saved.append(self.settings) or True,
    )

    assert controller._persist_current_manual_local_asr_fallback() is True
    assert controller.settings.provider.stt == STTProviderName.LOCAL_QWEN
    assert controller.settings.provider.peer_stt == STTProviderName.LOCAL_QWEN
    assert len(saved) == 1
    assert messages == [controller_module.t("local_stt.language_fallback_qwen")]


def test_cpu_auto_with_missing_parakeet_persists_qwen_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_CPU_AUTO
    installed_model_ids: list[str] = []
    saved: list[AppSettings] = []
    messages: list[str] = []

    async def fake_install(*, model_id: str, **_kwargs):
        installed_model_ids.append(model_id)
        return load_local_stt_asset_manifest(model_id)

    snapshot = LocalCPUInstallSnapshot(
        models=(
            LocalCPUModelInstall(
                model_id=PARAKEET_V3_MODEL_ID,
                state=LocalSTTInstallState(status="ready"),
            ),
            LocalCPUModelInstall(
                model_id=PARAKEET_JAPANESE_MODEL_ID,
                state=LocalSTTInstallState(status="missing"),
            ),
            LocalCPUModelInstall(
                model_id=LOCAL_STT_MODEL_ID,
                state=LocalSTTInstallState(status="ready"),
            ),
        )
    )
    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_save_settings",
        lambda self: saved.append(self.settings) or True,
    )
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(
            _show_snackbar=lambda message, _color: messages.append(message),
        ),
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller._local_cpu_install_snapshot = snapshot
    monkeypatch.setattr(
        GuiController,
        "_inspect_local_cpu_model_installs_for_selection",
        lambda self: snapshot,
    )

    assert controller._persist_current_manual_local_asr_fallback() is True
    assert controller.settings.provider.stt == STTProviderName.LOCAL_QWEN
    assert installed_model_ids == []
    assert len(saved) == 1
    assert messages == [controller_module.t("local_stt.installation_fallback_qwen")]


def test_successful_cpu_auto_probe_preserves_installed_model_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(),
        config_path=Path("settings.json"),
    )
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.LOCAL_CPU_AUTO
    controller._set_local_cpu_install_snapshot(_cpu_ready_snapshot())
    strict_snapshot = _cpu_ready_snapshot()
    monkeypatch.setattr(
        controller_module,
        "inspect_local_cpu_model_installs",
        lambda model_ids, *_args, **_kwargs: LocalCPUInstallSnapshot(
            models=tuple(
                strict_snapshot.models[
                    (
                        PARAKEET_V3_MODEL_ID,
                        PARAKEET_JAPANESE_MODEL_ID,
                        LOCAL_STT_MODEL_ID,
                    ).index(model_id)
                ]
                for model_id in model_ids
            )
        ),
    )

    controller._record_strict_local_stt_ready(
        (PARAKEET_V3_MODEL_ID, PARAKEET_JAPANESE_MODEL_ID, LOCAL_STT_MODEL_ID)
    )

    assert controller._local_cpu_install_snapshot is not None
    assert controller._local_cpu_install_snapshot.cpu_auto_available is True
    for language in ("bg", "ar", "ko", "en", "ja"):
        controller.settings.languages.source_language = language
        normalized, channels, installation_fallback = (
            controller._normalize_manual_local_asr_fallbacks(controller.settings)
        )
        assert normalized is controller.settings
        assert channels == ()
        assert installation_fallback is False


def test_runtime_refresh_clears_recovered_parakeet_download_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = DummyDashboard()
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=dashboard),
        config_path=Path("settings.json"),
    )
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.LOCAL_CPU_AUTO
    controller._local_stt_runtime_status = "download_failed"
    controller._local_stt_download_model_ids = (PARAKEET_V3_MODEL_ID,)
    controller._local_stt_notice_model_id = PARAKEET_V3_MODEL_ID
    ready_snapshot = _cpu_ready_snapshot()
    monkeypatch.setattr(
        GuiController,
        "_inspect_local_cpu_model_installs_for_selection",
        lambda self: ready_snapshot,
    )

    controller._refresh_local_stt_runtime_state()

    assert controller._local_stt_runtime_status == "ready"
    assert controller._local_stt_download_model_ids == ()
    assert controller._local_stt_notice_model_id is None
    assert dashboard.local_stt_notice_status is None


@pytest.mark.asyncio
async def test_cpu_auto_repair_attempts_remaining_models_after_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_CPU_AUTO
    attempted_model_ids: list[str] = []

    async def fake_install(*, model_id: str, **_kwargs):
        attempted_model_ids.append(model_id)
        if model_id == PARAKEET_V3_MODEL_ID:
            raise LocalSTTRuntimeInstallError("failed")
        return load_local_stt_asset_manifest(model_id)

    snapshot = LocalCPUInstallSnapshot(
        models=(
            LocalCPUModelInstall(
                model_id=PARAKEET_V3_MODEL_ID,
                state=LocalSTTInstallState(status="missing"),
            ),
            LocalCPUModelInstall(
                model_id=PARAKEET_JAPANESE_MODEL_ID,
                state=LocalSTTInstallState(status="missing"),
            ),
            LocalCPUModelInstall(
                model_id=LOCAL_STT_MODEL_ID,
                state=LocalSTTInstallState(status="ready"),
            ),
        )
    )
    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller._local_cpu_install_snapshot = snapshot
    controller._local_stt_download_model_ids = (
        PARAKEET_V3_MODEL_ID,
        PARAKEET_JAPANESE_MODEL_ID,
    )

    await controller._run_local_stt_download(origin="automatic")

    assert attempted_model_ids == [PARAKEET_V3_MODEL_ID, PARAKEET_JAPANESE_MODEL_ID]
    assert controller._local_stt_runtime_status == "download_failed"
    assert controller._local_stt_download_model_ids == (PARAKEET_V3_MODEL_ID,)
    assert (
        controller._local_cpu_install_snapshot.state_for(PARAKEET_V3_MODEL_ID).status == "missing"
    )
    assert (
        controller._local_cpu_install_snapshot.state_for(PARAKEET_JAPANESE_MODEL_ID).status
        == "ready"
    )


@pytest.mark.asyncio
async def test_self_cpu_auto_strict_corruption_falls_back_without_parakeet_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_CPU_AUTO
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    settings.ui.peer_translation_enabled = True
    settings.ui.peer_translation_eula_accepted = True
    strict_snapshot = _cpu_snapshot(parakeet_v3="invalid")
    download_origins: list[str] = []
    probed_providers: list[STTProviderName] = []
    saved: list[AppSettings] = []

    async def fake_probe(self, *, activation_generation=None) -> None:
        _ = activation_generation
        probed_providers.append(self.settings.provider.stt)
        if self.settings.provider.stt == STTProviderName.LOCAL_CPU_AUTO:
            raise LocalCPUAutoUnavailableError(strict_snapshot)

    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(_show_snackbar=lambda *_args, **_kwargs: None),
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller.hub = SimpleNamespace(
        stt=object(),
        peer_stt=object(),
    )
    controller._set_local_cpu_install_snapshot(_cpu_snapshot())
    ready_snapshot = _cpu_ready_snapshot()
    monkeypatch.setattr(
        controller_module,
        "inspect_local_cpu_model_installs",
        lambda model_ids, *_args, **_kwargs: LocalCPUInstallSnapshot(
            models=tuple(model for model in ready_snapshot.models if model.model_id in model_ids)
        ),
    )
    monkeypatch.setattr(GuiController, "_probe_self_local_stt_runtime_load", fake_probe)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_sync_ui_from_settings", lambda self: None)
    monkeypatch.setattr(
        GuiController,
        "_save_settings",
        lambda self: saved.append(self.settings) or True,
    )
    monkeypatch.setattr(
        GuiController,
        "_start_local_stt_download",
        lambda self, *, origin: download_origins.append(origin) or True,
    )

    assert await controller._ensure_local_stt_ready() is True
    assert controller.settings.provider.stt == STTProviderName.LOCAL_QWEN
    assert probed_providers == [STTProviderName.LOCAL_CPU_AUTO, STTProviderName.LOCAL_QWEN]
    assert len(saved) == 1
    assert controller._local_stt_pending_enable_after_install is False
    assert controller._local_stt_pending_peer_enable_after_install is False
    assert controller._local_cpu_install_snapshot.state_for(LOCAL_STT_MODEL_ID).status == "ready"
    assert (
        controller._local_stt_runtime_status_for_provider(STTProviderName.LOCAL_QWEN.value)
        == "ready"
    )
    assert download_origins == []


@pytest.mark.asyncio
async def test_invalid_self_parakeet_preserves_valid_peer_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_PARAKEET_V3
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    settings.languages.source_language = "en"
    settings.ui.peer_translation_enabled = True
    settings.ui.peer_translation_eula_accepted = True

    class Backend:
        async def open_session(self):
            raise controller_module.LocalSTTManifestInvalidError("corrupt")

    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller.hub = SimpleNamespace(
        stt=SimpleNamespace(backend=Backend()),
        peer_stt=object(),
    )
    controller._set_local_cpu_install_snapshot(_cpu_snapshot())
    monkeypatch.setattr(
        GuiController,
        "_start_local_stt_download",
        lambda self, *, origin: True,
    )

    assert await controller._ensure_local_stt_ready() is False
    assert controller._local_cpu_install_snapshot.state_for(PARAKEET_V3_MODEL_ID).status == (
        "invalid"
    )
    assert controller._local_cpu_install_snapshot.state_for(LOCAL_STT_MODEL_ID).status == "ready"
    assert (
        controller._local_stt_runtime_status_for_provider(STTProviderName.LOCAL_QWEN.value)
        == "ready"
    )
    assert controller._local_stt_pending_enable_after_install is True
    assert controller._local_stt_pending_peer_enable_after_install is False


@pytest.mark.asyncio
async def test_invalid_peer_parakeet_strict_probe_preserves_valid_self_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.LOCAL_PARAKEET_V3
    settings.languages.peer_source_language = "en"
    settings.ui.peer_translation_enabled = True
    settings.ui.peer_translation_eula_accepted = True
    probe_calls: list[str] = []

    async def fail_strict_probe(self) -> None:
        probe_calls.append("peer")
        raise controller_module.LocalSTTManifestInvalidError("corrupt")

    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller.hub = SimpleNamespace(stt=object(), peer_stt=object())
    controller._set_local_cpu_install_snapshot(_cpu_snapshot())
    monkeypatch.setattr(GuiController, "_probe_peer_local_stt_runtime_load", fail_strict_probe)
    monkeypatch.setattr(
        GuiController,
        "_start_local_stt_download",
        lambda self, *, origin: True,
    )

    assert await controller._ensure_peer_local_stt_ready() is False
    assert probe_calls == ["peer"]
    assert controller._local_cpu_install_snapshot.state_for(PARAKEET_V3_MODEL_ID).status == (
        "invalid"
    )
    assert controller._local_cpu_install_snapshot.state_for(LOCAL_STT_MODEL_ID).status == "ready"
    assert controller._current_local_stt_runtime_status() == "ready"
    assert controller._local_stt_pending_enable_after_install is False
    assert controller._local_stt_pending_peer_enable_after_install is True


@pytest.mark.asyncio
async def test_stale_self_strict_failure_after_disable_cannot_repair_or_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    download_origins: list[str] = []
    rebuilds: list[str] = []

    class Backend:
        async def open_session(self):
            entered.set()
            await release.wait()
            raise controller_module.LocalSTTManifestInvalidError("corrupt")

    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )
    controller.settings = AppSettings()
    controller.settings.provider.stt = STTProviderName.LOCAL_QWEN
    controller.hub = SimpleNamespace(
        stt=SimpleNamespace(backend=Backend()),
        mark_promo_eligible=lambda: None,
    )
    controller._set_local_cpu_install_snapshot(_cpu_snapshot())
    monkeypatch.setattr(
        GuiController,
        "_start_local_stt_download",
        lambda self, *, origin: download_origins.append(origin) or True,
    )

    enabling = asyncio.create_task(controller.set_stt_enabled(True))
    await entered.wait()
    disabling = asyncio.create_task(controller.set_stt_enabled(False))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(enabling, disabling)

    assert controller._stt_desired is False
    assert controller._local_stt_pending_enable_after_install is False
    assert controller._local_stt_pending_enable_generation is None
    assert download_origins == []

    async def fake_install(*, locale: str, on_status, cancel_event):
        _ = (locale, on_status, cancel_event)
        return load_local_stt_asset_manifest(LOCAL_STT_MODEL_ID)

    async def fake_rebuild(_self) -> None:
        rebuilds.append("rebuild")

    controller._local_stt_download_model_ids = (LOCAL_STT_MODEL_ID,)
    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild)
    await controller._run_local_stt_download(origin="manual")

    assert controller._stt_desired is False
    assert controller._local_stt_pending_enable_after_install is False
    assert rebuilds == []


@pytest.mark.asyncio
async def test_peer_disable_waits_for_cancelled_owned_checksum_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_started = threading.Event()
    thread_release = threading.Event()
    thread_finished = threading.Event()

    def blocking_probe(model_ids, *_args, **_kwargs):
        thread_started.set()
        try:
            assert thread_release.wait(timeout=5)
            return LocalCPUInstallSnapshot(
                models=tuple(
                    LocalCPUModelInstall(
                        model_id=model_id,
                        state=LocalSTTInstallState(status="ready"),
                    )
                    for model_id in model_ids
                )
            )
        finally:
            thread_finished.set()

    async def no_refresh(_self) -> None:
        return None

    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )
    controller.settings = AppSettings()
    controller.settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    controller.settings.ui.peer_translation_eula_accepted = True
    controller.settings.ui.overlay_enabled = True
    controller.overlay_state = "connected"
    controller.hub = SimpleNamespace(
        peer_stt=None,
        peer_translation_enabled=False,
        integrated_context_enabled=False,
    )
    controller._set_local_cpu_install_snapshot(_cpu_snapshot())
    monkeypatch.setattr(controller_module, "inspect_local_cpu_model_installs", blocking_probe)
    monkeypatch.setattr(GuiController, "_refresh_overlay_runtime_dependencies", no_refresh)

    enabling = asyncio.create_task(controller.set_peer_translation_enabled(True))
    while not thread_started.is_set():
        await asyncio.sleep(0)
    disabling = asyncio.create_task(controller.set_peer_translation_enabled(False))
    await asyncio.sleep(0)

    assert disabling.done() is False
    assert thread_finished.is_set() is False

    thread_release.set()
    results = await asyncio.gather(enabling, disabling, return_exceptions=True)

    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] is None
    assert thread_finished.is_set() is True
    assert controller.settings.ui.peer_translation_enabled is False
    assert controller._local_stt_pending_peer_enable_after_install is False
    assert controller._peer_local_stt_probe_task is None
    assert not any(
        name.startswith("peer-local-stt-probe-")
        for name in controller._ui_background_scope.active_task_names
    )


def test_action_snackbar_helper_removed_from_app_source() -> None:
    app_source = (Path(controller_module.__file__).parent / "app.py").read_text(encoding="utf-8")

    assert "def show_action_snackbar(" not in app_source


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_obsolete_local_stt_prompt_keys_are_removed(locale: str) -> None:
    bundle = i18n_module._load_bundle(locale)

    assert "local_stt.download_prompt_missing" not in bundle
    assert "local_stt.download_prompt_invalid" not in bundle
    assert "local_stt.download_prompt_failed" not in bundle
    assert "local_stt.download_action" not in bundle


@pytest.mark.asyncio
async def test_verify_qwen_llm_api_key_uses_async_verifier_in_low_latency(monkeypatch) -> None:
    settings = AppSettings()
    settings.stt.low_latency_mode = True
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_FLASH
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings

    seen: dict[str, str] = {}

    async def fake_async_verify(api_key: str, *, base_url: str, model: str) -> bool:
        seen["api_key"] = api_key
        seen["base_url"] = base_url
        seen["model"] = model
        return True

    async def fail_sync_verify(*_args, **_kwargs) -> bool:
        raise AssertionError("sync verifier must not be called in low latency mode")

    monkeypatch.setattr(AsyncQwenLLMProvider, "verify_api_key", staticmethod(fake_async_verify))
    monkeypatch.setattr(QwenLLMProvider, "verify_api_key", staticmethod(fail_sync_verify))

    ok = await controller._verify_qwen_llm_api_key(
        "secret", base_url="https://dashscope.aliyuncs.com/api/v1"
    )

    assert ok is True
    assert seen == {
        "api_key": "secret",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.5-flash",
    }


@pytest.mark.asyncio
async def test_verify_and_update_status_uses_qwen_specific_verifiers(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.provider.stt = STTProviderName.QWEN_ASR
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"alibaba_api_key_beijing": "secret"}),
    )

    llm_seen: list[tuple[str, str]] = []

    async def fake_verify_qwen_llm(self, api_key: str, *, base_url: str, model: str) -> bool:
        llm_seen.append((api_key, base_url))
        return True

    async def fail_qwen_asr_verify(*_args, **_kwargs) -> bool:
        raise AssertionError("qwen ASR verifier should not be called when Alibaba result is shared")

    async def fail_legacy_verify(*_args, **_kwargs) -> bool:
        raise AssertionError("legacy llm verifier path must not be called")

    monkeypatch.setattr(GuiController, "_verify_qwen_llm_api_key", fake_verify_qwen_llm)
    monkeypatch.setattr(
        QwenASRRealtimeSTTBackend, "verify_api_key", staticmethod(fail_qwen_asr_verify)
    )
    monkeypatch.setattr(QwenLLMProvider, "verify_api_key", staticmethod(fail_legacy_verify))

    await controller._verify_and_update_status()

    assert llm_seen == [("secret", "https://dashscope.aliyuncs.com/api/v1")]
    assert app.view_dashboard.translation_needs_key is False
    assert app.view_dashboard.stt_needs_key is False


@pytest.mark.asyncio
async def test_verify_api_key_returns_model_unavailable_when_fallback_model_works(
    monkeypatch,
) -> None:
    settings = AppSettings()
    settings.stt.low_latency_mode = True
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_FLASH
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings

    seen_models: list[str] = []

    async def fake_async_verify(api_key: str, *, base_url: str, model: str) -> bool:
        _ = api_key, base_url
        seen_models.append(model)
        return model == QwenLLMModel.QWEN_35_PLUS.value

    monkeypatch.setattr(AsyncQwenLLMProvider, "verify_api_key", staticmethod(fake_async_verify))

    success, msg = await controller.verify_api_key("alibaba_beijing", "secret")

    assert success is False
    assert msg == "qwen_model_unavailable:qwen3.5-flash"
    assert seen_models == ["qwen3.5-flash", "qwen3.5-plus"]


@pytest.mark.asyncio
async def test_verify_and_update_status_splits_llm_model_access_from_stt_key_validity(
    monkeypatch,
) -> None:
    settings = AppSettings()
    settings.stt.low_latency_mode = True
    settings.provider.llm = LLMProviderName.QWEN
    settings.provider.stt = STTProviderName.QWEN_ASR
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_FLASH
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"alibaba_api_key": "secret"}),
    )

    seen_models: list[str] = []

    async def fake_async_verify(api_key: str, *, base_url: str, model: str) -> bool:
        _ = api_key, base_url
        seen_models.append(model)
        return model == QwenLLMModel.QWEN_35_PLUS.value

    monkeypatch.setattr(AsyncQwenLLMProvider, "verify_api_key", staticmethod(fake_async_verify))

    await controller._verify_and_update_status()

    assert app.view_dashboard.translation_needs_key is True
    assert app.view_dashboard.stt_needs_key is False
    assert seen_models == ["qwen3.5-flash", "qwen3.5-plus"]


@pytest.mark.asyncio
async def test_verify_and_update_status_uses_selected_qwen_model_for_both_llm_and_stt_when_valid(
    monkeypatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.provider.stt = STTProviderName.QWEN_ASR
    settings.qwen.region = QwenRegion.SINGAPORE
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"alibaba_api_key_singapore": "secret"}),
    )

    seen_models: list[str] = []

    async def fake_verify_qwen_llm(self, api_key: str, *, base_url: str, model: str) -> bool:
        assert api_key == "secret"
        assert base_url == "https://dashscope-intl.aliyuncs.com/api/v1"
        seen_models.append(model)
        return True

    monkeypatch.setattr(GuiController, "_verify_qwen_llm_api_key", fake_verify_qwen_llm)

    await controller._verify_and_update_status()

    assert app.view_dashboard.translation_needs_key is False
    assert app.view_dashboard.stt_needs_key is False


@pytest.mark.asyncio
async def test_verify_and_update_status_uses_openrouter_verifier(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"openrouter_api_key": "secret"}),
    )

    seen: list[str] = []

    async def fake_verify(api_key: str) -> bool:
        seen.append(api_key)
        return True

    monkeypatch.setattr(OpenRouterLLMProvider, "verify_api_key", staticmethod(fake_verify))

    await controller._verify_and_update_status()

    assert seen == ["secret"]
    assert app.view_dashboard.translation_needs_key is False


@pytest.mark.asyncio
async def test_verify_api_key_uses_deepseek_verifier(monkeypatch) -> None:
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )

    seen: list[str] = []

    async def fake_verify(api_key: str) -> bool:
        seen.append(api_key)
        return True

    monkeypatch.setattr(DeepSeekLLMProvider, "verify_api_key", staticmethod(fake_verify))

    ok, message = await controller.verify_api_key("deepseek", "secret")

    assert ok is True
    assert message == "Verification successful"
    assert seen == ["secret"]


@pytest.mark.asyncio
async def test_verify_and_update_status_uses_deepseek_verifier(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.DEEPSEEK
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"deepseek_api_key": "secret"}),
    )

    seen: list[str] = []

    async def fake_verify(api_key: str) -> bool:
        seen.append(api_key)
        return True

    monkeypatch.setattr(DeepSeekLLMProvider, "verify_api_key", staticmethod(fake_verify))

    await controller._verify_and_update_status()

    assert seen == ["secret"]
    assert app.view_dashboard.translation_needs_key is False


@pytest.mark.asyncio
async def test_verify_and_update_status_uses_deepseek_env_key(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.DEEPSEEK
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")

    seen: list[str] = []

    async def fake_verify(api_key: str) -> bool:
        seen.append(api_key)
        return True

    monkeypatch.setattr(DeepSeekLLMProvider, "verify_api_key", staticmethod(fake_verify))

    await controller._verify_and_update_status()

    assert seen == ["env-secret"]
    assert app.view_dashboard.translation_needs_key is False


@pytest.mark.asyncio
async def test_local_llm_status_update_skips_connection_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.LOCAL_LLM
    app = SimpleNamespace(view_dashboard=DummyDashboard())
    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )
    monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)

    monkeypatch.setenv("LOCAL_LLM_API_KEY", "env-secret")

    await controller._verify_and_update_status()

    assert app.view_dashboard.translation_needs_key is False
    assert app.view_dashboard.translation_enabled is True


@pytest.mark.asyncio
async def test_verify_and_update_status_uses_selected_managed_openrouter_key(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"openrouter_managed_api_key": "managed-secret"}),
    )

    seen: list[str] = []

    async def fake_verify(api_key: str) -> bool:
        seen.append(api_key)
        return True

    monkeypatch.setattr(OpenRouterLLMProvider, "verify_api_key", staticmethod(fake_verify))

    await controller._verify_and_update_status()

    assert seen == ["managed-secret"]
    assert app.view_dashboard.translation_needs_key is False


@pytest.mark.asyncio
async def test_verify_and_update_status_keeps_managed_openrouter_toggle_available_without_local_key(
    monkeypatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub(llm=object())

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({}),
    )

    async def fail_verify(_api_key: str) -> bool:
        raise AssertionError("verify_api_key should not be called without a local managed key")

    monkeypatch.setattr(OpenRouterLLMProvider, "verify_api_key", staticmethod(fail_verify))

    await controller._verify_and_update_status()

    assert app.view_dashboard.translation_needs_key is False


@pytest.mark.asyncio
async def test_verify_and_update_status_marks_openrouter_none_selected_source_as_needs_key(
    monkeypatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.NONE
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub(llm=None)

    monkeypatch.setattr(
        controller_module,
        "create_secret_store",
        lambda *_args, **_kwargs: DummySecrets({"openrouter_api_key": "secret"}),
    )

    async def fail_verify(_api_key: str) -> bool:
        raise AssertionError("verify_api_key should not be called")

    monkeypatch.setattr(OpenRouterLLMProvider, "verify_api_key", staticmethod(fail_verify))

    await controller._verify_and_update_status()

    assert app.view_dashboard.translation_needs_key is True
    assert app.view_dashboard.translation_enabled is False


@pytest.mark.asyncio
async def test_verify_and_update_status_treats_local_qwen_stt_as_keyless(
    monkeypatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.llm = LLMProviderName.GEMINI
    app = SimpleNamespace(view_dashboard=DummyDashboard())

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyHub()

    def fail_secret_store(*_args, **_kwargs):
        raise RuntimeError("secret store should not be needed for local STT")

    monkeypatch.setattr(controller_module, "create_secret_store", fail_secret_store)

    await controller._verify_and_update_status()

    assert app.view_dashboard.stt_needs_key is False


@pytest.mark.asyncio
async def test_set_stt_enabled_starts_local_qwen_runtime_install_when_model_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    install_calls: list[str] = []
    release = SimpleNamespace(done=False)
    app = SimpleNamespace(
        view_dashboard=DummyDashboard(),
        _show_snackbar=lambda *_args, **_kwargs: None,
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    async def fake_install(**_kwargs):
        install_calls.append("install")
        while not release.done:
            await asyncio.sleep(0)
        return object()

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", lambda self: asyncio.sleep(0))

    controller = GuiController(
        page=SimpleNamespace(),
        app=app,
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)

    assert controller._stt_desired is False
    assert app.view_dashboard.stt_enabled is False
    assert app.view_dashboard.local_stt_notice_status == "downloading"
    assert app.view_dashboard.local_stt_notice_percent == 0
    assert install_calls == ["install"]

    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task


@pytest.mark.asyncio
async def test_set_stt_enabled_starts_local_qwen_runtime_install_when_model_load_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    install_calls: list[str] = []
    release = SimpleNamespace(done=False)
    app = SimpleNamespace(
        view_dashboard=DummyDashboard(),
        _show_snackbar=lambda *_args, **_kwargs: None,
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    async def fake_install(**_kwargs):
        install_calls.append("install")
        while not release.done:
            await asyncio.sleep(0)
        return object()

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", lambda self: asyncio.sleep(0))

    controller = GuiController(
        page=SimpleNamespace(),
        app=app,
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._local_stt_install_state = LocalSTTInstallState(status="invalid")

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)

    assert controller._stt_desired is False
    assert app.view_dashboard.stt_enabled is False
    assert app.view_dashboard.local_stt_notice_status == "downloading"
    assert app.view_dashboard.local_stt_notice_percent == 0
    assert install_calls == ["install"]

    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task


@pytest.mark.asyncio
async def test_set_stt_enabled_retries_runtime_install_after_download_failed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    install_calls: list[str] = []
    release = SimpleNamespace(done=False)
    app = SimpleNamespace(
        view_dashboard=DummyDashboard(),
        _show_snackbar=lambda *_args, **_kwargs: None,
    )

    async def fake_install(**_kwargs):
        install_calls.append("install")
        while not release.done:
            await asyncio.sleep(0)
        return object()

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", lambda self: asyncio.sleep(0))

    controller = GuiController(
        page=SimpleNamespace(),
        app=app,
        config_path=Path("settings.json"),
    )
    controller.settings = settings
    controller._local_stt_install_state = LocalSTTInstallState(status="invalid")
    controller._local_stt_runtime_status = "download_failed"

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)

    assert controller._stt_desired is False
    assert app.view_dashboard.stt_enabled is False
    assert app.view_dashboard.local_stt_notice_status == "downloading"
    assert app.view_dashboard.local_stt_notice_percent == 0
    assert install_calls == ["install"]

    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task


@pytest.mark.asyncio
async def test_local_qwen_repeated_enable_during_runtime_install_is_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    dashboard = DummyDashboard()
    status_messages: list[str] = []
    install_calls: list[str] = []
    release = SimpleNamespace(done=False)

    app = SimpleNamespace(
        view_dashboard=dashboard,
        _show_snackbar=lambda message, *_args, **_kwargs: status_messages.append(message),
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    async def fake_install(**_kwargs):
        install_calls.append("install")
        while not release.done:
            await asyncio.sleep(0)
        return object()

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", lambda self: asyncio.sleep(0))

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)
    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)

    assert install_calls == ["install"]
    assert dashboard.local_stt_notice_status == "downloading"
    assert dashboard.local_stt_notice_percent == 0
    assert controller_module.t("local_stt.download_in_progress") in status_messages

    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task


@pytest.mark.asyncio
async def test_local_stt_download_uses_named_runtime_owner_and_ignores_stale_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    dashboard = DummyDashboard()
    app = SimpleNamespace(
        view_dashboard=dashboard,
        _show_snackbar=lambda *_args, **_kwargs: None,
    )
    install_started = asyncio.Event()
    release_install = asyncio.Event()
    captured_status_callbacks: list[object] = []

    async def fake_install(**kwargs):
        captured_status_callbacks.append(kwargs["on_status"])
        install_started.set()
        await release_install.wait()
        return object()

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    assert controller._start_local_stt_download(origin="manual") is True
    await install_started.wait()

    owner = controller._local_stt_download_runtime
    assert owner is not None
    assert owner.lifecycle_owner_snapshot()["owner"] == "LocalSTTDownloadRuntime"
    assert owner.download_task is not None
    assert owner.cancel_event is not None
    assert owner.origin == "manual"

    on_status = captured_status_callbacks[0]
    await on_status(controller_module.RuntimeLocalSTTStatusUpdate("downloading", percent=42))
    assert controller._local_stt_runtime_status == "downloading"
    assert controller._local_stt_download_percent == 42
    assert dashboard.local_stt_notice_percent == 42

    await controller._cancel_local_stt_download()
    controller._local_stt_runtime_status = "ready"
    controller._local_stt_download_percent = None
    await on_status(controller_module.RuntimeLocalSTTStatusUpdate("downloading", percent=99))

    release_install.set()
    assert controller._local_stt_runtime_status == "ready"
    assert controller._local_stt_download_percent is None
    assert owner.download_task is None
    assert owner.cancel_event is None


@pytest.mark.asyncio
async def test_local_stt_download_rejects_new_start_after_runtime_close() -> None:
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )
    controller.settings = AppSettings()
    owner = controller._get_local_stt_download_runtime()
    await owner.close()

    assert controller._start_local_stt_download(origin="manual") is False
    assert owner.download_task is None


@pytest.mark.asyncio
async def test_stop_cancels_active_local_stt_download_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = GuiController(
        page=SimpleNamespace(),
        app=SimpleNamespace(view_dashboard=DummyDashboard()),
        config_path=Path("settings.json"),
    )

    async def fake_set_stt_enabled(self, enabled: bool) -> None:
        _ = self, enabled

    async def fake_configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        _ = self, enabled

    async def fake_shutdown_overlay_runtime(self, *, preserve_failure_reason: bool) -> None:
        _ = self, preserve_failure_reason

    monkeypatch.setattr(GuiController, "set_stt_enabled", fake_set_stt_enabled)
    monkeypatch.setattr(
        GuiController,
        "_configure_vrc_mic_receiver",
        fake_configure_vrc_mic_receiver,
    )
    monkeypatch.setattr(
        GuiController,
        "_shutdown_overlay_runtime",
        fake_shutdown_overlay_runtime,
    )

    owner = controller._get_local_stt_download_runtime()
    active_download = owner.start(
        origin="manual",
        run_download=lambda _cancel_event, _generation: asyncio.sleep(3600),
    )

    await controller.stop()

    assert active_download.done()
    assert owner.download_task is None


@pytest.mark.asyncio
async def test_local_qwen_successful_runtime_install_retries_enable_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    dashboard = DummyDashboard()
    rebuild_calls: list[str] = []
    status_messages: list[str] = []
    switch_calls: list[bool] = []

    app = SimpleNamespace(
        view_dashboard=dashboard,
        _show_snackbar=lambda message, *_args, **_kwargs: status_messages.append(message),
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    async def fake_install(**_kwargs):
        return object()

    async def fake_rebuild(self):
        rebuild_calls.append("rebuild")

    async def fake_switch(self):
        switch_calls.append(self._stt_desired)

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild)
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_switch)

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    await controller.set_stt_enabled(True)
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task

    assert rebuild_calls == ["rebuild"]
    assert switch_calls == [True]
    assert dashboard.local_stt_notice_status is None
    assert controller_module.t("local_stt.download_success") not in status_messages


@pytest.mark.asyncio
async def test_local_qwen_runtime_install_does_not_auto_enable_after_provider_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    dashboard = DummyDashboard()
    switch_calls: list[bool] = []
    release = SimpleNamespace(done=False)

    app = SimpleNamespace(
        view_dashboard=dashboard,
        _show_snackbar=lambda *_args, **_kwargs: None,
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    async def fake_install(**_kwargs):
        while not release.done:
            await asyncio.sleep(0)
        return object()

    async def fake_switch(self):
        switch_calls.append(self._stt_desired)

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_switch)

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)

    controller.settings.provider.stt = STTProviderName.DEEPGRAM
    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task

    assert switch_calls == []


@pytest.mark.asyncio
async def test_local_qwen_explicit_disable_during_runtime_install_clears_pending_auto_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    dashboard = DummyDashboard()
    rebuild_calls: list[str] = []
    switch_calls: list[bool] = []
    release = SimpleNamespace(done=False)

    app = SimpleNamespace(
        view_dashboard=dashboard,
        _show_snackbar=lambda *_args, **_kwargs: None,
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    async def fake_install(**_kwargs):
        while not release.done:
            await asyncio.sleep(0)
        return object()

    async def fake_rebuild(self):
        rebuild_calls.append("rebuild")

    async def fake_switch(self):
        switch_calls.append(self._stt_desired)

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild)
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_switch)

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)

    assert controller._local_stt_pending_enable_after_install is True

    await controller.set_stt_enabled(False)

    assert controller._local_stt_pending_enable_after_install is False

    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task

    assert rebuild_calls == []
    assert switch_calls == [False]
    assert dashboard.stt_enabled is False


@pytest.mark.asyncio
async def test_local_qwen_reenable_during_runtime_install_rearms_pending_auto_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    dashboard = DummyDashboard()
    status_messages: list[str] = []
    rebuild_calls: list[str] = []
    switch_calls: list[bool] = []
    install_calls: list[str] = []
    release = SimpleNamespace(done=False)

    app = SimpleNamespace(
        view_dashboard=dashboard,
        _show_snackbar=lambda message, *_args, **_kwargs: status_messages.append(message),
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    async def fake_install(**_kwargs):
        install_calls.append("install")
        while not release.done:
            await asyncio.sleep(0)
        return object()

    async def fake_rebuild(self):
        rebuild_calls.append("rebuild")

    async def fake_switch(self):
        switch_calls.append(self._stt_desired)
        self._mic_task = object() if self._stt_desired else None

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", fake_rebuild)
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", fake_switch)

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)
    await controller.set_stt_enabled(False)
    await controller.set_stt_enabled(True)

    assert install_calls == ["install"]
    assert controller._local_stt_pending_enable_after_install is True
    assert controller_module.t("local_stt.download_in_progress") in status_messages

    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task

    assert rebuild_calls == ["rebuild"]
    assert switch_calls == [False, True]
    assert dashboard.stt_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("install_state", "expected_notice"),
    [
        (LocalSTTInstallState(status="missing"), "missing"),
        (
            LocalSTTInstallState(status="invalid", error_message="broken manifest"),
            "invalid",
        ),
        (LocalSTTInstallState(status="ready"), None),
    ],
    ids=["missing", "invalid", "ready"],
)
async def test_start_with_local_qwen_inspects_runtime_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_state: LocalSTTInstallState,
    expected_notice: str | None,
) -> None:
    (
        controller,
        dash,
        inspect_calls,
        install_calls,
    ) = await _start_controller_with_inspected_stt_state(
        monkeypatch,
        config_path=tmp_path / "settings.json",
        provider=STTProviderName.LOCAL_QWEN,
        install_state=install_state,
        hub_stt=object(),
    )

    assert inspect_calls == ["inspect", "inspect"]
    assert install_calls == []
    assert _local_stt_download_task(controller) is None
    assert dash.stt_enabled is False
    assert dash.local_stt_notice_status == expected_notice
    assert dash.local_stt_notice_percent is None


@pytest.mark.asyncio
async def test_set_stt_enabled_local_qwen_download_path_does_not_prepare_managed_translation_or_mutate_selected_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    install_calls: list[str] = []
    release = SimpleNamespace(done=False)
    app = SimpleNamespace(
        view_dashboard=DummyDashboard(),
        _show_snackbar=lambda *_args, **_kwargs: None,
    )

    class DummyWarmupHub:
        def __init__(self) -> None:
            self.stt = object()
            self.peer_stt = None
            self.promo_calls = 0

        def mark_promo_eligible(self) -> None:
            self.promo_calls += 1

    class DummyManagedReleaseService:
        def __init__(self) -> None:
            self.prepare_calls = 0

        async def prepare_for_translation(self):
            self.prepare_calls += 1
            raise AssertionError("STT runtime path must not prepare managed translation")

    async def fake_install(**_kwargs):
        install_calls.append("install")
        while not release.done:
            await asyncio.sleep(0)
        return object()

    monkeypatch.setattr(controller_module, "ensure_local_stt_installed", fake_install)
    monkeypatch.setattr(GuiController, "_rebuild_stt_provider", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(GuiController, "_ensure_stt_switch", lambda self: asyncio.sleep(0))

    controller = GuiController(page=SimpleNamespace(), app=app, config_path=Path("settings.json"))
    controller.settings = settings
    controller.hub = DummyWarmupHub()
    controller._managed_openrouter_release_service = DummyManagedReleaseService()
    controller._local_stt_install_state = LocalSTTInstallState(status="missing")

    await controller.set_stt_enabled(True)
    await asyncio.sleep(0)

    assert install_calls == ["install"]
    assert controller._managed_openrouter_release_service.prepare_calls == 0
    assert controller.settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED

    release.done = True
    download_task = _local_stt_download_task(controller)
    assert download_task is not None
    await download_task

    assert controller._managed_openrouter_release_service.prepare_calls == 0
    assert controller.settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED


@pytest.mark.asyncio
async def test_start_inspects_local_stt_without_auto_download_for_non_local_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        controller,
        dash,
        inspect_calls,
        install_calls,
    ) = await _start_controller_with_inspected_stt_state(
        monkeypatch,
        config_path=tmp_path / "settings.json",
        provider=STTProviderName.DEEPGRAM,
        install_state=LocalSTTInstallState(status="missing"),
    )

    assert inspect_calls == ["inspect", "inspect"]
    assert install_calls == []
    assert _local_stt_download_task(controller) is None
    assert dash.local_stt_notice_status is None
    assert dash.local_stt_notice_percent is None
