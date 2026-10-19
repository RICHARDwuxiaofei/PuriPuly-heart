from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterStatusRefreshResult,
    TalkTogetherPassStatus,
)
from puripuly_heart.core.openrouter_handoff import is_effectively_exhausted
from puripuly_heart.core.openrouter_metadata import OpenRouterKeyMetadata, OpenRouterMetadataError
from puripuly_heart.core.runtime_logging import emit_basic_log

logger = logging.getLogger(__name__)

from .managed_status_refresh import ManagedStatusRefreshOwner

ManagedUsageStateProvider = Callable[[], "ManagedUsageState"]
ManagedUsageReleaseServiceProvider = Callable[[], object | None]
ManagedUsageMetadataFetcher = Callable[[], Awaitable["ManagedUsageMetadataResult"]]
ManagedUsagePendingSink = Callable[[bool], None]
ManagedUsageViewSink = Callable[["ManagedUsageViewState"], None]
ManagedUsageDisableTranslationSink = Callable[[bool], None]
ManagedUsageAutoShowFounderLetterProvider = Callable[[OpenRouterKeyMetadata | None], bool]
ManagedUsageReferralNormalizer = Callable[[object], str | None]
ManagedUsageWarningSink = Callable[[str, BaseException | None], None]

_PASS_STATUS_UNSET = object()


@dataclass(frozen=True, slots=True)
class ManagedUsageState:
    settings_available: bool
    managed_key_visible: bool
    release_settings_available: bool
    installation_id: str | None
    entitlement_ref: str | None
    referral_id: str | None
    ingress_frozen: bool
    referral_source: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedUsageViewState:
    visible: bool
    remaining_percent: int | None
    referral_id: str | None
    pass_status: TalkTogetherPassStatus | None
    usage_status: str = "unavailable"


@dataclass(frozen=True, slots=True)
class ManagedUsageMetadataResult:
    key_available: bool
    metadata: OpenRouterKeyMetadata | None
    failure_code: str | None = None
    failure_type: str | None = None


