from __future__ import annotations

import asyncio
import io
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("flet")

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
    ) -> None:
        self.translation_calls.append((text, language_code))

    def set_local_stt_notice(self, status: str | None) -> None:
        self.notice_calls.append(status)


class DummyLogs:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append_log(self, line: str) -> None:
        self.lines.append(line)


class DummyApp:
    def __init__(self) -> None:
        self.view_dashboard = DummyDashboard()
        self.view_logs = DummyLogs()
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
        )

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


def test_event_bridge_reports_overlay_state_to_app() -> None:
    app = DummyApp()
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())

    bridge.report_overlay_state("starting")
    bridge.report_overlay_state("failed", failure_reason="runtime_crashed")

    assert app.overlay_state == "failed"
    assert app.overlay_failure_reason == "runtime_crashed"
