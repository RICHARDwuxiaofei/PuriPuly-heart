from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from puripuly_heart.config.audio_host_api import WINDOWS_DIRECTSOUND_HOST_API
from puripuly_heart.config.settings import (
    DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS,
    DEFAULT_OPENROUTER_BROKER_BASE_URL,
    OVERLAY_TARGET_DESKTOP,
    AppSettings,
    DeepSeekLLMModel,
    GeminiLLMModel,
    OpenRouterCredentialSource,
    OpenRouterFallbackSelectionAlias,
    OpenRouterLLMModel,
    OpenRouterProviderRouting,
    OpenRouterRoutingMode,
    OpenRouterSelectionAlias,
    QwenLLMModel,
    QwenRegion,
    SecretsBackend,
    STTProviderName,
    TranslationConnection,
    TranslationModel,
    TranslationSettings,
    from_dict,
    to_dict,
)
from puripuly_heart.ui.overlay_calibration import OverlayCalibration

MAXIMAL_V24_FIXTURE_NAME = "maximal_v24_settings"
LEGACY_COMPATIBILITY_FIXTURE_NAME = "legacy_compatibility_settings"
MISSING_DEFAULTS_FIXTURE_NAME = "missing_field_defaults"

DYNAMIC_MAPPING_PATHS = frozenset(
    {
        "local_llm.extra_body",
        "stt.custom_terms",
        "translation.connection_history",
    }
)

SCHEMA_METADATA_CURRENT_PATHS = frozenset({"settings_version"})
SINGLETON_SUPPORTED_VALUE_CURRENT_PATHS = frozenset({"overlay.calibration.anchor"})
REPAIR_TO_CANONICAL_DEFAULT_CURRENT_PATHS = frozenset(
    {
        "audio.internal_channels",
        "audio.internal_sample_rate_hz",
        "local_llm.backend",
    }
)

USER_INTENT_CURRENT_PATHS = frozenset(
    {
        "audio.input_device",
        "audio.input_host_api",
        "audio.ring_buffer_ms",
        "deepgram_stt.model",
        "desktop_audio.output_device",
        "desktop_audio.vad_hangover_ms",
        "desktop_audio.vad_pre_roll_ms",
        "desktop_audio.vad_speech_threshold",
        "languages.peer_source_language",
        "languages.peer_target_language",
        "languages.recent_source_languages",
        "languages.recent_target_languages",
        "languages.source_language",
        "languages.target_language",
        "llm.concurrency_limit",
        "local_llm.base_url",
        "local_llm.extra_body",
        "local_llm.model",
        "openrouter.broker_base_url",
        "openrouter.fallback_selection_alias",
        "openrouter.routing_mode",
        "osc.chatbox_address",
        "osc.chatbox_clear",
        "osc.chatbox_include_source",
        "osc.chatbox_max_chars",
        "osc.chatbox_send",
        "osc.host",
        "osc.port",
        "osc.vrc_mic_intercept",
        "overlay.calibration.background_alpha",
        "overlay.calibration.distance",
        "overlay.calibration.offset_x",
        "overlay.calibration.offset_y",
        "overlay.calibration.text_scale",
        "overlay.desktop_flet.position.x",
        "overlay.desktop_flet.position.y",
        "overlay.desktop_flet.size_preset",
        "overlay.desktop_flet.visual.background_alpha",
        "overlay.show_peer_original",
        "overlay.show_translation",
        "overlay.target",
        "provider.peer_stt",
        "provider.stt",
        "qwen.llm_model",
        "qwen.region",
        "qwen_asr_stt.model",
        "secrets.backend",
        "secrets.encrypted_file_path",
        "soniox_stt.endpoint",
        "soniox_stt.keepalive_interval_s",
        "soniox_stt.model",
        "soniox_stt.trailing_silence_ms",
        "stt.custom_terms",
        "stt.custom_vocabulary_enabled",
        "stt.drain_timeout_s",
        "stt.low_latency_merge_gap_ms",
        "stt.low_latency_mode",
        "stt.low_latency_spec_retry_max",
        "stt.low_latency_vad_hangover_ms",
        "stt.vad_speech_threshold",
        "system_prompt",
        "translation.connection",
        "translation.connection_history",
        "translation.model",
        "ui.clipboard_auto_translate_enabled",
        "ui.integrated_context_enabled",
        "ui.locale",
    }
)

