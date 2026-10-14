from __future__ import annotations

import json
import re
from typing import Any

from ..agent.llama import LlamaClient
from .classifier import MemoryCandidate

CATEGORIES = {
    "ephemeral", "working_memory", "user_fact", "decision",
    "project_information", "idea", "task", "reference", "permanent_knowledge"
}

# GBNF grammar strictly forcing llama.cpp to output a tiny ~15-30 token JSON object
MEMORY_GBNF_GRAMMAR = r"""
root ::= "{" ws "\"type\"" ws ":" ws category ws "," ws "\"conf\"" ws ":" ws number ws "," ws "\"fact\"" ws ":" ws string ws "}"
category ::= "\"decision\"" | "\"user_fact\"" | "\"task\"" | "\"idea\"" | "\"project_information\"" | "\"permanent_knowledge\"" | "\"reference\"" | "\"working_memory\"" | "\"ephemeral\""
number ::= "0." [0-9]+ | "1.0" | "1" | "0"
string ::= "\"" ([^"\\] | "\\" ["\\/bfnrt])* "\""
ws ::= [ \t\n\r]*
""".strip()

EXTRACTOR_PROMPT = """Extract the single most important durable memory item from the user message as JSON:
{"type": "decision"|"user_fact"|"task"|"idea"|"project_information"|"permanent_knowledge", "conf": 0.0-1.0, "fact": "concise fact"}"""

# Stage 1: Heuristic triggers for explicit cues and first-person declarative markers
EXPLICIT_CUES = re.compile(
    r"\b(remember|keep\s+in\s+mind|take\s+note|note\s+that|save\s+this|record\s+this|"
    r"todo|to-do|action\s+item|task:|my\s+preference|i\s+prefer|we\s+prefer|"
    r"i\s+decided|we\s+decided|i\s+chose|we\s+chose|my\s+decision|our\s+decision|"
    r"my\s+rule|our\s+rule)\b",
    re.IGNORECASE,
)

DECLARATIVE_MARKERS = re.compile(
    r"\b(?:i'?m|i\s+am)\s+(?:working\s+on|planning\s+to|going\s+to|building|designing|using|configuring)\b|"
    r"\b(?:i|we)\s+(?:want|plan|need|will|should|must|concluded|believe|concluded\s+that)\b|"
    r"\b(?:i|we)\s+(?:have|bought|purchased|switched\s+to|adopted|maintain|run|own)\b|"
    r"\b(?:my|our)\s+(?:project|goal|system|setup|stack|choice|workflow|notes?)\b",
    re.IGNORECASE,
)


def should_trigger_extraction(message: str) -> bool:
    """Stage 1 gate: only trigger LLM extractor on first-person declarative markers or explicit cues."""
    text = message.strip()
    if len(text) < 4:
        return False
    return bool(EXPLICIT_CUES.search(text) or DECLARATIVE_MARKERS.search(text))


class MemoryExtractionError(ValueError):
    pass


class MemoryExtractor:
    def __init__(self, client: LlamaClient):
        self.client = client

    async def extract(self, message: str, source_message_id: str | None = None) -> list[MemoryCandidate]:
        # Stage 1: Deterministic gate
        if not should_trigger_extraction(message):
            return []

        # Stage 2: GBNF Grammar constrained LLM invocation
        try:
            response = await self.client.complete(
                [
                    {"role": "system", "content": EXTRACTOR_PROMPT},
                    {"role": "user", "content": message},
                ],
                temperature=0.0,
                max_tokens=60,
                grammar=MEMORY_GBNF_GRAMMAR,
                thinking=False,
            )
        except Exception as exc:
            # Fallback for clients/mocks that do not accept grammar parameter
            try:
                response = await self.client.complete(
                    [
                        {"role": "system", "content": EXTRACTOR_PROMPT},
                        {"role": "user", "content": message},
                    ],
                    temperature=0.0,
                )
            except Exception as inner:
                raise MemoryExtractionError(f"model completion failed: {inner}") from inner

        raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _parse_candidate(raw, source_message_id)


def _parse_candidate(raw: str, source_message_id: str | None) -> list[MemoryCandidate]:
    try:
        parsed = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, AttributeError) as exc:
        raise MemoryExtractionError("model returned invalid memory JSON") from exc

    # Compact grammar format: {"type": "decision", "conf": 0.9, "fact": "..."}
    if isinstance(parsed, dict) and ("type" in parsed or "fact" in parsed):
        cat = str(parsed.get("type", "user_fact")).lower()
        if cat not in CATEGORIES:
            cat = "user_fact"
        conf = float(parsed.get("conf", 0.9))
        fact = str(parsed.get("fact", "")).strip()
        if not fact:
            return []
        return [MemoryCandidate(cat, conf, fact, explicit=True, source_message_id=source_message_id)]

    # Candidates list format: {"candidates": [...]}
    if isinstance(parsed, dict) and "candidates" in parsed:
        candidates = parsed.get("candidates", [])
        if not isinstance(candidates, list):
            raise MemoryExtractionError("memory candidates must be a list")
        return [_candidate(item, source_message_id) for item in candidates[:10]]

    # Single candidate dict: {"category": "decision", "confidence": 0.9, "claim": "..."}
    if isinstance(parsed, dict) and "category" in parsed and "claim" in parsed:
        return [_candidate(parsed, source_message_id)]

    raise MemoryExtractionError("unrecognized memory JSON format")


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
