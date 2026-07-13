from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import FrozenInstanceError

import pytest

from puripuly_heart.app.adapters.canonical_state_repository import (
    AsyncCanonicalStateRepository,
    CanonicalStateUnitOfWork,
)
from puripuly_heart.app.ports.application_settings import (
    DesktopOverlayValue,
    GithubStarClickedCommand,
    JsonScalarEntry,
    LocalExtraBodyValue,
    OverlayCalibrationValue,
    OverlayOscOutputSettingsCommand,
    SettingChange,
    SettingsField,
    StringListMapValue,
    StringMapValue,
    SttLanguageAudioSettingsCommand,
    TranslationFallbackValue,
    TranslationProviderSettingsCommand,
    UiPromptClipboardSettingsCommand,
)
from puripuly_heart.app.ports.owned_async import settle_owned
from puripuly_heart.app.services.canonical_application_settings import (
    CanonicalApplicationSettingsService,
    CanonicalOperationalStateService,
)
from puripuly_heart.config.settings_vnext.facade import save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import RuntimeApplyResult


class RecordingRuntime:
    def __init__(self, result: RuntimeApplyResult | None = None) -> None:
        self.calls = []
        self.result = result or RuntimeApplyResult("applied", None, None)

    async def apply_runtime(self, request):
        self.calls.append(request)
        return self.result


def _services(tmp_path):
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    repository = AsyncCanonicalStateRepository(CanonicalStateUnitOfWork(path))
    runtime = RecordingRuntime()
    return path, repository, runtime, CanonicalApplicationSettingsService(repository, runtime)


@pytest.mark.asyncio
async def test_real_adapter_commits_exact_receipt_then_applies_once(tmp_path) -> None:
    _, repository, runtime, service = _services(tmp_path)
    before = await service.snapshot()
    result = await service.execute(
        UiPromptClipboardSettingsCommand(
            (SettingChange(SettingsField.UI_LOCALE, "ja"),), before.revision, "c-1"
        )
    )
    assert result.status == "applied"
    assert result.snapshot.revision == runtime.calls[0].receipt.revision
    assert runtime.calls[0].receipt.correlation_id == "c-1"
    assert len(runtime.calls) == 1
    assert (await repository.load()).intent.ui.locale == "ja"


@pytest.mark.asyncio
async def test_concurrent_intent_and_operational_updates_have_one_cas_winner(tmp_path) -> None:
    _, repository, runtime, intent = _services(tmp_path)
    operational = CanonicalOperationalStateService(repository)
    revision = (await repository.load()).revision
    results = await asyncio.gather(
        intent.execute(
            UiPromptClipboardSettingsCommand(
                (SettingChange(SettingsField.UI_LOCALE, "ko"),), revision
            )
        ),
        operational.execute_operational(GithubStarClickedCommand(True, revision)),
    )
    assert sum(result.status in {"applied", "committed"} for result in results) == 1
    assert sum(result.status == "conflict" for result in results) == 1
    assert len(runtime.calls) <= 1


@pytest.mark.asyncio
async def test_operational_github_star_commit_never_applies_runtime(tmp_path) -> None:
    _, repository, runtime, _ = _services(tmp_path)
    service = CanonicalOperationalStateService(repository)
    revision = (await repository.load()).revision
    result = await service.execute_operational(GithubStarClickedCommand(True, revision))
    assert result.status == "committed"
    assert runtime.calls == []
    assert (await repository.load()).operational_state.github_star_prompt.clicked


