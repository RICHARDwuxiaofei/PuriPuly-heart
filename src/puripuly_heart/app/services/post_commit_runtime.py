from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.post_commit_runtime import (
    AudioVadDirective,
    CommittedRuntimeSynchronizationPort,
    DashboardRetryFactsDirective,
    LanguageRuntimeDirective,
    LocaleUiProjectionDirective,
    OverlayOscDirective,
    PostCommitRuntimeExecutionResult,
    PostCommitRuntimePlan,
    PromptClipboardDirective,
    ProviderActivationDirective,
    ProviderActivationPort,
    RuntimeMutationProvenance,
    RuntimeOperation,
    RuntimeOperationalSnapshot,
    RuntimeSyncDirective,
    TranslationPolicyDirective,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.config.resolved import (
    ResolvedApplicationRuntimeConfig,
    resolve_desktop_overlay_size,
)
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_LIFECYCLE,
    DIAGNOSTIC_VISIBILITY_BASIC,
    RUNTIME_APPLY_STATUS_APPLIED,
    SEVERITY_WARNING,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    ErrorDiagnostics,
    RuntimeApplyResult,
    TransactionResult,
    UserMessageRef,
)

_SURFACE_RESPONSIBILITIES: dict[str, tuple[str, ...]] = {
    "translation_provider": ("translation_policy", "dashboard_retry_facts"),
    "stt_language_audio": (
        "language_runtime_clear",
        "audio_vad",
        "dashboard_retry_facts",
    ),
    "overlay_osc_output": ("overlay_osc", "dashboard_retry_facts"),
    "ui_prompt_clipboard_state": (
        "locale_ui_projection",
        "prompt_clipboard",
        "dashboard_retry_facts",
    ),
    "openrouter_pkce": ("translation_policy", "dashboard_retry_facts"),
    "managed_legacy": ("dashboard_retry_facts",),
}
_SYNC_OPERATION = {
    "translation_policy": "apply_translation_policy",
    "language_runtime_clear": "apply_language_runtime_clear",
    "audio_vad": "apply_audio_vad_runtime",
    "overlay_osc": "apply_overlay_osc_output_runtime",
    "locale_ui_projection": "apply_locale_ui_projection_runtime",
    "prompt_clipboard": "apply_prompt_clipboard_runtime",
    "dashboard_retry_facts": "publish_dashboard_retry_facts",
}


class RuntimeConfigResolverPort(Protocol):
    def resolve(self, receipt: SettingsCommitReceipt) -> ResolvedApplicationRuntimeConfig: ...


@dataclass(slots=True)
class PostCommitRuntimePlanBuilder:
    resolver: RuntimeConfigResolverPort

    def build(
        self,
        *,
        before: SettingsCommitReceipt | None,
        after: SettingsCommitReceipt,
        provenance: RuntimeMutationProvenance,
        operational: RuntimeOperationalSnapshot,
    ) -> PostCommitRuntimePlan:
        if provenance.reason != after.reason or provenance.correlation_id != after.correlation_id:
            raise ValueError("mutation provenance must match the committed receipt")
        valid_pair = (
            (provenance.cause == "pkce" and provenance.surface == "openrouter_pkce")
            or (provenance.cause == "managed_legacy" and provenance.surface == "managed_legacy")
            or (
                provenance.cause == "settings_surface"
                and provenance.surface
                in {
                    "translation_provider",
                    "stt_language_audio",
                    "overlay_osc_output",
                    "ui_prompt_clipboard_state",
                }
            )
        )
        if not valid_pair:
            raise ValueError("mutation cause and surface are incompatible")
        previous = self.resolver.resolve(before) if before is not None else None
        target = self.resolver.resolve(after)
        managed_legacy = (
            "managed_release_rebuild_c4" if provenance.cause == "managed_legacy" else "none"
        )
        providers = ProviderActivationDirective(
            llm=_provider_action(
                None if previous is None else previous.llm,
                target.llm,
                force=provenance.cause in {"pkce", "managed_legacy"},
                enabled=operational.translation_enabled,
                available=operational.llm_available,
                retry_pending=operational.llm_retry_pending,
            ),
            self_stt=_stt_action(
                None if previous is None else previous.self_stt,
                target.self_stt,
                enabled=operational.self_stt_enabled,
                staged=operational.self_stt_staged,
                available=operational.self_stt_available,
                retry_pending=operational.self_stt_retry_pending,
            ),
            peer_stt=_stt_action(
                None if previous is None else previous.peer_stt,
                target.peer_stt,
                enabled=operational.peer_stt_enabled,
                staged=operational.peer_stt_staged,
                available=operational.peer_stt_available,
                retry_pending=operational.peer_stt_retry_pending,
            ),
            self_stt_policy=_ingress_policy(
                operational.self_stt_enabled,
                operational.self_stt_running,
                operational.self_stt_staged,
            ),
            peer_stt_policy=_ingress_policy(
                operational.peer_stt_enabled,
                operational.peer_stt_running,
                operational.peer_stt_staged,
            ),
            managed_legacy=managed_legacy,
        )
        if provenance.surface == "overlay_osc_output":
            providers = ProviderActivationDirective(
                "retain",
                "retain",
                "retain",
                providers.self_stt_policy,
                providers.peer_stt_policy,
                "none",
            )
        return PostCommitRuntimePlan(
            before=before,
            after=after,
            operational=operational,
            request=ResolvedRuntimeActivationRequest(
                target,
                after.revision,
                after.reason,
                after.correlation_id,
                after,
            ),
            provenance=provenance,
            providers=providers,
            synchronization=_synchronization_directives(
                after, operational, _SURFACE_RESPONSIBILITIES[provenance.surface]
            ),
        )


