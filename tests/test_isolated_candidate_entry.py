import json
import os
import runpy
import sys
from pathlib import Path
from types import ModuleType


def test_candidate_entry_keeps_configuration_and_keyring_profile_isolated(tmp_path, monkeypatch):
    original = Path(__file__).resolve().parents[1] / "scripts/preview/entry.py"
    payload = tmp_path / "payload"
    app = payload / "app"
    app.mkdir(parents=True)
    entry = app / "preview_entry.py"
    entry.write_bytes(original.read_bytes())
    (payload / "ISOLATED-PROFILE.json").write_text(json.dumps({"name": "readiness-20261018"}))
    seen = []
    fake = ModuleType("puripuly_heart.main")
    fake.main = lambda: seen.append(
        (os.environ["PURIPULY_HEART_TEST_PROFILE"], os.environ["PURIPULY_HEART_TEST_DATA_DIR"])
    )
    monkeypatch.setitem(sys.modules, "puripuly_heart.main", fake)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(tmp_path)
    for name in (
        "PURIPULY_HEART_TEST_PROFILE",
        "PURIPULY_HEART_TEST_DATA_DIR",
        "PYTHONPATH",
        "FLET_VIEW_PATH",
        "FLET_SERVER_IP",
        "PYTHONIOENCODING",
        "PYTHONNOUSERSITE",
    ):
        monkeypatch.setenv(name, "test-previous")
    monkeypatch.setenv("PURIPULY_HEART_BETA_DISTRIBUTION", "1")
    monkeypatch.setenv("PURIPULY_HEART_WRIST_DEVELOPMENT", "1")
    runpy.run_path(str(entry), run_name="__main__")
    assert seen == [("readiness-20261018", str(payload / "isolated-data"))]
    from puripuly_heart.app.wiring.wiring_secrets_factory import _get_secret, create_secret_store
    from puripuly_heart.config.settings_vnext.schema import SecretsIntent

    store = create_secret_store(
        SecretsIntent(), config_path=payload / "isolated-data/settings.json"
    )
    assert store.service_name == "puripuly-heart.test.readiness-20261018"

    class Empty:
        def get(self, name):
            return None

    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-environment-key")
    assert _get_secret(Empty(), key="deepseek_api_key", env_var="DEEPSEEK_API_KEY") is None
