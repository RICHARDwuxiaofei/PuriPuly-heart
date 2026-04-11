from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.overlay.diagnostics import OverlayDiagnosticsRecorder
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.overlay.protocol import OverlayPresentationCalibration
from puripuly_heart.core.overlay.sink import (
    OverlayEventAdapter,
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
    TranslationFinal,
)
from puripuly_heart.core.runtime_logging import SessionLoggingMode
from puripuly_heart.domain.models import Transcript
from puripuly_heart.ui.overlay_calibration import OverlayCalibration
from tests.core.test_hub_branch_coverage import (
    _make_runtime_logging_capture,
    _runtime_log_messages,
)


@dataclass(slots=True)
class RecordingPresentationBridge:
    snapshots: list[object] = field(default_factory=list)
    shutdown_calls: int = 0

    async def replace_snapshot(self, snapshot: object) -> None:
        self.snapshots.append(snapshot)

    async def broadcast_shutdown(self) -> None:
        self.shutdown_calls += 1


@pytest.mark.asyncio
async def test_presenter_shows_first_self_transcript_without_waiting_for_next_utterance() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="hello now",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            transcript,
            source_language="ko",
            target_language="en",
        )
    )

    assert bridge.snapshots[-1].blocks[-1].channel == "self"
    assert bridge.snapshots[-1].blocks[-1].block_variant == "finalized"
    assert bridge.snapshots[-1].blocks[-1].primary_text == "hello now"
    assert bridge.snapshots[-1].blocks[-1].secondary_text == ""
    assert bridge.snapshots[-1].blocks[-1].secondary_enabled is True


@pytest.mark.asyncio
async def test_presenter_does_not_reorder_existing_turn_when_translation_updates() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    first_peer = Transcript(
        utterance_id=uuid4(), channel="peer", text="peer one", is_final=True, created_at=11.0
    )
    second_self = Transcript(
        utterance_id=uuid4(), channel="self", text="self two", is_final=True, created_at=12.0
    )

    await presenter.emit(
        adapter.transcript_final(first_peer, source_language="en", target_language="ko")
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=first_peer.utterance_id,
            channel="peer",
            text="피어 하나",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=12.5,
        )
    )
    await presenter.emit(
        adapter.transcript_final(second_self, source_language="ko", target_language="en")
    )
    first_order = presenter.snapshot().blocks[0].appearance_seq

    await presenter.emit(
        adapter.translation_final(
            utterance_id=first_peer.utterance_id,
            channel="peer",
            text="피어 하나 수정",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=13.0,
        )
    )

    assert presenter.snapshot().blocks[0].appearance_seq == first_order
    assert [block.occupant_key for block in presenter.snapshot().blocks] == [
        f"peer:{first_peer.utterance_id}",
        f"self:{second_self.utterance_id}",
    ]


@pytest.mark.asyncio
async def test_presenter_hides_peer_blocks_until_translation_exists() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    peer_transcript = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer original",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            peer_transcript,
            source_language="en",
            target_language="ko",
        )
    )

    assert presenter.snapshot().blocks == []

    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_transcript.utterance_id,
            channel="peer",
            text="상대 번역",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=12.0,
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [
        f"peer:{peer_transcript.utterance_id}"
    ]
    assert presenter.snapshot().blocks[0].primary_text == "상대 번역"
    assert presenter.snapshot().blocks[0].secondary_text == "peer original"
    assert presenter.snapshot().blocks[0].secondary_enabled is True


@pytest.mark.asyncio
async def test_presenter_keeps_closed_hidden_peer_entry_publishable_until_translation_arrives() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    adapter = OverlayEventAdapter(clock=clock)
    peer_transcript = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer original",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            peer_transcript,
            source_language="en",
            target_language="ko",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=peer_transcript.utterance_id,
            channel="peer",
            created_at=11.2,
        )
    )

    assert presenter.snapshot().blocks == []

    clock.advance(2.0)
    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_transcript.utterance_id,
            channel="peer",
            text="상대 번역",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=13.2,
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [
        f"peer:{peer_transcript.utterance_id}"
    ]
    assert presenter.snapshot().blocks[0].primary_text == "상대 번역"

    clock.advance(4.0)
    await presenter._publish_if_changed()
    assert [block.id for block in presenter.snapshot().blocks] == [
        f"peer:{peer_transcript.utterance_id}"
    ]

    clock.advance(1.1)
    await presenter._publish_if_changed()
    assert [block.id for block in presenter.snapshot().blocks] == [
        f"peer:{peer_transcript.utterance_id}"
    ]

    clock.advance(5.0)
    await presenter._publish_if_changed()
    assert presenter.snapshot().blocks == []


@pytest.mark.asyncio
async def test_presenter_reschedules_hidden_peer_expiration_when_translation_becomes_visible() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    sleep_calls: list[float] = []
    cancelled_delays: list[float] = []
    sleep_events: list[asyncio.Event] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        release = asyncio.Event()
        sleep_events.append(release)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled_delays.append(delay)
            raise
        clock.advance(delay)
        await asyncio.sleep(0)

    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep=fake_sleep,
    )
    adapter = OverlayEventAdapter(clock=clock)
    peer_transcript = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer original",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            peer_transcript,
            source_language="en",
            target_language="ko",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=peer_transcript.utterance_id,
            channel="peer",
            created_at=11.2,
        )
    )

    await asyncio.sleep(0)

    assert sleep_calls == [5.0]
    assert presenter.snapshot().blocks == []

    clock.advance(2.0)
    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_transcript.utterance_id,
            channel="peer",
            text="상대 번역",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=13.2,
        )
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert cancelled_delays == [5.0]
    assert sleep_calls == [5.0, 8.0]
    assert [block.id for block in presenter.snapshot().blocks] == [
        f"peer:{peer_transcript.utterance_id}"
    ]

    sleep_events[0].set()
    await asyncio.sleep(0)

    assert [block.id for block in presenter.snapshot().blocks] == [
        f"peer:{peer_transcript.utterance_id}"
    ]

    sleep_events[1].set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert presenter.snapshot().blocks == []


