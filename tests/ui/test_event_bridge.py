from __future__ import annotations

import asyncio
import io
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import flet as ft
import pytest

pytest.importorskip("flet")

from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterReleaseDiagnostics,
    ManagedOpenRouterUserFacingError,
)
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.events import STTSessionState, UIEvent, UIEventType
from puripuly_heart.domain.models import OSCMessage, Transcript, Translation
from puripuly_heart.ui import event_bridge as event_bridge_module
from puripuly_heart.ui.event_bridge import UIEventBridge
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.views.logs import FletLogHandler


class DummyDashboard:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.display_calls: list[tuple[str, str | None, bool]] = []
        self.translation_calls: list[tuple[str, str | None]] = []
        self.translation_metadata_calls: list[dict[str, object]] = []
        self.notice_calls: list[str | None] = []

    def set_status(self, status: str) -> None:
        self.statuses.append(status)

    def set_display_text(
        self,
        text: str,
        *,
        language_code: str | None = None,
        is_error: bool = False,
    ) -> None:
        self.display_calls.append((text, language_code, is_error))

    def set_display_translation_text(
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
    ) -> None:
        self.translation_calls.append((text, language_code))
        self.translation_metadata_calls.append(
            {
                "update_id": update_id,
                "origin_wall_clock_ms": origin_wall_clock_ms,
                "utterance_id": utterance_id,
                "channel": channel,
                "session_scope": session_scope,
                "source_text_hash": source_text_hash,
                "source_text_len": source_text_len,
                "logical_turn_key": logical_turn_key,
            }
        )

    def set_local_stt_notice(self, status: str | None) -> None:
        self.notice_calls.append(status)


class FailingTranslationDashboard(DummyDashboard):
    def set_display_translation_text(
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
    ) -> None:
        _ = (
            text,
            language_code,
            update_id,
            origin_wall_clock_ms,
            utterance_id,
            channel,
            session_scope,
            source_text_hash,
            source_text_len,
            logical_turn_key,
        )
        raise RuntimeError("dashboard setter failed")


class DummyLogs:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append_log(self, line: str) -> None:
        self.lines.append(line)


class DummyApp:
    def __init__(self) -> None:
        self.view_dashboard = DummyDashboard()
        self.view_logs = DummyLogs()
        self.snackbar_calls: list[tuple[str, object]] = []
        self.clear_managed_auth_pending_calls = 0
        self.history: list[tuple[str, str, bool, str | None]] = []
        self.overlay_state = "off"
        self.overlay_failure_reason: str | None = None
        self.controller = SimpleNamespace(
            settings=SimpleNamespace(
                languages=SimpleNamespace(source_language="ko", target_language="en")
            ),
            hub=SimpleNamespace(
                translation_enabled=False,
                stt=SimpleNamespace(state=STTSessionState.STREAMING),
            ),
            managed_auth_pending=False,
            clear_managed_auth_pending_state=lambda: self._record_clear_managed_auth_pending(),
        )

    def _record_clear_managed_auth_pending(self) -> None:
        self.clear_managed_auth_pending_calls += 1
        self.controller.managed_auth_pending = False

    def _show_snackbar(self, message: str, bgcolor, duration: int = 4000) -> None:
        _ = duration
        self.snackbar_calls.append((message, bgcolor))

    def add_history_entry(
        self,
        source: str,
        text: str,
        *,
        translated: bool = False,
        language_code: str | None = None,
    ) -> None:
        self.history.append((source, text, translated, language_code))

    def on_overlay_state_changed(
        self,
        *,
        state: str,
        failure_reason: str | None = None,
    ) -> None:
        self.overlay_state = state
        self.overlay_failure_reason = failure_reason


class RuntimeLoggingCapture:
    def __init__(
        self,
        *,
        detailed_enabled: bool = True,
        detailed_error: Exception | None = None,
    ) -> None:
        self.detailed_enabled = detailed_enabled
        self.detailed_error = detailed_error
        self.basic_messages: list[tuple[int, str]] = []
        self.detailed_calls: list[tuple[int, str]] = []
        self.detailed_messages: list[tuple[int, str]] = []

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self.basic_messages.append((level, message))

    def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        self.detailed_calls.append((level, message))
        if self.detailed_error is not None:
            raise self.detailed_error
        if not self.detailed_enabled:
            return False
        self.detailed_messages.append((level, message))
        return True


