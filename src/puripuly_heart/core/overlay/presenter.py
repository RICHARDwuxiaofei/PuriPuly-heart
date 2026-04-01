from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.ui.overlay_calibration import OverlayCalibration

from .protocol import (
    OverlayPresentationBlock,
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
)
from .sink import (
    OverlayEventUnion,
    OverlaySink,
    PeerTranscriptFinal,
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)

VISIBLE_WINDOW_TARGET_BLOCKS = 2
_ACTIVE_SELF_BLOCK_ID = "self:active"


class OverlayPresentationTransport(Protocol):
    async def replace_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None: ...

    async def broadcast_shutdown(self) -> None: ...


@dataclass(slots=True)
class _LogicalCaptionEntry:
    channel: str
    utterance_id: UUID
    original_text: str = ""
    translation_text: str = ""
    last_updated_seq: int = 0

    @property
    def block_id(self) -> str:
        return f"{self.channel}:{self.utterance_id}"

    def composed_text(self) -> str:
        if self.channel == "peer":
            return _compose_caption_pair(self.translation_text, self.original_text)
        return _compose_caption_pair(self.original_text, self.translation_text)


@dataclass(slots=True)
class _ActiveSelfEntry:
    text: str
    last_updated_seq: int

    def composed_text(self) -> str:
        return self.text


@dataclass(slots=True)
class OverlayPresenter(OverlaySink):
    calibration: OverlayCalibration
    bridge: OverlayPresentationTransport | None = None
    clock: Clock = field(default_factory=SystemClock)
    visible_window_target_blocks: int = VISIBLE_WINDOW_TARGET_BLOCKS

    _entries: dict[tuple[str, UUID], _LogicalCaptionEntry] = field(
        init=False,
        default_factory=dict,
    )
    _active_self: _ActiveSelfEntry | None = field(init=False, default=None)
    _revision: int = field(init=False, default=0)
    _snapshot: OverlayPresentationSnapshot = field(init=False)

    def __post_init__(self) -> None:
        self._snapshot = OverlayPresentationSnapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            blocks=[],
        )

    def attach_bridge(self, bridge: OverlayPresentationTransport) -> None:
        self.bridge = bridge

    def detach_bridge(self) -> None:
        self.bridge = None

    def snapshot(self) -> OverlayPresentationSnapshot:
        return self._snapshot

    def reset_scene(self) -> None:
        self._entries.clear()
        self._active_self = None
        self._revision = 0
        self._snapshot = OverlayPresentationSnapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            blocks=[],
        )

    async def emit(self, event: OverlayEventUnion) -> None:
        changed = self._apply_event(event)
        if not changed:
            return
        await self._publish_if_changed()

    async def update_calibration(self, calibration: OverlayCalibration) -> None:
        if calibration == self.calibration:
            return
        self.calibration = calibration.copy()
        await self._publish_if_changed()

    async def broadcast_shutdown(self) -> None:
        if self.bridge is None:
            return
        await self.bridge.broadcast_shutdown()

    def _apply_event(self, event: OverlayEventUnion) -> bool:
        if isinstance(event, SelfActiveUpdate):
            if self._active_self is not None and event.seq < self._active_self.last_updated_seq:
                return False
            next_active = _ActiveSelfEntry(text=event.text, last_updated_seq=event.seq)
            if self._active_self == next_active:
                return False
            self._active_self = next_active
            return True

        if isinstance(event, SelfActiveClear):
            if self._active_self is None:
                return False
            if event.seq < self._active_self.last_updated_seq:
                return False
            self._active_self = None
            return True

        if isinstance(event, (SelfTranscriptFinal, PeerTranscriptFinal)):
            entry = self._entry_for(event.channel, event.utterance_id)
            if event.seq < entry.last_updated_seq:
                return False
            if entry.original_text == event.text and entry.last_updated_seq == event.seq:
                return False
            entry.original_text = event.text
            entry.last_updated_seq = event.seq
            return True

        if isinstance(event, (TranslationStreamUpdate, TranslationFinal)):
            entry = self._entry_for(event.channel, event.utterance_id)
            if event.seq < entry.last_updated_seq:
                return False
            if entry.translation_text == event.text and entry.last_updated_seq == event.seq:
                return False
            entry.translation_text = event.text
            entry.last_updated_seq = event.seq
            return True

        if isinstance(event, UtteranceClosed):
            return False

        return False

    def _entry_for(self, channel: str | None, utterance_id: UUID | None) -> _LogicalCaptionEntry:
        if channel not in ("self", "peer"):
            raise ValueError(f"invalid overlay channel: {channel!r}")
        if utterance_id is None:
            raise ValueError("overlay presenter requires utterance_id for finalized entries")

        key = (channel, utterance_id)
        entry = self._entries.get(key)
        if entry is None:
            entry = _LogicalCaptionEntry(channel=channel, utterance_id=utterance_id)
            self._entries[key] = entry
        return entry

    async def _publish_if_changed(self) -> None:
        next_blocks = self._visible_blocks()
        next_calibration = _calibration_from_overlay(self.calibration)
        if next_blocks == self._snapshot.blocks and next_calibration == self._snapshot.calibration:
            return

        self._revision += 1
        self._snapshot = OverlayPresentationSnapshot(
            revision=self._revision,
            calibration=next_calibration,
            blocks=next_blocks,
        )
        if self.bridge is not None:
            await self.bridge.replace_snapshot(self._snapshot)

    def _visible_blocks(self) -> list[OverlayPresentationBlock]:
        candidates: list[tuple[int, str, OverlayPresentationBlock]] = []

        for entry in self._entries.values():
            text = entry.composed_text()
            if not text:
                continue
            candidates.append(
                (
                    entry.last_updated_seq,
                    entry.block_id,
                    OverlayPresentationBlock(
                        id=entry.block_id,
                        channel=entry.channel,  # type: ignore[arg-type]
                        text=text,
                    ),
                )
            )

        if self._active_self is not None and self._active_self.text:
            candidates.append(
                (
                    self._active_self.last_updated_seq,
                    _ACTIVE_SELF_BLOCK_ID,
                    OverlayPresentationBlock(
                        id=_ACTIVE_SELF_BLOCK_ID,
                        channel="self",
                        text=self._active_self.composed_text(),
                    ),
                )
            )

        candidates.sort(key=lambda item: (item[0], item[1]))
        selected = candidates[-self.visible_window_target_blocks :]
        return [block for _, _, block in selected]


def _calibration_from_overlay(
    calibration: OverlayCalibration,
) -> OverlayPresentationCalibration:
    return OverlayPresentationCalibration(
        anchor=calibration.anchor,
        offset_x=calibration.offset_x,
        offset_y=calibration.offset_y,
        distance=calibration.distance,
        text_scale=calibration.text_scale,
        background_alpha=calibration.background_alpha,
    )


def _compose_caption_pair(primary: str, secondary: str) -> str:
    if not primary:
        return secondary
    if not secondary:
        return primary
    return f"{primary} ({secondary})"
