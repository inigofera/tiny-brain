from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class StreamChunk:
    reasoning: str = ""
    content: str = ""


class LlamaClient:
    def __init__(self, base_url: str, model: str, *, timeout: float = 300.0, max_tokens: int = 4096, lookup_ngram_min: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.lookup_ngram_min = lookup_ngram_min
        self._last_active_time = time.time()
        self._active_requests = 0

    @property
    def is_busy(self) -> bool:
        return self._active_requests > 0

    @property
    def last_active_time(self) -> float:
        return self._last_active_time

    @property
    def idle_seconds(self) -> float:
        if self.is_busy:
            return 0.0
        return max(0.0, time.time() - self._last_active_time)

    def is_idle(self, min_idle_seconds: float = 10.0) -> bool:
        return not self.is_busy and self.idle_seconds >= min_idle_seconds

    def touch_activity(self) -> None:
        self._last_active_time = time.time()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(self.base_url.removesuffix("/v1") + "/health")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        thinking: bool = True,
        grammar: str | None = None,
        response_format: dict[str, Any] | None = None,
        id_slot: int | None = 0,
        lookup_ngram_min: int | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        output_tokens = max_tokens or self.max_tokens
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature, "stream": stream, "max_tokens": output_tokens}
        if id_slot is not None:
            payload["id_slot"] = id_slot
        ngram_min = lookup_ngram_min if lookup_ngram_min is not None else self.lookup_ngram_min
        if ngram_min is not None and ngram_min > 0:
            payload["lookup_ngram_min"] = ngram_min
        if tools:
            payload["tools"] = tools
        if grammar:
            payload["grammar"] = grammar
        if response_format:
            payload["response_format"] = response_format
        if not thinking:
            payload["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
            payload["chat_template_kwargs"] = {"enable_thinking": reasoning_effort != "none"}
        self._active_requests += 1
        self._last_active_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        finally:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_active_time = time.time()

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        thinking: bool = True,
        id_slot: int | None = 0,
        lookup_ngram_min: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        output_tokens = max_tokens or self.max_tokens
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature, "stream": True, "max_tokens": output_tokens}
        if id_slot is not None:
            payload["id_slot"] = id_slot
        ngram_min = lookup_ngram_min if lookup_ngram_min is not None else self.lookup_ngram_min
        if ngram_min is not None and ngram_min > 0:
            payload["lookup_ngram_min"] = ngram_min
        if not thinking:
            payload["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
            payload["chat_template_kwargs"] = {"enable_thinking": reasoning_effort != "none"}
        self._active_requests += 1
        self._last_active_time = time.time()
        try:
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
                        chunk_data = json.loads(line[5:].strip())
                        delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        content = delta.get("content") or ""
                        if reasoning or content:
                            yield StreamChunk(str(reasoning), str(content))
        finally:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_active_time = time.time()
