from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import flet as ft

from puripuly_heart.app.wiring import (
    build_peer_stt_provider_signature,
    create_llm_provider,
    create_peer_stt_backend,
    create_secret_store,
    create_stt_backend,
    resolve_peer_stt_config,
)
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
    load_settings,
    save_settings,
)
from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.desktop_source import DesktopLoopbackAudioSource
from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.audio.source import (
    SoundDeviceAudioSource,
    resolve_sounddevice_input_device,
)
from puripuly_heart.core.clock import SystemClock
from puripuly_heart.core.hardware_fingerprint import get_raw_hardware_fingerprint
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.local_stt_assets import (
    LocalSTTInstallState,
    LocalSTTManifestInvalidError,
    LocalSTTModelMissingError,
    inspect_local_stt_install_state,
)
from puripuly_heart.core.local_stt_runtime_installer import (
    LocalSTTRuntimeInstallCancelled,
    LocalSTTRuntimeInstallError,
    RuntimeLocalSTTStatusUpdate,
    ensure_local_stt_installed,
)
from puripuly_heart.core.managed_openrouter_broker_client import (
    HttpManagedOpenRouterBrokerClient,
)
from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterReleaseBehavior,
    ManagedOpenRouterReleaseService,
    UnavailableManagedOpenRouterReleaseClient,
)
from puripuly_heart.core.openrouter_credentials import resolve_openrouter_credentials
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.osc.receiver import (
    VRC_OSC_RECEIVER_HOST,
    VRC_OSC_RECEIVER_PORT,
    VrcMicState,
    VrcOscReceiver,
)
from puripuly_heart.core.osc.smart_queue import SmartOscQueue
from puripuly_heart.core.osc.udp_sender import VrchatOscUdpSender
from puripuly_heart.core.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.diagnostics import OverlayDiagnosticsRecorder
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.overlay.process import OverlayProcessManager
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntime, PeerRuntimeConfig
from puripuly_heart.core.runtime_logging import SessionLoggingMode, SessionRuntimeLoggingService
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms
from puripuly_heart.core.vad.bundled import SILERO_VAD_VERSION, ensure_silero_vad_onnx
from puripuly_heart.core.vad.gating import VadGating, create_peer_vad_gating
from puripuly_heart.core.vad.silero import SileroVadOnnx
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.openrouter import OpenRouterKeyMetadata, OpenRouterLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider
from puripuly_heart.providers.stt.deepgram import DeepgramRealtimeSTTBackend
from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaLoadError
from puripuly_heart.providers.stt.soniox import SonioxRealtimeSTTBackend
from puripuly_heart.ui.event_bridge import UIEventBridge
from puripuly_heart.ui.i18n import get_locale, set_locale, t
from puripuly_heart.ui.overlay_calibration import OverlayCalibration
from puripuly_heart.ui.views.logs import FletLogHandler

logger = logging.getLogger(__name__)

# Hardcoded STT session reset deadline (not configurable via settings)
STT_RESET_DEADLINE_S = 300.0
OVERLAY_STARTUP_TIMEOUT_MS = 3000
OVERLAY_SHUTDOWN_GRACE_S = 0.05
_OVERLAY_FAILURE_REASONS = frozenset(
    {
        "missing_executable",
        "spawn_failed",
        "manifest_invalid",
        "contract_mismatch",
        "bridge_auth_failed",
        "startup_timeout",
        "stale_overlay_build",
        "steamvr_not_installed",
        "steamvr_not_running",
        "hmd_not_found",
        "openvr_init_failed",
        "renderer_init_failed",
        "runtime_disconnected",
        "runtime_crashed",
        "unknown",
    }
)


@dataclass(slots=True)
class _HubVadSink:
    hub: ClientHub
    channel: str = "self"

    async def handle_vad_event(self, event) -> None:  # noqa: ANN001
        if self.channel == "peer":
            await self.hub.handle_peer_vad_event(event)
            return
        await self.hub.handle_vad_event(event)


