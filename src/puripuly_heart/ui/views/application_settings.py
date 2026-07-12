from __future__ import annotations

import logging
from typing import Awaitable, Callable, TypeVar

import flet as ft

from puripuly_heart.app.ports.application_settings import (
    DesktopOverlayValue,
    OverlayCalibrationValue,
    SecretMetadata,
    SecretSourceStatus,
    SecretVerificationStatus,
    SettingChange,
    SettingsField,
)
from puripuly_heart.app.ports.ui_settings import (
    AudioDeviceQueryResult,
    CaptureRetryResult,
    CaptureTargetSnapshot,
    InteractionResult,
    ManagedAction,
    ManagedActionResult,
    MicrophoneTestResult,
    OverlayAction,
    PkceStartRequest,
    ProviderVerificationSnapshot,
    RuntimeFacts,
    SettingsEditorKind,
    SettingsPresentationCatalog,
    UiSettingsApplied,
    UiSettingsConflict,
    UiSettingsDegraded,
    UiSettingsEditDraft,
    UiSettingsResult,
    UiSettingsSnapshot,
)
from puripuly_heart.app.services.ui_settings import (
    UiSettingsApplication,
    settings_presentation_catalog,
)
from puripuly_heart.ui.components.settings.semantic_settings_editors import (
    CaptureTargetEditor,
    CustomVocabularyEditor,
    DesktopOverlayEditor,
    LocalExtraBodyEditor,
    OverlayCalibrationEditor,
    StringListEditor,
    StringMapEditor,
    TranslationFallbackEditor,
)
from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, set_locale, t

logger = logging.getLogger(__name__)
T = TypeVar("T")

_BOOL_FIELDS = {
    SettingsField.STT_LOW_LATENCY_MODE,
    SettingsField.STT_CUSTOM_VOCABULARY_ENABLED,
    SettingsField.OVERLAY_SHOW_TRANSLATION,
    SettingsField.OVERLAY_SHOW_PEER_ORIGINAL,
    SettingsField.OSC_CHATBOX_SEND,
    SettingsField.OSC_CHATBOX_CLEAR,
    SettingsField.OSC_VRC_MIC_INTERCEPT,
    SettingsField.OSC_CHATBOX_INCLUDE_SOURCE,
    SettingsField.CLIPBOARD_AUTO_TRANSLATE,
    SettingsField.INTEGRATED_CONTEXT_ENABLED,
}
_INT_FIELDS = {
    SettingsField.LLM_CONCURRENCY_LIMIT,
    SettingsField.AUDIO_RING_BUFFER_MS,
    SettingsField.DESKTOP_VAD_HANGOVER_MS,
    SettingsField.DESKTOP_VAD_PRE_ROLL_MS,
    SettingsField.STT_LOW_LATENCY_VAD_HANGOVER_MS,
    SettingsField.STT_LOW_LATENCY_MERGE_GAP_MS,
    SettingsField.STT_LOW_LATENCY_SPEC_RETRY_MAX,
    SettingsField.SONIOX_STT_TRAILING_SILENCE_MS,
    SettingsField.OSC_PORT,
    SettingsField.OSC_CHATBOX_MAX_CHARS,
}
_FLOAT_FIELDS = {
    SettingsField.DESKTOP_VAD_THRESHOLD,
    SettingsField.STT_DRAIN_TIMEOUT_S,
    SettingsField.STT_VAD_THRESHOLD,
    SettingsField.SONIOX_STT_KEEPALIVE_S,
}
_TUPLE_FIELDS = {
    SettingsField.PEER_EXPECTED_LANGUAGES,
    SettingsField.RECENT_SOURCE_LANGUAGES,
    SettingsField.RECENT_TARGET_LANGUAGES,
}
_SECRET_KEYS = (
    "google_api_key",
    "openrouter_api_key",
    "deepseek_api_key",
    "deepgram_api_key",
    "soniox_api_key",
    "alibaba_api_key_beijing",
    "alibaba_api_key_singapore",
    "alibaba_api_key",
    "local_llm_api_key",
    "cerebras_api_key",
)

_FIELD_PRESENTATION: dict[SettingsField, tuple[str, dict[str, str]]] = {}
_FIELD_PRESENTATION.update(
    {
        SettingsField.TRANSLATION_MODEL: ("settings.translation_model", {}),
        SettingsField.TRANSLATION_CONNECTION: ("settings.translation_connection", {}),
        SettingsField.TRANSLATION_CONNECTION_HISTORY: (
            "settings.application.connection_history",
            {},
        ),
        SettingsField.TRANSLATION_FALLBACK: ("settings.fallback", {}),
        SettingsField.PROVIDER_STT: ("settings.self_stt_provider", {}),
        SettingsField.PROVIDER_PEER_STT: ("settings.peer_stt_provider", {}),
        SettingsField.SOURCE_LANGUAGE: ("settings.source_language", {}),
        SettingsField.TARGET_LANGUAGE: ("settings.target_language", {}),
        SettingsField.PEER_SOURCE_LANGUAGE: ("settings.peer_source_language", {}),
        SettingsField.PEER_TARGET_LANGUAGE: ("settings.peer_target_language", {}),
        SettingsField.AUDIO_INPUT_HOST_API: ("settings.audio_host_api", {}),
        SettingsField.AUDIO_INPUT_DEVICE: ("settings.microphone", {}),
        SettingsField.DESKTOP_AUDIO_OUTPUT_DEVICE: ("settings.desktop_audio.output_device", {}),
        SettingsField.STT_VAD_THRESHOLD: ("settings.section.self_vad_sensitivity", {}),
        SettingsField.DESKTOP_VAD_THRESHOLD: ("settings.desktop_audio.vad_speech_threshold", {}),
        SettingsField.DESKTOP_VAD_HANGOVER_MS: ("settings.desktop_audio.vad_hangover_ms", {}),
        SettingsField.DESKTOP_VAD_PRE_ROLL_MS: ("settings.desktop_audio.vad_pre_roll_ms", {}),
        SettingsField.UI_LOCALE: ("settings.language", {}),
        SettingsField.CLIPBOARD_AUTO_TRANSLATE: ("settings.clipboard_auto_translate", {}),
        SettingsField.INTEGRATED_CONTEXT_ENABLED: ("settings.integrated_context", {}),
        SettingsField.SYSTEM_PROMPT: ("settings.system_prompt", {}),
        SettingsField.OVERLAY_TARGET: ("settings.overlay.caption_location", {}),
        SettingsField.OVERLAY_SHOW_TRANSLATION: ("settings.overlay.show_translation", {}),
        SettingsField.OVERLAY_SHOW_PEER_ORIGINAL: ("settings.overlay.show_peer_original", {}),
        SettingsField.OVERLAY_CALIBRATION: ("settings.overlay.calibration", {}),
        SettingsField.OVERLAY_DESKTOP_FLET: ("settings.application.desktop_overlay", {}),
        SettingsField.LOCAL_LLM_BASE_URL: ("settings.local_llm.base_url", {}),
        SettingsField.LOCAL_LLM_MODEL: ("settings.local_llm.model", {}),
        SettingsField.LOCAL_LLM_EXTRA_BODY: ("settings.local_llm.extra_body", {}),
    }
)

