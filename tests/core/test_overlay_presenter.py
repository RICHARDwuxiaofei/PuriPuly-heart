from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.overlay.protocol import OverlayPresentationCalibration
from puripuly_heart.core.overlay.sink import (
    OverlayEventAdapter,
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
    TranslationFinal,
)
from puripuly_heart.domain.models import Transcript
from puripuly_heart.ui.overlay_calibration import OverlayCalibration


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
    assert bridge.snapshots[-1].blocks[-1].text == "hello now"


@pytest.mark.asyncio
async def test_presenter_moves_updated_translation_block_to_newest_visibility() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=FakeClock(_now=10.0),
    )
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))

    self_transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="self text",
        is_final=True,
        created_at=11.0,
    )
    peer_transcript = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer text",
        is_final=True,
        created_at=12.0,
        speaker_label="Speaker 0",
        peer_epoch=1,
    )

    await presenter.emit(
        adapter.transcript_final(
            self_transcript,
            source_language="ko",
            target_language="en",
        )
    )
    await presenter.emit(
        adapter.transcript_final(
            peer_transcript,
            source_language="en",
            target_language="ko",
        )
    )
    await presenter.emit(
        adapter.translation_final(
            utterance_id=self_transcript.utterance_id,
            channel="self",
            text="hello",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=13.0,
        )
    )

    latest = bridge.snapshots[-1]

    assert [block.id for block in latest.blocks] == [
        f"peer:{peer_transcript.utterance_id}",
        f"self:{self_transcript.utterance_id}",
    ]
    assert latest.blocks[-1].text == "self text (hello)"


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
    assert presenter.snapshot().blocks[-1].text == "live self"


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
    assert presenter.snapshot().blocks[-1].text == "latest original (latest translation)"