@pytest.mark.asyncio
async def test_presenter_reschedules_closed_self_expiration_with_translation_min_visibility() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    sleep_calls: list[float] = []
    cancelled_delays: list[float] = []
    sleep_events: list[asyncio.Event] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        release = asyncio.Event()
        sleep_events.append(release)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled_delays.append(delay)
            raise
        clock.advance(delay)
        await asyncio.sleep(0)

    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep=fake_sleep,
    )
    adapter = OverlayEventAdapter(clock=clock)
    transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="self original",
        is_final=True,
        created_at=10.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            transcript,
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=transcript.utterance_id,
            channel="self",
            created_at=10.1,
        )
    )

    await asyncio.sleep(0)

    assert sleep_calls == [8.0]

    clock.advance(7.0)
    await presenter.emit(
        adapter.translation_final(
            utterance_id=transcript.utterance_id,
            channel="self",
            text="self translation",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=17.0,
        )
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert cancelled_delays == [8.0]
    assert sleep_calls == [8.0, 4.0]
    assert presenter.snapshot().blocks[0].secondary_text == "self translation"

    sleep_events[0].set()
    await asyncio.sleep(0)

    assert presenter.snapshot().blocks[0].secondary_text == "self translation"

    sleep_events[1].set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert presenter.snapshot().blocks == []


@pytest.mark.asyncio
async def test_presenter_restarts_self_translation_min_visibility_when_translation_changes() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    sleep_calls: list[float] = []
    cancelled_delays: list[float] = []
    sleep_events: list[asyncio.Event] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        release = asyncio.Event()
        sleep_events.append(release)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled_delays.append(delay)
            raise
        clock.advance(delay)
        await asyncio.sleep(0)

    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep=fake_sleep,
    )
    adapter = OverlayEventAdapter(clock=clock)
    transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="self original",
        is_final=True,
        created_at=10.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            transcript,
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=transcript.utterance_id,
            channel="self",
            created_at=10.1,
        )
    )

    await asyncio.sleep(0)
    assert sleep_calls == [8.0]

    clock.advance(7.5)
    await presenter.emit(
        adapter.translation_final(
            utterance_id=transcript.utterance_id,
            channel="self",
            text="self translation one",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=17.5,
        )
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert cancelled_delays == [8.0]
    assert sleep_calls == [8.0, 4.0]
    assert presenter.snapshot().blocks[0].secondary_text == "self translation one"

    clock.advance(2.0)
    await presenter.emit(
        adapter.translation_final(
            utterance_id=transcript.utterance_id,
            channel="self",
            text="self translation two",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=19.5,
        )
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert cancelled_delays == [8.0, 4.0]
    assert sleep_calls == [8.0, 4.0, 4.0]
    assert presenter.snapshot().blocks[0].secondary_text == "self translation two"

    sleep_events[0].set()
    await asyncio.sleep(0)
    assert presenter.snapshot().blocks[0].secondary_text == "self translation two"

    sleep_events[1].set()
    await asyncio.sleep(0)
    assert presenter.snapshot().blocks[0].secondary_text == "self translation two"

    sleep_events[2].set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert presenter.snapshot().blocks == []


@pytest.mark.asyncio
async def test_presenter_records_expired_entry_diagnostic_with_deadlines(
    tmp_path,
) -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    diagnostics = OverlayDiagnosticsRecorder(
        overlay_instance_id="overlay-test",
        diagnostics_dir=tmp_path,
    )
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        diagnostics=diagnostics,
    )
    adapter = OverlayEventAdapter(clock=clock)
    utterance_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=utterance_id,
                channel="self",
                text="self original",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=utterance_id,
            channel="self",
            created_at=10.1,
        )
    )
    clock.advance(7.0)
    await presenter.emit(
        adapter.translation_final(
            utterance_id=utterance_id,
            channel="self",
            text="self translation",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=17.0,
        )
    )

    clock.advance(4.1)
    await presenter._publish_if_changed()

    assert list(diagnostics.presenter_events) == []
    assert list(diagnostics.presenter_removal_events) == []


@pytest.mark.asyncio
async def test_presenter_records_untranslated_self_visibility_duration(
    tmp_path,
) -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    diagnostics = OverlayDiagnosticsRecorder(
        overlay_instance_id="overlay-test",
        diagnostics_dir=tmp_path,
    )
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        diagnostics=diagnostics,
    )
    adapter = OverlayEventAdapter(clock=clock)
    utterance_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=utterance_id,
                channel="self",
                text="self original",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=utterance_id,
            channel="self",
            created_at=10.1,
        )
    )

    clock.advance(8.1)
    await presenter._publish_if_changed()

    assert list(diagnostics.presenter_events) == []
    assert list(diagnostics.presenter_removal_events) == []