@dataclass(slots=True)
class ManagedUsageOwner:
    state_provider: ManagedUsageStateProvider
    release_service_provider: ManagedUsageReleaseServiceProvider
    metadata_fetcher: ManagedUsageMetadataFetcher
    pending_sink: ManagedUsagePendingSink
    view_sink: ManagedUsageViewSink
    disable_translation_sink: ManagedUsageDisableTranslationSink
    auto_show_founder_letter_provider: ManagedUsageAutoShowFounderLetterProvider
    normalize_referral_id: ManagedUsageReferralNormalizer
    warning_sink: ManagedUsageWarningSink
    usage_metadata: OpenRouterKeyMetadata | None = None
    usage_metadata_entitlement_ref: str | None = None
    pass_status: TalkTogetherPassStatus | None = None
    pass_status_key: tuple[str | None, str | None, str | None, str | None] | None = None
    refresh_owner: ManagedStatusRefreshOwner = field(init=False, repr=False)
    metadata_timeout_s: float = 15.0
    usage_status: str = "unavailable"
    _refresh_generation: int = field(init=False, default=0, repr=False)

    def __post_init__(self) -> None:
        if self.metadata_timeout_s <= 0:
            raise ValueError("Managed usage timeout must be positive")
        self.refresh_owner = ManagedStatusRefreshOwner(
            diagnostics_sink=self._on_refresh_diagnostic,
        )

    @property
    def remaining_percent(self) -> int | None:
        return self.remaining_percent_for(self.usage_metadata)

    @property
    def is_exhausted(self) -> bool:
        return is_effectively_exhausted(self.usage_metadata)

    @staticmethod
    def remaining_percent_for(metadata: OpenRouterKeyMetadata | None) -> int | None:
        if (
            metadata is None
            or metadata.limit_usd is None
            or metadata.remaining_usd is None
            or metadata.limit_usd <= 0
            or not math.isfinite(metadata.limit_usd)
            or not math.isfinite(metadata.remaining_usd)
        ):
            return None
        return max(0, min(100, round((metadata.remaining_usd / metadata.limit_usd) * 100)))

    @property
    def current_referral_id(self) -> str | None:
        return self.normalize_referral_id(self.state_provider().referral_id)

    def identity_scope(
        self,
        referral_id: str | None,
        *,
        state: ManagedUsageState | None = None,
    ) -> tuple[str | None, str | None, str | None, str | None] | None:
        current = state or self.state_provider()
        if not current.settings_available:
            return None
        referral_source = (
            current.referral_source.strip().lower()
            if isinstance(current.referral_source, str)
            else None
        )
        if referral_source not in {"discord", "qq"}:
            referral_source = None
        return (
            current.installation_id,
            current.entitlement_ref,
            referral_source,
            self.normalize_referral_id(referral_id),
        )

    def clear_pass_status(self) -> None:
        self.pass_status = None
        self.pass_status_key = None

    def cached_pass_status_for(
        self,
        referral_id: str | None,
    ) -> TalkTogetherPassStatus | None:
        normalized = self.normalize_referral_id(referral_id)
        cache_key = self.identity_scope(normalized) if normalized is not None else None
        if cache_key is None or cache_key != self.pass_status_key:
            self.clear_pass_status()
            return None
        return self.pass_status

    def set_view_state(
        self,
        *,
        visible: bool,
        remaining_percent: int | None,
        referral_id: str | None,
        pass_status: TalkTogetherPassStatus | None | object = _PASS_STATUS_UNSET,
        usage_status: str | None = None,
    ) -> None:
        if usage_status is not None:
            self.usage_status = usage_status
        normalized = self.normalize_referral_id(referral_id)
        if not visible or normalized is None:
            self.clear_pass_status()
        elif pass_status is _PASS_STATUS_UNSET:
            pass
        elif isinstance(pass_status, TalkTogetherPassStatus) and pass_status.pass_id == normalized:
            self.pass_status = pass_status
            self.pass_status_key = self.identity_scope(normalized)
        else:
            self.clear_pass_status()
        self.view_sink(
            ManagedUsageViewState(
                visible=visible,
                remaining_percent=remaining_percent,
                referral_id=normalized,
                pass_status=self.cached_pass_status_for(normalized),
                usage_status=self.usage_status,
            )
        )

    async def refresh_status_best_effort(
        self,
        *,
        service: object | None = None,
    ) -> ManagedOpenRouterStatusRefreshResult:
        referral_id = self.current_referral_id
        resolved_service = service or self.release_service_provider()
        if resolved_service is None:
            return self._failed_status(referral_id)
        refresh_status = getattr(resolved_service, "refresh_managed_status", None)
        if callable(refresh_status):
            try:
                return await refresh_status()
            except Exception as exc:
                self._warn("[ManagedAuth] Managed status refresh failed", exc)
                return self._failed_status(referral_id)
        legacy_refresh = getattr(
            resolved_service,
            "refresh_owned_referral_id_from_status",
            None,
        )
        if callable(legacy_refresh):
            try:
                return ManagedOpenRouterStatusRefreshResult(
                    referral_id=self.normalize_referral_id(await legacy_refresh()) or referral_id,
                    pass_status=None,
                    succeeded=True,
                )
            except Exception as exc:
                self._warn("[ManagedAuth] Referral ID status refresh failed", exc)
        return self._failed_status(referral_id)

    def schedule_status_refresh(
        self,
        *,
        remaining_percent: int | None,
        current_referral_id: str | None,
    ) -> bool:
        state = self.state_provider()
        service = self.release_service_provider()
        if state.ingress_frozen or service is None:
            return False
        refresh_status = getattr(service, "refresh_managed_status", None)
        legacy_refresh = getattr(service, "refresh_owned_referral_id_from_status", None)
        if not callable(refresh_status) and not callable(legacy_refresh):
            return False
        scheduled_scope = self.identity_scope(current_referral_id, state=state)
        scheduled_base = scheduled_scope[:3] if scheduled_scope is not None else None
        generation = self._refresh_generation

        async def run() -> None:
            try:
                result = await self.refresh_status_best_effort(service=service)
                current = self.state_provider()
                if (
                    current.ingress_frozen
                    or generation != self._refresh_generation
                    or service is not self.release_service_provider()
                    or not current.settings_available
                    or not current.release_settings_available
                    or not current.managed_key_visible
                ):
                    return
                refreshed_referral_id = (
                    self.normalize_referral_id(result.referral_id) or current_referral_id
                )
                current_scope = self.identity_scope(self.current_referral_id, state=current)
                allowed_scopes = {scheduled_scope}
                if scheduled_base is not None:
                    allowed_scopes.add((*scheduled_base, refreshed_referral_id))
                if current_scope not in allowed_scopes:
                    return
                self.set_view_state(
                    visible=True,
                    remaining_percent=self.remaining_percent,
                    referral_id=refreshed_referral_id,
                    pass_status=(result.pass_status if result.succeeded else _PASS_STATUS_UNSET),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warn("[ManagedAuth] Referral ID status refresh failed", exc)

        return self.refresh_owner.schedule_status_refresh(run)

    def schedule_usage_refresh(self) -> bool:
        if self.state_provider().ingress_frozen:
            return False
        return self.refresh_owner.schedule_trial_usage_refresh(self.refresh_best_effort)

    def delegate_ready(self) -> None:
        if self.state_provider().ingress_frozen:
            return
        self.pending_sink(False)
        self.schedule_usage_refresh()

    async def refresh_best_effort(self) -> None:
        try:
            await self.refresh()
        except Exception as exc:
            self._warn("[ManagedAuth] Usage refresh failed", exc)

    async def refresh(self, *, auto_show_founder_letter: bool = True) -> None:
        self._refresh_generation += 1
        generation = self._refresh_generation
        state = self.state_provider()
        if state.ingress_frozen:
            return
        if not state.settings_available or not state.managed_key_visible:
            self.clear_usage_metadata()
            self.pending_sink(False)
            self.set_view_state(
                visible=False,
                remaining_percent=None,
                referral_id=state.referral_id,
            )
            return
        if not state.release_settings_available:
            self.clear_usage_metadata()
            self.pending_sink(False)
            self.set_view_state(
                visible=True,
                remaining_percent=None,
                referral_id=state.referral_id,
                usage_status="unavailable",
            )
            return
        entitlement_ref = self.sync_usage_metadata_scope(state)
        scope = self.identity_scope(self.current_referral_id, state=state)
        self.set_view_state(
            visible=True,
            remaining_percent=self.remaining_percent,
            referral_id=self.current_referral_id,
            usage_status="checking",
        )
        started = time.monotonic()
        self._log_usage(generation, "started", "checking", started)
        failure = None
        try:
            async with asyncio.timeout(self.metadata_timeout_s):
                metadata_result = await self.metadata_fetcher()
        except asyncio.CancelledError:
            cancelled_state = self.state_provider()
            if (
                generation == self._refresh_generation
                and not cancelled_state.ingress_frozen
                and self.identity_scope(self.current_referral_id, state=cancelled_state) == scope
            ):
                self.set_view_state(
                    visible=True,
                    remaining_percent=self.remaining_percent,
                    referral_id=self.current_referral_id,
                    usage_status="unavailable",
                )
            self._log_usage(generation, "cancelled", "cancelled", started)
            raise
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, OpenRouterMetadataError)
                else OpenRouterMetadataError.from_exception(exc)
            )
            metadata_result = ManagedUsageMetadataResult(
                key_available=False, metadata=None, failure_code=failure.code
            )
        current = self.state_provider()
        if (
            current.ingress_frozen
            or generation != self._refresh_generation
            or self.identity_scope(self.current_referral_id, state=current) != scope
            or not current.settings_available
            or not current.managed_key_visible
            or not current.release_settings_available
            or current.installation_id != state.installation_id
            or current.entitlement_ref != entitlement_ref
        ):
            if generation == self._refresh_generation and not current.ingress_frozen:
                self.clear_usage_metadata()
                self.set_view_state(
                    visible=current.managed_key_visible,
                    remaining_percent=None,
                    referral_id=current.referral_id,
                    usage_status="unavailable",
                )
            self._log_usage(generation, "discarded", "superseded", started)
            return
        if metadata_result.key_available:
            self.pending_sink(False)
        metadata = metadata_result.metadata
        code = metadata_result.failure_code
        if code is None:
            code = (
                "key_missing"
                if not metadata_result.key_available
                else (
                    "metadata_unavailable"
                    if self.remaining_percent_for(metadata) is None
                    else "ready"
                )
            )
        self._log_usage(
            generation,
            "success" if code == "ready" else "failed",
            code,
            started,
            failure,
            metadata_result.failure_type,
        )
        if metadata is not None or code == "key_missing":
            self.usage_metadata = metadata
        self.usage_metadata_entitlement_ref = entitlement_ref
        referral_id = self.current_referral_id
        self.set_view_state(
            visible=True,
            remaining_percent=self.remaining_percent,
            referral_id=referral_id,
            usage_status=(
                code
                if code in {"ready", "key_missing", "storage_unavailable", "metadata_unavailable"}
                else "request_failed"
            ),
        )
        if (
            auto_show_founder_letter
            and is_effectively_exhausted(metadata)
            and self.auto_show_founder_letter_provider(metadata)
        ):
            self.disable_translation_sink(True)
        self.schedule_status_refresh(
            remaining_percent=self.remaining_percent,
            current_referral_id=referral_id,
        )

    def _log_usage(
        self, generation, result, code, started, failure=None, failure_type=None
    ) -> None:
        allowed = {
            "checking",
            "ready",
            "key_missing",
            "storage_unavailable",
            "metadata_unavailable",
            "http_error",
            "timeout",
            "dns",
            "tls",
            "proxy",
            "network",
            "unknown_error",
            "response_invalid",
            "cancelled",
            "superseded",
        }
        code = code if code in allowed else "unknown_error"
        from puripuly_heart.core.qq_auth_trace import trace_event

        trace_event(
            "usage_refresh",
            "entered" if result == "started" else "failed" if result == "failed" else "completed",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        fields = [
            "[ManagedUsage]",
            (
                "stage=secret_storage"
                if code in {"storage_unavailable", "key_missing"}
                else "stage=metadata_query"
            ),
            f"request={generation}",
            f"result={result}",
            f"code={code}",
            f"elapsed_ms={int((time.monotonic() - started) * 1000)}",
        ]
        if failure is not None:
            failure_type = failure.fields.get("error_type", failure_type)
            if type(failure.http_status) is int and 100 <= failure.http_status <= 599:
                fields.append(f"http_status={failure.http_status}")
            proxy = failure.fields.get("proxy_source")
            if proxy in {"env", "system", "direct", "bypass", "explicit", "unknown"}:
                fields.append(f"proxy_source={proxy}")
            for name in ("os_errno", "winerror", "tls_verify_code"):
                value = failure.fields.get(name)
                if type(value) is int:
                    fields.append(f"{name}={value}")
        if (
            isinstance(failure_type, str)
            and failure_type.isascii()
            and failure_type.replace("_", "").isalnum()
            and len(failure_type) <= 80
        ):
            fields.append(f"exception_type={failure_type}")
        emit_basic_log(
            logger, " ".join(fields), level=logging.WARNING if result == "failed" else logging.INFO
        )

    async def should_route_to_founder_letter(self) -> bool:
        if not self.state_provider().settings_available:
            return False
        with contextlib.suppress(Exception):
            await self.refresh(auto_show_founder_letter=False)
        if not is_effectively_exhausted(self.usage_metadata):
            return False
        self.disable_translation_sink(True)
        return True

    def clear_usage_metadata(self) -> None:
        self.usage_metadata = None
        self.usage_metadata_entitlement_ref = None

    def sync_usage_metadata_scope(
        self,
        state: ManagedUsageState | None = None,
    ) -> str | None:
        current = state or self.state_provider()
        if not current.settings_available:
            self.clear_usage_metadata()
            return None
        if current.entitlement_ref != self.usage_metadata_entitlement_ref:
            self.usage_metadata = None
            self.usage_metadata_entitlement_ref = current.entitlement_ref
        return current.entitlement_ref

    async def close(self) -> None:
        await self.refresh_owner.close()

    def stop_ingress(self) -> None:
        self.refresh_owner.stop_ingress()

    def _failed_status(self, referral_id: str | None) -> ManagedOpenRouterStatusRefreshResult:
        return ManagedOpenRouterStatusRefreshResult(
            referral_id=referral_id,
            pass_status=self.cached_pass_status_for(referral_id),
            succeeded=False,
        )

    def _on_refresh_diagnostic(
        self,
        event: str,
        metadata: Mapping[str, object],
        exception: BaseException | None,
    ) -> None:
        self._warn(
            "[ManagedAuth] Background refresh failed "
            f"event={event} kind={metadata.get('kind')} "
            f"error_type={metadata.get('error_type')}",
            exception,
        )

    def _warn(self, message: str, exception: BaseException | None = None) -> None:
        try:
            self.warning_sink(message, exception)
        except Exception:
            return


__all__ = [
    "ManagedUsageOwner",
    "ManagedUsageMetadataResult",
    "ManagedUsageState",
    "ManagedUsageViewState",
]
