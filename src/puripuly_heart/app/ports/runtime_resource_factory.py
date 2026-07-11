from __future__ import annotations

from typing import Protocol

from puripuly_heart.app.ports.runtime_resources import AsyncProviderResource
from puripuly_heart.config.resolved import ResolvedLLMConfig, ResolvedSTTConfig


class RuntimeSecretReadPort(Protocol):
    def get(self, key: str) -> str | None: ...


class RuntimeClockPort(Protocol):
    def now(self) -> float: ...


class RuntimeLoggingPort(Protocol):
    def emit_basic(self, message: str, *, level: int = ...) -> None: ...


class RuntimeFactoryDiagnosticsPort(Protocol):
    def detailed_enabled(self) -> bool: ...

    def record_cleanup_failure(self, *, slot: str, exception_class: str) -> None: ...


class ManagedReleaseServicePort(Protocol):
    async def prepare_for_translation(self) -> object: ...


class ManagedDelegatePort(Protocol):
    def managed_delegate_ready(self) -> object: ...


class LLMResourceBuilderPort(Protocol):
    def build_llm(
        self,
        config: ResolvedLLMConfig,
        *,
        secrets: RuntimeSecretReadPort,
        managed_release_service: ManagedReleaseServicePort | None,
        managed_delegate: ManagedDelegatePort | None,
        runtime_logging: RuntimeLoggingPort | None,
    ) -> AsyncProviderResource: ...


class STTResourceBuilderPort(Protocol):
    def build_stt(
        self,
        config: ResolvedSTTConfig,
        *,
        secrets: RuntimeSecretReadPort,
        clock: RuntimeClockPort,
        runtime_logging: RuntimeLoggingPort | None,
        diagnostics: RuntimeFactoryDiagnosticsPort,
    ) -> AsyncProviderResource: ...


__all__ = [name for name in globals() if not name.startswith("_")]
