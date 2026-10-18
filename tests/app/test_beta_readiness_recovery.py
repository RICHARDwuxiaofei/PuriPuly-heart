import asyncio
import logging
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from puripuly_heart.app.services.managed_usage import ManagedUsageMetadataResult
from puripuly_heart.app.services.provider_runtime_apply import (
    ProviderRuntimeApplyPlan,
    ProviderRuntimeState,
)
from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterStatusRefreshResult
from puripuly_heart.core.openrouter_metadata import OpenRouterKeyMetadata, OpenRouterMetadataError

from puripuly_heart.app.adapters.local_asr_application import LocalASRNoticeProjector
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.providers.llm import openrouter
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from puripuly_heart.ui.components.managed_trial_usage_bar import ManagedTrialUsageBar
from puripuly_heart.ui.i18n import get_locale, set_locale
from tests.app.test_local_asr_notice_projector import _ready_provisioning, _settings
from tests.app.test_managed_usage_owner import _owner, _state
from tests.app.test_peer_application import Harness
from tests.app.test_provider_runtime_owner import _runtime_owner
from tests.app.test_ui_provider_runtime_adapter import _adapter


@pytest.mark.parametrize(
    "case,code", [("timeout", "timeout"), ("http", "http_error"), ("json", "response_invalid")]
)
async def test_real_metadata_boundary_preserves_safe_failure(monkeypatch, case, code):
    def response(request):
        if case == "timeout":
            raise httpx.ReadTimeout("secret-token proxy-password", request=request)
        return httpx.Response(401 if case == "http" else 200, text="secret-token")

    monkeypatch.setattr(
        openrouter.network_clients,
        "external_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(response)),
    )
    with pytest.raises(OpenRouterMetadataError) as caught:
        await OpenRouterLLMProvider.fetch_key_metadata("secret-key", strict=True)
    assert caught.value.code == code
    assert "secret" not in str(caught.value)
    if case == "http":
        assert caught.value.http_status == 401


async def test_usage_timeout_has_terminal_chinese_ui_and_safe_log(caplog):
    async def hang():
        await asyncio.Event().wait()

    views = []
    owner = _owner([_state()], metadata_fetcher=hang, views=views)
    owner.metadata_timeout_s = 0.01
    previous = get_locale()
    set_locale("zh-CN")
    try:
        with caplog.at_level(logging.INFO):
            await owner.refresh()
        assert views[0].usage_status == "checking"
        assert views[-1].usage_status == "request_failed"
        bar = ManagedTrialUsageBar()
        bar.set_status(views[-1].usage_status)
        assert bar._remaining_text.value == "用量查询失败，请查看日志"
        assert "[ManagedUsage]" in caplog.text and "code=timeout" in caplog.text
    finally:
        set_locale(previous)
        await owner.close()


@pytest.mark.parametrize(
    "available,code,status",
    [
        (False, None, "key_missing"),
        (False, "storage_unavailable", "storage_unavailable"),
        (True, None, "metadata_unavailable"),
    ],
)
async def test_usage_unknown_is_not_checking(available, code, status):
    views = []

    async def fetch():
        return ManagedUsageMetadataResult(available, None, code)

    owner = _owner([_state()], metadata_fetcher=fetch, views=views)
    await owner.refresh()
    assert views[-1].usage_status == status
    await owner.close()


async def test_late_status_cannot_restore_checking():
    gates = [asyncio.Event(), asyncio.Event()]
    results = iter([None, OpenRouterKeyMetadata(10, 8, 2)])
    started = 0

    class Service:
        async def refresh_managed_status(self):
            nonlocal started
            index = started
            started += 1
            await gates[index].wait()
            return ManagedOpenRouterStatusRefreshResult(succeeded=True)

    service = Service()

    async def fetch():
        return ManagedUsageMetadataResult(True, next(results))

    views = []
    owner = _owner(
        [_state()], metadata_fetcher=fetch, release_service_provider=lambda: service, views=views
    )
    await owner.refresh()
    await asyncio.sleep(0)
    await owner.refresh()
    await asyncio.sleep(0)
    gates[1].set()
    await asyncio.sleep(0)
    gates[0].set()
    await asyncio.sleep(0)
    assert views[-1].remaining_percent == 80
    assert views[-1].usage_status == "ready"
    await owner.close()


async def test_old_metadata_cannot_replace_new_result():
    gate = asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            await gate.wait()
            return ManagedUsageMetadataResult(True, OpenRouterKeyMetadata(10, 1, 9))
        return ManagedUsageMetadataResult(True, OpenRouterKeyMetadata(10, 8, 2))

    views = []
    owner = _owner([_state()], metadata_fetcher=fetch, views=views)
    first = asyncio.create_task(owner.refresh())
    await asyncio.sleep(0)
    await owner.refresh()
    gate.set()
    await first
    assert owner.remaining_percent == views[-1].remaining_percent == 80
    await owner.close()


