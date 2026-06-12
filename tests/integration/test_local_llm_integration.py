from __future__ import annotations

import os

import pytest

from puripuly_heart.providers.llm.local_openai import LocalOpenAICompatibleLLMProvider
from tests.integration.helpers import (
    integration_mark,
    require_local_llm_server,
    run_llm_smoke,
    suppressed_runtime_logger,
)

pytestmark = integration_mark()


@pytest.mark.asyncio
async def test_local_openai_compatible_translation_smoke() -> None:
    defaults = LocalOpenAICompatibleLLMProvider()
    base_url = os.getenv("LOCAL_LLM_BASE_URL", defaults.base_url)
    model = os.getenv("LOCAL_LLM_MODEL", defaults.model)
    api_key = os.getenv("LOCAL_LLM_API_KEY", "")

    await require_local_llm_server(base_url=base_url, model=model, api_key=api_key)

    provider = LocalOpenAICompatibleLLMProvider(
        base_url=base_url,
        model=model,
        api_key=api_key,
        runtime_logging=suppressed_runtime_logger(),
    )

    await run_llm_smoke(provider)
