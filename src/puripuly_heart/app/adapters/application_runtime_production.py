from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Awaitable, Callable

from puripuly_heart.app.adapters.peer_provider_ingress import HubPeerProviderIngressAdapter
from puripuly_heart.app.adapters.resolved_runtime_resource_factory import (
    ResolvedRuntimeResourceFactory,
)
from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.post_commit_runtime import (
    RuntimeMutationProvenance,
    RuntimeOperationalSnapshot,
)
from puripuly_heart.app.ports.runtime_resources import (
    ResourceRef,
    RuntimeCommittedSettlementFailure,
    RuntimeResourceInstallCancelled,
    RuntimeResourceReplacementPlan,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.application_runtime_host import (
    ApplicationRuntimeHost,
    ApplicationRuntimeParts,
)
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.resolved_runtime_adapter import ResolvedRuntimeResourceAdapter
from puripuly_heart.config.audio_host_api import normalize_input_host_api
from puripuly_heart.config.process_capture_resolution import (
    ProcessCaptureResolver,
    ProcessCaptureTargetUnavailableError,
)
from puripuly_heart.config.resolved import ResolvedSTTConfig
from puripuly_heart.config.settings_vnext.schema import ProcessCaptureTargetIntent
from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.desktop_source import DesktopLoopbackAudioSource
from puripuly_heart.core.audio.diagnostics import AudioFaultProfile, DiagnosticAudioSource
from puripuly_heart.core.audio.process_identity import (
    PsutilCurrentUserProcessSnapshots,
    PsutilProcessIdentityWatcher,
)
from puripuly_heart.core.audio.process_source import ProcessAudioCaptureSource
from puripuly_heart.core.audio.source import (
    SoundDeviceAudioSource,
    determine_self_mic_capture_channels,
    resolve_sounddevice_input_device,
)
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_LIFECYCLE,
    DIAGNOSTIC_VISIBILITY_BASIC,
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_DEGRADED,
    RUNTIME_APPLY_STATUS_FAILED,
    ErrorDiagnostics,
    RuntimeApplyResult,
)
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.osc.chatbox_paginator import ChatboxPaginator
from puripuly_heart.core.osc.udp_sender import VrchatOscUdpSender
from puripuly_heart.core.runtime.audio_vad_loop import run_audio_vad_loop
from puripuly_heart.core.runtime.logging import RuntimeLoggingService
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntime, PeerRuntimeConfig
from puripuly_heart.core.runtime.self_audio import SelfChannelConfig, SelfSTTChannelOwner
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.bundled import ensure_silero_vad_onnx
from puripuly_heart.core.vad.gating import VadGating, create_peer_vad_gating
from puripuly_heart.core.vad.silero import SileroVadOnnx


@dataclass(slots=True)
class PersistedCanonicalReceiptSource:
    persistence: object
    path: Path

    async def load_receipt(self) -> SettingsCommitReceipt:
        return await asyncio.to_thread(
            self.persistence.load_receipt,
            self.path,
            reason="production_runtime",
            correlation_id=None,
        )


@dataclass(slots=True)
class RuntimeDiagnosticsAdapter:
    runtime_logging: object

    def detailed_enabled(self) -> bool:
        mode = getattr(self.runtime_logging, "mode", None)
        return getattr(mode, "value", mode) == "detailed"

    def record_cleanup_failure(self, *, slot: str, exception_class: str) -> None:
        callback = getattr(self.runtime_logging, "log_detailed", None)
        if callable(callback):
            callback(f"runtime_resource_cleanup_failed slot={slot} exception={exception_class}")


@dataclass(slots=True)
class ProductionAudioRuntimeHooks:
    capture_fault: str = AudioFaultProfile.NONE.value
    stt_fault: str = AudioFaultProfile.NONE.value
    final_suppressed_callback: object | None = None

    def capture_fault_profile(self) -> str:
        return self.capture_fault

    def stt_fault_profile(self) -> str:
        return self.stt_fault

    def final_suppressed(self, notification: object) -> None:
        callback = self.final_suppressed_callback
        if callable(callback):
            callback(notification)


