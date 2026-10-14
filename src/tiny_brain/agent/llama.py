from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class StreamChunk:
    reasoning: str = ""
    content: str = ""


class LlamaClient:
    def __init__(self, base_url: str, model: str, *, timeout: float = 300.0, max_tokens: int = 4096):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(self.base_url.removesuffix("/v1") + "/health")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def complete(self, messages: list[dict[str, Any]], *, temperature: float = 0.2, stream: bool = False, tools: list[dict[str, Any]] | None = None, thinking: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature, "stream": stream, "max_tokens": self.max_tokens}
        if tools:
            payload["tools"] = tools
        if not thinking:
            payload["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def stream(self, messages: list[dict[str, Any]], *, temperature: float = 0.2, max_tokens: int | None = None, reasoning_effort: str | None = None, thinking: bool = True) -> AsyncIterator[StreamChunk]:
        output_tokens = max_tokens or self.max_tokens
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature, "stream": True, "max_tokens": output_tokens}
        if reasoning_effort is not None or not thinking:
            reasoning_effort = reasoning_effort or "none"
            payload["reasoning_effort"] = reasoning_effort
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:") or line.strip() == "data: [DONE]":
                        continue
                    import json
                    payload = json.loads(line[5:].strip())
                    delta = payload.get("choices", [{}])[0].get("delta", {})
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    content = delta.get("content") or ""
                    if reasoning or content:
                        yield StreamChunk(str(reasoning), str(content))
