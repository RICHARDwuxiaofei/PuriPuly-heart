from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import httpx

from puripuly_heart.domain.models import Translation

logger = logging.getLogger(__name__)
_QWEN_PROBE_MODEL = "qwen3.5-plus"


def _build_system_prompt(
    *,
    system_prompt: str,
    source_language: str,
    target_language: str,
) -> str:
    formatted = (
        system_prompt.format(
            source_language=source_language,
            target_language=target_language,
        )
        if "{source_language}" in system_prompt
        else system_prompt
    )
    return formatted


def _build_user_message(*, text: str, context: str) -> str:
    if context:
        return f"<context>\n{context}\n</context>\nInput: {text}"
    return text


def _extract_message_content(content: object) -> str:
    if isinstance(content, str):
        result = content.strip()
        if result:
            return result
        raise RuntimeError("DashScope response contained empty message content")

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts)

    raise RuntimeError("DashScope response did not contain message content")


class AsyncQwenClient(Protocol):
    async def translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> str: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class AsyncQwenLLMProvider:
    """httpx 기반 비동기 Qwen 클라이언트 (저지연 모드용)

    DashScope OpenAI 호환 API를 사용하여 즉시 취소 가능한 번역을 제공합니다.
    """

    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3.5-plus"
    timeout: float = 30.0
    client: AsyncQwenClient | None = None
    _internal_client: AsyncQwenClient | None = field(init=False, default=None, repr=False)

    def _get_client(self) -> AsyncQwenClient:
        if self.client is not None:
            return self.client
        if self._internal_client is None:
            self._internal_client = HttpxQwenClient(
                api_key=self.api_key,
                model=self.model,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._internal_client

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        client = self._get_client()
        translated = await client.translate(
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )
        return Translation(utterance_id=utterance_id, text=translated)

    async def close(self) -> None:
        if self._internal_client is not None:
            await self._internal_client.close()
            self._internal_client = None

    async def warmup(self) -> None:
        # Warmup probes the default model.
        await self.verify_api_key(self.api_key, base_url=self.base_url, model=_QWEN_PROBE_MODEL)

    @staticmethod
    async def verify_api_key(
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = _QWEN_PROBE_MODEL,
    ) -> bool:
        if not api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "enable_thinking": False,
                        "max_tokens": 1,
                    },
                )
                return response.status_code == 200
        except Exception:
            return False


@dataclass(slots=True)
class HttpxQwenClient:
    """httpx를 사용하는 DashScope OpenAI 호환 클라이언트"""

    api_key: str
    model: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout: float = 30.0
    _client: httpx.AsyncClient | None = field(init=False, default=None, repr=False)

    @staticmethod
    def _normalize_language_code(code: str) -> str:
        if not code:
            return "auto"
        normalized = code.lower()
        if normalized in {"auto"}:
            return "auto"
        if normalized in {"zh-cn", "zh-hans", "zh"}:
            return "zh"
        if normalized in {"zh-tw", "zh-hant", "zh_tw"}:
            return "zh_tw"
        return normalized.split("-")[0]

    async def translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> str:
        if context:
            logger.info(
                f"[LLM] Request with context: '{text}' -> {source_language} to {target_language}"
            )
        else:
            logger.info(f"[LLM] Request: '{text}' -> {source_language} to {target_language}")

        system_content = _build_system_prompt(
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
        )
        user_message = _build_user_message(text=text, context=context)

        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message},
            ],
            "enable_thinking": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
            response.raise_for_status()

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("DashScope response did not contain choices")

        message = choices[0].get("message", {})
        result = _extract_message_content(message.get("content"))
        logger.info(f"[LLM] Response: '{result}'")
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
