from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from puripuly_heart.core.lifecycle import LifecycleScope, start_lifecycle_task
from puripuly_heart.domain.models import FinalLanguageRun, Transcript

PeerFinalRunOutcome = Literal["translated", "source_only", "cancelled"]


@dataclass(frozen=True, slots=True)
class PeerFinalRunChild:
    parent_utterance_id: UUID
    utterance_id: UUID
    transcript: Transcript
    detected_language: str | None
    source: str


@dataclass(slots=True)
class _PeerFinalRunParent:
    parent_utterance_id: UUID
    child_ids: set[UUID] = field(default_factory=set)
    completed_child_ids: set[UUID] = field(default_factory=set)
    closed: bool = False


ChildCreated = Callable[[PeerFinalRunChild], Awaitable[None]]
ChildStarted = Callable[[PeerFinalRunChild, asyncio.Task[PeerFinalRunOutcome]], Awaitable[None]]
ChildCancellationRequested = Callable[[], bool]
ChildProcessor = Callable[
    [PeerFinalRunChild, ChildCancellationRequested], Awaitable[PeerFinalRunOutcome]
]
ChildTerminal = Callable[[PeerFinalRunChild, PeerFinalRunOutcome], Awaitable[None]]
ParentClosed = Callable[[UUID], Awaitable[None]]
ParentRejected = Callable[[UUID], Awaitable[None]]


