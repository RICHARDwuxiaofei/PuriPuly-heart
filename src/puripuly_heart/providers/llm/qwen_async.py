from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import httpx

from puripuly_heart.domain.models import Translation

logger = logging.getLogger(__name__)


class AsyncQwenClient(Protocol):
    async def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        domain_prompt: str = "",
        context_pairs: list[dict[str, str]] | None = None,
    ) -> str: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class AsyncQwenLLMProvider:
    """httpx 기반 비동기 Qwen 클라이언트 (저지연 모드용)

    DashScope OpenAI 호환 API를 사용하여 즉시 취소 가능한 번역을 제공합니다.
    """

    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-mt-flash"
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
        context_pairs: list[dict[str, str]] | None = None,
    ) -> Translation:
        _ = context  # Not used in Qwen MT API
        domain_prompt = system_prompt
        client = self._get_client()
        translated = await client.translate(
            text=text,
            source_language=source_language,
            target_language=target_language,
            domain_prompt=domain_prompt,
            context_pairs=context_pairs,
        )
        return Translation(utterance_id=utterance_id, text=translated)

    async def close(self) -> None:
        if self._internal_client is not None:
            await self._internal_client.close()
            self._internal_client = None

    @staticmethod
    async def verify_api_key(
        api_key: str, base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
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
                        "model": "qwen-mt-lite",
                        "messages": [{"role": "user", "content": "test"}],
                        "max_tokens": 1,
                        "translation_options": {
                            "source_lang": "en",
                            "target_lang": "zh",
                        },
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
        source_language: str,
        target_language: str,
        domain_prompt: str = "",
        context_pairs: list[dict[str, str]] | None = None,
    ) -> str:
        logger.info(f"[LLM] Request: '{text}' -> {source_language} to {target_language}")

        translation_options: dict[str, object] = {
            "source_lang": self._normalize_language_code(source_language),
            "target_lang": self._normalize_language_code(target_language),
        }
        if domain_prompt:
            translation_options["domains"] = domain_prompt
        if context_pairs:
            translation_options["tm_list"] = context_pairs

        request_body = {
            "model": self.model,
            "messages": [{"role": "user", "content": text}],
            "translation_options": translation_options,
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
        content = message.get("content")
        if not content:
            raise RuntimeError("DashScope response did not contain message content")

        result = str(content).strip()
        logger.info(f"[LLM] Response: '{result}'")
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
