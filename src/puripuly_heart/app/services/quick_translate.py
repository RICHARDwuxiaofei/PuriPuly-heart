from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from uuid import UUID

from puripuly_heart.core.feature_diagnostics import log_feature

FRAME_PREFIX = "PURIPULY_QUICK:"
MAX_FRAME_BYTES = 65536


class QuickTranslateOwner:
    def __init__(self, *, spawn, submit, preferences, save_preferences, stop=None):
        self.spawn = spawn
        self.submit = submit
        self.preferences = preferences
        self.save_preferences = save_preferences
        self.stop = stop
        self.process = None
        self.reader_task = None
        self.tasks = set()
        self.closed = False
        self.visible = False
        self.visibility_revision = 0
        self.lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.seen = OrderedDict()

    async def open(self):
        async with self.lock:
            if self.closed:
                return
            try:
                if self.process is None or self.process.returncode is not None:
                    if self.reader_task is not None:
                        await asyncio.gather(self.reader_task, return_exceptions=True)
                    self.process = await self.spawn()
                    self.visible = False
                    self.reader_task = asyncio.create_task(self.read_messages(self.process))
                requested_visible = not self.visible
                self.visibility_revision += 1
                message = dict(
                    type="show" if requested_visible else "hide",
                    visibility_revision=self.visibility_revision,
                )
                if requested_visible:
                    message["preferences"] = self.preferences()
                await self.send(message)
                self.visible = requested_visible
            except Exception as error:
                log_feature("QuickTranslate", "window", "window_start_failed", error=error)
                raise

    async def send(self, message):
        process = self.process
        if process is None or process.returncode is not None:
            return
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            raise ValueError("Window message too large")
        async with self.write_lock:
            process.stdin.write(data)
            await process.stdin.drain()

    async def read_messages(self, process):
        try:
            while not self.closed:
                line = await process.stdout.readline()
                if not line:
                    break
                if len(line) > MAX_FRAME_BYTES:
                    raise ValueError("Window message too large")
                line = line.decode("utf-8")
                if not line.startswith(FRAME_PREFIX):
                    continue
                message = json.loads(line[len(FRAME_PREFIX) :])
                if message.get("type") == "submit":
                    identity = str(UUID(message["id"]))
                    text = message["text"]
                    if (
                        not isinstance(text, str)
                        or not text.strip()
                        or len(text) > 8000
                        or identity in self.seen
                    ):
                        continue
                    if len(self.tasks) >= 8:
                        await self.send(dict(type="result", id=identity, code="busy"))
                        continue
                    self.seen[identity] = None
                    if len(self.seen) > 512:
                        self.seen.popitem(last=False)
                    task = asyncio.create_task(self.translate(identity, text, process))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                elif message.get("type") == "visibility":
                    if (
                        self.process is process
                        and message.get("visibility_revision") == self.visibility_revision
                        and isinstance(message.get("visible"), bool)
                    ):
                        self.visible = message["visible"]
                elif message.get("type") == "preferences":
                    try:
                        await self.save_preferences(message["values"])
                    except Exception as error:
                        log_feature(
                            "QuickTranslate", "settings", "preferences_save_failed", error=error
                        )
                elif message.get("type") == "failure":
                    log_feature("QuickTranslate", "window", "window_failed")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_feature("QuickTranslate", "window", "window_connection_failed", error=error)
        finally:
            if self.process is process:
                self.visible = False
            if process.returncode is None and not self.closed:
                log_feature("QuickTranslate", "window", "window_closed_unexpectedly")
                if self.stop is not None:
                    await self.stop(process)
                else:
                    process.terminate()
            await process.wait()

    async def translate(self, identity, text, process):
        try:
            utterance_id, translation = await self.submit(text)
            result = dict(
                type="result", id=identity, utterance_id=utterance_id, translation=translation
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = (
                "backend_not_ready"
                if isinstance(error, RuntimeError) and str(error) == "backend_not_ready"
                else "translation_failed"
            )
            log_feature(
                "QuickTranslate",
                "submission" if code == "backend_not_ready" else "translation",
                code,
                error=error,
            )
            result = dict(type="result", id=identity, code=code)
        if self.process is process and not self.closed:
            try:
                await self.send(result)
            except Exception as error:
                log_feature("QuickTranslate", "window", "window_connection_failed", error=error)

    async def close(self):
        self.closed = True
        self.visible = False
        process = self.process
        if process is not None and process.returncode is None:
            try:
                await self.send(dict(type="exit"))
                await asyncio.wait_for(process.wait(), 5)
            except (TimeoutError, BrokenPipeError, ConnectionError):
                if self.stop is not None:
                    await self.stop(process)
                else:
                    process.kill()
                    await process.wait()
        if self.reader_task is not None:
            await asyncio.gather(self.reader_task, return_exceptions=True)
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
