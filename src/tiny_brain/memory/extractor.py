from __future__ import annotations

import json
import re
from typing import Any

from ..agent.llama import LlamaClient
from .classifier import MemoryCandidate

EXTRACTOR_PROMPT = """Classify durable information from one user message. Return only JSON: {\"candidates\":[{\"category\": one of ephemeral, working_memory, user_fact, decision, project_information, idea, task, reference, permanent_knowledge, \"confidence\": number 0..1, \"claim\": string, \"subject\": string, \"related_paths\": [string], \"explicit\": boolean}]}. Return no candidates for ordinary conversation. This is classification only; never request tools or write files."""
CATEGORIES = {"ephemeral", "working_memory", "user_fact", "decision", "project_information", "idea", "task", "reference", "permanent_knowledge"}


class MemoryExtractionError(ValueError):
    pass


class MemoryExtractor:
    def __init__(self, client: LlamaClient):
        self.client = client

    async def extract(self, message: str, source_message_id: str | None = None) -> list[MemoryCandidate]:
        response = await self.client.complete([
            {"role": "system", "content": EXTRACTOR_PROMPT},
            {"role": "user", "content": message},
        ], temperature=0)
        raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(_strip_fence(raw))
            candidates = parsed.get("candidates", [])
        except (json.JSONDecodeError, AttributeError) as exc:
            raise MemoryExtractionError("model returned invalid memory JSON") from exc
        if not isinstance(candidates, list):
            raise MemoryExtractionError("memory candidates must be a list")
        return [_candidate(item, source_message_id) for item in candidates[:10]]


def _candidate(item: Any, source_message_id: str | None) -> MemoryCandidate:
    if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
        raise MemoryExtractionError("invalid memory category")
    confidence = float(item.get("confidence", 0))
    claim = str(item.get("claim", "")).strip()
    if not 0 <= confidence <= 1 or not claim or len(claim) > 2000:
        raise MemoryExtractionError("invalid memory candidate")
    paths = item.get("related_paths", [])
    if not isinstance(paths, list) or any(not isinstance(path, str) or len(path) > 300 for path in paths):
        raise MemoryExtractionError("invalid related paths")
    return MemoryCandidate(item["category"], confidence, claim, str(item.get("subject", ""))[:300], tuple(paths), bool(item.get("explicit", False)), source_message_id)


def _strip_fence(value: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.IGNORECASE)