class ResolvedLLMBuilderAdapter:
    def build_llm(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN201
        from puripuly_heart.app.wiring import create_llm_provider_from_resolved_config

        kwargs.pop("managed_delegate", None)
        return create_llm_provider_from_resolved_config(
            config,
            qwen_low_latency_mode=config.qwen_low_latency_mode,
            **kwargs,
        )


def create_production_managed_release_service(
    *,
    settings,
    secrets,
    on_discord_callback_received=None,  # noqa: ANN001
):  # noqa: ANN201
    from puripuly_heart import __version__
    from puripuly_heart.app.wiring import (
        build_managed_identity_state_port,
        build_openrouter_release_runtime_config,
    )
    from puripuly_heart.core.hardware_fingerprint import get_raw_hardware_fingerprint
    from puripuly_heart.core.managed_openrouter_broker_client import (
        HttpManagedOpenRouterBrokerClient,
    )
    from puripuly_heart.core.managed_openrouter_release import (
        ManagedOpenRouterReleaseService,
        UnavailableManagedOpenRouterReleaseClient,
    )

    try:
        client = HttpManagedOpenRouterBrokerClient(base_url=settings.openrouter.broker_base_url)
    except ValueError:
        client = UnavailableManagedOpenRouterReleaseClient()
    return ManagedOpenRouterReleaseService(
        openrouter_config=build_openrouter_release_runtime_config(settings),
        managed_state=build_managed_identity_state_port(settings, lambda _settings: None),
        secrets=secrets,
        client=client,
        raw_hardware_fingerprint_provider=get_raw_hardware_fingerprint,
        app_version=__version__,
        on_discord_callback_received=on_discord_callback_received,
    )


@dataclass(frozen=True, slots=True)
class ManagedDiscordCallbackEvent:
    payload: object


@dataclass(slots=True)
class ManagedDiscordCallbackOutput:
    subscribers: list[Callable[[ManagedDiscordCallbackEvent], object]]

    def __init__(self) -> None:
        self.subscribers = []

    def subscribe(self, subscriber: Callable[[ManagedDiscordCallbackEvent], object]) -> None:
        if subscriber not in self.subscribers:
            self.subscribers.append(subscriber)

    def publish(self, *payload: object) -> None:
        event = ManagedDiscordCallbackEvent(payload[0] if len(payload) == 1 else payload)
        for subscriber in tuple(self.subscribers):
            try:
                subscriber(event)
            except Exception:
                continue


@dataclass(slots=True)
class PendingCommittedSettlement:
    displaced_providers: list[ResourceRef]
    previous_service: object | None


@dataclass(slots=True)
class ProductionManagedReleaseServiceOwner:
    persistence: object
    state_path: Path
    secrets: object
    service: object | None = None
    signature: tuple[object, ...] | None = None
    receipt: SettingsCommitReceipt | None = None
    closed: bool = False
    callback_output: ManagedDiscordCallbackOutput | None = None
    _lock: asyncio.Lock | None = None
    _staged_service: object | None = None
    _retirement_failures: list[object] | None = None
    _committed_settlements: list[PendingCommittedSettlement] | None = None
    authoritative_receipts: object | None = None
    managed_transaction_port: object | None = None

    def __post_init__(self) -> None:
        self.callback_output = self.callback_output or ManagedDiscordCallbackOutput()
        self._lock = asyncio.Lock()
        self._retirement_failures = []
        self._committed_settlements = []

    def current_service(self):  # noqa: ANN201
        return self.service

    def construction_service(self):  # noqa: ANN201
        return self._staged_service or self.service

    async def replace_runtime(
        self,
        receipt: SettingsCommitReceipt,
        install: Callable[[], Awaitable[None]],
    ) -> None:
        assert self._lock is not None
        async with self._lock:
            await self._replace_runtime_locked(receipt, install, replacement_secrets=None)

    async def replace_runtime_with_secret_store(
        self,
        receipt: SettingsCommitReceipt,
        secrets: object,
        install: Callable[[], Awaitable[None]],
    ) -> None:
        assert self._lock is not None
        async with self._lock:
            await self._replace_runtime_locked(receipt, install, replacement_secrets=secrets)

    async def _replace_runtime_locked(
        self,
        receipt: SettingsCommitReceipt,
        install: Callable[[], Awaitable[None]],
        *,
        replacement_secrets: object | None,
    ) -> None:
        await self._retry_committed_settlements()
        await self._retry_retirements()
        await self._validate_authoritative(receipt)
        settings = self.persistence.legacy_projection(receipt.envelope)
        signature = self._signature(settings, receipt)
        if signature == self.signature and self.service is not None and replacement_secrets is None:
            try:
                await install()
            except RuntimeCommittedSettlementFailure as exc:
                assert self._committed_settlements is not None
                self._committed_settlements.append(
                    PendingCommittedSettlement(list(exc.failed_displaced), None)
                )
                if exc.cancellation_requested:
                    raise asyncio.CancelledError
                raise
            return
        if (
            self.service is not None
            and self.signature is not None
            and signature[:4] == self.signature[:4]
            and replacement_secrets is None
        ):
            from puripuly_heart.app.wiring import (
                build_managed_identity_state_port,
                build_openrouter_release_runtime_config,
            )

            managed_state = build_managed_identity_state_port(settings, self._persist_managed_state)
            previous_state = self.service.managed_state
            previous_config = self.service.openrouter_config
            self.service.managed_state = managed_state
            self.service.openrouter_config = build_openrouter_release_runtime_config(settings)
            previous_receipt = self.receipt
            self.receipt = receipt
            try:
                await install()
            except RuntimeCommittedSettlementFailure as exc:
                assert self._committed_settlements is not None
                self._committed_settlements.append(
                    PendingCommittedSettlement(list(exc.failed_displaced), None)
                )
                self.signature = signature
                if exc.cancellation_requested:
                    raise asyncio.CancelledError
                raise
            except RuntimeResourceInstallCancelled as exc:
                if exc.provider_state_committed:
                    self.signature = signature
                else:
                    self.service.managed_state = previous_state
                    self.service.openrouter_config = previous_config
                    self.receipt = previous_receipt
                raise
            except BaseException:
                self.service.managed_state = previous_state
                self.service.openrouter_config = previous_config
                self.receipt = previous_receipt
                raise
            self.signature = signature
            return
        candidate = create_production_managed_release_service(
            settings=settings,
            secrets=replacement_secrets or self.secrets,
            on_discord_callback_received=self.callback_output.publish,
        )
        bind_transaction = getattr(candidate, "bind_transaction_port", None)
        if callable(bind_transaction) and self.managed_transaction_port is not None:
            bind_transaction(self.managed_transaction_port)
        candidate.managed_state._persist = self._persist_managed_state
        self._staged_service = candidate
        previous_receipt = self.receipt
        self.receipt = receipt
        try:
            await install()
        except RuntimeCommittedSettlementFailure as exc:
            previous = self.service
            self.service = candidate
            self.signature = signature
            self.receipt = receipt
            self.closed = False
            if replacement_secrets is not None:
                self.secrets = replacement_secrets
            self._staged_service = None
            assert self._committed_settlements is not None
            self._committed_settlements.append(
                PendingCommittedSettlement(list(exc.failed_displaced), previous)
            )
            if exc.cancellation_requested:
                raise asyncio.CancelledError
            raise
        except RuntimeResourceInstallCancelled as exc:
            if not exc.provider_state_committed:
                self._staged_service = None
                self.receipt = previous_receipt
                await self._close_staged(candidate)
                raise
            previous = self.service
            self.service = candidate
            self.signature = signature
            self.receipt = receipt
            self.closed = False
            if replacement_secrets is not None:
                self.secrets = replacement_secrets
            self._staged_service = None
            if previous is not None and previous is not candidate:
                await self._retire(previous)
            raise
        except BaseException:
            self._staged_service = None
            self.receipt = previous_receipt
            await self._close_staged(candidate)
            raise
        previous = self.service
        self.service = candidate
        self.signature = signature
        self.receipt = receipt
        self.closed = False
        if replacement_secrets is not None:
            self.secrets = replacement_secrets
        self._staged_service = None
        if previous is not None and previous is not candidate:
            await self._retire(previous)

    @staticmethod
    def _signature(settings, receipt: SettingsCommitReceipt) -> tuple[object, ...]:  # noqa: ANN001
        return (
            settings.openrouter.broker_base_url,
            settings.openrouter.selected_source,
            settings.openrouter.selection_alias,
            settings.translation.connection,
            receipt.envelope.state.managed_connection,
        )

    async def synchronize(self, receipt: SettingsCommitReceipt) -> None:
        async def no_install() -> None:
            return None

        await self.replace_runtime(receipt, no_install)

    async def _close_staged(self, service: object) -> None:
        try:
            await service.close()  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            self._queue_retirement(service)
            raise
        except BaseException:
            self._queue_retirement(service)

    async def _retire(self, service: object) -> None:
        try:
            await service.close()  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            self._queue_retirement(service)
            raise
        except BaseException:
            self._queue_retirement(service)

    def _queue_retirement(self, service: object) -> None:
        if self._retirement_failures is None:
            return
        if not any(pending is service for pending in self._retirement_failures):
            self._retirement_failures.append(service)

    async def _retry_retirements(self) -> None:
        pending = list(self._retirement_failures or ())
        if self._retirement_failures is not None:
            self._retirement_failures.clear()
        for index, service in enumerate(pending):
            try:
                await self._retire(service)
            except asyncio.CancelledError:
                for unprocessed in pending[index + 1 :]:
                    self._queue_retirement(unprocessed)
                raise

    async def _retry_committed_settlements(self) -> None:
        pending = list(self._committed_settlements or ())
        if self._committed_settlements is not None:
            self._committed_settlements.clear()
        for index, settlement in enumerate(pending):
            remaining: list[ResourceRef] = []
            cancellation: asyncio.CancelledError | None = None
            for provider_index, ref in enumerate(settlement.displaced_providers):
                try:
                    await ref.resource.close()
                except asyncio.CancelledError as exc:
                    cancellation = exc
                    remaining.extend(settlement.displaced_providers[provider_index:])
                    break
                except BaseException:
                    remaining.append(ref)
            if remaining:
                assert self._committed_settlements is not None
                self._committed_settlements.append(
                    PendingCommittedSettlement(remaining, settlement.previous_service)
                )
                self._committed_settlements.extend(pending[index + 1 :])
                if cancellation is not None:
                    raise cancellation
                raise RuntimeError("committed provider settlement failed")
            if settlement.previous_service is not None:
                await self._retire(settlement.previous_service)

    async def _validate_authoritative(self, receipt: SettingsCommitReceipt) -> None:
        if self.authoritative_receipts is None:
            return
        latest = await self.authoritative_receipts.load_receipt()
        if latest.revision != receipt.revision or latest.envelope != receipt.envelope:
            raise RuntimeError("stale authoritative runtime receipt")

    def _persist_managed_state(self, settings) -> None:  # noqa: ANN001
        from puripuly_heart.app.adapters.canonical_state_repository import (
            CanonicalStateRevisionConflict,
        )
        from puripuly_heart.app.wiring_composition import create_canonical_state_repositories
        from puripuly_heart.config.settings_vnext.schema import ManagedConnectionState

        receipt = self.receipt
        if receipt is None:
            raise RuntimeError("managed release state has no authoritative receipt")
        managed = settings.managed_identity
        repository = create_canonical_state_repositories(self.state_path).operational_state
        for attempt in range(2):
            snapshot = repository.load()
            current_ack = snapshot.value.managed_connection
            managed_connection = ManagedConnectionState(
                installation_id=managed.installation_id,
                release_token=managed.release_token,
                release_token_expires_at=managed.release_token_expires_at,
                verified_hardware_hash=managed.verified_hardware_hash,
                verified_hardware_hash_salt_version=managed.verified_hardware_hash_salt_version,
                active_managed_credential_ref=managed.active_managed_credential_ref,
                active_managed_expires_at=managed.active_managed_expires_at,
                founder_letter_seen_credential_ref=managed.founder_letter_seen_credential_ref,
                referral_id=managed.referral_id,
                local_managed_claim_sources=tuple(managed.local_managed_claim_sources),
                pending_delivery_ack_source=current_ack.pending_delivery_ack_source,
                pending_delivery_ack_delivery_id=current_ack.pending_delivery_ack_delivery_id,
                pending_delivery_ack_managed_credential_ref=(
                    current_ack.pending_delivery_ack_managed_credential_ref
                ),
                pending_delivery_ack_expires_at=current_ack.pending_delivery_ack_expires_at,
                pending_delivery_ack_delivered=current_ack.pending_delivery_ack_delivered,
            )
            try:
                repository.save(
                    replace(
                        snapshot.value,
                        managed_connection=managed_connection,
                    ),
                    expected_revision=snapshot.revision,
                )
                return
            except CanonicalStateRevisionConflict:
                if attempt:
                    raise

    async def close(self) -> None:
        if self.closed:
            return
        await self._retry_committed_settlements()
        await self._retry_retirements()
        if self._retirement_failures:
            raise RuntimeError("managed release service retirement failed")
        service = self.service
        self.service = None
        if service is not None:
            await self._retire(service)
        if self._retirement_failures:
            raise RuntimeError("managed release service retirement failed")
        self.closed = True


@dataclass(slots=True)
class ResolvedSTTBuilderAdapter:
    hooks: object | None = None

    def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN201
        from puripuly_heart.app.wiring import create_stt_backend_from_resolved_config

        backend = create_stt_backend_from_resolved_config(
            config,
            secrets=kwargs["secrets"],
            diagnostics_enabled=kwargs["diagnostics"].detailed_enabled,
        )
        notification = None if self.hooks is None else self.hooks.final_suppressed
        fault_profile = None if self.hooks is None else self.hooks.stt_fault_profile
        return ManagedSTTProvider(
            backend=backend,
            sample_rate_hz=config.sample_rate_hz,
            stt_provider_name=config.provider,
            channel=config.channel,
            clock=kwargs["clock"],
            reset_deadline_s=300.0,
            drain_timeout_s=config.drain_timeout_s,
            bridging_ms=config.ring_buffer_ms,
            runtime_logging=kwargs.get("runtime_logging"),
            on_final_transcript_suppressed=notification,
            stt_input_fault_profile_provider=fault_profile,
        )


@dataclass(slots=True)
class RuntimePolicyEpoch:
    self_intent_generation: int = 0
    peer_intent_generation: int = 0


@dataclass(slots=True)
class ManagedAwareResolvedRuntimeAdapter:
    runtime: ResolvedRuntimeResourceAdapter
    managed_release_owner: ProductionManagedReleaseServiceOwner
    provider_settlement_owner: GeneralProviderSettlementOwner | None = None

    def __post_init__(self) -> None:
        if self.provider_settlement_owner is None:
            self.provider_settlement_owner = GeneralProviderSettlementOwner()

    async def replace_runtime_with_plan(self, request, plan) -> None:  # noqa: ANN001
        assert self.provider_settlement_owner is not None
        await self.provider_settlement_owner.retry()

        async def install() -> None:
            guarded = getattr(self.runtime, "replace_runtime_with_plan_guarded", None)
            if callable(guarded):
                await guarded(
                    request,
                    plan,
                    lambda: self.managed_release_owner._validate_authoritative(request.receipt),
                )
                return
            await self.runtime.replace_runtime_with_plan(request, plan)

        if plan.llm == "replace" and request.receipt is not None:
            await self.managed_release_owner.replace_runtime(request.receipt, install)
            return
        try:
            await install()
        except RuntimeCommittedSettlementFailure as exc:
            self.provider_settlement_owner.adopt(exc.failed_displaced)
            if exc.cancellation_requested:
                raise asyncio.CancelledError
            raise

    async def replace_runtime_with_secret_store(
        self,
        receipt,
        secrets,
        request,
        plan,  # noqa: ANN001
    ) -> None:
        assert self.provider_settlement_owner is not None
        await self.provider_settlement_owner.retry()

        async def install() -> None:
            factory = getattr(self.runtime, "factory", None)
            old_secrets = getattr(factory, "secrets", None)
            if factory is not None and hasattr(factory, "secrets"):
                factory.secrets = secrets
            guarded = getattr(self.runtime, "replace_runtime_with_plan_guarded", None)
            try:
                if callable(guarded):
                    await guarded(
                        request,
                        plan,
                        lambda: self.managed_release_owner._validate_authoritative(receipt),
                    )
                    return
                await self.runtime.replace_runtime_with_plan(request, plan)
            except RuntimeCommittedSettlementFailure:
                raise
            except RuntimeResourceInstallCancelled as exc:
                if not exc.provider_state_committed and factory is not None:
                    factory.secrets = old_secrets
                raise
            except BaseException:
                if factory is not None and hasattr(factory, "secrets"):
                    factory.secrets = old_secrets
                raise

        await self.managed_release_owner.replace_runtime_with_secret_store(
            receipt, secrets, install
        )

    async def close(self) -> None:
        assert self.provider_settlement_owner is not None
        await self.provider_settlement_owner.retry()


@dataclass(slots=True)
class GeneralProviderSettlementOwner:
    pending: list[ResourceRef] = field(default_factory=list)

    def adopt(self, refs: tuple[ResourceRef, ...]) -> None:
        for ref in refs:
            if not any(
                pending.identity == ref.identity and pending.resource is ref.resource
                for pending in self.pending
            ):
                self.pending.append(ref)

    async def retry(self) -> None:
        pending = list(self.pending)
        self.pending.clear()
        for index, ref in enumerate(pending):
            try:
                await ref.resource.close()
            except asyncio.CancelledError:
                self.adopt(tuple(pending[index:]))
                raise
            except BaseException:
                self.adopt((ref,))
        if self.pending:
            raise RuntimeError("provider settlement failed")


@dataclass(slots=True)
class SelectiveProviderActivationAdapter:
    runtime: ResolvedRuntimeResourceAdapter
    self_owner: SelfSTTChannelOwner
    peer_owner: PeerChannelRuntime
    epoch: RuntimePolicyEpoch

    async def activate_providers(self, request, directive) -> RuntimeApplyResult:  # noqa: ANN001
        self.epoch.self_intent_generation = self.self_owner.snapshot().intent_generation
        self.epoch.peer_intent_generation = self.peer_owner.policy_snapshot().intent_generation
        plan = RuntimeResourceReplacementPlan(
            directive.llm,
            directive.self_stt,
            directive.peer_stt,
        )
        try:
            await self.runtime.replace_runtime_with_plan(request, plan)
        except RuntimeCommittedSettlementFailure:
            return RuntimeApplyResult(
                RUNTIME_APPLY_STATUS_DEGRADED,
                None,
                ErrorDiagnostics(
                    component="provider_activation",
                    operation="settle_committed_provider_set",
                    code="provider_set_committed_settlement_pending",
                    category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
                    visibility=DIAGNOSTIC_VISIBILITY_BASIC,
                    content_policy=CONTENT_POLICY_METADATA_ONLY,
                    status_code=None,
                    retry_after_ms=None,
                    fields={"committed": True},
                ),
            )
        except Exception:
            return RuntimeApplyResult(
                RUNTIME_APPLY_STATUS_FAILED,
                None,
                ErrorDiagnostics(
                    component="provider_activation",
                    operation="activate_provider_set",
                    code="provider_set_activation_failed",
                    category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
                    visibility=DIAGNOSTIC_VISIBILITY_BASIC,
                    content_policy=CONTENT_POLICY_METADATA_ONLY,
                    status_code=None,
                    retry_after_ms=None,
                    fields={
                        "llm": directive.llm,
                        "self_stt": directive.self_stt,
                        "peer_stt": directive.peer_stt,
                    },
                ),
            )
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)