def assert_dashboard_translation_applied_marker(
    message: str,
    *,
    utterance_id: str,
    channel: str,
    source_label: str,
    dashboard_target_language: str | None,
    translation_target_language: str | None,
    text_len: int,
) -> None:
    assert "dashboard_translation_applied" in message
    assert f"utterance_id={utterance_id}" in message
    assert f"channel={channel}" in message
    assert f"source_label={json.dumps(source_label, ensure_ascii=False)}" in message
    assert f"dashboard_target_language={dashboard_target_language}" in message
    assert f"translation_target_language={translation_target_language}" in message
    assert f"text_len={text_len}" in message


@pytest.mark.asyncio
async def test_event_bridge_maps_session_and_transcript_events() -> None:
    app = DummyApp()
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())
    utterance_id = uuid4()

    await bridge._handle_event(
        UIEvent(type=UIEventType.SESSION_STATE_CHANGED, payload=STTSessionState.CONNECTING)
    )
    await bridge._handle_event(
        UIEvent(type=UIEventType.SESSION_STATE_CHANGED, payload=STTSessionState.STREAMING)
    )
    await bridge._handle_event(
        UIEvent(type=UIEventType.SESSION_STATE_CHANGED, payload=STTSessionState.DRAINING)
    )
    await bridge._handle_event(
        UIEvent(type=UIEventType.SESSION_STATE_CHANGED, payload=STTSessionState.DISCONNECTED)
    )

    partial = Transcript(utterance_id=utterance_id, text="partial", is_final=False)
    final = Transcript(utterance_id=utterance_id, text="final", is_final=True)
    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSCRIPT_PARTIAL, payload=partial, source="Mic")
    )
    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSCRIPT_FINAL, payload=final, source="Mic")
    )
    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSCRIPT_PARTIAL, payload="not-transcript")
    )

    assert app.view_dashboard.statuses == ["connecting", "connected", "stopping", "disconnected"]
    assert app.view_dashboard.display_calls[:2] == [
        ("partial", "ko", False),
        ("final", "ko", False),
    ]
    assert app.view_dashboard.notice_calls == []
    assert app.history == [("Mic", "final", False, "ko")]


@pytest.mark.asyncio
async def test_event_bridge_routes_translation_and_osc_history_by_language_mode() -> None:
    app = DummyApp()
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())
    utterance_id = uuid4()

    translation = Translation(utterance_id=utterance_id, text="translated")
    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSLATION_DONE, payload=translation, source="Mic")
    )
    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSLATION_DONE, payload="not-translation")
    )

    app.controller.hub.translation_enabled = True
    await bridge._handle_event(
        UIEvent(
            type=UIEventType.OSC_SENT,
            payload=OSCMessage(utterance_id=utterance_id, text="hello", created_at=0.0),
        )
    )

    app.controller.hub.translation_enabled = False
    await bridge._handle_event(
        UIEvent(
            type=UIEventType.OSC_SENT,
            payload=OSCMessage(utterance_id=utterance_id, text="bye", created_at=0.0),
        )
    )

    assert app.view_dashboard.translation_calls == [("translated", "en")]
    assert ("Mic", "translated", True, "en") in app.history
    assert ("VRChat", "hello", False, "en") in app.history
    assert ("VRChat", "bye", False, "ko") in app.history


@pytest.mark.asyncio
async def test_event_bridge_logs_self_dashboard_translation_applied_detail_only() -> None:
    app = DummyApp()
    runtime_logging = RuntimeLoggingCapture()
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )
    utterance_id = uuid4()
    translation = Translation(
        utterance_id=utterance_id,
        text="translated self",
        channel="self",
        target_language="en",
    )

    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSLATION_DONE, payload=translation, source="Mic")
    )

    assert app.view_dashboard.translation_calls == [("translated self", "en")]
    assert app.history == [("Mic", "translated self", True, "en")]
    assert runtime_logging.basic_messages == []
    assert len(runtime_logging.detailed_messages) == 1
    level, message = runtime_logging.detailed_messages[0]
    assert level == logging.INFO
    assert_dashboard_translation_applied_marker(
        message,
        utterance_id=str(utterance_id),
        channel="self",
        source_label="Mic",
        dashboard_target_language="en",
        translation_target_language="en",
        text_len=len("translated self"),
    )


