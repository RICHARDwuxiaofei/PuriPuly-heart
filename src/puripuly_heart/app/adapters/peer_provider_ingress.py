from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.runtime.peer_channel import PeerProviderReleaseOutcome


@dataclass(frozen=True, slots=True)
class HubPeerProviderIngressAdapter:
    hub: ClientHub

    async def ensure_peer_provider_ingress(self, provider: object) -> None:
        await self.hub.start_peer_stt_provider_ingress(provider)

    async def release_peer_provider_ingress(self, provider: object) -> PeerProviderReleaseOutcome:
        try:
            await self.hub.release_peer_stt_provider_ingress(provider)
        except BaseException as exc:
            return PeerProviderReleaseOutcome(exc)
        return PeerProviderReleaseOutcome()
