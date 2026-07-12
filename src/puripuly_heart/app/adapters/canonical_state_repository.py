from __future__ import annotations

import asyncio
import copy
import hashlib
from dataclasses import replace
from pathlib import Path

from puripuly_heart.app.ports.canonical_state_repository import (
    CanonicalEnvelopeSnapshot,
    CanonicalIntentSnapshot,
    CanonicalRepositoryCancelled,
    CanonicalRepositoryConflict,
    CanonicalRepositoryError,
    OperationalStateSnapshot,
)
from puripuly_heart.app.ports.owned_async import OwnedFailure, settle_owned
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitReceipt,
)
from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.compat import (
    canonical_settings_path_lock,
)
from puripuly_heart.config.settings_vnext.facade import load_vnext_settings, save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    PersistedOperationalState,
    TelemetryOperationalState,
    UserIntentSettings,
    with_telemetry_consent,
)

CanonicalStateRepositoryError = CanonicalRepositoryError
CanonicalStateRevisionConflict = CanonicalRepositoryConflict


class CanonicalStateUnitOfWork:
    process_ownership = "cross_process_path_scoped_os_lock"

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_envelope(self) -> tuple[AppSettingsVNext, str]:
        result = load_vnext_settings(self._path)
        if result.settings is None:
            status = getattr(result.status, "value", result.status)
            raise CanonicalStateRepositoryError(f"read_{status}")
        return result.settings, _revision(result.settings)

    def commit_envelope(
        self,
        envelope: AppSettingsVNext,
        *,
        expected_revision: str,
    ) -> tuple[AppSettingsVNext, str]:
        with canonical_settings_path_lock(self._path):
            current, current_revision = self.load_envelope()
            if current_revision != expected_revision:
                raise CanonicalStateRevisionConflict(
                    CanonicalEnvelopeSnapshot(current.intent, current.state, current_revision)
                )
            result = save_vnext_settings(self._path, envelope)
            if not result.ok:
                status = getattr(result.status, "value", result.status)
                raise CanonicalStateRepositoryError(f"write_{status}")
            return envelope, _revision(envelope)

    def commit_intent(
        self,
        value: UserIntentSettings,
        *,
        expected_revision: str,
    ) -> tuple[AppSettingsVNext, str]:
        with canonical_settings_path_lock(self._path):
            envelope, revision = self.load_envelope()
            if revision != expected_revision:
                raise CanonicalStateRevisionConflict(
                    CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, revision)
                )
            if value.telemetry.consent != envelope.intent.telemetry.consent:
                raise CanonicalStateRepositoryError(
                    "telemetry consent requires atomic telemetry state transition"
                )
            return self.commit_envelope(
                replace(envelope, intent=value),
                expected_revision=expected_revision,
            )

    def commit_operational_state(
        self,
        value: PersistedOperationalState,
        *,
        expected_revision: str,
    ) -> tuple[AppSettingsVNext, str]:
        with canonical_settings_path_lock(self._path):
            envelope, revision = self.load_envelope()
            if revision != expected_revision:
                raise CanonicalStateRevisionConflict(
                    CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, revision)
                )
            return self.commit_envelope(
                replace(envelope, state=value),
                expected_revision=expected_revision,
            )

    def load(self) -> CanonicalEnvelopeSnapshot:
        envelope, revision = self.load_envelope()
        return CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, revision)

    def set_telemetry_consent(
        self,
        consent: str,
        *,
        expected_revision: str,
    ) -> CanonicalEnvelopeSnapshot:
        with canonical_settings_path_lock(self._path):
            envelope, revision = self.load_envelope()
            if revision != expected_revision:
                raise CanonicalStateRevisionConflict(
                    CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, revision)
                )
            committed, next_revision = self.commit_envelope(
                with_telemetry_consent(envelope, consent),
                expected_revision=expected_revision,
            )
            return CanonicalEnvelopeSnapshot(
                committed.intent,
                committed.state,
                next_revision,
            )

    def mark_telemetry_date_sent(
        self,
        active_date_utc: str,
        expected_anonymous_id: str,
        *,
        expected_revision: str,
    ) -> CanonicalEnvelopeSnapshot:
        with canonical_settings_path_lock(self._path):
            envelope, revision = self.load_envelope()
            if revision != expected_revision:
                raise CanonicalStateRevisionConflict(
                    CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, revision)
                )
            telemetry = envelope.state.telemetry
            if (
                envelope.intent.telemetry.consent != "allow"
                or telemetry.anonymous_id != expected_anonymous_id
            ):
                raise CanonicalStateRevisionConflict(
                    CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, revision)
                )
            dates = tuple(sorted({*telemetry.sent_translation_success_dates_utc, active_date_utc}))
            if dates == telemetry.sent_translation_success_dates_utc:
                return CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, revision)
            next_state = replace(
                envelope.state,
                telemetry=TelemetryOperationalState(
                    anonymous_id=telemetry.anonymous_id,
                    sent_translation_success_dates_utc=dates,
                ),
            )
            committed, next_revision = self.commit_envelope(
                replace(envelope, state=next_state),
                expected_revision=expected_revision,
            )
            return CanonicalEnvelopeSnapshot(
                committed.intent,
                committed.state,
                next_revision,
            )