@pytest.mark.asyncio
async def test_event_bridge_passes_dashboard_translation_visual_commit_metadata_to_dashboard() -> (
    None
):
    app = DummyApp()
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())
    utterance_id = uuid4()
    translation = Translation(
        utterance_id=utterance_id,
        text="translated peer",
        channel="peer",
        target_language="ja",
        update_id="upd-dashboard-1",
        origin_wall_clock_ms=1712345678901,
        session_scope="session-42",
        source_text_hash="src-hash-42",
        source_text_len=17,
        logical_turn_key="peer:turn-42",
    )

    await bridge._handle_event(
        UIEvent(
            type=UIEventType.TRANSLATION_DONE,
            payload=translation,
            source="Peer Mic",
        )
    )

    assert app.view_dashboard.translation_calls == [("translated peer", "en")]
    assert app.view_dashboard.translation_metadata_calls == [
        {
            "update_id": "upd-dashboard-1",
            "origin_wall_clock_ms": 1712345678901,
            "utterance_id": utterance_id,
            "channel": "peer",
            "session_scope": "session-42",
            "source_text_hash": "src-hash-42",
            "source_text_len": 17,
            "logical_turn_key": "peer:turn-42",
        }
    ]


@pytest.mark.asyncio
async def test_event_bridge_logs_peer_dashboard_translation_applied_detail_only() -> None:
    app = DummyApp()
    runtime_logging = RuntimeLoggingCapture()
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )
    utterance_id = uuid4()
    translation = Translation(
        utterance_id=utterance_id,
        text="translated peer",
        channel="peer",
        target_language="ja",
    )

    await bridge._handle_event(
        UIEvent(
            type=UIEventType.TRANSLATION_DONE,
            payload=translation,
            source="Peer Mic",
        )
    )

    assert app.view_dashboard.translation_calls == [("translated peer", "en")]
    assert app.history == [("Peer Mic", "translated peer", True, "en")]
    assert runtime_logging.basic_messages == []
    assert len(runtime_logging.detailed_messages) == 1
    level, message = runtime_logging.detailed_messages[0]
    assert level == logging.INFO
    assert_dashboard_translation_applied_marker(
        message,
        utterance_id=str(utterance_id),
        channel="peer",
        source_label="Peer Mic",
        dashboard_target_language="en",
        translation_target_language="ja",
        text_len=len("translated peer"),
    )


@pytest.mark.asyncio
async def test_event_bridge_does_not_log_dashboard_translation_applied_for_invalid_payload() -> (
    None
):
    app = DummyApp()
    runtime_logging = RuntimeLoggingCapture()
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )

    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSLATION_DONE, payload="not-translation")
    )

    assert app.view_dashboard.translation_calls == []
    assert app.history == []
    assert runtime_logging.detailed_calls == []
    assert runtime_logging.basic_messages == []


@pytest.mark.asyncio
async def test_event_bridge_does_not_log_dashboard_translation_applied_without_dashboard() -> None:
    app = DummyApp()
    app.view_dashboard = None
    runtime_logging = RuntimeLoggingCapture()
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )
    translation = Translation(utterance_id=uuid4(), text="translated", channel="self")

    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSLATION_DONE, payload=translation, source="Mic")
    )

    assert app.history == [("Mic", "translated", True, "en")]
    assert runtime_logging.detailed_calls == []
    assert runtime_logging.basic_messages == []


@pytest.mark.asyncio
async def test_event_bridge_best_effort_translation_apply_logging_does_not_block_history() -> None:
    app = DummyApp()
    runtime_logging = RuntimeLoggingCapture(detailed_error=RuntimeError("detail emit failed"))
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )
    translation = Translation(utterance_id=uuid4(), text="translated", channel="self")

    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSLATION_DONE, payload=translation, source="Mic")
    )

    assert app.view_dashboard.translation_calls == [("translated", "en")]
    assert app.history == [("Mic", "translated", True, "en")]
    assert len(runtime_logging.detailed_calls) == 1
    assert runtime_logging.detailed_messages == []
    assert runtime_logging.basic_messages == []


