from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from puripuly_heart.app.ports.post_commit_runtime import RuntimeOperationalSnapshot
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.openrouter_pkce_handoff import (
    OpenRouterPkceHandoffRequest,
    OpenRouterPkceHandoffService,
)
from puripuly_heart.core.messages import TransactionResult
from puripuly_heart.core.openrouter_pkce import OpenRouterPKCEExchangeResult


class OpenRouterPkceClientPort(Protocol):
    async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult: ...

    def reopen_authorization_url(self) -> bool: ...

    def cancel(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StartOpenRouterPkce:
    settings_values: Mapping[str, object] = field(repr=False)
    expected_settings_revision: str | None
    launch_source: str
    correlation_id: str | None = None
    operational: RuntimeOperationalSnapshot | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ReopenOpenRouterPkce:
    pass


@dataclass(frozen=True, slots=True)
class CancelOpenRouterPkce:
    pass


OpenRouterPkceCommand = StartOpenRouterPkce | ReopenOpenRouterPkce | CancelOpenRouterPkce
OpenRouterPkceStatus = Literal[
    "started",
    "reopened",
    "cancelled",
    "succeeded",
    "committed_degraded",
    "failed",
    "inactive",
    "closed",
]


@dataclass(frozen=True, slots=True)
class OpenRouterPkceResult:
    status: OpenRouterPkceStatus
    transaction: TransactionResult | None = None
    launch_source: str | None = None
    receipt: SettingsCommitReceipt | None = field(default=None, repr=False)
    reconciliation_required: bool = False
    completed: tuple[str, ...] = ()
    failed_operation: str | None = None


class OpenRouterPkceOwner:
    def __init__(
        self,
        *,
        client_factory: Callable[[], OpenRouterPkceClientPort],
        handoff: OpenRouterPkceHandoffService,
    ) -> None:
        self._client_factory = client_factory
        self._handoff = handoff
        self._client: OpenRouterPkceClientPort | None = None
        self._task: asyncio.Task[OpenRouterPkceResult] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._phase: Literal["inactive", "exchange", "handoff"] = "inactive"
        self._handoff_task: asyncio.Task[TransactionResult] | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def execute(self, command: OpenRouterPkceCommand) -> OpenRouterPkceResult:
        if isinstance(command, ReopenOpenRouterPkce):
            return self._reopen()
        if isinstance(command, CancelOpenRouterPkce):
            return await self.cancel()
        async with self._lock:
            if self._closed:
                return OpenRouterPkceResult("closed", launch_source=command.launch_source)
            if self.active:
                return self._reopen(command.launch_source)
            client = self._client_factory()
            runtime_apply = getattr(self._handoff, "runtime_apply", None)
            if runtime_apply is not None and hasattr(runtime_apply, "operational"):
                runtime_apply.operational = command.operational
            self._client = client
            self._phase = "exchange"
            self._task = asyncio.create_task(
                self._run(client, command), name="OpenRouterPkceOwner:active-flow"
            )
            task = self._task
        return await task

    def _reopen(self, launch_source: str | None = None) -> OpenRouterPkceResult:
        client = self._client
        if client is None or not self.active:
            return OpenRouterPkceResult("inactive", launch_source=launch_source)
        status: OpenRouterPkceStatus = "reopened" if client.reopen_authorization_url() else "failed"
        return OpenRouterPkceResult(status, launch_source=launch_source)

    async def cancel(self) -> OpenRouterPkceResult:
        task = self._task
        client = self._client
        if task is None or task.done():
            return OpenRouterPkceResult("inactive")
        if self._phase == "exchange":
            task.cancel()
            if client is not None:
                await asyncio.to_thread(client.cancel)
        try:
            return await task
        except asyncio.CancelledError:
            return OpenRouterPkceResult("cancelled")

    async def close(self) -> None:
        self._closed = True
        await self.cancel()

    async def _run(
        self, client: OpenRouterPkceClientPort, command: StartOpenRouterPkce
    ) -> OpenRouterPkceResult:
        try:
            exchange = await client.run_desktop_flow()
            self._phase = "handoff"
            handoff_task = asyncio.create_task(
                self._handoff.complete_handoff(
                    OpenRouterPkceHandoffRequest(
                        provider="openrouter",
                        secret_key="openrouter_api_key",
                        transient_api_key=exchange.api_key,
                        settings_values=command.settings_values,
                        expected_settings_revision=command.expected_settings_revision,
                        reason="openrouter_pkce",
                        correlation_id=command.correlation_id,
                        verifier_context={
                            "flow": "openrouter.pkce",
                            "launch_source": command.launch_source,
                        },
                    )
                ),
                name="OpenRouterPkceOwner:durable-handoff",
            )
            self._handoff_task = handoff_task
            try:
                transaction = await asyncio.shield(handoff_task)
            except asyncio.CancelledError:
                transaction = await handoff_task
            status: OpenRouterPkceStatus
            if transaction.status == "settings_commit_success_runtime_applied":
                status = "succeeded"
            elif transaction.status == "settings_commit_success_runtime_degraded":
                status = "committed_degraded"
            else:
                status = "failed"
            return OpenRouterPkceResult(
                status,
                transaction,
                command.launch_source,
                getattr(transaction, "receipt", None),
                bool(getattr(transaction, "reconciliation_required", False)),
                tuple(getattr(transaction, "completed", ())),
                getattr(transaction, "failed", None),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return OpenRouterPkceResult("failed", launch_source=command.launch_source)
        finally:
            if self._client is client:
                self._client = None
            self._phase = "inactive"
            self._handoff_task = None
            if self._task is asyncio.current_task():
                self._task = None


__all__ = [
    "CancelOpenRouterPkce",
    "OpenRouterPkceOwner",
    "OpenRouterPkceResult",
    "ReopenOpenRouterPkce",
    "StartOpenRouterPkce",
]
