from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ChannelId = Literal["self", "peer"]


@dataclass(frozen=True, slots=True)
class OverlayPresentationCalibration:
    anchor: str = "head_locked"
    offset_x: float = 0.0
    offset_y: float = 0.0
    distance: float = 1.1
    text_scale: float = 1.0
    background_alpha: float = 0.24

    def to_dict(self) -> dict[str, object]:
        return {
            "anchor": self.anchor,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "distance": self.distance,
            "text_scale": self.text_scale,
            "background_alpha": self.background_alpha,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayPresentationCalibration":
        return cls(
            anchor=str(data.get("anchor", "head_locked")),
            offset_x=float(data.get("offset_x", 0.0)),
            offset_y=float(data.get("offset_y", 0.0)),
            distance=float(data.get("distance", 1.1)),
            text_scale=float(data.get("text_scale", 1.0)),
            background_alpha=float(data.get("background_alpha", 0.24)),
        )


@dataclass(frozen=True, slots=True)
class OverlayPresentationBlock:
    id: str
    channel: ChannelId
    text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "channel": self.channel,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayPresentationBlock":
        channel = data.get("channel")
        if channel not in ("self", "peer"):
            raise ValueError(f"invalid overlay presentation channel: {channel!r}")
        return cls(
            id=str(data["id"]),
            channel=channel,
            text=str(data["text"]),
        )


@dataclass(frozen=True, slots=True)
class OverlayPresentationSnapshot:
    revision: int = 0
    calibration: OverlayPresentationCalibration = field(
        default_factory=OverlayPresentationCalibration
    )
    blocks: list[OverlayPresentationBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "calibration": self.calibration.to_dict(),
            "blocks": [block.to_dict() for block in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayPresentationSnapshot":
        calibration = data.get("calibration", {})
        if not isinstance(calibration, dict):
            raise ValueError("overlay presentation snapshot calibration must be an object")

        raw_blocks = data.get("blocks", [])
        if not isinstance(raw_blocks, list):
            raise ValueError("overlay presentation snapshot blocks must be a list")

        blocks: list[OverlayPresentationBlock] = []
        for block in raw_blocks:
            if not isinstance(block, dict):
                raise ValueError(
                    "overlay presentation snapshot blocks must contain only dict items"
                )
            blocks.append(OverlayPresentationBlock.from_dict(block))

        return cls(
            revision=int(data.get("revision", 0)),
            calibration=OverlayPresentationCalibration.from_dict(calibration),
            blocks=blocks,
        )
