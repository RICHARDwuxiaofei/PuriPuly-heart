from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


@dataclass(slots=True)
class InMemorySecretStore:
    _items: dict[str, str]
    _lock: threading.RLock

    def __init__(self) -> None:
        self._items = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._items.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._items[key] = value

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def compare_and_clear(self, key: str, expected_revision: str) -> str:
        with self._lock:
            current = self._items.get(key)
            if current is None:
                return "absent"
            if _secret_revision(current) != expected_revision:
                return "stale"
            del self._items[key]
            return "cleared"


@dataclass(slots=True)
class KeyringSecretStore:
    service_name: str = "puripuly-heart"

    _locks_guard: ClassVar[threading.Lock] = threading.Lock()
    _locks: ClassVar[dict[tuple[str, str], threading.RLock]] = {}

    def _lock(self, key: str) -> threading.RLock:
        identity = (self.service_name, key)
        with self._locks_guard:
            return self._locks.setdefault(identity, threading.RLock())

    def _keyring(self):
        import keyring  # type: ignore

        return keyring

    def get(self, key: str) -> str | None:
        with self._lock(key):
            keyring = self._keyring()
            return keyring.get_password(self.service_name, key)

    def set(self, key: str, value: str) -> None:
        with self._lock(key):
            keyring = self._keyring()
            keyring.set_password(self.service_name, key, value)

    def delete(self, key: str) -> None:
        with self._lock(key):
            keyring = self._keyring()
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

    def compare_and_clear(self, key: str, expected_revision: str) -> str:
        with self._lock(key):
            current = self._keyring().get_password(self.service_name, key)
            if current is None:
                return "absent"
            if _secret_revision(current) != expected_revision:
                return "stale"
            self.delete(key)
            return "cleared"


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

        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            salt_b64 = raw["salt"]
            items = raw.get("items", {})
        else:
            salt = os.urandom(16)
            salt_b64 = base64.b64encode(salt).decode("ascii")
            items = {}
            _atomic_write_json(path, {"version": 1, "salt": salt_b64, "items": items})

        salt = base64.b64decode(salt_b64)
        key = _derive_key(passphrase=passphrase, salt=salt)
        self._fernet = Fernet(key)
        self._items = dict(items)

    def get(self, key: str) -> str | None:
        with _secret_path_lock(self.path):
            self._reload_items()
            return self._decrypt_value(key)

    def set(self, key: str, value: str) -> None:
        with _secret_path_lock(self.path):
            self._reload_items()
            token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
            self._items[key] = token
            self._save()

    def delete(self, key: str) -> None:
        with _secret_path_lock(self.path):
            self._reload_items()
            if key in self._items:
                del self._items[key]
                self._save()

    def compare_and_clear(self, key: str, expected_revision: str) -> str:
        with _secret_path_lock(self.path):
            self._reload_items()
            current = self._decrypt_value(key)
            if current is None:
                return "absent"
            if _secret_revision(current) != expected_revision:
                return "stale"
            del self._items[key]
            self._save()
            return "cleared"

    def _reload_items(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._items = dict(raw.get("items", {}))

    def _decrypt_value(self, key: str) -> str | None:
        token = self._items.get(key)
        if token is None:
            return None
        try:
            plaintext = self._fernet.decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise ValueError("invalid passphrase or corrupted secrets file") from exc
        return plaintext.decode("utf-8")

    def _save(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["items"] = self._items
        _atomic_write_json(self.path, raw)


def _derive_key(*, passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    key_bytes = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(key_bytes)


def _secret_revision(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_SECRET_PATH_LOCKS_GUARD = threading.Lock()
_SECRET_PATH_LOCKS: dict[Path, threading.RLock] = {}


@contextmanager
def _secret_path_lock(path: Path, *, timeout_s: float = 10.0) -> Iterator[None]:
    resolved = path.resolve()
    with _SECRET_PATH_LOCKS_GUARD:
        lock = _SECRET_PATH_LOCKS.setdefault(resolved, threading.RLock())
    with lock:
        lock_root = Path(tempfile.gettempdir()) / "puripuly-heart-secret-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(str(resolved).encode()).hexdigest()
        lock_path = lock_root / f"{lock_name}.lock"
        with lock_path.open("a+b") as handle:
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    _lock_secret_file(handle)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("secret store lock acquisition timed out")
                    time.sleep(0.01)
            try:
                yield
            finally:
                _unlock_secret_file(handle)


def _lock_secret_file(handle) -> None:  # noqa: ANN001
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_secret_file(handle) -> None:  # noqa: ANN001
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
