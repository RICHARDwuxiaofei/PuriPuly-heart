from __future__ import annotations

import asyncio

from puripuly_heart.app.ports.canonical_state_repository import (
    CanonicalEnvelopeSnapshot,
    CanonicalStateUnitOfWorkPort,
)
from puripuly_heart.core.telemetry import (
    PersistTranslationSuccessDateCommand,
    TranslationSuccessTelemetrySnapshot,
)


class TelemetryOperationalStateOwner:
    def __init__(
        self,
        unit_of_work: CanonicalStateUnitOfWorkPort,
    ) -> None:
        self._unit_of_work = unit_of_work

    async def load(self) -> TranslationSuccessTelemetrySnapshot | None:
        try:
            envelope = await asyncio.to_thread(self._unit_of_work.load)
            return _telemetry_snapshot(envelope)
        except Exception:
            return None

    async def set_consent(self, consent: str) -> TranslationSuccessTelemetrySnapshot | None:
        try:
            envelope = await asyncio.to_thread(self._unit_of_work.load)
            committed = await asyncio.to_thread(
                self._unit_of_work.set_telemetry_consent,
                consent,
                expected_revision=envelope.revision,
            )
        except Exception:
            return None
        return _telemetry_snapshot(committed)

    async def mark_translation_success_date_sent(
        self,
        command: PersistTranslationSuccessDateCommand,
    ) -> bool:
        try:
            envelope = await asyncio.to_thread(self._unit_of_work.load)
            await asyncio.to_thread(
                self._unit_of_work.mark_telemetry_date_sent,
                command.active_date_utc,
                command.anonymous_id,
                expected_revision=envelope.revision,
            )
        except Exception:
            return False
        return True


def _telemetry_snapshot(
    envelope: CanonicalEnvelopeSnapshot,
) -> TranslationSuccessTelemetrySnapshot:
    telemetry = envelope.operational_state.telemetry
    return TranslationSuccessTelemetrySnapshot(
        consent=envelope.intent.telemetry.consent,
        anonymous_id=telemetry.anonymous_id,
        sent_dates_utc=telemetry.sent_translation_success_dates_utc,
    )


__all__ = ["TelemetryOperationalStateOwner"]
