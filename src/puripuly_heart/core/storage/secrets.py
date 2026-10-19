from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_file_locks: dict[str, threading.RLock] = {}
_file_locks_guard = threading.Lock()


def _file_lock(path: Path) -> threading.RLock:
    with _file_locks_guard:
        return _file_locks.setdefault(str(path.resolve()).casefold(), threading.RLock())


def _keyring_lock(service: str) -> threading.RLock:
    with _file_locks_guard:
        return _file_locks.setdefault("keyring:" + service, threading.RLock())


def _load_or_initialize(path: Path) -> dict:
    with _file_lock(path):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raw = {"version": 1, "salt": base64.b64encode(os.urandom(16)).decode("ascii"), "items": {}}
        _atomic_write_json(path, raw)
        return raw


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


@dataclass(slots=True)
class InMemorySecretStore:
    _items: dict[str, str]

    def __init__(self) -> None:
        self._items = {}

    def get(self, key: str) -> str | None:
        return self._items.get(key)

    def set(self, key: str, value: str) -> None:
        self._items[key] = value

    def delete(self, key: str) -> None:
        self._items.pop(key, None)


@dataclass(slots=True)
class KeyringSecretStore:
    service_name: str = "puripuly-heart"

    def _keyring(self):
        import keyring  # type: ignore

        return keyring

    def get(self, key: str) -> str | None:
        keyring = self._keyring()
        with _keyring_lock(self.service_name):
            return keyring.get_password(self.service_name, key)

    def set(self, key: str, value: str) -> None:
        if self.service_name == "puripuly-heart.test.qq-auth-beta":
            from puripuly_heart.config.paths import user_config_dir

            inventory = user_config_dir() / "credential-keys.json"
            with _file_lock(inventory):
                known = (
                    json.loads(inventory.read_text(encoding="utf-8")) if inventory.exists() else []
                )
                if key not in known:
                    _atomic_write_json(inventory, sorted([*known, key]))
        keyring = self._keyring()
        with _keyring_lock(self.service_name):
            keyring.set_password(self.service_name, key, value)

    def delete(self, key: str) -> None:
        keyring = self._keyring()
        with _keyring_lock(self.service_name):
            self._delete_keyring_item(keyring, key)

    def _delete_keyring_item(self, keyring, key: str) -> None:
        try:
            keyring.delete_password(self.service_name, key)
        except Exception as exc:
            errors = getattr(keyring, "errors", None)
            password_delete_error = getattr(errors, "PasswordDeleteError", None)
            if password_delete_error is not None and isinstance(exc, password_delete_error):
                if keyring.get_password(self.service_name, key) is None:
                    return
                raise
            raise


def mask_secret(value: str, *, unmasked_prefix: int = 3) -> str:
    if not value:
        return value
    if len(value) <= unmasked_prefix:
        return "*" * len(value)
    return value[:unmasked_prefix] + "****"


@dataclass(slots=True)
class EncryptedFileSecretStore:
    path: Path
    _fernet: Fernet
    _items: dict[str, str]

    def __init__(self, path: Path, *, passphrase: str) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

        raw = _load_or_initialize(path)
        salt_b64 = raw["salt"]
        items = raw.get("items", {})

        salt = base64.b64decode(salt_b64)
        key = _derive_key(passphrase=passphrase, salt=salt)
        self._fernet = Fernet(key)
        self._items = dict(items)

    def get(self, key: str) -> str | None:
        with _file_lock(self.path):
            self._items = dict(json.loads(self.path.read_text(encoding="utf-8")).get("items", {}))
            token = self._items.get(key)
        if token is None:
            return None
        try:
            plaintext = self._fernet.decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise ValueError("invalid passphrase or corrupted secrets file") from exc
        return plaintext.decode("utf-8")

    def set(self, key: str, value: str) -> None:
        token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        with _file_lock(self.path):
            self._items = dict(json.loads(self.path.read_text(encoding="utf-8")).get("items", {}))
            self._items[key] = token
            self._save()

    def delete(self, key: str) -> None:
        with _file_lock(self.path):
            self._items = dict(json.loads(self.path.read_text(encoding="utf-8")).get("items", {}))
            if key in self._items:
                del self._items[key]
                self._save()

    def _save(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["items"] = self._items
        _atomic_write_json(self.path, raw)


def _derive_key(*, passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    key_bytes = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(key_bytes)


def _atomic_write_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
