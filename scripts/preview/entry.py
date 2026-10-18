from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
for name in tuple(os.environ):
    if (
        name.startswith(("PURIPULY_HEART_NATIVE_", "FLET_DART_BRIDGE"))
        or name == "PURIPULY_HEART_LLAMA_CPP_ROOT"
    ):
        os.environ.pop(name, None)
sys.path.insert(0, str(root / "src"))
os.environ["PYTHONPATH"] = str(root / "src")
beta = os.environ.get("PURIPULY_HEART_BETA_DISTRIBUTION") == "1"
wrist = os.environ.get("PURIPULY_HEART_WRIST_DEVELOPMENT") == "1"
os.environ["PURIPULY_HEART_TEST_PROFILE"] = "desktop-beta" if beta else "desktop-preview"
os.environ["PURIPULY_HEART_TEST_DATA_DIR"] = str(
    Path(os.environ.get("LOCALAPPDATA", root.parent)) / "PuriPulyHeartBeta"
    if beta
    else root.parent / "preview-data"
)
if wrist:
    os.environ["PURIPULY_HEART_TEST_PROFILE"] = "desktop-wrist-vision"
    os.environ["PURIPULY_HEART_TEST_DATA_DIR"] = str(root.parent / "wrist-development-data")
profile_file = root.parent / "ISOLATED-PROFILE.json"
if profile_file.is_file():
    profile = json.loads(profile_file.read_text(encoding="utf-8"))["name"]
    if (
        not isinstance(profile, str)
        or not profile
        or not profile.isascii()
        or not profile.replace("-", "").isalnum()
        or len(profile) > 64
    ):
        raise ValueError("Invalid isolated preview profile")
    os.environ["PURIPULY_HEART_TEST_PROFILE"] = profile
    os.environ["PURIPULY_HEART_TEST_DATA_DIR"] = str(root.parent / "isolated-data")
os.environ["FLET_VIEW_PATH"] = str(root.parent / "flet" / "flet")
os.environ["FLET_SERVER_IP"] = "127.0.0.1"
os.environ.pop("FLET_FORCE_WEB_SERVER", None)
os.environ.pop("FLET_SERVER_PORT", None)
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONNOUSERSITE"] = "1"
os.chdir(root)

from puripuly_heart.main import main

try:
    main()
except Exception as error:
    ctypes.windll.user32.MessageBoxW(
        None,
        f"测试程序无法启动：{type(error).__name__}。请查看 preview-data 日志。",
        "PuriPuly Preview",
        16,
    )
    raise