@pytest.mark.asyncio
async def test_presenter_records_window_selection_and_retained_hidden_self_diagnostics(
    tmp_path,
) -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    diagnostics = OverlayDiagnosticsRecorder(
        overlay_instance_id="overlay-test",
        diagnostics_dir=tmp_path,
    )
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        diagnostics=diagnostics,
    )
    adapter = OverlayEventAdapter(clock=clock)
    utterance_ids = [uuid4(), uuid4(), uuid4()]

    for index, utterance_id in enumerate(utterance_ids, start=1):
        await presenter.emit(
            adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    channel="self",
                    text=f"original {index}",
                    is_final=True,
                    created_at=float(index),
                ),
                source_language="ko",
                target_language="en",
            )
        )
        await presenter.emit(
            adapter.translation_final(
                utterance_id=utterance_id,
                channel="self",
                text=f"translation {index}",
                source_language="ko",
                target_language="en",
                applied_context_mode=None,
                created_at=float(index) + 0.1,
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="self",
                is_final=True,
                created_at=float(index) + 0.2,
            )
        )

    assert list(diagnostics.presenter_events) == []
    assert list(diagnostics.presenter_removal_events) == []


@pytest.mark.asyncio
async def test_presenter_records_peer_displacement_as_removal_diagnostic(tmp_path) -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    diagnostics = OverlayDiagnosticsRecorder(
        overlay_instance_id="overlay-test",
        diagnostics_dir=tmp_path,
    )
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        diagnostics=diagnostics,
    )
    adapter = OverlayEventAdapter(clock=clock)
    utterance_ids = [uuid4(), uuid4(), uuid4()]

    for index, utterance_id in enumerate(utterance_ids, start=1):
        await presenter.emit(
            adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    channel="peer",
                    text=f"peer original {index}",
                    is_final=True,
                    created_at=float(index),
                ),
                source_language="en",
                target_language="ko",
            )
        )
        await presenter.emit(
            adapter.translation_final(
                utterance_id=utterance_id,
                channel="peer",
                text=f"peer translation {index}",
                source_language="en",
                target_language="ko",
                applied_context_mode=None,
                created_at=float(index) + 0.1,
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="peer",
                is_final=True,
                created_at=float(index) + 0.2,
            )
        )

    assert list(diagnostics.presenter_events) == []
    assert list(diagnostics.presenter_removal_events) == []


@pytest.mark.asyncio
async def test_presenter_drops_closed_hidden_peer_entry_once_translation_ttl_expires() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    adapter = OverlayEventAdapter(clock=clock)
    peer_transcript = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer original",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            peer_transcript,
            source_language="en",
            target_language="ko",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=peer_transcript.utterance_id,
            channel="peer",
            created_at=11.2,
        )
    )

    clock.advance(6.0)
    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_transcript.utterance_id,
            channel="peer",
            text="너무 늦은 번역",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=17.2,
        )
    )

    assert presenter.snapshot().blocks == []


@pytest.mark.asyncio
async def test_presenter_includes_calibration_inside_snapshot_updates() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )

    await presenter.update_calibration(
        OverlayCalibration(
            anchor="head_locked",
            offset_x=0.2,
            offset_y=-0.1,
            distance=1.5,
            text_scale=1.1,
            background_alpha=0.33,
        )
    )

    latest = bridge.snapshots[-1]

    assert latest.calibration == OverlayPresentationCalibration(
        anchor="head_locked",
        offset_x=0.2,
        offset_y=-0.1,
        distance=1.5,
        text_scale=1.1,
        background_alpha=0.33,
    )


@pytest.mark.asyncio
async def test_presenter_shutdown_is_control_plane_only() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )

    await presenter.broadcast_shutdown()

    assert bridge.shutdown_calls == 1
    assert bridge.snapshots == []


@pytest.mark.asyncio
async def test_presenter_ignores_stale_self_active_clear() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )

    await presenter.emit(
        SelfActiveUpdate(
            event_id="active-new",
            seq=2,
            utterance_id=None,
            channel="self",
            created_at=10.0,
            text="live self",
            occupant_key="self:active-new",
        )
    )
    revision_before_clear = presenter.snapshot().revision

    await presenter.emit(
        SelfActiveClear(
            event_id="clear-old",
            seq=1,
            utterance_id=None,
            channel="self",
            created_at=9.0,
        )
    )

    assert presenter.snapshot().revision == revision_before_clear
    assert presenter.snapshot().blocks[-1].id == "self:active"
    assert presenter.snapshot().blocks[-1].block_variant == "active_self"
    assert presenter.snapshot().blocks[-1].primary_text == "live self"
    assert presenter.snapshot().blocks[-1].secondary_text == ""
    assert presenter.snapshot().blocks[-1].secondary_enabled is True


@pytest.mark.asyncio
async def test_presenter_ignores_stale_history_updates() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )
    utterance_id = uuid4()

    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-new",
            seq=10,
            utterance_id=utterance_id,
            channel="self",
            created_at=10.0,
            text="latest original",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )
    await presenter.emit(
        TranslationFinal(
            event_id="translation-new",
            seq=12,
            utterance_id=utterance_id,
            channel="self",
            created_at=12.0,
            text="latest translation",
            source_language="ko",
            target_language="en",
            is_final=True,
            applied_context_mode=None,
        )
    )
    revision_before_stale = presenter.snapshot().revision

    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-old",
            seq=9,
            utterance_id=utterance_id,
            channel="self",
            created_at=9.0,
            text="stale original",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )
    await presenter.emit(
        TranslationFinal(
            event_id="translation-old",
            seq=11,
            utterance_id=utterance_id,
            channel="self",
            created_at=11.0,
            text="stale translation",
            source_language="ko",
            target_language="en",
            is_final=True,
            applied_context_mode=None,
        )
    )

    assert presenter.snapshot().revision == revision_before_stale
    assert presenter.snapshot().blocks[-1].primary_text == "latest original"
    assert presenter.snapshot().blocks[-1].secondary_text == "latest translation"


