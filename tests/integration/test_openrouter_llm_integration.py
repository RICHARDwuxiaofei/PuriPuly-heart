from __future__ import annotations

import os

import pytest

from puripuly_heart.app.wiring import create_llm_provider
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterFallbackSelectionAlias,
    OpenRouterLLMModel,
    OpenRouterSelectionAlias,
)
from puripuly_heart.core.openrouter_credentials import OPENROUTER_MANAGED_API_KEY_SECRET
from puripuly_heart.core.storage.secrets import InMemorySecretStore
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from tests.integration.helpers import (
    integration_mark,
    require_env,
    run_llm_smoke,
    suppressed_runtime_logger,
)

pytestmark = integration_mark()


class FailFastManagedReleaseService:
    def __init__(self) -> None:
        self.accessed: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.accessed.append(name)
        raise AssertionError(f"managed release service unexpectedly used: {name}")


def fail_if_managed_delegate_ready() -> None:
    raise AssertionError("managed delegate-ready callback unexpectedly invoked")


@pytest.mark.asyncio
async def test_openrouter_byok_translation_smoke() -> None:
    api_key = require_env("OPENROUTER_API_KEY")

    provider = OpenRouterLLMProvider(
        api_key=api_key,
        model=os.getenv(
            "OPENROUTER_LLM_MODEL",
            OpenRouterLLMModel.GEMMA_4_26B_A4B_IT.value,
        ),
        runtime_logging=suppressed_runtime_logger(),
    )

    await run_llm_smoke(provider)


@pytest.mark.asyncio
async def test_openrouter_cached_managed_key_translation_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = require_env("OPENROUTER_TEST_CACHED_MANAGED_API_KEY")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.llm_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_MANAGED
    settings.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.NONE

    secrets = InMemorySecretStore()
    secrets.set(OPENROUTER_MANAGED_API_KEY_SECRET, api_key)

    managed_release_service = FailFastManagedReleaseService()
    provider = create_llm_provider(
        settings,
        secrets=secrets,
        managed_release_service=managed_release_service,
        managed_delegate_ready=fail_if_managed_delegate_ready,
        runtime_logging=suppressed_runtime_logger(),
    )

    await run_llm_smoke(provider)
    assert managed_release_service.accessed == []
