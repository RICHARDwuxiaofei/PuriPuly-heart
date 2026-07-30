from __future__ import annotations

import asyncio
import json
import math
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from puripuly_heart import __version__
from puripuly_heart.core.lifecycle import LifecycleScope, start_lifecycle_task
from puripuly_heart.core.llm.routing_telemetry import (
    OpenRouterGenerationTelemetry,
    RoutingAttemptTelemetry,
    RoutingRaceTelemetry,
)

ROUTING_TELEMETRY_SCHEMA_VERSION = 1
ROUTING_TELEMETRY_DIRNAME = "puripuly-heart-routing-telemetry"
ROUTING_TELEMETRY_FILENAME = "routing-telemetry.jsonl"


class JsonlRoutingTelemetrySession:
    def __init__(self, *, root_dir: Path | None = None) -> None:
        self._root_dir = root_dir or Path(tempfile.gettempdir()) / ROUTING_TELEMETRY_DIRNAME
        self._capture_id: str | None = None
        self._capture_started_at: str | None = None
        self._capture_file: Path | None = None
        self._last_capture_file: Path | None = None
        self._queue: asyncio.Queue[dict[str, object] | None] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._writer_scope: LifecycleScope | None = None
        self._races: list[RoutingRaceTelemetry] = []
        self._generations: dict[str, OpenRouterGenerationTelemetry] = {}
        self._generation_failures: dict[str, str] = {}
        self._pending_generations: set[str] = set()
        self._pending_generations_settled = asyncio.Event()
        self._pending_generations_settled.set()
        self._accepting_races = False
        self._lifecycle_lock = asyncio.Lock()
        self._writer_errors: list[str] = []

    @property
    def active(self) -> bool:
        return self._accepting_races and self._capture_id is not None

    @property
    def capture_folder(self) -> Path:
        return self._root_dir

    @property
    def last_capture_file(self) -> Path | None:
        return self._last_capture_file

    async def start(self) -> Path:
        async with self._lifecycle_lock:
            if self._capture_id is not None:
                if self._capture_file is None:
                    raise RuntimeError("routing telemetry capture has no output file")
                return self._capture_file
            await asyncio.to_thread(self._root_dir.mkdir, parents=True, exist_ok=True)
            started = datetime.now(UTC)
            capture_id = uuid4().hex
            self._capture_id = capture_id
            self._capture_started_at = _format_utc(started)
            self._capture_file = self._root_dir / ROUTING_TELEMETRY_FILENAME
            self._last_capture_file = self._capture_file
            self._queue = asyncio.Queue()
            self._races = []
            self._generations = {}
            self._generation_failures = {}
            self._pending_generations = set()
            self._pending_generations_settled.set()
            self._writer_errors = []
            self._writer_scope = LifecycleScope(f"routing-telemetry-writer-{capture_id[:8]}")
            self._writer_task = start_lifecycle_task(
                self._writer_scope,
                self._run_writer(self._capture_file, self._queue),
                name="jsonl-writer",
            )
            self._accepting_races = True
            self._enqueue(
                {
                    "type": "session_start",
                    "schema_version": ROUTING_TELEMETRY_SCHEMA_VERSION,
                    "capture_id": capture_id,
                    "started_at_utc": self._capture_started_at,
                    "app_version": __version__,
                    "percentile_method": "nearest_rank",
                }
            )
            return self._capture_file

    async def stop(self, *, enrichment_grace_s: float = 3.0) -> Path | None:
        async with self._lifecycle_lock:
            if self._capture_id is None:
                return self._last_capture_file
            self._accepting_races = False
            if self._pending_generations:
                try:
                    await asyncio.wait_for(
                        self._pending_generations_settled.wait(),
                        timeout=max(0.0, enrichment_grace_s),
                    )
                except TimeoutError:
                    pass
            capture_file = self._capture_file
            self._enqueue(self._build_summary())
            queue = self._queue
            writer_task = self._writer_task
            if queue is not None:
                queue.put_nowait(None)
            if writer_task is not None:
                await writer_task
            writer_scope = self._writer_scope
            if writer_scope is not None:
                await writer_scope.close()
            if capture_file is not None:
                file_summary = await asyncio.to_thread(
                    _build_file_summary,
                    capture_file,
                )
                await asyncio.to_thread(
                    _append_json_line,
                    capture_file,
                    file_summary,
                )
            self._capture_id = None
            self._capture_started_at = None
            self._capture_file = None
            self._queue = None
            self._writer_task = None
            self._writer_scope = None
            self._pending_generations.clear()
            self._pending_generations_settled.set()
            return capture_file

    async def close(self) -> None:
        await self.stop()

    def record_race(self, record: RoutingRaceTelemetry) -> None:
        if not self.active:
            return
        self._races.append(record)
        self._enqueue(
            {
                "type": "race",
                "schema_version": ROUTING_TELEMETRY_SCHEMA_VERSION,
                "capture_id": self._capture_id,
                **asdict(record),
            }
        )

    def register_openrouter_generation(self, generation_id: str) -> str | None:
        if not self.active or self._capture_id is None or not generation_id:
            return None
        self._pending_generations.add(generation_id)
        self._pending_generations_settled.clear()
        return self._capture_id

    def record_openrouter_generation(
        self,
        capture_id: str,
        record: OpenRouterGenerationTelemetry,
    ) -> None:
        if capture_id != self._capture_id:
            return
        self._generations[record.generation_id] = record
        self._generation_failures.pop(record.generation_id, None)
        self._settle_generation(record.generation_id)
        self._enqueue(
            {
                "type": "openrouter_generation",
                "schema_version": ROUTING_TELEMETRY_SCHEMA_VERSION,
                "capture_id": capture_id,
                "retrieved_at_utc": _format_utc(datetime.now(UTC)),
                **asdict(record),
            }
        )

    def record_openrouter_generation_failure(
        self,
        capture_id: str,
        generation_id: str,
        error_type: str,
    ) -> None:
        if capture_id != self._capture_id:
            return
        self._generation_failures[generation_id] = error_type
        self._settle_generation(generation_id)
        self._enqueue(
            {
                "type": "openrouter_generation_failure",
                "schema_version": ROUTING_TELEMETRY_SCHEMA_VERSION,
                "capture_id": capture_id,
                "generation_id": generation_id,
                "error_type": error_type,
            }
        )

    def _settle_generation(self, generation_id: str) -> None:
        self._pending_generations.discard(generation_id)
        if not self._pending_generations:
            self._pending_generations_settled.set()

    def _enqueue(self, payload: dict[str, object]) -> None:
        queue = self._queue
        if queue is None:
            return
        queue.put_nowait(payload)

    async def _run_writer(
        self,
        path: Path,
        queue: asyncio.Queue[dict[str, object] | None],
    ) -> None:
        while True:
            payload = await queue.get()
            try:
                if payload is None:
                    return
                await asyncio.to_thread(_append_json_line, path, payload)
            except Exception as exc:
                self._writer_errors.append(type(exc).__name__)
            finally:
                queue.task_done()

    def _build_summary(self) -> dict[str, object]:
        return self._build_summary_for(
            record_type="session_summary",
            capture_id=self._capture_id,
            started_at_utc=self._capture_started_at,
            races=self._races,
            generations=self._generations,
            generation_failures=self._generation_failures,
            pending_generation_count=len(self._pending_generations),
            writer_errors=self._writer_errors,
        )

    @staticmethod
    def _build_summary_for(
        *,
        record_type: str,
        capture_id: str | None,
        started_at_utc: str | None,
        races: list[RoutingRaceTelemetry],
        generations: dict[str, OpenRouterGenerationTelemetry],
        generation_failures: dict[str, str],
        pending_generation_count: int,
        writer_errors: list[str],
    ) -> dict[str, object]:
        successful = [race for race in races if race.succeeded]
        latency_values = [race.total_user_wait_ms for race in races]
        successful_latency_values = [race.total_user_wait_ms for race in successful]
        fallback_enabled = [race for race in races if race.fallback_enabled]
        second_eligible = [race for race in races if race.second_fallback_eligible]
        first_triggered = [race for race in fallback_enabled if race.first_fallback_triggered]
        second_triggered = [race for race in second_eligible if race.second_fallback_triggered]
        dual_bill_candidates = [race for race in fallback_enabled if race.dual_bill_candidate]
        winner_counts = Counter(
            race.winner_attempt for race in successful if race.winner_attempt is not None
        )
        attempt_outcomes = Counter(attempt.outcome for race in races for attempt in race.attempts)
        cost = JsonlRoutingTelemetrySession._cost_summary(races, generations)
        return {
            "type": record_type,
            "schema_version": ROUTING_TELEMETRY_SCHEMA_VERSION,
            "capture_id": capture_id,
            "started_at_utc": started_at_utc,
            "ended_at_utc": _format_utc(datetime.now(UTC)),
            "races": {
                "total": len(races),
                "successful": len(successful),
                "failed": len(races) - len(successful),
                "success_rate": _rate(len(successful), len(races)),
            },
            "latency_ms": _percentile_summary(latency_values),
            "successful_latency_ms": _percentile_summary(successful_latency_values),
            "fallback": {
                "enabled_races": len(fallback_enabled),
                "first_triggered": len(first_triggered),
                "first_trigger_rate": _rate(len(first_triggered), len(fallback_enabled)),
                "first_rescue_rate": _rate(
                    winner_counts["first_fallback"],
                    len(first_triggered),
                ),
                "second_eligible_races": len(second_eligible),
                "second_triggered": len(second_triggered),
                "second_trigger_rate": _rate(
                    len(second_triggered),
                    len(second_eligible),
                ),
                "second_rescue_rate": _rate(
                    winner_counts["second_fallback"],
                    len(second_triggered),
                ),
                "dual_bill_candidates": len(dual_bill_candidates),
                "dual_bill_candidate_rate": _rate(
                    len(dual_bill_candidates),
                    len(fallback_enabled),
                ),
                "trigger_reasons": dict(
                    Counter(
                        race.first_fallback_trigger_reason
                        for race in first_triggered
                        if race.first_fallback_trigger_reason is not None
                    )
                ),
            },
            "winners": {
                "counts": dict(winner_counts),
                "rates": {
                    name: _rate(count, len(successful))
                    for name, count in sorted(winner_counts.items())
                },
            },
            "attempt_outcomes": dict(attempt_outcomes),
            "cost": cost,
            "tokens": JsonlRoutingTelemetrySession._token_summary(races, generations),
            "openrouter_generation_lookup": {
                "confirmed": len(generations),
                "failed": len(generation_failures),
                "pending_at_stop": pending_generation_count,
                "failure_types": dict(Counter(generation_failures.values())),
            },
            "writer_errors": list(writer_errors),
        }

    @staticmethod
    def _cost_summary(
        races: list[RoutingRaceTelemetry],
        generations: dict[str, OpenRouterGenerationTelemetry],
    ) -> dict[str, object]:
        known_total = Decimal("0")
        known_winner = Decimal("0")
        known_loser = Decimal("0")
        known_attempts = 0
        eligible_attempts = 0
        per_race_costs: list[int] = []
        for race in races:
            race_cost = Decimal("0")
            race_has_cost = False
            for attempt in race.attempts:
                if attempt.started_offset_ms is None or attempt.requested_provider != "openrouter":
                    continue
                eligible_attempts += 1
                attempt_cost = JsonlRoutingTelemetrySession._attempt_cost(
                    attempt,
                    generations,
                )
                if attempt_cost is None:
                    continue
                known_attempts += 1
                known_total += attempt_cost
                race_cost += attempt_cost
                race_has_cost = True
                if attempt.name == race.winner_attempt:
                    known_winner += attempt_cost
                else:
                    known_loser += attempt_cost
            if race_has_cost:
                per_race_costs.append(int((race_cost * Decimal("1000000000")).to_integral_value()))
        return {
            "currency": "USD",
            "known_total_usd": _decimal_text(known_total),
            "known_winner_usd": _decimal_text(known_winner),
            "known_loser_hedge_usd": _decimal_text(known_loser),
            "known_attempts": known_attempts,
            "eligible_openrouter_attempts": eligible_attempts,
            "coverage_rate": _rate(known_attempts, eligible_attempts),
            "unknown_attempts": max(0, eligible_attempts - known_attempts),
            "per_race_nanousd": _percentile_summary(per_race_costs),
        }

    @staticmethod
    def _token_summary(
        races: list[RoutingRaceTelemetry],
        generations: dict[str, OpenRouterGenerationTelemetry],
    ) -> dict[str, object]:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        winner_total_tokens = 0
        loser_total_tokens = 0
        known_attempts = 0
        eligible_attempts = 0
        for race in races:
            for attempt in race.attempts:
                if attempt.started_offset_ms is None or attempt.requested_provider != "openrouter":
                    continue
                eligible_attempts += 1
                attempt_tokens = JsonlRoutingTelemetrySession._attempt_tokens(
                    attempt,
                    generations,
                )
                if attempt_tokens is None:
                    continue
                known_attempts += 1
                attempt_prompt, attempt_completion, attempt_total = attempt_tokens
                prompt_tokens += attempt_prompt
                completion_tokens += attempt_completion
                total_tokens += attempt_total
                if attempt.name == race.winner_attempt:
                    winner_total_tokens += attempt_total
                else:
                    loser_total_tokens += attempt_total
        return {
            "known_attempts": known_attempts,
            "eligible_openrouter_attempts": eligible_attempts,
            "coverage_rate": _rate(known_attempts, eligible_attempts),
            "unknown_attempts": max(0, eligible_attempts - known_attempts),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "winner_total_tokens": winner_total_tokens,
            "loser_hedge_total_tokens": loser_total_tokens,
        }

    @staticmethod
    def _attempt_cost(
        attempt: RoutingAttemptTelemetry,
        generations: dict[str, OpenRouterGenerationTelemetry],
    ) -> Decimal | None:
        if attempt.generation_id is not None:
            generation = generations.get(attempt.generation_id)
            if generation is not None:
                parsed = _decimal_or_none(generation.total_cost_usd)
                if parsed is not None:
                    return parsed
        return _decimal_or_none(attempt.response_cost_usd)

    @staticmethod
    def _attempt_tokens(
        attempt: RoutingAttemptTelemetry,
        generations: dict[str, OpenRouterGenerationTelemetry],
    ) -> tuple[int, int, int] | None:
        prompt_tokens = attempt.prompt_tokens
        completion_tokens = attempt.completion_tokens
        if attempt.generation_id is not None:
            generation = generations.get(attempt.generation_id)
            if generation is not None:
                if generation.prompt_tokens is not None:
                    prompt_tokens = generation.prompt_tokens
                if generation.completion_tokens is not None:
                    completion_tokens = generation.completion_tokens
        if prompt_tokens is None or completion_tokens is None:
            return None
        total_tokens = attempt.total_tokens
        if total_tokens is None or attempt.generation_id in generations:
            total_tokens = prompt_tokens + completion_tokens
        return prompt_tokens, completion_tokens, total_tokens


