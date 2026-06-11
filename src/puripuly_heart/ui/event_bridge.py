from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import OrderedDict
from collections.abc import Mapping
from typing import Protocol

import flet as ft

from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterUserFacingError
from puripuly_heart.core.messages import UserErrorReport, UserMessageRef
from puripuly_heart.core.runtime_logging import SessionLoggingMode, SessionRuntimeLoggingService
from puripuly_heart.domain.events import STTSessionState, UIEvent, UIEventType
from puripuly_heart.domain.models import OSCMessage, Transcript, Translation
from puripuly_heart.ui.i18n import localize_user_message_ref, t

logger = logging.getLogger(__name__)

_FINAL_TRANSCRIPT_CACHE_LIMIT = 500


def _short_visual_debug_token(value: object | None) -> str:
    text = "" if value is None else str(value).strip()
    normalized = "".join(char for char in text if char.isalnum())
    return (normalized[:4] or "none").lower()


class DashboardEventDestination(Protocol):
    def publish_status(self, status: str) -> None: ...

    def publish_transcript(
        self,
        text: str,
        *,
        language_code: str | None = None,
        utterance_id: object | None = None,
        channel: str | None = None,
        source_text_len: int | None = None,
        transcript_kind: str | None = None,
        should_log: bool = False,
        debug_prefix: str | None = None,
    ) -> bool | None: ...

    def publish_translation(
        self,
        text: str,
        *,
        language_code: str | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        utterance_id: object | None = None,
        channel: str | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
        debug_prefix: str | None = None,
    ) -> bool | None: ...

    def publish_error(self, text: str) -> None: ...


class HistoryEventDestination(Protocol):
    def append_entry(
        self,
        source: str,
        text: str,
        *,
        translated: bool = False,
        language_code: str | None = None,
    ) -> None: ...


class ConversationEventDestination(Protocol):
    def append_record(
        self,
        *,
        source: str,
        channel: str,
        source_text: str,
        translated_text: str,
        origin_wall_clock_ms: int | None = None,
    ) -> None: ...


class ErrorEventDestination(Protocol):
    def publish_error(
        self,
        text: str,
        *,
        payload: object | None,
        event: UIEvent,
    ) -> bool: ...


class AppDashboardEventDestination:
    def __init__(self, app: object) -> None:
        self._app = app

    def _dashboard(self) -> object | None:
        return getattr(self._app, "view_dashboard", None)

    def publish_status(self, status: str) -> None:
        dashboard = self._dashboard()
        if dashboard is not None:
            dashboard.set_status(status)

    def publish_transcript(
        self,
        text: str,
        *,
        language_code: str | None = None,
        utterance_id: object | None = None,
        channel: str | None = None,
        source_text_len: int | None = None,
        transcript_kind: str | None = None,
        should_log: bool = False,
        debug_prefix: str | None = None,
    ) -> bool:
        dashboard = self._dashboard()
        if dashboard is None:
            return False
        dashboard.set_display_text(
            text,
            language_code=language_code,
            utterance_id=utterance_id,
            channel=channel,
            source_text_len=source_text_len,
            transcript_kind=transcript_kind,
            should_log=should_log,
            debug_prefix=debug_prefix,
        )
        return True

    def publish_translation(
        self,
        text: str,
        *,
        language_code: str | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        utterance_id: object | None = None,
        channel: str | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
        debug_prefix: str | None = None,
    ) -> bool:
        dashboard = self._dashboard()
        if dashboard is None:
            return False
        dashboard.set_display_translation_text(
            text,
            language_code=language_code,
            update_id=update_id,
            origin_wall_clock_ms=origin_wall_clock_ms,
            utterance_id=utterance_id,
            channel=channel,
            session_scope=session_scope,
            source_text_hash=source_text_hash,
            source_text_len=source_text_len,
            logical_turn_key=logical_turn_key,
            debug_prefix=debug_prefix,
        )
        return True

    def publish_error(self, text: str) -> None:
        dashboard = self._dashboard()
        if dashboard is not None:
            dashboard.set_display_text(text, is_error=True)


class AppHistoryEventDestination:
    def __init__(self, app: object) -> None:
        self._app = app

    def append_entry(
        self,
        source: str,
        text: str,
        *,
        translated: bool = False,
        language_code: str | None = None,
    ) -> None:
        add_history = getattr(self._app, "add_history_entry", None)
        if callable(add_history):
            add_history(source, text, translated=translated, language_code=language_code)


class AppConversationEventDestination:
    def __init__(self, app: object) -> None:
        self._app = app

    def append_record(
        self,
        *,
        source: str,
        channel: str,
        source_text: str,
        translated_text: str,
        origin_wall_clock_ms: int | None = None,
    ) -> None:
        append_record = getattr(
            getattr(self._app, "view_logs", None), "append_conversation_record", None
        )
        if callable(append_record):
            append_record(
                source=source,
                channel=channel,
                source_text=source_text,
                translated_text=translated_text,
                origin_wall_clock_ms=origin_wall_clock_ms,
            )


