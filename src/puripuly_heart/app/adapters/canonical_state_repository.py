from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from puripuly_heart.app.ports.canonical_state_repository import (
    CanonicalEnvelopeSnapshot,
    CanonicalIntentSnapshot,
    OperationalStateSnapshot,
)
from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.compat import canonical_settings_path_lock
from puripuly_heart.config.settings_vnext.facade import load_vnext_settings, save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    PersistedOperationalState,
    TelemetryOperationalState,
    UserIntentSettings,
    with_telemetry_consent,
)


class CanonicalStateRepositoryError(RuntimeError):
    pass


class CanonicalStateRevisionConflict(CanonicalStateRepositoryError):
    pass


class CanonicalStateUnitOfWork:
    process_ownership = "single_process_path_scoped"

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_envelope(self) -> tuple[AppSettingsVNext, str]:
        result = load_vnext_settings(self._path)
        if result.settings is None:
            status = getattr(result.status, "value", result.status)
            raise CanonicalStateRepositoryError(str(status))
        return result.settings, _revision(result.settings)

    def commit_envelope(
        self,
        envelope: AppSettingsVNext,
        *,
        expected_revision: str,
    ) -> tuple[AppSettingsVNext, str]:
        with canonical_settings_path_lock(self._path):
            _, current_revision = self.load_envelope()
            if current_revision != expected_revision:
                raise CanonicalStateRevisionConflict("canonical state revision conflict")
            result = save_vnext_settings(self._path, envelope)
            if not result.ok:
                status = getattr(result.status, "value", result.status)
                raise CanonicalStateRepositoryError(str(status))
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
                raise CanonicalStateRevisionConflict("canonical state revision conflict")
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
                raise CanonicalStateRevisionConflict("canonical state revision conflict")
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
                raise CanonicalStateRevisionConflict("canonical state revision conflict")
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
                raise CanonicalStateRevisionConflict("canonical state revision conflict")
            telemetry = envelope.state.telemetry
            if (
                envelope.intent.telemetry.consent != "allow"
                or telemetry.anonymous_id != expected_anonymous_id
            ):
                raise CanonicalStateRevisionConflict("telemetry identity is no longer current")
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
