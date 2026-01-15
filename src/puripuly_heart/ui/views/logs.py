"""System logs view with real-time log display and folder access."""

import inspect
import logging
import subprocess
import sys
import time
from pathlib import Path

import flet as ft

from puripuly_heart.ui.components.glow import GLOW_CARD, create_glow_stack
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, t
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)

MAX_LOG_ENTRIES = 4000
CLEANUP_BATCH = 500
_UPDATE_INTERVAL = 0.2  # 200ms throttling


def _get_log_dir() -> Path:
    """Get the directory where log files are stored."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent  # views -> ui -> puripuly_heart


class FletLogHandler(logging.Handler):
    """Custom log handler that forwards logs to a LogsView."""

    def __init__(self, logs_view: "LogsView"):
        super().__init__()
        self.logs_view = logs_view
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.logs_view.append_log(msg)
        except Exception:
            pass


class _LogListProxy:
    """Compatibility proxy for tests expecting a list-style log view."""

    def __init__(self, view: "LogsView") -> None:
        self._view = view

    @property
    def controls(self) -> list[ft.Text]:
        return [ft.Text(entry) for entry in self._view._log_buffer]


class LogsView(ft.Column):
    """System logs view with VR-optimized display and folder access."""

    def __init__(self):
        super().__init__(expand=True, spacing=16)

        self._handler: FletLogHandler | None = None
        self._title_text: ft.Text | None = None
        self._log_text: ft.Text | None = None
        self._log_scroll: ft.Column | None = None
        self._folder_button: ft.TextButton | None = None

        # Log buffer and throttling state
        self._log_buffer: list[str] = []
        self._last_update: float = 0.0
        self._pending_update: bool = False
        self.log_list = _LogListProxy(self)

        self._build_ui()

    def _build_ui(self):
        """Build the logs view UI."""
        # Title (styled like About page section headers)
        self._title_text = ft.Text(
            t("logs.title"),
            size=28,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )

        # Folder open button (brown, hover -> primary)
        self._folder_button = ft.TextButton(
            text=t("logs.open_folder"),
            icon=ft.Icons.FOLDER_OPEN,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                icon_color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                text_style=ft.TextStyle(
                    size=20,
                    font_family=font_for_language(get_locale()),
                ),
                overlay_color=ft.Colors.TRANSPARENT,
                animation_duration=0,
            ),
            on_click=self._open_log_folder,
        )

        # Header row
        header = ft.Container(
            content=ft.Row(
                controls=[
                    self._title_text,
                    ft.Container(expand=True),
                    self._folder_button,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(left=16, right=8, top=8, bottom=0),
        )

        # Single selectable text for all logs (enables multi-line drag selection)
        self._log_text = ft.Text(
            "",
            size=16,
            font_family="Consolas",
            color=COLOR_ON_BACKGROUND,
            selectable=True,
        )

        # Scrollable container for log text
        self._log_scroll = ft.Column(
            controls=[
                ft.Container(
                    content=self._log_text,
                    padding=ft.padding.only(left=16, right=16, top=8, bottom=16),
                )
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

        # Card content
        card_content = ft.Column(
            controls=[header, self._log_scroll],
            spacing=0,
            expand=True,
        )

        # Wrap in glow stack
        content_with_glow = create_glow_stack(
            ft.Container(content=card_content, expand=True),
            config=GLOW_CARD,
        )

        # Outer card container
        card = ft.Container(
            content=content_with_glow,
            bgcolor=COLOR_SURFACE,
            border_radius=16,
            border=ft.border.all(1, ft.Colors.with_opacity(0.4, ft.Colors.WHITE)),
            expand=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=get_card_shadow(),
        )

        self.controls = [card]

    def attach_log_handler(self) -> None:
        """Attach this view as a logging handler to capture app logs."""
        if self._handler is not None:
            return
        self._handler = FletLogHandler(self)
        logging.getLogger().addHandler(self._handler)

    def append_log(self, record: str):
        """Append a log entry with throttled updates."""
        self._log_buffer.append(record)

        # Memory management: batch cleanup when threshold exceeded
        if len(self._log_buffer) > MAX_LOG_ENTRIES + CLEANUP_BATCH:
            del self._log_buffer[:CLEANUP_BATCH]

        # Throttled update
        now = time.time()
        if now - self._last_update >= _UPDATE_INTERVAL:
            self._flush_logs()
        else:
            self._pending_update = True

    def _flush_logs(self):
        """Flush pending logs to the UI."""
        if self._log_text is None:
            return

        self._log_text.value = "\n".join(self._log_buffer)
        self._last_update = time.time()
        self._pending_update = False

        if self.page:
            self._log_text.update()

    def apply_locale(self) -> None:
        """Refresh UI text when locale changes."""
        if self._title_text:
            self._title_text.value = t("logs.title")
        if self._folder_button:
            self._folder_button.text = t("logs.open_folder")
        # Only update if added to page
        if self.page:
            self.update()

    async def scroll_to_bottom(self) -> None:
        """Scroll to the latest log entry."""
        if self._log_scroll and self.page:
            if self._pending_update:
                self._flush_logs()
            result = self._log_scroll.scroll_to(offset=-1, duration=0)
            if inspect.isawaitable(result):
                await result

    def _open_log_folder(self, _):
        """Open the log folder in the system file explorer."""
        log_dir = _get_log_dir()
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(log_dir)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(log_dir)])
        else:
            subprocess.Popen(["xdg-open", str(log_dir)])
