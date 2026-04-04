from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
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
_CLOSED_TOMBSTONE_LIMIT = 64
LATE_ARRIVAL_WINDOW_SECONDS = 5.0
VISIBLE_TTL_SECONDS = 5.0
SleepFn = Callable[[float], Awaitable[None]]


class OverlayPresentationTransport(Protocol):
    async def replace_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None: ...

    async def broadcast_shutdown(self) -> None: ...


@dataclass(slots=True)
class _LogicalCaptionEntry:
    channel: str
    utterance_id: UUID
    original_text: str = ""
    translation_text: str = ""
    occupant_key: str = ""
    appearance_seq: int | None = None
    ever_publishable: bool = False
    visible_since: float | None = None
    last_updated_seq: int = 0
    closed_seq: int | None = None
    closed_at: float | None = None

    @property
    def block_id(self) -> str:
        return f"{self.channel}:{self.utterance_id}"


@dataclass(slots=True)
class _ActiveSelfEntry:
    text: str
    last_updated_seq: int
    occupant_key: str
    appearance_seq: int
    visible_since: float


@dataclass(slots=True)
class OverlayPresenter(OverlaySink):
    calibration: OverlayCalibration
    bridge: OverlayPresentationTransport | None = None
    clock: Clock = field(default_factory=SystemClock)
    sleep: SleepFn = asyncio.sleep
    visible_window_target_blocks: int = VISIBLE_WINDOW_TARGET_BLOCKS
    show_translation: bool = True
    show_peer_original: bool = True

    _entries: dict[tuple[str, UUID], _LogicalCaptionEntry] = field(
        init=False,
        default_factory=dict,
    )
    _closed_tombstones: OrderedDict[tuple[str, UUID], int] = field(
        init=False,
        default_factory=OrderedDict,
    )
    _active_self: _ActiveSelfEntry | None = field(init=False, default=None)
    _expiration_tasks: dict[tuple[str, UUID], asyncio.Task[None]] = field(
        init=False,
        default_factory=dict,
    )
    _revision: int = field(init=False, default=0)
    _appearance_seq: int = field(init=False, default=0)
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
        self._cancel_all_expiration_tasks()
        self._entries.clear()
        self._closed_tombstones.clear()
        self._active_self = None
        self._revision = 0
        self._appearance_seq = 0
        self._snapshot = OverlayPresentationSnapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            blocks=[],
        )

    async def clear_for_runtime_detach(self) -> None:
        self._cancel_all_expiration_tasks()
        self._entries.clear()
        self._closed_tombstones.clear()
        self._active_self = None
        self._revision += 1
        self._snapshot = OverlayPresentationSnapshot(
            revision=self._revision,
            calibration=_calibration_from_overlay(self.calibration),
            blocks=[],
        )
        if self.bridge is not None:
            await self.bridge.replace_snapshot(self._snapshot)

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

    async def update_display_preferences(
        self,
        *,
        show_translation: bool,
        show_peer_original: bool,
    ) -> None:
        next_show_translation = bool(show_translation)
        next_show_peer_original = bool(show_peer_original)
        if (
            next_show_translation == self.show_translation
            and next_show_peer_original == self.show_peer_original
        ):
            return
        self.show_translation = next_show_translation
        self.show_peer_original = next_show_peer_original
        await self._publish_if_changed()

    async def broadcast_shutdown(self) -> None:
        if self.bridge is None:
            return
        await self.bridge.broadcast_shutdown()

    def _apply_event(self, event: OverlayEventUnion) -> bool:
        now = self.clock.now()
        self._expire_closed_entries(now=now)

        if isinstance(event, SelfActiveUpdate):
            if self._active_self is not None and event.seq < self._active_self.last_updated_seq:
                return False
            if (
                self._active_self is not None
                and self._active_self.occupant_key == event.occupant_key
            ):
                appearance_seq = self._active_self.appearance_seq
                visible_since = self._active_self.visible_since
            else:
                appearance_seq = self._next_appearance_seq()
                visible_since = now
            if (
                self._active_self is not None
                and self._active_self.text == event.text
                and self._active_self.occupant_key == event.occupant_key
                and self._active_self.appearance_seq == appearance_seq
            ):
                self._active_self.last_updated_seq = event.seq
                return False
            self._active_self = _ActiveSelfEntry(
                text=event.text,
                last_updated_seq=event.seq,
                occupant_key=event.occupant_key,
                appearance_seq=appearance_seq,
                visible_since=visible_since,
            )
            return True

        if isinstance(event, SelfActiveClear):
            if self._active_self is None:
                return False
            if event.seq < self._active_self.last_updated_seq:
                return False
            self._active_self = None
            return True

        if isinstance(event, (SelfTranscriptFinal, PeerTranscriptFinal)):
            if self._is_tombstoned(event.channel, event.utterance_id):
                return False
            key = self._entry_key(event.channel, event.utterance_id)
            entry = self._entry_for(event.channel, event.utterance_id)
            if event.seq < entry.last_updated_seq:
                return False
            consumed_active = False
            active_self_metadata: _ActiveSelfEntry | None = None
            finalized_occupant_key = self._finalized_occupant_key(event.channel, event.utterance_id)
            if (
                isinstance(event, SelfTranscriptFinal)
                and self._active_self is not None
                and event.seq >= self._active_self.last_updated_seq
                and self._active_self.occupant_key == finalized_occupant_key
            ):
                active_self_metadata = self._active_self
                self._active_self = None
                consumed_active = True
            if entry.original_text == event.text and entry.last_updated_seq == event.seq:
                return consumed_active
            entry.original_text = event.text
            entry.last_updated_seq = event.seq
            if active_self_metadata is not None:
                self._inherit_active_self_visibility_metadata(entry, active_self_metadata)
            self._refresh_entry_visibility_and_expiration(key, entry, now=now)
            return True

        if isinstance(event, (TranslationStreamUpdate, TranslationFinal)):
            if self._is_tombstoned(event.channel, event.utterance_id):
                return False
            key = self._entry_key(event.channel, event.utterance_id)
            entry = self._entry_for(event.channel, event.utterance_id)
            if event.seq < entry.last_updated_seq:
                return False
            if entry.translation_text == event.text and entry.last_updated_seq == event.seq:
                return False
            entry.translation_text = event.text
            entry.last_updated_seq = event.seq
            self._refresh_entry_visibility_and_expiration(key, entry, now=now)
            return True

        if isinstance(event, UtteranceClosed):
            key = self._entry_key(event.channel, event.utterance_id)
            if key in self._closed_tombstones:
                return False
            entry = self._entries.get(key)
            if entry is None:
                return False
            if event.seq < entry.last_updated_seq:
                return False
            if entry.closed_seq == event.seq:
                return False
            entry.closed_seq = event.seq
            entry.closed_at = now
            entry.last_updated_seq = event.seq
            self._schedule_expiration(key, entry)
            return True

        return False

    def _entry_for(self, channel: str | None, utterance_id: UUID | None) -> _LogicalCaptionEntry:
        key = self._entry_key(channel, utterance_id)
        entry = self._entries.get(key)
        if entry is None:
            entry = _LogicalCaptionEntry(channel=key[0], utterance_id=key[1])
            self._entries[key] = entry
        return entry

    def _entry_key(self, channel: str | None, utterance_id: UUID | None) -> tuple[str, UUID]:
        if channel not in ("self", "peer"):
            raise ValueError(f"invalid overlay channel: {channel!r}")
        if utterance_id is None:
            raise ValueError("overlay presenter requires utterance_id for finalized entries")
        return (channel, utterance_id)

    def _is_tombstoned(self, channel: str | None, utterance_id: UUID | None) -> bool:
        return self._entry_key(channel, utterance_id) in self._closed_tombstones

    async def _publish_if_changed(self) -> None:
        self._expire_closed_entries(now=self.clock.now())
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
        self._expire_closed_entries(now=self.clock.now())
        visible_entry_keys = self._logical_visible_entry_keys()
        self._prune_displaced_finalized_entries(set(visible_entry_keys))
        blocks = [
            block
            for key in visible_entry_keys
            if (entry := self._entries.get(key)) is not None
            and (block := self._build_presentation_block(entry)) is not None
        ]
        if self._active_self is not None and self._active_self.text:
            blocks.append(
                OverlayPresentationBlock(
                    id=_ACTIVE_SELF_BLOCK_ID,
                    occupant_key=self._active_self.occupant_key,
                    appearance_seq=self._active_self.appearance_seq,
                    channel="self",
                    block_variant="active_self",
                    primary_text=self._active_self.text,
                    secondary_text="",
                    secondary_enabled=self.show_translation,
                )
            )
        blocks.sort(key=lambda block: (block.appearance_seq, block.occupant_key))
        return blocks

    def _logical_visible_entry_keys(self) -> list[tuple[str, UUID]]:
        finalized_limit = self.visible_window_target_blocks
        if self._active_self is not None and self._active_self.text:
            finalized_limit = max(finalized_limit - 1, 0)
        if finalized_limit == 0:
            return []

        publishable: list[tuple[int, str, tuple[str, UUID]]] = []
        for key, entry in self._entries.items():
            if not self._entry_is_publishable(entry):
                continue
            self._ensure_entry_visibility_metadata(
                entry,
                occupant_key=self._finalized_occupant_key(entry.channel, entry.utterance_id),
            )
            if entry.appearance_seq is None:
                continue
            publishable.append((entry.appearance_seq, entry.occupant_key, key))

        publishable.sort(key=lambda item: (item[0], item[1]))
        return [key for _, _, key in publishable[-finalized_limit:]]

    def _build_presentation_block(
        self,
        entry: _LogicalCaptionEntry,
    ) -> OverlayPresentationBlock | None:
        if entry.channel == "peer":
            primary_text = entry.translation_text.strip()
            if not primary_text:
                return None
            secondary_text = entry.original_text.strip()
            secondary_enabled = self.show_peer_original
        else:
            primary_text = entry.original_text.strip()
            if not primary_text:
                return None
            secondary_text = entry.translation_text.strip()
            secondary_enabled = self.show_translation

        return OverlayPresentationBlock(
            id=entry.block_id,
            occupant_key=entry.occupant_key,
            appearance_seq=entry.appearance_seq,
            channel=entry.channel,  # type: ignore[arg-type]
            block_variant="finalized",
            primary_text=primary_text,
            secondary_text=secondary_text,
            secondary_enabled=secondary_enabled,
        )

    def _prune_displaced_finalized_entries(self, visible_entry_keys: set[tuple[str, UUID]]) -> None:
        displaced_keys = [
            key
            for key, entry in self._entries.items()
            if self._entry_is_publishable(entry) and key not in visible_entry_keys
        ]
        for key in displaced_keys:
            entry = self._entries.get(key)
            if entry is None:
                continue
            self._remove_entry(key, tombstone_seq=entry.last_updated_seq)

    def _entry_is_publishable(self, entry: _LogicalCaptionEntry) -> bool:
        if entry.channel == "peer":
            return bool(entry.translation_text.strip())
        return bool(entry.original_text.strip())

    def _refresh_entry_visibility_and_expiration(
        self,
        key: tuple[str, UUID],
        entry: _LogicalCaptionEntry,
        *,
        now: float,
    ) -> None:
        if self._entry_is_publishable(entry):
            self._ensure_entry_visibility_metadata(
                entry,
                occupant_key=self._finalized_occupant_key(entry.channel, entry.utterance_id),
            )
            entry.ever_publishable = True
            if entry.visible_since is None:
                entry.visible_since = now
        if entry.closed_seq is not None:
            self._schedule_expiration(key, entry)

    def _finalized_occupant_key(self, channel: str, utterance_id: UUID) -> str:
        return f"{channel}:{utterance_id}"

    def _next_appearance_seq(self) -> int:
        self._appearance_seq += 1
        return self._appearance_seq

    def _ensure_entry_visibility_metadata(
        self,
        entry: _LogicalCaptionEntry,
        *,
        occupant_key: str,
    ) -> None:
        if not entry.occupant_key:
            entry.occupant_key = occupant_key
        if entry.appearance_seq is None:
            entry.appearance_seq = self._next_appearance_seq()

    def _inherit_active_self_visibility_metadata(
        self,
        entry: _LogicalCaptionEntry,
        active_entry: _ActiveSelfEntry,
    ) -> None:
        if not entry.occupant_key:
            entry.occupant_key = active_entry.occupant_key
        if entry.appearance_seq is None:
            entry.appearance_seq = active_entry.appearance_seq
        if entry.visible_since is None:
            entry.visible_since = active_entry.visible_since

    def _remember_tombstone(self, key: tuple[str, UUID], closed_seq: int) -> None:
        self._closed_tombstones.pop(key, None)
        self._closed_tombstones[key] = closed_seq
        while len(self._closed_tombstones) > _CLOSED_TOMBSTONE_LIMIT:
            self._closed_tombstones.popitem(last=False)

    def _schedule_expiration(
        self,
        key: tuple[str, UUID],
        entry: _LogicalCaptionEntry,
    ) -> None:
        self._cancel_expiration_task(key)
        if entry.closed_seq is None:
            return
        self._expiration_tasks[key] = asyncio.create_task(
            self._expire_entry_after_ttl(key, entry.closed_seq)
        )

    async def _expire_entry_after_ttl(self, key: tuple[str, UUID], closed_seq: int) -> None:
        try:
            while True:
                entry = self._entries.get(key)
                if entry is None or entry.closed_seq != closed_seq:
                    return

                deadline = self._entry_expiration_deadline(entry)
                if deadline is None:
                    return
                remaining = deadline - self.clock.now()
                if remaining > 0:
                    await self.sleep(remaining)
                    continue

                self._remove_entry(key, current_task=self._current_task())
                await self._publish_if_changed()
                return
        except asyncio.CancelledError:
            raise
        finally:
            current_task = self._current_task()
            if current_task is not None and self._expiration_tasks.get(key) is current_task:
                self._expiration_tasks.pop(key, None)

    def _expire_closed_entries(self, *, now: float) -> None:
        expired_keys = [
            key
            for key, entry in self._entries.items()
            if (deadline := self._entry_expiration_deadline(entry)) is not None and now >= deadline
        ]
        current_task = self._current_task()
        for key in expired_keys:
            self._remove_entry(key, current_task=current_task)

    def _entry_expiration_deadline(self, entry: _LogicalCaptionEntry) -> float | None:
        if entry.closed_at is None:
            return None
        if entry.visible_since is None:
            return entry.closed_at + LATE_ARRIVAL_WINDOW_SECONDS
        return max(entry.closed_at, entry.visible_since + VISIBLE_TTL_SECONDS)

    def _remove_entry(
        self,
        key: tuple[str, UUID],
        *,
        current_task: asyncio.Task[None] | None = None,
        tombstone_seq: int | None = None,
    ) -> None:
        if self._expiration_tasks.get(key) is not current_task:
            self._cancel_expiration_task(key)
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        seq = tombstone_seq if tombstone_seq is not None else entry.closed_seq
        if seq is not None:
            self._remember_tombstone(key, seq)

    def _cancel_expiration_task(self, key: tuple[str, UUID]) -> None:
        task = self._expiration_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_expiration_tasks(self) -> None:
        for task in self._expiration_tasks.values():
            if not task.done():
                task.cancel()
        self._expiration_tasks.clear()

    def _current_task(self) -> asyncio.Task[None] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None


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