@pytest.mark.asyncio
async def test_stale_commit_returns_authoritative_rebase_snapshot(tmp_path) -> None:
    _, repository, runtime, service = _services(tmp_path)
    stale = await service.snapshot()
    first = await service.execute(
        UiPromptClipboardSettingsCommand(
            (SettingChange(SettingsField.UI_LOCALE, "ja"),), stale.revision
        )
    )
    second = await service.execute(
        UiPromptClipboardSettingsCommand(
            (SettingChange(SettingsField.UI_LOCALE, "ko"),), stale.revision
        )
    )
    assert first.status == "applied"
    assert second.status == "conflict"
    assert second.snapshot.revision == first.snapshot.revision
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_runtime_failure_and_exception_preserve_committed_revision(tmp_path) -> None:
    _, repository, runtime, service = _services(tmp_path)
    runtime.result = RuntimeApplyResult("failed", None, None)
    before = await service.snapshot()
    failed = await service.execute(
        UiPromptClipboardSettingsCommand(
            (SettingChange(SettingsField.UI_LOCALE, "ja"),), before.revision
        )
    )
    assert failed.status == "degraded"
    assert (await repository.load()).revision == failed.snapshot.revision

    async def explode(_request):
        raise RuntimeError("sensitive detail")

    runtime.apply_runtime = explode
    before = await service.snapshot()
    raised = await service.execute(
        UiPromptClipboardSettingsCommand(
            (SettingChange(SettingsField.UI_LOCALE, "ko"),), before.revision
        )
    )
    assert raised.status == "degraded"
    assert raised.diagnostics is not None
    assert "sensitive" not in repr(raised.diagnostics)
    assert (await repository.load()).revision == raised.snapshot.revision


@pytest.mark.asyncio
async def test_sync_disk_work_runs_in_worker_thread_and_loop_remains_responsive(
    tmp_path, monkeypatch
) -> None:
    _, repository, _, _ = _services(tmp_path)
    worker = None
    loop_thread = threading.get_ident()
    original = repository._unit_of_work.load

    def slow_load():
        nonlocal worker
        worker = threading.get_ident()
        time.sleep(0.05)
        return original()

    monkeypatch.setattr(repository._unit_of_work, "load", slow_load)
    ticked = False

    async def tick():
        nonlocal ticked
        await asyncio.sleep(0.005)
        ticked = True

    tick = asyncio.create_task(tick())
    await repository.load()
    await tick
    assert ticked
    assert worker != loop_thread


@pytest.mark.asyncio
async def test_results_are_frozen_and_snapshot_leaves_are_deeply_immutable(tmp_path) -> None:
    _, _, _, service = _services(tmp_path)
    snapshot = await service.snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.revision = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.leaves[0][0][0] = "changed"  # type: ignore[index]


@pytest.mark.asyncio
async def test_nested_wrapper_values_materialize_as_canonical_schema_types(tmp_path) -> None:
    _, repository, runtime, service = _services(tmp_path)
    before = await service.snapshot()
    translation = await service.execute(
        TranslationProviderSettingsCommand(
            (
                SettingChange(
                    SettingsField.TRANSLATION_CONNECTION_HISTORY,
                    StringMapValue((("gemma4", "managed"),)),
                ),
                SettingChange(
                    SettingsField.LOCAL_LLM_EXTRA_BODY,
                    LocalExtraBodyValue((JsonScalarEntry("temperature", 0.2),)),
                ),
                SettingChange(
                    SettingsField.TRANSLATION_FALLBACK,
                    TranslationFallbackValue(False, "deepseek_v4_flash", "official_byok", "none"),
                ),
            ),
            before.revision,
        )
    )
    assert translation.status == "applied"
    envelope = await repository.load()
    assert type(envelope.intent.translation.connection_history) is dict
    assert type(envelope.intent.local_llm.extra_body) is dict
    assert runtime.calls[-1].receipt.revision == translation.snapshot.revision

    stt = await service.execute(
        SttLanguageAudioSettingsCommand(
            (
                SettingChange(
                    SettingsField.STT_CUSTOM_TERMS,
                    StringListMapValue((("ko", ("푸리",)),)),
                ),
            ),
            translation.snapshot.revision,
        )
    )
    overlay = await service.execute(
        OverlayOscOutputSettingsCommand(
            (
                SettingChange(SettingsField.OVERLAY_CALIBRATION, OverlayCalibrationValue()),
                SettingChange(SettingsField.OVERLAY_DESKTOP_FLET, DesktopOverlayValue()),
            ),
            stt.snapshot.revision,
        )
    )
    envelope = await repository.load()
    assert type(envelope.intent.stt.custom_terms) is dict
    assert type(envelope.intent.stt.custom_terms["ko"]) is list
    assert overlay.status == "applied"
    assert overlay.snapshot.revision == runtime.calls[-1].receipt.revision


