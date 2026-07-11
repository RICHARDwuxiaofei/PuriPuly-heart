from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.adapters.resolved_runtime_resource_factory import (
    ResolvedRuntimeResourceFactory,
)
from puripuly_heart.app.ports.runtime_resources import RuntimeResourceReplacementPlan
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.config.resolved import (
    ResolvedCredentialRequirement,
    ResolvedLLMFallbackPlan,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.clock import FakeClock


class Secrets:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def get(self, key: str) -> str | None:
        self.reads.append(key)
        return "raw-secret-value"


class Diagnostics:
    def __init__(self) -> None:
        self.cleanup_failures: list[tuple[str, str]] = []

    def detailed_enabled(self) -> bool:
        return True

    def record_cleanup_failure(self, *, slot: str, exception_class: str) -> None:
        self.cleanup_failures.append((slot, exception_class))


class BorrowedService:
    def __init__(self) -> None:
        self.close_calls = 0

    async def prepare_for_translation(self) -> object:
        return object()

    async def close(self) -> None:
        self.close_calls += 1


class BorrowedDelegate:
    def __init__(self) -> None:
        self.close_calls = 0

    def managed_delegate_ready(self) -> object:
        return object()

    async def close(self) -> None:
        self.close_calls += 1


class Resource:
    def __init__(self, label: str, *, close_failure: BaseException | None = None) -> None:
        self.label = label
        self.close_failure = close_failure
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_failure is not None:
            raise self.close_failure


class LLMBuilder:
    def __init__(self, resource: Resource | None = None) -> None:
        self.resource = resource or Resource("llm")
        self.calls: list[tuple[object, dict[str, object]]] = []

    def build_llm(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN202
        self.calls.append((config, kwargs))
        return self.resource


class STTBuilder:
    def __init__(self, *, fail_channel: str | None = None) -> None:
        self.fail_channel = fail_channel
        self.calls: list[tuple[object, dict[str, object]]] = []
        self.resources: dict[str, Resource] = {}

    def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN202
        self.calls.append((config, kwargs))
        if config.channel == self.fail_channel:
            raise RuntimeError(f"{config.channel} build failed raw-secret-value")
        resource = Resource(config.channel)
        self.resources[config.channel] = resource
        return resource


class SharedSTTBuilder:
    def __init__(self, resource: Resource) -> None:
        self.resource = resource

    def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN202
        _ = (config, kwargs)
        return self.resource


class SequencedSTTBuilder:
    def __init__(self, self_resource: Resource, peer_failure: BaseException) -> None:
        self.self_resource = self_resource
        self.peer_failure = peer_failure

    def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN202
        _ = kwargs
        if config.channel == "peer":
            raise self.peer_failure
        return self.self_resource


def _config():
    settings = AppSettingsVNext()
    config = CanonicalRuntimeConfigResolver().resolve(
        SettingsCommitReceipt(settings, "1", "test", "correlation")
    )
    primary = replace(
        config.llm.primary,
        model="primary-nondefault",
        credential=ResolvedCredentialRequirement("secret_store", True, "llm.primary.ref"),
        base_url="https://primary.invalid/v1",
        service_endpoint="https://service.invalid",
        region="region-a",
        routing_mode="latency",
        provider_routing="provider-a",
        provider_options={"temperature": 0.25, "nested": {"flag": True}},
    )
    fallback_target = replace(
        primary,
        model="fallback-nondefault",
        credential=ResolvedCredentialRequirement("managed", True, "llm.fallback.ref"),
        region="region-b",
    )
    llm = replace(
        config.llm,
        primary=primary,
        fallback=ResolvedLLMFallbackPlan(
            fallback_target,
            timeout_ms=3456,
            loser_grace_ms=87,
            force_managed_wrapper=True,
        ),
        concurrency_limit=9,
    )
    self_stt = replace(
        config.self_stt,
        source_language="ja",
        model="self-model",
        endpoint="wss://self.invalid",
        region="self-region",
        credential=ResolvedCredentialRequirement("secret_store", True, "stt.self.ref"),
        input_host_api="wasapi",
        input_device="self-input",
        output_device="self-output",
        ring_buffer_ms=777,
        drain_timeout_s=4.5,
        vad_speech_threshold=0.73,
        vad_hangover_ms=811,
        vad_pre_roll_ms=233,
        low_latency_enabled=True,
        low_latency_merge_gap_ms=122,
        low_latency_spec_retry_max=4,
        custom_vocabulary_enabled=True,
        custom_terms={"ja": ("固有名詞",)},
        provider_options={"keepalive_interval_s": 11.5},
    )
    peer_stt = replace(
        self_stt,
        channel="peer",
        source_language="ko",
        model="peer-model",
        endpoint="wss://peer.invalid",
        credential=ResolvedCredentialRequirement("secret_store", True, "stt.peer.ref"),
        input_device="peer-input",
        output_device="peer-output",
        custom_terms={"ko": ("고유명사",)},
        provider_options={"enable_language_identification": True},
    )
    return replace(config, llm=llm, self_stt=self_stt, peer_stt=peer_stt)


def _factory(*, llm_builder=None, stt_builder=None, diagnostics=None):  # noqa: ANN001
    secrets = Secrets()
    managed = BorrowedService()
    delegate = BorrowedDelegate()
    factory = ResolvedRuntimeResourceFactory(
        secrets=secrets,
        clock=FakeClock(12.0),
        diagnostics=diagnostics or Diagnostics(),
        llm_builder=llm_builder or LLMBuilder(),
        stt_builder=stt_builder or STTBuilder(),
        runtime_logging=None,
        managed_release_service=managed,
        managed_delegate=delegate,
    )
    return factory, secrets, managed, delegate


@pytest.mark.asyncio
async def test_full_nondefault_matrix_is_forwarded_through_explicit_ports() -> None:
    llm_builder, stt_builder = LLMBuilder(), STTBuilder()
    factory, secrets, managed, delegate = _factory(llm_builder=llm_builder, stt_builder=stt_builder)
    config = _config()

    staged = await factory.build_resources(
        config, RuntimeResourceReplacementPlan("replace", "replace", "replace")
    )

    assert tuple(staged.candidates) == ("llm", "self_stt", "peer_stt")
    assert llm_builder.calls[0][0] == config.llm
    assert [call[0] for call in stt_builder.calls] == [config.self_stt, config.peer_stt]
    llm_dependencies = llm_builder.calls[0][1]
    assert llm_dependencies["secrets"] is secrets
    assert llm_dependencies["managed_release_service"] is managed
    assert llm_dependencies["managed_delegate"] is delegate
    assert all(call[1]["clock"] is factory.clock for call in stt_builder.calls)
    assert secrets.reads == []
    assert "raw-secret-value" not in repr(config)
    assert config.llm.primary.credential.reference == "llm.primary.ref"
    assert config.llm.fallback is not None
    assert config.llm.fallback.target.credential.reference == "llm.fallback.ref"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        RuntimeResourceReplacementPlan("retain", "retain", "retain"),
        RuntimeResourceReplacementPlan("clear", "clear", "clear"),
        RuntimeResourceReplacementPlan("retain", "clear", "retain"),
    ],
)
async def test_retain_and_clear_never_build(plan: RuntimeResourceReplacementPlan) -> None:
    llm_builder, stt_builder = LLMBuilder(), STTBuilder()
    factory, _secrets, managed, delegate = _factory(
        llm_builder=llm_builder, stt_builder=stt_builder
    )

    staged = await factory.build_resources(_config(), plan)

    assert staged.candidates == {}
    assert llm_builder.calls == []
    assert stt_builder.calls == []
    assert managed.close_calls == delegate.close_calls == 0


