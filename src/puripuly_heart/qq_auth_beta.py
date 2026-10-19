from __future__ import annotations

import json
import os
import stat
from pathlib import Path

CHANNEL = "PuriPuly Heart QQ Auth Beta"
APP_ID = "PuriPulyHeart-Fork-QQ-Auth-Beta"
DATA_NAME = "PuriPulyHeartQQAuthBeta"
PROFILE = "qq-auth-beta"
NAMESPACE = "puripuly-heart.test.qq-auth-beta"
MARKER = ".qq-auth-beta-owned"


def has_reparse(path: Path) -> bool:
    for current in (path, *path.parents):
        if current.exists() or current.is_symlink():
            if current.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return True
    return False


def configure_channel() -> Path:
    root = Path(os.environ["LOCALAPPDATA"]) / DATA_NAME
    if has_reparse(root):
        raise ValueError("QQ Auth Beta data path contains a link")
    if root.exists() and any(root.iterdir()) and not (root / MARKER).is_file():
        raise ValueError("QQ Auth Beta data directory ownership cannot be confirmed")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if marker.exists() and marker.read_text(encoding="utf-8") != APP_ID:
        raise ValueError("QQ Auth Beta data directory identity mismatch")
    marker.write_text(APP_ID, encoding="utf-8")
    os.environ["PURIPULY_HEART_TEST_PROFILE"] = PROFILE
    os.environ["PURIPULY_HEART_TEST_DATA_DIR"] = str(root)
    os.environ["FLET_APP_USER_MODEL_ID"] = "RICHARDwuxiaofei.PuriPulyHeart.QQAuthBeta"
    os.environ["FLET_APP_NAME"] = CHANNEL
    os.environ["HF_HOME"] = str(root / "cache" / "huggingface")
    os.environ["HF_HUB_CACHE"] = str(root / "cache" / "huggingface" / "hub")
    os.environ["HF_XET_CACHE"] = str(root / "cache" / "huggingface" / "xet")
    os.environ["HUGGINGFACE_HUB_CACHE"] = os.environ["HF_HUB_CACHE"]
    return root


def cleanup_data() -> dict:
    root = Path(os.environ["LOCALAPPDATA"]) / DATA_NAME
    if not root.is_dir():
        return {"status": "absent"}
    if (
        has_reparse(root)
        or not (root / MARKER).is_file()
        or (root / MARKER).read_text(encoding="utf-8") != APP_ID
    ):
        raise ValueError("Refusing cleanup: QQ Auth Beta ownership is unconfirmed")
    entries = list(root.rglob("*"))
    if any(has_reparse(entry) for entry in entries):
        raise ValueError("Refusing cleanup: linked data must be reviewed manually")
    import keyring

    removed = 0
    inventory = root / "credential-keys.json"
    keys = json.loads(inventory.read_text(encoding="utf-8")) if inventory.is_file() else []
    if not isinstance(keys, list) or any(
        not isinstance(key, str)
        or not key.isascii()
        or not key.replace("_", "").isalnum()
        or len(key) > 128
        for key in keys
    ):
        raise ValueError("Refusing cleanup: invalid credential inventory")
    for key in keys:
        try:
            keyring.delete_password(NAMESPACE, key)
            removed += 1
        except keyring.errors.PasswordDeleteError:
            pass
    for entry in sorted(entries, key=lambda p: len(p.parts), reverse=True):
        if entry.is_dir():
            entry.rmdir()
        else:
            entry.unlink()
    root.rmdir()
    return {"status": "removed", "namespace": NAMESPACE, "credential_items_removed": removed}
