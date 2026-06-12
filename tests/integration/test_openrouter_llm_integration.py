from __future__ import annotations

import os

import pytest

from puripuly_heart.config.settings import OpenRouterLLMModel
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from tests.integration.helpers import (
    integration_mark,
    require_env,
    run_llm_smoke,
    suppressed_runtime_logger,
)

pytestmark = integration_mark()


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
