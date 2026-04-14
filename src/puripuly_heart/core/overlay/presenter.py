from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.ui.overlay_calibration import OverlayCalibration

from .diagnostics import OverlayDiagnosticsRecorder
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
_CLOSED_TOMBSTONE_LIMIT = 64
LATE_ARRIVAL_WINDOW_SECONDS = 5.0
VISIBLE_TTL_SECONDS = 8.0
SELF_TRANSLATION_MIN_VISIBLE_SECONDS = 4.0
SleepFn = Callable[[float], Awaitable[None]]


class OverlayPresentationTransport(Protocol):
    async def replace_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None: ...

    async def broadcast_shutdown(self) -> None: ...


class RuntimeDetailedLogger(Protocol):
    def __call__(self, message: str, *, level: int = logging.INFO) -> bool: ...


@dataclass(slots=True)
class _LogicalTurnEntry:
    channel: str
    utterance_id: UUID
    first_input_seq: int | None = None
    live_text: str = ""
    live_secondary_text: str = ""
    live_update_id: str | None = None
    live_origin_wall_clock_ms: int | None = None
    live_session_scope: str | None = None
    live_source_text_hash: str | None = None
    live_source_text_len: int | None = None
    live_logical_turn_key: str | None = None
    live_seq: int | None = None
    original_text: str = ""
    original_seq: int | None = None
    translation_text: str = ""
    translation_update_id: str | None = None
    translation_origin_wall_clock_ms: int | None = None
    translation_session_scope: str | None = None
    translation_source_text_hash: str | None = None
    translation_source_text_len: int | None = None
    translation_logical_turn_key: str | None = None
    translation_seq: int | None = None
    occupant_key: str = ""
    appearance_seq: int | None = None
    publishable_seq: int | None = None
    ever_publishable: bool = False
    ever_visible: bool = False
    visible_since: float | None = None
    last_meaningful_visible_at: float | None = None
    translation_visible_since: float | None = None
    translation_observed_visible_since: float | None = None
    last_updated_seq: int = 0
    closed_seq: int | None = None
    closed_at: float | None = None
    retained_hidden: bool = False
    window_evicted_at: float | None = None
    expiration_revision: int = 0

    @property
    def block_id(self) -> str:
        return f"{self.channel}:{self.utterance_id}"


