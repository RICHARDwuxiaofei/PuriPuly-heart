from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import httpx

from puripuly_heart.domain.models import Translation

logger = logging.getLogger(__name__)
_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


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
        raise RuntimeError("OpenRouter response contained empty message content")

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts)

    raise RuntimeError("OpenRouter response did not contain message content")


def _extract_stream_content(content: object) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)

    return ""


def _extract_error_message(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    message = data.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    error = data.get("error")
    if isinstance(error, dict):
        nested = error.get("message")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    return ""


def _extract_stream_delta(data: object) -> str:
    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    choice = choices[0]
    if not isinstance(choice, dict):
        return ""

    delta = choice.get("delta")
    if isinstance(delta, dict):
        content = _extract_stream_content(delta.get("content"))
        if content:
            return content

    message = choice.get("message")
    if isinstance(message, dict):
        return _extract_stream_content(message.get("content"))

    return ""


class OpenRouterClient(Protocol):
    async def translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> str: ...

    async def stream_translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class OpenRouterLLMProvider:
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "google/gemma-4-26b-a4b-it"
    timeout: float = 30.0
    client: OpenRouterClient | None = None
    _internal_client: OpenRouterClient | None = field(init=False, default=None, repr=False)

    def _get_client(self) -> OpenRouterClient:
        if self.client is not None:
            return self.client
        if self._internal_client is None:
            self._internal_client = HttpxOpenRouterClient(
                api_key=self.api_key,
                model=self.model,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._internal_client

    async def stream_translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]:
        _ = utterance_id
        client = self._get_client()
        cumulative = ""
        async for part in client.stream_translate(
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        ):
            if not part:
                continue
            cumulative += part
            yield cumulative

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

    @staticmethod
    async def verify_api_key(api_key: str) -> bool:
        if not api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    _OPENROUTER_KEY_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return response.status_code == 200
        except Exception:
            return False


@dataclass(slots=True)
class HttpxOpenRouterClient:
    api_key: str
    model: str
    base_url: str = "https://openrouter.ai/api/v1"
    timeout: float = 30.0
    _client: httpx.AsyncClient | None = field(init=False, default=None, repr=False)
    _client_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.timeout)
            return self._client

    def _build_request_body(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str,
        stream: bool = False,
    ) -> dict[str, object]:
        system_content = _build_system_prompt(
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
        )
        user_message = _build_user_message(text=text, context=context)

        request_body: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message},
            ],
            "reasoning": {"effort": "none"},
        }
        if stream:
            request_body["stream"] = True
        return request_body

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

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
                "[LLM] OpenRouter request with context: '%s' -> %s to %s",
                text,
                source_language,
                target_language,
            )
        else:
            logger.info(
                "[LLM] OpenRouter request: '%s' -> %s to %s",
                text,
                source_language,
                target_language,
            )

        request_body = self._build_request_body(
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )

        client = await self._get_http_client()
        response = await client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=request_body,
        )
        response.raise_for_status()

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("OpenRouter response did not contain choices")

        message = choices[0].get("message", {})
        result = _extract_message_content(message.get("content"))
        logger.info("[LLM] OpenRouter response: '%s'", result)
        return result

    async def stream_translate(
        self,
        *,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]:
        request_body = self._build_request_body(
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
            stream=True,
        )
        client = await self._get_http_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=request_body,
        ) as response:
            if response.status_code != 200:
                body_text = (await response.aread()).decode(errors="ignore")
                error_message = ""
                with contextlib.suppress(Exception):
                    error_message = _extract_error_message(json.loads(body_text))
                if not error_message:
                    error_message = body_text[:200]
                raise RuntimeError(
                    "OpenRouter request failed "
                    f"(status={response.status_code}, message={error_message})"
                )

            saw_text = False
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload or payload == "[DONE]":
                    continue
                data = json.loads(payload)
                part = _extract_stream_delta(data)
                if not part:
                    continue
                saw_text = True
                yield part
            if not saw_text:
                raise RuntimeError("OpenRouter stream did not contain message content")

    async def close(self) -> None:
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()