_PROPER_OPTION_LABELS = {
    "beijing": "Beijing",
    "singapore": "Singapore",
    "deepseek/deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek_v4_flash": "DeepSeek V4 Flash",
    "deepseek_v4_flash_byok": "DeepSeek V4 Flash (BYOK)",
    "deepseek_v4_flash_china": "DeepSeek V4 Flash (China)",
    "deepseek_v4_flash_managed": "DeepSeek V4 Flash (Managed)",
    "deepseek_v4_flash_official": "DeepSeek V4 Flash (Official API)",
    "deepseek_v4_pro": "DeepSeek V4 Pro",
    "gemini31_flash_lite": "Gemini 3.1 Flash-Lite",
    "gemini31_flash_lite_byok": "Gemini 3.1 Flash-Lite (BYOK)",
    "gemini3_flash": "Gemini 3 Flash",
    "gemini3_flash_byok": "Gemini 3 Flash (BYOK)",
    "gemma-4-31b": "Gemma 4 31B",
    "gemma4": "Gemma 4",
    "gemma4_31b_cerebras": "Gemma 4 31B (Cerebras)",
    "gemma4_byok": "Gemma 4 (BYOK)",
    "gemma4_managed": "Gemma 4 (Managed)",
    "google/gemini-3-flash-preview": "Gemini 3 Flash Preview",
    "google/gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
    "google/gemma-4-26b-a4b-it": "Gemma 4 26B A4B IT",
    "openrouter_deepseek_v4_flash": "DeepSeek V4 Flash (OpenRouter)",
    "openrouter_gemma4_26b_a4b": "Gemma 4 26B A4B (OpenRouter)",
    "qwen/qwen3.5-flash-02-23": "Qwen 3.5 Flash 02-23",
    "qwen3.5-flash": "Qwen 3.5 Flash",
    "qwen3.5-plus": "Qwen 3.5 Plus",
    "qwen35_flash": "Qwen 3.5 Flash",
    "qwen35_flash_byok": "Qwen 3.5 Flash (BYOK)",
    "qwen35_flash_managed": "Qwen 3.5 Flash (Managed)",
    "qwen35_plus": "Qwen 3.5 Plus",
    "cerebras_gemma4_31b": "Gemma 4 31B (Cerebras)",
    "google_gemini_latency": "Gemini latency routing",
}

_OPTION_PRESENTATION = {
    "managed": "settings.translation_connection.managed",
    "managed_china": "settings.translation_connection.managed_china",
    "openrouter": "settings.translation_connection.openrouter",
    "official_byok": "settings.translation_connection.official_byok",
    "byok": "settings.translation_connection.official_byok",
    "ollama": "settings.translation_connection.ollama",
    "none": "settings.fallback.none",
    "steamvr": "settings.overlay.target.steamvr",
    "desktop": "settings.overlay.target.desktop",
    "head_locked": "settings.overlay.calibration.anchor.head_locked",
    "tiny": "settings.overlay.desktop.size.option.tiny",
    "xsmall": "settings.overlay.desktop.size.option.xsmall",
    "small": "settings.overlay.desktop.size.option.small",
    "medium": "settings.overlay.desktop.size.option.medium",
    "large": "settings.overlay.desktop.size.option.large",
    "xlarge": "settings.overlay.desktop.size.option.xlarge",
    "deepgram": "provider.deepgram",
    "soniox": "provider.soniox",
    "qwen_asr": "provider.qwen_asr",
    "local_qwen": "provider.local_qwen",
    "local_llm": "provider.local_llm",
    "cerebras_gemma4_31b": "settings.fallback.cerebras_gemma4_31b",
    "default": "settings.application.option.default",
    "default_output_device": "settings.application.option.default_output_device",
    "named_output_device": "settings.application.option.named_output_device",
    "process": "settings.application.option.process",
    "discord": "settings.application.option.discord",
    "vrchat": "settings.application.option.vrchat",
    "generic_executable": "settings.application.option.generic_executable",
    "manual": "settings.application.option.manual",
    "soniox_auto": "settings.application.option.automatic",
    "latency": "settings.application.option.latency",
    "deepseek_only": "settings.application.option.deepseek_only",
    "encrypted_file": "settings.application.option.encrypted_file",
    "keyring": "settings.application.option.keyring",
    "string": "settings.application.option.string",
    "integer": "settings.application.option.integer",
    "number": "settings.application.option.number",
    "boolean": "settings.application.option.boolean",
    "null": "settings.application.option.null",
}

_SECRET_PRESENTATION = {
    "google_api_key": "settings.google_api_key",
    "openrouter_api_key": "settings.openrouter_api_key",
    "deepseek_api_key": "settings.deepseek_api_key",
    "deepgram_api_key": "settings.deepgram_api_key",
    "soniox_api_key": "settings.soniox_api_key",
    "alibaba_api_key_beijing": "settings.alibaba_api_key_beijing",
    "alibaba_api_key_singapore": "settings.alibaba_api_key_singapore",
    "alibaba_api_key": "settings.alibaba_api_key_beijing",
    "local_llm_api_key": "settings.local_llm.api_key",
    "cerebras_api_key": "settings.cerebras_api_key",
}

_PROVIDER_PRESENTATION = {
    "google": "provider.google",
    "openrouter": "provider.openrouter",
    "deepseek": "provider.deepseek_v4_pro",
    "deepgram": "provider.deepgram",
    "soniox": "provider.soniox",
    "alibaba": "provider.qwen",
    "alibaba_beijing": "settings.application.provider.alibaba_beijing",
    "alibaba_singapore": "settings.application.provider.alibaba_singapore",
    "local_llm": "provider.local_llm",
    "cerebras": "provider.cerebras",
}

_DETAIL_PRESENTATION = {
    "started": "settings.application.detail.started",
    "succeeded": "settings.application.detail.succeeded",
    "committed_degraded": "settings.application.detail.committed_degraded",
    "reopened": "settings.application.detail.reopened",
    "cancelled": "settings.application.detail.cancelled",
    "inactive": "settings.application.detail.inactive",
    "closed": "settings.application.detail.closed",
    "failed": "settings.application.detail.failed",
    "revision_conflict": "settings.application.detail.revision_conflict",
    "pkce_owner_unbound": "settings.application.detail.unavailable",
    "verification_owner_unbound": "settings.application.detail.unavailable",
    "telemetry_owner_unbound": "settings.application.detail.unavailable",
    "overlay_owner_unbound": "settings.application.detail.unavailable",
    "microphone_owner_unbound": "settings.application.detail.unavailable",
    "managed_owner_unavailable": "settings.application.detail.unavailable",
    "audio_enumerator_unbound": "settings.application.detail.audio_unavailable",
    "sounddevice_unavailable": "settings.application.detail.audio_unavailable",
    "device_unavailable": "settings.application.detail.device_unavailable",
    "session_start_failed": "settings.application.detail.session_start_failed",
    "overlay_action_not_supported": "settings.application.detail.unsupported",
    "managed_transaction_deferred_to_c3": "settings.application.detail.managed_deferred",
    "managed_refresh_failed": "settings.application.detail.managed_refresh_failed",
    "managed_refresh_cancelled": "settings.application.detail.managed_refresh_cancelled",
    "secret_missing": "settings.application.detail.secret_missing",
}


