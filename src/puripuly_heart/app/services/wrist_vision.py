from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import time

from puripuly_heart.app.services.quick_chat import QuickChatHistory, chat_text
from puripuly_heart.core.feature_diagnostics import log_feature
from puripuly_heart.core.vision.translation import VisionError, VisionTranslationService
from puripuly_heart.domain.events import UIEventType

NATIVE_ERRORS = frozenset(
    {
        "steamvr_unavailable",
        "openvr_interface_missing",
        "controller_unavailable",
        "controller_tracking_unavailable",
        "controller_input_unavailable",
        "hmd_tracking_unavailable",
        "scene_unavailable",
        "capture_tracking_changed",
        "frame_outside_eye",
        "frame_behind_eye",
        "frame_too_small",
        "invalid_projection",
        "mirror_format_unsupported",
        "mirror_acquire_failed",
        "d3d11_create_failed",
        "hmd_adapter_unavailable",
        "mirror_map_failed",
        "framing_timeout",
        "capture_rate_limited",
        "render_dc_failed",
        "render_bitmap_failed",
        "render_font_failed",
        "openvr_overlay_failed",
        "mirror_pitch_invalid",
        "staging_create_failed",
        "degenerate_frame",
    }
)


def captured_image(message):
    from PIL import Image

    try:
        width, height = message["size"]
        if (
            type(width) is not int
            or type(height) is not int
            or min(width, height) < 2
            or max(width, height) > 1024
        ):
            raise ValueError()
        encoded = message["rgb_hex"]
        if not isinstance(encoded, str) or len(encoded) != width * height * 6:
            raise ValueError()
        metadata = message["metadata"]
        age = time.time() * 1000 - metadata["captured_at_ms"]
        if (
            not -1000 <= age <= 5000
            or type(metadata["scene_pid"]) is not int
            or metadata["scene_pid"] <= 0
        ):
            raise ValueError()
        image = Image.frombytes("RGB", (width, height), bytes.fromhex(encoded))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except ValueError, KeyError, TypeError, OverflowError:
        raise VisionError("invalid_capture") from None