def _build_file_summary(path: Path) -> dict[str, object]:
    races: list[RoutingRaceTelemetry] = []
    generations: dict[str, OpenRouterGenerationTelemetry] = {}
    generation_failures: dict[str, str] = {}
    writer_errors: list[str] = []
    captures = 0
    invalid_records = 0
    started_at_utc: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    invalid_records += 1
                    continue
                record_type = payload.get("type")
                if record_type == "session_start":
                    captures += 1
                    if started_at_utc is None and isinstance(
                        payload.get("started_at_utc"),
                        str,
                    ):
                        started_at_utc = payload["started_at_utc"]
                elif record_type == "race":
                    races.append(_race_from_payload(payload))
                elif record_type == "openrouter_generation":
                    generation = _generation_from_payload(payload)
                    generations[generation.generation_id] = generation
                    generation_failures.pop(generation.generation_id, None)
                elif record_type == "openrouter_generation_failure":
                    generation_id = payload.get("generation_id")
                    error_type = payload.get("error_type")
                    if isinstance(generation_id, str) and isinstance(error_type, str):
                        generation_failures[generation_id] = error_type
                elif record_type == "session_summary":
                    errors = payload.get("writer_errors")
                    if isinstance(errors, list):
                        writer_errors.extend(error for error in errors if isinstance(error, str))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                invalid_records += 1
    generation_ids = {
        attempt.generation_id
        for race in races
        for attempt in race.attempts
        if attempt.generation_id is not None
    }
    pending_generation_count = len(generation_ids - generations.keys() - generation_failures.keys())
    summary = JsonlRoutingTelemetrySession._build_summary_for(
        record_type="file_summary",
        capture_id=None,
        started_at_utc=started_at_utc,
        races=races,
        generations=generations,
        generation_failures=generation_failures,
        pending_generation_count=pending_generation_count,
        writer_errors=writer_errors,
    )
    summary["file"] = {
        "name": path.name,
        "captures": captures,
        "invalid_records": invalid_records,
    }
    return summary


