from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import dataclass
from typing import Literal, Protocol

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.post_commit_runtime import (
    RuntimeMutationProvenance,
    RuntimeOperationalSnapshot,
)
from puripuly_heart.app.ports.runtime_resources import RuntimeResourceReplacementPlan
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.ports.translation_application import (
    SetTranslationEnabled,
    TranslationCommandResult,
    TranslationRuntimeSnapshot,
)
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.core.lifecycle import LifecycleScope, start_lifecycle_task
from puripuly_heart.core.messages import (
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    RuntimeApplyResult,
)
from puripuly_heart.core.runtime.self_audio import (
    SelfChannelCommandResult,
    SelfChannelConfig,
    SelfChannelSnapshot,
    SetSelfSTTEnabled,
)


@dataclass(frozen=True, slots=True)
class PeerRuntimeTransitionResult:
    status: str
    receipt: SettingsCommitReceipt
    reconciliation_required: bool = False


class RuntimeHubPort(Protocol):
    async def start(self, *, auto_flush_osc: bool) -> None: ...

    async def stop(self) -> None: ...

    def lease_stt_provider(self, slot: Literal["self_stt", "peer_stt"]): ...  # noqa: ANN201


class ChannelOwnerPort(Protocol):
    async def close(self) -> None: ...


class SelfChannelOwnerPort(ChannelOwnerPort, Protocol):
    async def execute(self, command: SetSelfSTTEnabled) -> SelfChannelCommandResult: ...

    def snapshot(self) -> SelfChannelSnapshot: ...

    async def freeze_for_provider_replacement(self): ...  # noqa: ANN201

    def record_intent(self, enabled: bool) -> int: ...


class SenderPort(Protocol):
    def close(self) -> None: ...


class CommittedSettingsPort(Protocol):
    async def load_receipt(self) -> SettingsCommitReceipt: ...


class ResolvedRuntimePort(Protocol):
    async def replace_runtime_with_plan(
        self,
        request: ResolvedRuntimeActivationRequest,
        plan: RuntimeResourceReplacementPlan,
    ) -> None: ...


class RuntimeCompositionPort(Protocol):
    resolved_adapter: ResolvedRuntimePort
    surface_transactions: object

    async def synchronize_startup(
        self, receipt: SettingsCommitReceipt, operational: RuntimeOperationalSnapshot
    ) -> RuntimeApplyResult: ...

    async def resume_peer_stt(self, receipt: SettingsCommitReceipt) -> RuntimeApplyResult: ...

    async def synchronize_managed_release_service(self, receipt: SettingsCommitReceipt) -> None: ...

    async def replace_runtime_with_managed_service(
        self,
        receipt: SettingsCommitReceipt,
        request: ResolvedRuntimeActivationRequest,
        plan: RuntimeResourceReplacementPlan,
    ) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ApplicationRuntimeParts:
    sender: SenderPort
    osc: object
    hub: RuntimeHubPort
    peer_runtime: ChannelOwnerPort
    self_stt: SelfChannelOwnerPort


