import asyncio
import io

import httpx
import pytest
from PIL import Image

from puripuly_heart.core.vision.translation import (
    VisionError,
    VisionTranslationService,
    parse_blocks,
    prepare_image,
)


def picture():
    output = io.BytesIO()
    Image.new("RGB", (64, 32), "white").save(output, format="PNG")
    return output.getvalue()


def answer():
    return {
        "choices": [
            {
                "message": {
                    "content": '{"blocks":[{"id":"a","original":"Hello","translation":"你好"}]}'
                },
                "finish_reason": "stop",
            }
        ]
    }


async def call(service):
    return await service.translate(
        picture(), key="fake-test-key", model="deepseek-flash", target_language="zh-CN", timeout=5
    )


@pytest.mark.asyncio
async def test_request_uses_mixed_content_and_current_model_once():
    requests = []

    def handle(request):
        import json

        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        return httpx.Response(200, json=answer())

    service = VisionTranslationService(
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    result = await call(service)
    assert result[0].translation == "你好"
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "key_rejected"),
        (403, "access_denied"),
        (429, "rate_limited"),
        (503, "service_unavailable"),
    ],
)
async def test_no_automatic_retry_or_sensitive_response(status, code, caplog):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="private-key-secret-image-data")

    service = VisionTranslationService(
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    with pytest.raises(VisionError) as caught:
        await call(service)
    assert caught.value.code == code
    assert caught.value.status == status
    service.report(caught.value)
    assert len(calls) == 1
    assert "private-key" not in caplog.text
    assert "fake-test-key" not in caplog.text
    assert "base64" not in caplog.text


@pytest.mark.asyncio
async def test_cancel_request_closes_owned_client():
    entered = asyncio.Event()

    async def handle(request):
        entered.set()
        await asyncio.Event().wait()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    task = asyncio.create_task(call(VisionTranslationService(client_factory=lambda **kw: client)))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.is_closed


@pytest.mark.asyncio
async def test_network_timeout_is_classified():
    def handle(request):
        raise httpx.ReadTimeout("must-not-log-raw-message")

    service = VisionTranslationService(
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    with pytest.raises(VisionError, match="request_timeout"):
        await call(service)


@pytest.mark.parametrize(
    "payload,code",
    [
        ({}, "invalid_response"),
        ({"choices": [{"message": {"content": "not json"}}]}, "invalid_response"),
        ({"choices": [{"message": {"content": ""}}]}, "empty_response"),
        ({"choices": [{"message": {"refusal": "No"}}]}, "model_refused"),
        ({"choices": [{"message": {"content": '{"blocks":[]}'}}]}, "no_text_found"),
    ],
)
def test_invalid_results(payload, code):
    with pytest.raises(VisionError, match=code):
        parse_blocks(payload)


def test_invalid_image_is_not_sent():
    with pytest.raises(VisionError):
        prepare_image(b"not an image")