@dataclass(slots=True)
class OverlayPresenter(OverlaySink):
    calibration: OverlayCalibration
    bridge: OverlayPresentationTransport | None = None
    diagnostics: OverlayDiagnosticsRecorder | None = None
    runtime_log_detailed: RuntimeDetailedLogger | None = None
    clock: Clock = field(default_factory=SystemClock)
    sleep: SleepFn = asyncio.sleep
    visible_window_target_blocks: int = VISIBLE_WINDOW_TARGET_BLOCKS
    show_translation: bool = True
    show_peer_original: bool = True

    _entries: dict[tuple[str, UUID], _LogicalTurnEntry] = field(
        init=False,
        default_factory=dict,
    )
    _terminal_registry: OrderedDict[tuple[str, UUID], int] = field(
        init=False,
        default_factory=OrderedDict,
    )
    _scene_terminal_keys: set[tuple[str, UUID]] = field(
        init=False,
        default_factory=set,
    )
    _scene_terminal_reasons: dict[tuple[str, UUID], str] = field(
        init=False,
        default_factory=dict,
    )
    _retired_preview_self_seqs: OrderedDict[tuple[str, UUID], int] = field(
        init=False,
        default_factory=OrderedDict,
    )
    _live_self_turn_key: tuple[str, UUID] | None = field(init=False, default=None)
    _expiration_tasks: dict[tuple[str, UUID], asyncio.Task[None]] = field(
        init=False,
        default_factory=dict,
    )
    _revision: int = field(init=False, default=0)
    _appearance_seq: int = field(init=False, default=0)
    _snapshot: OverlayPresentationSnapshot = field(init=False)
    _last_visible_window_signature: tuple[object, ...] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._snapshot = OverlayPresentationSnapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            blocks=[],
        )

    def _emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        if self.runtime_log_detailed is None:
            return False
        try:
            return self.runtime_log_detailed(message, level=level)
        except Exception:
            return False

    def _emit_detailed_lazy(
        self,
        build_message: Callable[[], str],
        *,
        level: int = logging.INFO,
    ) -> bool:
        runtime_log_detailed = self.runtime_log_detailed
        if runtime_log_detailed is None:
            return False

        owner = getattr(runtime_log_detailed, "__self__", None)
        try:
            if owner is not None:
                emit_detailed_lazy = getattr(owner, "emit_detailed_lazy", None)
                if callable(emit_detailed_lazy):
                    return emit_detailed_lazy(build_message, level=level)
                log_detailed_lazy = getattr(owner, "log_detailed_lazy", None)
                if callable(log_detailed_lazy):
                    return log_detailed_lazy(build_message, level=level)
            return runtime_log_detailed(build_message(), level=level)
        except Exception:
            return False

    def _emit_turn_decision(
        self,
        decision: str,
        *,
        disposition: str | None = None,
        key: tuple[str, UUID] | None = None,
        entry: _LogicalTurnEntry | None = None,
        block: OverlayPresentationBlock | None = None,
        extras: dict[str, object] | None = None,
    ) -> bool:
        def build_message() -> str:
            resolved_key = key
            if resolved_key is None and entry is not None:
                resolved_key = (entry.channel, entry.utterance_id)
            parts = [f"decision={decision}"]
            if disposition is not None:
                parts.append(f"disposition={disposition}")
            if resolved_key is not None:
                parts.append(f"entry={self._format_entry_key(resolved_key)}")
            if entry is not None:
                parts.extend(
                    [
                        f"channel={entry.channel}",
                        f"publishable={self._entry_is_publishable(entry)}",
                        f"ever_visible={entry.ever_visible}",
                        "ever_visible_with_translation="
                        f"{entry.translation_observed_visible_since is not None}",
                        f"retained_hidden={entry.retained_hidden}",
                    ]
                )
            if block is not None:
                parts.extend(
                    [
                        f"block_variant={block.block_variant}",
                        f"primary_len={len(block.primary_text)}",
                        f"secondary_len={len(block.secondary_text)}",
                    ]
                )
            if extras is not None:
                for field_name, value in extras.items():
                    parts.append(f"{field_name}={value}")
            return f"[OverlayPresenter][Decision] {' '.join(parts)}"

        return self._emit_detailed_lazy(build_message)

    def _emit_pair_state(
        self,
        key: tuple[str, UUID],
        entry: _LogicalTurnEntry,
        block: OverlayPresentationBlock,
        *,
        publish_kind: str,
    ) -> bool:
        def build_message() -> str:
            rendered_primary_source, rendered_secondary_source = self._rendered_text_sources(
                entry,
                block,
            )
            parts = [
                "[OverlayPresenter][PairState]",
                f"entry={self._format_entry_key(key)}",
                f"channel={entry.channel}",
                f"block_variant={block.block_variant}",
                f"publish_kind={publish_kind}",
                f"update_id={block.update_id}",
                f"origin_wall_clock_ms={block.origin_wall_clock_ms}",
                f"source_text_hash={block.source_text_hash}",
                f"source_text_len={block.source_text_len}",
                f"original_seq={entry.original_seq}",
                f"translation_seq={entry.translation_seq}",
                "rendered_pair_state="
                f"{self._rendered_pair_state(rendered_primary_source, rendered_secondary_source)}",
                f"rendered_primary_source={rendered_primary_source}",
                f"rendered_secondary_source={rendered_secondary_source}",
                f"appearance_seq={block.appearance_seq}",
                f"primary_len={len(block.primary_text)}",
                f"secondary_len={len(block.secondary_text) if block.secondary_enabled else 0}",
            ]
            elapsed_ms = self._elapsed_from_origin_wall_clock_ms(block.origin_wall_clock_ms)
            if elapsed_ms is not None:
                parts.append(f"elapsed_ms={elapsed_ms}")
            return " ".join(parts)

        return self._emit_detailed_lazy(build_message)

    def _emit_skip_disposition(
        self,
        *,
        decision: str,
        disposition: str,
        key: tuple[str, UUID] | None = None,
        entry: _LogicalTurnEntry | None = None,
        extras: dict[str, object] | None = None,
    ) -> bool:
        return self._emit_turn_decision(
            decision,
            disposition=disposition,
            key=key,
            entry=entry,
            extras=extras,
        )

    def _elapsed_from_origin_wall_clock_ms(self, origin_wall_clock_ms: int | None) -> int | None:
        if origin_wall_clock_ms is None:
            return None
        return max(0, int(time.time() * 1000) - origin_wall_clock_ms)

    def _rendered_text_sources(
        self,
        entry: _LogicalTurnEntry,
        block: OverlayPresentationBlock,
    ) -> tuple[str, str]:
        secondary_source = "none"
        if block.secondary_enabled and block.secondary_text:
            if entry.channel == "peer":
                secondary_source = "original_text"
            elif block.block_variant == "active_self" and entry.live_secondary_text.strip():
                secondary_source = "live_secondary_text"
            else:
                secondary_source = "translation_text"

        if block.block_variant == "active_self":
            return "live_text", secondary_source
        if entry.channel == "peer":
            return "translation_text", secondary_source
        return "original_text", secondary_source

    def _rendered_pair_state(self, primary_source: str, secondary_source: str) -> str:
        if primary_source == "live_text":
            if secondary_source == "live_secondary_text":
                return "live_with_preview_translation"
            if secondary_source == "translation_text":
                return "live_with_translation"
            return "live_only"
        if primary_source == "translation_text":
            if secondary_source == "original_text":
                return "translation_with_original"
            return "translation_only"
        if secondary_source in {"translation_text", "live_secondary_text"}:
            return "original_with_translation"
        return "original_only"

    def _rendered_self_translation_text(self, entry: _LogicalTurnEntry) -> str:
        live_secondary_text = entry.live_secondary_text.strip()
        if live_secondary_text:
            return live_secondary_text
        return entry.translation_text.strip()

    def _update_self_translation_visibility(
        self,
        entry: _LogicalTurnEntry,
        *,
        previous_rendered_text: str,
        next_rendered_text: str,
        now: float,
    ) -> None:
        if not self.show_translation:
            return
        entry.translation_visible_since = self._next_translation_visible_since(
            previous_text=previous_rendered_text,
            next_text=next_rendered_text,
            previous_visible_since=entry.translation_visible_since,
            now=now,
        )
        if next_rendered_text and entry.translation_observed_visible_since is None:
            entry.translation_observed_visible_since = now

    def _should_ignore_terminal_update(
        self, channel: str | None, utterance_id: UUID | None
    ) -> bool:
        key = self._entry_key(channel, utterance_id)
        if key not in self._scene_terminal_keys and key not in self._terminal_registry:
            return False
        terminal_reason = self._scene_terminal_reasons.get(key)
        if terminal_reason == "evicted_by_newer_turn":
            self._emit_turn_decision(
                "overlay_turn_late_update_ignored_after_eviction",
                disposition="evicted",
                key=key,
                extras={"terminal_reason": terminal_reason},
            )
        elif terminal_reason == "expired":
            self._emit_turn_decision(
                "overlay_turn_late_update_ignored_after_idle_hide",
                disposition="hidden_idle_ttl",
                key=key,
                extras={"terminal_reason": terminal_reason},
            )
        return True

    def _remember_scene_terminal_reason(self, key: tuple[str, UUID], *, reason: str) -> None:
        self._scene_terminal_keys.add(key)
        self._scene_terminal_reasons[key] = reason

    def attach_bridge(self, bridge: OverlayPresentationTransport) -> None:
        self.bridge = bridge

    def detach_bridge(self) -> None:
        self.bridge = None

    def snapshot(self) -> OverlayPresentationSnapshot:
        return self._snapshot

    def reset_scene(self) -> None:
        self._cancel_all_expiration_tasks()
        self._clear_entries_for_reason("scene_reset")
        self._terminal_registry.clear()
        self._scene_terminal_keys.clear()
        self._scene_terminal_reasons.clear()
        self._retired_preview_self_seqs.clear()
        self._live_self_turn_key = None
        self._revision = 0
        self._appearance_seq = 0
        self._last_visible_window_signature = None
        self._snapshot = OverlayPresentationSnapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            blocks=[],
        )

    async def clear_for_runtime_detach(self) -> None:
        self._cancel_all_expiration_tasks()
        self._clear_entries_for_reason("scene_reset")
        self._terminal_registry.clear()
        self._scene_terminal_keys.clear()
        self._scene_terminal_reasons.clear()
        self._retired_preview_self_seqs.clear()
        self._live_self_turn_key = None
        self._revision += 1
        self._last_visible_window_signature = None
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

        if event.channel == "self":
            return self._apply_self_event(event, now=now)
        if event.channel == "peer":
            return self._apply_peer_event(event, now=now)

        return False

    def _apply_self_event(self, event: OverlayEventUnion, *, now: float) -> bool:
        if isinstance(event, SelfActiveUpdate):
            return self._apply_self_active_update(event, now=now)
        if isinstance(event, SelfActiveClear):
            return self._apply_self_active_clear(event)
        if isinstance(event, SelfTranscriptFinal):
            return self._apply_transcript_final(event, now=now)
        if isinstance(event, (TranslationStreamUpdate, TranslationFinal)):
            return self._apply_translation_update(event, now=now)
        if isinstance(event, UtteranceClosed):
            return self._apply_utterance_closed(event, now=now)
        return False

    def _apply_peer_event(self, event: OverlayEventUnion, *, now: float) -> bool:
        if isinstance(event, PeerTranscriptFinal):
            return self._apply_transcript_final(event, now=now)
        if isinstance(event, (TranslationStreamUpdate, TranslationFinal)):
            return self._apply_translation_update(event, now=now)
        if isinstance(event, UtteranceClosed):
            return self._apply_utterance_closed(event, now=now)
        return False

    def _apply_self_active_update(self, event: SelfActiveUpdate, *, now: float) -> bool:
        if self._should_ignore_terminal_update(event.channel, event.utterance_id):
            return False
        key = self._entry_key(event.channel, event.utterance_id)
        retired_preview_seq = self._retired_preview_self_seqs.get(key)
        if retired_preview_seq is not None and event.seq <= retired_preview_seq:
            self._emit_skip_disposition(
                decision="overlay_turn_superseded",
                disposition="superseded",
                key=key,
                extras={"event_seq": event.seq, "retired_preview_seq": retired_preview_seq},
            )
            return False
        live_self = self._live_self_entry()
        if live_self is not None:
            live_key, live_entry = live_self
            if live_key != key and event.seq < live_entry.last_updated_seq:
                self._emit_skip_disposition(
                    decision="overlay_turn_superseded",
                    disposition="superseded",
                    key=key,
                    entry=live_entry,
                    extras={
                        "event_seq": event.seq,
                        "superseded_by_entry": self._format_entry_key(live_key),
                        "superseded_by_seq": live_entry.last_updated_seq,
                    },
                )
                return False

        entry = self._entry_for(event.channel, event.utterance_id)
        if event.seq < entry.last_updated_seq:
            self._emit_skip_disposition(
                decision="overlay_turn_superseded",
                disposition="superseded",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
            )
            return False

        if live_self is not None and live_self[0] != key:
            self._clear_live_self_pointer(reason="live_self_replaced")

        previous_rendered_translation_text = self._rendered_self_translation_text(entry)

        if (
            self._live_self_turn_key == key
            and entry.live_text == event.text
            and entry.live_secondary_text == event.secondary_text
            and entry.occupant_key == event.occupant_key
            and entry.live_update_id == event.update_id
            and entry.live_origin_wall_clock_ms == event.origin_wall_clock_ms
            and entry.live_session_scope == event.session_scope
            and entry.live_source_text_hash == event.source_text_hash
            and entry.live_source_text_len == event.source_text_len
            and entry.live_logical_turn_key == event.logical_turn_key
        ):
            self._emit_skip_disposition(
                decision="overlay_turn_coalesced",
                disposition="coalesced",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq},
            )
            entry.last_updated_seq = event.seq
            return False

        self._remember_entry_input_seq(entry, event_seq=event.seq)
        if not entry.occupant_key:
            entry.occupant_key = event.occupant_key
        if entry.visible_since is None:
            entry.visible_since = now

        if retired_preview_seq is not None and event.seq > retired_preview_seq:
            self._retired_preview_self_seqs.pop(key, None)
        entry.live_text = event.text
        entry.live_seq = event.seq
        entry.original_seq = event.seq
        entry.live_secondary_text = event.secondary_text
        if event.secondary_text.strip():
            entry.live_update_id = event.update_id
            entry.live_origin_wall_clock_ms = event.origin_wall_clock_ms
            entry.live_session_scope = event.session_scope
            entry.live_source_text_hash = event.source_text_hash
            entry.live_source_text_len = event.source_text_len
            entry.live_logical_turn_key = event.logical_turn_key
            entry.translation_seq = event.seq
        else:
            entry.live_update_id = None
            entry.live_origin_wall_clock_ms = None
            entry.live_session_scope = None
            entry.live_source_text_hash = None
            entry.live_source_text_len = None
            entry.live_logical_turn_key = None
            if not entry.translation_text.strip():
                entry.translation_seq = None
        self._update_self_translation_visibility(
            entry,
            previous_rendered_text=previous_rendered_translation_text,
            next_rendered_text=self._rendered_self_translation_text(entry),
            now=now,
        )
        entry.last_updated_seq = event.seq
        self._live_self_turn_key = key
        return True

    def _apply_self_active_clear(self, event: SelfActiveClear) -> bool:
        live_self = self._live_self_entry()
        if live_self is None:
            return False
        key, entry = live_self
        if event.seq < entry.last_updated_seq:
            self._emit_skip_disposition(
                decision="overlay_turn_superseded",
                disposition="superseded",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
            )
            return False
        if not entry.live_text:
            self._live_self_turn_key = None
            entry.last_updated_seq = event.seq
            self._emit_skip_disposition(
                decision="overlay_turn_coalesced",
                disposition="coalesced",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq},
            )
            return False

        previous_rendered_translation_text = self._rendered_self_translation_text(entry)
        entry.live_text = ""
        entry.live_secondary_text = ""
        entry.live_update_id = None
        entry.live_origin_wall_clock_ms = None
        entry.live_session_scope = None
        entry.live_source_text_hash = None
        entry.live_source_text_len = None
        entry.live_logical_turn_key = None
        entry.live_seq = None
        self._update_self_translation_visibility(
            entry,
            previous_rendered_text=previous_rendered_translation_text,
            next_rendered_text=self._rendered_self_translation_text(entry),
            now=self.clock.now(),
        )
        entry.last_updated_seq = event.seq
        if self._live_self_turn_key == key:
            self._live_self_turn_key = None
        if self._should_retire_preview_only_self_entry(entry):
            self._retire_preview_only_self_entry(
                key,
                entry,
                reason="live_self_cleared",
                now=self.clock.now(),
            )
        return True

    def _apply_transcript_final(
        self,
        event: SelfTranscriptFinal | PeerTranscriptFinal,
        *,
        now: float,
    ) -> bool:
        if self._should_ignore_terminal_update(event.channel, event.utterance_id):
            return False
        key = self._entry_key(event.channel, event.utterance_id)
        entry = self._entry_for(event.channel, event.utterance_id)
        if entry.retained_hidden:
            return False
        if event.seq < entry.last_updated_seq:
            self._emit_skip_disposition(
                decision="overlay_turn_superseded",
                disposition="superseded",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
            )
            return False
        if entry.original_text == event.text and entry.last_updated_seq == event.seq:
            self._emit_skip_disposition(
                decision="overlay_turn_coalesced",
                disposition="coalesced",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq},
            )
            return False

        previous_rendered_translation_text = (
            self._rendered_self_translation_text(entry) if event.channel == "self" else ""
        )
        self._remember_entry_input_seq(entry, event_seq=event.seq)
        entry.original_text = event.text
        entry.original_seq = event.seq
        entry.last_updated_seq = event.seq
        if isinstance(event, SelfTranscriptFinal) and self._live_self_turn_key == key:
            promoted_secondary_text = entry.live_secondary_text.strip()
            if promoted_secondary_text:
                entry.translation_text = promoted_secondary_text
                entry.translation_update_id = entry.live_update_id
                entry.translation_origin_wall_clock_ms = entry.live_origin_wall_clock_ms
                entry.translation_session_scope = entry.live_session_scope
                entry.translation_source_text_hash = entry.live_source_text_hash
                entry.translation_source_text_len = entry.live_source_text_len
                entry.translation_logical_turn_key = entry.live_logical_turn_key
                if entry.live_seq is not None:
                    entry.translation_seq = entry.live_seq
                if self.show_translation:
                    if entry.translation_visible_since is None:
                        entry.translation_visible_since = now
                    if entry.translation_observed_visible_since is None:
                        entry.translation_observed_visible_since = now
            entry.live_text = ""
            entry.live_secondary_text = ""
            entry.live_update_id = None
            entry.live_origin_wall_clock_ms = None
            entry.live_session_scope = None
            entry.live_source_text_hash = None
            entry.live_source_text_len = None
            entry.live_logical_turn_key = None
            entry.live_seq = None
            self._live_self_turn_key = None
        if event.channel == "self":
            self._update_self_translation_visibility(
                entry,
                previous_rendered_text=previous_rendered_translation_text,
                next_rendered_text=self._rendered_self_translation_text(entry),
                now=now,
            )
        if event.channel == "self":
            self._retired_preview_self_seqs.pop(key, None)
        self._refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=event.seq,
        )
        return True

    def _apply_translation_update(
        self,
        event: TranslationStreamUpdate | TranslationFinal,
        *,
        now: float,
    ) -> bool:
        if self._should_ignore_terminal_update(event.channel, event.utterance_id):
            return False
        key = self._entry_key(event.channel, event.utterance_id)
        entry = self._entry_for(event.channel, event.utterance_id)
        if event.seq < entry.last_updated_seq:
            self._emit_skip_disposition(
                decision="overlay_turn_superseded",
                disposition="superseded",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
            )
            return False
        if entry.translation_text == event.text and entry.last_updated_seq == event.seq:
            self._emit_skip_disposition(
                decision="overlay_turn_coalesced",
                disposition="coalesced",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq},
            )
            return False
        previous_rendered_translation_text = (
            self._rendered_self_translation_text(entry) if event.channel == "self" else ""
        )
        self._remember_entry_input_seq(entry, event_seq=event.seq)
        if entry.retained_hidden and event.channel == "self" and event.text.strip():
            entry.retained_hidden = False
            entry.window_evicted_at = None
        if not entry.retained_hidden and event.channel != "self":
            entry.translation_visible_since = self._next_translation_visible_since(
                previous_text=entry.translation_text,
                next_text=event.text,
                previous_visible_since=entry.translation_visible_since,
                now=now,
            )
        entry.translation_text = event.text
        if event.text.strip():
            entry.translation_update_id = event.update_id
            entry.translation_origin_wall_clock_ms = event.origin_wall_clock_ms
            entry.translation_session_scope = event.session_scope
            entry.translation_source_text_hash = event.source_text_hash
            entry.translation_source_text_len = event.source_text_len
            entry.translation_logical_turn_key = event.logical_turn_key
            entry.translation_seq = event.seq
        else:
            entry.translation_update_id = None
            entry.translation_origin_wall_clock_ms = None
            entry.translation_session_scope = None
            entry.translation_source_text_hash = None
            entry.translation_source_text_len = None
            entry.translation_logical_turn_key = None
            if not entry.live_secondary_text.strip():
                entry.translation_seq = None
        if not entry.retained_hidden and event.channel == "self":
            self._update_self_translation_visibility(
                entry,
                previous_rendered_text=previous_rendered_translation_text,
                next_rendered_text=self._rendered_self_translation_text(entry),
                now=now,
            )
        elif event.text.strip() and entry.translation_observed_visible_since is None:
            entry.translation_observed_visible_since = now
        entry.last_updated_seq = event.seq
        if event.channel == "self":
            self._retired_preview_self_seqs.pop(key, None)
        self._refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=event.seq,
        )
        return True

    def _apply_utterance_closed(self, event: UtteranceClosed, *, now: float) -> bool:
        key = self._entry_key(event.channel, event.utterance_id)
        if key in self._scene_terminal_keys or key in self._terminal_registry:
            return False
        entry = self._entries.get(key)
        if entry is None:
            return False
        if event.seq < entry.last_updated_seq:
            self._emit_skip_disposition(
                decision="overlay_turn_superseded",
                disposition="superseded",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
            )
            return False
        if entry.closed_seq == event.seq:
            self._emit_skip_disposition(
                decision="overlay_turn_coalesced",
                disposition="coalesced",
                key=key,
                entry=entry,
                extras={"event_seq": event.seq},
            )
            return False
        entry.closed_seq = event.seq
        entry.closed_at = now
        entry.last_updated_seq = event.seq
        self._schedule_expiration(key, entry)
        return True

    def _entry_for(self, channel: str | None, utterance_id: UUID | None) -> _LogicalTurnEntry:
        key = self._entry_key(channel, utterance_id)
        entry = self._entries.get(key)
        if entry is None:
            entry = _LogicalTurnEntry(channel=key[0], utterance_id=key[1])
            self._entries[key] = entry
        return entry

    def _entry_key(self, channel: str | None, utterance_id: UUID | None) -> tuple[str, UUID]:
        if channel not in ("self", "peer"):
            raise ValueError(f"invalid overlay channel: {channel!r}")
        if utterance_id is None:
            raise ValueError("overlay presenter requires utterance_id for finalized entries")
        return (channel, utterance_id)

    def _is_tombstoned(self, channel: str | None, utterance_id: UUID | None) -> bool:
        key = self._entry_key(channel, utterance_id)
        return key in self._scene_terminal_keys or key in self._terminal_registry

    def _live_self_entry(self) -> tuple[tuple[str, UUID], _LogicalTurnEntry] | None:
        if self._live_self_turn_key is None:
            return None
        entry = self._entries.get(self._live_self_turn_key)
        if entry is None:
            self._live_self_turn_key = None
            return None
        return self._live_self_turn_key, entry

    def _clear_live_self_pointer(self, *, reason: str) -> None:
        live_self = self._live_self_entry()
        if live_self is None:
            return
        key, entry = live_self
        previous_rendered_translation_text = self._rendered_self_translation_text(entry)
        entry.live_text = ""
        entry.live_secondary_text = ""
        entry.live_update_id = None
        entry.live_origin_wall_clock_ms = None
        entry.live_session_scope = None
        entry.live_source_text_hash = None
        entry.live_source_text_len = None
        entry.live_logical_turn_key = None
        entry.live_seq = None
        self._live_self_turn_key = None
        self._update_self_translation_visibility(
            entry,
            previous_rendered_text=previous_rendered_translation_text,
            next_rendered_text=self._rendered_self_translation_text(entry),
            now=self.clock.now(),
        )
        if self._should_retire_preview_only_self_entry(entry):
            self._retire_preview_only_self_entry(key, entry, reason=reason, now=self.clock.now())

    def _should_retire_preview_only_self_entry(self, entry: _LogicalTurnEntry) -> bool:
        return (
            entry.channel == "self"
            and not entry.original_text.strip()
            and not entry.translation_text.strip()
        )

    def _retire_preview_only_self_entry(
        self,
        key: tuple[str, UUID],
        entry: _LogicalTurnEntry,
        *,
        reason: str,
        now: float,
    ) -> None:
        self._remember_retired_preview_self_seq(key, entry.last_updated_seq)
        self._remove_entry(key, reason=reason, now=now)

    async def _publish_if_changed(self) -> None:
        now = self.clock.now()
        self._expire_closed_entries(now=now)
        rendered_entries = self._visible_block_entries(now=now)
        next_blocks = [block for _, block in rendered_entries]
        next_calibration = _calibration_from_overlay(self.calibration)
        previous_rendered_signature = self._rendered_blocks_signature(self._snapshot.blocks)
        next_rendered_signature = self._rendered_blocks_signature(next_blocks)
        previous_signatures = {
            block.id: self._rendered_block_signature(block) for block in self._snapshot.blocks
        }
        self._refresh_visible_expiration_deadlines(
            rendered_entries,
            previous_blocks=self._snapshot.blocks,
            now=now,
        )
        if (
            next_rendered_signature == previous_rendered_signature
            and next_calibration == self._snapshot.calibration
        ):
            self._emit_turn_decision(
                "overlay_turn_no_visible_change",
                disposition="rendered_signature_unchanged",
                extras={"block_count": len(next_blocks)},
            )
            return

        for key, block in rendered_entries:
            entry = self._entries.get(key)
            if entry is None:
                continue
            previous_signature = previous_signatures.get(block.id)
            if previous_signature is None:
                self._emit_turn_decision(
                    "overlay_turn_first_visible",
                    key=key,
                    entry=entry,
                    block=block,
                )
                self._emit_pair_state(key, entry, block, publish_kind="first_visible")
                continue
            if previous_signature != self._rendered_block_signature(block):
                self._emit_turn_decision(
                    "overlay_turn_updated",
                    key=key,
                    entry=entry,
                    block=block,
                )
                self._emit_pair_state(key, entry, block, publish_kind="visible_update")

        self._revision += 1
        self._snapshot = OverlayPresentationSnapshot(
            revision=self._revision,
            calibration=next_calibration,
            blocks=next_blocks,
        )
        blocks_summary = [
            {
                "id": block.id,
                "variant": block.block_variant,
                "primary_len": len(block.primary_text),
                "secondary_len": len(block.secondary_text),
            }
            for block in next_blocks
        ]
        self._emit_detailed_lazy(
            lambda: "[OverlayPresenter] Snapshot publish: revision=%s block_count=%s bridge_attached=%s blocks=%s"
            % (
                self._snapshot.revision,
                len(next_blocks),
                self.bridge is not None,
                blocks_summary,
            )
        )
        if self.diagnostics is not None:
            self.diagnostics.record_presenter(
                "snapshot_publish",
                revision=self._snapshot.revision,
                block_count=len(next_blocks),
                bridge_attached=self.bridge is not None,
                blocks=blocks_summary,
            )
        if self.bridge is not None:
            await self.bridge.replace_snapshot(self._snapshot)

    def _visible_block_entries(
        self,
        *,
        now: float,
    ) -> list[tuple[tuple[str, UUID], OverlayPresentationBlock]]:
        self._expire_closed_entries(now=now)
        live_self = self._live_self_entry()
        active_self_key = live_self[0] if live_self is not None and live_self[1].live_text else None
        active_self_present = active_self_key is not None
        finalized_limit = self.visible_window_target_blocks
        if active_self_present:
            finalized_limit = max(finalized_limit - 1, 0)
        visible_entry_keys, candidate_keys = self._logical_visible_entry_keys(
            now=now,
            finalized_limit=finalized_limit,
            excluded_key=active_self_key,
        )
        self._mark_entries_visible(visible_entry_keys)
        self._prune_displaced_finalized_entries(
            set(visible_entry_keys),
            candidate_keys=candidate_keys,
        )
        self._record_visible_window_selection(
            active_self_present=active_self_present,
            finalized_limit=finalized_limit,
            candidate_keys=candidate_keys,
            selected_keys=visible_entry_keys,
            protected_selected=[],
            retained_hidden=[],
        )
        rendered_entries = [
            (key, block)
            for key in visible_entry_keys
            if (entry := self._entries.get(key)) is not None
            and (block := self._build_presentation_block(entry)) is not None
        ]
        if active_self_key is not None:
            active_entry = self._entries.get(active_self_key)
            if (
                active_entry is not None
                and (block := self._build_presentation_block(active_entry, prefer_live_self=True))
                is not None
            ):
                active_entry.ever_visible = True
                rendered_entries.append((active_self_key, block))
        rendered_entries.sort(key=lambda item: (item[1].appearance_seq, item[1].occupant_key))
        return rendered_entries

    def _visible_blocks(self) -> list[OverlayPresentationBlock]:
        return [block for _, block in self._visible_block_entries(now=self.clock.now())]

    def _refresh_visible_expiration_deadlines(
        self,
        rendered_entries: list[tuple[tuple[str, UUID], OverlayPresentationBlock]],
        *,
        previous_blocks: list[OverlayPresentationBlock],
        now: float,
    ) -> None:
        previous_signatures = {
            block.id: self._visible_block_content_signature(block) for block in previous_blocks
        }
        for key, block in rendered_entries:
            if previous_signatures.get(block.id) == self._visible_block_content_signature(block):
                continue
            entry = self._entries.get(key)
            if entry is None:
                continue
            entry.ever_visible = True
            if entry.visible_since is None:
                entry.visible_since = now
            entry.last_meaningful_visible_at = now
            self._schedule_expiration(key, entry)

    def _visible_block_content_signature(
        self,
        block: OverlayPresentationBlock,
    ) -> tuple[str, str, str, bool]:
        secondary_text = block.secondary_text if block.secondary_enabled else ""
        return (
            block.block_variant,
            block.primary_text,
            secondary_text,
            block.secondary_enabled,
        )

    def _rendered_block_signature(
        self,
        block: OverlayPresentationBlock,
    ) -> tuple[
        str,
        str,
        int,
        str,
        str,
        str,
        str,
        bool,
        str | None,
        int | None,
        str | None,
        str | None,
        int | None,
        str | None,
    ]:
        secondary_text = block.secondary_text if block.secondary_enabled else ""
        include_translation_metadata = block.channel == "peer" or bool(secondary_text)
        return (
            block.id,
            block.occupant_key,
            block.appearance_seq,
            block.channel,
            block.block_variant,
            block.primary_text,
            secondary_text,
            block.secondary_enabled,
            block.update_id if include_translation_metadata else None,
            block.origin_wall_clock_ms if include_translation_metadata else None,
            block.session_scope if include_translation_metadata else None,
            block.source_text_hash if include_translation_metadata else None,
            block.source_text_len if include_translation_metadata else None,
            block.logical_turn_key if include_translation_metadata else None,
        )

    def _rendered_blocks_signature(
        self,
        blocks: list[OverlayPresentationBlock],
    ) -> tuple[object, ...]:
        return tuple(self._rendered_block_signature(block) for block in blocks)

    def _logical_visible_entry_keys(
        self,
        *,
        now: float,
        finalized_limit: int,
        excluded_key: tuple[str, UUID] | None,
    ) -> tuple[list[tuple[str, UUID]], list[tuple[str, UUID]]]:
        _ = now
        if finalized_limit == 0:
            return [], []

        publishable: list[tuple[int, int, str, str, tuple[str, UUID]]] = []
        for key, entry in self._entries.items():
            if excluded_key is not None and key == excluded_key:
                continue
            if not self._entry_is_selectable(entry):
                continue
            self._ensure_entry_visibility_metadata(
                entry,
                occupant_key=self._finalized_occupant_key(entry.channel, entry.utterance_id),
            )
            if entry.publishable_seq is None or entry.appearance_seq is None:
                continue
            publishable.append(
                (
                    entry.publishable_seq,
                    entry.appearance_seq,
                    entry.occupant_key,
                    self._format_entry_key(key),
                    key,
                )
            )

        display_order = sorted(publishable, key=lambda item: (item[1], item[2], item[3]))
        selected_candidates = sorted(
            publishable, key=lambda item: (item[0], item[1], item[2], item[3])
        )[-finalized_limit:]
        selected_set = {key for *_, key in selected_candidates}
        selected = [key for *_, key in display_order if key in selected_set]
        return selected, [key for *_, key in display_order]

    def _build_presentation_block(
        self,
        entry: _LogicalTurnEntry,
        *,
        prefer_live_self: bool = False,
    ) -> OverlayPresentationBlock | None:
        if prefer_live_self and entry.channel == "self":
            primary_text = entry.live_text.strip()
            if not primary_text:
                return None
            live_secondary_text = entry.live_secondary_text.strip()
            secondary_text = live_secondary_text or entry.translation_text.strip()
            if live_secondary_text:
                update_id = entry.live_update_id
                origin_wall_clock_ms = entry.live_origin_wall_clock_ms
                session_scope = entry.live_session_scope
                source_text_hash = entry.live_source_text_hash
                source_text_len = entry.live_source_text_len
                logical_turn_key = entry.live_logical_turn_key
            else:
                update_id = entry.translation_update_id
                origin_wall_clock_ms = entry.translation_origin_wall_clock_ms
                session_scope = entry.translation_session_scope
                source_text_hash = entry.translation_source_text_hash
                source_text_len = entry.translation_source_text_len
                logical_turn_key = entry.translation_logical_turn_key
            return OverlayPresentationBlock(
                id=entry.block_id,
                occupant_key=entry.occupant_key,
                appearance_seq=self._block_appearance_seq(entry),
                channel="self",
                block_variant="active_self",
                primary_text=primary_text,
                secondary_text=secondary_text,
                secondary_enabled=self.show_translation,
                update_id=update_id,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            )

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
            update_id=entry.translation_update_id,
            origin_wall_clock_ms=entry.translation_origin_wall_clock_ms,
            session_scope=entry.translation_session_scope,
            source_text_hash=entry.translation_source_text_hash,
            source_text_len=entry.translation_source_text_len,
            logical_turn_key=entry.translation_logical_turn_key,
        )

    def _prune_displaced_finalized_entries(
        self,
        visible_entry_keys: set[tuple[str, UUID]],
        *,
        candidate_keys: list[tuple[str, UUID]],
    ) -> None:
        displaced_keys = [
            key
            for key in candidate_keys
            if (entry := self._entries.get(key)) is not None
            and self._entry_is_selectable(entry)
            and key not in visible_entry_keys
        ]
        for key in displaced_keys:
            entry = self._entries.get(key)
            if entry is None:
                continue
            self._remove_entry(
                key,
                reason="evicted_by_newer_turn",
                now=self.clock.now(),
                tombstone_seq=entry.last_updated_seq,
            )

    def _entry_is_publishable(self, entry: _LogicalTurnEntry) -> bool:
        if entry.channel == "peer":
            return bool(entry.translation_text.strip())
        return bool(entry.original_text.strip())

    def _entry_is_selectable(self, entry: _LogicalTurnEntry) -> bool:
        return self._entry_is_publishable(entry) and not entry.retained_hidden

    def _mark_entries_visible(self, visible_entry_keys: list[tuple[str, UUID]]) -> None:
        for key in visible_entry_keys:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.retained_hidden:
                    entry.retained_hidden = False
                    entry.window_evicted_at = None
                    self._schedule_expiration(key, entry)
                entry.ever_visible = True

    def _should_retain_hidden_self_entry(
        self,
        entry: _LogicalTurnEntry,
    ) -> bool:
        return entry.channel == "self" and not entry.translation_text.strip()

    def _entry_has_translation_protection(
        self,
        entry: _LogicalTurnEntry,
        *,
        now: float,
    ) -> bool:
        if entry.channel != "self" or entry.translation_visible_since is None:
            return False
        return now < (entry.translation_visible_since + SELF_TRANSLATION_MIN_VISIBLE_SECONDS)

    def _refresh_entry_visibility_and_expiration(
        self,
        key: tuple[str, UUID],
        entry: _LogicalTurnEntry,
        *,
        now: float,
        publishable_seq: int | None = None,
    ) -> None:
        if self._entry_is_publishable(entry):
            self._ensure_entry_visibility_metadata(
                entry,
                occupant_key=self._finalized_occupant_key(entry.channel, entry.utterance_id),
                publishable_seq=publishable_seq,
            )
            entry.ever_publishable = True
            if entry.visible_since is None:
                entry.visible_since = now
        else:
            self._emit_turn_decision(
                "overlay_turn_not_yet_publishable",
                key=key,
                entry=entry,
            )
        if entry.closed_seq is not None or entry.retained_hidden:
            self._schedule_expiration(key, entry)

    def _finalized_occupant_key(self, channel: str, utterance_id: UUID) -> str:
        return f"{channel}:{utterance_id}"

    def _next_appearance_seq(self) -> int:
        self._appearance_seq += 1
        return self._appearance_seq

    def _ensure_entry_visibility_metadata(
        self,
        entry: _LogicalTurnEntry,
        *,
        occupant_key: str,
        publishable_seq: int | None = None,
    ) -> None:
        if not entry.occupant_key:
            entry.occupant_key = occupant_key
        if entry.appearance_seq is None:
            if entry.first_input_seq is not None:
                entry.appearance_seq = entry.first_input_seq
            elif publishable_seq is not None:
                entry.appearance_seq = publishable_seq
            else:
                entry.appearance_seq = self._next_appearance_seq()
        if entry.publishable_seq is None:
            if publishable_seq is not None:
                entry.publishable_seq = publishable_seq
            elif entry.last_updated_seq > 0:
                entry.publishable_seq = entry.last_updated_seq

    def _remember_entry_input_seq(self, entry: _LogicalTurnEntry, *, event_seq: int) -> None:
        if entry.first_input_seq is None:
            entry.first_input_seq = event_seq

    def _block_appearance_seq(self, entry: _LogicalTurnEntry) -> int:
        if entry.appearance_seq is not None:
            return entry.appearance_seq
        if entry.first_input_seq is not None:
            return entry.first_input_seq
        if entry.last_updated_seq > 0:
            return entry.last_updated_seq
        return 0

    def _next_translation_visible_since(
        self,
        *,
        previous_text: str,
        next_text: str,
        previous_visible_since: float | None,
        now: float,
    ) -> float | None:
        next_clean = next_text.strip()
        if not next_clean:
            return None
        if previous_text.strip() != next_clean:
            return now
        return previous_visible_since

    def _remember_tombstone(self, key: tuple[str, UUID], closed_seq: int) -> None:
        self._terminal_registry.pop(key, None)
        self._terminal_registry[key] = closed_seq
        while len(self._terminal_registry) > _CLOSED_TOMBSTONE_LIMIT:
            self._terminal_registry.popitem(last=False)

    def _remember_retired_preview_self_seq(self, key: tuple[str, UUID], retired_seq: int) -> None:
        self._retired_preview_self_seqs.pop(key, None)
        self._retired_preview_self_seqs[key] = retired_seq
        while len(self._retired_preview_self_seqs) > _CLOSED_TOMBSTONE_LIMIT:
            self._retired_preview_self_seqs.popitem(last=False)

    def _retain_hidden_entry(
        self,
        key: tuple[str, UUID],
        entry: _LogicalTurnEntry,
        *,
        now: float,
    ) -> None:
        if entry.retained_hidden:
            return
        entry.retained_hidden = True
        entry.window_evicted_at = now
        self._schedule_expiration(key, entry)
        if self.diagnostics is not None:
            self.diagnostics.record_presenter(
                "entry_retained_hidden",
                entry_key=self._format_entry_key(key),
                appearance_seq=entry.appearance_seq,
                channel=entry.channel,
                primary_len=len(entry.original_text.strip()),
                secondary_len=len(entry.translation_text.strip()),
                visible_since=entry.visible_since,
                translation_visible_since=entry.translation_visible_since,
                closed_at=entry.closed_at,
                window_evicted_at=entry.window_evicted_at,
            )

    def _schedule_expiration(
        self,
        key: tuple[str, UUID],
        entry: _LogicalTurnEntry,
    ) -> None:
        self._cancel_expiration_task(key)
        if self._entry_expiration_deadline(entry) is None:
            return
        entry.expiration_revision += 1
        self._record_deadline(entry)
        self._expiration_tasks[key] = asyncio.create_task(
            self._expire_entry_after_ttl(key, entry.expiration_revision)
        )

    async def _expire_entry_after_ttl(
        self, key: tuple[str, UUID], expiration_revision: int
    ) -> None:
        try:
            while True:
                entry = self._entries.get(key)
                if entry is None or entry.expiration_revision != expiration_revision:
                    return

                deadline = self._entry_expiration_deadline(entry)
                if deadline is None:
                    return
                remaining = deadline - self.clock.now()
                if remaining > 0:
                    await self.sleep(remaining)
                    continue

                self._remove_entry(
                    key,
                    reason="expired",
                    now=self.clock.now(),
                    current_task=self._current_task(),
                    tombstone_seq=entry.last_updated_seq if entry.closed_seq is None else None,
                )
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
            entry = self._entries.get(key)
            if entry is None:
                continue
            self._remove_entry(
                key,
                reason="expired",
                now=now,
                current_task=current_task,
                tombstone_seq=entry.last_updated_seq if entry.closed_seq is None else None,
            )

    def _entry_expiration_deadline(self, entry: _LogicalTurnEntry) -> float | None:
        return self._entry_expiration_components(entry)[0]

    def _entry_expiration_components(
        self,
        entry: _LogicalTurnEntry,
    ) -> tuple[float | None, float | None, float | None]:
        hidden_deadline = (
            entry.window_evicted_at + LATE_ARRIVAL_WINDOW_SECONDS
            if entry.retained_hidden and entry.window_evicted_at is not None
            else None
        )
        visible_anchor = entry.last_meaningful_visible_at
        if visible_anchor is None and entry.visible_since is not None:
            visible_anchor = entry.visible_since

        visible_deadline: float | None = None
        if visible_anchor is not None:
            visible_deadline = visible_anchor + VISIBLE_TTL_SECONDS
        elif entry.closed_at is not None:
            visible_deadline = entry.closed_at + LATE_ARRIVAL_WINDOW_SECONDS

        translation_deadline: float | None = None
        if (
            entry.channel == "self"
            and self.show_translation
            and entry.translation_visible_since is not None
        ):
            translation_deadline = (
                entry.translation_visible_since + SELF_TRANSLATION_MIN_VISIBLE_SECONDS
            )
        effective_deadline = visible_deadline
        if translation_deadline is not None:
            if effective_deadline is None:
                effective_deadline = translation_deadline
            else:
                effective_deadline = max(effective_deadline, translation_deadline)
        if hidden_deadline is not None:
            if effective_deadline is None:
                effective_deadline = hidden_deadline
            else:
                effective_deadline = min(effective_deadline, hidden_deadline)
        return effective_deadline, visible_deadline, translation_deadline

    def _remove_entry(
        self,
        key: tuple[str, UUID],
        *,
        reason: str,
        now: float | None = None,
        current_task: asyncio.Task[None] | None = None,
        tombstone_seq: int | None = None,
    ) -> None:
        if self._expiration_tasks.get(key) is not current_task:
            self._cancel_expiration_task(key)
        if self._live_self_turn_key == key:
            self._live_self_turn_key = None
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        effective_deadline, visible_deadline, translation_deadline = (
            self._entry_expiration_components(entry)
        )
        removal_time = now if now is not None else self.clock.now()
        extra_fields: dict[str, object] = {}
        if entry.channel == "self":
            lifetime_ms = 0.0
            if entry.visible_since is not None:
                lifetime_ms = max(0.0, (removal_time - entry.visible_since) * 1000.0)
            translated_lifetime_ms = 0.0
            if entry.translation_observed_visible_since is not None:
                translated_lifetime_ms = max(
                    0.0,
                    (removal_time - entry.translation_observed_visible_since) * 1000.0,
                )
            extra_fields = {
                "lifetime_ms": lifetime_ms,
                "translated_lifetime_ms": translated_lifetime_ms,
                "had_translation": bool(entry.translation_text.strip()),
                "ever_visible_with_translation": entry.translation_observed_visible_since
                is not None,
                "translation_observed_visible_since": entry.translation_observed_visible_since,
            }
        if self.diagnostics is not None:
            self.diagnostics.record_presenter_removal(
                reason=reason,
                entry_key=self._format_entry_key(key),
                appearance_seq=entry.appearance_seq,
                channel=entry.channel,
                primary_len=len(entry.original_text.strip()),
                secondary_len=len(entry.translation_text.strip()),
                visible_since=entry.visible_since,
                translation_visible_since=entry.translation_visible_since,
                closed_at=entry.closed_at,
                now=removal_time,
                visible_deadline=visible_deadline,
                translation_deadline=translation_deadline,
                effective_deadline=effective_deadline,
                **extra_fields,
            )
        seq = tombstone_seq if tombstone_seq is not None else entry.closed_seq
        if reason == "expired" and entry.ever_visible:
            self._remember_scene_terminal_reason(key, reason=reason)
            self._emit_turn_decision(
                "overlay_turn_hidden_idle_ttl",
                disposition="hidden_idle_ttl",
                key=key,
                entry=entry,
                extras={"deadline": effective_deadline},
            )
        if reason == "evicted_by_newer_turn":
            self._remember_scene_terminal_reason(key, reason=reason)
            self._emit_turn_decision(
                "overlay_turn_evicted_by_newer_turn",
                disposition="evicted",
                key=key,
                entry=entry,
            )
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

    def _clear_entries_for_reason(self, reason: str) -> None:
        for key in list(self._entries):
            self._remove_entry(key, reason=reason, now=self.clock.now())

    def _record_visible_window_selection(
        self,
        *,
        active_self_present: bool,
        finalized_limit: int,
        candidate_keys: list[tuple[str, UUID]],
        selected_keys: list[tuple[str, UUID]],
        protected_selected: list[tuple[str, UUID]],
        retained_hidden: list[str],
    ) -> None:
        if self.diagnostics is None:
            return
        candidate_labels = [self._format_entry_key(key) for key in candidate_keys]
        selected_labels = [self._format_entry_key(key) for key in selected_keys]
        dropped_labels = [label for label in candidate_labels if label not in selected_labels]
        protected_labels = [self._format_entry_key(key) for key in protected_selected]
        signature = (
            active_self_present,
            finalized_limit,
            tuple(candidate_labels),
            tuple(selected_labels),
            tuple(dropped_labels),
            tuple(protected_labels),
            tuple(retained_hidden),
        )
        if signature == self._last_visible_window_signature:
            return
        self._last_visible_window_signature = signature
        self.diagnostics.record_presenter(
            "visible_window",
            active_self_present=active_self_present,
            finalized_limit=finalized_limit,
            candidate_keys=candidate_labels,
            selected_keys=selected_labels,
            dropped_keys=dropped_labels,
            protected_selected=protected_labels,
            retained_hidden=retained_hidden,
        )

    def _retained_hidden_labels(self) -> list[str]:
        return [
            self._format_entry_key(key)
            for key, entry in self._entries.items()
            if entry.retained_hidden
        ]

    def _record_deadline(self, entry: _LogicalTurnEntry) -> None:
        if self.diagnostics is None:
            return
        effective_deadline, visible_deadline, translation_deadline = (
            self._entry_expiration_components(entry)
        )
        self.diagnostics.record_presenter(
            "deadline_scheduled",
            entry_key=self._format_entry_key((entry.channel, entry.utterance_id)),
            channel=entry.channel,
            visible_since=entry.visible_since,
            translation_visible_since=entry.translation_visible_since,
            closed_at=entry.closed_at,
            visible_deadline=visible_deadline,
            translation_deadline=translation_deadline,
            effective_deadline=effective_deadline,
        )

    def _format_entry_key(self, key: tuple[str, UUID]) -> str:
        return f"{key[0]}:{key[1]}"


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