@pytest.mark.asyncio
async def test_presenter_prunes_closed_entries_once_newer_turns_displace_them() -> None:
    bridge = RecordingPresentationBridge()
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )

    utterance_ids = [uuid4(), uuid4(), uuid4()]

    for offset, utterance_id in enumerate(utterance_ids, start=1):
        await presenter.emit(
            SelfTranscriptFinal(
                event_id=f"self-{offset}",
                seq=offset * 10,
                utterance_id=utterance_id,
                channel="self",
                created_at=float(offset),
                text=f"original {offset}",
                source_language="ko",
                target_language="en",
                is_final=True,
            )
        )
        await presenter.emit(
            TranslationFinal(
                event_id=f"translation-{offset}",
                seq=offset * 10 + 1,
                utterance_id=utterance_id,
                channel="self",
                created_at=float(offset) + 0.1,
                text=f"translation {offset}",
                source_language="ko",
                target_language="en",
                is_final=True,
                applied_context_mode=None,
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="self",
                is_final=True,
                created_at=float(offset) + 0.2,
            )
        )

    latest = presenter.snapshot()

    assert [block.id for block in latest.blocks] == [
        f"self:{utterance_ids[1]}",
        f"self:{utterance_ids[2]}",
    ]
    assert latest.blocks[0].primary_text == "original 2"
    assert latest.blocks[0].secondary_text == "translation 2"
    assert latest.blocks[1].primary_text == "original 3"
    assert latest.blocks[1].secondary_text == "translation 3"


@pytest.mark.asyncio
async def test_presenter_keeps_recently_translated_self_row_visible_over_newer_untranslated_row() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        visible_window_target_blocks=1,
    )
    adapter = OverlayEventAdapter(clock=clock)
    translated_id = uuid4()
    newer_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=translated_id,
                channel="self",
                text="translated original",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=translated_id,
            channel="self",
            text="translated secondary",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=10.1,
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=translated_id,
            channel="self",
            is_final=True,
            created_at=10.2,
        )
    )

    clock.advance(1.0)
    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=newer_id,
                channel="self",
                text="newer untranslated",
                is_final=True,
                created_at=11.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{translated_id}"]
    assert presenter.snapshot().blocks[0].primary_text == "translated original"
    assert presenter.snapshot().blocks[0].secondary_text == "translated secondary"


@pytest.mark.asyncio
async def test_presenter_does_not_protect_self_row_over_newer_peer_row() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        visible_window_target_blocks=1,
    )
    adapter = OverlayEventAdapter(clock=clock)
    self_id = uuid4()
    peer_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=self_id,
                channel="self",
                text="self original",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=self_id,
            channel="self",
            text="self translation",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=10.1,
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=self_id,
            channel="self",
            is_final=True,
            created_at=10.2,
        )
    )

    clock.advance(1.0)
    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=peer_id,
                channel="peer",
                text="peer original",
                is_final=True,
                created_at=11.0,
            ),
            source_language="en",
            target_language="ko",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_id,
            channel="peer",
            text="peer translation",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=11.1,
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [f"peer:{peer_id}"]
    assert presenter.snapshot().blocks[0].primary_text == "peer translation"
    assert presenter._entries[("self", self_id)].retained_hidden is True


@pytest.mark.asyncio
async def test_presenter_ignores_late_updates_for_pruned_closed_entries() -> None:
    bridge = RecordingPresentationBridge()
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )

    displaced_utterance_id = uuid4()
    newer_ids = [uuid4(), uuid4()]

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=displaced_utterance_id,
                channel="self",
                text="original 1",
                is_final=True,
                created_at=1.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=displaced_utterance_id,
            channel="self",
            text="translation 1",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=1.1,
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=displaced_utterance_id,
            channel="self",
            is_final=True,
            created_at=1.2,
        )
    )

    for index, utterance_id in enumerate(newer_ids, start=2):
        await presenter.emit(
            adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    channel="self",
                    text=f"original {index}",
                    is_final=True,
                    created_at=float(index),
                ),
                source_language="ko",
                target_language="en",
            )
        )
        await presenter.emit(
            adapter.translation_final(
                utterance_id=utterance_id,
                channel="self",
                text=f"translation {index}",
                source_language="ko",
                target_language="en",
                applied_context_mode=None,
                created_at=float(index) + 0.1,
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="self",
                is_final=True,
                created_at=float(index) + 0.2,
            )
        )

    revision_before_late_update = presenter.snapshot().revision
    blocks_before_late_update = presenter.snapshot().blocks

    await presenter.emit(
        adapter.translation_final(
            utterance_id=displaced_utterance_id,
            channel="self",
            text="late duplicate translation",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=9.9,
        )
    )

    assert presenter.snapshot().revision == revision_before_late_update
    assert presenter.snapshot().blocks == blocks_before_late_update