CURRENT_COMPATIBILITY_INPUT_PATHS = frozenset(
    {
        "deepseek.llm_model",
        "gemini.llm_model",
        "openrouter.llm_model",
        "openrouter.provider_routing",
        "openrouter.selected_source",
        "openrouter.selection_alias",
        "provider.llm",
        "qwen_asr_stt.endpoint",
    }
)

PERSISTED_OPERATIONAL_STATE_CURRENT_PATHS = frozenset(
    {
        "api_key_verified.alibaba_beijing",
        "api_key_verified.alibaba_singapore",
        "api_key_verified.deepgram",
        "api_key_verified.deepseek",
        "api_key_verified.google",
        "api_key_verified.openrouter",
        "api_key_verified.soniox",
        "managed_identity.active_managed_credential_ref",
        "managed_identity.active_managed_expires_at",
        "managed_identity.founder_letter_seen_credential_ref",
        "managed_identity.installation_id",
        "managed_identity.referral_id",
        "managed_identity.release_token",
        "managed_identity.release_token_expires_at",
        "managed_identity.verified_hardware_hash",
        "managed_identity.verified_hardware_hash_salt_version",
        "ui.github_star_prompt_clicked",
        "ui.github_star_prompt_eligible_launch_count",
        "ui.github_star_prompt_last_shown_at",
        "ui.github_star_prompt_show_count",
        "ui.github_star_prompt_translation_success_observed",
    }
)

DECISION_PENDING_CURRENT_DESTINATIONS = {
    "ui.integrated_context_bootstrapped": "state.integrated_context.*",
    "ui.peer_translation_eula_accepted": "state.peer_translation.* or intent.ui.*",
}


@dataclass(frozen=True, slots=True)
class FieldClassification:
    category: str
    destination: str
    status: str
    fixture: str
    notes: str = ""
    missing_default_fixture: str = MISSING_DEFAULTS_FIXTURE_NAME


def serialized_field_paths(data: dict[str, Any], prefix: str = "") -> Iterator[str]:
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and path in DYNAMIC_MAPPING_PATHS:
            yield path
        elif isinstance(value, dict) and value:
            yield from serialized_field_paths(value, path)
        elif isinstance(value, dict):
            yield path
        else:
            yield path