@dataclass(slots=True)
class HubSelfVadIngress:
    hub: ClientHub

    async def handle_self_vad_event(self, event: object, provider: object) -> None:
        await self.hub.handle_vad_event(event, stt_provider=provider)


@dataclass(slots=True)
class ChannelAwareRuntimeResourceHost:
    hub: ClientHub
    peer: PeerChannelRuntime
    self_owner: SelfSTTChannelOwner
    epoch: RuntimePolicyEpoch

    async def current_runtime_state(self):  # noqa: ANN201
        return await self.hub.current_runtime_state()

    async def install_runtime_resources(self, staged):  # noqa: ANN001, ANN201
        self_resume = None
        if staged.plan.self_stt != "retain":
            self_resume = await self.self_owner.freeze_for_provider_replacement()
        action = staged.plan.peer_stt
        resume = None
        if action != "retain":
            resume = await self.peer.freeze_for_provider_replacement()
            await self.hub.peer_final_runs.cancel_pending()
        try:
            return await self.hub.install_runtime_resources(staged)
        finally:
            if self_resume is not None:
                await self.self_owner.resume_after_provider_replacement(self_resume)
            if resume is not None:
                await self.peer.resume_after_provider_replacement(resume)


@dataclass(slots=True)
class ProductionAudioFactories:
    detailed_enabled: object
    safe_log: object
    hooks: object
    audio_gate: object | None = None
    source_type: object = SoundDeviceAudioSource
    device_resolver: object = resolve_sounddevice_input_device

    def self_source(self, config: SelfChannelConfig):  # noqa: ANN201
        backend = self._backend(config)
        profile = normalize_input_host_api(backend.input_host_api)
        attempts = (
            (
                profile.actual_host_api,
                backend.input_device,
                profile.wasapi_auto_convert,
                profile.wasapi_exclusive,
            ),
            ("", backend.input_device, False, False),
            ("", "", False, False),
        )
        last_error = None
        attempted: set[tuple[object, ...]] = set()
        for host_api, device_name, auto_convert, exclusive in attempts:
            try:
                device = (
                    None
                    if not host_api and not device_name
                    else self.device_resolver(host_api=host_api, device=device_name)
                )
                key = (device, auto_convert, exclusive)
                if key in attempted:
                    continue
                attempted.add(key)
                decision = determine_self_mic_capture_channels(
                    device_idx=device,
                    internal_channels=backend.channels,
                )
                source = self._open_with_channel_fallback(
                    backend,
                    device,
                    decision.preferred_capture_channels,
                    auto_convert,
                    exclusive,
                )
                return DiagnosticAudioSource(
                    source=source,
                    channel_label="self",
                    is_detailed_enabled=self.detailed_enabled,
                    log_detailed=self.safe_log,
                    fault_profile_provider=self.hooks.capture_fault_profile,
                    extra_fields_provider=lambda: self._capture_metadata(source),
                )
            except Exception as exc:
                last_error = exc
        raise RuntimeError("all microphone capture attempts failed") from last_error

    def _open_with_channel_fallback(
        self,
        backend,
        device,
        channels,
        auto_convert,
        exclusive,  # noqa: ANN001
    ):
        try:
            return self.source_type(
                sample_rate_hz=None,
                channels=channels,
                device=device,
                wasapi_auto_convert=auto_convert,
                wasapi_exclusive=exclusive,
            )
        except Exception:
            if channels <= backend.channels:
                raise
            return self.source_type(
                sample_rate_hz=None,
                channels=backend.channels,
                device=device,
                wasapi_auto_convert=auto_convert,
                wasapi_exclusive=exclusive,
            )

    @staticmethod
    def _capture_metadata(source: object) -> dict[str, object]:
        return {
            "queue_drops": getattr(source, "queue_drop_count", 0),
            "callback_statuses": getattr(source, "callback_status_count", 0),
            "last_callback_status": getattr(source, "last_callback_status", None),
            "resolved_device_name": getattr(source, "resolved_device_name", None),
            "resolved_device_index": getattr(source, "resolved_device_index", None),
            "resolved_channels": getattr(source, "opened_channels", None),
            "actual_sample_rate_hz": getattr(source, "actual_sample_rate_hz", None),
            "used_default_fallback": getattr(source, "device", None) is None,
        }

    def self_vad(self, config: SelfChannelConfig):  # noqa: ANN201
        backend = self._backend(config)
        return VadGating(
            engine=SileroVadOnnx(model_path=ensure_silero_vad_onnx()),
            sample_rate_hz=backend.sample_rate_hz,
            ring_buffer_ms=backend.ring_buffer_ms,
            speech_threshold=backend.vad_speech_threshold,
            hangover_ms=backend.vad_hangover_ms,
            diagnostic_event_callback=self.safe_log,
            diagnostics_enabled=self.detailed_enabled,
            diagnostic_label="self",
        )

    async def peer_source(self, config: PeerRuntimeConfig):  # noqa: ANN201
        target = config.capture_target
        if target.kind == "process":
            if target.process_kind == "discord":
                process_target = ProcessCaptureTargetIntent.discord(target.discord_channel or "")
            elif target.process_kind == "vrchat":
                process_target = ProcessCaptureTargetIntent.vrchat(target.executable_identity or "")
            else:
                process_target = ProcessCaptureTargetIntent.generic_executable(
                    target.executable_identity or ""
                )
            resolution = await asyncio.to_thread(
                ProcessCaptureResolver(
                    snapshots=PsutilCurrentUserProcessSnapshots()
                ).resolve_for_start,
                process_target,
            )
            if resolution.identity is None:
                assert resolution.unavailable_reason is not None
                raise ProcessCaptureTargetUnavailableError(resolution.unavailable_reason)
            source = ProcessAudioCaptureSource(
                identity=resolution.identity,
                watcher=PsutilProcessIdentityWatcher(),
            )
        else:
            device_name = target.device_name or config.output_device
            source = DesktopLoopbackAudioSource(device_name=device_name)
        return DesktopPeerPipeline(
            source=source,
            target_sample_rate_hz=config.backend.sample_rate_hz,
            is_detailed_enabled=self.detailed_enabled,
            log_detailed=self.safe_log,
        )

    def peer_vad(self, config: PeerRuntimeConfig, model_path: Path):  # noqa: ANN201
        return create_peer_vad_gating(
            engine=SileroVadOnnx(model_path=model_path),
            sample_rate_hz=config.backend.sample_rate_hz,
            ring_buffer_ms=config.vad_pre_roll_ms,
            speech_threshold=config.vad_threshold,
            hangover_ms=config.vad_hangover_ms,
            diagnostic_event_callback=self.safe_log,
            diagnostics_enabled=self.detailed_enabled,
            diagnostic_label="peer",
        )

    async def self_loop(self, **kwargs) -> None:  # noqa: ANN003
        await run_audio_vad_loop(
            **kwargs,
            channel_label="self",
            is_detailed_enabled=self.detailed_enabled,
            log_detailed=self.safe_log,
            audio_gate=self.audio_gate,
        )

    async def peer_loop(self, **kwargs) -> None:  # noqa: ANN003
        await run_audio_vad_loop(
            **kwargs,
            channel_label="peer",
            is_detailed_enabled=self.detailed_enabled,
            log_detailed=self.safe_log,
        )

    @staticmethod
    def _backend(config: SelfChannelConfig) -> ResolvedSTTConfig:
        if config.backend is None:
            raise RuntimeError("resolved self STT config is required")
        return config.backend


