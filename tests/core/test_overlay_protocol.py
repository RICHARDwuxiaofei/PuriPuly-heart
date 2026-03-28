from __future__ import annotations

from uuid import uuid4

import pytest

from puripuly_heart.core.overlay.protocol import (
    OVERLAY_ROW_IDENTITY_RULE,
    OverlayStateSnapshot,
    PeerTranscriptFinal,
    SelfTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)


def test_translation_stream_update_serializes_common_identity_and_context_fields() -> None:
    event = TranslationStreamUpdate(
        event_id="evt-1",
        seq=7,
        utterance_id=uuid4(),
        channel="peer",
        text="hello wor",
        source_language="ko",
        target_language="en",
        created_at=123.0,
        is_final=False,
        applied_context_mode="integrated",
        speaker_label="Speaker 0",
        peer_epoch=3,
    )

    payload = event.to_dict()

    assert payload["type"] == "translation_stream_update"
    assert payload["event_id"] == "evt-1"
    assert payload["seq"] == 7
    assert payload["channel"] == "peer"
    assert payload["speaker_label"] == "Speaker 0"
    assert payload["applied_context_mode"] == "integrated"
    assert payload["source_language"] == "ko"
    assert payload["target_language"] == "en"
    assert payload["created_at"] == 123.0
    assert payload["is_final"] is False


def test_overlay_state_snapshot_round_trips_channel_identity_and_ordering() -> None:
    event = PeerTranscriptFinal(
        event_id="evt-2",
        seq=8,
        utterance_id=uuid4(),
        channel="peer",
        text="hello there",
        source_language="en",
        target_language="ko",
        created_at=456.0,
        is_final=True,
        speaker_label="Speaker 1",
        peer_epoch=2,
    )

    snapshot = OverlayStateSnapshot(events=[event])
    restored = OverlayStateSnapshot.from_dict(snapshot.to_dict())
    restored_event = restored.events[0]

    assert restored_event.event_id == "evt-2"
    assert restored_event.seq == 8
    assert restored_event.channel == "peer"
    assert restored_event.utterance_id == event.utterance_id


def test_overlay_row_key_uses_channel_plus_utterance_id_identity_rule() -> None:
    utterance_id = uuid4()
    event = PeerTranscriptFinal(
        event_id="evt-3",
        seq=9,
        utterance_id=utterance_id,
        channel="peer",
        text="identity",
        source_language="en",
        target_language="ko",
        created_at=789.0,
    )

    assert OVERLAY_ROW_IDENTITY_RULE == "channel+utterance_id"
    assert event.row_key == f"peer:{utterance_id}"


def test_final_events_reject_invalid_channel_or_is_final_values() -> None:
    utterance_id = uuid4()

    with pytest.raises(ValueError, match="channel='self'"):
        SelfTranscriptFinal(
            event_id="evt-4",
            seq=10,
            utterance_id=utterance_id,
            channel="peer",
            text="self text",
            source_language="ko",
            target_language="en",
            created_at=100.0,
        )

    with pytest.raises(ValueError, match="channel='peer'"):
        PeerTranscriptFinal(
            event_id="evt-5",
            seq=11,
            utterance_id=utterance_id,
            channel="self",
            text="peer text",
            source_language="en",
            target_language="ko",
            created_at=101.0,
        )

    with pytest.raises(ValueError, match="is_final=True"):
        TranslationFinal(
            event_id="evt-6",
            seq=12,
            utterance_id=utterance_id,
            channel="peer",
            text="final text",
            source_language="en",
            target_language="ko",
            created_at=102.0,
            is_final=False,
        )


def test_overlay_state_snapshot_from_dict_rejects_non_dict_event_items() -> None:
    with pytest.raises(ValueError, match="dict items"):
        OverlayStateSnapshot.from_dict({"events": ["not-a-dict"]})


def test_utterance_closed_round_trips_incomplete_state() -> None:
    event = UtteranceClosed(
        event_id="evt-7",
        seq=13,
        utterance_id=uuid4(),
        channel="peer",
        created_at=103.0,
        is_final=False,
    )

    restored = OverlayStateSnapshot.from_dict({"events": [event.to_dict()]}).events[0]

    assert isinstance(restored, UtteranceClosed)
    assert restored.is_final is False
