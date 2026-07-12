from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any

from puripuly_heart.app.ports.application_settings import (
    CaptureTargetValue,
    SecretMetadataQuery,
    SettingChange,
    SettingsField,
    SettingsSurface,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.ports.ui_settings import (
    AudioSettingsSnapshot,
    CanonicalUiCommands,
    CaptureTargetSnapshot,
    CredentialMetadataSnapshot,
    LanguageSettingsSnapshot,
    ManagedPresentation,
    OscOutputSettingsSnapshot,
    OverlaySettingsSnapshot,
    PromptSettingsSnapshot,
    ProviderSettingsSnapshot,
    ProviderVerificationSnapshot,
    RuntimeFacts,
    SttSettingsSnapshot,
    TranslationSettingsSnapshot,
    UiClipboardTelemetrySnapshot,
    UiSettingsApplied,
    UiSettingsConflict,
    UiSettingsDegraded,
    UiSettingsDelta,
    UiSettingsInteractionPort,
    UiSettingsResult,
    UiSettingsSnapshot,
    UiSurfaceOutcome,
    VocabularyGroup,
)
from puripuly_heart.app.services.canonical_command_composition import settings_command_for_surface
from puripuly_heart.core.lifecycle import LifecycleScope, start_lifecycle_task


def _surface(field: SettingsField) -> SettingsSurface:
    value = field.value
    if value.startswith(
        ("translation.", "openrouter.", "qwen.", "cerebras.", "local_llm.", "llm.")
    ):
        return SettingsSurface.TRANSLATION_PROVIDER
    if value.startswith(
        (
            "provider.",
            "languages.",
            "audio.",
            "desktop_audio.",
            "stt.",
            "deepgram_stt.",
            "qwen_asr_stt.",
            "soniox_stt.",
        )
    ):
        return SettingsSurface.STT_LANGUAGE_AUDIO
    if value.startswith(("overlay.", "osc.")):
        return SettingsSurface.OVERLAY_OSC_OUTPUT
    return SettingsSurface.UI_PROMPT_CLIPBOARD


def _value(leaves, name, default=None):  # noqa: ANN001, ANN202
    values = dict(leaves)
    return values.get((name,), values.get(tuple(name.split(".")), default))


def _capture_target_id(value: CaptureTargetValue) -> str:
    if value.kind == "default_output_device":
        return "device:"
    if value.kind == "named_output_device":
        return f"device:{value.device_name}"
    if value.process_kind == "discord":
        return f"process:discord:{value.discord_channel}"
    return f"process:{value.process_kind}:{value.executable_identity}"


def _capture_target_value(target_id: str) -> CaptureTargetValue:
    if target_id == "device:":
        return CaptureTargetValue()
    if target_id.startswith("device:"):
        return CaptureTargetValue("named_output_device", target_id.removeprefix("device:"))
    prefix, process_kind, identity = target_id.split(":", 2)
    if prefix != "process":
        raise ValueError("unsupported capture target")
    if process_kind == "discord":
        return CaptureTargetValue("process", process_kind="discord", discord_channel=identity)
    return CaptureTargetValue("process", process_kind=process_kind, executable_identity=identity)


class ApplicationUiSettingsService:
    def __init__(
        self,
        *,
        commands: CanonicalUiCommands,
        secret_keys: tuple[str, ...] = (),
        runtime_facts: Callable[[], RuntimeFacts] = RuntimeFacts,
        verification: Callable[[], tuple[ProviderVerificationSnapshot, ...]] = tuple,
        capture: Callable[[], CaptureTargetSnapshot] = CaptureTargetSnapshot,
        telemetry: Callable[[], tuple[str, bool, str | None]] = lambda: ("unset", False, None),
        managed: Callable[[], ManagedPresentation] = ManagedPresentation,
        interactions: UiSettingsInteractionPort | None = None,
    ) -> None:
        self._commands = commands
        self._secret_keys = tuple(secret_keys)
        self._runtime_facts = runtime_facts
        self._verification = verification
        self._capture = capture
        self._telemetry = telemetry
        self._managed = managed
        self._interactions = interactions

    async def snapshot(self) -> UiSettingsSnapshot:
        canonical = await self._commands.settings_queries.snapshot()
        operational = await self._commands.operational_queries.operational_snapshot()

        def get(name, default=None):  # noqa: ANN001, ANN202
            return _value(canonical.leaves, name, default)

        if self._interactions is None:
            consent, endpoint, delivery = self._telemetry()
            runtime = self._runtime_facts()
            verification = self._verification()
            managed = self._managed()
        else:
            telemetry = await self._interactions.telemetry_presentation()
            consent, endpoint, delivery = (
                telemetry.consent,
                telemetry.endpoint_configured,
                telemetry.last_delivery_status,
            )
            runtime = await self._interactions.runtime_facts()
            verification = await self._interactions.verification_presentation()
            managed = await self._interactions.managed_presentation()
        credentials = CredentialMetadataSnapshot(
            tuple(
                [
                    await self._commands.secret_queries.secret_metadata(SecretMetadataQuery(key))
                    for key in self._secret_keys
                ]
            )
        )
        capture_value = get(SettingsField.DESKTOP_AUDIO_CAPTURE_TARGET.value)
        connection_history = get("translation.connection_history", ())
        if hasattr(connection_history, "entries"):
            connection_history = tuple(connection_history.entries)
        custom_terms = get("stt.custom_terms", ())
        if hasattr(custom_terms, "entries"):
            custom_terms = tuple(
                VocabularyGroup(language, tuple(terms)) for language, terms in custom_terms.entries
            )
        capture = self._capture()
        if isinstance(capture_value, CaptureTargetValue):
            selected_id = _capture_target_id(capture_value)
            if self._interactions is not None:
                capture = await self._interactions.capture_targets(selected_id)
            capture = replace(
                capture,
                selected_id=selected_id,
            )
        return UiSettingsSnapshot(
            TranslationSettingsSnapshot(
                get("translation.model"),
                get("translation.connection"),
                connection_history,
                fallback=get("translation.fallback"),
            ),
            ProviderSettingsSnapshot(
                get(SettingsField.OPENROUTER_LLM_MODEL.value),
                get("openrouter.routing_mode"),
                get(SettingsField.OPENROUTER_SELECTED_SOURCE.value),
                get("qwen.llm_model"),
                get("qwen.region"),
                get("cerebras.llm_model"),
                get("local_llm.backend"),
                get("local_llm.base_url"),
                get("local_llm.model"),
                get("llm.concurrency_limit"),
                openrouter_provider_routing=get("openrouter.provider_routing"),
                openrouter_selection_alias=get(SettingsField.OPENROUTER_SELECTION_ALIAS.value),
                openrouter_broker_base_url=get("openrouter.broker_base_url"),
                local_extra_body=tuple(getattr(get("local_llm.extra_body"), "entries", ())),
            ),
            PromptSettingsSnapshot(get("system_prompt")),
            SttSettingsSnapshot(
                get("provider.stt"),
                get("provider.peer_stt"),
                get("stt.drain_timeout_s"),
                get("stt.vad_speech_threshold"),
                get("stt.low_latency_mode"),
                get("stt.custom_vocabulary_enabled"),
                custom_terms,
                deepgram_model=get("deepgram_stt.model"),
                qwen_asr_model=get("qwen_asr_stt.model"),
                soniox_model=get("soniox_stt.model"),
                soniox_endpoint=get("soniox_stt.endpoint"),
                soniox_keepalive_s=get("soniox_stt.keepalive_interval_s"),
                soniox_trailing_silence_ms=get("soniox_stt.trailing_silence_ms"),
                low_latency_hangover_ms=get("stt.low_latency_vad_hangover_ms"),
                low_latency_merge_gap_ms=get("stt.low_latency_merge_gap_ms"),
                low_latency_retry_max=get("stt.low_latency_spec_retry_max"),
            ),
            LanguageSettingsSnapshot(
                get("languages.source_language"),
                get("languages.target_language"),
                get("languages.peer_source_language"),
                get("languages.peer_target_language"),
                get("languages.peer_source_mode"),
                get("languages.peer_expected_languages", ()),
                get("languages.recent_source_languages", ()),
                get("languages.recent_target_languages", ()),
            ),
            AudioSettingsSnapshot(
                get("audio.ring_buffer_ms"),
                get("audio.input_host_api"),
                get("audio.input_device"),
                get("desktop_audio.output_device"),
                get("desktop_audio.vad_speech_threshold"),
                get("desktop_audio.vad_hangover_ms"),
                get("desktop_audio.vad_pre_roll_ms"),
                capture_value,
            ),
            OverlaySettingsSnapshot(
                get("overlay.target"),
                get("overlay.show_translation"),
                get("overlay.show_peer_original"),
                get("overlay.target") == "desktop",
                get("overlay.calibration"),
                get("overlay.desktop_flet"),
            ),
            OscOutputSettingsSnapshot(
                get("osc.host"),
                get("osc.port"),
                get("osc.chatbox_address"),
                get("osc.chatbox_send"),
                get("osc.chatbox_clear"),
                get("osc.chatbox_max_chars"),
                get("osc.chatbox_include_source"),
                get("osc.vrc_mic_intercept"),
            ),
            UiClipboardTelemetrySnapshot(
                get("ui.locale"),
                get("ui.clipboard_auto_translate_enabled"),
                get("ui.integrated_context_enabled"),
                consent,
                endpoint,
                delivery,
                get("secrets.backend"),
                get("secrets.encrypted_file_path"),
            ),
            credentials,
            verification,
            capture,
            managed,
            runtime,
            canonical.revision,
            operational.revision,
        )

    async def apply(self, delta: UiSettingsDelta) -> UiSettingsResult:
        initial = await self.snapshot()
        if delta.expected_revision != initial.canonical_revision:
            return UiSettingsConflict(initial, delta.expected_revision, initial.canonical_revision)
        revision = delta.expected_revision
        outcomes: list[UiSurfaceOutcome] = []
        for surface in SettingsSurface:
            changes = tuple(change for change in delta.changes if _surface(change.field) == surface)
            if not changes:
                continue
            result = await self._commands.settings_commands.execute(
                settings_command_for_surface(
                    (
                        "ui_prompt_clipboard_state"
                        if surface == SettingsSurface.UI_PROMPT_CLIPBOARD
                        else surface.value
                    ),
                    changes,
                    revision,
                )
            )
            receipt = result.receipt if isinstance(result.receipt, SettingsCommitReceipt) else None
            outcomes.append(
                UiSurfaceOutcome(
                    surface.value,
                    result.status,
                    receipt.revision if receipt else None,
                    receipt.reason if receipt else None,
                    receipt.correlation_id if receipt else None,
                    result.runtime_status,
                    result.runtime_completed,
                    result.runtime_failed,
                    result.runtime_skipped,
                    result.reconciliation_required,
                )
            )
            if receipt is not None:
                revision = receipt.revision
            if result.status == "conflict":
                authoritative = await self.snapshot()
                if any(item.receipt_revision for item in outcomes[:-1]):
                    return UiSettingsDegraded(
                        authoritative, "partial_commit_degraded", tuple(outcomes), True
                    )
                return UiSettingsConflict(
                    authoritative,
                    delta.expected_revision,
                    authoritative.canonical_revision,
                    tuple(outcomes),
                )
            if result.status not in {
                "applied",
                "no_change",
                "degraded",
                "cancelled_committed",
                "cancelled_degraded",
            }:
                authoritative = await self.snapshot()
                return UiSettingsDegraded(
                    authoritative,
                    (
                        "partial_commit_degraded"
                        if any(item.receipt_revision for item in outcomes[:-1])
                        else result.status
                    ),
                    tuple(outcomes),
                    True,
                )
        final = await self.snapshot()
        if any(
            item.status in {"degraded", "cancelled_degraded"} or item.reconciliation_required
            for item in outcomes
        ):
            return UiSettingsDegraded(final, "degraded", tuple(outcomes), True)
        return UiSettingsApplied(final, tuple(outcomes))


class UiSettingsApplication:
    def __init__(
        self, settings: ApplicationUiSettingsService, interactions: UiSettingsInteractionPort
    ) -> None:
        self.settings = settings
        self.interactions = interactions
        self._scope = LifecycleScope("UiSettingsApplication")
        self._interaction_sequence = 0
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("ui settings application is closed")
        self._started = True

    async def select_capture_target(
        self, target_id: str, expected_revision: str
    ) -> UiSettingsResult:
        return await self.settings.apply(
            UiSettingsDelta(
                expected_revision,
                (
                    SettingChange(
                        SettingsField.DESKTOP_AUDIO_CAPTURE_TARGET,
                        _capture_target_value(target_id),
                    ),
                ),
            )
        )

    def run_interaction(self, operation: Coroutine[Any, Any, Any]):
        if not self._started or self._closed:
            operation.close()
            raise RuntimeError("ui settings application is not running")
        self._interaction_sequence += 1
        return start_lifecycle_task(
            self._scope, operation, name=f"interaction-{self._interaction_sequence}"
        )

    async def close(self) -> None:
        if self._closed:
            return
        await self._scope.close()
        await self.interactions.close()
        self._closed = True


def create_ui_settings_application(
    *, canonical_commands: CanonicalUiCommands, interactions: UiSettingsInteractionPort, **kwargs
) -> UiSettingsApplication:  # noqa: ANN003
    return UiSettingsApplication(
        ApplicationUiSettingsService(
            commands=canonical_commands, interactions=interactions, **kwargs
        ),
        interactions,
    )


__all__ = [
    "ApplicationUiSettingsService",
    "UiSettingsApplication",
    "create_ui_settings_application",
]
