from __future__ import annotations

import os

import pytest

from puripuly_heart.providers.llm.deepseek import DeepSeekLLMProvider
from tests.integration.helpers import (
    integration_mark,
    require_env,
    run_llm_smoke,
    suppressed_runtime_logger,
)

pytestmark = integration_mark()


@pytest.mark.asyncio
async def test_deepseek_direct_translation_smoke() -> None:
    api_key = require_env("DEEPSEEK_API_KEY")
    model = os.getenv("DEEPSEEK_LLM_MODEL")

    provider_kwargs: dict[str, object] = {
        "api_key": api_key,
        "runtime_logging": suppressed_runtime_logger(),
    }
    if model:
        provider_kwargs["model"] = model

    provider = DeepSeekLLMProvider(**provider_kwargs)

    await run_llm_smoke(provider)
