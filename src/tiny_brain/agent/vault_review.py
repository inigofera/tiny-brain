from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ..search.embeddings import EmbeddingProvider
from ..search.retrieval import Retriever
from ..vault.paths import UnsafePath, is_indexable_markdown, resolve_vault_path
from .llama import LlamaClient
from .prompts import VAULT_REVIEW_PROMPT

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 2
MAX_OPEN_DRAFTS = 3
REVIEW_UPDATE_PREFIX = "Update from conversation: "
REVIEW_CREATE_PREFIX = "New note from conversation: "
REVIEW_DESCRIPTION_PREFIXES = (REVIEW_UPDATE_PREFIX, REVIEW_CREATE_PREFIX)
MAX_NOTE_CHARS = 6000
MAX_CONTEXT_CHARS = 3000
MAX_ANSWER_CHARS = 6000
MIN_REVIEW_CHARS = 200
MIN_UPDATE_RATIO = 0.5
RESERVED_PREFIX = "_AI"

_FENCE_RE = re.compile(r"^```[\w-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class CandidateNote:
    path: str
    content: str
    change_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    action: str
    significance: str
    path: str
    reason: str
    content: str


def find_candidates(
    connection: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vault_root: Path,
    query: str,
    drafts: Sequence[CandidateNote] = (),
    reranker: Any = None,
) -> list[CandidateNote]:
    result = Retriever(connection, embedding_provider, reranker).search(query, limit=8, context_tokens=4000)
    candidates: list[CandidateNote] = list(drafts)
    retrieved = 0
    for citation in result.citations:
        if retrieved >= MAX_CANDIDATES:
            break
        path = citation.relative_path
        if path.split("/")[0] == RESERVED_PREFIX or any(item.path == path for item in candidates):
            continue
        try:
            file_path = resolve_vault_path(vault_root, path)
        except UnsafePath:
            continue
        if not file_path.is_file() or not is_indexable_markdown(vault_root, file_path):
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(content) <= MAX_NOTE_CHARS:
            candidates.append(CandidateNote(path, content))
            retrieved += 1
    return candidates


def build_review_messages(question: str, answer: str, context: str | None, candidates: list[CandidateNote]) -> list[dict[str, Any]]:
    notes = "\n\n".join(f"### {item.path}{' (open draft)' if item.change_id else ''}\n{item.content}" for item in candidates) or "[no related notes found]"
    user = (
        f"Conversation so far:\n{_clip(context or '[none]', MAX_CONTEXT_CHARS)}\n\n"
        f"Latest question: {question}\n\n"
        f"Latest answer:\n{_clip(answer, MAX_ANSWER_CHARS)}\n\n"
        f"Candidate notes:\n{notes}"
    )
    return [{"role": "system", "content": VAULT_REVIEW_PROMPT}, {"role": "user", "content": user}]


async def decide_vault_change(llama: LlamaClient, messages: list[dict[str, Any]]) -> ReviewDecision | None:
    response = await llama.complete(messages, temperature=0.1, thinking=False)
    text = str(response.get("choices", [{}])[0].get("message", {}).get("content") or "")
    decision = parse_decision(text)
    if decision is None:
        logger.warning("vault review returned no usable decision: %r", text[:300])
    return decision


def parse_decision(text: str) -> ReviewDecision | None:
    body = strip_fence(text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    action = _field(data, "action").lower()
    significance = _field(data, "significance").lower()
    path = _field(data, "path")
    content = strip_fence(_field(data, "content", strip=False)).strip()
    if action == "create" and path and not path.lower().endswith(".md"):
        path += ".md"
    return ReviewDecision(action, significance, path, _field(data, "reason")[:500], content + "\n" if content else "")


def validate_decision(decision: ReviewDecision, candidates: list[CandidateNote], vault_root: Path) -> str | None:
    if decision.action not in {"update", "create"}:
        return "no change proposed"
    if decision.significance not in {"medium", "high"}:
        return "change is not significant enough"
    if decision.action == "update":
        base = next((item.content for item in candidates if item.path == decision.path), None)
        if base is None:
            return "update target is not a candidate note"
        return check_content(base, decision.content, is_update=True)
    if decision.path.split("/")[0] == RESERVED_PREFIX:
        return "new note path is reserved"
    try:
        path = resolve_vault_path(vault_root, decision.path)
    except UnsafePath:
        return "new note path is invalid"
    if path.exists():
        return "new note path already exists"
    return check_content("", decision.content, is_update=False)


_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+)(?:#[^\[\]|\n]*)?(?:\|([^\[\]\n]*))?\]\]")


def note_titles(connection: sqlite3.Connection) -> set[str]:
    titles: set[str] = set()
    for (relative,) in connection.execute("SELECT relative_path FROM notes WHERE index_status = 'indexed'"):
        titles.add(str(relative).removesuffix(".md"))
        titles.add(PurePosixPath(str(relative)).stem)
    return titles


def fix_wikilinks(content: str, known_titles: set[str]) -> str:
    known = {title.casefold() for title in known_titles}

    def keep_or_unwrap(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        if target.casefold() in known:
            return match.group(0)
        return match.group(2) or target

    return _WIKILINK_RE.sub(keep_or_unwrap, content)


def normalize_decision(decision: ReviewDecision, candidates: list[CandidateNote], known_titles: set[str]) -> ReviewDecision:
    draft_paths = {item.path for item in candidates if item.change_id}
    action = "update" if decision.action == "create" and decision.path in draft_paths else decision.action
    content = fix_wikilinks(decision.content, known_titles) if decision.content else decision.content
    return replace(decision, action=action, content=content)


def check_content(base: str, content: str, *, is_update: bool) -> str | None:
    if not content.strip():
        return "proposed content is empty"
    if content == base:
        return "proposed content has no changes"
    if is_update and len(content) < MIN_UPDATE_RATIO * len(base):
        return "proposed update drops too much of the note"
    return None


def make_diff(base: str, proposed: str, path: str, *, limit: int = 400) -> tuple[list[str], bool]:
    lines = list(difflib.unified_diff(base.splitlines(), proposed.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=3))
    return lines[:limit], len(lines) > limit


def strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text


def _field(data: dict[str, Any], key: str, *, strip: bool = True) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip() if strip else value


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"
