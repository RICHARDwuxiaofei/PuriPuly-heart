from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from puripuly_heart.core.llm.attempt_metadata import (
    LLMAttemptMetadataRecorder,
    attempt_metadata_context,
)
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.llm.routing_telemetry import (
    RoutingAttemptTelemetry,
    RoutingRaceTelemetry,
    RoutingTelemetrySink,
)
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.models import Translation

_PERSISTED_FALLBACK_PREFIX = "[Persisted][Fallback] "


@dataclass(slots=True)
class _BranchOutcome:
    result: Translation | None = None
    error: Exception | None = None
    elapsed_ms: int | None = None
    started_offset_ms: int | None = None
    completed_offset_ms: int | None = None
    duration_ms: int | None = None
    cancelled: bool = False
    metadata: LLMAttemptMetadataRecorder = field(default_factory=LLMAttemptMetadataRecorder)

    @property
    def resolved(self) -> bool:
        return self.result is not None or self.error is not None


@dataclass(slots=True)
class FallbackRacingLLMProvider(LLMProvider):
    primary: LLMProvider
    fallback: LLMProvider
    second_fallback: LLMProvider | None = None
    fallback_timeout_ms: int = 1300
    second_fallback_timeout_ms: int = 4500
    loser_grace_ms: int = 50
    fallback_start_on_primary_error: bool = True
    runtime_logging: SessionRuntimeLoggingService | None = None
    routing_telemetry: RoutingTelemetrySink | None = None
    primary_requested_provider: str = "unknown"
    primary_requested_models: tuple[str, ...] = ()
    fallback_requested_provider: str = "unknown"
    fallback_requested_models: tuple[str, ...] = ()
    second_fallback_requested_provider: str = "unknown"
    second_fallback_requested_models: tuple[str, ...] = ()
    _inflight_tasks: set[asyncio.Task[object]] = field(
        init=False,
        default_factory=set,
        repr=False,
    )
    _state_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _close_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _providers_closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.fallback_timeout_ms = max(0, int(self.fallback_timeout_ms))
        self.second_fallback_timeout_ms = max(0, int(self.second_fallback_timeout_ms))
        self.loser_grace_ms = max(0, int(self.loser_grace_ms))

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
        params = {
            "utterance_id": utterance_id,
            "text": text,
            "system_prompt": system_prompt,
            "source_language": source_language,
            "target_language": target_language,
            "context": context,
        }
        race_id = uuid4().hex
        started_at = time.monotonic()
        started_at_utc = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        outcomes = {
            "main_attempt": _BranchOutcome(),
            "first_fallback": _BranchOutcome(),
            "second_fallback": _BranchOutcome(),
        }
        tasks: dict[str, asyncio.Task[object] | None] = {
            "main_attempt": None,
            "first_fallback": None,
            "second_fallback": None,
        }
        first_triggered = False
        first_trigger_reason: str | None = None
        second_triggered = False
        dual_bill_candidate = False
        winner: str | None = None
        winner_wait_ms: int | None = None
        telemetry_recorded = False

        tasks["main_attempt"] = await self._create_tracked_task(
            self._run_attempt(
                self.primary,
                params,
                outcomes["main_attempt"],
                race_started_at=started_at,
            )
        )
        first_timeout_task = await self._create_tracked_task(
            self._wait_for_deadline(started_at, self.fallback_timeout_ms)
        )
        second_timeout_task = (
            await self._create_tracked_task(
                self._wait_for_deadline(started_at, self.second_fallback_timeout_ms)
            )
            if self.second_fallback is not None
            else None
        )

        try:
            while winner is None:
                waiters = {
                    task
                    for name, task in tasks.items()
                    if task is not None and not outcomes[name].resolved
                }
                if first_timeout_task is not None:
                    waiters.add(first_timeout_task)
                if second_timeout_task is not None:
                    waiters.add(second_timeout_task)
                if not waiters:
                    break

                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

                for name in ("main_attempt", "first_fallback", "second_fallback"):
                    task = tasks[name]
                    outcome = outcomes[name]
                    if task is not None and task.done() and not outcome.resolved:
                        await self._capture_outcome(
                            task=task,
                            outcome=outcome,
                            started_at=started_at,
                        )
                        if outcome.result is not None and winner is None:
                            winner = name
                            winner_wait_ms = outcome.elapsed_ms

                if winner is not None:
                    break

                main_outcome = outcomes["main_attempt"]
                if (
                    main_outcome.error is not None
                    and not first_triggered
                    and self.fallback_start_on_primary_error
                ):
                    first_triggered = True
                    first_trigger_reason = "primary_error"
                    await self._cancel_task(first_timeout_task)
                    first_timeout_task = None
                    tasks["first_fallback"] = await self._create_tracked_task(
                        self._run_attempt(
                            self.fallback,
                            params,
                            outcomes["first_fallback"],
                            race_started_at=started_at,
                        )
                    )
                    await asyncio.sleep(0)
                    self._emit_race_event(
                        race_id=race_id,
                        utterance_id=utterance_id,
                        event="fallback_triggered",
                        trigger_reason="primary_error",
                        outcomes=outcomes,
                        first_triggered=first_triggered,
                        second_triggered=second_triggered,
                        winner=None,
                        winner_wait_ms=None,
                        dual_bill_candidate=dual_bill_candidate,
                    )

                if first_timeout_task in done:
                    first_timeout_task = None
                    if not first_triggered:
                        first_triggered = True
                        first_trigger_reason = "timeout"
                        dual_bill_candidate = not main_outcome.resolved
                        self._emit_basic_fallback_triggered()
                        tasks["first_fallback"] = await self._create_tracked_task(
                            self._run_attempt(
                                self.fallback,
                                params,
                                outcomes["first_fallback"],
                                race_started_at=started_at,
                            )
                        )
                        await asyncio.sleep(0)
                        if outcomes["main_attempt"].elapsed_ms is None:
                            outcomes["main_attempt"].elapsed_ms = self._elapsed_ms(started_at)
                        self._emit_race_event(
                            race_id=race_id,
                            utterance_id=utterance_id,
                            event="fallback_triggered",
                            trigger_reason="timeout",
                            outcomes=outcomes,
                            first_triggered=first_triggered,
                            second_triggered=second_triggered,
                            winner=None,
                            winner_wait_ms=None,
                            dual_bill_candidate=dual_bill_candidate,
                        )

                if second_timeout_task in done:
                    second_timeout_task = None
                    if not second_triggered and self.second_fallback is not None:
                        second_triggered = True
                        tasks["second_fallback"] = await self._create_tracked_task(
                            self._run_attempt(
                                self.second_fallback,
                                params,
                                outcomes["second_fallback"],
                                race_started_at=started_at,
                            )
                        )
                        await asyncio.sleep(0)
                        self._emit_race_event(
                            race_id=race_id,
                            utterance_id=utterance_id,
                            event="second_fallback_triggered",
                            trigger_reason="timeout",
                            outcomes=outcomes,
                            first_triggered=first_triggered,
                            second_triggered=second_triggered,
                            winner=None,
                            winner_wait_ms=None,
                            dual_bill_candidate=dual_bill_candidate,
                        )

            if winner is None:
                error_labels = {
                    "main_attempt": "primary",
                    "first_fallback": "fallback",
                    "second_fallback": "second fallback",
                }
                errors = [
                    f"{error_labels[name]} failed: {self._format_error(outcome.error)}"
                    for name, outcome in outcomes.items()
                    if outcome.error is not None
                ]
                combined_message = "; ".join(errors) or "fallback race ended without a result"
                self._emit_event(
                    race_id=race_id,
                    utterance_id=utterance_id,
                    event="race_failed",
                    primary_elapsed_ms=outcomes["main_attempt"].elapsed_ms,
                    fallback_elapsed_ms=outcomes["first_fallback"].elapsed_ms,
                    second_fallback_elapsed_ms=outcomes["second_fallback"].elapsed_ms,
                    fallback_triggered=first_triggered,
                    second_fallback_triggered=second_triggered,
                    winner=None,
                    winner_attempt=None,
                    returned_source=None,
                    total_user_wait_ms=self._elapsed_ms(started_at),
                    primary_error=self._format_error(outcomes["main_attempt"].error),
                    fallback_error=self._format_error(outcomes["first_fallback"].error),
                    second_fallback_error=self._format_error(outcomes["second_fallback"].error),
                    fallback_unusable=False,
                    dual_bill_candidate=dual_bill_candidate,
                    outcomes=outcomes,
                )
                self._record_routing_telemetry(
                    race_id=race_id,
                    utterance_id=utterance_id,
                    started_at_utc=started_at_utc,
                    total_user_wait_ms=self._elapsed_ms(started_at),
                    winner=None,
                    first_triggered=first_triggered,
                    first_trigger_reason=first_trigger_reason,
                    second_triggered=second_triggered,
                    dual_bill_candidate=dual_bill_candidate,
                    outcomes=outcomes,
                )
                telemetry_recorded = True
                raise RuntimeError(combined_message)

            await self._allow_loser_grace_all(
                started_at=started_at,
                tasks=tasks,
                outcomes=outcomes,
            )

            returned = outcomes[winner].result
            if returned is None:
                raise RuntimeError("fallback race winner did not produce a translation")

            legacy_winner = "primary" if winner == "main_attempt" else "fallback"
            fallback_unusable = (
                winner == "main_attempt" and outcomes["first_fallback"].error is not None
            )
            event = "fallback_unusable" if fallback_unusable else "race_finished"
            self._emit_event(
                race_id=race_id,
                utterance_id=utterance_id,
                event=(
                    "primary_completed" if not first_triggered and not second_triggered else event
                ),
                primary_elapsed_ms=outcomes["main_attempt"].elapsed_ms,
                fallback_elapsed_ms=outcomes["first_fallback"].elapsed_ms,
                second_fallback_elapsed_ms=outcomes["second_fallback"].elapsed_ms,
                fallback_triggered=first_triggered,
                second_fallback_triggered=second_triggered,
                winner=legacy_winner,
                winner_attempt=winner,
                returned_source=legacy_winner,
                total_user_wait_ms=winner_wait_ms,
                primary_error=self._format_error(outcomes["main_attempt"].error),
                fallback_error=self._format_error(outcomes["first_fallback"].error),
                second_fallback_error=self._format_error(outcomes["second_fallback"].error),
                fallback_unusable=fallback_unusable,
                dual_bill_candidate=dual_bill_candidate,
                outcomes=outcomes,
            )
            self._record_routing_telemetry(
                race_id=race_id,
                utterance_id=utterance_id,
                started_at_utc=started_at_utc,
                total_user_wait_ms=(
                    winner_wait_ms if winner_wait_ms is not None else self._elapsed_ms(started_at)
                ),
                winner=winner,
                first_triggered=first_triggered,
                first_trigger_reason=first_trigger_reason,
                second_triggered=second_triggered,
                dual_bill_candidate=dual_bill_candidate,
                outcomes=outcomes,
            )
            telemetry_recorded = True
            return returned
        finally:
            await self._cancel_task(first_timeout_task)
            await self._cancel_task(second_timeout_task)
            for task in tasks.values():
                await self._cancel_task(task)
            if not telemetry_recorded:
                self._record_routing_telemetry(
                    race_id=race_id,
                    utterance_id=utterance_id,
                    started_at_utc=started_at_utc,
                    total_user_wait_ms=self._elapsed_ms(started_at),
                    winner=winner,
                    first_triggered=first_triggered,
                    first_trigger_reason=first_trigger_reason,
                    second_triggered=second_triggered,
                    dual_bill_candidate=dual_bill_candidate,
                    outcomes=outcomes,
                )

    async def close(self) -> None:
        async with self._close_lock:
            async with self._state_lock:
                self._closed = True
                inflight_tasks = list(self._inflight_tasks)
            for task in inflight_tasks:
                task.cancel()
            if inflight_tasks:
                await asyncio.gather(*inflight_tasks, return_exceptions=True)
            if self._providers_closed:
                return
            await self.primary.close()
            if self.fallback is not self.primary:
                await self.fallback.close()
            if (
                self.second_fallback is not None
                and self.second_fallback is not self.primary
                and self.second_fallback is not self.fallback
            ):
                await self.second_fallback.close()
            self._providers_closed = True

    async def _run_attempt(
        self,
        provider: LLMProvider,
        params: dict[str, object],
        outcome: _BranchOutcome,
        *,
        race_started_at: float,
    ) -> Translation:
        outcome.started_offset_ms = self._elapsed_ms(race_started_at)
        try:
            with attempt_metadata_context(outcome.metadata):
                return await provider.translate(**params)
        except asyncio.CancelledError:
            outcome.cancelled = True
            raise
        finally:
            outcome.completed_offset_ms = self._elapsed_ms(race_started_at)
            outcome.duration_ms = max(
                0,
                outcome.completed_offset_ms - outcome.started_offset_ms,
            )

    @staticmethod
    async def _wait_for_deadline(started_at: float, delay_ms: int) -> None:
        deadline = started_at + delay_ms / 1000.0
        while True:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                return
            await asyncio.sleep(remaining_s)

    async def _allow_loser_grace_all(
        self,
        *,
        started_at: float,
        tasks: dict[str, asyncio.Task[object] | None],
        outcomes: dict[str, _BranchOutcome],
    ) -> None:
        pending = [
            task for name, task in tasks.items() if task is not None and not outcomes[name].resolved
        ]
        if not pending:
            return

        if self.loser_grace_ms > 0:
            done, _ = await asyncio.wait(pending, timeout=self.loser_grace_ms / 1000.0)
            for name, task in tasks.items():
                if task in done and not outcomes[name].resolved:
                    await self._capture_outcome(
                        task=task,
                        outcome=outcomes[name],
                        started_at=started_at,
                    )

        for name, task in tasks.items():
            if task is None or outcomes[name].resolved:
                continue
            outcomes[name].elapsed_ms = outcomes[name].elapsed_ms or self._elapsed_ms(started_at)
            outcomes[name].cancelled = True
            await self._cancel_task(task)

    async def _capture_outcome(
        self,
        *,
        task: asyncio.Task[object],
        outcome: _BranchOutcome,
        started_at: float,
    ) -> None:
        if outcome.resolved:
            return
        if task.cancelled():
            raise asyncio.CancelledError
        exc = task.exception()
        outcome.elapsed_ms = self._elapsed_ms(started_at)
        outcome.completed_offset_ms = outcome.completed_offset_ms or outcome.elapsed_ms
        if exc is None:
            outcome.result = task.result()
            return
        outcome.error = exc

    async def _create_tracked_task(self, awaitable: Awaitable[object]) -> asyncio.Task[object]:
        task = asyncio.create_task(awaitable)
        async with self._state_lock:
            if self._closed:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                raise asyncio.CancelledError
            self._inflight_tasks.add(task)
        task.add_done_callback(self._inflight_tasks.discard)
        return task

    async def _cancel_task(self, task: asyncio.Task[object] | None) -> None:
        if task is None:
            return
        if task.done():
            self._consume_task_result(task)
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    def _emit_basic_fallback_triggered(self) -> None:
        if self.runtime_logging is None:
            return
        emit = getattr(self.runtime_logging, "emit_basic", None)
        if not callable(emit):
            return
        with contextlib.suppress(Exception):
            emit(f"Fallback triggered after {self.fallback_timeout_ms} ms", level=logging.INFO)

    def _record_routing_telemetry(
        self,
        *,
        race_id: str,
        utterance_id: UUID,
        started_at_utc: str,
        total_user_wait_ms: int,
        winner: str | None,
        first_triggered: bool,
        first_trigger_reason: str | None,
        second_triggered: bool,
        dual_bill_candidate: bool,
        outcomes: dict[str, _BranchOutcome],
    ) -> None:
        telemetry = self.routing_telemetry
        if telemetry is None or not telemetry.active:
            return
        requested = {
            "main_attempt": (
                self.primary_requested_provider,
                self.primary_requested_models,
            ),
            "first_fallback": (
                self.fallback_requested_provider,
                self.fallback_requested_models,
            ),
            "second_fallback": (
                self.second_fallback_requested_provider,
                self.second_fallback_requested_models,
            ),
        }
        attempts: list[RoutingAttemptTelemetry] = []
        for name in ("main_attempt", "first_fallback", "second_fallback"):
            outcome = outcomes[name]
            provider_name, models = requested[name]
            response = outcome.metadata.response
            if outcome.result is not None:
                status = "success"
            elif outcome.error is not None:
                status = "error"
            elif outcome.cancelled:
                status = "cancelled"
            elif outcome.started_offset_ms is not None:
                status = "incomplete"
            else:
                status = "not_started"
            attempts.append(
                RoutingAttemptTelemetry(
                    name=name,
                    requested_provider=provider_name,
                    requested_models=models,
                    started_offset_ms=outcome.started_offset_ms,
                    completed_offset_ms=outcome.completed_offset_ms,
                    duration_ms=outcome.duration_ms,
                    outcome=status,
                    response_model=response.model if response is not None else None,
                    response_provider=response.provider if response is not None else None,
                    generation_id=response.generation_id if response is not None else None,
                    prompt_tokens=response.prompt_tokens if response is not None else None,
                    completion_tokens=(
                        response.completion_tokens if response is not None else None
                    ),
                    total_tokens=response.total_tokens if response is not None else None,
                    response_cost_usd=response.cost_usd if response is not None else None,
                    error_type=(
                        type(outcome.error).__name__ if outcome.error is not None else None
                    ),
                )
            )
        telemetry.record_race(
            RoutingRaceTelemetry(
                race_id=race_id,
                utterance_id=str(utterance_id),
                started_at_utc=started_at_utc,
                total_user_wait_ms=total_user_wait_ms,
                succeeded=winner is not None,
                winner_attempt=winner,
                fallback_enabled=True,
                first_fallback_triggered=first_triggered,
                first_fallback_trigger_reason=first_trigger_reason,
                second_fallback_eligible=self.second_fallback is not None,
                second_fallback_triggered=second_triggered,
                dual_bill_candidate=dual_bill_candidate,
                attempts=tuple(attempts),
            )
        )

    def _emit_race_event(
        self,
        *,
        race_id: str,
        utterance_id: UUID,
        event: str,
        trigger_reason: str,
        outcomes: dict[str, _BranchOutcome],
        first_triggered: bool,
        second_triggered: bool,
        winner: str | None,
        winner_wait_ms: int | None,
        dual_bill_candidate: bool,
    ) -> None:
        legacy_winner = None
        if winner is not None:
            legacy_winner = "primary" if winner == "main_attempt" else "fallback"
        self._emit_event(
            race_id=race_id,
            utterance_id=utterance_id,
            event=event,
            primary_elapsed_ms=outcomes["main_attempt"].elapsed_ms,
            fallback_elapsed_ms=outcomes["first_fallback"].elapsed_ms,
            second_fallback_elapsed_ms=outcomes["second_fallback"].elapsed_ms,
            fallback_triggered=first_triggered,
            second_fallback_triggered=second_triggered,
            winner=legacy_winner,
            winner_attempt=winner,
            returned_source=legacy_winner,
            total_user_wait_ms=winner_wait_ms,
            primary_error=self._format_error(outcomes["main_attempt"].error),
            fallback_error=self._format_error(outcomes["first_fallback"].error),
            second_fallback_error=self._format_error(outcomes["second_fallback"].error),
            fallback_unusable=False,
            dual_bill_candidate=dual_bill_candidate,
            outcomes=outcomes,
            trigger_reason=trigger_reason,
        )

    def _emit_event(
        self,
        *,
        race_id: str,
        utterance_id: UUID,
        event: str,
        primary_elapsed_ms: int | None,
        fallback_elapsed_ms: int | None,
        second_fallback_elapsed_ms: int | None = None,
        fallback_triggered: bool,
        second_fallback_triggered: bool = False,
        winner: str | None,
        winner_attempt: str | None = None,
        returned_source: str | None,
        total_user_wait_ms: int | None,
        primary_error: str | None,
        fallback_error: str | None,
        second_fallback_error: str | None = None,
        fallback_unusable: bool,
        dual_bill_candidate: bool,
        outcomes: dict[str, _BranchOutcome] | None = None,
        trigger_reason: str | None = None,
    ) -> None:
        if self.runtime_logging is None:
            return
        emit = getattr(self.runtime_logging, "emit_persisted", None)
        if not callable(emit):
            return

        if outcomes is None:
            outcomes = {
                "main_attempt": _BranchOutcome(),
                "first_fallback": _BranchOutcome(),
                "second_fallback": _BranchOutcome(),
            }
        if winner_attempt is None:
            winner_attempt = {
                "primary": "main_attempt",
                "fallback": "first_fallback",
            }.get(winner)
        primary_model, primary_credential_source = self._provider_identity(self.primary)
        fallback_model, fallback_credential_source = self._provider_identity(self.fallback)
        second_fallback_model, second_fallback_credential_source = self._provider_identity(
            self.second_fallback
        )
        payload = {
            "race_id": race_id,
            "utterance_id": str(utterance_id),
            "event": event,
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "second_fallback_model": second_fallback_model,
            "primary_credential_source": primary_credential_source,
            "fallback_credential_source": fallback_credential_source,
            "second_fallback_credential_source": second_fallback_credential_source,
            "primary_elapsed_ms": primary_elapsed_ms,
            "fallback_elapsed_ms": fallback_elapsed_ms,
            "second_fallback_elapsed_ms": second_fallback_elapsed_ms,
            "fallback_triggered": fallback_triggered,
            "second_fallback_triggered": second_fallback_triggered,
            "winner": winner,
            "winner_attempt": winner_attempt,
            "returned_source": returned_source,
            "total_user_wait_ms": total_user_wait_ms,
            "primary_error": primary_error,
            "fallback_error": fallback_error,
            "second_fallback_error": second_fallback_error,
            "fallback_unusable": fallback_unusable,
            "dual_bill_candidate": dual_bill_candidate,
            "main_attempt_response_model": self._response_model(outcomes["main_attempt"]),
            "main_attempt_response_provider": self._response_provider(outcomes["main_attempt"]),
            "first_fallback_response_model": self._response_model(outcomes["first_fallback"]),
            "first_fallback_response_provider": self._response_provider(outcomes["first_fallback"]),
            "second_fallback_response_model": self._response_model(outcomes["second_fallback"]),
            "second_fallback_response_provider": self._response_provider(
                outcomes["second_fallback"]
            ),
        }
        if trigger_reason is not None:
            payload["trigger_reason"] = trigger_reason

        with contextlib.suppress(Exception):
            emit(
                _PERSISTED_FALLBACK_PREFIX
                + json.dumps(payload, ensure_ascii=False, sort_keys=True),
                level=logging.INFO,
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int(round((time.monotonic() - started_at) * 1000)))

    @classmethod
    def _provider_identity(cls, provider: object) -> tuple[str | None, str | None]:
        seen: set[int] = set()
        pending: list[object] = [provider]
        resolved_model: str | None = None
        resolved_source: str | None = None
        saw_openrouter = False

        while pending:
            current = pending.pop(0)
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))

            node_model, node_source, node_is_openrouter = cls._identity_from_node(current)
            if resolved_model is None and node_model is not None:
                resolved_model = node_model
            if resolved_source is None and node_source is not None:
                resolved_source = node_source
            saw_openrouter = saw_openrouter or node_is_openrouter

            if resolved_model is not None and resolved_source is not None:
                return resolved_model, resolved_source

            for attr_name in ("_delegate", "inner"):
                wrapped = getattr(current, attr_name, None)
                if wrapped is not None and id(wrapped) not in seen:
                    pending.append(wrapped)

        if resolved_source is None and saw_openrouter:
            resolved_source = "openrouter"
        return resolved_model, resolved_source

    @classmethod
    def _identity_from_node(cls, provider: object) -> tuple[str | None, str | None, bool]:
        direct_model = cls._stringify_metadata(getattr(provider, "model", None))
        direct_source = cls._stringify_metadata(
            getattr(
                provider,
                "selected_source",
                getattr(provider, "credential_source", None),
            )
        )
        release_service = getattr(provider, "release_service", None)
        release_model = cls._stringify_metadata(getattr(release_service, "model", None))
        release_source = cls._stringify_metadata(
            getattr(
                release_service,
                "selected_source",
                getattr(release_service, "credential_source", None),
            )
        )

        model = direct_model or release_model
        source = direct_source or release_source
        is_openrouter = cls._is_openrouter_provider(provider) or any(
            value is not None for value in (release_model, release_source)
        )
        return model, source, is_openrouter

    @staticmethod
    def _is_openrouter_provider(provider: object) -> bool:
        provider_type = type(provider)
        haystack = f"{provider_type.__module__}.{provider_type.__name__}".lower()
        return "openrouter" in haystack

    @staticmethod
    def _stringify_metadata(value: object | None) -> str | None:
        if value is None:
            return None
        raw = getattr(value, "value", value)
        return str(raw)

    @staticmethod
    def _response_model(outcome: _BranchOutcome) -> str | None:
        response = outcome.metadata.response
        return response.model if response is not None else None

    @staticmethod
    def _response_provider(outcome: _BranchOutcome) -> str | None:
        response = outcome.metadata.response
        return response.provider if response is not None else None

    @staticmethod
    def _format_error(error: Exception | None) -> str | None:
        if error is None:
            return None
        message = str(error)
        if not message:
            return type(error).__name__
        return f"{type(error).__name__}: {message}"

    @staticmethod
    def _consume_task_result(task: asyncio.Task[object]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()
