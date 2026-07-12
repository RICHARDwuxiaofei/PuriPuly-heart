from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.ports.post_commit_runtime import (
    RuntimeMutationProvenance,
    RuntimeOperationalSnapshot,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.post_commit_runtime import (
    PostCommitRuntimePlanBuilder,
    PostCommitRuntimeTransactionOwner,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import (
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    RuntimeApplyResult,
)


def _receipt(revision: str, settings: AppSettingsVNext | None = None):
    return SettingsCommitReceipt(settings or AppSettingsVNext(), revision, "reason", "correlation")


def _facts(**changes) -> RuntimeOperationalSnapshot:  # noqa: ANN003
    values = {
        "translation_enabled": True,
        "self_stt_enabled": True,
        "self_stt_running": True,
        "self_stt_staged": False,
        "peer_stt_enabled": False,
        "peer_stt_running": False,
        "peer_stt_staged": True,
        "llm_available": True,
        "llm_retry_pending": False,
        "self_stt_available": True,
        "self_stt_retry_pending": False,
        "peer_stt_available": True,
        "peer_stt_retry_pending": False,
    }
    values.update(changes)
    return RuntimeOperationalSnapshot(**values)


def _provenance(surface="stt_language_audio", cause="settings_surface"):
    return RuntimeMutationProvenance(surface, cause, "reason", "correlation")


def _changed_after() -> SettingsCommitReceipt:
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            translation=replace(settings.intent.translation, concurrency_limit=11),
        ),
    )
    return _receipt("after", settings)


def _plan(*, surface="stt_language_audio", cause="settings_surface", facts=None):  # noqa: ANN001
    return PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver()).build(
        before=_receipt("before"),
        after=_changed_after(),
        provenance=_provenance(surface, cause),
        operational=facts or _facts(),
    )


class Provider:
    def __init__(self, result=None, failure: BaseException | None = None) -> None:  # noqa: ANN001
        self.result = result or RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)
        self.failure = failure
        self.calls = 0

    async def activate_providers(self, request, directive):  # noqa: ANN001, ANN202
        _ = (request, directive)
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.result


class Sync:
    def __init__(self, *, fail_at: str | None = None, cancel_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.cancel_at = cancel_at
        self.calls: list[str] = []

    async def synchronize_runtime(
        self, request, directive, **context
    ):  # noqa: ANN001, ANN003, ANN202
        _ = (request, context)
        self.calls.append(directive.operation)
        if directive.operation == self.cancel_at:
            raise asyncio.CancelledError
        if directive.operation == self.fail_at:
            raise RuntimeError("raw secret")
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("translation_provider", ("translation_policy", "dashboard_retry_facts")),
        (
            "stt_language_audio",
            ("language_runtime_clear", "audio_vad", "dashboard_retry_facts"),
        ),
        ("overlay_osc_output", ("overlay_osc", "dashboard_retry_facts")),
        (
            "ui_prompt_clipboard_state",
            ("locale_ui_projection", "prompt_clipboard", "dashboard_retry_facts"),
        ),
        ("openrouter_pkce", ("translation_policy", "dashboard_retry_facts")),
    ],
)
def test_a_d_surface_responsibility_matrix_is_mandatory_and_deterministic(
    surface: str, expected: tuple[str, ...]
) -> None:
    first = _plan(
        surface=surface, cause="pkce" if surface == "openrouter_pkce" else "settings_surface"
    )
    second = _plan(
        surface=surface, cause="pkce" if surface == "openrouter_pkce" else "settings_surface"
    )
    assert first == second
    assert tuple(item.operation for item in first.synchronization) == expected


def test_operational_snapshot_is_facts_only_and_derives_clear_and_ingress_policy() -> None:
    plan = _plan(
        facts=_facts(
            translation_enabled=False,
            self_stt_enabled=False,
            self_stt_running=False,
            self_stt_staged=False,
            peer_stt_enabled=True,
            peer_stt_running=False,
            peer_stt_staged=True,
        )
    )
    assert plan.providers.llm == "clear"
    assert plan.providers.self_stt == "clear"
    assert plan.providers.self_stt_policy == "inactive"
    assert plan.providers.peer_stt_policy == "staged"