class ApplicationRuntimeHost:
    def __init__(
        self,
        *,
        parts: ApplicationRuntimeParts,
        runtime_composition: RuntimeCompositionPort,
        committed_settings: CommittedSettingsPort,
        resolver: CanonicalRuntimeConfigResolver,
        runtime_logging: object | None = None,
        audio_hooks: object | None = None,
        initial_translation_enabled: bool = False,
        openrouter_pkce: object | None = None,
        dashboard_projection: object | None = None,
    ) -> None:
        self._parts: ApplicationRuntimeParts | None = parts
        self._runtime_composition = runtime_composition
        self._committed_settings = committed_settings
        self._resolver = resolver
        self.runtime_logging = runtime_logging
        self.audio_hooks = audio_hooks
        self._started = False
        self._hub_stopped = False
        self._self_closed = False
        self._peer_closed = False
        self._sender_closed = False
        self._composition_closed = False
        self._shutdown = False
        self._translation_desired = initial_translation_enabled
        self._openrouter_pkce = openrouter_pkce
        self._dashboard_projection = dashboard_projection
        self._canonical_commands = None
        self._secret_rebind_lock = asyncio.Lock()
        self._manual_input_idle_task: asyncio.Task[None] | None = None
        self._manual_submit_generation = 0
        self._manual_input_generation = 0
        self._manual_input_scope = LifecycleScope("application-runtime-manual-input")

    @property
    def parts(self) -> ApplicationRuntimeParts | None:
        return self._parts

    @property
    def commands(self) -> ApplicationRuntimeHost:
        return self

    @property
    def state(self) -> ApplicationRuntimeHost:
        return self

    @property
    def canonical_commands(self):  # noqa: ANN201
        return self._canonical_commands

    def bind_canonical_commands(self, composition: object) -> None:
        if self._canonical_commands is not None:
            raise RuntimeError("canonical command composition is already bound")
        self._canonical_commands = composition

    def runtime_operational_snapshot(self) -> RuntimeOperationalSnapshot:
        from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntimeState
        from puripuly_heart.core.runtime.self_audio import SelfChannelState

        parts = self._require_parts()
        translation = self.translation_snapshot()
        self_snapshot = parts.self_stt.snapshot()
        peer_policy = parts.peer_runtime.policy_snapshot()
        peer_state = getattr(parts.peer_runtime, "state", PeerChannelRuntimeState.STOPPED)
        self_available = bool(
            self_snapshot.provider_available or self._provider_is_available("self_stt")
        )
        peer_available = self._provider_is_available("peer_stt")
        self_enabled = bool(self_snapshot.intent_enabled)
        peer_enabled = bool(peer_policy.intent_desired_active)
        self_running = self_snapshot.state == SelfChannelState.RUNNING
        peer_running = peer_state == PeerChannelRuntimeState.RUNNING
        return RuntimeOperationalSnapshot(
            translation_enabled=translation.desired_enabled,
            self_stt_enabled=self_enabled,
            self_stt_running=self_running,
            self_stt_staged=bool(self_available and not self_enabled),
            peer_stt_enabled=peer_enabled,
            peer_stt_running=peer_running,
            peer_stt_staged=bool(peer_available and not peer_enabled),
            llm_available=translation.provider_available,
            llm_retry_pending=bool(
                translation.desired_enabled and not translation.provider_available
            ),
            self_stt_available=self_available,
            self_stt_retry_pending=not self_available,
            peer_stt_available=peer_available,
            peer_stt_retry_pending=not peer_available,
        )

    async def rebind_secret_store(self, secrets: object, receipt: SettingsCommitReceipt) -> None:
        async with self._secret_rebind_lock:
            await self._rebind_secret_store_locked(secrets, receipt)

    async def _rebind_secret_store_locked(
        self, secrets: object, receipt: SettingsCommitReceipt
    ) -> None:
        adapter = getattr(self._runtime_composition, "resolved_adapter", None)
        runtime = getattr(adapter, "runtime", adapter)
        factory = getattr(runtime, "factory", None)
        owner = getattr(self._runtime_composition, "managed_release_owner", None)
        old_factory_secrets = getattr(factory, "secrets", None)
        old_owner_secrets = getattr(owner, "secrets", None)
        old_owner_signature = getattr(owner, "signature", None)
        replace_secrets = getattr(
            self._runtime_composition, "replace_runtime_with_secret_store", None
        )
        try:
            if callable(replace_secrets):
                config = self._resolver.resolve(receipt)
                request = ResolvedRuntimeActivationRequest(
                    config, receipt.revision, receipt.reason, receipt.correlation_id, receipt
                )
                await replace_secrets(
                    receipt,
                    secrets,
                    request,
                    RuntimeResourceReplacementPlan("replace", "replace", "replace"),
                )
            else:
                if factory is not None and hasattr(factory, "secrets"):
                    factory.secrets = secrets
                if owner is not None and hasattr(owner, "secrets"):
                    owner.secrets = secrets
                    owner.signature = None
                await self._replace(
                    receipt,
                    RuntimeResourceReplacementPlan("replace", "replace", "replace"),
                )
        except BaseException:
            committed = owner is not None and getattr(owner, "secrets", None) is secrets
            if not committed and factory is not None and hasattr(factory, "secrets"):
                factory.secrets = old_factory_secrets
            if not committed and owner is not None and hasattr(owner, "secrets"):
                owner.secrets = old_owner_secrets
                owner.signature = old_owner_signature
            raise

    def secret_store_is_authoritative(self, secrets: object) -> bool:
        adapter = getattr(self._runtime_composition, "resolved_adapter", None)
        runtime = getattr(adapter, "runtime", adapter)
        factory = getattr(runtime, "factory", None)
        owner = getattr(self._runtime_composition, "managed_release_owner", None)
        return bool(
            factory is not None
            and getattr(factory, "secrets", None) is secrets
            and (owner is None or getattr(owner, "secrets", None) is secrets)
        )

    def bind_final_suppressed(self, callback: object) -> None:
        if self.audio_hooks is not None:
            self.audio_hooks.final_suppressed_callback = callback

    @property
    def ui_event_queue(self):  # noqa: ANN201
        return self._require_parts().hub.ui_events

    @property
    def output_runtime(self):  # noqa: ANN201
        return self._require_parts().hub.output_runtime

    def set_debug_audio_faults(self, *, capture: str, stt: str) -> None:
        if self.audio_hooks is not None:
            self.audio_hooks.capture_fault = capture
            self.audio_hooks.stt_fault = stt

    def snapshot(self) -> SelfChannelSnapshot:
        parts = self._require_parts()
        return parts.self_stt.snapshot()

    def translation_snapshot(self) -> TranslationRuntimeSnapshot:
        parts = self._require_parts()
        state = parts.hub.provider_state_snapshot().llm  # type: ignore[attr-defined]
        return TranslationRuntimeSnapshot(
            self._translation_desired,
            bool(getattr(parts.hub, "translation_enabled", False)),
            state.provider is not None,
            state.generation,
        )

    @property
    def managed_release_service(self):  # noqa: ANN201
        owner = getattr(self._runtime_composition, "managed_release_owner", None)
        return None if owner is None else owner.current_service()

    async def resolve_managed_release_service(self):  # noqa: ANN201
        receipt = await self._committed_settings.load_receipt()
        synchronize = getattr(
            self._runtime_composition, "synchronize_managed_release_service", None
        )
        if callable(synchronize):
            await synchronize(receipt)
        return self.managed_release_service

    def subscribe_managed_discord_callback(self, handler: object) -> None:
        owner = getattr(self._runtime_composition, "managed_release_owner", None)
        output = None if owner is None else owner.callback_output
        if output is not None and callable(handler):
            output.subscribe(handler)

    def bind_managed_transaction_port(self, port: object) -> None:
        owner = getattr(self._runtime_composition, "managed_release_owner", None)
        if owner is None:
            return
        owner.managed_transaction_port = port
        service = owner.current_service()
        bind = getattr(service, "bind_transaction_port", None)
        if callable(bind):
            bind(port)

    def subscribe_dashboard_runtime_facts(self, handler: object) -> None:
        subscribe = getattr(self._dashboard_projection, "subscribe_dashboard", None)
        if callable(subscribe) and callable(handler):
            subscribe(handler)

    async def start(self, *, auto_flush_osc: bool = True) -> None:
        if self._started:
            return
        parts = self._require_parts()
        receipt = await self._committed_settings.load_receipt()
        slots: tuple[Literal["llm", "self_stt", "peer_stt"], ...] = (
            ("llm", "self_stt", "peer_stt")
            if self._translation_desired
            else ("self_stt", "peer_stt")
        )
        installed = await self._install_available(receipt, slots)
        operational = RuntimeOperationalSnapshot(
            translation_enabled=self._translation_desired,
            self_stt_enabled=False,
            self_stt_running=False,
            self_stt_staged=True,
            peer_stt_enabled=False,
            peer_stt_running=False,
            peer_stt_staged=True,
            llm_available="llm" in installed,
            llm_retry_pending=self._translation_desired and "llm" not in installed,
            self_stt_available="self_stt" in installed,
            self_stt_retry_pending="self_stt" not in installed,
            peer_stt_available="peer_stt" in installed,
            peer_stt_retry_pending="peer_stt" not in installed,
        )
        synchronized = await self._runtime_composition.synchronize_startup(receipt, operational)
        if synchronized.status != RUNTIME_APPLY_STATUS_APPLIED:
            await self.shutdown()
            raise RuntimeError("authoritative startup synchronization failed")
        try:
            await parts.hub.start(auto_flush_osc=auto_flush_osc)
        except BaseException:
            await self.shutdown()
            raise
        self._started = True

    async def set_translation_enabled(
        self, command: SetTranslationEnabled
    ) -> TranslationCommandResult:
        self._translation_desired = command.enabled
        parts = self._require_parts()
        if not command.enabled:
            parts.hub.translation_enabled = False  # type: ignore[attr-defined]
            parts.hub.clear_context()  # type: ignore[attr-defined]
            receipt = await self._committed_settings.load_receipt()
            await self._replace(
                receipt, RuntimeResourceReplacementPlan("clear", "retain", "retain")
            )
            return TranslationCommandResult("applied", self.translation_snapshot())
        receipt = await self._committed_settings.load_receipt()
        installed = await self._install_available(receipt, ("llm",))
        if "llm" not in installed:
            parts.hub.translation_enabled = False  # type: ignore[attr-defined]
            return TranslationCommandResult("unavailable", self.translation_snapshot())
        parts.hub.clear_context()  # type: ignore[attr-defined]
        parts.hub.translation_enabled = True  # type: ignore[attr-defined]
        provider = self.translation_snapshot()
        if provider.provider_available:
            active = getattr(parts.hub, "llm", None)
            active = getattr(active, "inner", active)
            warmup = getattr(active, "warmup", None)
            if callable(warmup):
                with contextlib.suppress(Exception):
                    warmed = warmup()
                    if inspect.isawaitable(warmed):
                        await warmed
        return TranslationCommandResult("applied", self.translation_snapshot())

    async def execute(self, command: SetSelfSTTEnabled) -> SelfChannelCommandResult:
        parts = self._require_parts()
        if not command.enabled:
            return await parts.self_stt.execute(command)
        intent_token = parts.self_stt.record_intent(True)
        receipt = await self._committed_settings.load_receipt()
        config = self._resolver.resolve(receipt).self_stt
        resolved_config = SelfChannelConfig(
            config.sample_rate_hz,
            (config,),
            config.provider == "local_qwen",
            config,
        )
        if config.provider == "local_qwen" and self._provider_is_available("self_stt"):
            current = parts.self_stt.snapshot()
            if current.intent_generation != intent_token or not current.intent_enabled:
                return SelfChannelCommandResult("applied", current)
            return await parts.self_stt.execute(
                SetSelfSTTEnabled(
                    True,
                    resolved_config,
                    command.force_immediate,
                    record_intent=False,
                )
            )
        snapshot = parts.self_stt.snapshot()
        if (
            snapshot.desired_enabled
            and snapshot.runtime_signature == resolved_config.runtime_signature
        ):
            return await parts.self_stt.execute(
                SetSelfSTTEnabled(True, resolved_config, record_intent=False)
            )
        await parts.self_stt.freeze_for_provider_replacement()
        installed = await self._install_available(receipt, ("self_stt",))
        if "self_stt" not in installed:
            return SelfChannelCommandResult("preparation_failed", parts.self_stt.snapshot())
        current = parts.self_stt.snapshot()
        if current.intent_generation != intent_token or not current.intent_enabled:
            return SelfChannelCommandResult("applied", current)
        resolved_command = SetSelfSTTEnabled(
            True,
            resolved_config,
            command.force_immediate,
            record_intent=False,
        )
        return await parts.self_stt.execute(resolved_command)

    async def resume_peer_stt(self) -> PeerRuntimeTransitionResult:
        receipt = await self._committed_settings.load_receipt()
        installed = await self._install_available(receipt, ("peer_stt",))
        if "peer_stt" not in installed:
            return PeerRuntimeTransitionResult(RUNTIME_APPLY_STATUS_FAILED, receipt, True)
        result = await self._runtime_composition.resume_peer_stt(receipt)
        return PeerRuntimeTransitionResult(
            result.status, receipt, result.status != RUNTIME_APPLY_STATUS_APPLIED
        )

    async def pause_peer_stt(self) -> PeerRuntimeTransitionResult:
        receipt = await self._committed_settings.load_receipt()
        config = self._peer_runtime_config(receipt)
        await self._require_parts().peer_runtime.apply_policy(config=config, desired_active=False)
        return PeerRuntimeTransitionResult(RUNTIME_APPLY_STATUS_APPLIED, receipt)

    async def submit_manual_self_text(self, text: str) -> str:
        normalized = text.strip()
        if not normalized:
            return "rejected"
        self.set_manual_input_activity(False)
        parts = self._require_parts()
        self._manual_submit_generation += 1
        reason = f"manual_submit:{self._manual_submit_generation}"
        parts.osc.set_typing_reason(reason, True)
        try:
            utterance_id = await parts.hub.submit_text(normalized, source="You")
            translation_task = parts.hub.self_runtime.translation_tasks.get(utterance_id)
            if isinstance(translation_task, asyncio.Task):
                settlement = asyncio.gather(translation_task, return_exceptions=True)
            elif inspect.isawaitable(translation_task):
                settlement = translation_task
            else:
                settlement = None
            if settlement is not None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(settlement), timeout=30.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            return "failed"
        finally:
            parts.osc.set_typing_reason(reason, False)
        return "applied"

    def set_manual_input_activity(self, has_text: bool) -> None:
        task = self._manual_input_idle_task
        self._manual_input_idle_task = None
        if task is not None and not task.done():
            task.cancel()
        osc = self._require_parts().osc
        osc.set_typing_reason("manual_input", has_text)
        if has_text:
            self._manual_input_generation += 1
            self._manual_input_idle_task = start_lifecycle_task(
                self._manual_input_scope,
                self._clear_manual_input_after_idle(),
                name=f"idle-timeout-{self._manual_input_generation}",
            )

    async def _clear_manual_input_after_idle(self) -> None:
        try:
            await asyncio.sleep(1.5)
            self._require_parts().osc.set_typing_reason("manual_input", False)
        finally:
            if self._manual_input_idle_task is asyncio.current_task():
                self._manual_input_idle_task = None

    def _peer_runtime_config(self, receipt: SettingsCommitReceipt):  # noqa: ANN201
        from puripuly_heart.core.runtime.peer_channel import PeerRuntimeConfig

        peer = self._resolver.resolve(receipt).peer_stt
        return PeerRuntimeConfig(
            backend=peer,
            output_device=peer.output_device or "",
            vad_threshold=peer.vad_speech_threshold,
            vad_hangover_ms=peer.vad_hangover_ms,
            vad_pre_roll_ms=peer.vad_pre_roll_ms,
            provider_signature=(peer.provider, peer.credential, peer.provider_options),
            runtime_signature=(peer,),
            capture_target=peer.capture_target,
        )

    async def retry_peer_process_capture(self):  # noqa: ANN201
        from puripuly_heart.app.ports.ui_settings import (
            CaptureDiagnosticReason,
            CaptureRetryResult,
            CaptureRetryStatus,
        )

        receipt = await self._committed_settings.load_receipt()
        config = self._peer_runtime_config(receipt)
        runtime = self._require_parts().peer_runtime
        if config.capture_target.kind != "process":
            return CaptureRetryResult(
                CaptureRetryStatus.NOT_APPLICABLE,
                CaptureDiagnosticReason.NOT_APPLICABLE,
                receipt.revision,
            )
        installed = await self._install_available(receipt, ("peer_stt",))
        if "peer_stt" not in installed or not self._provider_is_available("peer_stt"):
            return CaptureRetryResult(
                CaptureRetryStatus.FAILED,
                CaptureDiagnosticReason.PROVIDER_FAILURE,
                receipt.revision,
            )
        succeeded = await runtime.retry_process_capture(config=config)
        diagnostic = runtime.last_failure
        if succeeded:
            return CaptureRetryResult(
                CaptureRetryStatus.SUCCEEDED,
                CaptureDiagnosticReason.SUCCESS,
                receipt.revision,
            )
        if diagnostic is None:
            return CaptureRetryResult(
                CaptureRetryStatus.NOT_APPLICABLE,
                CaptureDiagnosticReason.NOT_APPLICABLE,
                receipt.revision,
            )
        return CaptureRetryResult(
            CaptureRetryStatus.FAILED,
            _capture_diagnostic_reason(diagnostic),
            receipt.revision,
            diagnostic.process_unavailable_reason,
        )

    async def rebuild(self) -> object | None:
        receipt = await self._committed_settings.load_receipt()
        installed = await self._install_available(receipt, ("self_stt",))
        if "self_stt" not in installed:
            return None
        lease = self._require_parts().hub.lease_stt_provider("self_stt")  # type: ignore[attr-defined]
        return None if lease is None else lease.current

    async def apply_committed_runtime(
        self,
        *,
        before: SettingsCommitReceipt | None,
        after: SettingsCommitReceipt,
        surface: str,
        cause: str = "settings_surface",
        operational: RuntimeOperationalSnapshot,
    ):
        return await self._runtime_composition.surface_transactions.apply_surface_runtime(
            before=before,
            after=after,
            provenance=RuntimeMutationProvenance(
                surface, cause, after.reason, after.correlation_id
            ),
            operational=operational,
        )

    async def execute_openrouter_pkce(self, command: object):  # noqa: ANN201
        owner = self._openrouter_pkce
        if owner is None:
            raise RuntimeError("OpenRouter PKCE is not configured")
        return await owner.execute(command)

    def openrouter_pkce_active(self) -> bool:
        return bool(self._openrouter_pkce and self._openrouter_pkce.active)

    def bind_openrouter_pkce(self, owner: object) -> None:
        if self._openrouter_pkce is not None:
            raise RuntimeError("OpenRouter PKCE owner is already bound")
        self._openrouter_pkce = owner

    def bind_overlay_runtime(self, runtime: object | None) -> None:
        bind = getattr(self._runtime_composition, "bind_overlay_runtime", None)
        if callable(bind):
            bind(runtime)

    async def shutdown(self) -> None:
        if self._shutdown:
            return
        parts = self._parts
        if parts is None:
            return
        failures: list[BaseException] = []
        try:
            await self._manual_input_scope.close()
        except BaseException as exc:
            failures.append(exc)
        manual_task = self._manual_input_idle_task
        self._manual_input_idle_task = None
        if manual_task is not None and not manual_task.done():
            manual_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await manual_task
        if self._openrouter_pkce is not None:
            try:
                await self._openrouter_pkce.close()
            except BaseException as exc:
                failures.append(exc)
        for attribute, owner in (
            ("_self_closed", parts.self_stt),
            ("_peer_closed", parts.peer_runtime),
        ):
            if getattr(self, attribute):
                continue
            try:
                await owner.close()
            except BaseException as exc:
                failures.append(exc)
            else:
                setattr(self, attribute, True)
        if not self._hub_stopped:
            try:
                await parts.hub.stop()
            except BaseException as exc:
                failures.append(exc)
            else:
                self._hub_stopped = True
        if not self._composition_closed:
            try:
                await self._runtime_composition.close()
            except BaseException as exc:
                failures.append(exc)
            else:
                self._composition_closed = True
        if not self._sender_closed:
            try:
                parts.sender.close()
            except BaseException as exc:
                failures.append(exc)
            else:
                self._sender_closed = True
        if not failures and self._hub_stopped:
            self._parts = None
            self._started = False
            self._shutdown = True
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("application runtime shutdown failed", failures)

    async def _install_available(
        self,
        receipt: SettingsCommitReceipt,
        slots: tuple[Literal["llm", "self_stt", "peer_stt"], ...],
    ) -> frozenset[str]:
        installed: set[str] = set()
        for slot in slots:
            plan = RuntimeResourceReplacementPlan(
                "replace" if slot == "llm" else "retain",
                "replace" if slot == "self_stt" else "retain",
                "replace" if slot == "peer_stt" else "retain",
            )
            try:
                await self._replace(receipt, plan)
            except Exception:
                continue
            installed.add(slot)
        return frozenset(installed)

    def _provider_is_available(self, slot: Literal["self_stt", "peer_stt"]) -> bool:
        lease_provider = getattr(self._require_parts().hub, "lease_stt_provider", None)
        if not callable(lease_provider):
            return False
        lease = lease_provider(slot)
        return lease is not None and lease.current is not None

    async def _replace(
        self, receipt: SettingsCommitReceipt, plan: RuntimeResourceReplacementPlan
    ) -> None:
        config = self._resolver.resolve(receipt)
        request = ResolvedRuntimeActivationRequest(
            config, receipt.revision, receipt.reason, receipt.correlation_id, receipt
        )
        replace_with_service = getattr(
            self._runtime_composition, "replace_runtime_with_managed_service", None
        )
        if callable(replace_with_service):
            await replace_with_service(receipt, request, plan)
            return
        await self._runtime_composition.resolved_adapter.replace_runtime_with_plan(request, plan)

    def _require_parts(self) -> ApplicationRuntimeParts:
        if self._parts is None:
            raise RuntimeError("application runtime is closed")
        return self._parts


