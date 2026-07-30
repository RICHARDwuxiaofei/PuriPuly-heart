from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from puripuly_heart.core.llm.attempt_metadata import (
    LLMAttemptMetadataRecorder,
    attempt_metadata_context,
)
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.domain.models import Translation


@dataclass(frozen=True, slots=True)
class RoutingAttemptTelemetry:
    name: str
    requested_provider: str
    requested_models: tuple[str, ...]
    started_offset_ms: int | None
    completed_offset_ms: int | None
    duration_ms: int | None
    outcome: str
    response_model: str | None = None
    response_provider: str | None = None
    generation_id: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    response_cost_usd: str | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingRaceTelemetry:
    race_id: str
    utterance_id: str
    started_at_utc: str
    total_user_wait_ms: int
    succeeded: bool
    winner_attempt: str | None
    fallback_enabled: bool
    first_fallback_triggered: bool
    first_fallback_trigger_reason: str | None
    second_fallback_eligible: bool
    second_fallback_triggered: bool
    dual_bill_candidate: bool
    attempts: tuple[RoutingAttemptTelemetry, ...]


@dataclass(frozen=True, slots=True)
class OpenRouterGenerationTelemetry:
    generation_id: str
    total_cost_usd: str | None
    upstream_inference_cost_usd: str | None
    provider_name: str | None
    model: str | None
    latency_ms: int | None
    generation_time_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cancelled: bool | None
    is_byok: bool | None
    finish_reason: str | None


class RoutingTelemetrySink(Protocol):
    @property
    def active(self) -> bool: ...

    def record_race(self, record: RoutingRaceTelemetry) -> None: ...

    def register_openrouter_generation(self, generation_id: str) -> str | None: ...

    def record_openrouter_generation(
        self,
        capture_id: str,
        record: OpenRouterGenerationTelemetry,
    ) -> None: ...

    def record_openrouter_generation_failure(
        self,
        capture_id: str,
        generation_id: str,
        error_type: str,
    ) -> None: ...


@dataclass(slots=True)
class SingleAttemptRoutingTelemetryProvider(LLMProvider):
    inner: LLMProvider
    requested_provider: str
    requested_models: tuple[str, ...]
    telemetry: RoutingTelemetrySink

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        started_at = time.monotonic()
        started_at_utc = _utc_now()
        recorder = LLMAttemptMetadataRecorder()
        result: Translation | None = None
        error: BaseException | None = None
        outcome = "success"
        try:
            with attempt_metadata_context(recorder):
                result = await self.inner.translate(
                    utterance_id=utterance_id,
                    text=text,
                    system_prompt=system_prompt,
                    source_language=source_language,
                    target_language=target_language,
                    context=context,
                )
            return result
        except asyncio.CancelledError as exc:
            error = exc
            outcome = "cancelled"
            raise
        except Exception as exc:
            error = exc
            outcome = "error"
            raise
        finally:
            if self.telemetry.active:
                elapsed_ms = _elapsed_ms(started_at)
                response = recorder.response
                self.telemetry.record_race(
                    RoutingRaceTelemetry(
                        race_id=uuid4().hex,
                        utterance_id=str(utterance_id),
                        started_at_utc=started_at_utc,
                        total_user_wait_ms=elapsed_ms,
                        succeeded=result is not None,
                        winner_attempt="main_attempt" if result is not None else None,
                        fallback_enabled=False,
                        first_fallback_triggered=False,
                        first_fallback_trigger_reason=None,
                        second_fallback_eligible=False,
                        second_fallback_triggered=False,
                        dual_bill_candidate=False,
                        attempts=(
                            RoutingAttemptTelemetry(
                                name="main_attempt",
                                requested_provider=self.requested_provider,
                                requested_models=self.requested_models,
                                started_offset_ms=0,
                                completed_offset_ms=elapsed_ms,
                                duration_ms=elapsed_ms,
                                outcome=outcome,
                                response_model=(response.model if response is not None else None),
                                response_provider=(
                                    response.provider if response is not None else None
                                ),
                                generation_id=(
                                    response.generation_id if response is not None else None
                                ),
                                prompt_tokens=(
                                    response.prompt_tokens if response is not None else None
                                ),
                                completion_tokens=(
                                    response.completion_tokens if response is not None else None
                                ),
                                total_tokens=(
                                    response.total_tokens if response is not None else None
                                ),
                                response_cost_usd=(
                                    response.cost_usd if response is not None else None
                                ),
                                error_type=type(error).__name__ if error is not None else None,
                            ),
                        ),
                    )
                )

    async def close(self) -> None:
        await self.inner.close()


def _elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.monotonic() - started_at) * 1000)))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