@dataclass(slots=True)
class ProductionRuntimeSynchronization:
    hub: ClientHub
    self_owner: SelfSTTChannelOwner
    peer_owner: PeerChannelRuntime
    epoch: RuntimePolicyEpoch
    dashboard_projection: object | None = None
    overlay_runtime: object | None = None

    def bind_overlay_runtime(self, runtime: object | None) -> None:
        self.overlay_runtime = runtime

    async def synchronize_runtime(
        self,
        request,
        directive,
        *,
        before,
        after,
        operational,  # noqa: ANN001
    ) -> RuntimeApplyResult:
        _ = before
        operation = directive.operation
        if operation == "language_runtime_clear":
            self.hub.source_language = directive.source_language
            self.hub.target_language = directive.target_language
            self.hub.peer_source_language = directive.peer_source_language
            self.hub.peer_target_language = directive.peer_target_language
            self.hub.clear_context()
        elif operation == "translation_policy":
            self.hub.translation_enabled = bool(directive.enabled and self.hub.llm is not None)
            self.hub.clear_context()
        elif operation == "audio_vad":
            resolved = request.config.self_stt
            self.hub.low_latency_mode = resolved.low_latency_enabled
            self.hub.low_latency_merge_gap_ms = resolved.low_latency_merge_gap_ms
            self.hub.low_latency_spec_retry_max = resolved.low_latency_spec_retry_max
            self.hub.hangover_s = resolved.vad_hangover_ms / 1000.0
            self.hub.peer_hangover_s = request.config.peer_stt.vad_hangover_ms / 1000.0
            await self._synchronize_channels(request, operational)
        elif operation == "prompt_clipboard":
            self.hub.system_prompt = directive.system_prompt
            self.hub.clipboard_auto_translate_enabled = directive.clipboard_auto_translate_enabled
        elif operation == "overlay_osc":
            self.hub.chatbox_include_source = directive.chatbox_include_source
            self.hub.runtime_overlay_directive = directive
            apply_overlay = getattr(self.overlay_runtime, "apply_overlay_osc", None)
            if callable(apply_overlay):
                applied = await apply_overlay(directive)
                if not applied:
                    return RuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)
        elif operation == "locale_ui_projection":
            self.hub.runtime_locale = directive.locale
        elif operation == "dashboard_retry_facts":
            provider_snapshot = getattr(self.hub, "provider_state_snapshot", None)
            llm_provider = (
                provider_snapshot().llm.provider
                if callable(provider_snapshot)
                else getattr(self.hub, "llm", None)
            )
            llm_available = llm_provider is not None
            directive = replace(
                directive,
                llm_available=llm_available,
                llm_retry_pending=bool(operational.translation_enabled and not llm_available),
                translation_desired=operational.translation_enabled,
                translation_effective=bool(
                    operational.translation_enabled
                    and getattr(self.hub, "translation_enabled", False)
                    and llm_available
                ),
                settings_revision=after.revision,
            )
            self.hub.runtime_dashboard_facts = directive
            publish = getattr(self.dashboard_projection, "publish_dashboard_runtime_facts", None)
            if callable(publish):
                publish(directive)
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    async def _synchronize_channels(self, request, operational) -> None:  # noqa: ANN001
        self_config = request.config.self_stt
        self_snapshot = self.self_owner.snapshot()
        self_enabled = (
            self_snapshot.intent_enabled
            if self_snapshot.intent_generation != self.epoch.self_intent_generation
            else operational.self_stt_enabled
        )
        if self_enabled:
            await self.self_owner.execute(
                __import__(
                    "puripuly_heart.core.runtime.self_audio", fromlist=["SetSelfSTTEnabled"]
                ).SetSelfSTTEnabled(
                    True,
                    SelfChannelConfig(
                        self_config.sample_rate_hz,
                        (self_config,),
                        self_config.provider == "local_qwen",
                        self_config,
                    ),
                    record_intent=False,
                )
            )
        else:
            await self.self_owner.freeze_for_provider_replacement()
        peer = request.config.peer_stt
        peer_snapshot = self.peer_owner.policy_snapshot()
        peer_enabled = (
            peer_snapshot.intent_desired_active
            if peer_snapshot.intent_generation != self.epoch.peer_intent_generation
            else operational.peer_stt_enabled
        )
        await self.peer_owner.apply_policy(
            config=PeerRuntimeConfig(
                backend=peer,
                output_device=peer.output_device or "",
                vad_threshold=peer.vad_speech_threshold,
                vad_hangover_ms=peer.vad_hangover_ms,
                vad_pre_roll_ms=peer.vad_pre_roll_ms,
                provider_signature=(peer.provider, peer.credential, peer.provider_options),
                runtime_signature=(peer,),
                capture_target=peer.capture_target,
            ),
            desired_active=peer_enabled,
            record_intent=False,
        )

    async def synchronize_peer(self, request, *, desired_active: bool) -> None:  # noqa: ANN001
        peer = request.config.peer_stt
        await self.peer_owner.apply_policy(
            config=PeerRuntimeConfig(
                backend=peer,
                output_device=peer.output_device or "",
                vad_threshold=peer.vad_speech_threshold,
                vad_hangover_ms=peer.vad_hangover_ms,
                vad_pre_roll_ms=peer.vad_pre_roll_ms,
                provider_signature=(peer.provider, peer.credential, peer.provider_options),
                runtime_signature=(peer,),
                capture_target=peer.capture_target,
            ),
            desired_active=desired_active,
            record_intent=False,
        )