def test_pkce_and_managed_causes_derive_force_and_c4_without_commands() -> None:
    equal_before = _receipt("before")
    equal_after = _receipt("after")
    builder = PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver())
    pkce = builder.build(
        before=equal_before,
        after=equal_after,
        provenance=_provenance("openrouter_pkce", "pkce"),
        operational=_facts(),
    )
    managed = builder.build(
        before=equal_before,
        after=equal_after,
        provenance=_provenance("managed_legacy", "managed_legacy"),
        operational=_facts(),
    )
    assert pkce.providers.llm == "replace"
    assert managed.providers.llm == "replace"
    assert managed.providers.managed_legacy == "managed_release_rebuild_c4"


def test_provenance_must_match_receipt_and_pkce_surface() -> None:
    builder = PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver())
    with pytest.raises(ValueError, match="match the committed receipt"):
        builder.build(
            before=None,
            after=_receipt("after"),
            provenance=RuntimeMutationProvenance(
                "translation_provider", "settings_surface", "wrong", "correlation"
            ),
            operational=_facts(),
        )


@pytest.mark.parametrize(
    ("surface", "cause"),
    [
        ("openrouter_pkce", "settings_surface"),
        ("openrouter_pkce", "managed_legacy"),
        ("managed_legacy", "settings_surface"),
        ("managed_legacy", "pkce"),
        ("translation_provider", "pkce"),
        ("translation_provider", "managed_legacy"),
    ],
)
def test_cause_surface_pairs_are_enforced_bidirectionally(surface: str, cause: str) -> None:
    with pytest.raises(ValueError, match="incompatible"):
        PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver()).build(
            before=_receipt("before"),
            after=_receipt("after"),
            provenance=_provenance(surface, cause),  # type: ignore[arg-type]
            operational=_facts(),
        )


def test_authoritative_receipts_facts_and_nondefault_values_reach_directives() -> None:
    settings = AppSettingsVNext()
    intent = settings.intent
    intent = replace(
        intent,
        languages=replace(
            intent.languages,
            source_language="ja",
            target_language="fr",
            peer_source_language="ko",
            peer_target_language="de",
        ),
        audio=replace(
            intent.audio,
            input_host_api="mme",
            input_device="Mic 7",
            ring_buffer_ms=987,
        ),
        desktop_audio=replace(
            intent.desktop_audio,
            output_device="Speakers 9",
            vad_speech_threshold=0.77,
            vad_hangover_ms=876,
            vad_pre_roll_ms=345,
        ),
        stt=replace(intent.stt, vad_speech_threshold=0.66),
        overlay=replace(
            intent.overlay,
            target="desktop",
            show_translation=False,
            show_peer_original=False,
        ),
        osc=replace(
            intent.osc,
            host="192.0.2.8",
            port=9012,
            chatbox_address="/custom/chatbox",
            chatbox_send=False,
            chatbox_clear=True,
            chatbox_max_chars=99,
            vrc_mic_intercept=True,
            chatbox_include_source=True,
        ),
        ui=replace(intent.ui, locale="ja"),
        prompts=replace(intent.prompts, system_prompt="nondefault prompt"),
        clipboard=replace(intent.clipboard, auto_translate_enabled=True),
    )
    after = _receipt("after", replace(settings, intent=intent))
    facts = _facts(
        llm_available=False,
        llm_retry_pending=True,
        self_stt_available=False,
        self_stt_retry_pending=True,
        peer_stt_available=False,
        peer_stt_retry_pending=True,
    )
    builder = PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver())
    plans = {
        surface: builder.build(
            before=_receipt("before"),
            after=after,
            provenance=_provenance(surface),  # type: ignore[arg-type]
            operational=facts,
        )
        for surface in (
            "stt_language_audio",
            "overlay_osc_output",
            "ui_prompt_clipboard_state",
        )
    }
    stt = plans["stt_language_audio"]
    assert stt.before == _receipt("before")
    assert stt.after is after
    assert stt.operational is facts
    language, audio, dashboard = stt.synchronization
    assert (language.source_language, language.peer_target_language) == ("ja", "de")
    assert (audio.input_host_api, audio.input_device, audio.output_device) == (
        "mme",
        "Mic 7",
        "Speakers 9",
    )
    assert (audio.ring_buffer_ms, audio.self_vad_threshold) == (987, 0.66)
    assert dashboard.llm_available is False
    assert dashboard.peer_stt_retry_pending is True
    overlay = plans["overlay_osc_output"].synchronization[0]
    assert (overlay.overlay_target, overlay.osc_host, overlay.osc_port) == (
        "desktop",
        "192.0.2.8",
        9012,
    )
    locale, prompt, _dashboard = plans["ui_prompt_clipboard_state"].synchronization
    assert locale.locale == "ja"
    assert prompt.system_prompt == "nondefault prompt"
    assert prompt.clipboard_auto_translate_enabled is True
    assert (stt.providers.llm, stt.providers.self_stt, stt.providers.peer_stt) == (
        "replace",
        "replace",
        "replace",
    )
    with pytest.raises(ValueError, match="incompatible"):
        builder.build(
            before=None,
            after=_receipt("after"),
            provenance=_provenance("translation_provider", "pkce"),
            operational=_facts(),
        )