def _race_from_payload(payload: dict[str, object]) -> RoutingRaceTelemetry:
    raw_attempts = payload["attempts"]
    if not isinstance(raw_attempts, list):
        raise TypeError("race attempts must be a list")
    attempts = tuple(
        _attempt_from_payload(attempt) for attempt in raw_attempts if isinstance(attempt, dict)
    )
    return RoutingRaceTelemetry(
        race_id=str(payload["race_id"]),
        utterance_id=str(payload["utterance_id"]),
        started_at_utc=str(payload["started_at_utc"]),
        total_user_wait_ms=int(payload["total_user_wait_ms"]),
        succeeded=bool(payload["succeeded"]),
        winner_attempt=(
            str(payload["winner_attempt"]) if payload.get("winner_attempt") is not None else None
        ),
        fallback_enabled=bool(payload["fallback_enabled"]),
        first_fallback_triggered=bool(payload["first_fallback_triggered"]),
        first_fallback_trigger_reason=(
            str(payload["first_fallback_trigger_reason"])
            if payload.get("first_fallback_trigger_reason") is not None
            else None
        ),
        second_fallback_eligible=bool(payload["second_fallback_eligible"]),
        second_fallback_triggered=bool(payload["second_fallback_triggered"]),
        dual_bill_candidate=bool(payload["dual_bill_candidate"]),
        attempts=attempts,
    )


