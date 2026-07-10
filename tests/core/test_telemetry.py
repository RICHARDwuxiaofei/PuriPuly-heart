from __future__ import annotations

from datetime import date

import pytest

from puripuly_heart.core.telemetry import (
    PersistTranslationSuccessDateCommand,
    TranslationSuccessTelemetryService,
    TranslationSuccessTelemetrySnapshot,
)


class FakeTelemetryClient:
    def __init__(self, *, result: bool = True, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[str, str]] = []

    async def record_translation_success_day(self, identifier: str, active_date_utc: str) -> bool:
        self.calls.append((identifier, active_date_utc))
        if self.exc is not None:
            raise self.exc
        return self.result


class PersistRecorder:
    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.calls: list[PersistTranslationSuccessDateCommand] = []

    async def __call__(self, command: PersistTranslationSuccessDateCommand) -> bool:
        self.calls.append(command)
        return self.result


def _date_provider() -> date:
    return date(2026, 7, 3)


def _snapshot(
    *,
    consent: str = "allow",
    identifier: str | None = "anon-id",
    sent_dates: tuple[str, ...] = (),
) -> TranslationSuccessTelemetrySnapshot:
    return TranslationSuccessTelemetrySnapshot(consent, identifier, sent_dates)


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", ["unknown", "decline"])
async def test_unknown_and_declined_consent_skip_without_client_call(consent: str) -> None:
    client = FakeTelemetryClient()
    persist = PersistRecorder()
    events: list[tuple[str, dict[str, object]]] = []
    service = TranslationSuccessTelemetryService(
        client,
        utc_date_provider=_date_provider,
        diagnostics_sink=lambda event, metadata: events.append((event, dict(metadata))),
    )

    result = await service.record_translation_success_day(
        _snapshot(consent=consent), persist_sent_date=persist
    )

    assert result.status == "skipped_consent"
    assert client.calls == []
    assert persist.calls == []
    assert events == [("skipped_consent", {"reason": "consent_not_allow"})]


@pytest.mark.asyncio
async def test_allowed_missing_identifier_skips_without_client_call() -> None:
    client = FakeTelemetryClient()
    persist = PersistRecorder()
    service = TranslationSuccessTelemetryService(client, utc_date_provider=_date_provider)
    result = await service.record_translation_success_day(
        _snapshot(identifier=None), persist_sent_date=persist
    )
    assert result.status == "skipped_missing_identifier"
    assert client.calls == []
    assert persist.calls == []


@pytest.mark.asyncio
async def test_already_sent_date_is_idempotent() -> None:
    client = FakeTelemetryClient()
    persist = PersistRecorder()
    service = TranslationSuccessTelemetryService(client, utc_date_provider=_date_provider)
    result = await service.record_translation_success_day(
        _snapshot(sent_dates=("2026-07-03",)), persist_sent_date=persist
    )
    assert result.status == "skipped_already_sent"
    assert client.calls == []
    assert persist.calls == []


@pytest.mark.asyncio
async def test_success_sends_once_and_commands_owner_to_persist_date() -> None:
    client = FakeTelemetryClient(result=True)
    persist = PersistRecorder()
    service = TranslationSuccessTelemetryService(client, utc_date_provider=_date_provider)
    result = await service.record_translation_success_day(_snapshot(), persist_sent_date=persist)
    assert result.status == "sent"
    assert result.persisted is True
    assert client.calls == [("anon-id", "2026-07-03")]
    assert persist.calls == [PersistTranslationSuccessDateCommand("2026-07-03", "anon-id")]


@pytest.mark.asyncio
async def test_failed_send_does_not_command_persistence() -> None:
    client = FakeTelemetryClient(result=False)
    persist = PersistRecorder()
    service = TranslationSuccessTelemetryService(client, utc_date_provider=_date_provider)
    result = await service.record_translation_success_day(_snapshot(), persist_sent_date=persist)
    assert result.status == "send_failed"
    assert persist.calls == []


@pytest.mark.asyncio
async def test_client_exception_returns_safe_diagnostics() -> None:
    client = FakeTelemetryClient(exc=RuntimeError("secret payload should not appear"))
    service = TranslationSuccessTelemetryService(client, utc_date_provider=_date_provider)
    result = await service.record_translation_success_day(
        _snapshot(), persist_sent_date=PersistRecorder()
    )
    assert result.diagnostics == {"reason": "client_exception", "error_type": "RuntimeError"}
    assert "secret payload" not in str(result.diagnostics)


@pytest.mark.asyncio
async def test_failed_persistence_does_not_report_persisted() -> None:
    service = TranslationSuccessTelemetryService(
        FakeTelemetryClient(), utc_date_provider=_date_provider
    )
    result = await service.record_translation_success_day(
        _snapshot(), persist_sent_date=PersistRecorder(result=False)
    )
    assert result.status == "persist_failed"
    assert result.persisted is False


@pytest.mark.asyncio
async def test_persistence_exception_returns_safe_persist_failed_result() -> None:
    async def fail(_command: PersistTranslationSuccessDateCommand) -> bool:
        raise RuntimeError("raw secret persistence failure")

    service = TranslationSuccessTelemetryService(
        FakeTelemetryClient(), utc_date_provider=_date_provider
    )
    result = await service.record_translation_success_day(_snapshot(), persist_sent_date=fail)
    assert result.status == "persist_failed"
    assert result.persisted is False
    assert result.diagnostics == {
        "reason": "persistence_exception",
        "error_type": "RuntimeError",
    }
    assert "raw secret" not in str(result)
