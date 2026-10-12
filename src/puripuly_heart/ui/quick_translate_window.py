from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import sys
from uuid import uuid4

import flet as ft

from puripuly_heart.app.services.quick_translate import FRAME_PREFIX, MAX_FRAME_BYTES
from puripuly_heart.runtime_layout import current_runtime_layout
from puripuly_heart.ui.i18n import set_locale, t
from puripuly_heart.ui.theme import COLOR_BACKGROUND, COLOR_DIVIDER


async def spawn_quick_window(locale: str):
    layout = current_runtime_layout()
    executable = layout.host_executable
    if layout.host_kind == "source" and executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    command = [str(executable)]
    if layout.host_kind == "source":
        command.extend(["-m", "puripuly_heart.main"])
    elif layout.host_kind == "native":
        command.append("--headless")
    command.extend(["run-quick-translate", "--locale", locale])
    return await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        limit=MAX_FRAME_BYTES,
    )


async def stop_quick_window(process):
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await killer.wait()
    else:
        process.kill()
    await process.wait()


def clamp_window_bounds(values: dict, *, work_area=(0, 0, 1920, 1080)) -> dict:
    left, top, right, bottom = work_area
    width = max(320, min(int(values.get("width", 480)), max(320, right - left)))
    height = max(280, min(int(values.get("height", 520)), max(280, bottom - top)))
    return dict(
        width=width,
        height=height,
        left=max(left, min(int(values.get("left", left + 40)), right - width)),
        top=max(top, min(int(values.get("top", top + 40)), bottom - height)),
    )


def primary_work_area():
    if os.name != "nt":
        return 0, 0, 1920, 1080
    from ctypes import wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
        return 0, 0, 1280, 720
    scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
    return tuple(round(value / scale) for value in (rect.left, rect.top, rect.right, rect.bottom))


class QuickWindow:
    def __init__(self, page):
        self.page = page
        self.pending = None
        self.history = {}
        self.order = []
        self.input = ft.TextField(
            label=t("quick.input"),
            multiline=True,
            shift_enter=True,
            min_lines=2,
            max_lines=4,
            max_length=8000,
            on_submit=self.send,
        )
        self.status = ft.Text(t("quick.hint"))
        self.results = ft.ListView(expand=True, spacing=12, auto_scroll=False)
        self.pin = ft.Switch(label=t("quick.pin"), value=True, on_change=self.set_pin)
        self.button = ft.Button(t("quick.send"), on_click=self.send)
        page.title = t("quick.title")
        page.bgcolor = COLOR_BACKGROUND
        page.window.bgcolor = COLOR_BACKGROUND
        page.window.opacity = 1
        page.window.min_width = 360
        page.window.min_height = 300
        page.window.prevent_close = True
        page.window.on_event = self.window_event
        page.add(
            ft.Container(
                expand=True,
                padding=16,
                border=ft.Border.all(1, COLOR_DIVIDER),
                content=ft.Column(
                    [
                        ft.Row([self.pin, ft.TextButton(t("quick.hide"), on_click=self.hide)]),
                        self.results,
                        self.input,
                        ft.Row([self.button, self.status]),
                    ],
                    expand=True,
                ),
            )
        )
        page.run_task(self.read_parent)

    def emit(self, message):
        line = FRAME_PREFIX + json.dumps(message, ensure_ascii=False) + "\n"
        sys.stdout.buffer.write(line.encode("utf-8"))
        sys.stdout.buffer.flush()

    async def send(self, _event=None):
        text = (self.input.value or "").strip()
        if self.pending is not None or not text:
            return
        identity = str(uuid4())
        self.pending = (identity, text)
        self.button.disabled = True
        self.status.value = t("quick.pending")
        row = ft.Text(text + "\n" + t("quick.pending"), selectable=True)
        self.history[identity] = (text, row)
        self.order.append(identity)
        self.results.controls.append(row)
        self.results.controls.append(
            ft.TextButton(
                t("quick.copy"), on_click=lambda _: self.page.run_task(self.copy_row, row)
            )
        )
        while len(self.order) > 100:
            self.history.pop(self.order.pop(0), None)
            del self.results.controls[:2]
        try:
            self.emit(dict(type="submit", id=identity, text=text))
        except Exception:
            self.status.value = t("quick.error.window_connection_failed")
            self.button.disabled = False
            self.pending = None
        self.page.update()

    async def copy_row(self, row):
        try:
            await ft.Clipboard().set(row.value)
        except Exception:
            self.emit(dict(type="failure"))
            self.status.value = t("quick.error.window_connection_failed")
            self.page.update()

    async def read_parent(self):
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME_BYTES + 1)
                if not line:
                    await self.page.window.destroy()
                    return
                if len(line) > MAX_FRAME_BYTES:
                    raise ValueError("Window message too large")
                message = json.loads(line)
                if message["type"] == "exit":
                    await self.page.window.destroy()
                    return
                if message["type"] == "show":
                    values = message["preferences"]
                    for name, value in clamp_window_bounds(
                        values, work_area=primary_work_area()
                    ).items():
                        setattr(self.page.window, name, value)
                    self.pin.value = bool(values.get("always_on_top", True))
                    self.page.window.always_on_top = self.pin.value
                    self.page.window.visible = True
                    self.page.window.minimized = False
                elif message["type"] == "result":
                    self.accept_result(message)
                self.page.update()
        except Exception:
            self.emit(dict(type="failure"))
            self.status.value = t("quick.error.window_connection_failed")
            self.page.update()

    def accept_result(self, message):
        identity = message["id"]
        if identity not in self.history:
            return
        original, row = self.history[identity]
        code = message.get("code")
        if code:
            row.value = (
                original
                + "\n"
                + t("quick.error." + code, default=t("quick.error.translation_failed"))
            )
            self.status.value = t("quick.retry")
        else:
            row.value = original + "\n→ " + message["translation"]
            if (
                self.pending
                and self.pending[0] == identity
                and (self.input.value or "").strip() == self.pending[1]
            ):
                self.input.value = ""
            self.status.value = t("quick.complete")
        if self.pending and self.pending[0] == identity:
            self.pending = None
            self.button.disabled = False

    async def set_pin(self, _event):
        self.page.window.always_on_top = bool(self.pin.value)
        self.page.update()
        self.save_preferences()

    def save_preferences(self):
        window = self.page.window
        self.emit(
            dict(
                type="preferences",
                values=dict(
                    always_on_top=bool(self.pin.value),
                    width=round(window.width or 480),
                    height=round(window.height or 520),
                    left=round(window.left or 0),
                    top=round(window.top or 0),
                ),
            )
        )

    async def hide(self, _event=None):
        self.save_preferences()
        self.page.window.visible = False
        self.page.update()

    async def window_event(self, event):
        if event.type == ft.WindowEventType.CLOSE:
            await self.hide()


def run_quick_window(locale):
    set_locale(locale)
    ft.run(lambda page: QuickWindow(page))
