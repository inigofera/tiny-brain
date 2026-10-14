from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .embeddings import EmbeddingProvider, EmbeddingUnavailable, cosine_similarity, pack_vector, unpack_vector
from .lexical import search as lexical_search
from .ranking import RankedHit, reciprocal_rank_fusion

SEMANTIC_MIN_SCORE = 0.75


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    section_id: str
    relative_path: str
    title: str
    heading_path: str
    content: str
    relationship: str


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    hits: tuple[RankedHit, ...]
    citations: tuple[Citation, ...]
    omitted_tokens: int

    def context(self) -> str:
        if not self.citations:
            return "[NO MATCHING VAULT SOURCES]\nThe vault search returned no relevant indexed source blocks.\n[/NO MATCHING VAULT SOURCES]"
        blocks = []
        for citation in self.citations:
            blocks.append(
                f"[SOURCE {citation.citation_id}]\nPath: {citation.relative_path}\n"
                f"Title: {citation.title}\nHeading: {citation.heading_path}\n"
                f"Relationship: {citation.relationship}\nContent:\n{citation.content}\n[/SOURCE {citation.citation_id}]"
            )
        return "\n\n".join(blocks)


class Retriever:
    def __init__(self, connection: sqlite3.Connection, embedding_provider: EmbeddingProvider | None = None):
        self.connection = connection
        self.embedding_provider = embedding_provider

    def search(self, query: str, *, limit: int = 8, context_tokens: int = 4000) -> RetrievalResult:
        lexical = lexical_search(self.connection, query, limit=max(limit * 3, 20))
        semantic = self._semantic_search(query, limit=max(limit * 3, 20))
        ranked = reciprocal_rank_fusion(lexical, semantic, limit=max(limit * 2, 16))
        ranked = self._expand_graph(ranked, max_items=max(limit * 2, 16))
        selected: list[RankedHit] = []
        citations: list[Citation] = []
        used_tokens = 0
        for hit in ranked:
            estimate = max(1, len(hit.content.split()))
            if selected and used_tokens + estimate > context_tokens:
                continue
            used_tokens += estimate
            selected.append(hit)
            citations.append(Citation(
                citation_id=f"S{len(citations) + 1}", section_id=hit.section_id,
                relative_path=hit.relative_path, title=hit.title, heading_path=hit.heading_path,
                content=hit.content, relationship=", ".join(hit.reasons) or "retrieved",
            ))
            if len(selected) >= limit:
                break
        return RetrievalResult(query, tuple(selected), tuple(citations), max(0, sum(max(1, len(hit.content.split())) for hit in ranked) - used_tokens))

    def validate_citations(self, result: RetrievalResult, citation_ids: list[str]) -> bool:
        allowed = {citation.citation_id for citation in result.citations}
        return bool(citation_ids) and set(citation_ids).issubset(allowed)

    def _semantic_search(self, query: str, limit: int) -> list[RankedHit]:
        if self.embedding_provider is None:
            return []
        try:
            query_vector = self.embedding_provider.embed([query])[0]
        except EmbeddingUnavailable:
            return []
        rows = self.connection.execute(
            """SELECT s.section_id, s.relative_path, n.title, s.heading_path_json, s.content, e.vector
               FROM sections s JOIN notes n ON n.note_id = s.note_id
               JOIN section_embeddings e ON e.section_id = s.section_id
               WHERE e.model_name = ? AND e.dimension = ?""",
            (self.embedding_provider.model_name, self.embedding_provider.dimension),
        ).fetchall()
        scored = []
        for row in rows:
            score = cosine_similarity(query_vector, unpack_vector(row[5]))
            if score >= SEMANTIC_MIN_SCORE:
                scored.append(RankedHit(row[0], row[1], row[2], " / ".join(json.loads(row[3])), row[4], score, ["semantic match"]))
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    def _expand_graph(self, hits: list[RankedHit], max_items: int) -> list[RankedHit]:
        if not hits:
            return []
        ids = [hit.section_id for hit in hits]
        placeholders = ",".join("?" for _ in ids)
        related_rows = self.connection.execute(
            f"""SELECT DISTINCT s.section_id, s.relative_path, n.title, s.heading_path_json, s.content
                FROM links l JOIN sections s ON s.note_id = l.resolved_note_id
                JOIN notes n ON n.note_id = s.note_id
                WHERE l.source_section_id IN ({placeholders})""", ids,
        ).fetchall()
        existing = {hit.section_id for hit in hits}
        for row in related_rows:
            if row[0] in existing or len(hits) >= max_items:
                continue
            hits.append(RankedHit(row[0], row[1], row[2], " / ".join(json.loads(row[3])), row[4], 0.001, ["outgoing link"]))
        return hits


def store_embedding(connection: sqlite3.Connection, section_id: str, vector: list[float], *, model_name: str, runtime: str, content_hash: str) -> None:
    from datetime import datetime, timezone
    connection.execute(
        """INSERT INTO section_embeddings(section_id, model_name, runtime, dimension, normalized, vector, content_hash, generated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?)
           ON CONFLICT(section_id) DO UPDATE SET model_name=excluded.model_name, runtime=excluded.runtime,
           dimension=excluded.dimension, normalized=excluded.normalized, vector=excluded.vector,
           content_hash=excluded.content_hash, generated_at=excluded.generated_at""",
        (section_id, model_name, runtime, len(vector), pack_vector(vector), content_hash, datetime.now(timezone.utc).isoformat()),
    )
