from __future__ import annotations

import asyncio
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.adapters.canonical_state_repository import (
    CanonicalStateRepositoryError,
    CanonicalStateRevisionConflict,
    CanonicalStateUnitOfWork,
)
from puripuly_heart.app.ports.canonical_state_repository import CanonicalEnvelopeSnapshot
from puripuly_heart.app.ports.settings_repository import SettingsRevisionConflict
from puripuly_heart.app.services.telemetry_operational_state import (
    TelemetryOperationalStateOwner,
)
from puripuly_heart.app.wiring_composition import create_canonical_state_repositories
from puripuly_heart.config.settings_vnext import compat as compat_module
from puripuly_heart.config.settings_vnext.facade import load_vnext_settings, save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    TelemetryConsentIntent,
    TelemetryOperationalState,
)
from puripuly_heart.core.telemetry import PersistTranslationSuccessDateCommand


def _competing_cas(path_text: str, revision: str, locale: str, queue, start) -> None:
    repositories = create_canonical_state_repositories(Path(path_text))
    current = repositories.intent.load().value
    queue.put("ready")
    start.wait(timeout=10)
    try:
        repositories.intent.save(
            replace(current, ui=replace(current.ui, locale=locale)),
            expected_revision=revision,
        )
        queue.put("saved")
    except CanonicalStateRevisionConflict:
        queue.put("conflict")


def test_distinct_owners_share_one_envelope_and_revision(tmp_path) -> None:
    path = tmp_path / "settings.json"
    initial = AppSettingsVNext()
    assert save_vnext_settings(path, initial).ok
    repositories = create_canonical_state_repositories(path)

    intent = repositories.intent.load()
    state = repositories.operational_state.load()
    assert intent.revision == state.revision

    next_intent = replace(
        intent.value,
        ui=replace(intent.value.ui, locale="ja"),
    )
    committed = repositories.intent.save(next_intent, expected_revision=intent.revision)
    assert committed.revision != intent.revision
    assert repositories.operational_state.load().value == state.value

    with pytest.raises(CanonicalStateRevisionConflict):
        repositories.operational_state.save(state.value, expected_revision=state.revision)


def test_revision_conflict_preserves_legacy_base_and_typed_authority() -> None:
    assert issubclass(CanonicalStateRevisionConflict, SettingsRevisionConflict)
    legacy = CanonicalStateRevisionConflict("legacy conflict")
    assert legacy.authoritative is None
    envelope = AppSettingsVNext()
    typed = CanonicalStateRevisionConflict(
        CanonicalEnvelopeSnapshot(envelope.intent, envelope.state, "r1")
    )
    assert typed.authoritative is not None
    assert typed.authoritative.revision == "r1"


def test_generic_intent_commit_rejects_telemetry_consent_change(tmp_path) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    repositories = create_canonical_state_repositories(path)
    snapshot = repositories.intent.load()
    with pytest.raises(CanonicalStateRepositoryError, match="atomic telemetry state transition"):
        repositories.intent.save(
            replace(snapshot.value, telemetry=TelemetryConsentIntent("allow")),
            expected_revision=snapshot.revision,
        )


@pytest.mark.asyncio
async def test_telemetry_owner_updates_only_operational_state_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / "settings.json"
    initial = AppSettingsVNext(
        intent=replace(
            AppSettingsVNext().intent,
            telemetry=TelemetryConsentIntent("allow"),
        ),
        state=replace(
            AppSettingsVNext().state,
            telemetry=TelemetryOperationalState(anonymous_id="anon-c1"),
        ),
    )
    assert save_vnext_settings(path, initial).ok
    repositories = create_canonical_state_repositories(path)
    owner = TelemetryOperationalStateOwner(repositories.unit_of_work)

    loaded = await owner.load()
    assert loaded is not None
    assert loaded.anonymous_id == "anon-c1"
    command = PersistTranslationSuccessDateCommand("2026-07-11", "anon-c1")
    assert await owner.mark_translation_success_date_sent(command)
    first = repositories.operational_state.load()
    assert first.value.telemetry.sent_translation_success_dates_utc == ("2026-07-11",)
    assert await owner.mark_translation_success_date_sent(command)
    assert repositories.operational_state.load().revision == first.revision
    assert repositories.intent.load().value == initial.intent


