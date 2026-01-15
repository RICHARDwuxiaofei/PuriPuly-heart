from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from puripuly_heart.providers.llm.qwen import DashScopeQwenClient


class DummyGeneration:
    last_call: dict | None = None

    @classmethod
    def call(cls, *_, **kwargs):
        cls.last_call = kwargs

        class Response:
            status_code = 200
            output = {"choices": [{"message": {"content": "OK"}}]}

        return Response()


@pytest.mark.asyncio
async def test_qwen_client_builds_prompt_with_context(monkeypatch) -> None:
    dummy = SimpleNamespace(api_key=None, base_http_api_url=None, Generation=DummyGeneration)
    monkeypatch.setitem(sys.modules, "dashscope", dummy)

    client = DashScopeQwenClient(api_key="key", model="qwen-mt-flash")
    context_pairs = [{"source": "hello", "target": "안녕"}]
    result = await client.translate(
        text="hello",
        source_language="ko",
        target_language="en",
        domain_prompt="PROMPT",
        context_pairs=context_pairs,
    )

    assert result == "OK"
    assert DummyGeneration.last_call is not None
    assert DummyGeneration.last_call.get("messages") == [{"role": "user", "content": "hello"}]
    assert DummyGeneration.last_call.get("translation_options") == {
        "source_lang": "ko",
        "target_lang": "en",
        "domains": "PROMPT",
        "tm_list": context_pairs,
    }


@pytest.mark.asyncio
async def test_qwen_client_builds_prompt_without_context(monkeypatch) -> None:
    dummy = SimpleNamespace(api_key=None, base_http_api_url=None, Generation=DummyGeneration)
    monkeypatch.setitem(sys.modules, "dashscope", dummy)

    client = DashScopeQwenClient(api_key="key", model="qwen-mt-flash")
    result = await client.translate(
        text="hello",
        source_language="ko",
        target_language="en",
        domain_prompt="",
        context_pairs=None,
    )

    assert result == "OK"
    assert DummyGeneration.last_call is not None
    assert DummyGeneration.last_call.get("messages") == [{"role": "user", "content": "hello"}]
    assert DummyGeneration.last_call.get("translation_options") == {
        "source_lang": "ko",
        "target_lang": "en",
    }
