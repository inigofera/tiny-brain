from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
                INSERT INTO schema_version(version)
                    SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

                CREATE TABLE IF NOT EXISTS notes (
                    note_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    frontmatter_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    modified_time REAL,
                    indexed_at TEXT,
                    index_status TEXT NOT NULL,
                    conflict_status TEXT NOT NULL DEFAULT 'none',
                    parse_error TEXT
                );
                CREATE INDEX IF NOT EXISTS notes_path_idx ON notes(relative_path);

                CREATE TABLE IF NOT EXISTS sections (
                    section_id TEXT PRIMARY KEY,
                    note_id TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    heading_path_json TEXT NOT NULL,
                    heading_level INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    token_estimate INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_status TEXT NOT NULL DEFAULT 'missing',
                    UNIQUE(note_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS sections_note_idx ON sections(note_id, ordinal);

                CREATE VIRTUAL TABLE IF NOT EXISTS fts_sections USING fts5(
                    section_id UNINDEXED, relative_path, title, heading_path, aliases, tags, content
                );

                CREATE TABLE IF NOT EXISTS section_embeddings (
                    section_id TEXT PRIMARY KEY REFERENCES sections(section_id) ON DELETE CASCADE,
                    model_name TEXT NOT NULL,
                    runtime TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    normalized INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS links (
                    link_id INTEGER PRIMARY KEY,
                    source_section_id TEXT NOT NULL REFERENCES sections(section_id) ON DELETE CASCADE,
                    target TEXT NOT NULL,
                    resolved_note_id TEXT REFERENCES notes(note_id) ON DELETE SET NULL,
                    alias TEXT,
                    heading TEXT,
                    kind TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS links_target_idx ON links(target);
                CREATE INDEX IF NOT EXISTS links_resolved_idx ON links(resolved_note_id);

                CREATE TABLE IF NOT EXISTS tags (
                    tag TEXT NOT NULL,
                    note_id TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
                    PRIMARY KEY(tag, note_id)
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    classification_json TEXT,
                    retrieval_json TEXT
                );
                CREATE TABLE IF NOT EXISTS retrieval_events (
                    query_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    query TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    source_message_id TEXT,
                    category TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    subject TEXT,
                    claim TEXT NOT NULL,
                    related_paths_json TEXT NOT NULL,
                    gate_decision TEXT NOT NULL,
                    gate_reason TEXT NOT NULL,
                    duplicate_match TEXT,
                    inbox_change_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS change_proposals (
                    change_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    source_message_id TEXT,
                    operation TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    description TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    base_hash TEXT NOT NULL,
                    base_content TEXT,
                    proposed_content TEXT NOT NULL,
                    current_hash TEXT,
                    state TEXT NOT NULL,
                    conflict_data TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    git_commit_id TEXT
                );
                CREATE INDEX IF NOT EXISTS changes_state_idx ON change_proposals(state, updated_at);
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    change_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS index_jobs (
                    job_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    cause TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS model_metadata (
                    model_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            current = connection.execute("SELECT version FROM schema_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"database version {current} is newer than application {SCHEMA_VERSION}")
            connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


def has_sqlite_vec(connection: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec
        sqlite_vec.load(connection)
    except (AttributeError, ImportError, OSError, sqlite3.Error):
        return False
    return True
