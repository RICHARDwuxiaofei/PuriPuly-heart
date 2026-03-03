from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("flet")

from puripuly_heart.domain.events import STTSessionState, UIEvent, UIEventType
from puripuly_heart.domain.models import OSCMessage, Transcript, Translation
from puripuly_heart.ui.event_bridge import UIEventBridge
from puripuly_heart.ui.i18n import t


class DummyDashboard:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.display_calls: list[tuple[str, str | None, bool]] = []
        self.translation_calls: list[tuple[str, str | None]] = []

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
async def test_event_bridge_handles_error_and_soniox_shutdown_suppression() -> None:
    app = DummyApp()
    bridge = UIEventBridge(app=app, event_queue=asyncio.Queue())

    app.controller.hub.stt.state = STTSessionState.DRAINING
    await bridge._handle_event(UIEvent(type=UIEventType.ERROR, payload="Soniox 400 bad request"))

    app.controller.hub.stt.state = STTSessionState.STREAMING
    await bridge._handle_event(UIEvent(type=UIEventType.ERROR, payload="General failure"))
    await bridge._handle_event(UIEvent(type=UIEventType.ERROR, payload=None))

    assert len(app.view_logs.lines) == 3
    assert app.view_dashboard.display_calls[-2:] == [
        ("General failure", None, True),
        (t("error.unknown"), None, True),
    ]


@pytest.mark.asyncio
async def test_event_bridge_ignores_unknown_event_and_keeps_queue_alive() -> None:
    app = DummyApp()
    queue: asyncio.Queue = asyncio.Queue()
    bridge = UIEventBridge(app=app, event_queue=queue)

    task = asyncio.create_task(bridge.run())
    await queue.put(SimpleNamespace(type="UNKNOWN", payload="x", source=None))
    await queue.put(UIEvent(type=UIEventType.ERROR, payload="after unknown"))
    await queue.join()

    assert any("after unknown" in line for line in app.view_logs.lines)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
