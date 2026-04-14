from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import flet as ft

from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterUserFacingError
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.events import STTSessionState, UIEvent, UIEventType
from puripuly_heart.domain.models import OSCMessage, Transcript, Translation
from puripuly_heart.ui.i18n import t

logger = logging.getLogger(__name__)


class UIEventBridge:
    def __init__(
        self,
        *,
        app: object,
        event_queue: asyncio.Queue[UIEvent],
        runtime_logging: SessionRuntimeLoggingService | None = None,
    ):
        self.app = app
        self.event_queue = event_queue
        self.runtime_logging = runtime_logging
        self._running = False

    def _get_language_codes(self) -> tuple[str | None, str | None]:
        controller = getattr(self.app, "controller", None)
        settings = getattr(controller, "settings", None)
        if settings is None:
            return None, None
        return settings.languages.source_language, settings.languages.target_language

    def _translation_enabled(self) -> bool:
        controller = getattr(self.app, "controller", None)
        hub = getattr(controller, "hub", None)
        return bool(getattr(hub, "translation_enabled", False))

    def _emit_dashboard_translation_applied_detailed(
        self,
        *,
        translation: Translation,
        source_label: str,
        dashboard_target_language: str | None,
    ) -> None:
        if self.runtime_logging is None:
            return
        message = (
            "[Detailed][UIEventBridge] dashboard_translation_applied "
            f"utterance_id={translation.utterance_id} "
            f"channel={translation.channel} "
            f"source_label={json.dumps(source_label, ensure_ascii=False)} "
            f"dashboard_target_language={dashboard_target_language} "
            f"translation_target_language={translation.target_language} "
            f"text_len={len(translation.text)}"
        )
        with contextlib.suppress(Exception):
            self.runtime_logging.emit_detailed(message)

    def report_overlay_state(
        self,
        state: str,
        *,
        failure_reason: str | None = None,
    ) -> None:
        state_handler = getattr(self.app, "on_overlay_state_changed", None)
        if callable(state_handler):
            state_handler(state=state, failure_reason=failure_reason)

    async def run(self) -> None:
        self._running = True
        logger.info("UI Event Bridge started")
        try:
            while self._running:
                event = await self.event_queue.get()
                try:
                    await self._handle_event(event)
                except Exception:
                    logger.exception("Error handling UI event")
                finally:
                    self.event_queue.task_done()
        except asyncio.CancelledError:
            logger.info("UI Event Bridge cancelled")
            raise

    async def _handle_event(self, event: UIEvent) -> None:
        if event.type == UIEventType.SESSION_STATE_CHANGED:
            state = event.payload
            state_name = getattr(state, "name", "")
            if state_name == "CONNECTING":
                status = "connecting"
            elif state_name == "STREAMING":
                status = "connected"
            elif state_name == "DRAINING":
                status = "stopping"
            else:
                status = "disconnected"
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_status(status)
            return

        if event.type in (UIEventType.TRANSCRIPT_PARTIAL, UIEventType.TRANSCRIPT_FINAL):
            transcript = event.payload
            if not isinstance(transcript, Transcript):
                return
            source = event.source or "Mic"
            source_lang, _ = self._get_language_codes()

            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_display_text(transcript.text, language_code=source_lang)

            if event.type == UIEventType.TRANSCRIPT_FINAL:
                add_history = getattr(self.app, "add_history_entry", None)
                if add_history is not None:
                    add_history(source, transcript.text, language_code=source_lang)
            return

        if event.type == UIEventType.TRANSLATION_DONE:
            translation = event.payload
            if not isinstance(translation, Translation):
                return
            source = event.source or "Mic"
            _, target_lang = self._get_language_codes()
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_display_translation_text(
                    translation.text,
                    language_code=target_lang,
                    update_id=translation.update_id,
                    origin_wall_clock_ms=translation.origin_wall_clock_ms,
                    utterance_id=translation.utterance_id,
                    channel=translation.channel,
                    session_scope=translation.session_scope,
                    source_text_hash=translation.source_text_hash,
                    source_text_len=translation.source_text_len,
                    logical_turn_key=translation.logical_turn_key,
                )
                self._emit_dashboard_translation_applied_detailed(
                    translation=translation,
                    source_label=source,
                    dashboard_target_language=target_lang,
                )
            add_history = getattr(self.app, "add_history_entry", None)
            if add_history is not None:
                add_history(source, translation.text, translated=True, language_code=target_lang)
            return

        if event.type == UIEventType.OSC_SENT:
            msg = event.payload
            if not isinstance(msg, OSCMessage):
                return
            source_lang, target_lang = self._get_language_codes()
            lang_code = target_lang if self._translation_enabled() else source_lang
            add_history = getattr(self.app, "add_history_entry", None)
            if add_history is not None:
                add_history("VRChat", msg.text, language_code=lang_code)
            return

        if event.type == UIEventType.ERROR:
            payload = event.payload
            text = str(payload) if payload is not None else t("error.unknown")
            controller = getattr(self.app, "controller", None)
            try:
                if self.runtime_logging is not None:
                    if not event.runtime_log_handled:
                        self.runtime_logging.emit_basic(text, level=logging.ERROR)
                else:
                    logger.error(text)
            except Exception:
                logger.error(text)
            if isinstance(payload, ManagedOpenRouterUserFacingError):
                clear_pending = (
                    getattr(controller, "clear_managed_auth_pending_state", None)
                    if controller is not None
                    else None
                )
                if callable(clear_pending):
                    with contextlib.suppress(Exception):
                        clear_pending()
                show_snackbar = getattr(self.app, "_show_snackbar", None)
                if callable(show_snackbar):
                    with contextlib.suppress(Exception):
                        show_snackbar(text, ft.Colors.ORANGE_700)
                        return
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                msg_lower = text.lower()
                controller = getattr(self.app, "controller", None)
                hub = getattr(controller, "hub", None)
                stt = getattr(hub, "stt", None)
                stt_state = getattr(stt, "state", None)
                if (
                    "soniox" in msg_lower
                    and "400" in msg_lower
                    and stt_state in (STTSessionState.DRAINING, STTSessionState.DISCONNECTED)
                ):
                    return
                dash.set_display_text(text, is_error=True)
            return
