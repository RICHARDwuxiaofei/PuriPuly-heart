from __future__ import annotations

import json

import pytest

from puripuly_heart.app.adapters.routing_telemetry_jsonl import (
    JsonlRoutingTelemetrySession,
)
from puripuly_heart.core.llm.routing_telemetry import (
    OpenRouterGenerationTelemetry,
    RoutingAttemptTelemetry,
    RoutingRaceTelemetry,
)


def _attempt(
    name: str,
    *,
    outcome: str,
    generation_id: str | None = None,
) -> RoutingAttemptTelemetry:
    started = 0 if name == "main_attempt" else 1300
    return RoutingAttemptTelemetry(
        name=name,
        requested_provider="openrouter",
        requested_models=("model-a",),
        started_offset_ms=started,
        completed_offset_ms=started + 800,
        duration_ms=800,
        outcome=outcome,
        response_model="model-a" if outcome == "success" else None,
        response_provider="provider-a" if outcome == "success" else None,
        generation_id=generation_id,
        prompt_tokens=20 if generation_id is not None else None,
        completion_tokens=8 if generation_id is not None else None,
        total_tokens=28 if generation_id is not None else None,
    )


@pytest.mark.asyncio
async def test_jsonl_routing_capture_writes_races_enrichment_and_summary(tmp_path) -> None:
    session = JsonlRoutingTelemetrySession(root_dir=tmp_path)
    path = await session.start()
    session.record_race(
        RoutingRaceTelemetry(
            race_id="race-1",
            utterance_id="utterance-1",
            started_at_utc="2026-07-30T00:00:00.000Z",
            total_user_wait_ms=2100,
            succeeded=True,
            winner_attempt="first_fallback",
            fallback_enabled=True,
            first_fallback_triggered=True,
            first_fallback_trigger_reason="timeout",
            second_fallback_eligible=True,
            second_fallback_triggered=False,
            dual_bill_candidate=True,
            attempts=(
                _attempt("main_attempt", outcome="cancelled"),
                _attempt(
                    "first_fallback",
                    outcome="success",
                    generation_id="gen-1",
                ),
                RoutingAttemptTelemetry(
                    name="second_fallback",
                    requested_provider="openrouter",
                    requested_models=("model-b",),
                    started_offset_ms=None,
                    completed_offset_ms=None,
                    duration_ms=None,
                    outcome="not_started",
                ),
            ),
        )
    )
    capture_id = session.register_openrouter_generation("gen-1")
    assert capture_id is not None
    session.record_openrouter_generation(
        capture_id,
        OpenRouterGenerationTelemetry(
            generation_id="gen-1",
            total_cost_usd="0.00042",
            upstream_inference_cost_usd="0.0004",
            provider_name="Provider A",
            model="model-a",
            latency_ms=780,
            generation_time_ms=700,
            prompt_tokens=20,
            completion_tokens=8,
            cancelled=False,
            is_byok=False,
            finish_reason="stop",
        ),
    )

    stopped_path = await session.stop()

    assert stopped_path == path
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == [
        "session_start",
        "race",
        "openrouter_generation",
        "session_summary",
        "file_summary",
    ]
    summary = records[-2]
    assert summary["latency_ms"]["p50"] == 2100
    assert summary["latency_ms"]["p99"] == 2100
    assert summary["fallback"]["first_trigger_rate"] == 1.0
    assert summary["fallback"]["first_rescue_rate"] == 1.0
    assert summary["fallback"]["dual_bill_candidate_rate"] == 1.0
    assert summary["winners"]["counts"] == {"first_fallback": 1}
    assert summary["cost"]["known_total_usd"] == "0.00042"
    assert summary["cost"]["known_loser_hedge_usd"] == "0"
    assert summary["cost"]["coverage_rate"] == 0.5
    assert summary["tokens"]["total_tokens"] == 28
    assert summary["tokens"]["winner_total_tokens"] == 28
    assert summary["tokens"]["coverage_rate"] == 0.5
    assert records[-1]["file"]["captures"] == 1
    assert records[-1]["races"]["total"] == 1


@pytest.mark.asyncio
async def test_jsonl_routing_capture_appends_sessions_and_summarizes_entire_file(
    tmp_path,
) -> None:
    session = JsonlRoutingTelemetrySession(root_dir=tmp_path)
    first_path = await session.start()
    session.record_race(
        RoutingRaceTelemetry(
            race_id="race-1",
            utterance_id="utterance-1",
            started_at_utc="2026-07-30T00:00:00.000Z",
            total_user_wait_ms=2100,
            succeeded=True,
            winner_attempt="first_fallback",
            fallback_enabled=True,
            first_fallback_triggered=True,
            first_fallback_trigger_reason="timeout",
            second_fallback_eligible=False,
            second_fallback_triggered=False,
            dual_bill_candidate=True,
            attempts=(
                _attempt("main_attempt", outcome="cancelled"),
                _attempt("first_fallback", outcome="success"),
            ),
        )
    )
    await session.stop()

    second_path = await session.start()
    session.record_race(
        RoutingRaceTelemetry(
            race_id="race-2",
            utterance_id="utterance-2",
            started_at_utc="2026-07-30T00:01:00.000Z",
            total_user_wait_ms=1000,
            succeeded=True,
            winner_attempt="main_attempt",
            fallback_enabled=False,
            first_fallback_triggered=False,
            first_fallback_trigger_reason=None,
            second_fallback_eligible=False,
            second_fallback_triggered=False,
            dual_bill_candidate=False,
            attempts=(_attempt("main_attempt", outcome="success"),),
        )
    )
    await session.stop()

    assert second_path == first_path
    records = [json.loads(line) for line in first_path.read_text(encoding="utf-8").splitlines()]
    file_summaries = [record for record in records if record["type"] == "file_summary"]
    assert len(file_summaries) == 2
    cumulative = file_summaries[-1]
    assert cumulative["file"]["captures"] == 2
    assert cumulative["races"]["total"] == 2
    assert cumulative["latency_ms"]["p50"] == 1000
    assert cumulative["latency_ms"]["p99"] == 2100
    assert cumulative["fallback"]["first_trigger_rate"] == 1.0
    assert cumulative["winners"]["counts"] == {
        "first_fallback": 1,
        "main_attempt": 1,
    }


@pytest.mark.asyncio
async def test_jsonl_routing_capture_is_inert_before_start(tmp_path) -> None:
    session = JsonlRoutingTelemetrySession(root_dir=tmp_path)

    assert session.active is False
    assert session.register_openrouter_generation("gen-1") is None
    assert await session.stop() is None
    assert list(tmp_path.iterdir()) == []