class AppErrorEventDestination:
    def __init__(
        self,
        *,
        app: object,
        runtime_logging: SessionRuntimeLoggingService | None,
    ) -> None:
        self._app = app
        self._runtime_logging = runtime_logging

    def publish_error(
        self,
        text: str,
        *,
        payload: object | None,
        event: UIEvent,
    ) -> bool:
        self._emit_runtime_error_log(text, runtime_log_handled=event.runtime_log_handled)
        if _is_managed_openrouter_error_payload(payload):
            self._clear_managed_auth_pending_state()
            if self._show_managed_auth_snackbar(text):
                return False
        return self._should_display_dashboard_error(text)

    def _emit_runtime_error_log(self, text: str, *, runtime_log_handled: bool) -> None:
        try:
            if self._runtime_logging is not None:
                if not runtime_log_handled:
                    self._runtime_logging.emit_basic(text, level=logging.ERROR)
            else:
                logger.error(text)
        except Exception:
            logger.error(text)

    def _clear_managed_auth_pending_state(self) -> None:
        controller = getattr(self._app, "controller", None)
        clear_pending = (
            getattr(controller, "clear_managed_auth_pending_state", None)
            if controller is not None
            else None
        )
        if callable(clear_pending):
            with contextlib.suppress(Exception):
                clear_pending()

    def _show_managed_auth_snackbar(self, text: str) -> bool:
        show_snackbar = getattr(self._app, "_show_snackbar", None)
        if not callable(show_snackbar):
            return False
        with contextlib.suppress(Exception):
            show_snackbar(text, ft.Colors.ORANGE_700)
            return True
        return False

    def _should_display_dashboard_error(self, text: str) -> bool:
        controller = getattr(self._app, "controller", None)
        hub = getattr(controller, "hub", None)
        stt = getattr(hub, "stt", None)
        stt_state = getattr(stt, "state", None)
        msg_lower = text.lower()
        return not (
            "soniox" in msg_lower
            and "400" in msg_lower
            and stt_state in (STTSessionState.DRAINING, STTSessionState.DISCONNECTED)
        )


def _localized_error_event_text(payload: object | None) -> str:
    if isinstance(payload, ManagedOpenRouterUserFacingError):
        return t(payload.message_key, **_safe_i18n_params(payload.message_kwargs))
    if isinstance(payload, UserErrorReport):
        return localize_user_message_ref(payload.message)
    if isinstance(payload, UserMessageRef):
        return localize_user_message_ref(payload)
    return str(payload) if payload is not None else t("error.unknown")


def _safe_i18n_params(params: Mapping[str, object]) -> dict[str, object]:
    safe_params: dict[str, object] = {}
    for key, value in params.items():
        if not isinstance(key, str) or len(key) > 64:
            continue
        if value is None or isinstance(value, str | int | float | bool):
            safe_params[key] = value
    return safe_params


def _is_managed_openrouter_error_payload(payload: object | None) -> bool:
    if isinstance(payload, ManagedOpenRouterUserFacingError):
        return True
    if isinstance(payload, UserErrorReport):
        return _is_managed_openrouter_message(payload.message)
    if isinstance(payload, UserMessageRef):
        return _is_managed_openrouter_message(payload)
    return False


def _is_managed_openrouter_message(message: UserMessageRef) -> bool:
    return message.key.startswith("managed_release.")


