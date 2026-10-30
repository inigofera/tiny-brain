from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ..search.embeddings import EmbeddingProvider
from ..search.retrieval import Retriever
from ..vault.paths import UnsafePath, resolve_vault_path


class ToolError(ValueError):
    pass


class ReadTools:
    """Allowlisted read operations; model arguments never become host paths."""

    def __init__(self, vault_root: Path, connection: sqlite3.Connection, embedding_provider: EmbeddingProvider | None = None):
        self.vault_root = vault_root
        self.connection = connection
        self.embedding_provider = embedding_provider
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "search_notes": self.search_notes,
            "read_note": self.read_note,
            "get_note_outline": self.get_note_outline,
            "find_backlinks": self.find_backlinks,
            "find_related_notes": self.find_related_notes,
            "find_notes_by_tag": self.find_notes_by_tag,
            "find_recent_notes": self.find_recent_notes,
        }

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in self._handlers]

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"Tool '{name}' is not available. Available tools: {list(self._handlers.keys())}")
        try:
            return handler(**arguments)
        except TypeError as exc:
            raise ToolError(f"Invalid arguments for tool '{name}': {exc}") from exc

    def search_notes(self, query: str, limit: int = 8) -> dict[str, Any]:
        if not isinstance(query, str) or len(query) > 500:
            raise ToolError("Query is invalid: must be a string up to 500 characters")
        result = Retriever(self.connection, self.embedding_provider).search(query, limit=max(1, min(limit, 30)), context_tokens=4000)
        return {"results": [{"citation_id": item.citation_id, "path": item.relative_path, "title": item.title, "heading": item.heading_path, "content": item.content} for item in result.citations]}

    def read_note(self, path: str) -> dict[str, Any]:
        try:
            safe_path = resolve_vault_path(self.vault_root, path)
        except UnsafePath as exc:
            raise ToolError(f"Unsafe note path '{path}': paths must be relative and stay within vault") from exc
        if not safe_path.exists():
            raise ToolError(f"Note '{path}' was not found in vault. Use search_notes to locate existing notes.")
        content = safe_path.read_text(encoding="utf-8")
        if len(content) > 2_000_000:
            raise ToolError("Note exceeds maximum read limit (2MB)")
        return {"path": path, "content": content}

    def get_note_outline(self, path: str) -> dict[str, Any]:
        try:
            candidate = path if path.lower().endswith(".md") else f"{path}.md"
            safe_file = resolve_vault_path(self.vault_root, candidate)
            safe_rel = safe_file.relative_to(self.vault_root.resolve()).as_posix()
        except UnsafePath as exc:
            raise ToolError(f"Unsafe note path '{path}': paths must be relative and stay within vault") from exc

        row = self.connection.execute(
            """SELECT note_id, title, relative_path FROM notes
               WHERE lower(relative_path) IN (lower(?), lower(?))""",
            (safe_rel, safe_rel.removesuffix(".md")),
        ).fetchone()
        if not row:
            if not safe_file.exists():
                raise ToolError(f"Note '{path}' was not found in vault. Use search_notes to locate existing notes.")
            raise ToolError(f"Note '{path}' has not been indexed yet.")
        note_id, title, relative_path = row[0], row[1], row[2]
        toc_rows = self.connection.execute(
            """SELECT heading_level, heading_text, heading_path_json, section_id
               FROM note_toc WHERE note_id = ? ORDER BY ordinal""",
            (note_id,),
        ).fetchall()
        outline = [
            {
                "level": r[0],
                "heading": r[1],
                "heading_path": " > ".join(json.loads(r[2])),
                "section_id": r[3],
            }
            for r in toc_rows
        ]
        return {
            "path": relative_path,
            "title": title,
            "outline": outline,
        }

    def find_backlinks(self, path: str, limit: int = 20) -> dict[str, Any]:
        safe_path = _safe_note_path(path)
        rows = self.connection.execute(
            """SELECT DISTINCT s.relative_path FROM links l JOIN sections s ON s.section_id = l.source_section_id
               WHERE lower(l.target) IN (lower(?), lower(?)) LIMIT ?""",
            (safe_path, safe_path.removesuffix(".md"), max(1, min(limit, 50))),
        ).fetchall()
        return {"path": safe_path, "backlinks": [row[0] for row in rows]}

    def find_related_notes(self, path: str, limit: int = 20) -> dict[str, Any]:
        safe_path = _safe_note_path(path)
        rows = self.connection.execute(
            """SELECT DISTINCT n.relative_path, n.title FROM links l JOIN sections s ON s.section_id = l.source_section_id
               JOIN notes n ON n.note_id = l.resolved_note_id WHERE s.relative_path = ? LIMIT ?""",
            (safe_path, max(1, min(limit, 50))),
        ).fetchall()
        return {"path": safe_path, "related": [{"path": row[0], "title": row[1]} for row in rows]}

    def find_notes_by_tag(self, tag: str, limit: int = 20) -> dict[str, Any]:
        if not isinstance(tag, str) or len(tag) > 200:
            raise ToolError("tag is invalid")
        normalized = tag.lstrip("#").lower()
        rows = self.connection.execute(
            "SELECT n.relative_path, n.title FROM tags t JOIN notes n ON n.note_id = t.note_id WHERE t.tag = ? LIMIT ?",
            (normalized, max(1, min(limit, 50))),
        ).fetchall()
        return {"tag": normalized, "notes": [{"path": row[0], "title": row[1]} for row in rows]}

    def find_recent_notes(self, limit: int = 20) -> dict[str, Any]:
        rows = self.connection.execute(
            "SELECT relative_path, title, modified_time FROM notes ORDER BY modified_time DESC LIMIT ?",
            (max(1, min(limit, 50)),),
        ).fetchall()
        return {"notes": [{"path": row[0], "title": row[1], "modified_time": row[2]} for row in rows]}


def _safe_note_path(path: str) -> str:
    try:
        root = Path(".").resolve()
        return resolve_vault_path(root, path).relative_to(root).as_posix()
    except (UnsafePath, ValueError) as exc:
        raise ToolError("unsafe note path") from exc