@pytest.mark.asyncio
async def test_presenter_retains_displaced_self_row_for_late_translation_without_resurfacing() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    adapter = OverlayEventAdapter(clock=clock)
    first = uuid4()
    second = uuid4()
    active = uuid4()

    for offset, utterance_id in enumerate((first, second), start=1):
        await presenter.emit(
            adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    channel="self",
                    text=f"original {offset}",
                    is_final=True,
                    created_at=10.0 + offset,
                ),
                source_language="ko",
                target_language="en",
            )
        )
        await presenter.emit(
            adapter.translation_final(
                utterance_id=utterance_id,
                channel="self",
                text=f"translation {offset}",
                source_language="ko",
                target_language="en",
                applied_context_mode=None,
                created_at=10.1 + offset,
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="self",
                is_final=True,
                created_at=10.2 + offset,
            )
        )

    await presenter.emit(
        adapter.self_active_update(
            text="live self",
            occupant_key=f"self:{active}",
            created_at=13.0,
        )
    )

    first_entry = presenter._entries[("self", first)]
    first_translation_visible_since = first_entry.translation_visible_since

    assert first_entry.retained_hidden is True
    assert first_entry.window_evicted_at == pytest.approx(10.0)

    await presenter.emit(
        adapter.translation_final(
            utterance_id=first,
            channel="self",
            text="late translation 1",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=13.1,
        )
    )

    first_entry = presenter._entries[("self", first)]
    assert first_entry.translation_text == "late translation 1"
    assert first_entry.translation_visible_since == first_translation_visible_since

    await presenter.emit(adapter.self_active_clear(created_at=13.2))

    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{second}"]


@pytest.mark.asyncio
async def test_presenter_does_not_resurface_newer_self_row_after_active_window_displacement() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    adapter = OverlayEventAdapter(clock=clock)
    older = uuid4()
    protected = uuid4()
    newer = uuid4()

    for offset, utterance_id in enumerate((older, protected), start=1):
        await presenter.emit(
            adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    channel="self",
                    text=f"original {offset}",
                    is_final=True,
                    created_at=10.0 + offset,
                ),
                source_language="ko",
                target_language="en",
            )
        )
        await presenter.emit(
            adapter.translation_final(
                utterance_id=utterance_id,
                channel="self",
                text=f"translation {offset}",
                source_language="ko",
                target_language="en",
                applied_context_mode=None,
                created_at=10.1 + offset,
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="self",
                is_final=True,
                created_at=10.2 + offset,
            )
        )

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=newer,
                channel="self",
                text="newer original",
                is_final=True,
                created_at=13.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [
        f"self:{protected}",
        f"self:{newer}",
    ]

    await presenter.emit(
        adapter.self_active_update(
            text="live self",
            occupant_key="self:active",
            created_at=13.1,
        )
    )

    assert presenter._entries[("self", newer)].retained_hidden is True
    assert [block.id for block in presenter.snapshot().blocks] == [
        f"self:{protected}",
        "self:active",
    ]

    await presenter.emit(adapter.self_active_clear(created_at=13.2))
    clock.advance(4.1)
    await presenter._publish_if_changed()

    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{protected}"]


@pytest.mark.asyncio
async def test_presenter_expires_preclose_retained_hidden_self_after_late_arrival_window() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        visible_window_target_blocks=1,
    )
    adapter = OverlayEventAdapter(clock=clock)
    first = uuid4()
    second = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=first,
                channel="self",
                text="original 1",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=second,
                channel="self",
                text="original 2",
                is_final=True,
                created_at=11.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )

    assert presenter._entries[("self", first)].retained_hidden is True

    clock.advance(5.1)
    await presenter._publish_if_changed()

    assert ("self", first) not in presenter._entries
    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{second}"]

    revision_before_late_retry = presenter.snapshot().revision
    blocks_before_late_retry = presenter.snapshot().blocks

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=first,
                channel="self",
                text="late original 1",
                is_final=True,
                created_at=16.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )

    assert presenter.snapshot().revision == revision_before_late_retry
    assert presenter.snapshot().blocks == blocks_before_late_retry
    assert ("self", first) not in presenter._entries


@pytest.mark.asyncio
async def test_presenter_caps_hidden_self_retention_after_delayed_close() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        visible_window_target_blocks=1,
    )
    adapter = OverlayEventAdapter(clock=clock)
    first = uuid4()
    second = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=first,
                channel="self",
                text="original 1",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=second,
                channel="self",
                text="original 2",
                is_final=True,
                created_at=11.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )

    assert presenter._entries[("self", first)].retained_hidden is True
    assert presenter._entries[("self", first)].window_evicted_at == pytest.approx(10.0)

    clock.advance(3.0)
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=first,
            channel="self",
            is_final=True,
            created_at=13.0,
        )
    )

    clock.advance(2.1)
    await presenter._publish_if_changed()

    assert ("self", first) not in presenter._entries
    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{second}"]


@pytest.mark.asyncio
async def test_presenter_reset_scene_clears_closed_entry_tombstones() -> None:
    bridge = RecordingPresentationBridge()
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )
    reused_utterance_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=reused_utterance_id,
                channel="self",
                text="first scene",
                is_final=True,
                created_at=1.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=reused_utterance_id,
            channel="self",
            text="first translation",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=1.1,
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=reused_utterance_id,
            channel="self",
            is_final=True,
            created_at=1.2,
        )
    )

    for index in range(2):
        utterance_id = uuid4()
        await presenter.emit(
            adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    channel="self",
                    text=f"scene filler {index}",
                    is_final=True,
                    created_at=2.0 + index,
                ),
                source_language="ko",
                target_language="en",
            )
        )
        await presenter.emit(
            adapter.translation_final(
                utterance_id=utterance_id,
                channel="self",
                text=f"translation filler {index}",
                source_language="ko",
                target_language="en",
                applied_context_mode=None,
                created_at=2.1 + index,
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="self",
                is_final=True,
                created_at=2.2 + index,
            )
        )

    presenter.reset_scene()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=reused_utterance_id,
                channel="self",
                text="second scene",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )

    assert presenter.snapshot().blocks == [presenter.snapshot().blocks[0]]
    assert presenter.snapshot().blocks[0].id == f"self:{reused_utterance_id}"
    assert presenter.snapshot().blocks[0].primary_text == "second scene"


