from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..db import Database
from ..search.embeddings import EmbeddingProvider
from ..search.retrieval import store_embedding
from .chunker import Section, chunk_note, link_toc_to_sections
from .parser import ParsedNote, ToCEntry, build_table_of_contents, parse_note
from .paths import is_indexable_markdown, validate_relative_path


class IndexErrorPreservingState(RuntimeError):
    pass


class VaultIndexer:
    def __init__(self, vault_root: Path, database: Database, embedding_provider: EmbeddingProvider | None = None):
        self.vault_root = vault_root.resolve()
        self.database = database
        self.embedding_provider = embedding_provider

    def full_scan(self) -> dict[str, int]:
        indexed = 0
        skipped = 0
        for path in sorted(self.vault_root.rglob("*.md")):
            if not is_indexable_markdown(self.vault_root, path):
                skipped += 1
                continue
            relative = path.resolve().relative_to(self.vault_root).as_posix()
            if self.index_path(relative):
                indexed += 1
        return {"indexed": indexed, "skipped": skipped}

    def index_path(self, relative_path: str) -> bool:
        relative_path = validate_relative_path(relative_path)
        path = self.vault_root / Path(*relative_path.split("/"))
        if not path.exists() or not path.is_file() or path.is_symlink():
            return self.remove_path(relative_path)
        content = path.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self.database.connect() as connection:
            current = connection.execute(
                "SELECT note_id, content_hash, index_status FROM notes WHERE relative_path = ?", (relative_path,)
            ).fetchone()
        if current and current["content_hash"] == content_hash and current["index_status"] == "indexed":
            if self.embedding_provider is None:
                return False
            with self.database.connect() as connection:
                missing = connection.execute(
                    """SELECT COUNT(*) FROM sections s
                       LEFT JOIN section_embeddings e ON e.section_id = s.section_id
                           AND e.model_name = ? AND e.content_hash = s.content_hash
                       WHERE s.note_id = ? AND e.section_id IS NULL""",
                    (self.embedding_provider.model_name, current["note_id"]),
                ).fetchone()[0]
            if not missing:
                return False
        try:
            note = parse_note(relative_path, content)
            sections = chunk_note(note)
            toc_entries = link_toc_to_sections(build_table_of_contents(note), sections)
            self._replace_note(note, sections, path.stat().st_mtime, toc_entries=toc_entries)
        except Exception as exc:
            self._record_failure(relative_path, content_hash, len(content.encode("utf-8")), str(exc))
            raise IndexErrorPreservingState(relative_path) from exc
        return True

    def remove_path(self, relative_path: str) -> bool:
        relative_path = validate_relative_path(relative_path)
        with self.database.connect() as connection:
            connection.execute("DELETE FROM fts_sections WHERE relative_path = ?", (relative_path,))
            connection.execute("DELETE FROM fts_toc WHERE relative_path = ?", (relative_path,))
            cursor = connection.execute("DELETE FROM notes WHERE relative_path = ?", (relative_path,))
        return cursor.rowcount > 0

    def _replace_note(
        self,
        note: ParsedNote,
        sections: list[Section],
        modified_time: float,
        toc_entries: list[ToCEntry] | None = None,
    ) -> None:
        note_id = _note_id(note.relative_path)
        now = _now()
        if toc_entries is None:
            toc_entries = link_toc_to_sections(build_table_of_contents(note), sections)
        vectors = (
            self.embedding_provider.embed([f"{section.breadcrumb}\n{section.content}" for section in sections])
            if self.embedding_provider
            else []
        )
        if self.embedding_provider and len(vectors) != len(sections):
            raise RuntimeError("embedding provider returned an unexpected number of vectors")
        aliases = note.frontmatter.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [str(item) for item in aliases] if isinstance(aliases, list) else []
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM fts_sections WHERE relative_path = ?", (note.relative_path,))
                connection.execute("DELETE FROM fts_toc WHERE relative_path = ?", (note.relative_path,))
                connection.execute("DELETE FROM notes WHERE relative_path = ?", (note.relative_path,))
                connection.execute(
                    """INSERT INTO notes
                    (note_id, relative_path, title, frontmatter_json, tags_json, aliases_json,
                     content_hash, byte_size, modified_time, indexed_at, index_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed')""",
                    (note_id, note.relative_path, note.title, json.dumps(note.frontmatter), json.dumps(note.tags),
                     json.dumps(aliases), note.content_hash, len(note.content.encode("utf-8")), modified_time, now),
                )
                for section in sections:
                    connection.execute(
                        """INSERT INTO sections
                        (section_id, note_id, relative_path, heading_path_json, heading_level, ordinal,
                         content, start_offset, end_offset, token_estimate, content_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (section.section_id, note_id, section.relative_path, json.dumps(section.heading_path),
                         section.heading_level, section.ordinal, section.content, section.start_offset,
                         section.end_offset, section.token_estimate, section.content_hash),
                    )
                    heading_path = " / ".join(section.heading_path)
                    connection.execute(
                        "INSERT INTO fts_sections(section_id, relative_path, title, heading_path, aliases, tags, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (section.section_id, note.relative_path, note.title, heading_path, " ".join(aliases), " ".join(note.tags), section.content),
                    )
                    if self.embedding_provider:
                        store_embedding(
                            connection, section.section_id, vectors[section.ordinal],
                            model_name=self.embedding_provider.model_name,
                            runtime=self.embedding_provider.runtime,
                            content_hash=section.content_hash,
                        )
                        connection.execute(
                            "UPDATE sections SET embedding_status = 'ready' WHERE section_id = ?",
                            (section.section_id,),
                        )
                    for link in _links_for_section(note, section):
                        connection.execute(
                            "INSERT INTO links(source_section_id, target, alias, heading, kind) VALUES (?, ?, ?, ?, ?)",
                            (section.section_id, link[0], link[1], link[2], link[3]),
                        )
                for tag in note.tags:
                    connection.execute("INSERT INTO tags(tag, note_id) VALUES (?, ?)", (tag, note_id))
                for entry in toc_entries:
                    connection.execute(
                        """INSERT INTO note_toc
                        (toc_id, note_id, relative_path, heading_level, heading_text, heading_path_json,
                         parent_toc_id, ordinal, start_offset, end_offset, section_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (entry.toc_id, note_id, entry.relative_path, entry.heading_level, entry.heading_text,
                         json.dumps(entry.heading_path), entry.parent_toc_id, entry.ordinal,
                         entry.start_offset, entry.end_offset, entry.section_id),
                    )
                    heading_path_str = " / ".join(entry.heading_path)
                    connection.execute(
                        """INSERT INTO fts_toc
                        (toc_id, note_id, section_id, relative_path, title, heading_text, heading_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (entry.toc_id, note_id, entry.section_id, note.relative_path, note.title,
                         entry.heading_text, heading_path_str),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        self._resolve_links()

    def _resolve_links(self) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE links SET resolved_note_id = (
                    SELECT note_id FROM notes WHERE lower(relative_path) = lower(
                        CASE WHEN lower(links.target) LIKE '%.md' THEN links.target ELSE links.target || '.md' END
                    )
                )"""
            )

    def _record_failure(self, relative_path: str, content_hash: str, byte_size: int, error: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO notes(note_id, relative_path, title, frontmatter_json, tags_json, aliases_json,
                   content_hash, byte_size, indexed_at, index_status, parse_error)
                   VALUES (?, ?, ?, '{}', '[]', '[]', ?, ?, ?, 'failed', ?)
                   ON CONFLICT(relative_path) DO UPDATE SET content_hash=excluded.content_hash,
                   byte_size=excluded.byte_size, indexed_at=excluded.indexed_at, index_status='failed', parse_error=excluded.parse_error""",
                (_note_id(relative_path), relative_path, Path(relative_path).stem, content_hash, byte_size, _now(), error[:2000]),
            )


def iter_markdown_paths(vault_root: Path) -> Iterable[str]:
    root = vault_root.resolve()
    for path in sorted(root.rglob("*.md")):
        if is_indexable_markdown(root, path):
            yield path.resolve().relative_to(root).as_posix()


def _note_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.lower().encode("utf-8")).hexdigest()[:24]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _links_for_section(note: ParsedNote, section: Section) -> list[tuple[str, str | None, str | None, str]]:
    section_start = section.start_offset
    section_end = section.end_offset
    results = []
    for link in note.links:
        # Link positions are intentionally not persisted until offset-aware parsing is needed.
        # For now, retain all links on the note's first section, which keeps the graph complete.
        if section.ordinal == 0:
            results.append((link.target, link.alias, link.heading, link.kind))
    return results