class UIEventBridge:
    def __init__(
        self,
        *,
        app: object,
        event_queue: asyncio.Queue[UIEvent],
        runtime_logging: SessionRuntimeLoggingService | None = None,
        dashboard_destination: DashboardEventDestination | None = None,
        history_destination: HistoryEventDestination | None = None,
        conversation_destination: ConversationEventDestination | None = None,
        error_destination: ErrorEventDestination | None = None,
    ):
        self.app = app
        self.event_queue = event_queue
        self.runtime_logging = runtime_logging
        self.dashboard_destination = dashboard_destination or AppDashboardEventDestination(app)
        self.history_destination = history_destination or AppHistoryEventDestination(app)
        self.conversation_destination = conversation_destination or AppConversationEventDestination(
            app
        )
        self.error_destination = error_destination or AppErrorEventDestination(
            app=app,
            runtime_logging=runtime_logging,
        )
        self._running = False
        self._closed = False
        self._primary_first_partial_emitted: set[str] = set()
        self._final_self_transcripts: OrderedDict[str, Transcript] = OrderedDict()

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

    def _remember_final_self_transcript(self, transcript: Transcript) -> None:
        if self._closed:
            return
        if transcript.channel != "self" or not transcript.is_final:
            return
        key = str(transcript.utterance_id)
        self._final_self_transcripts[key] = transcript
        self._final_self_transcripts.move_to_end(key)
        while len(self._final_self_transcripts) > _FINAL_TRANSCRIPT_CACHE_LIMIT:
            self._final_self_transcripts.popitem(last=False)

    def _source_text_for_translation(self, translation: Translation) -> str:
        source_text = translation.source_text.strip()
        if source_text:
            return source_text
        transcript = self._final_self_transcripts.get(str(translation.utterance_id))
        if transcript is None:
            return ""
        return transcript.text.strip()

    def _append_conversation_record(self, translation: Translation, *, source: str) -> None:
        if self._closed:
            return
        if translation.channel != "self":
            return
        translated_text = translation.text.strip()
        if not translated_text:
            return
        source_text = self._source_text_for_translation(translation)
        if not source_text:
            return
        try:
            self.conversation_destination.append_record(
                source=source,
                channel=translation.channel,
                source_text=source_text,
                translated_text=translated_text,
                origin_wall_clock_ms=translation.origin_wall_clock_ms,
            )
        except Exception:
            logger.exception("Failed to append conversation record")

    def _visual_debug_prefix(
        self,
        *,
        channel: str | None,
        utterance_id: object | None,
        update_id: str | None = None,
    ) -> str | None:
        if channel != "peer" or utterance_id is None:
            return None
        mode = getattr(self.runtime_logging, "mode", None)
        mode_value = getattr(mode, "value", mode)
        if mode_value != SessionLoggingMode.DETAILED.value:
            return None
        turn_token = _short_visual_debug_token(utterance_id)
        stage_token = _short_visual_debug_token(update_id) if update_id else "src"
        return f"[P {turn_token}/{stage_token}]"

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

    def _schedule_github_star_prompt_translation_success(self, translation: Translation) -> None:
        if not translation.text.strip():
            return
        controller = getattr(self.app, "controller", None)
        scheduler = getattr(
            controller,
            "schedule_github_star_prompt_translation_success_observed",
            None,
        )
        if not callable(scheduler):
            return
        with contextlib.suppress(Exception):
            scheduler()

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
            while self._running and not self._closed:
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
        finally:
            self._running = False

    def close(self) -> None:
        self._closed = True
        self._running = False
        self._primary_first_partial_emitted.clear()
        self._final_self_transcripts.clear()

    async def _handle_event(self, event: UIEvent) -> None:
        if self._closed:
            return
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
            self.dashboard_destination.publish_status(status)
            return

        if event.type in (UIEventType.TRANSCRIPT_PARTIAL, UIEventType.TRANSCRIPT_FINAL):
            transcript = event.payload
            if not isinstance(transcript, Transcript):
                return
            source = event.source or "Mic"
            source_lang, _ = self._get_language_codes()

            is_final = event.type == UIEventType.TRANSCRIPT_FINAL
            utterance_key = str(transcript.utterance_id)
            if is_final:
                self._primary_first_partial_emitted.discard(utterance_key)
                should_log = True
                transcript_kind = "final"
            else:
                should_log = utterance_key not in self._primary_first_partial_emitted
                if should_log:
                    self._primary_first_partial_emitted.add(utterance_key)
                transcript_kind = "partial"

            self.dashboard_destination.publish_transcript(
                transcript.text,
                language_code=source_lang,
                utterance_id=transcript.utterance_id,
                channel=transcript.channel,
                source_text_len=len(transcript.text),
                transcript_kind=transcript_kind,
                should_log=should_log,
                debug_prefix=self._visual_debug_prefix(
                    channel=transcript.channel,
                    utterance_id=transcript.utterance_id,
                ),
            )

            if is_final:
                self._remember_final_self_transcript(transcript)
                self.history_destination.append_entry(
                    source, transcript.text, language_code=source_lang
                )
            return

        if event.type == UIEventType.TRANSLATION_DONE:
            translation = event.payload
            if not isinstance(translation, Translation):
                return
            source = event.source or "Mic"
            _, target_lang = self._get_language_codes()
            dashboard_published = self.dashboard_destination.publish_translation(
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
                debug_prefix=self._visual_debug_prefix(
                    channel=translation.channel,
                    utterance_id=translation.utterance_id,
                    update_id=translation.update_id,
                ),
            )
            if dashboard_published is not False:
                self._emit_dashboard_translation_applied_detailed(
                    translation=translation,
                    source_label=source,
                    dashboard_target_language=target_lang,
                )
            self._append_conversation_record(translation, source=source)
            self.history_destination.append_entry(
                source,
                translation.text,
                translated=True,
                language_code=target_lang,
            )
            self._schedule_github_star_prompt_translation_success(translation)
            return

        if event.type == UIEventType.OSC_SENT:
            msg = event.payload
            if not isinstance(msg, OSCMessage):
                return
            source_lang, target_lang = self._get_language_codes()
            lang_code = target_lang if self._translation_enabled() else source_lang
            self.history_destination.append_entry("VRChat", msg.text, language_code=lang_code)
            return

        if event.type == UIEventType.ERROR:
            payload = event.payload
            text = _localized_error_event_text(payload)
            if self.error_destination.publish_error(text, payload=payload, event=event):
                self.dashboard_destination.publish_error(text)
            return
