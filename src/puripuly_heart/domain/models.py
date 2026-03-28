from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ChannelId = Literal["self", "peer"]


def _validate_channel(channel: str) -> None:
    if channel not in ("self", "peer"):
        raise ValueError(f"invalid channel: {channel!r}")


@dataclass(frozen=True, slots=True)
class Transcript:
    utterance_id: UUID
    text: str
    is_final: bool
    created_at: float | None = None  # monotonic seconds (Clock)
    channel: ChannelId = "self"
    speaker_label: str | None = None
    peer_epoch: int | None = None

    def __post_init__(self) -> None:
        _validate_channel(self.channel)


@dataclass(frozen=True, slots=True, init=False)
class Translation:
    utterance_id: UUID
    translated_text: str
    source_text: str
    source_language: str | None
    target_language: str | None
    channel: ChannelId
    speaker_label: str | None
    peer_epoch: int | None
    created_at: float | None = None  # monotonic seconds (Clock)

    def __init__(
        self,
        utterance_id: UUID,
        text: str | None = None,
        *,
        translated_text: str | None = None,
        source_text: str = "",
        source_language: str | None = None,
        target_language: str | None = None,
        channel: ChannelId = "self",
        speaker_label: str | None = None,
        peer_epoch: int | None = None,
        created_at: float | None = None,
    ) -> None:
        if text is not None and translated_text is not None and text != translated_text:
            raise ValueError("text and translated_text must match when both are set")

        resolved_text = translated_text if translated_text is not None else text
        if resolved_text is None:
            raise TypeError("Translation requires text or translated_text")

        _validate_channel(channel)

        object.__setattr__(self, "utterance_id", utterance_id)
        object.__setattr__(self, "translated_text", resolved_text)
        object.__setattr__(self, "source_text", source_text)
        object.__setattr__(self, "source_language", source_language)
        object.__setattr__(self, "target_language", target_language)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "speaker_label", speaker_label)
        object.__setattr__(self, "peer_epoch", peer_epoch)
        object.__setattr__(self, "created_at", created_at)

    @property
    def text(self) -> str:
        return self.translated_text


@dataclass(frozen=True, slots=True)
class OSCMessage:
    utterance_id: UUID
    text: str
    created_at: float  # monotonic seconds (Clock)


@dataclass(slots=True)
class UtteranceBundle:
    utterance_id: UUID
    channel: ChannelId = "self"
    partial: Transcript | None = None
    final: Transcript | None = None
    translation: Translation | None = None

    def __post_init__(self) -> None:
        _validate_channel(self.channel)

    def with_transcript(self, transcript: Transcript) -> "UtteranceBundle":
        if transcript.utterance_id != self.utterance_id:
            raise ValueError("utterance_id mismatch")
        if self.partial is None and self.final is None and self.translation is None:
            self.channel = transcript.channel
        elif transcript.channel != self.channel:
            raise ValueError("channel mismatch")

        if transcript.is_final:
            self.final = transcript
            self.partial = None
        else:
            if self.final is None:
                self.partial = transcript
        return self

    def with_translation(self, translation: Translation) -> "UtteranceBundle":
        if translation.utterance_id != self.utterance_id:
            raise ValueError("utterance_id mismatch")
        if self.partial is None and self.final is None and self.translation is None:
            self.channel = translation.channel
        elif translation.channel != self.channel:
            raise ValueError("channel mismatch")
        self.translation = translation
        return self
