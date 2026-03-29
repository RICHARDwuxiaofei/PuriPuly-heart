from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal
from uuid import UUID

ChannelId = Literal["self", "peer"]
AppliedContextMode = Literal["local", "integrated"]
OverlayContentKind = Literal["original", "translation"]
OVERLAY_ROW_IDENTITY_RULE = "channel+utterance_id+content_kind"


def overlay_row_key(
    channel: ChannelId, utterance_id: UUID, content_kind: OverlayContentKind
) -> str:
    return f"{channel}:{utterance_id}:{content_kind}"


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayEvent:
    event_id: str
    seq: int
    utterance_id: UUID | None
    channel: ChannelId | None
    created_at: float

    EVENT_TYPE: ClassVar[str] = "overlay_event"

    @property
    def type(self) -> str:
        return self.EVENT_TYPE

    @property
    def content_kind(self) -> OverlayContentKind | None:
        return None

    @property
    def row_key(self) -> str | None:
        if self.channel is None or self.utterance_id is None or self.content_kind is None:
            return None
        return overlay_row_key(self.channel, self.utterance_id, self.content_kind)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.type,
            "event_id": self.event_id,
            "seq": self.seq,
            "utterance_id": str(self.utterance_id) if self.utterance_id is not None else None,
            "channel": self.channel,
            "created_at": self.created_at,
        }
        payload.update(self._extra_dict())
        return payload

    def _extra_dict(self) -> dict[str, object]:
        return {}


@dataclass(frozen=True, slots=True, kw_only=True)
class _TranscriptEvent(OverlayEvent):
    text: str
    source_language: str
    target_language: str
    is_final: bool = True
    speaker_label: str | None = None
    peer_epoch: int | None = None

    def _extra_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "is_final": self.is_final,
            "speaker_label": self.speaker_label,
            "peer_epoch": self.peer_epoch,
        }

    @property
    def content_kind(self) -> OverlayContentKind:
        return "original"


@dataclass(frozen=True, slots=True, kw_only=True)
class SelfTranscriptFinal(_TranscriptEvent):
    EVENT_TYPE: ClassVar[str] = "self_transcript_final"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfTranscriptFinal requires channel='self'")


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerTranscriptFinal(_TranscriptEvent):
    EVENT_TYPE: ClassVar[str] = "peer_transcript_final"

    def __post_init__(self) -> None:
        if self.channel != "peer":
            raise ValueError("PeerTranscriptFinal requires channel='peer'")


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationStreamUpdate(OverlayEvent):
    text: str
    source_language: str
    target_language: str
    is_final: bool = False
    applied_context_mode: AppliedContextMode | None = None
    speaker_label: str | None = None
    peer_epoch: int | None = None

    EVENT_TYPE: ClassVar[str] = "translation_stream_update"

    def _extra_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "is_final": self.is_final,
            "applied_context_mode": self.applied_context_mode,
            "speaker_label": self.speaker_label,
            "peer_epoch": self.peer_epoch,
        }

    @property
    def content_kind(self) -> OverlayContentKind:
        return "translation"


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationFinal(TranslationStreamUpdate):
    is_final: bool = True

    EVENT_TYPE: ClassVar[str] = "translation_final"

    def __post_init__(self) -> None:
        if not self.is_final:
            raise ValueError("TranslationFinal requires is_final=True")


@dataclass(frozen=True, slots=True, kw_only=True)
class UtteranceClosed(OverlayEvent):
    is_final: bool = True

    EVENT_TYPE: ClassVar[str] = "utterance_closed"

    def _extra_dict(self) -> dict[str, object]:
        return {"is_final": self.is_final}


@dataclass(frozen=True, slots=True, kw_only=True)
class Shutdown(OverlayEvent):
    utterance_id: UUID | None = None
    channel: ChannelId | None = None

    EVENT_TYPE: ClassVar[str] = "shutdown"


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayCalibrationUpdate(OverlayEvent):
    utterance_id: UUID | None = None
    channel: ChannelId | None = None
    anchor: str
    offset_x: float
    offset_y: float
    distance: float
    text_scale: float
    background_alpha: float

    EVENT_TYPE: ClassVar[str] = "overlay_calibration_update"

    def _extra_dict(self) -> dict[str, object]:
        return {
            "anchor": self.anchor,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "distance": self.distance,
            "text_scale": self.text_scale,
            "background_alpha": self.background_alpha,
        }


