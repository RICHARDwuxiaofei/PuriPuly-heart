from __future__ import annotations

from puripuly_heart.app.ports.dashboard_presentation import PresentationEventBridgeRequest
from puripuly_heart.ui.event_dispatch import (
    AppConversationEventDestination,
    AppDashboardEventDestination,
    AppErrorEventDestination,
    AppHistoryEventDestination,
    ConversationEventDestination,
    DashboardEventDestination,
    ErrorEventDestination,
    HistoryEventDestination,
    RuntimeLoggingPort,
    UIEventBridge,
)


class ApplicationUIEventBridgeFactory:
    def __init__(self, presentation) -> None:  # noqa: ANN001
        self._presentation = presentation

    def create_event_bridge(self, request: PresentationEventBridgeRequest) -> UIEventBridge:
        presentation = self._presentation
        context = request.context

        def event_context():  # noqa: ANN202
            return context.current_event_context()

        return UIEventBridge(
            event_queue=request.event_queue,
            runtime_logging=request.runtime_logging,
            dashboard_destination=AppDashboardEventDestination(presentation.view_dashboard),
            history_destination=AppHistoryEventDestination(presentation.add_history_entry),
            conversation_destination=AppConversationEventDestination(
                presentation.view_logs.append_conversation_record
            ),
            get_language_codes=lambda: (
                event_context().source_language,
                event_context().target_language,
            ),
            is_translation_enabled=lambda: event_context().translation_enabled,
            get_stt_state=lambda: event_context().self_stt_state,
            clear_managed_auth_pending=context.clear_managed_auth_pending,
            show_snackbar=presentation.show_snackbar,
            on_github_star_translation_success=context.observe_translation_success,
            on_telemetry_translation_success=presentation.on_telemetry_translation_success,
            on_overlay_state_changed=presentation.on_overlay_state_changed,
        )


__all__ = [
    "AppConversationEventDestination",
    "AppDashboardEventDestination",
    "AppErrorEventDestination",
    "AppHistoryEventDestination",
    "ApplicationUIEventBridgeFactory",
    "ConversationEventDestination",
    "DashboardEventDestination",
    "ErrorEventDestination",
    "HistoryEventDestination",
    "RuntimeLoggingPort",
    "UIEventBridge",
]