@dataclass(slots=True)
class PostCommitRuntimeTransactionOwner:
    provider_activation: ProviderActivationPort
    synchronization: CommittedRuntimeSynchronizationPort

    async def apply(self, plan: PostCommitRuntimePlan) -> PostCommitRuntimeExecutionResult:
        operations: tuple[RuntimeOperation, ...] = (
            "provider_activation",
            *(directive.operation for directive in plan.synchronization),
        )
        completed: list[RuntimeOperation] = []
        try:
            provider_result = await self.provider_activation.activate_providers(
                plan.request, plan.providers
            )
        except Exception:
            await self._publish_reconciliation_dashboard(plan, completed)
            return _failed_execution(plan, operations, completed, "provider_activation", None)
        if provider_result.status != RUNTIME_APPLY_STATUS_APPLIED:
            await self._publish_reconciliation_dashboard(plan, completed)
            return _failed_execution(
                plan, operations, completed, "provider_activation", provider_result
            )
        completed.append("provider_activation")
        for directive in plan.synchronization:
            try:
                result = await self.synchronization.synchronize_runtime(
                    plan.request,
                    directive,
                    before=plan.before,
                    after=plan.after,
                    operational=plan.operational,
                )
            except Exception:
                return _failed_execution(plan, operations, completed, directive.operation, None)
            if result.status != RUNTIME_APPLY_STATUS_APPLIED:
                return _failed_execution(plan, operations, completed, directive.operation, result)
            completed.append(directive.operation)
        return PostCommitRuntimeExecutionResult(
            transaction=TransactionResult(
                TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
                None,
                None,
            ),
            completed=tuple(completed),
            failed=None,
            skipped=(),
            reconciliation_required=False,
        )

    async def _publish_reconciliation_dashboard(
        self,
        plan: PostCommitRuntimePlan,
        completed: list[RuntimeOperation],
    ) -> None:
        directive = next(
            (item for item in plan.synchronization if item.operation == "dashboard_retry_facts"),
            None,
        )
        if directive is None:
            return
        try:
            result = await self.synchronization.synchronize_runtime(
                plan.request,
                directive,
                before=plan.before,
                after=plan.after,
                operational=plan.operational,
            )
        except Exception:
            return
        if result.status == RUNTIME_APPLY_STATUS_APPLIED:
            completed.append("dashboard_retry_facts")


def _provider_action(
    previous,
    target,
    *,
    force: bool,
    enabled: bool,
    available: bool,
    retry_pending: bool,
):  # noqa: ANN001, ANN202
    if not enabled:
        return "clear"
    return (
        "replace"
        if force or not available or retry_pending or previous is None or previous != target
        else "retain"
    )


def _stt_action(
    previous,
    target,
    *,
    enabled: bool,
    staged: bool,
    available: bool,
    retry_pending: bool,
):  # noqa: ANN001, ANN202
    if not enabled and not staged:
        return "clear"
    return (
        "replace"
        if not available or retry_pending or previous is None or previous != target
        else "retain"
    )


def _ingress_policy(enabled: bool, running: bool, staged: bool):  # noqa: ANN202
    if enabled and running:
        return "active"
    if staged or enabled:
        return "staged"
    return "inactive"