@pytest.mark.asyncio
async def test_no_sync_and_every_sync_exploits_are_impossible() -> None:
    provider, sync = Provider(), Sync()
    plan = _plan(surface="stt_language_audio")
    result = await PostCommitRuntimeTransactionOwner(provider, sync).apply(plan)
    assert result.transaction.status == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
    assert sync.calls == [item.operation for item in plan.synchronization]
    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_at", "completed", "skipped"),
    [
        (
            "language_runtime_clear",
            ("provider_activation",),
            ("audio_vad", "dashboard_retry_facts"),
        ),
        (
            "audio_vad",
            ("provider_activation", "language_runtime_clear"),
            ("dashboard_retry_facts",),
        ),
        (
            "dashboard_retry_facts",
            ("provider_activation", "language_runtime_clear", "audio_vad"),
            (),
        ),
    ],
)
async def test_sequential_partial_failure_keeps_completed_and_skips_remaining(
    fail_at: str, completed: tuple[str, ...], skipped: tuple[str, ...]
) -> None:
    sync = Sync(fail_at=fail_at)
    result = await PostCommitRuntimeTransactionOwner(Provider(), sync).apply(_plan())
    assert result.transaction.status == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED
    assert result.completed == completed
    assert result.failed == fail_at
    assert result.skipped == skipped
    assert result.reconciliation_required is True
    assert result.transaction.diagnostics is not None
    assert result.transaction.diagnostics.fields["reconciliation_required"] is True
    assert "raw secret" not in repr(result)


@pytest.mark.asyncio
async def test_diagnosticless_port_failure_is_synthesized_with_safe_diagnostics() -> None:
    failure = RuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)
    result = await PostCommitRuntimeTransactionOwner(Provider(result=failure), Sync()).apply(
        _plan()
    )
    assert result.transaction.status == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED
    assert result.transaction.message is not None
    assert result.transaction.diagnostics is not None
    assert result.transaction.diagnostics.operation == "apply_provider_runtime"
    assert result.transaction.diagnostics.content_policy == "metadata_only"


@pytest.mark.asyncio
async def test_cancellation_propagates_and_no_operations_run_after_cancelled_step() -> None:
    sync = Sync(cancel_at="audio_vad")
    with pytest.raises(asyncio.CancelledError):
        await PostCommitRuntimeTransactionOwner(Provider(), sync).apply(_plan())
    assert sync.calls == ["language_runtime_clear", "audio_vad"]


def test_boundary_has_no_controller_or_concrete_factory_dependency() -> None:
    root = Path(__file__).parents[2] / "src" / "puripuly_heart" / "app"
    source = (root / "ports" / "post_commit_runtime.py").read_text(encoding="utf-8")
    source += (root / "services" / "post_commit_runtime.py").read_text(encoding="utf-8")
    assert "ui.controller" not in source
    assert "GuiController" not in source
    assert "resolved_runtime_resource_factory" not in source