def _capture_diagnostic_reason(diagnostic):  # noqa: ANN001, ANN202
    from puripuly_heart.app.ports.ui_settings import CaptureDiagnosticReason
    from puripuly_heart.core.runtime.peer_channel import PeerRuntimeFailureReason

    if diagnostic.reason is PeerRuntimeFailureReason.PROCESS_TARGET_UNAVAILABLE:
        return {
            "no_process": CaptureDiagnosticReason.PROCESS_NOT_FOUND,
            "access_denied": CaptureDiagnosticReason.PROCESS_ACCESS_DENIED,
            "ineligible": CaptureDiagnosticReason.PROCESS_INELIGIBLE,
        }.get(diagnostic.process_unavailable_reason, CaptureDiagnosticReason.TARGET_UNAVAILABLE)
    return {
        PeerRuntimeFailureReason.PROCESS_SETUP_FAILED: CaptureDiagnosticReason.SETUP_FAILURE,
        PeerRuntimeFailureReason.PROCESS_TARGET_EXITED: CaptureDiagnosticReason.TARGET_EXITED,
        PeerRuntimeFailureReason.PROCESS_SOURCE_FAILED: CaptureDiagnosticReason.SOURCE_FAILURE,
        PeerRuntimeFailureReason.PROCESS_PROVIDER_FAILED: CaptureDiagnosticReason.PROVIDER_FAILURE,
        PeerRuntimeFailureReason.PEER_RUNTIME_FAILED: CaptureDiagnosticReason.RUNTIME_FAILURE,
    }[diagnostic.reason]


__all__ = ["ApplicationRuntimeHost", "ApplicationRuntimeParts"]