@dataclass(slots=True)
class GuiController:
    page: ft.Page
    app: object
    config_path: Path

    settings: AppSettings | None = None
    clock: SystemClock = SystemClock()
    _managed_openrouter_release_service: ManagedOpenRouterReleaseService | None = None

    sender: VrchatOscUdpSender | None = None
    osc: SmartOscQueue | None = None
    hub: ClientHub | None = None
    _peer_runtime: PeerChannelRuntime | None = None
    receiver: VrcOscReceiver | None = None
    vrc_mic_state: VrcMicState | None = None
    vrc_mic_audio_gate: VrcMicAudioGate | None = None

    _bridge_task: asyncio.Task[None] | None = None
    _mic_task: asyncio.Task[None] | None = None
    _audio_source: SoundDeviceAudioSource | None = None
    _vad: VadGating | None = None
    _stt_desired: bool = False
    _stt_switch_lock: asyncio.Lock | None = None
    _stt_switch_task: asyncio.Task[None] | None = None
    _stt_restart_requested: bool = False
    _last_stt_runtime_signature: tuple[object, ...] | None = None
    _last_self_stt_runtime_signature: tuple[object, ...] | None = None
    _last_peer_stt_runtime_signature: tuple[object, ...] | None = None
    _last_self_stt_provider_signature: tuple[object, ...] | None = None
    _last_peer_stt_provider_signature: tuple[object, ...] | None = None
    _last_llm_provider_signature: tuple[object, ...] | None = None
    _last_peer_translation_enabled: bool | None = None
    _last_vrc_mic_sync_enabled: bool | None = None
    _vrc_receiver_lock: asyncio.Lock | None = None
    _ui_event_bridge: UIEventBridge | None = None
    _local_stt_install_state: LocalSTTInstallState = field(
        init=False,
        default_factory=lambda: LocalSTTInstallState(status="ready"),
    )
    _local_stt_runtime_status: str = field(init=False, default="ready")
    _local_stt_download_origin: str | None = field(init=False, default=None)
    _local_stt_download_percent: int | None = field(init=False, default=None)
    _local_stt_download_task: asyncio.Task[object] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _local_stt_download_cancel_event: threading.Event | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _local_stt_pending_enable_after_install: bool = field(init=False, default=False)
    _overlay_bridge: OverlayBridge | None = None
    _overlay_presenter: OverlayPresenter | None = None
    _overlay_manager: OverlayProcessManager | None = None
    _overlay_diagnostics: OverlayDiagnosticsRecorder | None = None
    _overlay_start_task: asyncio.Task[None] | None = None
    _overlay_monitor_task: asyncio.Task[None] | None = None
    _overlay_lock: asyncio.Lock | None = None
    _managed_trial_transient_message_key: str | None = field(init=False, default=None)
    _managed_trial_transient_message_kwargs: dict[str, object] = field(
        init=False,
        default_factory=dict,
    )
    _managed_trial_pending_auth: bool = field(init=False, default=False)
    _runtime_logging: SessionRuntimeLoggingService | None = field(init=False, default=None)

    overlay_state: str = "off"
    failure_reason: str | None = None
    auto_restart_scheduled: bool = False
    overlay_calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    _overlay_calibration_draft: OverlayCalibration | None = None

    @property
    def effective_peer_translation_enabled(self) -> bool:
        if self.settings is None:
            return False
        return self._effective_peer_translation_enabled_for(self.settings)

    @property
    def effective_context_mode(self) -> str:
        if self.settings is None:
            return "local"
        if self._effective_integrated_context_enabled_for(self.settings):
            return "integrated"
        return "local"

    def _effective_peer_translation_enabled_for(self, settings: AppSettings) -> bool:
        return bool(
            settings.ui.peer_translation_enabled
            and self._effective_peer_overlay_enabled_for(settings)
            and self.hub is not None
            and getattr(self.hub, "peer_stt", None) is not None
        )

    def _effective_peer_overlay_enabled_for(self, settings: AppSettings) -> bool:
        _ = settings
        return self.overlay_state == "connected"

    def _effective_integrated_context_enabled_for(self, settings: AppSettings) -> bool:
        return bool(
            settings.ui.integrated_context_enabled
            and self._effective_peer_translation_enabled_for(settings)
        )

    def _sync_effective_hub_flags(self, settings: AppSettings | None = None) -> None:
        resolved_settings = settings or self.settings
        if resolved_settings is None or self.hub is None:
            return
        self.hub.peer_translation_enabled = self._effective_peer_translation_enabled_for(
            resolved_settings
        )
        self.hub.integrated_context_enabled = self._effective_integrated_context_enabled_for(
            resolved_settings
        )

    async def _refresh_overlay_runtime_dependencies(self) -> None:
        if self.settings is None or self.hub is None:
            return

        await self._refresh_peer_stt_runtime()
        self._sync_effective_hub_flags(self.settings)

    async def start(self) -> None:
        self.settings = self._load_or_init_settings(self.config_path)
        self.overlay_calibration = self.settings.overlay_calibration.copy()
        self._overlay_calibration_draft = None
        set_locale(self.settings.ui.locale)
        self._sync_ui_from_settings()
        with contextlib.suppress(Exception):
            apply_locale = getattr(self.app, "apply_locale", None)
            if callable(apply_locale):
                apply_locale()

        runtime_logging = self.runtime_logging
        runtime_logging.set_mode(SessionLoggingMode.BASIC)

        # Attach realtime sink to LogsView for GUI log display
        logs_view = getattr(self.app, "view_logs", None)
        if logs_view is not None:
            runtime_logging.attach_realtime_sink(logs_view)

        await self._init_pipeline()
        self._refresh_local_stt_runtime_state()

        assert self.hub is not None

        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            # Set needs_key flags based on saved verification status & key existence
            # STT: check current provider's verification status
            stt_provider = self.settings.provider.stt.value
            if self._stt_provider_requires_secret(self.settings.provider.stt):
                # Map stt provider to api_key_verified field name (qwen_asr uses alibaba keys)
                stt_key_map = {"qwen_asr": self._get_alibaba_verified_key()}
                stt_verified_key = stt_key_map.get(stt_provider, stt_provider)
                stt_verified = getattr(self.settings.api_key_verified, stt_verified_key, False)
                dash.stt_needs_key = (self.hub.stt is None) or (not stt_verified)
            else:
                dash.stt_needs_key = False

            # LLM: check current provider's verification status
            llm_provider = self.settings.provider.llm.value
            # Map llm provider to api_key_verified field name
            llm_key_map = {
                "gemini": "google",
                "openrouter": "openrouter",
                "qwen": self._get_alibaba_verified_key(),
            }
            llm_verified_key = llm_key_map.get(llm_provider, llm_provider)
            llm_verified = getattr(self.settings.api_key_verified, llm_verified_key, False)
            dash.translation_needs_key = (
                False
                if self._managed_openrouter_can_attempt_translation()
                else (self.hub.llm is None) or (not llm_verified)
            )

            # Set initial enabled states (all start as off/gray)
            dash.set_translation_enabled(False)
            dash.set_stt_enabled(False)
            self.hub.translation_enabled = False
            await self._refresh_managed_trial_usage_state()

        await self.hub.start(auto_flush_osc=True)

        bridge = UIEventBridge(
            app=self.app,
            event_queue=self.hub.ui_events,
            runtime_logging=runtime_logging,
        )
        self._ui_event_bridge = bridge
        self._bridge_task = asyncio.create_task(bridge.run())

        if self.settings.ui.overlay_enabled:
            await self.set_overlay_enabled(True)

    def _get_alibaba_verified_key(self) -> str:
        """Get the api_key_verified field name based on Qwen region."""
        from puripuly_heart.config.settings import QwenRegion

        if self.settings.qwen.region == QwenRegion.BEIJING:
            return "alibaba_beijing"
        return "alibaba_singapore"

    def _stt_provider_applies_custom_vocabulary(self, settings: AppSettings) -> bool:
        return settings.provider.stt in (
            STTProviderName.DEEPGRAM,
            STTProviderName.LOCAL_QWEN,
            STTProviderName.SONIOX,
        )

    def _stt_provider_requires_secret(self, provider: STTProviderName) -> bool:
        return provider in (
            STTProviderName.DEEPGRAM,
            STTProviderName.QWEN_ASR,
            STTProviderName.SONIOX,
        )

    def _selected_stt_provider(self) -> STTProviderName | None:
        if self.settings is None:
            return None
        return self.settings.provider.stt

    def _dashboard_stt_needs_key(self, *, stt_available: bool) -> bool:
        provider = self._selected_stt_provider()
        if provider is None:
            return not stt_available
        return self._stt_provider_requires_secret(provider) and not stt_available

    def _stt_runtime_custom_vocabulary_signature(
        self, settings: AppSettings
    ) -> tuple[bool, tuple[str, ...]]:
        if not self._stt_provider_applies_custom_vocabulary(settings):
            return False, ()
        if settings.provider.stt == STTProviderName.LOCAL_QWEN:
            from puripuly_heart.core.stt.custom_vocab import get_effective_local_qwen_hotwords

            return (
                settings.stt.custom_vocabulary_enabled,
                tuple(
                    get_effective_local_qwen_hotwords(settings, settings.languages.source_language)
                ),
            )
        return (
            settings.stt.custom_vocabulary_enabled,
            tuple(get_effective_custom_terms(settings, settings.languages.source_language)),
        )

    def _peer_stt_runtime_custom_vocabulary_signature(
        self, settings: AppSettings
    ) -> tuple[bool, tuple[str, ...]]:
        return (
            settings.stt.custom_vocabulary_enabled,
            tuple(get_effective_custom_terms(settings, settings.languages.effective_peer_source)),
        )

    def _build_self_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        custom_vocab_enabled, custom_terms = self._stt_runtime_custom_vocabulary_signature(settings)
        return (
            settings.languages.source_language,
            settings.audio.input_host_api,
            settings.audio.input_device,
            settings.provider.stt,
            settings.stt.vad_speech_threshold,
            settings.stt.low_latency_mode,
            settings.stt.low_latency_merge_gap_ms,
            settings.stt.low_latency_spec_retry_max,
            settings.stt.low_latency_vad_hangover_ms,
            settings.stt.drain_timeout_s,
            settings.audio.ring_buffer_ms,
            settings.audio.internal_sample_rate_hz,
            settings.audio.internal_channels,
            custom_vocab_enabled,
            custom_terms,
        )

    def _build_self_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        local_qwen_identity = None
        if settings.provider.stt == STTProviderName.LOCAL_QWEN:
            from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir

            local_qwen_identity = str(default_local_stt_model_dir())

        return (
            settings.provider.stt,
            (
                settings.deepgram_stt.model
                if settings.provider.stt == STTProviderName.DEEPGRAM
                else None
            ),
            settings.qwen.region if settings.provider.stt == STTProviderName.QWEN_ASR else None,
            (
                settings.qwen_asr_stt.model
                if settings.provider.stt == STTProviderName.QWEN_ASR
                else None
            ),
            settings.soniox_stt.model if settings.provider.stt == STTProviderName.SONIOX else None,
            (
                settings.soniox_stt.endpoint
                if settings.provider.stt == STTProviderName.SONIOX
                else None
            ),
            (
                settings.soniox_stt.keepalive_interval_s
                if settings.provider.stt == STTProviderName.SONIOX
                else None
            ),
            (
                settings.soniox_stt.trailing_silence_ms
                if settings.provider.stt == STTProviderName.SONIOX
                else None
            ),
            local_qwen_identity,
        )

    def _build_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return self._build_self_stt_runtime_signature(settings)

    def _build_peer_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return self._build_peer_runtime_config(settings).runtime_signature

    def _build_peer_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return build_peer_stt_provider_signature(settings)

    def _managed_openrouter_can_attempt_translation(self) -> bool:
        return bool(
            self.settings is not None
            and self.settings.provider.llm == LLMProviderName.OPENROUTER
            and self.settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
            and self.hub is not None
            and self.hub.llm is not None
        )

    def _set_managed_trial_transient_message(
        self,
        message_key: str | None,
        message_kwargs: dict[str, object] | None = None,
    ) -> None:
        self._managed_trial_transient_message_key = message_key
        self._managed_trial_transient_message_kwargs = dict(message_kwargs or {})

    def _managed_trial_remaining_percent(
        self, usage_metadata: OpenRouterKeyMetadata | None
    ) -> int | None:
        if usage_metadata is None:
            return None
        if usage_metadata.limit_usd is None or usage_metadata.remaining_usd is None:
            return None
        if usage_metadata.limit_usd <= 0:
            return None
        return max(
            0, min(100, round((usage_metadata.remaining_usd / usage_metadata.limit_usd) * 100))
        )

    def _schedule_managed_trial_usage_refresh(self) -> None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._refresh_managed_trial_usage_state())

    def _on_managed_trial_delegate_ready(self) -> None:
        self._managed_trial_pending_auth = False
        self._set_managed_trial_transient_message(None)
        self._schedule_managed_trial_usage_refresh()

    async def _refresh_managed_trial_usage_state(self) -> None:
        view_settings = getattr(self.app, "view_settings", None)
        setter = (
            getattr(view_settings, "set_managed_trial_usage_state", None)
            if view_settings is not None
            else None
        )
        if (
            self.settings is None
            or self.settings.provider.llm != LLMProviderName.OPENROUTER
            or self.settings.openrouter.selected_source != OpenRouterCredentialSource.MANAGED
        ):
            self._managed_trial_pending_auth = False
            self._set_managed_trial_transient_message(None)
            if callable(setter):
                setter(visible=False, remaining_percent=None)
            return

        if not callable(setter):
            return

        try:
            secrets = create_secret_store(self.settings.secrets, config_path=self.config_path)
            resolution = resolve_openrouter_credentials(self.settings, secrets=secrets)
        except Exception:
            resolution = None

        usage_metadata: OpenRouterKeyMetadata | None = None
        api_key = resolution.api_key if resolution is not None else None
        if api_key:
            self._managed_trial_pending_auth = False
            usage_metadata = await OpenRouterLLMProvider.fetch_key_metadata(api_key)

        setter(
            visible=True,
            remaining_percent=self._managed_trial_remaining_percent(usage_metadata),
        )

    def _build_llm_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return (
            settings.provider.llm,
            settings.gemini.llm_model if settings.provider.llm == LLMProviderName.GEMINI else None,
            (
                settings.openrouter.llm_model
                if settings.provider.llm == LLMProviderName.OPENROUTER
                else None
            ),
            (
                settings.openrouter.routing_mode
                if settings.provider.llm == LLMProviderName.OPENROUTER
                else None
            ),
            (
                settings.openrouter.selected_source
                if settings.provider.llm == LLMProviderName.OPENROUTER
                else None
            ),
            settings.qwen.llm_model if settings.provider.llm == LLMProviderName.QWEN else None,
            settings.qwen.region if settings.provider.llm == LLMProviderName.QWEN else None,
        )

    def _sync_signature_caches(self, settings: AppSettings) -> None:
        current_self_signature = self._build_self_stt_runtime_signature(settings)
        self._last_stt_runtime_signature = current_self_signature
        self._last_self_stt_runtime_signature = current_self_signature
        self._last_peer_stt_runtime_signature = self._build_peer_stt_runtime_signature(settings)
        self._last_self_stt_provider_signature = self._build_self_stt_provider_signature(settings)
        self._last_peer_stt_provider_signature = self._build_peer_stt_provider_signature(settings)
        self._last_llm_provider_signature = self._build_llm_provider_signature(settings)
        self._last_peer_translation_enabled = settings.ui.peer_translation_enabled

    def _peer_runtime_should_be_active(self, settings: AppSettings) -> bool:
        return bool(
            settings.ui.peer_translation_enabled
            and self._effective_peer_overlay_enabled_for(settings)
            and self.hub is not None
            and self._overlay_bridge is not None
        )

    def _build_peer_runtime_config(self, settings: AppSettings) -> PeerRuntimeConfig:
        backend = resolve_peer_stt_config(settings)
        provider_signature = build_peer_stt_provider_signature(settings)
        return PeerRuntimeConfig(
            backend=backend,
            output_device=settings.desktop_audio.output_device,
            vad_threshold=settings.desktop_audio.vad_speech_threshold,
            vad_hangover_ms=settings.desktop_audio.vad_hangover_ms,
            vad_pre_roll_ms=settings.desktop_audio.vad_pre_roll_ms,
            provider_signature=provider_signature,
            runtime_signature=(
                backend.source_language,
                settings.desktop_audio.output_device,
                settings.desktop_audio.vad_speech_threshold,
                settings.desktop_audio.vad_hangover_ms,
                settings.desktop_audio.vad_pre_roll_ms,
                provider_signature,
            ),
        )

    async def stop(self) -> None:
        await self._cancel_local_stt_download()
        await self.set_stt_enabled(False)
        await self._configure_vrc_mic_receiver(enabled=False)
        await self._shutdown_overlay_runtime(preserve_failure_reason=True)
        if self._peer_runtime is not None:
            with contextlib.suppress(Exception):
                await self._peer_runtime.close()
            self._peer_runtime = None

        if self._bridge_task:
            self._bridge_task.cancel()
            await asyncio.gather(self._bridge_task, return_exceptions=True)
            self._bridge_task = None
        self._ui_event_bridge = None

        if self.hub is not None:
            with contextlib.suppress(Exception):
                await self.hub.stop()
            self.hub = None

        if self.sender is not None:
            with contextlib.suppress(Exception):
                self.sender.close()
            self.sender = None
        self.osc = None
        await self._replace_managed_openrouter_release_service(None)
        if self._runtime_logging is not None:
            with contextlib.suppress(Exception):
                self._runtime_logging.close()
            self._runtime_logging = None

    async def set_overlay_enabled(self, enabled: bool) -> None:
        if self.settings is None:
            return

        self.log_basic(f"[Overlay] Toggle request: enabled={enabled}")
        self.log_detailed(
            "[Overlay] Toggle detail: "
            f"current_state={self.overlay_state} "
            f"has_bridge={self._overlay_bridge is not None} "
            f"has_manager={self._overlay_manager is not None}"
        )
        self.settings.ui.overlay_enabled = bool(enabled)
        if not enabled:
            self.settings.ui.peer_translation_enabled = False
            self._last_peer_translation_enabled = False
        self._save_settings()

        if enabled:
            await self._begin_overlay_start()
            return

        await self._shutdown_overlay_runtime(preserve_failure_reason=True)

    def on_overlay_start_failed(self, failure_reason: str | None) -> None:
        previous_state = self.overlay_state
        self.overlay_state = "failed"
        self.failure_reason = self._normalize_overlay_failure_reason(failure_reason)
        self.auto_restart_scheduled = False
        self._log_overlay_state_transition(previous_state, self.overlay_state)
        self._sync_effective_hub_flags()
        self._notify_overlay_state()

    def on_overlay_runtime_disconnected(self) -> None:
        self.on_overlay_start_failed("runtime_disconnected")

    def on_overlay_runtime_crashed(self) -> None:
        self.on_overlay_start_failed("runtime_crashed")

    async def _begin_overlay_start(self) -> None:
        if self._overlay_lock is None:
            self._overlay_lock = asyncio.Lock()

        async with self._overlay_lock:
            if self.overlay_state in {"starting", "connected"}:
                return

            await self._teardown_overlay_runtime(preserve_presenter_state=True)
            previous_state = self.overlay_state
            self.overlay_state = "starting"
            self.auto_restart_scheduled = False
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._notify_overlay_state()
            self._overlay_start_task = asyncio.create_task(self._run_overlay_start())

    async def _run_overlay_start(self) -> None:
        current_task = asyncio.current_task()
        try:
            if self.settings is None or self.hub is None:
                self.on_overlay_start_failed("unknown")
                return

            presenter = self._overlay_presenter
            overlay_instance_id = f"overlay-{secrets.token_hex(8)}"
            diagnostics = OverlayDiagnosticsRecorder(overlay_instance_id=overlay_instance_id)

            def runtime_log_detailed(message: str, *, level: int = logging.INFO) -> bool:
                return self.log_detailed(message, level=level)

            if presenter is None:
                presenter = OverlayPresenter(
                    calibration=self.overlay_calibration.copy(),
                    clock=self.clock,
                    diagnostics=diagnostics,
                    runtime_log_detailed=runtime_log_detailed,
                    show_translation=self.settings.ui.show_overlay_translation,
                    show_peer_original=self.settings.ui.show_overlay_peer_original,
                )
                self._overlay_presenter = presenter
            else:
                presenter.diagnostics = diagnostics
                presenter.runtime_log_detailed = runtime_log_detailed
            bridge = OverlayBridge(
                session_token=secrets.token_urlsafe(16),
                initial_snapshot=presenter.snapshot(),
                overlay_instance_id=overlay_instance_id,
                diagnostics=diagnostics,
                runtime_logging_mode=self.runtime_logging_mode,
            )
            await bridge.start()
            presenter.attach_bridge(bridge)
            self._overlay_bridge = bridge
            self._overlay_diagnostics = diagnostics
            self.hub.overlay_sink = presenter
            self.hub.overlay_diagnostics = diagnostics

            manager = OverlayProcessManager(
                bridge_url=bridge.url,
                bridge_messages=bridge.messages,
                session_token=bridge.session_token,
                locale=self.settings.ui.locale,
                startup_timeout_ms=OVERLAY_STARTUP_TIMEOUT_MS,
                overlay_instance_id=overlay_instance_id,
                logging_mode=self.runtime_logging_mode,
                diagnostics=diagnostics,
            )
            self._overlay_manager = manager
            await manager.start()

            if self._overlay_manager is not manager:
                return

            if manager.state != "connected":
                await self._handle_overlay_start_failure(manager.failure_reason)
                return

            self._mark_overlay_connected()
            await self._refresh_overlay_runtime_dependencies()
            monitor_task = getattr(manager, "_monitor_task", None)
            if monitor_task is not None:
                self._overlay_monitor_task = asyncio.create_task(
                    self._watch_overlay_runtime(manager, monitor_task)
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log_detailed(
                "[Overlay] Failed to start overlay runtime",
                level=logging.ERROR,
                exception=exc,
            )
            await self._handle_overlay_start_failure("unknown")
        finally:
            if self._overlay_start_task is current_task:
                self._overlay_start_task = None

    async def _watch_overlay_runtime(
        self,
        manager: OverlayProcessManager,
        monitor_task: asyncio.Task[None],
    ) -> None:
        current_task = asyncio.current_task()
        try:
            await monitor_task
            if self._overlay_manager is not manager:
                return
            if manager.state != "failed":
                return

            reason = self._normalize_overlay_failure_reason(manager.failure_reason)
            if reason == "runtime_disconnected":
                self.on_overlay_runtime_disconnected()
            elif reason == "runtime_crashed":
                self.on_overlay_runtime_crashed()
            else:
                self.on_overlay_start_failed(reason)
            await self._teardown_overlay_runtime(preserve_presenter_state=True)
            await self._refresh_overlay_runtime_dependencies()
        except asyncio.CancelledError:
            raise
        finally:
            if self._overlay_monitor_task is current_task:
                self._overlay_monitor_task = None

    async def _handle_overlay_start_failure(self, failure_reason: str | None) -> None:
        self.on_overlay_start_failed(failure_reason)
        await self._teardown_overlay_runtime(preserve_presenter_state=True)
        await self._refresh_overlay_runtime_dependencies()

    async def _shutdown_overlay_runtime(self, *, preserve_failure_reason: bool) -> None:
        if self._overlay_lock is None:
            self._overlay_lock = asyncio.Lock()

        self.log_basic("[Overlay] Shutdown requested")
        self.log_detailed(
            "[Overlay] Shutdown detail: "
            f"preserve_failure_reason={preserve_failure_reason} "
            f"state={self.overlay_state} "
            f"has_bridge={self._overlay_bridge is not None} "
            f"has_manager={self._overlay_manager is not None} "
            f"presenter_attached={self._overlay_presenter is not None}"
        )
        async with self._overlay_lock:
            has_runtime = (
                self._overlay_bridge is not None
                or self._overlay_manager is not None
                or (self._overlay_start_task is not None and not self._overlay_start_task.done())
            )
            if not has_runtime and self.overlay_state == "off":
                return

            previous_state = self.overlay_state
            self.overlay_state = "stopping"
            self.auto_restart_scheduled = False
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._notify_overlay_state()

            await self._emit_overlay_shutdown()
            await self._teardown_overlay_runtime(preserve_presenter_state=False)
            previous_state = self.overlay_state
            self.overlay_state = "off"
            if not preserve_failure_reason:
                self.failure_reason = None
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._sync_effective_hub_flags()
            await self._refresh_overlay_runtime_dependencies()
            self._notify_overlay_state()

    async def _emit_overlay_shutdown(self) -> None:
        presenter = self._overlay_presenter
        if presenter is None:
            return
        with contextlib.suppress(Exception):
            await presenter.broadcast_shutdown()
            await asyncio.sleep(OVERLAY_SHUTDOWN_GRACE_S)

    async def _teardown_overlay_runtime(self, *, preserve_presenter_state: bool) -> None:
        current_task = asyncio.current_task()

        start_task = self._overlay_start_task
        if start_task is not None and start_task is not current_task and not start_task.done():
            start_task.cancel()
            await asyncio.gather(start_task, return_exceptions=True)
        if start_task is not None and start_task.done():
            self._overlay_start_task = None

        monitor_task = self._overlay_monitor_task
        if (
            monitor_task is not None
            and monitor_task is not current_task
            and not monitor_task.done()
        ):
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if monitor_task is not None and monitor_task.done():
            self._overlay_monitor_task = None

        presenter = self._overlay_presenter
        if not preserve_presenter_state and presenter is not None:
            with contextlib.suppress(Exception):
                await presenter.clear_for_runtime_detach()
        if presenter is not None:
            presenter.detach_bridge()
        if (
            presenter is not None
            and self.hub is not None
            and getattr(self.hub, "overlay_sink", None) is presenter
        ):
            if preserve_presenter_state:
                self.hub.overlay_sink = presenter
            else:
                self.hub.overlay_sink = None
                self.hub.overlay_diagnostics = None
                with contextlib.suppress(Exception):
                    await self.hub.reset_overlay_preview()
        if not preserve_presenter_state and presenter is not None:
            presenter.reset_scene()
            self._overlay_presenter = None

        manager = self._overlay_manager
        self._overlay_manager = None
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.stop()

        bridge = self._overlay_bridge
        self._overlay_bridge = None
        if bridge is not None:
            with contextlib.suppress(Exception):
                await bridge.stop()
        if not preserve_presenter_state:
            self._overlay_diagnostics = None

    def _mark_overlay_connected(self) -> None:
        previous_state = self.overlay_state
        self.overlay_state = "connected"
        self.failure_reason = None
        self.auto_restart_scheduled = False
        self._log_overlay_state_transition(previous_state, self.overlay_state)
        self._sync_effective_hub_flags()
        self._notify_overlay_state()

    def _normalize_overlay_failure_reason(self, failure_reason: str | None) -> str:
        if isinstance(failure_reason, str) and failure_reason in _OVERLAY_FAILURE_REASONS:
            return failure_reason
        return "unknown"

    def _notify_overlay_state(self) -> None:
        bridge = self._ui_event_bridge
        if bridge is not None:
            bridge.report_overlay_state(self.overlay_state, failure_reason=self.failure_reason)

    def _log_overlay_state_transition(self, previous_state: str, next_state: str) -> None:
        manager = self._overlay_manager
        transition_message = f"[Overlay] State transition: {previous_state} -> {next_state}"
        if self.failure_reason is not None:
            transition_message = f"{transition_message} failure_reason={self.failure_reason}"
        self.log_basic(transition_message)
        self.log_detailed(
            "[Overlay] State detail: "
            f"presenter_attached={self._overlay_presenter is not None} "
            f"bridge_attached={self._overlay_bridge is not None} "
            f"manager_state={manager.state if manager is not None else None}"
        )

    def begin_overlay_calibration(self) -> OverlayCalibration:
        if self._overlay_calibration_draft is None:
            self._overlay_calibration_draft = self.overlay_calibration.copy()
        return self._overlay_calibration_draft.copy()

    def set_overlay_calibration_field(
        self,
        field_name: str,
        value: object,
    ) -> OverlayCalibration:
        if self._overlay_calibration_draft is None:
            self._overlay_calibration_draft = self.overlay_calibration.copy()

        if field_name not in OverlayCalibration.__dataclass_fields__:
            raise ValueError(f"unknown overlay calibration field: {field_name}")

        if field_name == "anchor":
            setattr(self._overlay_calibration_draft, field_name, str(value))
        else:
            setattr(self._overlay_calibration_draft, field_name, float(value))

        self._overlay_calibration_draft.validate()
        return self._overlay_calibration_draft.copy()

    def apply_overlay_calibration(self) -> OverlayCalibration:
        if self._overlay_calibration_draft is None:
            return self.overlay_calibration.copy()

        self._overlay_calibration_draft.validate()
        self.overlay_calibration = self._overlay_calibration_draft.copy()
        self._overlay_calibration_draft = None
        if self.settings is not None:
            self.settings.overlay_calibration = self.overlay_calibration.copy()
            self._save_settings()
        self._schedule_overlay_calibration_emit()
        return self.overlay_calibration.copy()

    def cancel_overlay_calibration(self) -> OverlayCalibration:
        self._overlay_calibration_draft = None
        return self.overlay_calibration.copy()

    async def _emit_overlay_calibration_update(self) -> None:
        presenter = self._overlay_presenter
        if presenter is None:
            return
        with contextlib.suppress(Exception):
            await presenter.update_calibration(self.overlay_calibration.copy())

    def _schedule_overlay_calibration_emit(self) -> None:
        if self._overlay_presenter is None:
            return
        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._emit_overlay_calibration_update)
                return
            except Exception as exc:
                self.log_detailed(
                    "[Overlay] Failed to schedule calibration update via page.run_task",
                    level=logging.WARNING,
                    exception=exc,
                )
                return

        try:
            asyncio.get_running_loop().create_task(self._emit_overlay_calibration_update())
        except RuntimeError:
            self.log_detailed(
                "[Overlay] Skipping calibration update; no running loop and page.run_task unavailable",
                level=logging.WARNING,
            )

    def begin_overlay_calibration_for_test(self) -> None:
        self.begin_overlay_calibration()

    def set_overlay_calibration_field_for_test(self, field_name: str, value: object) -> None:
        self.set_overlay_calibration_field(field_name, value)

    def apply_overlay_calibration_for_test(self) -> None:
        self.apply_overlay_calibration()

    def cancel_overlay_calibration_for_test(self) -> None:
        self.cancel_overlay_calibration()

    async def set_translation_enabled(self, enabled: bool) -> None:
        if self.hub is None:
            return
        self.log_basic(f"[Translation] Toggle request: enabled={enabled}")
        self.log_detailed(
            "[Translation] Toggle detail: "
            f"current_enabled={self.hub.translation_enabled} "
            f"llm_available={self.hub.llm is not None}"
        )
        if enabled and await self._handle_managed_translation_enable() is False:
            return
        if enabled and self.hub.llm is None:
            self.hub.translation_enabled = False
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_translation_enabled(False)
            self._log_error("Translation is ON but LLM provider is not configured.")
            return

        # Log provider info when enabling
        if enabled and self.settings is not None:
            provider = self.settings.provider.llm.value
            if provider == "qwen":
                region = self.settings.qwen.region.value
                self.log_basic(f"[Translation] Enabled with provider: {provider}")
                self.log_detailed(
                    f"[Translation] Provider detail: provider={provider} region={region}"
                )
            else:
                self.log_basic(f"[Translation] Enabled with provider: {provider}")

        # Clear context history when toggling translation
        self.hub.clear_context()
        self.hub.translation_enabled = bool(enabled)
        if enabled and self.hub.llm is not None:
            llm = self.hub.llm
            if isinstance(llm, SemaphoreLLMProvider):
                llm = llm.inner
            if isinstance(llm, (GeminiLLMProvider, QwenLLMProvider, AsyncQwenLLMProvider)):
                with contextlib.suppress(Exception):
                    await llm.warmup()

    async def set_stt_enabled(self, enabled: bool) -> None:
        self.log_basic(f"[STT] Toggle request: enabled={enabled}")
        self.log_detailed(
            "[STT] Toggle detail: "
            f"desired_before={self._stt_desired} overlay_state={self.overlay_state}"
        )
        self._stt_desired = bool(enabled)
        if not enabled:
            self._reset_local_stt_pending_enable_after_install()

        # Log provider info when enabling
        if enabled and self.settings is not None:
            provider = self.settings.provider.stt.value
            if provider == "qwen_asr":
                region = self.settings.qwen.region.value
                self.log_basic(f"[STT] Enabled with provider: {provider}")
                self.log_detailed(f"[STT] Provider detail: provider={provider} region={region}")
            else:
                self.log_basic(f"[STT] Enabled with provider: {provider}")

        if (
            enabled
            and self.settings is not None
            and self.settings.provider.stt == STTProviderName.LOCAL_QWEN
        ):
            current_status = self._current_local_stt_runtime_status()
            if current_status == "downloading":
                self._local_stt_pending_enable_after_install = True
                self._stt_desired = False
                dash = getattr(self.app, "view_dashboard", None)
                if dash is not None:
                    dash.set_stt_enabled(False)
                self._show_short_stt_message("local_stt.download_in_progress")
                return
            if current_status in ("missing", "invalid", "download_failed"):
                self._handle_local_stt_unavailable(current_status)
                return

        # Mark promo eligible when user explicitly enables STT via button
        if enabled and self.hub is not None:
            self.hub.mark_promo_eligible()

        await self._ensure_stt_switch()

    def _show_short_stt_message(self, message_key: str) -> None:
        self._show_short_message(message_key)

    def _show_short_message(self, message_key: str, **message_kwargs: object) -> None:
        message = t(message_key, **message_kwargs)
        show_snackbar = getattr(self.app, "_show_snackbar", None)
        if callable(show_snackbar):
            with contextlib.suppress(Exception):
                show_snackbar(message, ft.Colors.ORANGE_700)
                return
        opener = getattr(self.page, "open", None)
        if callable(opener):
            with contextlib.suppress(Exception):
                opener(
                    ft.SnackBar(
                        ft.Text(message, color=ft.Colors.WHITE),
                        bgcolor=ft.Colors.ORANGE_700,
                        duration=4000,
                        behavior=ft.SnackBarBehavior.FLOATING,
                        margin=ft.margin.only(bottom=90),
                        padding=20,
                    )
                )
                return
        self._log_error(message)

    async def _handle_managed_translation_enable(self) -> bool:
        if self.settings is None or self.hub is None:
            return True
        if self.settings.provider.llm != LLMProviderName.OPENROUTER:
            return True
        if self.settings.openrouter.selected_source != OpenRouterCredentialSource.MANAGED:
            return True
        service = self._managed_openrouter_release_service
        if service is None:
            return True

        result = await service.prepare_for_translation()
        if result.behavior == ManagedOpenRouterReleaseBehavior.READY:
            self._managed_trial_pending_auth = bool(result.pending_issue and not result.api_key)
            self._set_managed_trial_transient_message(None)
            await self._refresh_managed_trial_usage_state()
            if self.hub.llm is None:
                await self._rebuild_llm_provider()
            return True

        self._managed_trial_pending_auth = False
        self._set_managed_trial_transient_message(result.message_key, dict(result.message_kwargs))
        await self._refresh_managed_trial_usage_state()
        self.hub.translation_enabled = False
        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_translation_enabled(False)
        self._show_short_message(result.message_key, **dict(result.message_kwargs))
        return False

    def _refresh_local_stt_runtime_state(self) -> None:
        if self.settings is None:
            return
        self._local_stt_install_state = inspect_local_stt_install_state()
        if self._local_stt_runtime_status not in ("downloading", "download_failed"):
            self._local_stt_runtime_status = self._local_stt_install_state.status
        self._sync_local_stt_notice()

    def _current_local_stt_runtime_status(self) -> str:
        if self._local_stt_runtime_status in ("downloading", "download_failed"):
            return self._local_stt_runtime_status
        return self._local_stt_install_state.status

    def _reset_local_stt_pending_enable_after_install(self) -> None:
        self._local_stt_pending_enable_after_install = False

    def _clear_local_stt_pending_enable_if_provider_switched_away(self) -> None:
        if self.settings is None:
            return
        if self.settings.provider.stt != STTProviderName.LOCAL_QWEN:
            self._reset_local_stt_pending_enable_after_install()

    def _sync_local_stt_notice(self) -> None:
        dash = getattr(self.app, "view_dashboard", None)
        if dash is None or self.settings is None:
            return
        status = self._current_local_stt_runtime_status()
        should_show = status == "downloading" or (
            self.settings.provider.stt == STTProviderName.LOCAL_QWEN and status != "ready"
        )
        with contextlib.suppress(Exception):
            dash.set_local_stt_notice(
                status if should_show else None,
                percent=self._local_stt_download_percent if status == "downloading" else None,
            )

    def _start_local_stt_download(self, *, origin: str) -> bool:
        task = self._local_stt_download_task
        if task is not None and not task.done():
            return False
        self._local_stt_download_origin = origin
        self._local_stt_download_percent = 0
        self._local_stt_download_cancel_event = threading.Event()
        self._local_stt_download_task = asyncio.create_task(
            self._run_local_stt_download(origin=origin)
        )
        return True

    async def _run_local_stt_download(self, *, origin: str) -> None:
        current_task = asyncio.current_task()
        cancel_event = self._local_stt_download_cancel_event
        if self.settings is None:
            return
        self._local_stt_runtime_status = "downloading"
        self._local_stt_download_percent = 0
        self._sync_local_stt_notice()
        try:
            installed = await ensure_local_stt_installed(
                locale=self.settings.ui.locale,
                on_status=self._handle_local_stt_download_status,
                cancel_event=cancel_event,
            )
        except (asyncio.CancelledError, LocalSTTRuntimeInstallCancelled):
            return
        except LocalSTTRuntimeInstallError as exc:
            self._local_stt_runtime_status = "download_failed"
            self._local_stt_download_percent = None
            self._sync_local_stt_notice()
            if origin == "manual":
                self._show_short_stt_message("local_stt.download_failed")
            self._log_error(f"Local STT download failed: {exc}")
            return
        finally:
            if self._local_stt_download_task is current_task:
                self._local_stt_download_task = None
            if self._local_stt_download_cancel_event is cancel_event:
                self._local_stt_download_cancel_event = None
            if self._local_stt_download_origin == origin:
                self._local_stt_download_origin = None

        self._local_stt_install_state = LocalSTTInstallState(
            status="ready",
            installed_manifest=installed,
        )
        self._local_stt_runtime_status = "ready"
        self._local_stt_download_percent = None
        self._clear_local_stt_pending_enable_if_provider_switched_away()
        self._sync_local_stt_notice()

        if (
            origin == "manual"
            and self.settings is not None
            and self.settings.provider.stt == STTProviderName.LOCAL_QWEN
            and self._local_stt_pending_enable_after_install
        ):
            self._reset_local_stt_pending_enable_after_install()
            await self._rebuild_stt_provider()
            self._stt_desired = True
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_stt_enabled(True)
            await self._ensure_stt_switch()

    async def _handle_local_stt_download_status(self, update: RuntimeLocalSTTStatusUpdate) -> None:
        self._local_stt_runtime_status = update.status
        self._local_stt_download_percent = update.percent
        self._sync_local_stt_notice()

    def _handle_local_stt_unavailable(self, status: str) -> bool:
        if status in ("missing", "invalid"):
            self._local_stt_install_state = LocalSTTInstallState(status=status)
        if self._local_stt_runtime_status != "downloading":
            self._local_stt_runtime_status = status
            self._local_stt_download_percent = None
        self._local_stt_pending_enable_after_install = True
        self._stt_desired = False
        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_stt_enabled(False)
            dash.set_stt_needs_key(False)
        self._sync_local_stt_notice()
        self._start_local_stt_download(origin="manual")
        return False

    async def _ensure_local_stt_ready(self) -> bool:
        if self.settings is None or self.settings.provider.stt != STTProviderName.LOCAL_QWEN:
            return True
        current_status = self._current_local_stt_runtime_status()
        if current_status == "downloading":
            self._stt_desired = False
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_stt_enabled(False)
            self._show_short_stt_message("local_stt.download_in_progress")
            return False
        if current_status in ("missing", "invalid", "download_failed"):
            return self._handle_local_stt_unavailable(current_status)
        if self.hub is None or self.hub.stt is None:
            self._stt_desired = False
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_stt_enabled(False)
                dash.set_stt_needs_key(False)
            self._show_short_stt_message("error.local_stt_model_invalid")
            return False
        try:
            await self.hub.stt.warmup()
            self._local_stt_install_state = LocalSTTInstallState(status="ready")
            if self._local_stt_runtime_status != "downloading":
                self._local_stt_runtime_status = "ready"
            self._sync_local_stt_notice()
            return True
        except LocalSTTModelMissingError:
            return self._handle_local_stt_unavailable("missing")
        except (LocalSTTManifestInvalidError, LocalQwenSherpaLoadError):
            return self._handle_local_stt_unavailable("invalid")

    async def _cancel_local_stt_download(self) -> None:
        task = self._local_stt_download_task
        cancel_event = self._local_stt_download_cancel_event
        self._reset_local_stt_pending_enable_after_install()
        if cancel_event is not None:
            cancel_event.set()
        if task is None:
            self._local_stt_download_cancel_event = None
            return
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._local_stt_download_task = None
        self._local_stt_download_cancel_event = None

    async def _ensure_stt_switch(self) -> None:
        if self._stt_switch_task is None or self._stt_switch_task.done():
            self._stt_switch_task = asyncio.create_task(self._run_stt_switch())
        await self._stt_switch_task

    async def _replace_runtime_stt_provider(self) -> None:
        self.log_detailed(
            "[STT] Replacing runtime provider detail: "
            f"desired={self._stt_desired} mic_task_active={self._mic_task is not None}"
        )
        if self._mic_task is not None:
            await self._stop_mic_loop()
        self._stt_restart_requested = False
        await self._rebuild_stt_provider()
        if self._stt_desired:
            await self._ensure_stt_switch()

    async def _run_stt_switch(self) -> None:
        if self._stt_switch_lock is None:
            self._stt_switch_lock = asyncio.Lock()
        async with self._stt_switch_lock:
            while True:
                desired = self._stt_desired
                restart = self._stt_restart_requested
                self._stt_restart_requested = False

                if not desired:
                    await self._stop_mic_loop()
                    if self.hub is not None:
                        with contextlib.suppress(Exception):
                            await self.hub.stt.close()
                else:
                    if self.hub is None:
                        self.log_detailed(
                            "[STT] Enable requested before hub is ready",
                            level=logging.WARNING,
                        )
                        break
                    if restart:
                        await self._stop_mic_loop()
                        with contextlib.suppress(Exception):
                            await self.hub.stt.close()
                    if not await self._ensure_local_stt_ready():
                        break
                    await self._start_mic_loop()
                    # Pre-warm STT session for faster first response
                    if (
                        self.hub is not None
                        and self.hub.stt is not None
                        and self._selected_stt_provider() != STTProviderName.LOCAL_QWEN
                    ):
                        with contextlib.suppress(Exception):
                            await self.hub.stt.warmup()

                if desired == self._stt_desired and not self._stt_restart_requested:
                    break

    async def submit_text(self, text: str) -> None:
        if self.hub is None:
            return
        try:
            await self.hub.submit_text(text, source="You")
        except Exception as exc:
            self._log_error(f"Submit failed: {exc}")

    async def apply_settings(self, settings: AppSettings) -> None:
        prev_locale = get_locale()
        prev_overlay_enabled = (
            self.settings.ui.overlay_enabled if self.settings is not None else False
        )
        prev_peer_translation_enabled = (
            self._last_peer_translation_enabled
            if self._last_peer_translation_enabled is not None
            else (self.settings.ui.peer_translation_enabled if self.settings is not None else False)
        )
        prev_self_signature = (
            self._last_self_stt_runtime_signature or self._last_stt_runtime_signature
        )
        prev_peer_signature = self._last_peer_stt_runtime_signature
        # hub.source_language를 기준으로 비교 (settings 객체는 이미 수정되어 전달될 수 있음)
        prev_source_lang = self.hub.source_language if self.hub else None
        prev_target_lang = self.hub.target_language if self.hub else None
        prev_low_latency = self.hub.low_latency_mode if self.hub else None
        source_language_changed = (
            prev_source_lang is not None and prev_source_lang != settings.languages.source_language
        )
        target_language_changed = (
            prev_target_lang is not None and prev_target_lang != settings.languages.target_language
        )
        if source_language_changed or target_language_changed:
            presenter = self._overlay_presenter
            self.log_basic(
                "[Settings] Applying languages: "
                f"source={prev_source_lang}->{settings.languages.source_language} "
                f"target={prev_target_lang}->{settings.languages.target_language}"
            )
            self.log_detailed(
                "[Settings] Language apply detail: "
                f"overlay_state={self.overlay_state} "
                f"presenter_attached={presenter is not None} "
                f"bridge_attached={self._overlay_bridge is not None} "
                "overlay_sink_matches_presenter="
                f"{self.hub is not None and presenter is not None and getattr(self.hub, 'overlay_sink', None) is presenter}"
            )
        self.settings = settings
        self._save_settings()
        self._refresh_local_stt_runtime_state()
        self._clear_local_stt_pending_enable_if_provider_switched_away()

        # low_latency_mode 변경 시 Qwen LLM 프로바이더 재생성 필요
        # (AsyncQwenLLMProvider vs QwenLLMProvider 전환)
        if (
            prev_low_latency is not None
            and prev_low_latency != settings.stt.low_latency_mode
            and self.settings.provider.llm.value == "qwen"
        ):
            self.log_detailed(
                "[Settings] Low latency detail: "
                f"mode={prev_low_latency}->{settings.stt.low_latency_mode} rebuilding_llm_provider=True"
            )
            await self._rebuild_llm_provider()

        if self.hub is not None:
            self.hub.source_language = settings.languages.source_language
            self.hub.target_language = settings.languages.target_language
            self.hub.peer_source_language = settings.languages.peer_source_language
            self.hub.peer_target_language = settings.languages.peer_target_language
            self.hub.system_prompt = settings.system_prompt
            self.hub.low_latency_mode = settings.stt.low_latency_mode
            self.hub.low_latency_merge_gap_ms = settings.stt.low_latency_merge_gap_ms
            self.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
            self.hub.hangover_s = (
                settings.stt.low_latency_vad_hangover_ms / 1000.0
                if settings.stt.low_latency_mode
                else 1.1
            )
            self.hub.chatbox_include_source = settings.osc.chatbox_include_source
            self._sync_effective_hub_flags(settings)

        presenter = self._overlay_presenter
        if presenter is not None:
            await presenter.update_display_preferences(
                show_translation=settings.ui.show_overlay_translation,
                show_peer_original=settings.ui.show_overlay_peer_original,
            )

        if prev_overlay_enabled != settings.ui.overlay_enabled:
            await self.set_overlay_enabled(settings.ui.overlay_enabled)

        if self._last_vrc_mic_sync_enabled != settings.osc.vrc_mic_intercept:
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_enabled(settings.osc.vrc_mic_intercept)
            self.log_detailed(f"[Settings] VRC mic sync enabled: {settings.osc.vrc_mic_intercept}")
            await self._configure_vrc_mic_receiver(enabled=settings.osc.vrc_mic_intercept)

        current_self_signature = self._build_self_stt_runtime_signature(settings)
        current_peer_signature = self._build_peer_stt_runtime_signature(settings)
        should_restart_stt = (
            prev_self_signature is not None and current_self_signature != prev_self_signature
        )
        should_refresh_peer = (
            prev_peer_signature is None
            or current_peer_signature != prev_peer_signature
            or prev_peer_translation_enabled != settings.ui.peer_translation_enabled
        )

        self._sync_signature_caches(settings)

        if source_language_changed or target_language_changed:
            self.log_detailed(
                "[Settings] Language runtime impact: "
                f"should_restart_stt={should_restart_stt} "
                f"should_refresh_peer={should_refresh_peer} "
                f"prev_overlay_enabled={prev_overlay_enabled} "
                f"next_overlay_enabled={settings.ui.overlay_enabled}"
            )

        if should_refresh_peer and self.hub is not None:
            await self._refresh_peer_stt_runtime()
            self._sync_effective_hub_flags(settings)

        if should_restart_stt:
            await self._replace_runtime_stt_provider()

        if source_language_changed:
            view_settings = getattr(self.app, "view_settings", None)
            if view_settings is not None:
                with contextlib.suppress(Exception):
                    view_settings.load_from_settings(
                        settings,
                        config_path=self.config_path,
                        preserve_custom_vocab_draft=True,
                    )

        if prev_locale != settings.ui.locale:
            set_locale(settings.ui.locale)
            apply_locale = getattr(self.app, "apply_locale", None)
            if callable(apply_locale):
                try:
                    apply_locale()
                except Exception as exc:
                    self._log_error(f"Failed to apply locale: {exc}")

    async def verify_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        """Verify API key using the respective provider's static check. Returns (success, error_msg)."""
        if not key:
            return False, "API Key is empty"

        try:
            success = False
            if provider == "google":
                success = await GeminiLLMProvider.verify_api_key(key)
            elif provider == "openrouter":
                success = await OpenRouterLLMProvider.verify_api_key(key)
            elif provider == "alibaba_beijing":
                return await self._verify_qwen_key_with_model_fallback(
                    key,
                    base_url="https://dashscope.aliyuncs.com/api/v1",
                )
            elif provider == "alibaba_singapore":
                return await self._verify_qwen_key_with_model_fallback(
                    key,
                    base_url="https://dashscope-intl.aliyuncs.com/api/v1",
                )
            elif provider == "deepgram":
                success = await DeepgramRealtimeSTTBackend.verify_api_key(key)
            elif provider == "soniox":
                success = await SonioxRealtimeSTTBackend.verify_api_key(key)
            else:
                return False, f"Unknown provider: {provider}"

            if success:
                return True, "Verification successful"
            else:
                return False, "Verification failed (check logs/console for details)"
        except Exception as exc:
            msg = f"Verification error for {provider}: {exc}"
            self._log_error(msg)
            return False, str(exc)

    async def apply_providers(self, settings: AppSettings | None = None) -> None:
        next_settings = settings or self.settings
        if next_settings is None:
            return

        prev_settings = self.settings
        prev_self_provider_signature = self._last_self_stt_provider_signature
        prev_peer_provider_signature = self._last_peer_stt_provider_signature
        prev_llm_provider_signature = self._last_llm_provider_signature

        if prev_settings is not None:
            if prev_self_provider_signature is None:
                prev_self_provider_signature = self._build_self_stt_provider_signature(
                    prev_settings
                )
            if prev_peer_provider_signature is None:
                prev_peer_provider_signature = self._build_peer_stt_provider_signature(
                    prev_settings
                )
            if prev_llm_provider_signature is None:
                prev_llm_provider_signature = self._build_llm_provider_signature(prev_settings)

        next_self_provider_signature = self._build_self_stt_provider_signature(next_settings)
        next_peer_provider_signature = self._build_peer_stt_provider_signature(next_settings)
        next_llm_provider_signature = self._build_llm_provider_signature(next_settings)

        should_rebuild_llm = (
            prev_llm_provider_signature is None
            or next_llm_provider_signature != prev_llm_provider_signature
        )
        should_refresh_peer = (
            prev_peer_provider_signature is None
            or next_peer_provider_signature != prev_peer_provider_signature
        )
        should_refresh_self_stt = (
            prev_self_provider_signature is None
            or next_self_provider_signature != prev_self_provider_signature
        )

        self.settings = next_settings
        self._save_settings()
        self._clear_local_stt_pending_enable_if_provider_switched_away()

        if self.hub is not None:
            self.hub.source_language = next_settings.languages.source_language
            self.hub.target_language = next_settings.languages.target_language
            self.hub.peer_source_language = next_settings.languages.peer_source_language
            self.hub.peer_target_language = next_settings.languages.peer_target_language
            self.hub.system_prompt = next_settings.system_prompt
            self.hub.low_latency_mode = next_settings.stt.low_latency_mode
            self.hub.low_latency_merge_gap_ms = next_settings.stt.low_latency_merge_gap_ms
            self.hub.low_latency_spec_retry_max = next_settings.stt.low_latency_spec_retry_max
            self.hub.hangover_s = (
                next_settings.stt.low_latency_vad_hangover_ms / 1000.0
                if next_settings.stt.low_latency_mode
                else 1.1
            )
            self.hub.chatbox_include_source = next_settings.osc.chatbox_include_source
            self._sync_effective_hub_flags(next_settings)

        if should_rebuild_llm:
            await self._rebuild_llm_provider()

        if should_refresh_peer:
            await self._refresh_peer_stt_runtime()
            self._sync_effective_hub_flags(next_settings)

        if should_refresh_self_stt:
            if self._stt_desired:
                await self._replace_runtime_stt_provider()
            else:
                await self._rebuild_stt_provider()

        self._sync_signature_caches(next_settings)

    def _load_or_init_settings(self, path: Path) -> AppSettings:
        if path.exists():
            return load_settings(path)
        settings = AppSettings()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_settings(path, settings)
        return settings

    async def _rebuild_llm_provider(self) -> None:
        """Rebuild only the LLM provider without tearing down the entire pipeline."""
        if self.hub is None or self.settings is None:
            return

        # Close existing LLM provider
        previous_llm = self.hub.llm
        self.hub.llm = None
        if previous_llm is not None:
            with contextlib.suppress(Exception):
                await previous_llm.close()

        # Create new LLM provider with current settings
        llm = None
        llm_error: Exception | None = None
        try:
            secrets = create_secret_store(self.settings.secrets, config_path=self.config_path)
            new_managed_release_service = self._create_managed_openrouter_release_service(
                secrets=secrets
            )
            await self._replace_managed_openrouter_release_service(new_managed_release_service)
            llm = create_llm_provider(
                self.settings,
                secrets=secrets,
                managed_release_service=self._managed_openrouter_release_service,
                managed_delegate_ready=self._on_managed_trial_delegate_ready,
                runtime_logging=self.runtime_logging,
            )
        except Exception as exc:
            llm_error = exc

        # Update hub's LLM provider
        self.hub.llm = llm

        # Update dashboard status
        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_translation_needs_key(llm is None)

        await self._refresh_managed_trial_usage_state()

        if llm is None:
            message = "LLM provider not available"
            if llm_error is not None:
                message = f"{message}: {llm_error}"
            self._log_error(message)
            return

        self.log_basic("[Settings] LLM provider rebuilt successfully")

    async def _rebuild_stt_provider(self) -> None:
        """Rebuild only the STT provider so later enable uses current settings."""
        if self.hub is None or self.settings is None:
            return

        stt = None
        stt_error: Exception | None = None
        try:
            secrets = create_secret_store(self.settings.secrets, config_path=self.config_path)
            backend = create_stt_backend(self.settings, secrets=secrets)
            stt = ManagedSTTProvider(
                backend=backend,
                sample_rate_hz=self.settings.audio.internal_sample_rate_hz,
                clock=self.clock,
                reset_deadline_s=STT_RESET_DEADLINE_S,
                drain_timeout_s=self.settings.stt.drain_timeout_s,
                bridging_ms=self.settings.audio.ring_buffer_ms,
                runtime_logging=self.runtime_logging,
            )
        except Exception as exc:
            stt_error = exc

        await self.hub.replace_stt_provider(stt)
        self._sync_effective_hub_flags(self.settings)

        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_stt_needs_key(self._dashboard_stt_needs_key(stt_available=stt is not None))
            if stt is None:
                dash.set_stt_enabled(False)

        if stt is None:
            assert stt_error is not None
            self._log_error(f"STT backend not available: {stt_error}")
            return

        self.log_basic("[Settings] STT provider replacement completed successfully")

    def _create_peer_stt_provider_from_runtime_config(
        self,
        config: PeerRuntimeConfig,
        on_terminal_failure,
    ) -> ManagedSTTProvider:
        assert self.settings is not None
        secrets = create_secret_store(self.settings.secrets, config_path=self.config_path)
        peer_backend = create_peer_stt_backend(self.settings, secrets=secrets)
        return ManagedSTTProvider(
            backend=peer_backend,
            sample_rate_hz=config.backend.sample_rate_hz,
            channel="peer",
            clock=self.clock,
            reset_deadline_s=STT_RESET_DEADLINE_S,
            drain_timeout_s=self.settings.stt.drain_timeout_s,
            bridging_ms=max(1, config.vad_pre_roll_ms),
            on_terminal_failure=on_terminal_failure,
            runtime_logging=self.runtime_logging,
        )

    def _create_peer_audio_source_from_runtime_config(self, config: PeerRuntimeConfig):
        return DesktopPeerPipeline(
            source=DesktopLoopbackAudioSource(device_name=config.output_device),
            target_sample_rate_hz=config.backend.sample_rate_hz,
        )

    def _create_peer_vad_from_runtime_config(self, config: PeerRuntimeConfig, model_path: Path):
        return create_peer_vad_gating(
            engine=SileroVadOnnx(model_path=model_path),
            sample_rate_hz=config.backend.sample_rate_hz,
            ring_buffer_ms=config.vad_pre_roll_ms,
            speech_threshold=config.vad_threshold,
            hangover_ms=config.vad_hangover_ms,
        )

    async def _refresh_peer_stt_runtime(self) -> None:
        if self.settings is None or self.hub is None or self._peer_runtime is None:
            return

        config = self._build_peer_runtime_config(self.settings)
        desired_active = self._peer_runtime_should_be_active(self.settings)
        await self._peer_runtime.apply_policy(config=config, desired_active=desired_active)
        self._last_peer_stt_runtime_signature = config.runtime_signature
        self._sync_effective_hub_flags(self.settings)

    async def _rebuild_pipeline(self, *, rebuild_stt: bool) -> None:
        self.log_detailed(
            f"[Settings] Rebuilding pipeline detail: rebuild_stt={rebuild_stt} overlay_state={self.overlay_state}"
        )
        _ = rebuild_stt
        restore_stt_enabled = self._stt_desired
        if self._bridge_task:
            self._bridge_task.cancel()
            await asyncio.gather(self._bridge_task, return_exceptions=True)
            self._bridge_task = None

        peer_runtime = self._peer_runtime
        if peer_runtime is not None:
            with contextlib.suppress(Exception):
                await peer_runtime.close()
            self._peer_runtime = None

        await self.set_stt_enabled(False)
        await self._configure_vrc_mic_receiver(enabled=False)
        if self.hub is not None:
            with contextlib.suppress(Exception):
                await self.hub.stop()
        if self.sender is not None:
            with contextlib.suppress(Exception):
                self.sender.close()
        self.sender = None
        self.osc = None
        self.hub = None
        await self._init_pipeline()
        assert self.hub is not None
        presenter = self._overlay_presenter
        if presenter is not None:
            self.hub.overlay_sink = presenter

        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_translation_needs_key(self.hub.llm is None)
            dash.set_stt_needs_key(
                self._dashboard_stt_needs_key(stt_available=self.hub.stt is not None)
            )

            self.hub.translation_enabled = (
                bool(getattr(dash, "is_translation_on", True)) and self.hub.llm is not None
            )
            dash.set_translation_enabled(self.hub.translation_enabled)

        await self.hub.start(auto_flush_osc=True)

        bridge = UIEventBridge(
            app=self.app,
            event_queue=self.hub.ui_events,
            runtime_logging=self.runtime_logging,
        )
        self._bridge_task = asyncio.create_task(bridge.run())

        if self.overlay_state == "connected" and presenter is not None:
            await self._refresh_overlay_runtime_dependencies()

        if restore_stt_enabled:
            await self.set_stt_enabled(True)

        # Trigger background verification to sync button colors
        asyncio.create_task(self._verify_and_update_status())

    async def _init_pipeline(self) -> None:
        assert self.settings is not None
        self._sync_signature_caches(self.settings)
        secrets = create_secret_store(self.settings.secrets, config_path=self.config_path)
        new_managed_release_service = self._create_managed_openrouter_release_service(
            secrets=secrets
        )
        await self._replace_managed_openrouter_release_service(new_managed_release_service)

        llm = None
        with contextlib.suppress(Exception):
            llm = create_llm_provider(
                self.settings,
                secrets=secrets,
                managed_release_service=self._managed_openrouter_release_service,
                managed_delegate_ready=self._on_managed_trial_delegate_ready,
                runtime_logging=self.runtime_logging,
            )

        stt = None
        try:
            backend = create_stt_backend(self.settings, secrets=secrets)
            stt = ManagedSTTProvider(
                backend=backend,
                sample_rate_hz=self.settings.audio.internal_sample_rate_hz,
                clock=self.clock,
                reset_deadline_s=STT_RESET_DEADLINE_S,
                drain_timeout_s=self.settings.stt.drain_timeout_s,
                bridging_ms=self.settings.audio.ring_buffer_ms,
                runtime_logging=self.runtime_logging,
            )
        except Exception as exc:
            self._log_error(f"STT backend not available: {exc}")

        sender = VrchatOscUdpSender(
            host=self.settings.osc.host,
            port=self.settings.osc.port,
            chatbox_address=self.settings.osc.chatbox_address,
            chatbox_send=self.settings.osc.chatbox_send,
            chatbox_clear=self.settings.osc.chatbox_clear,
        )
        osc = SmartOscQueue(
            sender=sender,
            clock=self.clock,
            max_chars=self.settings.osc.chatbox_max_chars,
            cooldown_s=self.settings.osc.cooldown_s,
            ttl_s=self.settings.osc.ttl_s,
            runtime_logging=self.runtime_logging,
        )

        hub = ClientHub(
            stt=stt,
            llm=llm,
            osc=osc,
            peer_stt=None,
            clock=self.clock,
            runtime_logging=self.runtime_logging,
            source_language=self.settings.languages.source_language,
            target_language=self.settings.languages.target_language,
            peer_source_language=self.settings.languages.peer_source_language,
            peer_target_language=self.settings.languages.peer_target_language,
            system_prompt=self.settings.system_prompt,
            chatbox_include_source=self.settings.osc.chatbox_include_source,
            fallback_transcript_only=True,
            translation_enabled=True,
            peer_translation_enabled=False,
            integrated_context_enabled=False,
            low_latency_mode=self.settings.stt.low_latency_mode,
            low_latency_merge_gap_ms=self.settings.stt.low_latency_merge_gap_ms,
            low_latency_spec_retry_max=self.settings.stt.low_latency_spec_retry_max,
            hangover_s=(
                self.settings.stt.low_latency_vad_hangover_ms / 1000.0
                if self.settings.stt.low_latency_mode
                else 1.1
            ),
        )

        if self.vrc_mic_state is None:
            self.vrc_mic_state = VrcMicState()
        if self.vrc_mic_audio_gate is None:
            self.vrc_mic_audio_gate = VrcMicAudioGate(
                state=self.vrc_mic_state,
                enabled=self.settings.osc.vrc_mic_intercept,
            )
        else:
            self.vrc_mic_audio_gate.state = self.vrc_mic_state
            self.vrc_mic_audio_gate.set_enabled(self.settings.osc.vrc_mic_intercept)
        self.vrc_mic_audio_gate.set_receiver_active(self.receiver is not None)
        self.vrc_mic_audio_gate.reset()

        self.sender = sender
        self.osc = osc
        self.hub = hub
        from puripuly_heart.app.headless_mic import run_audio_vad_loop

        self._peer_runtime = PeerChannelRuntime(
            hub=hub,
            clock=self.clock,
            stt_factory=self._create_peer_stt_provider_from_runtime_config,
            source_factory=self._create_peer_audio_source_from_runtime_config,
            vad_factory=self._create_peer_vad_from_runtime_config,
            vad_model_resolver=ensure_silero_vad_onnx,
            run_audio_loop=run_audio_vad_loop,
        )
        self._last_peer_translation_enabled = self.settings.ui.peer_translation_enabled
        await self._configure_vrc_mic_receiver(enabled=self.settings.osc.vrc_mic_intercept)

    async def _replace_managed_openrouter_release_service(
        self,
        service: ManagedOpenRouterReleaseService | None,
    ) -> None:
        previous = self._managed_openrouter_release_service
        self._managed_openrouter_release_service = service
        if previous is not None and previous is not service:
            with contextlib.suppress(Exception):
                await previous.close()

    def _create_managed_openrouter_release_service(
        self, *, secrets
    ) -> ManagedOpenRouterReleaseService | None:
        if self.settings is None:
            return None
        if self.settings.provider.llm != LLMProviderName.OPENROUTER:
            return None
        if self.settings.openrouter.selected_source != OpenRouterCredentialSource.MANAGED:
            return None

        from puripuly_heart import __version__

        try:
            client = HttpManagedOpenRouterBrokerClient(
                base_url=self.settings.openrouter.broker_base_url,
            )
        except ValueError as exc:
            logger.warning(
                "[Managed OpenRouter] Invalid broker base URL %r; using unavailable fallback: %s",
                self.settings.openrouter.broker_base_url,
                exc,
            )
            client = UnavailableManagedOpenRouterReleaseClient()

        return ManagedOpenRouterReleaseService(
            settings=self.settings,
            secrets=secrets,
            client=client,
            persist_settings=lambda updated: save_settings(self.config_path, updated),
            raw_hardware_fingerprint_provider=get_raw_hardware_fingerprint,
            app_version=__version__,
        )

    async def _start_mic_loop(self) -> None:
        assert self.settings is not None
        assert self.hub is not None

        if self._mic_task is not None:
            return

        try:
            model_path = ensure_silero_vad_onnx()
        except Exception as exc:
            self._log_error(f"Failed to prepare Silero VAD model ({SILERO_VAD_VERSION}): {exc}")
            return

        if self._mic_task is None:
            vad = VadGating(
                engine=SileroVadOnnx(model_path=model_path),
                sample_rate_hz=self.settings.audio.internal_sample_rate_hz,
                ring_buffer_ms=self.settings.audio.ring_buffer_ms,
                speech_threshold=self.settings.stt.vad_speech_threshold,
                hangover_ms=(
                    self.settings.stt.low_latency_vad_hangover_ms
                    if self.settings.stt.low_latency_mode
                    else 1100
                ),
            )

            def _resolve_device(host_api: str, device: str) -> int | None:
                try:
                    return resolve_sounddevice_input_device(host_api=host_api, device=device)
                except Exception as exc:
                    self.log_detailed(
                        "[STT] Device resolution detail: "
                        f"host_api={host_api!r} device={device!r} error={exc}",
                        level=logging.WARNING,
                    )
                    return None

            def _open_source(dev_idx: int | None) -> SoundDeviceAudioSource:
                return SoundDeviceAudioSource(
                    sample_rate_hz=None,
                    channels=self.settings.audio.internal_channels,
                    device=dev_idx,
                )

            host_api = self.settings.audio.input_host_api
            device_name = self.settings.audio.input_device

            # 1차 시도: 설정된 Host API + 마이크
            device_idx = _resolve_device(host_api, device_name)
            source: SoundDeviceAudioSource | None = None

            try:
                source = _open_source(device_idx)
                self.log_detailed(f"[STT] Microphone opened: device_idx={device_idx}")
            except Exception as exc:
                self.log_detailed(
                    "[STT] Microphone open detail: "
                    f"host_api={host_api!r} device={device_name!r} error={exc}",
                    level=logging.ERROR,
                )

            # 2차 시도: Host API 무시, 마이크 이름만
            if source is None and device_name:
                fallback_idx = _resolve_device("", device_name)
                if fallback_idx != device_idx:
                    try:
                        source = _open_source(fallback_idx)
                        self.log_detailed(
                            f"[STT] Microphone opened with fallback: device_idx={fallback_idx}"
                        )
                    except Exception as exc:
                        self.log_detailed(
                            f"[STT] Fallback microphone detail: error={exc}",
                            level=logging.ERROR,
                        )

            # 3차 시도: 시스템 기본 장치
            if source is None:
                try:
                    source = _open_source(None)
                    self.log_detailed("[STT] Microphone opened with system default")
                except Exception as exc:
                    self.log_detailed(
                        f"[STT] System default microphone detail: error={exc}",
                        level=logging.ERROR,
                    )

            if source is None:
                self._log_error("All microphone attempts failed")
                return

            self._vad = vad
            self._audio_source = source
            self._mic_task = asyncio.create_task(self._run_mic_loop())

    async def _stop_mic_loop(self) -> None:
        if self._mic_task is not None:
            self._mic_task.cancel()
            await asyncio.gather(self._mic_task, return_exceptions=True)
            self._mic_task = None

        if self._audio_source is not None:
            with contextlib.suppress(Exception):
                await self._audio_source.close()
            self._audio_source = None
        self._vad = None
        if self.vrc_mic_audio_gate is not None:
            self.vrc_mic_audio_gate.reset()

    async def _run_mic_loop(self) -> None:
        assert self.hub is not None
        assert self._audio_source is not None
        assert self._vad is not None

        from puripuly_heart.app.headless_mic import run_audio_vad_loop

        try:
            await run_audio_vad_loop(
                source=self._audio_source,
                vad=self._vad,
                sink=_HubVadSink(hub=self.hub),
                target_sample_rate_hz=self.settings.audio.internal_sample_rate_hz,  # type: ignore[union-attr]
                audio_gate=self.vrc_mic_audio_gate,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_error(f"Mic loop error: {exc}")

    async def _configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        if self._vrc_receiver_lock is None:
            self._vrc_receiver_lock = asyncio.Lock()

        async with self._vrc_receiver_lock:
            self._last_vrc_mic_sync_enabled = enabled
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_enabled(enabled)

            if not enabled:
                self._stop_vrc_mic_receiver()
                return

            if self.receiver is not None or self.vrc_mic_state is None:
                if self.vrc_mic_audio_gate is not None:
                    self.vrc_mic_audio_gate.set_receiver_active(self.receiver is not None)
                return

            receiver = VrcOscReceiver(
                state=self.vrc_mic_state,
                host=VRC_OSC_RECEIVER_HOST,
                port=VRC_OSC_RECEIVER_PORT,
            )
            try:
                await receiver.start()
            except OSError as exc:
                if self.vrc_mic_audio_gate is not None:
                    self.vrc_mic_audio_gate.set_receiver_active(False)
                self._log_error(
                    "VRChat mic sync receiver unavailable on "
                    f"{VRC_OSC_RECEIVER_HOST}:{VRC_OSC_RECEIVER_PORT}: {exc}"
                )
                return

            self.receiver = receiver
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_receiver_active(True)
                self.vrc_mic_audio_gate.reset()

    def _stop_vrc_mic_receiver(self) -> None:
        if self.receiver is not None:
            with contextlib.suppress(Exception):
                self.receiver.stop()
            self.receiver = None
        if self.vrc_mic_audio_gate is not None:
            self.vrc_mic_audio_gate.set_receiver_active(False)

    def _save_settings(self) -> None:
        assert self.settings is not None
        try:
            save_settings(self.config_path, self.settings)
        except Exception as exc:
            self._log_error(f"Failed to save settings: {exc}")

    def _sync_ui_from_settings(self) -> None:
        settings = self.settings
        if settings is None:
            return

        # Dashboard language dropdowns are initialized by the view; set values if present.
        with contextlib.suppress(Exception):
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_languages_from_codes(
                    settings.languages.source_language, settings.languages.target_language
                )
                # Load recent languages from settings
                dash.set_recent_languages(
                    settings.languages.recent_source_languages,
                    settings.languages.recent_target_languages,
                )
                # Connect callback for persistence
                dash.on_recent_languages_change = self._on_recent_languages_change

        with contextlib.suppress(Exception):
            view_settings = getattr(self.app, "view_settings", None)
            if view_settings is not None:
                view_settings.load_from_settings(settings, config_path=self.config_path)
                view_settings.set_overlay_calibration(self.overlay_calibration)

    def _on_recent_languages_change(self, source: list[str], target: list[str]) -> None:
        """Callback when recent languages change in dashboard."""
        if self.settings is None:
            return
        self.settings.languages.recent_source_languages = list(source)
        self.settings.languages.recent_target_languages = list(target)
        self._save_settings()

    @property
    def runtime_logging(self) -> SessionRuntimeLoggingService:
        if self._runtime_logging is None:
            self._runtime_logging = SessionRuntimeLoggingService(ui_handler_factory=FletLogHandler)
        logs_view = getattr(self.app, "view_logs", None)
        if logs_view is not None:
            self._runtime_logging.attach_realtime_sink(logs_view)
        return self._runtime_logging

    @property
    def runtime_logging_mode(self) -> str:
        return self.runtime_logging.mode.value

    def set_runtime_logging_mode(self, mode: SessionLoggingMode | str) -> None:
        self.runtime_logging.set_mode(mode)
        normalized_mode = self.runtime_logging.mode.value
        manager = self._overlay_manager
        if manager is not None:
            set_logging_mode = getattr(manager, "set_logging_mode", None)
            if callable(set_logging_mode):
                set_logging_mode(normalized_mode)
        self._schedule_overlay_runtime_logging_mode_update()

    async def _emit_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return
        await bridge.broadcast_runtime_control(logging_mode=self.runtime_logging_mode)

    def _schedule_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return

        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._emit_overlay_runtime_logging_mode_update)
                return
            except Exception as exc:
                self.log_detailed(
                    "[Overlay] Failed to schedule logging mode update via page.run_task",
                    level=logging.WARNING,
                    exception=exc,
                )
                return

        try:
            asyncio.get_running_loop().create_task(self._emit_overlay_runtime_logging_mode_update())
        except RuntimeError:
            self.log_detailed(
                "[Overlay] Skipping logging mode update; no running loop and page.run_task unavailable",
                level=logging.WARNING,
            )

    def log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        try:
            self.runtime_logging.emit_basic(message, level=level)
            return
        except Exception:
            logger.log(level, message)

    def log_detailed(
        self,
        message: str,
        *,
        level: int = logging.INFO,
        exception: BaseException | None = None,
    ) -> bool:
        rendered_message = message
        exc_info = None
        if exception is not None:
            exc_info = (type(exception), exception, exception.__traceback__)
            rendered_message = (
                f"{message}\n{''.join(traceback.format_exception(*exc_info)).rstrip()}"
            )
        try:
            return self.runtime_logging.emit_detailed(rendered_message, level=level)
        except Exception:
            logger.log(level, message, exc_info=exc_info)
            return True

    def _log_error(self, message: str) -> None:
        self.log_basic(message, level=logging.ERROR)

    def _get_qwen_key_and_base_url(self, secrets) -> tuple[str, str]:
        if self.settings is None:
            return "", ""
        if self.settings.qwen.region == QwenRegion.BEIJING:
            target_key = "alibaba_api_key_beijing"
        else:
            target_key = "alibaba_api_key_singapore"

        api_key = secrets.get(target_key) or ""
        if api_key:
            return api_key, self.settings.qwen.get_llm_base_url()

        # Backward compatibility: legacy single-key storage from older versions.
        legacy_key = secrets.get("alibaba_api_key") or ""
        if legacy_key:
            setter = getattr(secrets, "set", None)
            if callable(setter):
                with contextlib.suppress(Exception):
                    setter(target_key, legacy_key)
            return legacy_key, self.settings.qwen.get_llm_base_url()

        return "", self.settings.qwen.get_llm_base_url()

    async def _verify_qwen_key_with_model_fallback(
        self,
        api_key: str,
        *,
        base_url: str,
    ) -> tuple[bool, str]:
        if self.settings is None:
            return False, "Verification failed (check logs/console for details)"

        selected_model = self.settings.qwen.llm_model.value
        if await self._verify_qwen_llm_api_key(api_key, base_url=base_url, model=selected_model):
            return True, "Verification successful"

        for fallback_model in (
            model.value for model in QwenLLMModel if model.value != selected_model
        ):
            if await self._verify_qwen_llm_api_key(
                api_key,
                base_url=base_url,
                model=fallback_model,
            ):
                return False, f"qwen_model_unavailable:{selected_model}"

        return False, "Verification failed (check logs/console for details)"

    async def _verify_qwen_llm_api_key(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str | None = None,
    ) -> bool:
        if self.settings is None:
            return False
        runtime_model = model or self.settings.qwen.llm_model.value
        if self.settings.stt.low_latency_mode:
            async_base_url = base_url.replace("/api/v1", "/compatible-mode/v1")
            return await AsyncQwenLLMProvider.verify_api_key(
                api_key,
                base_url=async_base_url,
                model=runtime_model,
            )
        return await QwenLLMProvider.verify_api_key(
            api_key,
            base_url=base_url,
            model=runtime_model,
        )

    async def _verify_and_update_status(self) -> None:
        """Background task to verify keys and update dashboard status."""
        if self.settings is None:
            return

        dash = getattr(self.app, "view_dashboard", None)
        if dash is None:
            return

        secrets = None
        with contextlib.suppress(Exception):
            secrets = create_secret_store(self.settings.secrets, config_path=self.config_path)

        alibaba_selected_valid_cache: bool | None = None
        alibaba_any_valid_cache: bool | None = None

        async def _verify_alibaba_selected() -> bool:
            nonlocal alibaba_selected_valid_cache
            if alibaba_selected_valid_cache is not None:
                return alibaba_selected_valid_cache
            if secrets is None:
                alibaba_selected_valid_cache = False
                return False
            key, base_url = self._get_qwen_key_and_base_url(secrets)
            selected_model = self.settings.qwen.llm_model.value
            alibaba_selected_valid_cache = await self._verify_qwen_llm_api_key(
                key,
                base_url=base_url,
                model=selected_model,
            )
            return alibaba_selected_valid_cache

        async def _verify_alibaba_any_model() -> bool:
            nonlocal alibaba_any_valid_cache
            if alibaba_any_valid_cache is not None:
                return alibaba_any_valid_cache
            if await _verify_alibaba_selected():
                alibaba_any_valid_cache = True
                return True
            if secrets is None:
                alibaba_any_valid_cache = False
                return False
            key, base_url = self._get_qwen_key_and_base_url(secrets)
            selected_model = self.settings.qwen.llm_model.value
            for fallback_model in (
                model.value for model in QwenLLMModel if model.value != selected_model
            ):
                if await self._verify_qwen_llm_api_key(
                    key,
                    base_url=base_url,
                    model=fallback_model,
                ):
                    alibaba_any_valid_cache = True
                    return True
            alibaba_any_valid_cache = False
            return False

        # 1. Verify LLM
        llm_valid = False
        if self.hub and self.hub.llm:
            # It was created, but is the key valid?
            try:
                provider_name = self.settings.provider.llm
                key = ""
                if provider_name == "gemini":
                    key = secrets.get("google_api_key") or "" if secrets is not None else ""
                    llm_valid = await GeminiLLMProvider.verify_api_key(key)
                elif provider_name == LLMProviderName.OPENROUTER:
                    resolution = (
                        resolve_openrouter_credentials(self.settings, secrets=secrets)
                        if secrets is not None
                        else None
                    )
                    if (
                        self.settings.openrouter.selected_source
                        == OpenRouterCredentialSource.MANAGED
                        and (resolution is None or resolution.api_key is None)
                    ):
                        llm_valid = self._managed_openrouter_can_attempt_translation()
                    else:
                        key = (
                            resolution.api_key
                            if resolution is not None and resolution.api_key
                            else ""
                        )
                        llm_valid = bool(key) and await OpenRouterLLMProvider.verify_api_key(key)
                elif provider_name == "qwen":
                    llm_valid = await _verify_alibaba_selected()
                else:
                    # Assume valid for others or if no key usage known
                    llm_valid = True
            except Exception:
                llm_valid = False

        # If LLM verification failed, force needs_key = True even if provider object exists
        if not llm_valid:
            dash.set_translation_needs_key(True)
            # If it was enabled, we potentially disable it or just let the warning show on next interaction
            # User request: "Validation Fail -> Orange". Implicitly, if it's ON and fails, maybe we should turn it OFF?
            # For now, setting needs_key=True ensures that if they try to toggle, it warns.
            # If it is currently ON, we might want to flag it.
            if self.hub:
                self.hub.translation_enabled = False  # Disable internally
            dash.set_translation_enabled(False)  # Visually turn off
        else:
            dash.set_translation_needs_key(False)

        # 2. Verify STT
        stt_requires_secret = self._stt_provider_requires_secret(self.settings.provider.stt)
        stt_valid = not stt_requires_secret
        if self.hub and self.hub.stt and stt_requires_secret:
            try:
                provider_name = self.settings.provider.stt

                if provider_name == STTProviderName.DEEPGRAM:
                    key = secrets.get("deepgram_api_key") or "" if secrets is not None else ""
                    stt_valid = await DeepgramRealtimeSTTBackend.verify_api_key(key)
                elif provider_name == STTProviderName.QWEN_ASR:
                    stt_valid = await _verify_alibaba_any_model()
                elif provider_name == STTProviderName.SONIOX:
                    key = secrets.get("soniox_api_key") or "" if secrets is not None else ""
                    stt_valid = await SonioxRealtimeSTTBackend.verify_api_key(key)
                else:
                    stt_valid = True
            except Exception:
                stt_valid = False

        if not stt_valid:
            dash.set_stt_needs_key(stt_requires_secret)
            if self.hub:
                # Close STT backend?
                pass
            dash.set_stt_enabled(False)
        else:
            dash.set_stt_needs_key(False)

        await self._refresh_managed_trial_usage_state()
