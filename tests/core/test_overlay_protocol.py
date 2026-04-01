from __future__ import annotations

import pytest

from puripuly_heart.core.overlay.protocol import (
    OverlayPresentationBlock,
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
)


def test_overlay_presentation_snapshot_round_trips_blocks_and_calibration() -> None:
    snapshot = OverlayPresentationSnapshot(
        revision=7,
        calibration=OverlayPresentationCalibration(
            anchor="head_locked",
            offset_x=0.15,
            offset_y=-0.2,
            distance=1.1,
            text_scale=1.25,
            background_alpha=0.4,
        ),
        blocks=[
            OverlayPresentationBlock(id="self:1", channel="self", text="hello"),
            OverlayPresentationBlock(id="peer:2", channel="peer", text="hola (hello)"),
        ],
    )

    restored = OverlayPresentationSnapshot.from_dict(snapshot.to_dict())

    assert restored.revision == 7
    assert restored.calibration.anchor == "head_locked"
    assert restored.calibration.distance == 1.1
    assert restored.blocks == [
        OverlayPresentationBlock(id="self:1", channel="self", text="hello"),
        OverlayPresentationBlock(id="peer:2", channel="peer", text="hola (hello)"),
    ]


def test_overlay_presentation_snapshot_rejects_non_list_blocks() -> None:
    with pytest.raises(ValueError, match="blocks must be a list"):
        OverlayPresentationSnapshot.from_dict(
            {
                "revision": 1,
                "calibration": OverlayPresentationCalibration().to_dict(),
                "blocks": "not-a-list",
            }
        )


def test_overlay_presentation_snapshot_rejects_non_dict_block_items() -> None:
    with pytest.raises(ValueError, match="dict items"):
        OverlayPresentationSnapshot.from_dict(
            {
                "revision": 1,
                "calibration": OverlayPresentationCalibration().to_dict(),
                "blocks": ["not-a-dict"],
            }
        )