@pytest.mark.asyncio
async def test_partial_failure_closes_owned_candidates_once_and_preserves_primary() -> None:
    llm = Resource("llm", close_failure=RuntimeError("cleanup secret must not replace primary"))
    diagnostics = Diagnostics()
    factory, _secrets, managed, delegate = _factory(
        llm_builder=LLMBuilder(llm),
        stt_builder=STTBuilder(fail_channel="self"),
        diagnostics=diagnostics,
    )

    with pytest.raises(RuntimeError, match="self build failed raw-secret-value"):
        await factory.build_resources(
            _config(), RuntimeResourceReplacementPlan("replace", "replace", "retain")
        )

    assert llm.close_calls == 1
    assert diagnostics.cleanup_failures == [("llm", "RuntimeError")]
    assert "secret" not in repr(diagnostics.cleanup_failures).lower()
    assert managed.close_calls == delegate.close_calls == 0


@pytest.mark.asyncio
async def test_staged_constructor_failure_settles_every_owned_candidate_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_builder, stt_builder = LLMBuilder(), STTBuilder()
    factory, _secrets, managed, delegate = _factory(
        llm_builder=llm_builder, stt_builder=stt_builder
    )

    def fail_staging(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise ValueError("staging failed")

    monkeypatch.setattr(
        "puripuly_heart.app.adapters.resolved_runtime_resource_factory.StagedRuntimeResources",
        fail_staging,
    )
    with pytest.raises(ValueError, match="staging failed"):
        await factory.build_resources(
            _config(), RuntimeResourceReplacementPlan("replace", "replace", "replace")
        )

    assert llm_builder.resource.close_calls == 1
    assert all(resource.close_calls == 1 for resource in stt_builder.resources.values())
    assert managed.close_calls == delegate.close_calls == 0


@pytest.mark.asyncio
async def test_shared_resource_across_llm_and_stt_slots_reuses_exact_ref_identity() -> None:
    shared = Resource("shared")
    factory, _secrets, managed, delegate = _factory(
        llm_builder=LLMBuilder(shared),
        stt_builder=SharedSTTBuilder(shared),
    )

    staged = await factory.build_resources(
        _config(), RuntimeResourceReplacementPlan("replace", "replace", "replace")
    )

    llm_ref = staged.candidates["llm"]
    assert staged.candidates["self_stt"] is llm_ref
    assert staged.candidates["peer_stt"] is llm_ref
    assert len({ref.identity for ref in staged.candidates.values()}) == 1
    assert shared.close_calls == 0
    assert managed.close_calls == delegate.close_calls == 0


@pytest.mark.asyncio
async def test_shared_partial_candidate_cleanup_closes_resource_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = Resource("shared")
    factory, _secrets, _managed, _delegate = _factory(
        llm_builder=LLMBuilder(shared),
        stt_builder=SharedSTTBuilder(shared),
    )
    monkeypatch.setattr(
        "puripuly_heart.app.adapters.resolved_runtime_resource_factory.StagedRuntimeResources",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("staging failed")),
    )

    with pytest.raises(ValueError, match="staging failed"):
        await factory.build_resources(
            _config(), RuntimeResourceReplacementPlan("replace", "replace", "replace")
        )

    assert shared.close_calls == 1