class WristVisionOwner:
    def __init__(self, *, spawn, preferences, translate, labels, on_state=None):
        self.spawn, self.preferences, self.translate, self.labels = (
            spawn,
            preferences,
            translate,
            labels,
        )
        self.on_state = on_state
        self.process = None
        self.reader = None
        self.delivery = None
        self.job = None
        self.history = QuickChatHistory()
        self.phase = "off"
        self.code = None
        self.running = False
        self.closed = False
        self.epoch = 0
        self.capture_id = None
        self.lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.ready = None
        self.dirty = False
        self.stopping = False

    def state(self, phase, code=None):
        self.phase, self.code = phase, code
        if self.on_state is not None:
            try:
                self.on_state(phase, code)
            except Exception as error:
                log_feature("WristVision", "presentation", "state_callback_failed", error=error)

    async def enable(self):
        async with self.lock:
            if self.closed:
                return False
            if self.running and self.process is not None and self.process.returncode is None:
                await self.sync()
                return True
            await self._stop()
            self.state("starting")
            try:
                self.ready = asyncio.get_running_loop().create_future()
                self.process = await self.spawn()
                self.stopping = False
                self.running = True
                self.reader = asyncio.create_task(self._read(self.process))
                await asyncio.wait_for(asyncio.shield(self.ready), 8)
                await self.sync()
                self.state("chat")
                log_feature("WristVision", "startup", "started", level=logging.INFO)
                return True
            except asyncio.CancelledError:
                await self._stop()
                raise
            except Exception as error:
                code = (
                    "wrist_component_missing"
                    if isinstance(error, FileNotFoundError)
                    else "native_start_failed"
                )
                log_feature("WristVision", "startup", code, error=error)
                await self._stop()
                self.state("failed", code)
                return False

    async def send(self, message):
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(encoded) > 500_000:
            raise VisionError("presentation_too_large")
        async with self.write_lock:
            process = self.process
            if process is None or process.returncode is not None or not self.running:
                return
            process.stdin.write(encoded)
            await asyncio.wait_for(process.stdin.drain(), 3)

    def publish_event(self, event):
        if self.closed:
            return
        context = self.preferences()
        if event.type == UIEventType.SESSION_STATE_CHANGED:
            self.schedule_sync()
            return
        enabled = bool(
            context.get(
                "peer_translation_enabled" if event.channel == "peer" else "translation_enabled",
                False,
            )
        )
        row = self.history.accept(event, translation_enabled=enabled)
        if row is not None and self.running:
            self.schedule_sync()

    def schedule_sync(self):
        if not self.running or self.closed:
            return
        self.dirty = True
        if self.delivery is None or self.delivery.done():
            self.delivery = asyncio.create_task(self._deliver())

    async def _deliver(self):
        try:
            while self.dirty and self.running:
                self.dirty = False
                await self.sync()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_feature("WristVision", "presentation", "delivery_failed", error=error)
            self.state("failed", "delivery_failed")

    async def sync(self):
        preferences = self.preferences()
        config = preferences["config"]
        copy = self.labels()
        await self.send(
            dict(type="config", values=config, title=copy["title"], labels=copy["buttons"])
        )
        rows = []
        for row in self.history.records.values():
            peer = row["role"] == "peer"
            enabled = bool(
                preferences.get(
                    "peer_translation_enabled" if peer else "translation_enabled", False
                )
            )
            mode = config["peer_display" if peer else "self_display"]
            display = dict(row)
            if not peer and mode == "original":
                display["translation"] = ""
            text = chat_text(display, peer_mode=mode, translation_enabled=enabled)
            if not peer and mode == "translation" and enabled and row.get("translation"):
                text = row["translation"]
            if row.get("state") == "failed":
                text += "\n" + copy["translation_failed"]
            rows.append(dict(id=row["id"], role=row["role"], text=text[:3500]))
        await self.send(dict(type="snapshot", rows=rows[-40:]))
        if self.phase in {"starting", "chat"}:
            await self.send(
                dict(
                    type="status",
                    text="STT "
                    + str(preferences.get("recognition_state", "unknown"))
                    + " · "
                    + ("TRANS ON" if preferences.get("translation_enabled") else "TRANS OFF"),
                )
            )

    async def cancel(self, *, notify=True):
        self.epoch += 1
        self.capture_id = None
        task, self.job = self.job, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if notify:
            await self.send(dict(type="cancel"))
        if self.running:
            self.state("chat")

    async def handle(self, message):
        kind = message.get("type")
        if kind == "ready":
            if message.get("protocol") != 1:
                raise VisionError("protocol_mismatch")
            if self.ready is not None and not self.ready.done():
                self.ready.set_result(True)
        elif kind == "framing":
            if self.job is None:
                self.state("framing")
        elif kind == "capturing" and self.phase == "framing":
            identity = message.get("id")
            if type(identity) is not int or identity <= 0:
                raise VisionError("invalid_capture")
            self.capture_id = identity
            self.state("capturing")
        elif (
            kind == "capture" and self.phase == "capturing" and message.get("id") == self.capture_id
        ):
            if self.job is not None and not self.job.done():
                return
            self.epoch += 1
            epoch = self.epoch
            self.state("busy")
            self.job = asyncio.create_task(self._translate(message, epoch))
        elif kind == "cancel":
            await self.cancel(notify=False)
        elif kind == "error":
            code = message.get("code")
            code = code if code in NATIVE_ERRORS else "native_failed"
            log_feature("WristVision", "native", code)
            await self.cancel(notify=False)
            self.state("failed", code)
            await self.send(dict(type="status", text=self.labels()["error"] + " (" + code + ")"))
        elif kind == "closed":
            await self.cancel(notify=False)
            self.running = False
            self.state("off")

    async def _translate(self, message, epoch):
        image = None
        try:
            image = await asyncio.to_thread(captured_image, message)
            message.pop("rgb_hex", None)
            await self.send(dict(type="status", text=self.labels()["processing"]))
            blocks = await self.translate(image)
            if epoch != self.epoch or not self.running:
                return
            rows = [
                dict(id=block.id, role="vision", text=block.original + "\n→ " + block.translation)
                for block in blocks
            ]
            await self.send(dict(type="result", id=self.capture_id, rows=rows))
            await self.send(
                dict(
                    type="status",
                    text=(
                        self.labels()["spatial_fallback"]
                        if self.preferences()["config"]["spatial_enabled"]
                        else self.labels()["result"]
                    ),
                )
            )
            self.state("result")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if epoch == self.epoch:
                code = VisionTranslationService.report(error)
                self.state("failed", code)
                await self.send(dict(type="result", id=self.capture_id, rows=[]))
                await self.send(
                    dict(type="status", text=self.labels()["error"] + " (" + code + ")")
                )
        finally:
            image = None
            message.pop("rgb_hex", None)
            if self.job is asyncio.current_task():
                self.job = None

    async def _read(self, process):
        try:
            while self.running and process is self.process:
                raw = await process.stdout.readline()
                if not raw:
                    break
                if len(raw) > 7_000_000:
                    raise VisionError("frame_too_large")
                await self.handle(json.loads(raw))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_feature("WristVision", "transport", "native_transport_failed", error=error)
            self.state("failed", "native_transport_failed")
        finally:
            if self.ready is not None and not self.ready.done():
                self.ready.set_exception(VisionError("native_start_failed"))
            previous_phase, previous_code = self.phase, self.code
            unexpected = self.running and not self.stopping
            await self.cancel(notify=False)
            self.running = False
            if previous_phase == "failed":
                self.state("failed", previous_code)
            elif unexpected:
                log_feature("WristVision", "transport", "native_exited")
                self.state("failed", "native_exited")
            else:
                self.state("off")
            if not self.stopping and process.returncode is None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    async def _stop(self):
        self.stopping = True
        await self.cancel()
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(Exception):
                await self.send(dict(type="exit"))
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        self.running = False
        for task in (self.reader, self.delivery):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.reader = self.delivery = self.process = None

    async def disable(self):
        async with self.lock:
            await self._stop()
            self.state("off")

    async def close(self):
        self.closed = True
        await self.disable()