def _attempt_from_payload(payload: dict[str, object]) -> RoutingAttemptTelemetry:
    raw_models = payload.get("requested_models")
    models = tuple(str(model) for model in raw_models) if isinstance(raw_models, list) else ()
    return RoutingAttemptTelemetry(
        name=str(payload["name"]),
        requested_provider=str(payload["requested_provider"]),
        requested_models=models,
        started_offset_ms=_payload_optional_int(payload.get("started_offset_ms")),
        completed_offset_ms=_payload_optional_int(payload.get("completed_offset_ms")),
        duration_ms=_payload_optional_int(payload.get("duration_ms")),
        outcome=str(payload["outcome"]),
        response_model=_payload_optional_str(payload.get("response_model")),
        response_provider=_payload_optional_str(payload.get("response_provider")),
        generation_id=_payload_optional_str(payload.get("generation_id")),
        prompt_tokens=_payload_optional_int(payload.get("prompt_tokens")),
        completion_tokens=_payload_optional_int(payload.get("completion_tokens")),
        total_tokens=_payload_optional_int(payload.get("total_tokens")),
        response_cost_usd=_payload_optional_str(payload.get("response_cost_usd")),
        error_type=_payload_optional_str(payload.get("error_type")),
    )


def _generation_from_payload(payload: dict[str, object]) -> OpenRouterGenerationTelemetry:
    return OpenRouterGenerationTelemetry(
        generation_id=str(payload["generation_id"]),
        total_cost_usd=_payload_optional_str(payload.get("total_cost_usd")),
        upstream_inference_cost_usd=_payload_optional_str(
            payload.get("upstream_inference_cost_usd")
        ),
        provider_name=_payload_optional_str(payload.get("provider_name")),
        model=_payload_optional_str(payload.get("model")),
        latency_ms=_payload_optional_int(payload.get("latency_ms")),
        generation_time_ms=_payload_optional_int(payload.get("generation_time_ms")),
        prompt_tokens=_payload_optional_int(payload.get("prompt_tokens")),
        completion_tokens=_payload_optional_int(payload.get("completion_tokens")),
        cancelled=_payload_optional_bool(payload.get("cancelled")),
        is_byok=_payload_optional_bool(payload.get("is_byok")),
        finish_reason=_payload_optional_str(payload.get("finish_reason")),
    )


def _payload_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _payload_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _payload_optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _append_json_line(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.write("\n")


def _format_utc(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _nearest_rank(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def _percentile_summary(values: list[int]) -> dict[str, int | str | None]:
    return {
        "count": len(values),
        "method": "nearest_rank",
        "p50": _nearest_rank(values, 50),
        "p75": _nearest_rank(values, 75),
        "p90": _nearest_rank(values, 90),
        "p95": _nearest_rank(values, 95),
        "p99": _nearest_rank(values, 99),
        "max": max(values) if values else None,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")