def test_failed_atomic_save_preserves_previous_envelope(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    before = path.read_bytes()
    repositories = create_canonical_state_repositories(path)
    snapshot = repositories.intent.load()
    replace_reached = False

    def fail_replace(source, target):
        nonlocal replace_reached
        replace_reached = True
        raise OSError("isolated forced replace failure")

    monkeypatch.setattr("puripuly_heart.config.settings_vnext.compat.os.replace", fail_replace)
    with pytest.raises(CanonicalStateRepositoryError):
        repositories.intent.save(
            replace(snapshot.value, ui=replace(snapshot.value.ui, locale="ja")),
            expected_revision=snapshot.revision,
        )
    assert replace_reached
    assert path.read_bytes() == before


def test_path_scoped_cas_prevents_lost_updates_across_repository_instances(tmp_path) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    first = create_canonical_state_repositories(path)
    second = create_canonical_state_repositories(path)
    revision = first.intent.load().revision

    def save(repository, locale):
        current = repository.intent.load().value
        return repository.intent.save(
            replace(current, ui=replace(current.ui, locale=locale)),
            expected_revision=revision,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(save, first, "ja"),
            executor.submit(save, second, "ko"),
        ]
    outcomes = []
    for future in futures:
        try:
            future.result()
            outcomes.append("saved")
        except CanonicalStateRevisionConflict:
            outcomes.append("conflict")
    assert sorted(outcomes) == ["conflict", "saved"]
    assert not path.with_suffix(".json.tmp").exists()


def test_cross_process_cas_has_exactly_one_winner_and_no_temp_leak(tmp_path) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    revision = create_canonical_state_repositories(path).intent.load().revision
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    start = context.Event()
    processes = [
        context.Process(target=_competing_cas, args=(str(path), revision, locale, queue, start))
        for locale in ("ja", "ko")
    ]
    for process in processes:
        process.start()
    assert [queue.get(timeout=10) for _ in processes].count("ready") == 2
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert sorted(queue.get(timeout=2) for _ in processes) == ["conflict", "saved"]
    assert load_vnext_settings(path).settings is not None
    assert list(tmp_path.glob(".settings.json.*.tmp")) == []


def test_canonical_unit_of_work_declares_cross_process_path_ownership() -> None:
    assert CanonicalStateUnitOfWork.process_ownership == "cross_process_path_scoped_os_lock"


def test_legacy_facade_writer_participates_in_same_path_lock(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    repositories = create_canonical_state_repositories(path)
    snapshot = repositories.intent.load()
    entered = threading.Event()
    release = threading.Event()
    original_write = compat_module._atomic_write_text

    def delayed_write(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(compat_module, "_atomic_write_text", delayed_write)
    direct_value = AppSettingsVNext(
        intent=replace(snapshot.value, telemetry=TelemetryConsentIntent("allow"))
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        direct = executor.submit(save_vnext_settings, path, direct_value)
        assert entered.wait(timeout=2)
        cas = executor.submit(
            repositories.intent.save,
            replace(snapshot.value, telemetry=TelemetryConsentIntent("decline")),
            expected_revision=snapshot.revision,
        )
        release.set()
        assert direct.result().ok
        with pytest.raises(CanonicalStateRevisionConflict):
            cas.result()
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.asyncio
async def test_allow_decline_lifecycle_is_one_coherent_envelope_transition(tmp_path) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    repositories = create_canonical_state_repositories(path)
    owner = TelemetryOperationalStateOwner(repositories.unit_of_work)

    allowed = await owner.set_consent("allow")
    assert allowed is not None
    assert allowed.consent == "allow"
    assert allowed.anonymous_id
    assert allowed.anonymous_id is not None
    command = PersistTranslationSuccessDateCommand("2026-07-11", allowed.anonymous_id)
    assert await owner.mark_translation_success_date_sent(command)

    declined = await owner.set_consent("decline")
    assert declined is not None
    assert declined.consent == "decline"
    assert declined.anonymous_id is None
    assert declined.sent_dates_utc == ()


@pytest.mark.asyncio
async def test_sent_date_rejects_decline_and_reenabled_identity_races(tmp_path) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    repositories = create_canonical_state_repositories(path)
    owner = TelemetryOperationalStateOwner(repositories.unit_of_work)
    allowed = await owner.set_consent("allow")
    assert allowed is not None and allowed.anonymous_id is not None
    stale_command = PersistTranslationSuccessDateCommand("2026-07-11", allowed.anonymous_id)

    declined = await owner.set_consent("decline")
    assert declined is not None
    assert not await owner.mark_translation_success_date_sent(stale_command)
    assert (
        repositories.operational_state.load().value.telemetry.sent_translation_success_dates_utc
        == ()
    )

    reenabled = await owner.set_consent("allow")
    assert reenabled is not None and reenabled.anonymous_id is not None
    assert reenabled.anonymous_id != allowed.anonymous_id
    assert not await owner.mark_translation_success_date_sent(stale_command)
    assert (
        repositories.operational_state.load().value.telemetry.sent_translation_success_dates_utc
        == ()
    )


@pytest.mark.asyncio
async def test_telemetry_load_uses_one_coherent_envelope_revision() -> None:
    class RecordingUnitOfWork:
        load_calls = 0

        def load(self):
            self.load_calls += 1
            settings = AppSettingsVNext(
                intent=replace(
                    AppSettingsVNext().intent,
                    telemetry=TelemetryConsentIntent("allow"),
                ),
                state=replace(
                    AppSettingsVNext().state,
                    telemetry=TelemetryOperationalState(anonymous_id="paired-id"),
                ),
            )
            return CanonicalEnvelopeSnapshot(settings.intent, settings.state, "paired-r1")

    unit_of_work = RecordingUnitOfWork()
    owner = TelemetryOperationalStateOwner(unit_of_work)  # type: ignore[arg-type]
    snapshot = await owner.load()
    assert snapshot is not None
    assert snapshot.consent == "allow"
    assert snapshot.anonymous_id == "paired-id"
    assert unit_of_work.load_calls == 1


@pytest.mark.asyncio
async def test_telemetry_owner_keeps_blocking_io_off_event_loop() -> None:
    class SlowUnitOfWork:
        def load(self):
            time.sleep(0.05)
            return CanonicalStateUnitOfWork.__new__(CanonicalStateUnitOfWork)

    owner = TelemetryOperationalStateOwner(SlowUnitOfWork())  # type: ignore[arg-type]
    ticked = False

    async def tick() -> None:
        nonlocal ticked
        await asyncio.sleep(0.005)
        ticked = True

    task = asyncio.create_task(tick())
    assert await owner.load() is None
    await task
    assert ticked


@pytest.mark.asyncio
async def test_telemetry_owner_contains_repository_failures() -> None:
    class FailingUnitOfWork:
        def load(self):
            raise RuntimeError("raw secret failure")

    owner = TelemetryOperationalStateOwner(FailingUnitOfWork())  # type: ignore[arg-type]
    assert await owner.load() is None
    assert await owner.set_consent("allow") is None
    assert not await owner.mark_translation_success_date_sent(
        PersistTranslationSuccessDateCommand("2026-07-11", "failed-id")
    )