@dataclass(slots=True)
class PeerFinalRunsLifecycleOwner:
    on_child_created: ChildCreated
    on_child_started: ChildStarted
    process_child: ChildProcessor
    on_child_terminal: ChildTerminal
    on_parent_closed: ParentClosed
    on_parent_rejected: ParentRejected
    _queue: asyncio.Queue[PeerFinalRunChild] = field(default_factory=asyncio.Queue)
    _parents: dict[UUID, _PeerFinalRunParent] = field(default_factory=dict)
    _closed_parent_ids: set[UUID] = field(default_factory=set)
    _cancelling_parent_ids: set[UUID] = field(default_factory=set)
    _worker_task: asyncio.Task[None] | None = None
    _active_child: PeerFinalRunChild | None = None
    _active_task: asyncio.Task[PeerFinalRunOutcome] | None = None
    _scope: LifecycleScope = field(init=False)
    _accepting: bool = True
    _closed: bool = False

    def __post_init__(self) -> None:
        self._scope = LifecycleScope("peer-final-runs")

    @property
    def has_resources(self) -> bool:
        return self._worker_task is not None or bool(self._parents) or not self._queue.empty()

    def is_parent_closed(self, parent_utterance_id: UUID) -> bool:
        return parent_utterance_id in self._closed_parent_ids

    def is_child_cancellation_requested(self, child: PeerFinalRunChild) -> bool:
        return (
            self._closed
            or not self._accepting
            or child.parent_utterance_id in self._cancelling_parent_ids
        )

    def lifecycle_owner_snapshot(self) -> dict[str, object]:
        return {
            "owner": "PeerFinalRunsLifecycleOwner",
            "resource_fields": ("parent queue", "worker task", "parent/child terminal state"),
            "shutdown_policy": "cancel active child, terminalize queued children, await worker",
            "late_callback_rule": "closed parents reject child completion and publication",
        }

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("PeerFinalRunsLifecycleOwner is closed")
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = start_lifecycle_task(self._scope, self._run(), name="worker")

    async def submit_parent(self, transcript: Transcript, *, source: str) -> tuple[UUID, ...]:
        if transcript.channel != "peer" or not transcript.is_final:
            raise ValueError("peer final-runs owner requires a final peer transcript")
        if (
            transcript.utterance_id in self._closed_parent_ids
            or transcript.utterance_id in self._parents
        ):
            await self.on_parent_rejected(transcript.utterance_id)
            return ()
        if not self._accepting or self._closed:
            return ()
        await self.start()
        parent = self._parents.get(transcript.utterance_id)
        if parent is None:
            parent = _PeerFinalRunParent(parent_utterance_id=transcript.utterance_id)
            self._parents[transcript.utterance_id] = parent
        runs = transcript.final_language_runs or (
            FinalLanguageRun(text=transcript.text, language=""),
        )
        children: list[PeerFinalRunChild] = []
        for run in runs:
            if not run.text.strip():
                continue
            child_id = uuid4()
            child = PeerFinalRunChild(
                parent_utterance_id=transcript.utterance_id,
                utterance_id=child_id,
                transcript=Transcript(
                    utterance_id=child_id,
                    text=run.text,
                    is_final=True,
                    created_at=transcript.created_at,
                    channel="peer",
                    final_language_runs=(run,),
                ),
                detected_language=run.language or None,
                source=source,
            )
            parent.child_ids.add(child_id)
            children.append(child)
            await self.on_child_created(child)
            await self._queue.put(child)
        if not children:
            await self._close_parent(parent)
        else:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        return tuple(child.utterance_id for child in children)

    async def cancel_pending(self) -> None:
        if self._closed:
            return
        self._accepting = False
        self._request_cancellation()
        worker = self._worker_task
        if worker is not None:
            await self._scope.close()
        self._worker_task = None
        await self._terminalize_queued_children()
        self._accepting = True
        self._scope = LifecycleScope("peer-final-runs")

    async def wait_for_idle(self) -> None:
        while self._parents or not self._queue.empty() or self._active_child is not None:
            await asyncio.sleep(0)

    async def close(self) -> None:
        if self._closed:
            return
        self._accepting = False
        self._closed = True
        self._request_cancellation()
        await self._scope.close()
        self._worker_task = None
        await self._terminalize_queued_children()

    async def _run(self) -> None:
        try:
            while True:
                child = await self._queue.get()
                parent = self._parents.get(child.parent_utterance_id)
                if parent is None or parent.closed:
                    continue
                self._active_child = child
                self._active_task = start_lifecycle_task(
                    self._scope, self._process_child(child), name=f"child:{child.utterance_id}"
                )
                await self.on_child_started(child, self._active_task)
                try:
                    outcome = await self._active_task
                except asyncio.CancelledError:
                    await self._terminalize_child(child, "cancelled")
                    raise
                except Exception:
                    await self._terminalize_child(child, "source_only")
                else:
                    if self.is_child_cancellation_requested(child):
                        await self._terminalize_child(child, "cancelled")
                        raise asyncio.CancelledError
                    await self._terminalize_child(child, outcome)
                finally:
                    self._active_child = None
                    self._active_task = None
        except asyncio.CancelledError:
            raise

    async def _process_child(self, child: PeerFinalRunChild) -> PeerFinalRunOutcome:
        try:
            return await self.process_child(
                child,
                lambda: self.is_child_cancellation_requested(child),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return "source_only"

    def _request_cancellation(self) -> None:
        self._cancelling_parent_ids.update(self._parents)
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()

    async def _terminalize_queued_children(self) -> None:
        queued: list[PeerFinalRunChild] = []
        while True:
            try:
                queued.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for child in queued:
            await self._terminalize_child(child, "cancelled")

    async def _terminalize_child(
        self,
        child: PeerFinalRunChild,
        outcome: PeerFinalRunOutcome,
    ) -> None:
        parent = self._parents.get(child.parent_utterance_id)
        if parent is None or parent.closed or child.utterance_id in parent.completed_child_ids:
            return
        await self.on_child_terminal(child, outcome)
        parent.completed_child_ids.add(child.utterance_id)
        if parent.completed_child_ids == parent.child_ids:
            await self._close_parent(parent)

    async def _close_parent(self, parent: _PeerFinalRunParent) -> None:
        if parent.closed:
            return
        parent.closed = True
        self._parents.pop(parent.parent_utterance_id, None)
        self._cancelling_parent_ids.discard(parent.parent_utterance_id)
        self._closed_parent_ids.add(parent.parent_utterance_id)
        await self.on_parent_closed(parent.parent_utterance_id)