@pytest.mark.parametrize("stage", ["readiness", "translation_demand", "provider_prepare"])
async def test_peer_start_failure_is_terminal(stage):
    from types import SimpleNamespace

    harness = Harness()
    harness.settings.ui.peer_translation_eula_accepted = True
    owner = harness.owner()

    async def fail(*_):
        raise RuntimeError("secret-token")

    if stage == "readiness":
        owner.ensure_local_ready = fail
    elif stage == "translation_demand":
        owner.translation_demand_sink = fail
    else:
        owner._runtime = SimpleNamespace(prepare_provider=fail)
    with pytest.raises(RuntimeError):
        await owner.set_enabled(True)
    assert not owner.activation_starting and not owner.model_loading
    seen = []
    LocalASRNoticeProjector(
        settings_provider=lambda: replace(_settings(), peer_requested=True),
        self_capture_provider=lambda: None,
        peer=lambda: owner,
        provisioning_snapshot=_ready_provisioning,
        sink=lambda **kw: seen.append(kw),
    ).sync()
    assert seen[-1]["status"] == "start_failed"
    assert any("code=activation_failed" in str(event) for event in harness.events)
    assert "secret-token" not in str(harness.events)


async def test_saved_key_verification_can_continue_after_runtime_exception(caplog):
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            translation=replace(
                settings.intent.translation,
                model="deepseek_v4_flash_41",
                connection="official_byok",
            ),
        ),
    )
    adapter = _adapter(
        settings,
        change_secret=AsyncMock(return_value=True),
        apply=AsyncMock(side_effect=RuntimeError("secret-token")),
    )
    notices = []
    adapter.runtime_warning_sink = lambda: notices.append("runtime_apply_failed")
    assert await adapter.persist_provider_secret_change("deepseek_api_key", "secret-key")
    assert notices == ["runtime_apply_failed"]
    assert "key_saved_runtime_failed" in caplog.text
    assert "secret-token" not in caplog.text and "secret-key" not in caplog.text


async def test_missing_llm_remains_recoverable_after_other_runtime_failure():
    events = []
    owner = _runtime_owner(
        events=events,
        state=ProviderRuntimeState(True, False, True, True, True, False),
        current="same",
        cache=(("self", "same"), ("peer",), ("llm", "same")),
    )
    owner.refresh_self_stt = AsyncMock(side_effect=RuntimeError("stt failure"))
    with pytest.raises(RuntimeError):
        await owner.apply("same", ProviderRuntimeApplyPlan(True, False, True, False))
    assert events[-1] == "retry"
    assert owner.build_plan("same", force_rebuild_llm=False).should_rebuild_llm


async def test_managed_failure_survives_log_view_close_and_file_flush(tmp_path):
    from puripuly_heart.app.services.managed_usage import ManagedUsageOwner

    from puripuly_heart.core.runtime_logging import (
        RealtimeLogHandler,
        SessionRuntimeLoggingService,
        configure_main_logging,
    )
    from puripuly_heart.ui.views.logs import LogsView

    logger = logging.getLogger(ManagedUsageOwner.__module__)
    old_handlers, old_level = list(logger.handlers), logger.level
    sinks = configure_main_logging(root_logger=logger, log_dir=tmp_path)
    service = SessionRuntimeLoggingService(
        root_logger=logger,
        session_logger=logging.getLogger("test.managed.failure"),
        sinks=sinks,
        ui_handler_factory=RealtimeLogHandler,
    )
    view = LogsView()
    service.attach_realtime_sink(view)

    async def fail():
        raise httpx.ReadTimeout("123456789 secret-token secret-key proxy-password")

    owner = _owner([_state()], metadata_fetcher=fail)
    try:
        await owner.refresh()
        view._flush_logs()
        assert "[ManagedUsage]" in view._log_text.value and "code=timeout" in view._log_text.value
        service.detach_realtime_sink()
        view.will_unmount()
        service.attach_realtime_sink(view)
        view._flush_logs()
        assert "code=timeout" in view._log_text.value
    finally:
        await owner.close()
        service.detach_realtime_sink()
        sinks.close()
        for handler in tuple(logger.handlers):
            if handler not in old_handlers:
                logger.removeHandler(handler)
        logger.setLevel(old_level)
    persisted = sinks.log_file.read_text(encoding="utf-8")
    assert "code=timeout" in persisted and "[WARNING]" in persisted
    for secret in ("123456789", "secret-token", "secret-key", "proxy-password"):
        assert secret not in persisted


async def test_usage_cancel_finishes_checking_without_error(caplog):
    entered = asyncio.Event()

    async def hang():
        entered.set()
        await asyncio.Event().wait()

    views = []
    owner = _owner([_state()], metadata_fetcher=hang, views=views)
    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(owner.refresh())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert views[-1].usage_status == "unavailable"
    assert "result=cancelled" in caplog.text
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)
    await owner.close()


async def test_cancelled_old_peer_start_does_not_clear_new_start():
    harness = Harness(readiness_entered=asyncio.Event(), readiness_release=asyncio.Event())
    harness.settings.ui.peer_translation_eula_accepted = True
    owner = harness.owner()
    first = asyncio.create_task(owner.set_enabled(True))
    await harness.readiness_entered.wait()
    second = asyncio.create_task(owner.set_enabled(True))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert owner.activation_starting and not owner.activation_failed
    harness.readiness_release.set()
    await second


async def test_cpu_initialization_logs_last_stage_without_exception_text(tmp_path, caplog):
    from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend

    backend = LocalQwenSherpaSTTBackend(tmp_path, stream_label="peer")
    backend._validate_runtime_assets = lambda: None

    def fail():
        raise RuntimeError("secret-token secret-key")

    backend._create_recognizer = fail
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        await backend._ensure_recognizer()
    assert "stage=asset_validation result=success" in caplog.text
    assert "stage=recognizer_create result=failed channel=peer" in caplog.text
    assert "secret-token" not in caplog.text and "secret-key" not in caplog.text
    await backend.close()
