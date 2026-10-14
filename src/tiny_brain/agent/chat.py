from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llama import LlamaClient
from .prompts import SYSTEM_PROMPT
from .tools import ReadTools, ToolError
from ..search.retrieval import RetrievalResult


@dataclass(frozen=True, slots=True)
class ChatResult:
    answer: str
    reasoning: str
    messages: tuple[dict[str, Any], ...]
    tool_errors: tuple[str, ...]


class ChatAgent:
    def __init__(self, client: LlamaClient, max_tool_loops: int = 4):
        self.client = client
        self.max_tool_loops = max_tool_loops

    async def answer(self, question: str, retrieval: RetrievalResult, tools: ReadTools | None = None, *, thinking: bool = True) -> ChatResult:
        user_content = f"Sources:\n{retrieval.context()}\n\nQuestion: {question}" if retrieval.citations else f"Question: {question}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        errors: list[str] = []
        reasoning_parts: list[str] = []
        seen_tool_calls: set[str] = set()
        for _ in range(self.max_tool_loops + 1):
            response = await self.client.complete(messages, tools=tools.schemas if tools else None, thinking=thinking)
            message = response.get("choices", [{}])[0].get("message", {})
            messages.append(message)
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            if reasoning:
                reasoning_parts.append(str(reasoning))
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return ChatResult(str(message.get("content", "")), "".join(reasoning_parts), tuple(messages), tuple(errors))
            if tools is None:
                errors.append("model requested tools but no tool registry was available")
                break
            for call in tool_calls:
                try:
                    function = call.get("function", {})
                    name = function.get("name", "")
                    arguments = json.loads(function.get("arguments", "{}"))
                    call_key = json.dumps({"name": name, "arguments": arguments}, sort_keys=True)
                    if call_key in seen_tool_calls:
                        result = {"error": "This exact tool request was already answered. Use the existing result and answer now."}
                        errors.append("duplicate tool call")
                    else:
                        seen_tool_calls.add(call_key)
                        result = tools.invoke(name, arguments)
                except (json.JSONDecodeError, ToolError) as exc:
                    result = {"error": str(exc)}
                    errors.append(str(exc))
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(result, ensure_ascii=False)})
        return ChatResult("I could not complete the requested tool-assisted answer safely.", "".join(reasoning_parts), tuple(messages), tuple(errors))