def path_get(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        current = current[part]
    return current


def path_remove(data: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    current: Any = data
    for part in parts[:-1]:
        current = current[part]
    current.pop(parts[-1], None)


EXPLICIT_MISSING_FIELD_DEFAULT_EXPECTATIONS: dict[str, Any] = {
    # Partial legacy settings default missing peer STT to Deepgram, while new AppSettings defaults
    # peer STT to local Qwen.
    "provider.peer_stt": STTProviderName.DEEPGRAM.value,
    # Broker URL is a public compatibility surface; missing values restore the production /v1 broker.
    "openrouter.broker_base_url": DEFAULT_OPENROUTER_BROKER_BASE_URL,
    # Current peer desktop-audio VAD default is intentionally lower than older schema defaults.
    "desktop_audio.vad_hangover_ms": DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS,
    # Missing vocabulary restores the shipped multilingual terms and keeps vocabulary enabled.
    "stt.custom_terms": {
        "ko": ["아이리", "시나노"],
        "en": ["airi", "shinano"],
        "zh-CN": ["airi", "shinano"],
        "ja": ["airi", "shinano"],
    },
    # Integrated context remains enabled for missing legacy UI settings.
    "ui.integrated_context_enabled": True,
    # Clipboard watcher and GitHub prompt counters are opt-in/operational state defaults.
    "ui.clipboard_auto_translate_enabled": False,
    "ui.github_star_prompt_show_count": 0,
    # Missing referral identity state remains absent rather than inventing a value.
    "managed_identity.referral_id": None,
}


def missing_field_default_expectations() -> dict[str, Any]:
    baseline = to_dict(AppSettings())
    expectations: dict[str, Any] = {}
    for path in serialized_field_paths(baseline):
        raw = copy.deepcopy(baseline)
        path_remove(raw, path)
        expectations[path] = path_get(to_dict(from_dict(raw)), path)
    return expectations


def maximal_v24_settings_fixture() -> dict[str, Any]:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.DEEPGRAM
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.translation = TranslationSettings(
        model=TranslationModel.LOCAL_LLM,
        connection=TranslationConnection.OLLAMA,
        connection_history={
            TranslationModel.GEMMA4.value: TranslationConnection.OPENROUTER,
            TranslationModel.DEEPSEEK_V4_FLASH.value: TranslationConnection.MANAGED_CHINA,
            TranslationModel.DEEPSEEK_V4_PRO.value: TranslationConnection.OFFICIAL_BYOK,
            TranslationModel.GEMINI_3_FLASH.value: TranslationConnection.OFFICIAL_BYOK,
            TranslationModel.GEMINI_31_FLASH_LITE.value: TranslationConnection.OFFICIAL_BYOK,
            TranslationModel.QWEN_35_PLUS.value: TranslationConnection.OFFICIAL_BYOK,
            TranslationModel.LOCAL_LLM.value: TranslationConnection.OLLAMA,
        },
    )
    settings.languages.source_language = "ja"
    settings.languages.target_language = "zh-CN"
    settings.languages.peer_source_language = "fr"
    settings.languages.peer_target_language = "es"
    settings.languages.recent_source_languages = ["fr", "de", "it"]
    settings.languages.recent_target_languages = ["es", "th", "vi"]
    settings.audio.ring_buffer_ms = 750
    settings.audio.input_host_api = WINDOWS_DIRECTSOUND_HOST_API
    settings.audio.input_device = "Fixture Microphone"
    settings.desktop_audio.output_device = "Fixture Speakers"
    settings.desktop_audio.vad_speech_threshold = 0.4
    settings.desktop_audio.vad_hangover_ms = 650
    settings.desktop_audio.vad_pre_roll_ms = 250
    settings.overlay.target = OVERLAY_TARGET_DESKTOP
    settings.overlay.show_translation = False
    settings.overlay.show_peer_original = False
    settings.overlay.calibration = OverlayCalibration(
        anchor="head_locked",
        offset_x=0.25,
        offset_y=-0.2,
        distance=1.8,
        text_scale=1.3,
        background_alpha=0.5,
    )
    settings.overlay.desktop_flet.size_preset = "large"
    settings.overlay.desktop_flet.position.x = 321
    settings.overlay.desktop_flet.position.y = 654
    settings.overlay.desktop_flet.visual.background_alpha = 0.42
    settings.stt.drain_timeout_s = 3.5
    settings.stt.vad_speech_threshold = 0.3
    settings.stt.low_latency_mode = False
    settings.stt.low_latency_vad_hangover_ms = 700
    settings.stt.low_latency_merge_gap_ms = 550
    settings.stt.low_latency_spec_retry_max = 5
    settings.stt.custom_vocabulary_enabled = False
    settings.stt.custom_terms = {"en": ["fixture-term"], "ja": ["フィクスチャ"]}
    settings.deepgram_stt.model = "nova-2"
    settings.qwen_asr_stt.model = "qwen-asr-fixture"
    settings.soniox_stt.model = "stt-rt-fixture"
    settings.soniox_stt.endpoint = "wss://soniox.fixture.test/transcribe"
    settings.soniox_stt.keepalive_interval_s = 12.5
    settings.soniox_stt.trailing_silence_ms = 250
    settings.gemini.llm_model = GeminiLLMModel.GEMINI_3_FLASH
    settings.openrouter.llm_model = OpenRouterLLMModel.QWEN_35_FLASH_02_23
    settings.openrouter.routing_mode = OpenRouterRoutingMode.PARASAIL_FIRST
    settings.openrouter.provider_routing = OpenRouterProviderRouting.DEEPSEEK_ONLY
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    settings.openrouter.selection_alias = OpenRouterSelectionAlias.QWEN35_FLASH_BYOK
    settings.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.NONE
    settings.openrouter.broker_base_url = "https://broker.fixture.test"
    settings.qwen.region = QwenRegion.SINGAPORE
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_FLASH
    settings.deepseek.llm_model = DeepSeekLLMModel.DEEPSEEK_V4_PRO
    settings.local_llm.base_url = "http://127.0.0.1:12345/v1"
    settings.local_llm.model = "fixture-local-model"
    settings.local_llm.extra_body = {"temperature": 0.25, "reasoning_effort": "low"}
    settings.llm.concurrency_limit = 7
    settings.osc.host = "192.0.2.25"
    settings.osc.port = 9012
    settings.osc.chatbox_address = "/fixture/chatbox"
    settings.osc.chatbox_send = False
    settings.osc.chatbox_clear = True
    settings.osc.chatbox_max_chars = 96
    settings.osc.vrc_mic_intercept = True
    settings.osc.chatbox_include_source = True
    settings.secrets.backend = SecretsBackend.ENCRYPTED_FILE
    settings.secrets.encrypted_file_path = "fixture-secrets.json"
    settings.ui.locale = "ja"
    settings.ui.peer_translation_eula_accepted = True
    settings.ui.integrated_context_enabled = False
    settings.ui.integrated_context_bootstrapped = True
    settings.ui.clipboard_auto_translate_enabled = True
    settings.ui.github_star_prompt_clicked = True
    settings.ui.github_star_prompt_last_shown_at = "2026-06-08T00:00:00Z"
    settings.ui.github_star_prompt_show_count = 2
    settings.ui.github_star_prompt_translation_success_observed = True
    settings.ui.github_star_prompt_eligible_launch_count = 3
    settings.api_key_verified.deepgram = True
    settings.api_key_verified.soniox = True
    settings.api_key_verified.google = True
    settings.api_key_verified.openrouter = True
    settings.api_key_verified.deepseek = True
    settings.api_key_verified.alibaba_beijing = True
    settings.api_key_verified.alibaba_singapore = True
    settings.managed_identity.installation_id = "fixture-installation-id"
    settings.managed_identity.release_token = "fixture-release-token"
    settings.managed_identity.release_token_expires_at = "2026-07-08T00:00:00Z"
    settings.managed_identity.verified_hardware_hash = "fixture-hardware-hash"
    settings.managed_identity.verified_hardware_hash_salt_version = 7
    settings.managed_identity.active_managed_credential_ref = "fixture-credential-ref"
    settings.managed_identity.active_managed_expires_at = "2026-07-09T00:00:00Z"
    settings.managed_identity.founder_letter_seen_credential_ref = "fixture-founder-ref"
    settings.managed_identity.referral_id = "7KQ9M2"
    settings.system_prompt = "Fixture system prompt text."
    settings.validate()

    data = to_dict(settings)
    data["audio"]["internal_sample_rate_hz"] = 8000
    data["audio"]["internal_channels"] = "1"
    data["local_llm"]["backend"] = "fixture_backend"
    data["openrouter"]["provider_routing"] = OpenRouterProviderRouting.DEEPSEEK_ONLY.value
    return data


def legacy_compatibility_settings_fixture() -> dict[str, Any]:
    data = copy.deepcopy(maximal_v24_settings_fixture())
    data["settings_version"] = 17
    data["openrouter"]["credential_source"] = OpenRouterCredentialSource.BYOK.value
    data["openrouter"]["selected_credential_source"] = OpenRouterCredentialSource.MANAGED.value
    data["overlay_calibration"] = {
        "offset_x": 0.42,
        "offset_y": -0.12,
        "distance": 1.6,
        "text_scale": 1.2,
        "background_alpha": 0.33,
    }
    data["overlay"].pop("calibration", None)
    data["overlay"].pop("show_translation", None)
    data["overlay"].pop("show_peer_original", None)
    data["overlay"]["desktop_flet"]["locked"] = True
    data["ui"]["show_overlay_translation"] = False
    data["ui"]["show_overlay_peer_original"] = False
    data["ui"]["overlay_enabled"] = True
    data["ui"]["peer_translation_enabled"] = True
    data["osc"]["cooldown_s"] = 1.5
    data["osc"]["ttl_s"] = 7.0
    data["peer_deepgram_stt"] = {"model": "legacy-peer-deepgram-model"}
    data["peer_qwen_asr_stt"] = {
        "model": "legacy-peer-qwen-model",
        "region": QwenRegion.SINGAPORE.value,
    }
    data["peer_soniox_stt"] = {
        "model": "legacy-peer-soniox-model",
        "endpoint": "wss://legacy-soniox.fixture.test/transcribe",
        "keepalive_interval_s": 13.0,
        "trailing_silence_ms": 275,
    }
    data["system_prompts"] = {"legacy": "legacy fixture prompt"}
    return data


def _put_classification(
    table: dict[str, FieldClassification],
    paths: frozenset[str],
    *,
    category: str,
    destination_prefix: str,
    status: str,
    fixture: str,
    notes: str = "",
) -> None:
    for path in paths:
        if path in table:
            raise ValueError(f"duplicate migration classification path: {path}")
        table[path] = FieldClassification(
            category=category,
            destination=f"{destination_prefix}.{path}",
            status=status,
            fixture=fixture,
            notes=notes,
        )


def _current_migration_classification() -> dict[str, FieldClassification]:
    table: dict[str, FieldClassification] = {}
    _put_classification(
        table,
        USER_INTENT_CURRENT_PATHS,
        category="persisted_user_intent",
        destination_prefix="intent",
        status="retained",
        fixture=MAXIMAL_V24_FIXTURE_NAME,
    )
    _put_classification(
        table,
        CURRENT_COMPATIBILITY_INPUT_PATHS,
        category="compatibility_input",
        destination_prefix="intent.translation.compatibility",
        status="accepted_read_no_vnext_write_projection",
        fixture=MAXIMAL_V24_FIXTURE_NAME,
    )
    _put_classification(
        table,
        PERSISTED_OPERATIONAL_STATE_CURRENT_PATHS,
        category="persisted_operational_state",
        destination_prefix="state",
        status="retained_or_reclassified",
        fixture=MAXIMAL_V24_FIXTURE_NAME,
    )
    _put_classification(
        table,
        REPAIR_TO_CANONICAL_DEFAULT_CURRENT_PATHS,
        category="compatibility_repair",
        destination_prefix="intent",
        status="raw_fixture_non_default_repairs_to_canonical_default",
        fixture=MAXIMAL_V24_FIXTURE_NAME,
        notes="Maximal raw v24 fixture uses non-default input; current loader repairs it.",
    )
    table["settings_version"] = FieldClassification(
        category="schema_metadata",
        destination="settings_version",
        status="current_schema_version",
        fixture=MAXIMAL_V24_FIXTURE_NAME,
        notes="Schema metadata, not a user/state setting field; v24 fixture must remain version 24.",
    )
    table["overlay.calibration.anchor"] = FieldClassification(
        category="singleton_supported_value",
        destination="intent.overlay.calibration.anchor",
        status="singleton_supported_value",
        fixture=MAXIMAL_V24_FIXTURE_NAME,
        notes="Current overlay calibration supports only 'head_locked'; non-default values fail validation.",
    )
    for path, destination in DECISION_PENDING_CURRENT_DESTINATIONS.items():
        if path in table:
            raise ValueError(f"duplicate migration classification path: {path}")
        table[path] = FieldClassification(
            category="decision_pending",
            destination=destination,
            status="requires_decision_before_vnext_write",
            fixture=MAXIMAL_V24_FIXTURE_NAME,
            notes="Bundle watchpoint requires an exact vNext destination before migration.",
        )
    return table


V24_MIGRATION_CLASSIFICATION = _current_migration_classification()

LEGACY_MIGRATION_CLASSIFICATION: dict[str, FieldClassification] = {
    "openrouter.credential_source": FieldClassification(
        category="legacy_input",
        destination="intent.translation.openrouter.selected_source",
        status="accepted_read_drives_migration_when_current_source_absent",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "openrouter.selected_credential_source": FieldClassification(
        category="legacy_input",
        destination="intent.translation.openrouter.selected_source",
        status="accepted_read_drives_migration_when_current_source_absent",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "osc.cooldown_s": FieldClassification(
        category="retired_input",
        destination="retired",
        status="removed_by_v18_migration",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "osc.ttl_s": FieldClassification(
        category="retired_input",
        destination="retired",
        status="removed_by_v18_migration",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "overlay.desktop_flet.locked": FieldClassification(
        category="retired_input",
        destination="retired",
        status="accepted_read_not_serialized",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "overlay_calibration.background_alpha": FieldClassification(
        category="legacy_input",
        destination="intent.overlay.calibration",
        status="merged_into_overlay_calibration_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "overlay_calibration.distance": FieldClassification(
        category="legacy_input",
        destination="intent.overlay.calibration",
        status="merged_into_overlay_calibration_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "overlay_calibration.offset_x": FieldClassification(
        category="legacy_input",
        destination="intent.overlay.calibration",
        status="merged_into_overlay_calibration_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "overlay_calibration.offset_y": FieldClassification(
        category="legacy_input",
        destination="intent.overlay.calibration",
        status="merged_into_overlay_calibration_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "overlay_calibration.text_scale": FieldClassification(
        category="legacy_input",
        destination="intent.overlay.calibration",
        status="merged_into_overlay_calibration_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "peer_deepgram_stt.model": FieldClassification(
        category="retired_input",
        destination="retired",
        status="removed_by_migration",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "peer_qwen_asr_stt.model": FieldClassification(
        category="legacy_input",
        destination="intent.peer_stt.qwen_asr.compatibility",
        status="accepted_read_no_vnext_write_projection",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "peer_qwen_asr_stt.region": FieldClassification(
        category="legacy_input",
        destination="intent.peer_stt.qwen_asr.compatibility",
        status="accepted_read_no_vnext_write_projection",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "peer_soniox_stt.endpoint": FieldClassification(
        category="legacy_input",
        destination="intent.peer_stt.soniox.compatibility",
        status="accepted_read_no_vnext_write_projection",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "peer_soniox_stt.keepalive_interval_s": FieldClassification(
        category="legacy_input",
        destination="intent.peer_stt.soniox.compatibility",
        status="accepted_read_no_vnext_write_projection",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "peer_soniox_stt.model": FieldClassification(
        category="legacy_input",
        destination="intent.peer_stt.soniox.compatibility",
        status="accepted_read_no_vnext_write_projection",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "peer_soniox_stt.trailing_silence_ms": FieldClassification(
        category="legacy_input",
        destination="intent.peer_stt.soniox.compatibility",
        status="accepted_read_no_vnext_write_projection",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "system_prompts.legacy": FieldClassification(
        category="legacy_input",
        destination="intent.prompts.compatibility",
        status="accepted_read_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "ui.overlay_enabled": FieldClassification(
        category="runtime_only_reclassification",
        destination="runtime_controller_state",
        status="dropped_from_persisted_settings",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "ui.peer_translation_enabled": FieldClassification(
        category="runtime_only_reclassification",
        destination="runtime_controller_state",
        status="dropped_from_persisted_settings",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "ui.show_overlay_peer_original": FieldClassification(
        category="legacy_input",
        destination="intent.overlay.show_peer_original",
        status="merged_into_overlay_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
    "ui.show_overlay_translation": FieldClassification(
        category="legacy_input",
        destination="intent.overlay.show_translation",
        status="merged_into_overlay_removed_on_write",
        fixture=LEGACY_COMPATIBILITY_FIXTURE_NAME,
    ),
}


def missing_classification_paths(
    paths: set[str], classification: dict[str, FieldClassification]
) -> list[str]:
    return sorted(paths.difference(classification))


def migrated_serialization(data: dict[str, Any]) -> dict[str, Any]:
    settings = from_dict(data)
    return to_dict(settings)
