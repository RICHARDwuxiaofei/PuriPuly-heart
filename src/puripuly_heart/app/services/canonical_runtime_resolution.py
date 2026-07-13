from __future__ import annotations

from dataclasses import asdict, replace

from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.config.capture_target_resolution import resolve_desktop_audio_capture_target
from puripuly_heart.config.resolved import RUNTIME_CHANNEL_PEER, ResolvedApplicationRuntimeConfig
from puripuly_heart.config.runtime_resolution import (
    DirectProviderRuntimeIntent,
    OpenRouterRuntimeIntent,
    OverlayRuntimeIntent,
    RuntimeResolutionInput,
    STTRuntimeIntent,
    TranslationFallbackRuntimeIntent,
    TranslationRuntimeIntent,
    resolve_llm_config,
    resolve_overlay_config,
    resolve_stt_config,
)
from puripuly_heart.core.language import get_soniox_language_hints


class CanonicalRuntimeConfigResolver:
    def resolve(self, receipt: SettingsCommitReceipt) -> ResolvedApplicationRuntimeConfig:
        intent = receipt.envelope.intent
        translation = intent.translation
        stt = intent.stt
        languages = intent.languages
        runtime_input = RuntimeResolutionInput(
            translation=TranslationRuntimeIntent(
                model=translation.model,
                connection=translation.connection,
                concurrency_limit=translation.concurrency_limit,
            ),
            translation_fallback=TranslationFallbackRuntimeIntent(
                enabled=translation.fallback.enabled,
                model=translation.fallback.model,
                connection=translation.fallback.connection,
            ),
            openrouter=OpenRouterRuntimeIntent(
                model=translation.openrouter_model,
                selected_source=translation.openrouter_selected_source,
                selection_alias=translation.openrouter_selection_alias,
                routing_mode=translation.openrouter_routing_mode,
                provider_routing=translation.openrouter_provider_routing,
                broker_base_url=translation.openrouter_broker_base_url,
            ),
            direct=DirectProviderRuntimeIntent(
                qwen_35_plus_model=translation.qwen.llm_model,
                qwen_region=translation.qwen.region,
                local_llm_backend=intent.local_llm.backend,
                local_llm_base_url=intent.local_llm.base_url,
                local_llm_model=intent.local_llm.model,
                local_llm_extra_body=intent.local_llm.extra_body,
                cerebras_model=translation.cerebras.llm_model,
            ),
        )
        self_stt = STTRuntimeIntent(
            provider=stt.provider,
            source_language=languages.source_language,
            input_host_api=intent.audio.input_host_api,
            input_device=intent.audio.input_device,
            ring_buffer_ms=intent.audio.ring_buffer_ms,
            drain_timeout_s=stt.drain_timeout_s,
            vad_speech_threshold=stt.vad_speech_threshold,
            vad_hangover_ms=stt.low_latency_vad_hangover_ms,
            low_latency_enabled=stt.low_latency_mode,
            low_latency_merge_gap_ms=stt.low_latency_merge_gap_ms,
            low_latency_spec_retry_max=stt.low_latency_spec_retry_max,
            custom_vocabulary_enabled=stt.custom_vocabulary_enabled,
            custom_terms=stt.custom_terms,
            deepgram_model=stt.deepgram.model,
            qwen_asr_model=stt.qwen_asr.model,
            qwen_region=translation.qwen.region,
            soniox_model=stt.soniox.model,
            soniox_endpoint=stt.soniox.endpoint,
            soniox_keepalive_interval_s=stt.soniox.keepalive_interval_s,
            soniox_trailing_silence_ms=stt.soniox.trailing_silence_ms,
        )
        runtime_input = replace(runtime_input, self_stt=self_stt)
        peer_stt = STTRuntimeIntent(
            channel=RUNTIME_CHANNEL_PEER,
            provider=intent.peer_stt.provider,
            source_language=languages.peer_source_language,
            ring_buffer_ms=intent.audio.ring_buffer_ms,
            drain_timeout_s=stt.drain_timeout_s,
            output_device=intent.desktop_audio.output_device,
            vad_speech_threshold=intent.desktop_audio.vad_speech_threshold,
            vad_hangover_ms=intent.desktop_audio.vad_hangover_ms,
            vad_pre_roll_ms=intent.desktop_audio.vad_pre_roll_ms,
            low_latency_enabled=stt.low_latency_mode,
            low_latency_merge_gap_ms=stt.low_latency_merge_gap_ms,
            low_latency_spec_retry_max=stt.low_latency_spec_retry_max,
            deepgram_model=stt.deepgram.model,
            qwen_asr_model=stt.qwen_asr.model,
            qwen_region=translation.qwen.region,
            soniox_model=stt.soniox.model,
            soniox_endpoint=stt.soniox.endpoint,
            soniox_keepalive_interval_s=stt.soniox.keepalive_interval_s,
            soniox_trailing_silence_ms=stt.soniox.trailing_silence_ms,
            soniox_enable_language_identification=languages.peer_source_mode == "soniox_auto",
            soniox_language_hints=(
                tuple(
                    dict.fromkeys(
                        hint
                        for language in languages.peer_expected_languages
                        for hint in get_soniox_language_hints(language)
                    )
                )
                if languages.peer_source_mode == "soniox_auto"
                else None
            ),
        )
        overlay = intent.overlay
        return ResolvedApplicationRuntimeConfig(
            llm=resolve_llm_config(runtime_input),
            self_stt=resolve_stt_config(self_stt),
            peer_stt=replace(
                resolve_stt_config(peer_stt),
                capture_target=resolve_desktop_audio_capture_target(
                    intent.desktop_audio.capture_target
                ),
            ),
            overlay=resolve_overlay_config(
                OverlayRuntimeIntent(
                    target=overlay.target,
                    show_translation=overlay.show_translation,
                    show_peer_original=overlay.show_peer_original,
                    calibration=asdict(overlay.calibration),
                    desktop_overlay_options=asdict(overlay.desktop_flet),
                )
            ),
        )


__all__ = ["CanonicalRuntimeConfigResolver"]
