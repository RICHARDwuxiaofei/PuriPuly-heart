import asyncio
from dataclasses import replace

import httpx

from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner
from puripuly_heart.composition.headless_application import compose_headless_application
from puripuly_heart.core import network_clients
from puripuly_heart.diagnostics.qq_auth_probe import ControlledBroker


async def test_full_headless_application_auth_uses_real_storage_and_broker(tmp_path, monkeypatch):
    monkeypatch.setenv("PURIPULY_HEART_TEST_PROFILE", "qq-auth-beta-offline-full-app")
    monkeypatch.setenv("PURIPULY_HEART_TEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "synthetic-full-app-only")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    real_factory = network_clients.external_async_client

    class LoopbackOnly(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.AsyncHTTPTransport(trust_env=False)

        async def handle_async_request(self, request):
            if request.url.host != "127.0.0.1":
                raise httpx.ConnectError("offline test refuses external traffic", request=request)
            return await self.inner.handle_async_request(request)

        async def aclose(self):
            await self.inner.aclose()

    def guarded_factory(**kwargs):
        kwargs["transport"] = LoopbackOnly()
        return real_factory(**kwargs)

    monkeypatch.setattr(network_clients, "external_async_client", guarded_factory)
    with ControlledBroker() as broker:
        owner = compose_settings_owner(tmp_path / "settings.json")
        settings = owner.start().settings
        owner.canonical = replace(
            settings,
            intent=replace(
                settings.intent,
                secrets=replace(settings.intent.secrets, backend="encrypted_file"),
                translation=replace(
                    settings.intent.translation,
                    connection="managed_china",
                    model="deepseek_v4_flash_41",
                    openrouter_broker_base_url=broker.url,
                    openrouter_selected_source="managed",
                ),
            ),
        )
        owner.persist()
        app = compose_headless_application(owner.path)
        try:
            await asyncio.wait_for(app.start(), 25)
            result = await asyncio.wait_for(
                app.start_qq_managed_auth_from_dialog(qq_identity="100001", credential="a" * 64), 15
            )
            assert result is True
            assert broker.assert_count == 1
            assert broker.ack_count == 1
            persisted = owner.persistence.load_active(owner.path).canonical_settings
            assert (
                persisted.state.managed_connection.active_managed_credential_ref
                == "local-reference"
            )
            assert persisted.state.managed_connection.pending_managed_operation_id is None
        finally:
            await app.stop()
