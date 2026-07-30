from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMAttemptResponseMetadata:
    model: str | None = None
    provider: str | None = None
    generation_id: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: str | None = None


@dataclass(slots=True)
class LLMAttemptMetadataRecorder:
    response: LLMAttemptResponseMetadata | None = None

    def record(self, response: LLMAttemptResponseMetadata) -> None:
        current = self.response
        if current is None:
            self.response = response
            return
        self.response = LLMAttemptResponseMetadata(
            model=response.model or current.model,
            provider=response.provider or current.provider,
            generation_id=response.generation_id or current.generation_id,
            prompt_tokens=(
                response.prompt_tokens
                if response.prompt_tokens is not None
                else current.prompt_tokens
            ),
            completion_tokens=(
                response.completion_tokens
                if response.completion_tokens is not None
                else current.completion_tokens
            ),
            total_tokens=(
                response.total_tokens if response.total_tokens is not None else current.total_tokens
            ),
            cost_usd=response.cost_usd or current.cost_usd,
        )


_current_recorder: contextvars.ContextVar[LLMAttemptMetadataRecorder | None] = (
    contextvars.ContextVar("puripuly_heart_llm_attempt_metadata_recorder", default=None)
)


@contextlib.contextmanager
def attempt_metadata_context(recorder: LLMAttemptMetadataRecorder) -> Iterator[None]:
    token = _current_recorder.set(recorder)
    try:
        yield
    finally:
        _current_recorder.reset(token)


def report_attempt_response_metadata(response: LLMAttemptResponseMetadata) -> None:
    recorder = _current_recorder.get()
    if recorder is None:
        return
    with contextlib.suppress(Exception):
        recorder.record(response)
