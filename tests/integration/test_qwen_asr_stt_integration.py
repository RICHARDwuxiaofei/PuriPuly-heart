from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("INTEGRATION") != "1", reason="set INTEGRATION=1 to run integration tests"
)


@pytest.mark.asyncio
async def test_qwen_asr_realtime_streaming_smoke():
    api_key = os.getenv("ALIBABA_API_KEY")
    if not api_key:
        pytest.skip("missing env var ALIBABA_API_KEY")

    try:
        import dashscope  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "dashscope is required for this integration test; install project dependencies."
        ) from exc

    from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend

    region = os.getenv("QWEN_REGION", "beijing").lower()
    default_endpoint = (
        "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
        if region == "singapore"
        else "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    )

    backend = QwenASRRealtimeSTTBackend(
        api_key=api_key,
        model=os.getenv("QWEN_ASR_MODEL", "qwen3-asr-flash-realtime"),
        endpoint=os.getenv("QWEN_ASR_ENDPOINT", default_endpoint),
        language=os.getenv("QWEN_ASR_LANGUAGE", "ko"),
        sample_rate_hz=int(os.getenv("QWEN_ASR_SAMPLE_RATE", "16000")),
    )

    session = await backend.open_session()

    # Send a short silence stream just to validate connectivity/stream lifecycle.
    silence = b"\0" * 1024
    for _ in range(10):
        await session.send_audio(silence)
        await asyncio.sleep(0.032)

    await session.stop()

    async def _drain():
        async for _ev in session.events():
            pass

    await asyncio.wait_for(_drain(), timeout=30.0)
    await asyncio.wait_for(session.close(), timeout=5.0)