OverlayEventUnion = (
    SelfTranscriptFinal
    | PeerTranscriptFinal
    | TranslationStreamUpdate
    | TranslationFinal
    | UtteranceClosed
    | Shutdown
    | OverlayCalibrationUpdate
)

_EVENT_TYPES: dict[str, type[OverlayEvent]] = {
    SelfTranscriptFinal.EVENT_TYPE: SelfTranscriptFinal,
    PeerTranscriptFinal.EVENT_TYPE: PeerTranscriptFinal,
    TranslationStreamUpdate.EVENT_TYPE: TranslationStreamUpdate,
    TranslationFinal.EVENT_TYPE: TranslationFinal,
    UtteranceClosed.EVENT_TYPE: UtteranceClosed,
    Shutdown.EVENT_TYPE: Shutdown,
    OverlayCalibrationUpdate.EVENT_TYPE: OverlayCalibrationUpdate,
}


def _parse_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return UUID(str(value))


def _parse_channel(value: object) -> ChannelId | None:
    if value in ("self", "peer"):
        return value
    if value is None:
        return None
    raise ValueError(f"invalid overlay channel: {value!r}")


def overlay_event_from_dict(data: dict[str, object]) -> OverlayEventUnion:
    event_type = data.get("type")
    if not isinstance(event_type, str):
        raise ValueError("overlay event payload is missing type")

    cls = _EVENT_TYPES.get(event_type)
    if cls is None:
        raise ValueError(f"unsupported overlay event type: {event_type}")

    common = {
        "event_id": str(data["event_id"]),
        "seq": int(data["seq"]),
        "utterance_id": _parse_uuid(data.get("utterance_id")),
        "channel": _parse_channel(data.get("channel")),
        "created_at": float(data["created_at"]),
    }

    if cls in (SelfTranscriptFinal, PeerTranscriptFinal):
        return cls(
            **common,
            text=str(data["text"]),
            source_language=str(data["source_language"]),
            target_language=str(data["target_language"]),
            is_final=bool(data.get("is_final", True)),
            speaker_label=(
                str(data["speaker_label"]) if data.get("speaker_label") is not None else None
            ),
            peer_epoch=int(data["peer_epoch"]) if data.get("peer_epoch") is not None else None,
        )

    if cls in (TranslationStreamUpdate, TranslationFinal):
        return cls(
            **common,
            text=str(data["text"]),
            source_language=str(data["source_language"]),
            target_language=str(data["target_language"]),
            is_final=bool(data.get("is_final", cls is TranslationFinal)),
            applied_context_mode=data.get("applied_context_mode"),  # type: ignore[arg-type]
            speaker_label=(
                str(data["speaker_label"]) if data.get("speaker_label") is not None else None
            ),
            peer_epoch=int(data["peer_epoch"]) if data.get("peer_epoch") is not None else None,
        )

    if cls is UtteranceClosed:
        return cls(
            **common,
            is_final=bool(data.get("is_final", True)),
        )

    if cls is OverlayCalibrationUpdate:
        return cls(
            **common,
            anchor=str(data["anchor"]),
            offset_x=float(data["offset_x"]),
            offset_y=float(data["offset_y"]),
            distance=float(data["distance"]),
            text_scale=float(data["text_scale"]),
            background_alpha=float(data["background_alpha"]),
        )

    return cls(**common)


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayStateSnapshot:
    events: list[OverlayEventUnion]

    def to_dict(self) -> dict[str, object]:
        return {"events": [event.to_dict() for event in self.events]}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayStateSnapshot":
        raw_events = data.get("events", [])
        if not isinstance(raw_events, list):
            raise ValueError("overlay state snapshot events must be a list")
        events: list[OverlayEventUnion] = []
        for event in raw_events:
            if not isinstance(event, dict):
                raise ValueError("overlay state snapshot events must contain only dict items")
            events.append(overlay_event_from_dict(event))
        return cls(events=events)
