"""Managed-identity state port for read/mutate/persist contracts.

Core runtime services consume this port instead of importing ``AppSettings``
directly. The port exposes the persisted managed-identity fields as read/write
attributes plus a ``persist`` method. Adapter implementations that bridge to
``AppSettings`` live at the wiring/controller boundary, never in core code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ManagedIdentitySnapshot:
    """Frozen snapshot of managed-identity state used for rollback safety."""

    installation_id: str
    release_token: str | None
    release_token_expires_at: str | None
    verified_hardware_hash: str | None
    verified_hardware_hash_salt_version: int | None
    active_managed_credential_ref: str | None
    active_managed_expires_at: str | None
    founder_letter_seen_credential_ref: str | None
    referral_id: str | None


class ManagedIdentityStatePort(Protocol):
    """Read/write/persist contract for persisted managed-identity state.

    Implementations must proxy attribute reads and writes to the underlying
    persisted state owner so that mutations are visible to subsequent reads
    before ``persist`` is called. ``persist`` must durably save the current
    state. The port must not expose ``AppSettings`` in its interface.
    """

    installation_id: str
    release_token: str | None
    release_token_expires_at: str | None
    verified_hardware_hash: str | None
    verified_hardware_hash_salt_version: int | None
    active_managed_credential_ref: str | None
    active_managed_expires_at: str | None
    founder_letter_seen_credential_ref: str | None
    referral_id: str | None

    def persist(self) -> None: ...

    def snapshot(self) -> ManagedIdentitySnapshot: ...

    def restore(self, snapshot: ManagedIdentitySnapshot) -> None: ...


__all__ = [
    "ManagedIdentitySnapshot",
    "ManagedIdentityStatePort",
]