@pytest.mark.asyncio
async def test_event_bridge_dashboard_translation_applied_detail_disabled_keeps_dashboard_and_history() -> (
    None
):
    app = DummyApp()
    runtime_logging = RuntimeLoggingCapture(detailed_enabled=False)
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )
    translation = Translation(utterance_id=uuid4(), text="translated", channel="peer")

    await bridge._handle_event(
        UIEvent(type=UIEventType.TRANSLATION_DONE, payload=translation, source="Peer Mic")
    )

    assert app.view_dashboard.translation_calls == [("translated", "en")]
    assert app.history == [("Peer Mic", "translated", True, "en")]
    assert len(runtime_logging.detailed_calls) == 1
    assert runtime_logging.detailed_messages == []
    assert runtime_logging.basic_messages == []


@pytest.mark.asyncio
async def test_event_bridge_does_not_log_dashboard_translation_applied_when_setter_fails() -> None:
    app = DummyApp()
    app.view_dashboard = FailingTranslationDashboard()
    runtime_logging = RuntimeLoggingCapture()
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )
    translation = Translation(utterance_id=uuid4(), text="translated", channel="self")

    with pytest.raises(RuntimeError, match="dashboard setter failed"):
        await bridge._handle_event(
            UIEvent(type=UIEventType.TRANSLATION_DONE, payload=translation, source="Mic")
        )

    assert app.history == []
    assert runtime_logging.detailed_calls == []
    assert runtime_logging.basic_messages == []


@pytest.mark.asyncio
async def test_event_bridge_handles_error_and_soniox_shutdown_suppression(tmp_path) -> None:
    app = DummyApp()
    root_logger = logging.getLogger(f"test.event_bridge.root.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    session_logger = logging.getLogger(f"test.event_bridge.session.{uuid4()}")
    session_logger.handlers.clear()
    session_logger.propagate = False
    log_file = tmp_path / "event-bridge.log"
    stream_handler = logging.StreamHandler(io.StringIO())
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    runtime_logging = SessionRuntimeLoggingService(
        root_logger=root_logger,
        session_logger=session_logger,
        sinks=SimpleNamespace(
            stream_handler=stream_handler,
            file_handler=file_handler,
            log_file=log_file,
        ),
        ui_handler_factory=FletLogHandler,
    )
    runtime_logging.attach_realtime_sink(app.view_logs)
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )

    try:
        app.controller.hub.stt.state = STTSessionState.DRAINING
        await bridge._handle_event(
            UIEvent(type=UIEventType.ERROR, payload="Soniox 400 bad request")
        )

        app.controller.hub.stt.state = STTSessionState.STREAMING
        await bridge._handle_event(UIEvent(type=UIEventType.ERROR, payload="General failure"))
        await bridge._handle_event(UIEvent(type=UIEventType.ERROR, payload=None))

        assert len(app.view_logs.lines) == 3
        assert all("[ERROR]" in line for line in app.view_logs.lines)
        assert app.view_dashboard.display_calls[-2:] == [
            ("General failure", None, True),
            (t("error.unknown"), None, True),
        ]
    finally:
        runtime_logging.close()
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()


@pytest.mark.asyncio
async def test_event_bridge_skips_duplicate_runtime_log_for_already_logged_errors(tmp_path) -> None:
    app = DummyApp()
    root_logger = logging.getLogger(f"test.event_bridge.runtime.root.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False
    session_logger = logging.getLogger(f"test.event_bridge.runtime.session.{uuid4()}")
    session_logger.handlers.clear()
    session_logger.propagate = False
    log_file = tmp_path / "event-bridge-duplicate.log"
    stream_handler = logging.StreamHandler(io.StringIO())
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    runtime_logging = SessionRuntimeLoggingService(
        root_logger=root_logger,
        session_logger=session_logger,
        sinks=SimpleNamespace(
            stream_handler=stream_handler,
            file_handler=file_handler,
            log_file=log_file,
        ),
        ui_handler_factory=FletLogHandler,
    )
    runtime_logging.attach_realtime_sink(app.view_logs)
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=runtime_logging,
    )

    try:
        runtime_logging.emit_basic("already logged failure", level=logging.ERROR)

        await bridge._handle_event(
            UIEvent(
                type=UIEventType.ERROR,
                payload="already logged failure",
                runtime_log_handled=True,
            )
        )

        assert len(app.view_logs.lines) == 1
        assert "already logged failure" in app.view_logs.lines[0]
        assert app.view_dashboard.display_calls[-1] == ("already logged failure", None, True)
    finally:
        runtime_logging.close()
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()