@pytest.mark.asyncio
async def test_presenter_expires_visible_finalized_entry_after_eight_seconds() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        clock.advance(delay)
        await asyncio.sleep(0)

    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep=fake_sleep,
    )
    adapter = OverlayEventAdapter(clock=clock)
    transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="hello now",
        is_final=True,
        created_at=10.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            transcript,
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=transcript.utterance_id,
            channel="self",
            is_final=True,
            created_at=10.1,
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [
        f"self:{transcript.utterance_id}"
    ]
    assert len(bridge.snapshots) == 1

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert sleep_calls == [8.0]
    assert presenter.snapshot().blocks == []
    assert len(bridge.snapshots) == 2
    assert bridge.snapshots[-1].blocks == []


@pytest.mark.asyncio
async def test_presenter_keeps_active_self_visible_when_finalized_entry_expires() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=20.0)

    async def fake_sleep(delay: float) -> None:
        clock.advance(delay)
        await asyncio.sleep(0)

    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep=fake_sleep,
    )
    adapter = OverlayEventAdapter(clock=clock)
    transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="final line",
        is_final=True,
        created_at=20.0,
    )

    await presenter.emit(
        adapter.transcript_final(
            transcript,
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=transcript.utterance_id,
            channel="self",
            is_final=True,
            created_at=20.1,
        )
    )
    await presenter.emit(
        SelfActiveUpdate(
            event_id="self-active",
            seq=999,
            utterance_id=None,
            channel="self",
            created_at=20.2,
            text="live self",
            occupant_key="self:active-live",
        )
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [block.id for block in presenter.snapshot().blocks] == ["self:active"]
    assert presenter.snapshot().blocks[0].primary_text == "live self"


@pytest.mark.asyncio
async def test_presenter_caps_visible_set_to_two_occupants_when_active_self_exists() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=30.0)
    adapter = OverlayEventAdapter(clock=clock)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    utterance_ids = [uuid4(), uuid4()]

    for index, utterance_id in enumerate(utterance_ids, start=1):
        await presenter.emit(
            adapter.transcript_final(
                Transcript(
                    utterance_id=utterance_id,
                    channel="self",
                    text=f"final {index}",
                    is_final=True,
                    created_at=30.0 + index,
                ),
                source_language="ko",
                target_language="en",
            )
        )
        await presenter.emit(
            adapter.utterance_closed(
                utterance_id=utterance_id,
                channel="self",
                is_final=True,
                created_at=30.1 + index,
            )
        )

    await presenter.emit(
        SelfActiveUpdate(
            event_id="active-now",
            seq=999,
            utterance_id=None,
            channel="self",
            created_at=35.0,
            text="live self",
            occupant_key="self:merge-live",
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [
        f"self:{utterance_ids[1]}",
        "self:active",
    ]


@pytest.mark.asyncio
async def test_presenter_keeps_active_self_and_matching_final_on_same_occupant_key() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    utterance_id = uuid4()

    await presenter.emit(
        adapter.self_active_update(
            text="hello live",
            occupant_key=f"self:{utterance_id}",
            created_at=10.0,
        )
    )
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="final-1",
            seq=2,
            utterance_id=utterance_id,
            channel="self",
            text="hello live",
            source_language="ko",
            target_language="en",
            created_at=11.0,
        )
    )

    blocks = presenter.snapshot().blocks
    assert len(blocks) == 1
    assert blocks[0].occupant_key == f"self:{utterance_id}"
    assert blocks[0].block_variant == "finalized"
    assert blocks[0].appearance_seq == 1


@pytest.mark.asyncio
async def test_presenter_renders_active_self_secondary_text() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))

    await presenter.emit(
        adapter.self_active_update(
            text="hello live",
            secondary_text="translated live",
            occupant_key="self:merge-live",
            created_at=10.0,
        )
    )

    blocks = presenter.snapshot().blocks
    assert len(blocks) == 1
    assert blocks[0].id == "self:active"
    assert blocks[0].block_variant == "active_self"
    assert blocks[0].primary_text == "hello live"
    assert blocks[0].secondary_text == "translated live"
    assert blocks[0].secondary_enabled is True


@pytest.mark.asyncio
async def test_presenter_updates_active_self_when_secondary_changes_only() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))

    await presenter.emit(
        adapter.self_active_update(
            text="hello live",
            occupant_key="self:merge-live",
            created_at=10.0,
        )
    )
    revision_before_secondary = presenter.snapshot().revision

    await presenter.emit(
        adapter.self_active_update(
            text="hello live",
            secondary_text="translated live",
            occupant_key="self:merge-live",
            created_at=11.0,
        )
    )

    assert presenter.snapshot().revision == revision_before_secondary + 1
    assert presenter.snapshot().blocks[-1].secondary_text == "translated live"