@pytest.mark.asyncio
async def test_owned_await_settles_after_repeated_cancellation_without_orphan() -> None:
    release = asyncio.Event()
    settled = False

    async def operation():
        nonlocal settled
        await release.wait()
        settled = True
        return "receipt"

    owner = asyncio.create_task(settle_owned(operation()))
    await asyncio.sleep(0)
    owner.cancel()
    await asyncio.sleep(0)
    owner.cancel()
    await asyncio.sleep(0)
    release.set()
    outcome = await owner
    assert outcome.value == "receipt"
    assert outcome.cancellation_count == 2
    assert settled


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_cancelled_runtime_failure_keeps_receipt_snapshot_and_reconciliation(
    tmp_path, raises
) -> None:
    _, _, runtime, service = _services(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed(_request):
        entered.set()
        await release.wait()
        if raises:
            raise RuntimeError("private runtime failure")
        return RuntimeApplyResult("failed", None, None)

    runtime.apply_runtime = delayed
    before = await service.snapshot()
    task = asyncio.create_task(
        service.execute(
            UiPromptClipboardSettingsCommand(
                (SettingChange(SettingsField.UI_LOCALE, "ja"),), before.revision
            )
        )
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    outcome = await task
    assert outcome.status == "cancelled_degraded"
    assert outcome.cancellation_count >= 1
    assert outcome.committed_revision == outcome.snapshot.revision
    assert outcome.diagnostics is not None or not raises


@pytest.mark.asyncio
async def test_sequential_commands_do_not_share_cancellation_bookkeeping(tmp_path) -> None:
    _, _, runtime, service = _services(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_failure(_request):
        entered.set()
        await release.wait()
        raise RuntimeError("runtime failure")

    runtime.apply_runtime = delayed_failure
    before = await service.snapshot()
    first_task = asyncio.create_task(
        service.execute(
            UiPromptClipboardSettingsCommand(
                (SettingChange(SettingsField.UI_LOCALE, "ja"),), before.revision
            )
        )
    )
    await entered.wait()
    first_task.cancel()
    await asyncio.sleep(0)
    release.set()
    first = await first_task
    assert first.status == "cancelled_degraded"
    assert first.cancellation_count == 1

    async def immediate_failure(_request):
        raise RuntimeError("ordinary failure")

    runtime.apply_runtime = immediate_failure
    second = await service.execute(
        UiPromptClipboardSettingsCommand(
            (SettingChange(SettingsField.UI_LOCALE, "ko"),), first.snapshot.revision
        )
    )
    assert second.status == "degraded"
    assert second.cancellation_count == 0

    runtime.apply_runtime = immediate_failure
    entered.clear()
    release.clear()
    third_revision = second.snapshot.revision
    third = await service.execute(UiPromptClipboardSettingsCommand((), third_revision))
    assert third.status == "degraded"
    runtime.apply_runtime = delayed_failure
    fourth_task = asyncio.create_task(
        service.execute(UiPromptClipboardSettingsCommand((), third.snapshot.revision))
    )
    await entered.wait()
    fourth_task.cancel()
    await asyncio.sleep(0)
    release.set()
    fourth = await fourth_task
    assert fourth.status == "cancelled_degraded"
    assert fourth.cancellation_count == 1


@pytest.mark.asyncio
async def test_repeated_commit_cancellation_reports_exact_count_and_settles(
    tmp_path, monkeypatch
) -> None:
    _, repository, _, service = _services(tmp_path)
    before = await service.snapshot()
    entered = threading.Event()
    release = threading.Event()
    original = repository._unit_of_work.commit_intent

    def delayed_commit(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(repository._unit_of_work, "commit_intent", delayed_commit)
    task = asyncio.create_task(
        service.execute(
            UiPromptClipboardSettingsCommand(
                (SettingChange(SettingsField.UI_LOCALE, "ja"),), before.revision
            )
        )
    )
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    result = await task
    assert result.cancellation_count == 2
    assert result.committed_revision == result.snapshot.revision