class ApplicationSettingsView(ft.Column):
    tab_order = ("api", "general", "prompt", "overlay")

    def __init__(
        self,
        application: UiSettingsApplication,
        *,
        on_snackbar: Callable[[str, str], None] | None = None,
        on_locale_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.application = application
        self.on_snackbar = on_snackbar
        self.on_locale_changed = on_locale_changed
        self.snapshot: UiSettingsSnapshot | None = None
        self._capture_presentation: CaptureTargetSnapshot | None = None
        self.draft: UiSettingsEditDraft | None = None
        self.secret_entries: dict[str, str] = {}
        self.catalog: SettingsPresentationCatalog = getattr(
            application, "presentation_catalog", settings_presentation_catalog()
        )
        self.controls_by_field: dict[SettingsField, ft.Control] = {}
        self.semantic_option_fields: set[SettingsField] = set()
        self.secret_controls: dict[str, ft.TextField] = {}
        self.credential_controls: dict[tuple[str, str], ft.Control] = {}
        self.credential_metadata_controls: dict[str, ft.Text] = {}
        self.credential_group_controls: dict[str, ft.Column] = {}
        self.credential_provider_ids: dict[str, str] = {}
        self.interaction_controls: dict[str, ft.Control] = {}
        self._interaction_label_keys: dict[str, str] = {}
        self.status_text = ft.Text(value="")
        self.managed_text = ft.Text(value="")
        self.capture_status = ft.Text(value="")
        self.runtime_text = ft.Text(value="")
        self.peer_runtime_text = ft.Text(value="")
        self.overlay_active_text = ft.Text(value="")
        self.microphone_runtime_text = ft.Text(value="")
        self.capture_active_status = ft.Text(value="")
        self.interaction_status = ft.Text(value="")
        self.verification_status = ft.Text(value="")
        self.telemetry_status = ft.Text(value="")
        self.capture_target_control = ft.Dropdown(
            label=t("settings.application.capture_target"),
            on_change=lambda event: self._schedule(
                self.select_capture_target(event.control.value or "")
            ),
        )
        self.telemetry_control = ft.Dropdown(
            label=t("settings.application.telemetry"),
            options=[
                ft.dropdown.Option(key=value, text=self._consent_label(value))
                for value in ("allow", "decline", "unset")
            ],
            on_change=lambda event: self._schedule(
                self.set_telemetry_consent(event.control.value or "unset")
            ),
        )
        self._pending = False
        self._rendered_locale = get_locale()
        self.apply_button = ft.FilledButton(
            text=t("settings.application.apply"),
            disabled=True,
            on_click=lambda _e: self._schedule(self.submit()),
        )
        tabs = self._build_tabs()
        self.subtabs = TextSubtabShell(
            tabs=tabs,
            initial_key="api",
            font_family=font_for_language(get_locale()),
            subtab_bar_position="bottom",
        )
        super().__init__(
            controls=[self.subtabs, ft.Row([self.status_text, self.apply_button])],
            expand=True,
            spacing=16,
        )

    def _text(self, field: SettingsField, label: str, *, password: bool = False) -> ft.Control:
        descriptor = self.catalog.field(field.value)
        if descriptor is not None and descriptor.options:
            self.semantic_option_fields.add(field)
            control = ft.Dropdown(
                label=label,
                options=[
                    ft.dropdown.Option(
                        key=option.semantic_id,
                        text=self._option_label(option.semantic_id),
                        disabled=option.disabled,
                    )
                    for option in descriptor.options
                ],
                on_change=lambda e, item=field: self.edit(item, e.control.value),
            )
            self.controls_by_field[field] = control
            return control
        control = ft.TextField(
            label=label,
            password=password,
            can_reveal_password=password,
            on_change=lambda e, item=field: self.edit(item, e.control.value or ""),
        )
        self.controls_by_field[field] = control
        return control

    def _number(self, field: SettingsField, label: str, *, integer: bool = False) -> ft.TextField:
        def changed(e) -> None:
            value = e.control.value
            if value in (None, ""):
                self.edit(field, None)
                return
            try:
                self.edit(field, int(value) if integer else float(value))
            except ValueError:
                return

        control = ft.TextField(label=label, keyboard_type=ft.KeyboardType.NUMBER, on_change=changed)
        self.controls_by_field[field] = control
        return control

    def _switch(self, field: SettingsField, label: str) -> ft.Switch:
        control = ft.Switch(
            label=label, on_change=lambda e, item=field: self.edit(item, e.control.value)
        )
        self.controls_by_field[field] = control
        return control

    def _dynamic_dropdown(self, field: SettingsField, label: str) -> ft.Dropdown:
        control = ft.Dropdown(
            label=label,
            on_change=lambda event, item=field: self.edit(item, event.control.value or ""),
        )
        self.controls_by_field[field] = control
        return control

    def _secret(self, key: str, label: str) -> ft.TextField:
        control = ft.TextField(
            label=label,
            password=True,
            can_reveal_password=True,
            on_change=lambda e, item=key: self._edit_secret(item, e.control.value or ""),
        )
        self.secret_controls[key] = control
        return control

    def _credential_group(self, key: str) -> ft.Column:
        metadata = ft.Text(value="")
        self.credential_metadata_controls[key] = metadata
        group = ft.Column([self._secret(key, self._secret_label(key)), metadata])
        self.credential_group_controls[key] = group
        return group

    def _row(self, controls: list[ft.Control]) -> ft.ResponsiveRow:
        for control in controls:
            control.col = {"xs": 12, "md": 6}
        return ft.ResponsiveRow(controls=controls, spacing=12, run_spacing=12)

    def _action(self, key: str, label_key: str, operation) -> ft.OutlinedButton:
        control = ft.OutlinedButton(
            text=t(label_key), on_click=lambda _e: self._schedule(operation())
        )
        self.interaction_controls[key] = control
        self._interaction_label_keys[key] = label_key
        return control

    def _build_tabs(self) -> list[TextSubtab]:
        api = [
            self._row(
                [
                    self._text(SettingsField.TRANSLATION_MODEL, t("settings.translation_model")),
                    self._text(
                        SettingsField.TRANSLATION_CONNECTION, t("settings.translation_connection")
                    ),
                    self._text(
                        SettingsField.OPENROUTER_LLM_MODEL,
                        self._field_label(SettingsField.OPENROUTER_LLM_MODEL),
                    ),
                    self._text(
                        SettingsField.OPENROUTER_SELECTION_ALIAS,
                        self._field_label(SettingsField.OPENROUTER_SELECTION_ALIAS),
                    ),
                    self._text(
                        SettingsField.QWEN_LLM_MODEL,
                        self._field_label(SettingsField.QWEN_LLM_MODEL),
                    ),
                    self._text(
                        SettingsField.QWEN_REGION, self._field_label(SettingsField.QWEN_REGION)
                    ),
                    self._text(
                        SettingsField.CEREBRAS_LLM_MODEL,
                        self._field_label(SettingsField.CEREBRAS_LLM_MODEL),
                    ),
                    self._text(
                        SettingsField.LOCAL_LLM_BASE_URL,
                        self._field_label(SettingsField.LOCAL_LLM_BASE_URL),
                    ),
                    self._text(
                        SettingsField.LOCAL_LLM_MODEL,
                        self._field_label(SettingsField.LOCAL_LLM_MODEL),
                    ),
                    self._number(
                        SettingsField.LLM_CONCURRENCY_LIMIT,
                        self._field_label(SettingsField.LLM_CONCURRENCY_LIMIT),
                        integer=True,
                    ),
                ]
            ),
            self._row(
                [
                    *[self._credential_group(key) for key in _SECRET_KEYS],
                ]
            ),
            self.managed_text,
            ft.Row(
                controls=[
                    self._action(
                        "managed_primary_connect",
                        "settings.application.connect",
                        lambda: self.managed_action(ManagedAction.CONNECT),
                    ),
                    self._action(
                        "managed_primary_disconnect",
                        "settings.application.disconnect",
                        lambda: self.managed_action(ManagedAction.DISCONNECT),
                    ),
                    self._action(
                        "managed_primary_refresh",
                        "settings.application.refresh",
                        lambda: self.managed_action(ManagedAction.REFRESH),
                    ),
                ],
                wrap=True,
            ),
        ]
        for key in _SECRET_KEYS:
            provider = key.removesuffix("_api_key").replace("alibaba_api_key", "alibaba")
            self.credential_provider_ids[key] = provider
            clear = self._action(
                f"secret_clear:{key}",
                "settings.application.clear",
                lambda item=key: self._clear_secret(item),
            )
            verify = self._action(
                f"secret_verify:{key}",
                "settings.application.verify",
                lambda item=key, name=provider: self.verify_provider(name, item),
            )
            self.credential_controls[(key, "clear")] = clear
            self.credential_controls[(key, "verify")] = verify
            self.credential_group_controls[key].controls.append(
                ft.Row(controls=[clear, verify], wrap=True)
            )
        general = [
            self._row(
                [
                    self._text(SettingsField.PROVIDER_STT, t("settings.stt_provider")),
                    self._text(SettingsField.PROVIDER_PEER_STT, t("settings.peer_stt_provider")),
                    self._text(SettingsField.SOURCE_LANGUAGE, t("settings.source_language")),
                    self._text(SettingsField.TARGET_LANGUAGE, t("settings.target_language")),
                    self._text(
                        SettingsField.PEER_SOURCE_LANGUAGE, t("settings.peer_source_language")
                    ),
                    self._text(
                        SettingsField.PEER_TARGET_LANGUAGE, t("settings.peer_target_language")
                    ),
                    self._dynamic_dropdown(
                        SettingsField.AUDIO_INPUT_HOST_API, t("settings.audio_host_api")
                    ),
                    self._dynamic_dropdown(
                        SettingsField.AUDIO_INPUT_DEVICE, t("settings.microphone")
                    ),
                    self._dynamic_dropdown(
                        SettingsField.DESKTOP_AUDIO_OUTPUT_DEVICE,
                        t("settings.desktop_audio.output_device"),
                    ),
                    self._number(
                        SettingsField.STT_VAD_THRESHOLD,
                        self._field_label(SettingsField.STT_VAD_THRESHOLD),
                    ),
                    self._number(
                        SettingsField.DESKTOP_VAD_THRESHOLD,
                        self._field_label(SettingsField.DESKTOP_VAD_THRESHOLD),
                    ),
                    self._switch(
                        SettingsField.STT_LOW_LATENCY_MODE,
                        self._field_label(SettingsField.STT_LOW_LATENCY_MODE),
                    ),
                    self._switch(
                        SettingsField.STT_CUSTOM_VOCABULARY_ENABLED,
                        self._field_label(SettingsField.STT_CUSTOM_VOCABULARY_ENABLED),
                    ),
                    self._text(SettingsField.UI_LOCALE, t("settings.language")),
                    self._switch(
                        SettingsField.CLIPBOARD_AUTO_TRANSLATE,
                        t("settings.clipboard_auto_translate"),
                    ),
                    self._switch(
                        SettingsField.INTEGRATED_CONTEXT_ENABLED, t("settings.integrated_context")
                    ),
                ]
            ),
            self.capture_status,
            self.capture_active_status,
            self.peer_runtime_text,
            self.microphone_runtime_text,
            self.capture_target_control,
            self.telemetry_control,
            self.telemetry_status,
            self.interaction_status,
            ft.Row(
                controls=[
                    self._action(
                        "microphone_primary_start",
                        "settings.microphone_test",
                        lambda: self.microphone_test(True),
                    ),
                    self._action(
                        "microphone_primary_stop",
                        "settings.application.stop",
                        lambda: self.microphone_test(False),
                    ),
                    self._action(
                        "capture_primary_retry",
                        "settings.application.retry_capture",
                        self.retry_capture,
                    ),
                ],
                wrap=True,
            ),
        ]
        prompt = [
            self._text(SettingsField.SYSTEM_PROMPT, t("settings.system_prompt")),
            self._row(
                [
                    self._text(
                        SettingsField.SECRETS_BACKEND,
                        self._field_label(SettingsField.SECRETS_BACKEND),
                    ),
                    self._text(
                        SettingsField.SECRETS_ENCRYPTED_FILE_PATH,
                        self._field_label(SettingsField.SECRETS_ENCRYPTED_FILE_PATH),
                    ),
                ]
            ),
        ]
        overlay = [
            self._row(
                [
                    self._text(SettingsField.OVERLAY_TARGET, t("settings.overlay.target")),
                    self._switch(
                        SettingsField.OVERLAY_SHOW_TRANSLATION,
                        t("settings.overlay.show_translation"),
                    ),
                    self._switch(
                        SettingsField.OVERLAY_SHOW_PEER_ORIGINAL,
                        t("settings.overlay.show_peer_original"),
                    ),
                    self._text(SettingsField.OSC_HOST, self._field_label(SettingsField.OSC_HOST)),
                    self._number(
                        SettingsField.OSC_PORT,
                        self._field_label(SettingsField.OSC_PORT),
                        integer=True,
                    ),
                    self._text(
                        SettingsField.OSC_CHATBOX_ADDRESS,
                        self._field_label(SettingsField.OSC_CHATBOX_ADDRESS),
                    ),
                    self._switch(
                        SettingsField.OSC_CHATBOX_SEND,
                        self._field_label(SettingsField.OSC_CHATBOX_SEND),
                    ),
                    self._switch(
                        SettingsField.OSC_CHATBOX_CLEAR,
                        self._field_label(SettingsField.OSC_CHATBOX_CLEAR),
                    ),
                    self._number(
                        SettingsField.OSC_CHATBOX_MAX_CHARS,
                        self._field_label(SettingsField.OSC_CHATBOX_MAX_CHARS),
                        integer=True,
                    ),
                    self._switch(
                        SettingsField.OSC_CHATBOX_INCLUDE_SOURCE,
                        self._field_label(SettingsField.OSC_CHATBOX_INCLUDE_SOURCE),
                    ),
                    self._switch(
                        SettingsField.OSC_VRC_MIC_INTERCEPT,
                        self._field_label(SettingsField.OSC_VRC_MIC_INTERCEPT),
                    ),
                ]
            ),
            self.runtime_text,
            self.overlay_active_text,
            ft.Row(
                controls=[
                    self._action(
                        "overlay_primary_start",
                        "settings.application.start",
                        lambda: self.overlay_action(OverlayAction.START),
                    ),
                    self._action(
                        "overlay_primary_stop",
                        "settings.application.stop",
                        lambda: self.overlay_action(OverlayAction.STOP),
                    ),
                    self._action(
                        "overlay_primary_retry",
                        "settings.application.retry",
                        lambda: self.overlay_action(OverlayAction.RETRY),
                    ),
                    self._action(
                        "overlay_primary_reset",
                        "settings.application.reset_position",
                        lambda: self.overlay_action(OverlayAction.RESET_POSITION),
                    ),
                ],
                wrap=True,
            ),
        ]
        missing = [field for field in SettingsField if field not in self.controls_by_field]
        general.append(self._row([self._generic(field) for field in missing]))
        general.append(
            ft.Row(
                controls=[
                    self._action("pkce", "settings.application.connect", self._start_current_pkce),
                    self._action(
                        "pkce_reopen",
                        "settings.application.reopen",
                        self.reopen_pkce,
                    ),
                    self._action(
                        "pkce_cancel",
                        "settings.application.cancel",
                        self.cancel_pkce,
                    ),
                    self._action(
                        "audio_refresh",
                        "settings.application.refresh",
                        self.refresh_audio_devices,
                    ),
                    self._action(
                        "capture_refresh",
                        "settings.application.refresh",
                        self.refresh_capture_targets,
                    ),
                    self._action(
                        "telemetry",
                        "settings.application.telemetry",
                        lambda: self.set_telemetry_consent("allow"),
                    ),
                    self._action(
                        "capture_retry", "settings.application.retry_capture", self.retry_capture
                    ),
                    self._action(
                        "overlay_start",
                        "settings.application.start",
                        lambda: self.overlay_action(OverlayAction.START),
                    ),
                    self._action(
                        "overlay_stop",
                        "settings.application.stop",
                        lambda: self.overlay_action(OverlayAction.STOP),
                    ),
                    self._action(
                        "overlay_retry",
                        "settings.application.retry",
                        lambda: self.overlay_action(OverlayAction.RETRY),
                    ),
                    self._action(
                        "overlay_reset",
                        "settings.application.reset_position",
                        lambda: self.overlay_action(OverlayAction.RESET_POSITION),
                    ),
                    self._action(
                        "microphone_start",
                        "settings.application.start",
                        lambda: self.microphone_test(True),
                    ),
                    self._action(
                        "microphone_stop",
                        "settings.application.stop",
                        lambda: self.microphone_test(False),
                    ),
                    self._action(
                        "managed_connect",
                        "settings.application.connect",
                        lambda: self.managed_action(ManagedAction.CONNECT),
                    ),
                    self._action(
                        "managed_disconnect",
                        "settings.application.disconnect",
                        lambda: self.managed_action(ManagedAction.DISCONNECT),
                    ),
                    self._action(
                        "managed_refresh",
                        "settings.application.refresh",
                        lambda: self.managed_action(ManagedAction.REFRESH),
                    ),
                ],
                wrap=True,
            )
        )
        labels = (
            "settings.tab.api",
            "settings.tab.general",
            "settings.tab.prompt",
            "settings.tab.overlay",
        )
        return [
            TextSubtab(key, t(label), controls)
            for key, label, controls in zip(
                self.tab_order, labels, (api, general, prompt, overlay), strict=True
            )
        ]

    def edit(self, field: SettingsField, value: object) -> None:
        if self.draft is None:
            raise RuntimeError("settings snapshot has not been loaded")
        self.draft.set(SettingChange(field, value))
        self._set_dirty(True)

    def _edit_secret(self, key: str, value: str) -> None:
        self.secret_entries[key] = value
        self._set_dirty(True)

    def _generic(self, field: SettingsField) -> ft.Control:
        descriptor = self.catalog.field(field.value)
        if descriptor is None:
            raise ValueError(f"missing presentation contract for {field.value}")
        if descriptor.editor == SettingsEditorKind.BOOLEAN:
            return self._switch(field, self._field_label(field))
        if descriptor.editor == SettingsEditorKind.INTEGER:
            return self._number(field, self._field_label(field), integer=True)
        if descriptor.editor == SettingsEditorKind.NUMBER:
            return self._number(field, self._field_label(field))
        if descriptor.editor not in {SettingsEditorKind.TEXT, SettingsEditorKind.OPTIONAL_TEXT}:
            return self._structured(field, descriptor.editor)
        return self._text(field, self._field_label(field))

    def _structured(self, field: SettingsField, kind: SettingsEditorKind) -> ft.Control:
        label = self._field_label(field)

        def changed(value: object) -> None:
            self.edit(field, value)

        descriptor = self.catalog.field(field.value)
        assert descriptor is not None
        sections = {
            section: tuple(option for option in descriptor.options if option.section == section)
            for section in {option.section for option in descriptor.options}
        }
        if kind == SettingsEditorKind.STRING_LIST:
            control = StringListEditor(label, changed)
        elif kind == SettingsEditorKind.STRING_MAP:
            control = StringMapEditor(
                label,
                changed,
                key_options=tuple(
                    option.semantic_id
                    for option in self.catalog.options_for(SettingsField.TRANSLATION_MODEL.value)
                ),
                value_options=tuple(
                    option.semantic_id
                    for option in self.catalog.options_for(
                        SettingsField.TRANSLATION_CONNECTION.value
                    )
                ),
            )
        elif kind == SettingsEditorKind.STRING_LIST_MAP:
            control = CustomVocabularyEditor(label, changed)
        elif kind == SettingsEditorKind.JSON_SCALAR_MAP:
            control = LocalExtraBodyEditor(label, changed)
        elif kind == SettingsEditorKind.FALLBACK:
            control = TranslationFallbackEditor(
                label,
                changed,
                sections.get("model", ()),
                sections.get("connection", ()),
                sections.get("selection_alias", ()),
            )
        elif kind == SettingsEditorKind.CAPTURE_TARGET:
            control = CaptureTargetEditor(
                label,
                changed,
                sections.get("kind", ()),
                sections.get("process_kind", ()),
            )
        elif kind == SettingsEditorKind.CALIBRATION:
            control = OverlayCalibrationEditor(label, changed, sections.get("anchor", ()))
        elif kind == SettingsEditorKind.DESKTOP_OVERLAY:
            control = DesktopOverlayEditor(label, changed, sections.get("size", ()))
        else:
            raise ValueError(f"unsupported structured editor {kind.value}")
        self.controls_by_field[field] = control
        return control

    @staticmethod
    def _field_label(field: SettingsField) -> str:
        return t(f"settings.application.field.{field.value}")

    @staticmethod
    def _option_label(value: str) -> str:
        if value in _PROPER_OPTION_LABELS:
            return t("settings.application.proper_name", name=_PROPER_OPTION_LABELS[value])
        key = _OPTION_PRESENTATION.get(value)
        if key is None:
            return t("settings.application.option.unknown")
        return t(key)

    @staticmethod
    def _secret_label(key: str) -> str:
        return t(_SECRET_PRESENTATION[key])

    @staticmethod
    def _provider_label(value: str) -> str:
        key = _PROVIDER_PRESENTATION.get(value)
        return t(key) if key is not None else t("settings.application.provider.unknown")

    @staticmethod
    def _credential_source_label(value: str) -> str:
        keys = {
            "none": "settings.application.credential.source.none",
            "keyring": "settings.application.credential.source.keyring",
            "encrypted_file": "settings.application.credential.source.encrypted_file",
            "environment": "settings.application.credential.source.environment",
        }
        return t(keys.get(value, "settings.application.credential.source.unknown"))

    @staticmethod
    def _status_label(value: str) -> str:
        labels = {
            "running": t("settings.application.status.running"),
            "off": t("settings.application.status.off"),
            "starting": t("settings.application.status.starting"),
            "connected": t("settings.application.status.connected"),
            "stopping": t("settings.application.status.stopping"),
            "applied": t("settings.application.status.applied"),
            "succeeded": t("settings.application.status.succeeded"),
            "failed": t("settings.application.status.failed"),
            "unavailable": t("settings.application.status.unavailable"),
            "cancelled": t("settings.application.status.cancelled"),
            "deferred": t("settings.application.status.deferred"),
            "started": t("settings.application.status.started"),
            "stopped": t("settings.application.status.stopped"),
            "already_active": t("settings.application.status.already_active"),
            "not_applicable": t("settings.application.status.not_applicable"),
            "verified": t("settings.application.status.verified"),
            "skipped": t("settings.application.status.skipped"),
            "reopened": t("settings.application.status.reopened"),
            "inactive": t("settings.application.status.inactive"),
            "active": t("settings.application.status.active"),
            "committed_degraded": t("settings.application.status.committed_degraded"),
            "closed": t("settings.application.status.closed"),
        }
        return labels.get(value, t("settings.application.status.unknown"))

    @staticmethod
    def _managed_state_label(value: str) -> str:
        labels = {
            "connected": t("settings.application.managed_state.connected"),
            "disconnected": t("settings.application.managed_state.disconnected"),
            "connecting": t("settings.application.managed_state.connecting"),
            "unknown": t("settings.application.managed_state.unknown"),
            "unavailable": t("settings.application.managed_state.unavailable"),
            "failed": t("settings.application.managed_state.failed"),
        }
        return labels.get(value, labels["unknown"])

    @staticmethod
    def _capture_status_label(value: str) -> str:
        labels = {
            "ready": t("settings.application.capture_status.ready"),
            "unavailable": t("settings.application.capture_status.unavailable"),
            "failed": t("settings.application.capture_status.failed"),
            "available": t("settings.application.capture_status.available"),
            "refresh_failed": t("settings.application.capture_status.refresh_failed"),
        }
        return labels.get(value, t("settings.application.status.unknown"))

    @staticmethod
    def _consent_label(value: str) -> str:
        labels = {
            "allow": t("settings.application.consent.allow"),
            "decline": t("settings.application.consent.decline"),
            "unset": t("settings.application.consent.unset"),
        }
        return labels.get(value, labels["unset"])

    @staticmethod
    def _capture_reason_label(value: str) -> str:
        labels = {
            "target_unavailable": t(
                "settings.peer_translation.warning.process_unavailable_no_process"
            ),
            "process_not_found": t(
                "settings.peer_translation.warning.process_unavailable_no_process"
            ),
            "process_access_denied": t(
                "settings.peer_translation.warning.process_unavailable_ineligible"
            ),
            "process_ineligible": t(
                "settings.peer_translation.warning.process_unavailable_ineligible"
            ),
            "setup_failure": t("settings.peer_translation.warning.process_setup_failed"),
            "target_exited": t("settings.peer_translation.warning.process_target_exited"),
            "source_failure": t("settings.peer_translation.warning.process_source_failed"),
            "provider_failure": t("settings.peer_translation.warning.process_provider_failed"),
            "runtime_failure": t("settings.peer_translation.warning.process_capture_failed"),
            "success": t("settings.application.status.succeeded"),
            "not_applicable": t("settings.application.status.not_applicable"),
        }
        return labels.get(value, t("settings.application.status.unknown"))

    @staticmethod
    def _interaction_detail_label(value: str | None) -> str:
        if not value:
            return t("settings.application.detail.none")
        key = _DETAIL_PRESENTATION.get(value)
        return t(key) if key is not None else t("settings.application.detail.unknown")

    @classmethod
    def _status_with_detail(cls, status: str, detail: str | None) -> str:
        if not detail:
            return cls._status_label(status)
        return t(
            "settings.application.status_with_detail",
            status=cls._status_label(status),
            detail=cls._interaction_detail_label(detail),
        )

    @staticmethod
    def _overlay_failure_label(value: str | None) -> str | None:
        if not value:
            return None
        return t(
            f"settings.overlay.failure.{value}",
            default=t("settings.application.detail.unknown"),
        )

    def _render_credentials(self, entries: tuple[SecretMetadata, ...]) -> None:
        by_key = {entry.key: entry for entry in entries}
        for key, control in self.credential_metadata_controls.items():
            entry = by_key.get(key)
            present = bool(entry and entry.present)
            source = entry.source.value if entry else SecretSourceStatus.NONE.value
            verification = (
                entry.verification.value
                if entry is not None
                else SecretVerificationStatus.UNKNOWN.value
            )
            control.value = t(
                "settings.application.credential.metadata",
                configured=t(
                    "settings.application.credential.configured"
                    if present
                    else "settings.application.credential.not_configured"
                ),
                source=self._credential_source_label(source),
                verification=self._status_label(verification),
            )

    def _render_runtime_facts(self, facts: RuntimeFacts) -> None:
        self.peer_runtime_text.value = t(
            "settings.application.runtime.peer",
            status=self._status_label("active" if facts.peer_active else "inactive"),
        )
        self.overlay_active_text.value = t(
            "settings.application.runtime.overlay",
            status=self._status_label("active" if facts.overlay_active else "inactive"),
        )
        self.microphone_runtime_text.value = t(
            "settings.application.runtime.microphone",
            status=self._status_label("active" if facts.microphone_test_active else "inactive"),
        )

    def _set_dirty(self, dirty: bool) -> None:
        self.apply_button.disabled = not dirty or self._pending
        self.apply_button.text = t(
            "settings.application.pending" if self._pending else "settings.application.apply"
        )
        for field, control in self.controls_by_field.items():
            if hasattr(control, "refresh_locale"):
                control.refresh_locale(self._field_label(field))
            elif hasattr(control, "label"):
                control.label = self._field_label(field)
            if isinstance(control, ft.Dropdown) and field in self.semantic_option_fields:
                for option in control.options:
                    option.text = self._option_label(option.key)
        for key, control in self.secret_controls.items():
            control.label = self._secret_label(key)
        for key, control in self.interaction_controls.items():
            control.text = t(self._interaction_label_keys[key])
        self.capture_target_control.label = t("settings.application.capture_target")
        self.telemetry_control.label = t("settings.application.telemetry")
        for option in self.telemetry_control.options:
            option.text = self._consent_label(option.key)
        self._update()

    def set_overlay_calibration(self, value: OverlayCalibrationValue) -> None:
        self.edit(SettingsField.OVERLAY_CALIBRATION, value)

    def set_desktop_overlay(self, value: DesktopOverlayValue) -> None:
        self.edit(SettingsField.OVERLAY_DESKTOP_FLET, value)

    async def load(self) -> UiSettingsSnapshot:
        snapshot = await self.application.snapshot()
        self.render(snapshot)
        return snapshot

    def render(self, snapshot: UiSettingsSnapshot) -> None:
        self.snapshot = snapshot
        self.draft = snapshot.edit()
        values = self._snapshot_values(snapshot)
        for field, value in values.items():
            control = self.controls_by_field.get(field)
            if control is not None:
                descriptor = self.catalog.field(field.value)
                if descriptor is not None and descriptor.editor not in {
                    SettingsEditorKind.TEXT,
                    SettingsEditorKind.OPTIONAL_TEXT,
                    SettingsEditorKind.BOOLEAN,
                    SettingsEditorKind.INTEGER,
                    SettingsEditorKind.NUMBER,
                }:
                    control.value = value
                else:
                    control.value = value
        self._refresh_locale(snapshot.ui_clipboard_telemetry.locale)
        self.managed_text.value = snapshot.managed.connection_state
        self.capture_status.value = snapshot.capture.status
        self._render_capture(snapshot.capture)
        self._render_managed(snapshot.managed)
        self._render_credentials(snapshot.credentials.entries)
        self._render_runtime_facts(snapshot.runtime)
        self.telemetry_control.value = snapshot.ui_clipboard_telemetry.telemetry_consent
        self.telemetry_status.value = self._consent_label(
            snapshot.ui_clipboard_telemetry.telemetry_consent
        )
        self.runtime_text.value = t(
            "settings.application.runtime_status",
            status=self._status_label(snapshot.runtime.overlay_state),
        )
        overlay_failure = self._overlay_failure_label(snapshot.runtime.overlay_failure_code)
        if overlay_failure is not None:
            self.runtime_text.value = t(
                "settings.application.status_with_detail",
                status=self.runtime_text.value,
                detail=overlay_failure,
            )
        if snapshot.verification:
            latest = snapshot.verification[-1]
            self.verification_status.value = t(
                "settings.application.verification_status",
                provider=self._provider_label(latest.provider),
                status=self._status_with_detail(latest.status, latest.detail_code),
            )
        self.status_text.value = ""
        self._set_dirty(False)
        self._update()

    async def submit(self) -> UiSettingsResult:
        if self.draft is None:
            raise RuntimeError("settings snapshot has not been loaded")
        pending_secrets = dict(self.secret_entries)
        self._pending = True
        self._set_dirty(True)
        try:
            for key, value in pending_secrets.items():
                if value:
                    await self.application.interactions.set_secret(key, value)
            result = await self.application.apply(self.draft.delta())
        except Exception:
            self._pending = False
            self._set_dirty(True)
            raise
        self._pending = False
        self.secret_entries.clear()
        for control in self.secret_controls.values():
            control.value = ""
        self.render(result.snapshot)
        if isinstance(result, UiSettingsConflict):
            key, severity = "settings.application.conflict", "warning"
        elif isinstance(result, UiSettingsDegraded):
            key, severity = "settings.application.degraded", "error"
        elif isinstance(result, UiSettingsApplied):
            key, severity = "settings.application.applied", "success"
        self.status_text.value = t(key)
        self._notify(self.status_text.value, severity)
        self._update()
        return result

    async def verify_provider(self, provider: str, secret_key: str) -> ProviderVerificationSnapshot:
        result = await self.application.interactions.verify_provider(provider, secret_key)
        self.verification_status.value = t(
            "settings.application.verification_status",
            provider=self._provider_label(result.provider),
            status=self._status_with_detail(result.status, result.detail_code),
        )
        await self.load()
        return result

    async def refresh_audio_devices(self) -> AudioDeviceQueryResult:
        host = self.controls_by_field[SettingsField.AUDIO_INPUT_HOST_API].value or ""
        result = await self.application.interactions.query_audio_devices(host)
        self._render_audio(result)
        return result

    def _render_audio(self, result: AudioDeviceQueryResult) -> None:
        host = self.controls_by_field[SettingsField.AUDIO_INPUT_HOST_API]
        microphone = self.controls_by_field[SettingsField.AUDIO_INPUT_DEVICE]
        output = self.controls_by_field[SettingsField.DESKTOP_AUDIO_OUTPUT_DEVICE]
        host.options = [ft.dropdown.Option(key=value, text=value) for value in result.host_apis]
        microphone.options = [
            ft.dropdown.Option(key=item.device_id, text=item.display_name) for item in result.inputs
        ]
        output.options = [
            ft.dropdown.Option(key=item.device_id, text=item.display_name)
            for item in result.outputs
        ]
        self.interaction_status.value = self._status_with_detail(
            result.status.value, result.detail_code
        )
        self._update()

    async def refresh_capture_targets(self) -> CaptureTargetSnapshot:
        result = await self.application.interactions.capture_targets(
            self.capture_target_control.value
        )
        self._render_capture(result)
        self._update()
        return result

    def _render_capture(self, result: CaptureTargetSnapshot) -> None:
        self._capture_presentation = result
        names = {item.target_id: item.display_name for item in result.options}
        self.capture_target_control.options = [
            ft.dropdown.Option(
                key=item.target_id,
                text=item.display_name,
                disabled=not item.available,
            )
            for item in result.options
        ]
        self.capture_target_control.value = result.selected_id
        self.capture_status.value = self._capture_status_label(result.status)
        self.capture_active_status.value = t(
            "settings.application.capture_ids",
            selected=(
                names.get(result.selected_id, result.selected_id)
                if result.selected_id
                else t("settings.application.capture.none")
            ),
            active=(
                names.get(result.active_id, result.active_id)
                if result.active_id
                else t("settings.application.capture.none")
            ),
        )

    async def set_telemetry_consent(self, consent: str) -> InteractionResult:
        result = await self.application.interactions.set_telemetry_consent(consent)
        if result.status.value == "applied":
            self.telemetry_control.value = consent
        self.telemetry_status.value = t(
            "settings.application.telemetry_result",
            status=self._status_with_detail(result.status.value, result.detail_code),
        )
        self._update()
        return result

    async def _clear_secret(self, key: str) -> None:
        await self.application.interactions.clear_secret(key)
        self.secret_entries.pop(key, None)
        self.secret_controls[key].value = ""
        await self.load()

    async def start_pkce(
        self, selection_alias: str, model: str, launch_source: str = "settings"
    ) -> InteractionResult:
        if self.draft is None:
            raise RuntimeError("settings snapshot has not been loaded")
        result = await self.application.interactions.start_pkce(
            PkceStartRequest(selection_alias, model, self.draft.expected_revision, launch_source)
        )
        await self.load()
        self._render_pkce_result(result)
        return result

    async def reopen_pkce(self) -> InteractionResult:
        result = await self.application.interactions.reopen_pkce()
        self._render_pkce_result(result)
        return result

    async def cancel_pkce(self) -> InteractionResult:
        result = await self.application.interactions.cancel_pkce()
        self._render_pkce_result(result)
        return result

    def _render_pkce_result(self, result: InteractionResult) -> None:
        self.interaction_status.value = t(
            "settings.application.pkce_result",
            status=self._status_label(result.status.value),
            detail=self._interaction_detail_label(result.detail_code),
        )
        self._update()

    async def _start_current_pkce(self) -> InteractionResult:
        snapshot = self.snapshot
        if snapshot is None:
            raise RuntimeError("settings snapshot has not been loaded")
        return await self.start_pkce(
            snapshot.providers.openrouter_selection_alias or "gemma4_byok",
            snapshot.providers.openrouter_model or snapshot.translation.model or "gemma4",
        )

    def request_pkce(self, launch_source: str) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            self._notify(t("settings.application.interaction_failed"), "error")
            return
        self._schedule(
            self.start_pkce(
                snapshot.providers.openrouter_selection_alias or "gemma4_byok",
                snapshot.providers.openrouter_model or snapshot.translation.model or "gemma4",
                launch_source,
            )
        )

    async def overlay_action(self, action: OverlayAction) -> InteractionResult:
        result = await self.application.interactions.overlay_action(action)
        self.interaction_status.value = t(
            "settings.application.overlay_result",
            status=self._status_with_detail(result.status.value, result.detail_code),
        )
        await self.load()
        return result

    async def microphone_test(self, start: bool) -> MicrophoneTestResult:
        result = await self.application.interactions.microphone_test(start)
        self.interaction_status.value = t(
            "settings.application.microphone_result",
            status=self._status_with_detail(result.status.value, result.detail_code),
        )
        await self.load()
        return result

    async def retry_capture(self) -> CaptureRetryResult:
        result = await self.application.interactions.retry_capture()
        await self.load()
        self.capture_status.value = t(
            "settings.application.capture_retry_result",
            status=self._status_label(result.status.value),
            reason=self._capture_reason_label(result.reason.value),
        )
        if result.process_detail:
            self.capture_status.value = t(
                "settings.application.status_with_detail",
                status=self.capture_status.value,
                detail=result.process_detail,
            )
        self._update()
        return result

    async def select_capture_target(self, target_id: str) -> UiSettingsResult:
        if self.draft is None:
            raise RuntimeError("settings snapshot has not been loaded")
        result = await self.application.select_capture_target(
            target_id, self.draft.expected_revision
        )
        self.render(result.snapshot)
        return result

    async def managed_action(self, action: ManagedAction) -> ManagedActionResult:
        result = await self.application.interactions.managed_action(action)
        self._render_managed(result.presentation, result.status.value, result.detail_code)
        self._update()
        return result

    def _render_managed(
        self,
        presentation,
        result_status: str | None = None,
        detail_code: str | None = None,
    ) -> None:
        parts = [self._managed_state_label(presentation.connection_state)]
        if presentation.trial_remaining_percent is not None:
            parts.append(
                t("settings.application.managed_trial", value=presentation.trial_remaining_percent)
            )
        if presentation.referral_id:
            parts.append(t("settings.application.managed_referral", value=presentation.referral_id))
        if presentation.pass_status:
            parts.append(
                t(
                    "settings.application.managed_pass",
                    value=presentation.pass_status,
                )
            )
        if result_status:
            parts.append(self._status_with_detail(result_status, detail_code))
        self.managed_text.value = " · ".join(parts)
        available = set(presentation.available_actions)
        for action in ManagedAction:
            for prefix in ("managed_", "managed_primary_"):
                control = self.interaction_controls.get(f"{prefix}{action.value}")
                if control is not None:
                    control.disabled = action not in available

    def _schedule(self, operation: Awaitable[T]) -> None:
        page = self.page
        if page is None:
            close = getattr(operation, "close", None)
            if close is not None:
                close()
            return

        async def run() -> None:
            try:
                await self.application.run_interaction(operation)
            except Exception:
                logger.exception("settings interaction failed")
                self._notify(t("settings.application.interaction_failed"), "error")

        page.run_task(run)

    def _refresh_locale(self, locale: str | None) -> None:
        if not locale or locale == self._rendered_locale:
            return
        if locale != get_locale():
            set_locale(locale)
        self._rendered_locale = locale
        self.subtabs.set_font_family(font_for_language(locale))
        self.apply_button.text = t(
            "settings.application.pending" if self._pending else "settings.application.apply"
        )
        for field, control in self.controls_by_field.items():
            if hasattr(control, "refresh_locale"):
                control.refresh_locale(self._field_label(field))
            elif hasattr(control, "label"):
                control.label = self._field_label(field)
            if isinstance(control, ft.Dropdown) and field in self.semantic_option_fields:
                for option in control.options:
                    option.text = self._option_label(option.key)
        for key, control in self.secret_controls.items():
            control.label = self._secret_label(key)
        for key, control in self.interaction_controls.items():
            control.text = t(self._interaction_label_keys[key])
        self.capture_target_control.label = t("settings.application.capture_target")
        self.telemetry_control.label = t("settings.application.telemetry")
        for option in self.telemetry_control.options:
            option.text = self._consent_label(option.key)
        for key, label in zip(
            self.tab_order,
            (
                "settings.tab.api",
                "settings.tab.general",
                "settings.tab.prompt",
                "settings.tab.overlay",
            ),
            strict=True,
        ):
            self.subtabs.set_tab_label(key, t(label))
        if self.on_locale_changed is not None:
            self.on_locale_changed(locale)

    def refresh_locale(self, locale: str) -> None:
        draft = self.draft
        secret_entries = dict(self.secret_entries)
        self._refresh_locale(locale)
        self.draft = draft
        self.secret_entries = secret_entries
        for key, value in secret_entries.items():
            self.secret_controls[key].value = value
        if self.snapshot is not None:
            self._render_capture(self._capture_presentation or self.snapshot.capture)
            self._render_managed(self.snapshot.managed)
            self._render_credentials(self.snapshot.credentials.entries)
            self._render_runtime_facts(self.snapshot.runtime)
            self.runtime_text.value = t(
                "settings.application.runtime_status",
                status=self._status_label(self.snapshot.runtime.overlay_state),
            )
            overlay_failure = self._overlay_failure_label(
                self.snapshot.runtime.overlay_failure_code
            )
            if overlay_failure is not None:
                self.runtime_text.value = t(
                    "settings.application.status_with_detail",
                    status=self.runtime_text.value,
                    detail=overlay_failure,
                )
            self.telemetry_status.value = self._consent_label(
                self.snapshot.ui_clipboard_telemetry.telemetry_consent
            )
            if self.snapshot.verification:
                latest = self.snapshot.verification[-1]
                self.verification_status.value = t(
                    "settings.application.verification_status",
                    provider=self._provider_label(latest.provider),
                    status=self._status_with_detail(latest.status, latest.detail_code),
                )
        self._update()

    def _notify(self, message: str, severity: str) -> None:
        if self.on_snackbar is not None:
            self.on_snackbar(message, severity)

    def _update(self) -> None:
        if self.page is not None:
            self.update()

    @staticmethod
    def _snapshot_values(snapshot: UiSettingsSnapshot) -> dict[SettingsField, object]:
        return {
            SettingsField.TRANSLATION_MODEL: snapshot.translation.model,
            SettingsField.TRANSLATION_CONNECTION: snapshot.translation.connection,
            SettingsField.TRANSLATION_CONNECTION_HISTORY: snapshot.translation.connection_history,
            SettingsField.TRANSLATION_FALLBACK: snapshot.translation.fallback,
            SettingsField.OPENROUTER_LLM_MODEL: snapshot.providers.openrouter_model,
            SettingsField.OPENROUTER_ROUTING_MODE: snapshot.providers.openrouter_routing_mode,
            SettingsField.OPENROUTER_PROVIDER_ROUTING: snapshot.providers.openrouter_provider_routing,
            SettingsField.OPENROUTER_SELECTED_SOURCE: snapshot.providers.openrouter_selected_source,
            SettingsField.OPENROUTER_SELECTION_ALIAS: snapshot.providers.openrouter_selection_alias,
            SettingsField.OPENROUTER_BROKER_BASE_URL: snapshot.providers.openrouter_broker_base_url,
            SettingsField.QWEN_LLM_MODEL: snapshot.providers.qwen_model,
            SettingsField.QWEN_REGION: snapshot.providers.qwen_region,
            SettingsField.CEREBRAS_LLM_MODEL: snapshot.providers.cerebras_model,
            SettingsField.LOCAL_LLM_BASE_URL: snapshot.providers.local_base_url,
            SettingsField.LOCAL_LLM_BACKEND: snapshot.providers.local_backend,
            SettingsField.LOCAL_LLM_MODEL: snapshot.providers.local_model,
            SettingsField.LOCAL_LLM_EXTRA_BODY: snapshot.providers.local_extra_body,
            SettingsField.LLM_CONCURRENCY_LIMIT: snapshot.providers.concurrency_limit,
            SettingsField.PROVIDER_STT: snapshot.stt.self_provider,
            SettingsField.PROVIDER_PEER_STT: snapshot.stt.peer_provider,
            SettingsField.SOURCE_LANGUAGE: snapshot.languages.source,
            SettingsField.TARGET_LANGUAGE: snapshot.languages.target,
            SettingsField.PEER_SOURCE_LANGUAGE: snapshot.languages.peer_source,
            SettingsField.PEER_TARGET_LANGUAGE: snapshot.languages.peer_target,
            SettingsField.PEER_SOURCE_MODE: snapshot.languages.peer_source_mode,
            SettingsField.PEER_EXPECTED_LANGUAGES: snapshot.languages.peer_expected,
            SettingsField.RECENT_SOURCE_LANGUAGES: snapshot.languages.recent_source,
            SettingsField.RECENT_TARGET_LANGUAGES: snapshot.languages.recent_target,
            SettingsField.AUDIO_RING_BUFFER_MS: snapshot.audio.ring_buffer_ms,
            SettingsField.AUDIO_INPUT_HOST_API: snapshot.audio.input_host_api,
            SettingsField.AUDIO_INPUT_DEVICE: snapshot.audio.input_device,
            SettingsField.DESKTOP_AUDIO_OUTPUT_DEVICE: snapshot.audio.desktop_output_device,
            SettingsField.DESKTOP_AUDIO_CAPTURE_TARGET: snapshot.audio.capture_target,
            SettingsField.STT_VAD_THRESHOLD: snapshot.stt.vad_threshold,
            SettingsField.DESKTOP_VAD_THRESHOLD: snapshot.audio.desktop_vad_threshold,
            SettingsField.DESKTOP_VAD_HANGOVER_MS: snapshot.audio.desktop_vad_hangover_ms,
            SettingsField.DESKTOP_VAD_PRE_ROLL_MS: snapshot.audio.desktop_vad_pre_roll_ms,
            SettingsField.STT_DRAIN_TIMEOUT_S: snapshot.stt.drain_timeout_s,
            SettingsField.STT_LOW_LATENCY_MODE: snapshot.stt.low_latency,
            SettingsField.STT_CUSTOM_VOCABULARY_ENABLED: snapshot.stt.custom_vocabulary,
            SettingsField.STT_CUSTOM_TERMS: snapshot.stt.custom_terms,
            SettingsField.STT_LOW_LATENCY_VAD_HANGOVER_MS: snapshot.stt.low_latency_hangover_ms,
            SettingsField.STT_LOW_LATENCY_MERGE_GAP_MS: snapshot.stt.low_latency_merge_gap_ms,
            SettingsField.STT_LOW_LATENCY_SPEC_RETRY_MAX: snapshot.stt.low_latency_retry_max,
            SettingsField.DEEPGRAM_STT_MODEL: snapshot.stt.deepgram_model,
            SettingsField.QWEN_ASR_STT_MODEL: snapshot.stt.qwen_asr_model,
            SettingsField.SONIOX_STT_MODEL: snapshot.stt.soniox_model,
            SettingsField.SONIOX_STT_ENDPOINT: snapshot.stt.soniox_endpoint,
            SettingsField.SONIOX_STT_KEEPALIVE_S: snapshot.stt.soniox_keepalive_s,
            SettingsField.SONIOX_STT_TRAILING_SILENCE_MS: snapshot.stt.soniox_trailing_silence_ms,
            SettingsField.UI_LOCALE: snapshot.ui_clipboard_telemetry.locale,
            SettingsField.CLIPBOARD_AUTO_TRANSLATE: snapshot.ui_clipboard_telemetry.clipboard_auto_translate,
            SettingsField.INTEGRATED_CONTEXT_ENABLED: snapshot.ui_clipboard_telemetry.integrated_context,
            SettingsField.SYSTEM_PROMPT: snapshot.prompt.system_prompt,
            SettingsField.SECRETS_BACKEND: snapshot.ui_clipboard_telemetry.secrets_backend,
            SettingsField.SECRETS_ENCRYPTED_FILE_PATH: snapshot.ui_clipboard_telemetry.secrets_encrypted_file_path,
            SettingsField.OVERLAY_TARGET: snapshot.overlay.target,
            SettingsField.OVERLAY_SHOW_TRANSLATION: snapshot.overlay.show_translation,
            SettingsField.OVERLAY_SHOW_PEER_ORIGINAL: snapshot.overlay.show_peer_original,
            SettingsField.OVERLAY_CALIBRATION: snapshot.overlay.calibration,
            SettingsField.OVERLAY_DESKTOP_FLET: snapshot.overlay.desktop,
            SettingsField.OSC_HOST: snapshot.osc_output.host,
            SettingsField.OSC_PORT: snapshot.osc_output.port,
            SettingsField.OSC_CHATBOX_ADDRESS: snapshot.osc_output.chatbox_address,
            SettingsField.OSC_CHATBOX_SEND: snapshot.osc_output.send,
            SettingsField.OSC_CHATBOX_CLEAR: snapshot.osc_output.clear,
            SettingsField.OSC_CHATBOX_MAX_CHARS: snapshot.osc_output.max_chars,
            SettingsField.OSC_CHATBOX_INCLUDE_SOURCE: snapshot.osc_output.include_source,
            SettingsField.OSC_VRC_MIC_INTERCEPT: snapshot.osc_output.vrc_mic_intercept,
        }


__all__ = ["ApplicationSettingsView"]
