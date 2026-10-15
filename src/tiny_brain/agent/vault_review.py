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
from ..vault.chunker import Section, chunk_note
from ..vault.parser import parse_note
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
    sections: tuple[Section, ...] = ()


def _ensure_sections(note: CandidateNote) -> tuple[Section, ...]:
    if note.sections:
        return note.sections
    try:
        parsed = parse_note(note.path, note.content)
        return tuple(chunk_note(parsed))
    except Exception:
        return ()


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    action: str
    significance: str
    path: str
    reason: str
    content: str
    section_id: str | None = None


def find_candidates(
    connection: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vault_root: Path,
    query: str,
    drafts: Sequence[CandidateNote] = (),
    reranker: Any = None,
) -> list[CandidateNote]:
    result = Retriever(connection, embedding_provider, reranker).search(query, limit=8, context_tokens=4000)
    candidates: list[CandidateNote] = [
        replace(draft, sections=_ensure_sections(draft)) if not draft.sections else draft
        for draft in drafts
    ]
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
            parsed = parse_note(path, content)
            sections = tuple(chunk_note(parsed))
            candidates.append(CandidateNote(path, content, sections=sections))
            retrieved += 1
    return candidates


def _format_candidate(item: CandidateNote) -> str:
    tag = " (open draft)" if item.change_id else ""
    header = f"### {item.path}{tag}"
    sections = _ensure_sections(item)
    if not sections:
        return f"{header}\n{item.content}"
    section_blocks = []
    for s in sections:
        heading = " > ".join(s.heading_path) if s.heading_path else "(top-level)"
        section_blocks.append(f"  [Section ID: {s.section_id}] Heading: {heading}\n{s.content}")
    return f"{header}\n" + "\n\n".join(section_blocks)


def build_review_messages(question: str, answer: str, context: str | None, candidates: list[CandidateNote]) -> list[dict[str, Any]]:
    notes = "\n\n".join(_format_candidate(item) for item in candidates) or "[no related notes found]"
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
    action = (_field(data, "action") or _field(data, "operation")).lower()
    significance = _field(data, "significance").lower()
    path = _field(data, "path") or _field(data, "target_path")
    section_id = _field(data, "section_id") or None
    raw_content = _field(data, "paragraph", strip=False) or _field(data, "content", strip=False)
    content = strip_fence(raw_content).strip()
    if action in ("create", "create_note") and path and not path.lower().endswith(".md"):
        path += ".md"
    return ReviewDecision(
        action=action,
        significance=significance,
        path=path,
        reason=_field(data, "reason")[:500],
        content=content + "\n" if content else "",
        section_id=section_id,
    )


def splice_section_paragraph(
    base: str,
    sections: Sequence[Section],
    operation: str,
    section_id: str | None,
    paragraph: str,
) -> str:
    """Deterministically splice a paragraph into the base note text."""
    op = operation.lower().strip()
    if op in ("create_note", "create"):
        return paragraph.strip() + "\n"
    if op == "update" and not section_id:
        return paragraph

    clean_paragraph = paragraph.strip()
    if not clean_paragraph:
        return base

    target_section = next((s for s in sections if s.section_id == section_id), None) if section_id else None

    if op in ("replace_section", "replace"):
        if target_section is not None:
            start = target_section.start_offset
            end = target_section.end_offset
            before = base[:start]
            after = base[end:]
            return before + clean_paragraph + ("\n" if not clean_paragraph.endswith("\n") else "") + after
        return base.rstrip() + "\n\n" + clean_paragraph + "\n"

    # Default: append
    if target_section is not None:
        end = target_section.end_offset
        before = base[:end].rstrip()
        after = base[end:].lstrip("\r\n")
        separator = "\n\n" if before else ""
        tail = ("\n\n" + after) if after else "\n"
        return before + separator + clean_paragraph + tail

    return base.rstrip() + "\n\n" + clean_paragraph + "\n"


def validate_decision(decision: ReviewDecision, candidates: list[CandidateNote], vault_root: Path) -> str | None:
    valid_actions = {"update", "create", "create_note", "append", "replace_section"}
    if decision.action not in valid_actions:
        return "no change proposed"
    if decision.significance not in {"medium", "high"}:
        return "change is not significant enough"
    if decision.action in {"update", "append", "replace_section"}:
        candidate = next((item for item in candidates if item.path == decision.path), None)
        if candidate is None:
            return "update target is not a candidate note"
        sections = _ensure_sections(candidate)
        spliced = splice_section_paragraph(candidate.content, sections, decision.action, decision.section_id, decision.content)
        return check_content(candidate.content, spliced, is_update=True)
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