def _synchronization_directives(
    after: SettingsCommitReceipt,
    operational: RuntimeOperationalSnapshot,
    operations: tuple[str, ...],
) -> tuple[RuntimeSyncDirective, ...]:
    intent = after.envelope.intent
    desktop_width, desktop_height = resolve_desktop_overlay_size(
        intent.overlay.desktop_flet.size_preset
    )
    values: dict[str, RuntimeSyncDirective] = {
        "translation_policy": TranslationPolicyDirective(
            "translation_policy",
            operational.translation_enabled,
        ),
        "language_runtime_clear": LanguageRuntimeDirective(
            "language_runtime_clear",
            intent.languages.source_language,
            intent.languages.target_language,
            intent.languages.peer_source_language,
            intent.languages.peer_target_language,
        ),
        "audio_vad": AudioVadDirective(
            "audio_vad",
            intent.audio.input_host_api,
            intent.audio.input_device,
            intent.desktop_audio.output_device,
            intent.audio.ring_buffer_ms,
            intent.stt.vad_speech_threshold,
            intent.desktop_audio.vad_speech_threshold,
            intent.desktop_audio.vad_hangover_ms,
            intent.desktop_audio.vad_pre_roll_ms,
        ),
        "overlay_osc": OverlayOscDirective(
            "overlay_osc",
            intent.overlay.target,
            intent.overlay.show_translation,
            intent.overlay.show_peer_original,
            intent.osc.host,
            intent.osc.port,
            intent.osc.chatbox_address,
            intent.osc.chatbox_send,
            intent.osc.chatbox_clear,
            intent.osc.chatbox_max_chars,
            intent.osc.vrc_mic_intercept,
            intent.osc.chatbox_include_source,
            intent.overlay.calibration.copy(),
            {
                "size_preset": intent.overlay.desktop_flet.size_preset,
                "size": {
                    "width": desktop_width,
                    "height": desktop_height,
                },
                "position": {
                    "x": intent.overlay.desktop_flet.position.x,
                    "y": intent.overlay.desktop_flet.position.y,
                },
                "visual": {
                    "text_scale": 1.0,
                    "background_alpha": intent.overlay.desktop_flet.visual.background_alpha,
                    "outline_width": None,
                },
                "interaction_mode": "edit",
            },
        ),
        "locale_ui_projection": LocaleUiProjectionDirective(
            "locale_ui_projection", intent.ui.locale
        ),
        "prompt_clipboard": PromptClipboardDirective(
            "prompt_clipboard",
            intent.prompts.system_prompt,
            intent.clipboard.auto_translate_enabled,
        ),
        "dashboard_retry_facts": DashboardRetryFactsDirective(
            "dashboard_retry_facts",
            operational.llm_available,
            operational.llm_retry_pending,
            operational.self_stt_available,
            operational.self_stt_retry_pending,
            operational.peer_stt_available,
            operational.peer_stt_retry_pending,
        ),
    }
    return tuple(values[operation] for operation in operations)


def _failed_execution(
    plan: PostCommitRuntimePlan,
    operations: tuple[RuntimeOperation, ...],
    completed: list[RuntimeOperation],
    failed: RuntimeOperation,
    result: RuntimeApplyResult | None,
) -> PostCommitRuntimeExecutionResult:
    failed_index = operations.index(failed)
    skipped = tuple(
        operation for operation in operations[failed_index + 1 :] if operation not in completed
    )
    operation = (
        "apply_provider_runtime" if failed == "provider_activation" else _SYNC_OPERATION[failed]
    )
    code = (
        "provider_runtime_apply_failed"
        if failed == "provider_activation"
        else (
            "overlay_osc_output_runtime_apply_exception"
            if failed == "overlay_osc"
            else f"{failed}_runtime_apply_failed"
        )
    )
    message = result.message if result is not None and result.message is not None else _message()
    diagnostics = _diagnostics(
        plan,
        operation=operation,
        code=code,
        completed=tuple(completed),
        failed=failed,
        skipped=skipped,
        prior=result.diagnostics if result is not None else None,
    )
    return PostCommitRuntimeExecutionResult(
        transaction=TransactionResult(
            TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
            message,
            diagnostics,
        ),
        completed=tuple(completed),
        failed=failed,
        skipped=skipped,
        reconciliation_required=True,
    )


def _message() -> UserMessageRef:
    return UserMessageRef(
        "settings.mutation.runtime_apply_failed",
        {"phase": "runtime_apply"},
        SEVERITY_WARNING,
    )


def _diagnostics(
    plan: PostCommitRuntimePlan,
    *,
    operation: str,
    code: str,
    completed: tuple[RuntimeOperation, ...],
    failed: RuntimeOperation,
    skipped: tuple[RuntimeOperation, ...],
    prior: ErrorDiagnostics | None,
) -> ErrorDiagnostics:
    fields = {
        "surface": plan.provenance.surface,
        "failed_operation": failed,
        "completed_operations": ",".join(completed),
        "skipped_operations": ",".join(skipped),
        "reconciliation_required": True,
    }
    if failed == "overlay_osc":
        fields = {"surface": plan.provenance.surface}
    if prior is not None:
        fields["reported_component"] = prior.component
        fields["reported_code"] = prior.code
    return ErrorDiagnostics(
        component="gui_controller" if failed == "overlay_osc" else "post_commit_runtime",
        operation=operation,
        code=code,
        category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
        visibility=DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields=fields,
    )


__all__ = ["PostCommitRuntimePlanBuilder", "PostCommitRuntimeTransactionOwner"]
