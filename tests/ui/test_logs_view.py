"""Tests for LogsView batch deletion optimization."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import PropertyMock, patch

from puripuly_heart.ui.views import logs as logs_module
from puripuly_heart.ui.views.logs import (
    CLEANUP_BATCH,
    MAX_LOG_ENTRIES,
    FletLogHandler,
    LogsView,
    _get_log_dir,
)


class TestLogsView:
    def test_append_log_adds_entry(self):
        """로그 항목이 정상적으로 추가되는지 확인"""
        view = LogsView()
        with patch.object(type(view), "page", new_callable=PropertyMock, return_value=None):
            view.append_log("test message")
        assert len(view.log_list.controls) == 1

    def test_batch_cleanup_triggers_at_threshold(self):
        """4500개 초과 시 500개 배치 삭제 확인"""
        view = LogsView()
        with patch.object(type(view), "page", new_callable=PropertyMock, return_value=None):
            for i in range(MAX_LOG_ENTRIES + CLEANUP_BATCH + 1):
                view.append_log(f"log {i}")
        assert len(view.log_list.controls) == MAX_LOG_ENTRIES + 1

    def test_no_cleanup_under_threshold(self):
        """4500개 이하면 삭제 안 함"""
        view = LogsView()
        with patch.object(type(view), "page", new_callable=PropertyMock, return_value=None):
            for i in range(MAX_LOG_ENTRIES + CLEANUP_BATCH):
                view.append_log(f"log {i}")
        assert len(view.log_list.controls) == MAX_LOG_ENTRIES + CLEANUP_BATCH

    def test_oldest_entries_removed_first(self):
        """오래된 항목부터 삭제되는지 확인"""
        view = LogsView()
        with patch.object(type(view), "page", new_callable=PropertyMock, return_value=None):
            for i in range(MAX_LOG_ENTRIES + CLEANUP_BATCH + 1):
                view.append_log(f"log {i}")
        # 첫 번째 남은 항목이 "log 500"이어야 함
        first_text = view.log_list.controls[0].value
        assert "log 500" in first_text

    def test_apply_locale_updates_title_and_folder_text(self):
        view = LogsView()
        with patch.object(type(view), "page", new_callable=PropertyMock, return_value=None):
            view.apply_locale()
        assert view._title_text.value == logs_module.t("logs.title")
        assert view._folder_button.text == logs_module.t("logs.open_folder")

    def test_open_log_folder_uses_platform_specific_launcher(self):
        view = LogsView()
        commands = []

        def fake_popen(cmd):
            commands.append(cmd)

        with (
            patch.object(type(view), "page", new_callable=PropertyMock, return_value=object()),
            patch.object(logs_module, "_get_log_dir", return_value=logs_module.Path("/tmp/logs")),
            patch.object(logs_module.subprocess, "Popen", side_effect=fake_popen),
            patch.object(logs_module.sys, "platform", "linux"),
        ):
            view._open_log_folder(None)

        assert commands == [["xdg-open", "/tmp/logs"]]

    def test_open_log_folder_windows_and_macos(self):
        view = LogsView()
        commands = []

        def fake_popen(cmd):
            commands.append(cmd)

        with (
            patch.object(type(view), "page", new_callable=PropertyMock, return_value=object()),
            patch.object(logs_module, "_get_log_dir", return_value=logs_module.Path("/tmp/logs")),
            patch.object(logs_module.subprocess, "Popen", side_effect=fake_popen),
            patch.object(logs_module.sys, "platform", "win32"),
        ):
            view._open_log_folder(None)

        with (
            patch.object(type(view), "page", new_callable=PropertyMock, return_value=object()),
            patch.object(logs_module, "_get_log_dir", return_value=logs_module.Path("/tmp/logs")),
            patch.object(logs_module.subprocess, "Popen", side_effect=fake_popen),
            patch.object(logs_module.sys, "platform", "darwin"),
        ):
            view._open_log_folder(None)

        assert commands == [["explorer", "/tmp/logs"], ["open", "/tmp/logs"]]

    def test_get_log_dir_delegates_to_user_config_dir(self):
        fake_paths = type(
            "FakePaths", (), {"user_config_dir": staticmethod(lambda: Path("/tmp/cfg"))}
        )
        with patch.dict("sys.modules", {"puripuly_heart.config.paths": fake_paths}):
            assert _get_log_dir() == Path("/tmp/cfg")

    def test_flet_log_handler_emit_success_and_error_path(self):
        class GoodView:
            def __init__(self):
                self.lines = []

            def append_log(self, line: str) -> None:
                self.lines.append(line)

        good = GoodView()
        handler = FletLogHandler(good)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        assert good.lines and "hello" in good.lines[0]

        class BadView:
            def append_log(self, _line: str) -> None:
                raise RuntimeError("fail")

        FletLogHandler(BadView()).emit(record)

    def test_attach_log_handler_idempotent(self):
        view = LogsView()
        added = []

        class DummyLogger:
            def addHandler(self, handler):
                added.append(handler)

        with patch.object(logs_module.logging, "getLogger", return_value=DummyLogger()):
            view.attach_log_handler()
            view.attach_log_handler()

        assert len(added) == 1

    @patch("time.time", side_effect=[0.0, 0.3, 0.4])
    def test_scroll_to_bottom_flushes_pending_and_awaits_scroll(self, _mock_time):
        view = LogsView()
        scrolled = {"called": False}

        async def fake_scroll_to(**_kwargs):
            scrolled["called"] = True

        view._log_scroll.scroll_to = fake_scroll_to
        with patch.object(type(view), "page", new_callable=PropertyMock, return_value=object()):
            with patch.object(type(view._log_text), "update", lambda self: None):
                view._pending_update = True
                view._log_buffer.append("line")
                asyncio.run(view.scroll_to_bottom())

        assert scrolled["called"] is True