@dataclass(slots=True)
class ProductionRuntimeComposition:
    resolved_adapter: ManagedAwareResolvedRuntimeAdapter
    surface_transactions: object
    plan_builder: object
    synchronization: ProductionRuntimeSynchronization
    resolver: CanonicalRuntimeConfigResolver
    managed_release_owner: ProductionManagedReleaseServiceOwner | None = None

    def bind_overlay_runtime(self, runtime: object | None) -> None:
        self.synchronization.bind_overlay_runtime(runtime)

    async def synchronize_managed_release_service(self, receipt: SettingsCommitReceipt) -> None:
        if self.managed_release_owner is not None:
            await self.managed_release_owner.synchronize(receipt)

    async def replace_runtime_with_managed_service(
        self,
        receipt: SettingsCommitReceipt,
        request: ResolvedRuntimeActivationRequest,
        plan: RuntimeResourceReplacementPlan,
    ) -> None:
        _ = receipt
        await self.resolved_adapter.replace_runtime_with_plan(request, plan)

    async def replace_runtime_with_secret_store(
        self,
        receipt: SettingsCommitReceipt,
        secrets: object,
        request: ResolvedRuntimeActivationRequest,
        plan: RuntimeResourceReplacementPlan,
    ) -> None:
        await self.resolved_adapter.replace_runtime_with_secret_store(
            receipt, secrets, request, plan
        )

    async def close(self) -> None:
        await self.resolved_adapter.close()
        if self.managed_release_owner is not None:
            await self.managed_release_owner.close()

    async def synchronize_startup(
        self, receipt: SettingsCommitReceipt, operational: RuntimeOperationalSnapshot
    ) -> RuntimeApplyResult:
        directives = {}
        for surface in (
            "translation_provider",
            "stt_language_audio",
            "overlay_osc_output",
            "ui_prompt_clipboard_state",
        ):
            plan = self.plan_builder.build(
                before=None,
                after=receipt,
                provenance=RuntimeMutationProvenance(
                    surface,
                    "settings_surface",
                    receipt.reason,
                    receipt.correlation_id,
                ),
                operational=operational,
            )
            directives.update(
                (directive.operation, directive) for directive in plan.synchronization
            )
        request = ResolvedRuntimeActivationRequest(
            self.resolver.resolve(receipt),
            receipt.revision,
            receipt.reason,
            receipt.correlation_id,
            receipt,
        )
        for operation in (
            "translation_policy",
            "language_runtime_clear",
            "overlay_osc",
            "locale_ui_projection",
            "prompt_clipboard",
            "dashboard_retry_facts",
            "audio_vad",
        ):
            result = await self.synchronization.synchronize_runtime(
                request,
                directives[operation],
                before=None,
                after=receipt,
                operational=operational,
            )
            if result.status != RUNTIME_APPLY_STATUS_APPLIED:
                return result
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    async def resume_peer_stt(self, receipt: SettingsCommitReceipt) -> RuntimeApplyResult:
        request = ResolvedRuntimeActivationRequest(
            self.resolver.resolve(receipt),
            receipt.revision,
            receipt.reason,
            receipt.correlation_id,
            receipt,
        )
        await self.synchronization.synchronize_peer(request, desired_active=True)
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)