class CanonicalIntentRepository:
    def __init__(self, unit_of_work: CanonicalStateUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def load(self) -> CanonicalIntentSnapshot:
        envelope, revision = self._unit_of_work.load_envelope()
        return CanonicalIntentSnapshot(envelope.intent, revision)

    def save(
        self,
        value: UserIntentSettings,
        *,
        expected_revision: str,
    ) -> CanonicalIntentSnapshot:
        committed, next_revision = self._unit_of_work.commit_intent(
            value,
            expected_revision=expected_revision,
        )
        return CanonicalIntentSnapshot(committed.intent, next_revision)


class OperationalStateRepository:
    def __init__(self, unit_of_work: CanonicalStateUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def load(self) -> OperationalStateSnapshot:
        envelope, revision = self._unit_of_work.load_envelope()
        return OperationalStateSnapshot(envelope.state, revision)

    def save(
        self,
        value: PersistedOperationalState,
        *,
        expected_revision: str,
    ) -> OperationalStateSnapshot:
        committed, next_revision = self._unit_of_work.commit_operational_state(
            value,
            expected_revision=expected_revision,
        )
        return OperationalStateSnapshot(committed.state, next_revision)


def _revision(settings: AppSettingsVNext) -> str:
    payload = serialization.to_json_text(settings).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class AsyncCanonicalStateRepository:
    def __init__(self, unit_of_work: CanonicalStateUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    async def load(self) -> CanonicalEnvelopeSnapshot:
        try:
            outcome = await settle_owned(asyncio.to_thread(self._unit_of_work.load))
        except OwnedFailure as exc:
            status = (
                exc.cause.status
                if isinstance(exc.cause, CanonicalRepositoryError)
                else "read_failed"
            )
            raise CanonicalRepositoryCancelled(
                cancellation_count=exc.cancellation_count, failure_status=status
            ) from None
        except CanonicalRepositoryError:
            raise
        except OSError as exc:
            raise CanonicalRepositoryError("read_lock_failed") from exc
        if outcome.cancellation_count:
            raise CanonicalRepositoryCancelled(cancellation_count=outcome.cancellation_count)
        return copy.deepcopy(outcome.value)

    async def commit_intent(
        self,
        value: UserIntentSettings,
        *,
        expected_revision: str,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt:
        try:
            outcome = await settle_owned(
                asyncio.to_thread(
                    self._unit_of_work.commit_intent,
                    copy.deepcopy(value),
                    expected_revision=expected_revision,
                )
            )
        except OwnedFailure as exc:
            status = (
                exc.cause.status
                if isinstance(exc.cause, CanonicalRepositoryError)
                else "write_failed"
            )
            raise CanonicalRepositoryCancelled(
                cancellation_count=exc.cancellation_count, failure_status=status
            ) from None
        except CanonicalRepositoryError:
            raise
        except OSError as exc:
            raise CanonicalRepositoryError("write_lock_failed") from exc
        envelope, revision = outcome.value
        receipt = SettingsCommitReceipt(envelope, revision, reason, correlation_id)
        if outcome.cancellation_count:
            raise CanonicalRepositoryCancelled(
                receipt, cancellation_count=outcome.cancellation_count
            )
        return receipt

    async def commit_operational_state(
        self,
        value: PersistedOperationalState,
        *,
        expected_revision: str,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt:
        try:
            outcome = await settle_owned(
                asyncio.to_thread(
                    self._unit_of_work.commit_operational_state,
                    copy.deepcopy(value),
                    expected_revision=expected_revision,
                )
            )
        except OwnedFailure as exc:
            status = (
                exc.cause.status
                if isinstance(exc.cause, CanonicalRepositoryError)
                else "write_failed"
            )
            raise CanonicalRepositoryCancelled(
                cancellation_count=exc.cancellation_count, failure_status=status
            ) from None
        except CanonicalRepositoryError:
            raise
        except OSError as exc:
            raise CanonicalRepositoryError("write_lock_failed") from exc
        envelope, revision = outcome.value
        receipt = SettingsCommitReceipt(envelope, revision, reason, correlation_id)
        if outcome.cancellation_count:
            raise CanonicalRepositoryCancelled(
                receipt, cancellation_count=outcome.cancellation_count
            )
        return receipt
