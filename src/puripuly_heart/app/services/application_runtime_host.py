from __future__ import annotations

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


class RuntimeHubPort(Protocol):
    async def start(self, *, auto_flush_osc: bool) -> None: ...

    async def stop(self) -> None: ...


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

    @property
    def parts(self) -> ApplicationRuntimeParts | None:
        return self._parts

    @property
    def commands(self) -> ApplicationRuntimeHost:
        return self

    @property
    def state(self) -> ApplicationRuntimeHost:
        return self

    def bind_final_suppressed(self, callback: object) -> None:
        if self.audio_hooks is not None:
            self.audio_hooks.final_suppressed_callback = callback

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

    async def resume_peer_stt(self) -> RuntimeApplyResult:
        receipt = await self._committed_settings.load_receipt()
        installed = await self._install_available(receipt, ("peer_stt",))
        if "peer_stt" not in installed:
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)
        return await self._runtime_composition.resume_peer_stt(receipt)

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

    async def shutdown(self) -> None:
        if self._shutdown:
            return
        parts = self._parts
        if parts is None:
            return
        failures: list[BaseException] = []
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


__all__ = ["ApplicationRuntimeHost", "ApplicationRuntimeParts"]