def create_production_application_runtime(
    *,
    state_path: Path,
    initial_settings,
    persistence,
    secrets,
    clock,
    runtime_logging: object | None = None,
    audio_gate: object | None = None,
):  # noqa: ANN001, ANN201
    from puripuly_heart.app.services.post_commit_runtime import (
        PostCommitRuntimePlanBuilder,
        PostCommitRuntimeTransactionOwner,
    )
    from puripuly_heart.app.services.surface_runtime_transactions import (
        SelectiveSurfaceRuntimeTransactionPort,
    )

    logging = runtime_logging or RuntimeLoggingService()
    legacy = persistence.legacy_projection(initial_settings)
    sender = VrchatOscUdpSender(
        host=legacy.osc.host,
        port=legacy.osc.port,
        chatbox_address=legacy.osc.chatbox_address,
        chatbox_send=legacy.osc.chatbox_send,
        chatbox_clear=legacy.osc.chatbox_clear,
    )
    osc = ChatboxPaginator(
        sender=sender,
        clock=clock,
        max_chars=legacy.osc.chatbox_max_chars,
        runtime_logging=logging,
    )
    hub = ClientHub(
        stt=None, peer_stt=None, llm=None, osc=osc, clock=clock, runtime_logging=logging
    )
    hooks = ProductionAudioRuntimeHooks()
    factories = ProductionAudioFactories(
        detailed_enabled=lambda: getattr(logging.mode, "value", logging.mode) == "detailed",
        safe_log=lambda message: logging.log_detailed(message),
        hooks=hooks,
        audio_gate=audio_gate,
    )
    self_owner = SelfSTTChannelOwner(
        provider_read_port=hub,
        provider_host=hub,
        ingress=HubSelfVadIngress(hub),
        source_factory=factories.self_source,
        vad_factory=factories.self_vad,
        run_audio_loop=factories.self_loop,
        audio_gate=audio_gate,
    )
    peer_owner = PeerChannelRuntime(
        hub=hub,
        clock=clock,
        provider_read_port=hub,
        provider_ingress_port=HubPeerProviderIngressAdapter(hub),
        source_factory=factories.peer_source,
        vad_factory=factories.peer_vad,
        vad_model_resolver=ensure_silero_vad_onnx,
        run_audio_loop=factories.peer_loop,
    )
    resolver = CanonicalRuntimeConfigResolver()
    receipt_source = PersistedCanonicalReceiptSource(persistence, state_path)
    managed_release_owner = ProductionManagedReleaseServiceOwner(
        persistence=persistence,
        state_path=state_path,
        secrets=secrets,
        authoritative_receipts=receipt_source,
    )
    resource_factory = ResolvedRuntimeResourceFactory(
        secrets=secrets,
        clock=clock,
        diagnostics=RuntimeDiagnosticsAdapter(logging),
        llm_builder=ResolvedLLMBuilderAdapter(),
        stt_builder=ResolvedSTTBuilderAdapter(hooks),
        runtime_logging=logging,
        managed_release_owner=managed_release_owner,
    )
    epoch = RuntimePolicyEpoch()
    resolved = ResolvedRuntimeResourceAdapter(
        factory=resource_factory,
        host=ChannelAwareRuntimeResourceHost(hub, peer_owner, self_owner, epoch),
    )
    managed_resolved = ManagedAwareResolvedRuntimeAdapter(resolved, managed_release_owner)
    provider_activation = SelectiveProviderActivationAdapter(
        managed_resolved, self_owner, peer_owner, epoch
    )
    from puripuly_heart.app.adapters.overlay_ui_projection import ProductionUiProjection

    dashboard_projection = ProductionUiProjection()
    synchronization = ProductionRuntimeSynchronization(
        hub, self_owner, peer_owner, epoch, dashboard_projection
    )
    coordinator = PostCommitRuntimeTransactionOwner(provider_activation, synchronization)
    transactions = SelectiveSurfaceRuntimeTransactionPort(
        PostCommitRuntimePlanBuilder(resolver),
        coordinator,
        frozenset(
            {
                "translation_provider",
                "stt_language_audio",
                "overlay_osc_output",
                "ui_prompt_clipboard_state",
            }
        ),
    )
    composition = ProductionRuntimeComposition(
        managed_resolved,
        transactions,
        PostCommitRuntimePlanBuilder(resolver),
        synchronization,
        resolver,
        managed_release_owner,
    )
    host = ApplicationRuntimeHost(
        parts=ApplicationRuntimeParts(sender, osc, hub, peer_owner, self_owner),
        runtime_composition=composition,
        committed_settings=receipt_source,
        resolver=resolver,
        runtime_logging=logging,
        audio_hooks=hooks,
        dashboard_projection=dashboard_projection,
    )
    from puripuly_heart.app.adapters.openrouter_pkce_production import (
        create_production_openrouter_pkce_owner,
    )

    pkce_owner, _runtime_apply = create_production_openrouter_pkce_owner(
        host=host,
        persistence=persistence,
        state_path=state_path,
        secrets=secrets,
    )
    host.bind_openrouter_pkce(pkce_owner)
    return host


__all__ = ["create_production_application_runtime"]
