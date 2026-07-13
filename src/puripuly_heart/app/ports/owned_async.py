from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class OwnedOutcome(Generic[T]):
    value: T
    cancellation_count: int


class OwnedFailure(Exception):
    def __init__(self, cause: Exception, cancellation_count: int) -> None:
        super().__init__(type(cause).__name__)
        self.cause = cause
        self.cancellation_count = cancellation_count


async def settle_owned(awaitable: Awaitable[T]) -> OwnedOutcome[T]:
    task = awaitable if isinstance(awaitable, asyncio.Task) else asyncio.Task(awaitable)
    cancellation_count = 0
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_count += 1
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
        except Exception:
            break
    try:
        return OwnedOutcome(task.result(), cancellation_count)
    except Exception as exc:
        if not cancellation_count:
            raise
        raise OwnedFailure(exc, cancellation_count) from None


__all__ = ["OwnedFailure", "OwnedOutcome", "settle_owned"]
