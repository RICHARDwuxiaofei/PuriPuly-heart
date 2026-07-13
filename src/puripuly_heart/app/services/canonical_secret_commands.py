from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass

from puripuly_heart.app.ports.application_settings import (
    ClearSecretCommand,
    SecretCommandPort,
    SecretMetadata,
    SecretMetadataQuery,
    SecretQueryPort,
    SecretSourceStatus,
    SecretVerificationStatus,
    SetSecretCommand,
)
from puripuly_heart.app.ports.secret_store import SecretStorePort
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
)

_SECRET_KEY_FALLBACKS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "google_api_key": ((), ("GOOGLE_API_KEY",)),
    "openrouter_api_key": ((), ("OPENROUTER_API_KEY",)),
    "deepseek_api_key": ((), ("DEEPSEEK_API_KEY",)),
    "deepgram_api_key": ((), ("DEEPGRAM_API_KEY",)),
    "soniox_api_key": ((), ("SONIOX_API_KEY",)),
    "cerebras_api_key": ((), ("CEREBRAS_API_KEY",)),
    "alibaba_api_key_beijing": (
        ("alibaba_api_key",),
        ("ALIBABA_API_KEY_BEIJING", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
    ),
    "alibaba_api_key_singapore": (
        ("alibaba_api_key",),
        ("ALIBABA_API_KEY_SINGAPORE", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
    ),
    "alibaba_api_key": (
        (),
        ("ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
    ),
}
UI_SETTINGS_SECRET_KEYS = (
    "google_api_key",
    "openrouter_api_key",
    "deepseek_api_key",
    "deepgram_api_key",
    "soniox_api_key",
    "alibaba_api_key_beijing",
    "alibaba_api_key_singapore",
    "alibaba_api_key",
    "local_llm_api_key",
    "cerebras_api_key",
)


def _fingerprint_revision(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_for_store(store: object) -> SecretSourceStatus:
    if isinstance(store, EncryptedFileSecretStore):
        return SecretSourceStatus.ENCRYPTED_FILE
    if isinstance(store, KeyringSecretStore):
        return SecretSourceStatus.KEYRING
    name = type(store).__name__.lower()
    if "encrypted" in name:
        return SecretSourceStatus.ENCRYPTED_FILE
    if "keyring" in name:
        return SecretSourceStatus.KEYRING
    if "environment" in name or "env" in name:
        return SecretSourceStatus.ENVIRONMENT
    return SecretSourceStatus.KEYRING


def _metadata(
    *,
    key: str,
    present: bool,
    revision: str | None,
    source: SecretSourceStatus,
) -> SecretMetadata:
    return SecretMetadata(
        key=key,
        present=present,
        revision=revision,
        verification=SecretVerificationStatus.UNKNOWN,
        source=source if present else SecretSourceStatus.NONE,
    )


@dataclass(slots=True)
class CanonicalSecretCommandService(SecretCommandPort, SecretQueryPort):
    secret_store: SecretStorePort
    store_kind: object | None = None

    def _store_source(self) -> SecretSourceStatus:
        if self.store_kind is not None:
            return _source_for_store(self.store_kind)
        return SecretSourceStatus.KEYRING

    def _fallbacks(self, key: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return _SECRET_KEY_FALLBACKS.get(key, ((), ()))

    async def set_secret(self, command: SetSecretCommand) -> SecretMetadata:
        if not isinstance(command.key, str) or not command.key:
            raise ValueError("secret key must be non-empty text")
        if not isinstance(command.value, str) or not command.value:
            raise ValueError("secret value must be non-empty text")
        write = await self.secret_store.set_secret(command.key, command.value)
        if not write.succeeded:
            raise RuntimeError("secret write failed")
        revision = write.revision or _fingerprint_revision(command.value)
        return _metadata(
            key=command.key,
            present=True,
            revision=revision,
            source=self._store_source(),
        )

    async def clear_secret(self, command: ClearSecretCommand) -> SecretMetadata:
        if not isinstance(command.key, str) or not command.key:
            raise ValueError("secret key must be non-empty text")
        write = await self.secret_store.clear_secret(command.key)
        if not write.succeeded:
            raise RuntimeError("secret clear failed")
        remaining = await self.secret_metadata(SecretMetadataQuery(command.key))
        if remaining.present:
            return remaining
        return _metadata(
            key=command.key,
            present=False,
            revision=None,
            source=SecretSourceStatus.NONE,
        )

    async def secret_metadata(self, query: SecretMetadataQuery) -> SecretMetadata:
        if not isinstance(query.key, str) or not query.key:
            raise ValueError("secret key must be non-empty text")
        read = await self.secret_store.get_secret(query.key)
        if read.value:
            revision = read.revision or _fingerprint_revision(read.value)
            return _metadata(
                key=query.key,
                present=True,
                revision=revision,
                source=self._store_source(),
            )
        legacy_keys, env_vars = self._fallbacks(query.key)
        for legacy_key in legacy_keys:
            legacy = await self.secret_store.get_secret(legacy_key)
            if legacy.value:
                revision = legacy.revision or _fingerprint_revision(legacy.value)
                return _metadata(
                    key=query.key,
                    present=True,
                    revision=revision,
                    source=self._store_source(),
                )
        for env_var in env_vars:
            env_value = os.getenv(env_var)
            if env_value:
                return _metadata(
                    key=query.key,
                    present=True,
                    revision=_fingerprint_revision(env_value),
                    source=SecretSourceStatus.ENVIRONMENT,
                )
        return _metadata(
            key=query.key,
            present=False,
            revision=None,
            source=SecretSourceStatus.NONE,
        )

    async def resolve_secret_value(self, key: str) -> str | None:
        read = await self.secret_store.get_secret(key)
        if read.value:
            return read.value
        legacy_keys, env_vars = self._fallbacks(key)
        for legacy_key in legacy_keys:
            legacy = await self.secret_store.get_secret(legacy_key)
            if legacy.value:
                return legacy.value
        for env_var in env_vars:
            value = os.getenv(env_var)
            if value:
                return value
        return None


@dataclass(slots=True)
class SyncSecretStorePortAdapter:
    store: object

    async def get_secret(self, key: str):
        from puripuly_heart.app.ports.secret_store import SecretReadResult

        value = await asyncio.to_thread(self.store.get, key)
        revision = _fingerprint_revision(value) if value is not None else None
        return SecretReadResult(key, value, revision, None, None)

    async def set_secret(self, key: str, value: str):
        from puripuly_heart.app.ports.secret_store import SecretWriteResult

        await asyncio.to_thread(self.store.set, key, value)
        return SecretWriteResult(True, key, _fingerprint_revision(value), None, None)

    async def clear_secret(self, key: str):
        from puripuly_heart.app.ports.secret_store import SecretWriteResult

        await asyncio.to_thread(self.store.delete, key)
        return SecretWriteResult(True, key, None, None, None)

    async def snapshot_secret(self, key: str):
        from puripuly_heart.app.ports.secret_store import SecretSnapshot

        value = await asyncio.to_thread(self.store.get, key)
        revision = _fingerprint_revision(value) if value is not None else None
        return SecretSnapshot(key, value, revision, value is not None)

    async def restore_secret(self, snapshot):
        from puripuly_heart.app.ports.secret_store import SecretWriteResult

        if snapshot.existed and snapshot.value is not None:
            await asyncio.to_thread(self.store.set, snapshot.key, snapshot.value)
            revision = _fingerprint_revision(snapshot.value)
        else:
            await asyncio.to_thread(self.store.delete, snapshot.key)
            revision = None
        return SecretWriteResult(True, snapshot.key, revision, None, None)

    async def compare_and_clear_secret(self, key: str, expected_revision: str):
        from puripuly_heart.app.ports.secret_store import SecretCompareAndClearResult

        compare_and_clear = getattr(self.store, "compare_and_clear", None)
        if not callable(compare_and_clear):
            raise RuntimeError("secret store compare-and-clear is unavailable")
        status = await asyncio.to_thread(compare_and_clear, key, expected_revision)
        return SecretCompareAndClearResult(status, key, expected_revision)


__all__ = [
    "CanonicalSecretCommandService",
    "SyncSecretStorePortAdapter",
]