@pytest.mark.asyncio
async def test_event_bridge_ignores_unknown_event_and_keeps_queue_alive() -> None:
    app = DummyApp()
    queue: asyncio.Queue = asyncio.Queue()
    bridge = UIEventBridge(app=app, event_queue=queue)

    task = asyncio.create_task(bridge.run())
    await queue.put(SimpleNamespace(type="UNKNOWN", payload="x", source=None))
    await queue.put(UIEvent(type=UIEventType.ERROR, payload="after unknown"))
    await queue.join()

    assert app.view_dashboard.display_calls[-1] == ("after unknown", None, True)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_event_bridge_error_without_runtime_logging_uses_standard_logger_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = DummyApp()
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())
    seen: list[str] = []
    monkeypatch.setattr(event_bridge_module.logger, "error", lambda message: seen.append(message))

    await bridge._handle_event(UIEvent(type=UIEventType.ERROR, payload="plain failure"))

    assert seen == ["plain failure"]
    assert app.view_logs.lines == []
    assert app.view_dashboard.display_calls[-1] == ("plain failure", None, True)


@pytest.mark.asyncio
async def test_event_bridge_error_with_broken_runtime_logging_uses_standard_logger_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = DummyApp()
    seen: list[str] = []

    class BrokenRuntimeLogging:
        def emit_basic(self, _message: str, *, level: int = logging.INFO) -> None:
            _ = level
            raise RuntimeError("emit failed")

    monkeypatch.setattr(event_bridge_module.logger, "error", lambda message: seen.append(message))
    bridge = UIEventBridge(
        app=app,
        event_queue=asyncio.Queue(),
        runtime_logging=BrokenRuntimeLogging(),
    )

    await bridge._handle_event(UIEvent(type=UIEventType.ERROR, payload="broken runtime"))

    assert seen == ["broken runtime"]
    assert app.view_logs.lines == []
    assert app.view_dashboard.display_calls[-1] == ("broken runtime", None, True)


@pytest.mark.asyncio
async def test_event_bridge_routes_managed_auth_error_to_snackbar_without_dashboard_clobber() -> (
    None
):
    app = DummyApp()
    app.controller.managed_auth_pending = True
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())
    payload = ManagedOpenRouterUserFacingError(
        message_key="managed_release.retry_after_ms",
        message_kwargs={"retry_after_ms": 9000},
        diagnostics=ManagedOpenRouterReleaseDiagnostics(
            operation="issue",
            code="trial_unavailable",
            error_class="retryable",
            subcode="broker_backoff",
            retry_after_ms=9000,
            message="broker is temporarily unavailable",
        ),
    )

    await bridge._handle_event(
        UIEvent(type=UIEventType.ERROR, payload=payload, runtime_log_handled=True)
    )

    assert app.snackbar_calls == [
        (str(payload), ft.Colors.ORANGE_700),
    ]
    assert app.clear_managed_auth_pending_calls == 1
    assert app.view_dashboard.display_calls == []


@pytest.mark.asyncio
async def test_event_bridge_keeps_general_error_display_when_managed_auth_is_pending() -> None:
    app = DummyApp()
    app.controller.managed_auth_pending = True
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())

    await bridge._handle_event(
        UIEvent(type=UIEventType.ERROR, payload="managed auth boom", runtime_log_handled=True)
    )

    assert app.snackbar_calls == []
    assert app.clear_managed_auth_pending_calls == 0
    assert app.view_dashboard.display_calls == [("managed auth boom", None, True)]


def test_event_bridge_reports_overlay_state_to_app() -> None:
    app = DummyApp()
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())

    bridge.report_overlay_state("starting")
    bridge.report_overlay_state("failed", failure_reason="runtime_crashed")

    assert app.overlay_state == "failed"
    assert app.overlay_failure_reason == "runtime_crashed"