@pytest.mark.asyncio
async def test_presenter_inherits_active_self_translation_visibility_when_promoted() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    utterance_id = uuid4()

    await presenter.emit(
        SelfActiveUpdate(
            event_id="self-active",
            seq=1,
            utterance_id=None,
            channel="self",
            created_at=10.0,
            text="live self",
            secondary_text="translated live",
            occupant_key=f"self:{utterance_id}",
        )
    )
    clock.advance(1.0)
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=2,
            utterance_id=utterance_id,
            channel="self",
            created_at=11.0,
            text="live self",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )

    entry = presenter._entries[("self", utterance_id)]
    assert entry.translation_visible_since == 10.0


@pytest.mark.asyncio
async def test_presenter_promotes_active_self_secondary_into_finalized_row_without_blank_snapshot() -> (
    None
):
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )
    utterance_id = uuid4()

    await presenter.emit(
        SelfActiveUpdate(
            event_id="self-active",
            seq=1,
            utterance_id=None,
            channel="self",
            created_at=10.0,
            text="live self",
            secondary_text="translated live",
            occupant_key=f"self:{utterance_id}",
        )
    )
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=2,
            utterance_id=utterance_id,
            channel="self",
            created_at=11.0,
            text="live self",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )
    await presenter.emit(
        TranslationFinal(
            event_id="self-translation-final",
            seq=3,
            utterance_id=utterance_id,
            channel="self",
            created_at=12.0,
            text="translated live",
            source_language="ko",
            target_language="en",
            is_final=True,
            applied_context_mode=None,
        )
    )

    finalized_snapshots = [
        snapshot
        for snapshot in bridge.snapshots
        if snapshot.blocks
        and snapshot.blocks[-1].occupant_key == f"self:{utterance_id}"
        and snapshot.blocks[-1].block_variant == "finalized"
    ]

    assert finalized_snapshots != []
    assert all(snapshot.blocks[-1].primary_text == "live self" for snapshot in finalized_snapshots)
    assert all(
        snapshot.blocks[-1].secondary_text == "translated live" for snapshot in finalized_snapshots
    )
    assert all(snapshot.blocks[-1].secondary_enabled is True for snapshot in finalized_snapshots)


@pytest.mark.asyncio
async def test_presenter_clears_active_self_secondary_text_on_empty_update() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))

    await presenter.emit(
        adapter.self_active_update(
            text="hello live",
            secondary_text="translated live",
            occupant_key="self:merge-live",
            created_at=10.0,
        )
    )
    revision_before_clear = presenter.snapshot().revision

    await presenter.emit(
        adapter.self_active_update(
            text="hello live",
            secondary_text="",
            occupant_key="self:merge-live",
            created_at=11.0,
        )
    )

    assert presenter.snapshot().revision == revision_before_clear + 1
    assert presenter.snapshot().blocks[-1].secondary_text == ""


@pytest.mark.asyncio
async def test_presenter_keeps_snapshot_order_when_active_self_promotes_to_finalized() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    adapter = OverlayEventAdapter(clock=clock)
    older_id = uuid4()
    promoted_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=older_id,
                channel="self",
                text="older",
                is_final=True,
                created_at=10.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.self_active_update(
            text="live self",
            occupant_key=f"self:{promoted_id}",
            created_at=11.0,
        )
    )
    before_promotion = presenter.snapshot().blocks

    await presenter.emit(
        SelfTranscriptFinal(
            event_id="final-promoted",
            seq=3,
            utterance_id=promoted_id,
            channel="self",
            text="live self",
            source_language="ko",
            target_language="en",
            created_at=12.0,
        )
    )

    blocks = presenter.snapshot().blocks
    assert [block.id for block in before_promotion] == [
        f"self:{older_id}",
        "self:active",
    ]
    assert [block.id for block in blocks] == [
        f"self:{older_id}",
        f"self:{promoted_id}",
    ]
    assert [block.appearance_seq for block in blocks] == [1, 2]


@pytest.mark.asyncio
async def test_presenter_keeps_first_visible_ttl_when_active_self_promotes_to_finalized() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        clock.advance(delay)
        await asyncio.sleep(0)

    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep=fake_sleep,
    )
    adapter = OverlayEventAdapter(clock=clock)
    utterance_id = uuid4()

    await presenter.emit(
        adapter.self_active_update(
            text="live self",
            occupant_key=f"self:{utterance_id}",
            created_at=10.0,
        )
    )
    clock.advance(2.0)
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="final-visible",
            seq=2,
            utterance_id=utterance_id,
            channel="self",
            text="live self",
            source_language="ko",
            target_language="en",
            created_at=12.0,
        )
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=utterance_id,
            channel="self",
            is_final=True,
            created_at=12.1,
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{utterance_id}"]

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert sleep_calls == [6.0]
    assert presenter.snapshot().blocks == []


@pytest.mark.asyncio
async def test_presenter_displaces_oldest_finalized_turn_and_tombstones_it() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))

    first = Transcript(
        utterance_id=uuid4(), channel="self", text="one", is_final=True, created_at=11.0
    )
    second = Transcript(
        utterance_id=uuid4(), channel="self", text="two", is_final=True, created_at=12.0
    )
    third = Transcript(
        utterance_id=uuid4(), channel="self", text="three", is_final=True, created_at=13.0
    )

    for transcript in (first, second, third):
        await presenter.emit(
            adapter.transcript_final(transcript, source_language="ko", target_language="en")
        )

    assert [block.primary_text for block in presenter.snapshot().blocks] == ["two", "three"]

    await presenter.emit(
        adapter.translation_final(
            utterance_id=first.utterance_id,
            channel="self",
            text="하나",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=14.0,
        )
    )

    assert [block.primary_text for block in presenter.snapshot().blocks] == ["two", "three"]
    assert all(
        block.occupant_key != f"self:{first.utterance_id}" for block in presenter.snapshot().blocks
    )


