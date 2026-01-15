from __future__ import annotations

from pathlib import Path

from puripuly_heart.config import paths


def test_user_config_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert paths.user_config_dir(app_dir_name="app") == tmp_path / "app"


def test_default_paths_use_user_config_dir(monkeypatch, tmp_path):
    def _fake_user_config_dir(*, app_dir_name: str = paths.APP_DIR_NAME) -> Path:
        return tmp_path / app_dir_name

    monkeypatch.setattr(paths, "user_config_dir", _fake_user_config_dir)

    assert paths.default_settings_path() == tmp_path / paths.APP_DIR_NAME / paths.SETTINGS_FILENAME
    assert (
        paths.default_vad_model_path() == tmp_path / paths.APP_DIR_NAME / paths.VAD_MODEL_FILENAME
    )