@pytest.mark.asyncio
async def test_build_cancellation_cleans_owned_candidate_then_reraises_cancellation() -> None:
    llm = Resource("llm")
    factory, _secrets, managed, delegate = _factory(
        llm_builder=LLMBuilder(llm),
        stt_builder=STTBuilder(fail_channel="self"),
    )

    class CancelBuilder:
        def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN202
            _ = (config, kwargs)
            raise asyncio.CancelledError

    factory.stt_builder = CancelBuilder()

    with pytest.raises(asyncio.CancelledError):
        await factory.build_resources(
            _config(), RuntimeResourceReplacementPlan("replace", "replace", "retain")
        )

    assert llm.close_calls == 1
    assert managed.close_calls == delegate.close_calls == 0


@pytest.mark.asyncio
async def test_cleanup_cancellation_and_exception_continue_and_preserve_build_primary() -> None:
    llm = Resource("llm", close_failure=asyncio.CancelledError())
    self_stt = Resource("self", close_failure=RuntimeError("cleanup failed"))
    diagnostics = Diagnostics()
    factory, _secrets, managed, delegate = _factory(
        llm_builder=LLMBuilder(llm),
        stt_builder=SequencedSTTBuilder(self_stt, ValueError("peer primary raw-secret-value")),
        diagnostics=diagnostics,
    )

    with pytest.raises(ValueError, match="peer primary raw-secret-value"):
        await factory.build_resources(
            _config(), RuntimeResourceReplacementPlan("replace", "replace", "replace")
        )

    assert llm.close_calls == self_stt.close_calls == 1
    assert diagnostics.cleanup_failures == [
        ("self_stt", "RuntimeError"),
        ("llm", "CancelledError"),
    ]
    assert managed.close_calls == delegate.close_calls == 0
    pending_cleanup = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and "close" in repr(task.get_coro())
    ]
    assert pending_cleanup == []


def test_factory_has_no_legacy_settings_ui_or_controller_dependency() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "puripuly_heart"
        / "app"
        / "adapters"
        / "resolved_runtime_resource_factory.py"
    ).read_text(encoding="utf-8")
    assert "AppSettings" not in source
    assert "ui.controller" not in source
    assert "GuiController" not in source
    assert "wiring_llm_factory" not in source
    assert "wiring_stt_factory" not in source