@pytest.mark.asyncio
async def test_presenter_assigns_peer_appearance_seq_on_first_visible_translation() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    peer_turn = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer one",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(peer_turn, source_language="en", target_language="ko")
    )
    assert presenter.snapshot().blocks == []

    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_turn.utterance_id,
            channel="peer",
            text="피어 하나",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=12.0,
        )
    )
    first_visible = presenter.snapshot().blocks[0]

    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_turn.utterance_id,
            channel="peer",
            text="피어 하나 수정",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=13.0,
        )
    )

    assert presenter.snapshot().blocks[0].occupant_key == f"peer:{peer_turn.utterance_id}"
    assert presenter.snapshot().blocks[0].appearance_seq == first_visible.appearance_seq


@pytest.mark.asyncio
async def test_presenter_hidden_peer_cancel_before_first_visibility_never_assigns_metadata() -> (
    None
):
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    peer_turn = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer one",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(peer_turn, source_language="en", target_language="ko")
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=peer_turn.utterance_id,
            channel="peer",
            created_at=11.5,
            is_final=False,
        )
    )

    assert presenter.snapshot().blocks == []
    assert bridge.snapshots == [] or bridge.snapshots[-1].blocks == []


@pytest.mark.asyncio
async def test_presenter_clear_for_runtime_detach_publishes_empty_snapshot_with_higher_revision() -> (
    None
):
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    utterance_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=utterance_id,
                channel="self",
                text="hello",
                is_final=True,
                created_at=11.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    revision_before_clear = presenter.snapshot().revision

    await presenter.clear_for_runtime_detach()

    assert presenter.snapshot().blocks == []
    assert presenter.snapshot().revision == revision_before_clear + 1
    assert bridge.snapshots[-1].blocks == []


@pytest.mark.asyncio
async def test_presenter_updates_secondary_visibility_preferences_without_changing_primary_semantics() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=40.0)
    adapter = OverlayEventAdapter(clock=clock)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
    )
    self_utterance_id = uuid4()
    peer_utterance_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=self_utterance_id,
                channel="self",
                text="self original",
                is_final=True,
                created_at=40.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=self_utterance_id,
            channel="self",
            text="self translation",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=40.1,
        )
    )
    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=peer_utterance_id,
                channel="peer",
                text="peer original",
                is_final=True,
                created_at=41.0,
            ),
            source_language="en",
            target_language="ko",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_utterance_id,
            channel="peer",
            text="peer translation",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=41.1,
        )
    )

    await presenter.update_display_preferences(
        show_translation=False,
        show_peer_original=False,
    )

    blocks_by_id = {block.id: block for block in presenter.snapshot().blocks}
    self_block = blocks_by_id[f"self:{self_utterance_id}"]
    peer_block = blocks_by_id[f"peer:{peer_utterance_id}"]

    assert self_block.primary_text == "self original"
    assert self_block.secondary_text == "self translation"
    assert self_block.secondary_enabled is False
    assert peer_block.primary_text == "peer translation"
    assert peer_block.secondary_text == "peer original"
    assert peer_block.secondary_enabled is False


@pytest.mark.asyncio
async def test_presenter_snapshot_publish_logs_only_to_detailed_runtime_logging() -> None:
    basic_runtime_logging, basic_stream = _make_runtime_logging_capture()
    detailed_runtime_logging, detailed_stream = _make_runtime_logging_capture()
    detailed_runtime_logging.set_mode(SessionLoggingMode.DETAILED)

    def basic_runtime_log_detailed(message: str, *, level: int = logging.INFO) -> bool:
        return basic_runtime_logging.emit_detailed(message, level=level)

    def detailed_runtime_log_detailed(message: str, *, level: int = logging.INFO) -> bool:
        return detailed_runtime_logging.emit_detailed(message, level=level)

    basic_presenter = OverlayPresenter(
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
        runtime_log_detailed=basic_runtime_log_detailed,
    )
    detailed_presenter = OverlayPresenter(
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
        runtime_log_detailed=detailed_runtime_log_detailed,
    )
    basic_adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    detailed_adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))

    try:
        await basic_presenter.emit(
            basic_adapter.transcript_final(
                Transcript(
                    utterance_id=uuid4(),
                    channel="self",
                    text="hello basic",
                    is_final=True,
                    created_at=11.0,
                ),
                source_language="ko",
                target_language="en",
            )
        )
        await detailed_presenter.emit(
            detailed_adapter.transcript_final(
                Transcript(
                    utterance_id=uuid4(),
                    channel="self",
                    text="hello detailed",
                    is_final=True,
                    created_at=11.0,
                ),
                source_language="ko",
                target_language="en",
            )
        )

        assert not any(
            "[OverlayPresenter] Snapshot publish" in message
            for message in _runtime_log_messages(basic_stream)
        )
        assert any(
            "[OverlayPresenter] Snapshot publish" in message
            for message in _runtime_log_messages(detailed_stream)
        )
    finally:
        basic_runtime_logging.close()
        detailed_runtime_logging.close()
