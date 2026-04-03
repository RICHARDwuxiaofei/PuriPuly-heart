from __future__ import annotations

import asyncio
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
    assert bridge.snapshots[-1].blocks[-1].block_variant == "finalized"
    assert bridge.snapshots[-1].blocks[-1].primary_text == "hello now"
    assert bridge.snapshots[-1].blocks[-1].secondary_text == ""
    assert bridge.snapshots[-1].blocks[-1].secondary_enabled is True


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
        f"self:{self_transcript.utterance_id}",
    ]
    assert latest.blocks[-1].block_variant == "finalized"
    assert latest.blocks[-1].primary_text == "self text"
    assert latest.blocks[-1].secondary_text == "hello"
    assert latest.blocks[-1].secondary_enabled is True


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
async def test_presenter_keeps_closed_hidden_peer_entry_publishable_until_translation_arrives() -> None:
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
    assert presenter.snapshot().blocks == []


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
async def test_presenter_expires_visible_finalized_entry_after_five_seconds() -> None:
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

    assert sleep_calls == [5.0]
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
        )
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [block.id for block in presenter.snapshot().blocks] == ["self:active"]
    assert presenter.snapshot().blocks[0].primary_text == "live self"


@pytest.mark.asyncio
async def test_presenter_keeps_active_self_in_addition_to_two_finalized_turns() -> None:
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
        )
    )

    assert [block.id for block in presenter.snapshot().blocks] == [
        f"self:{utterance_ids[0]}",
        f"self:{utterance_ids[1]}",
        "self:active",
    ]


@pytest.mark.asyncio
async def test_presenter_updates_secondary_visibility_preferences_without_changing_primary_semantics() -> None:
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
