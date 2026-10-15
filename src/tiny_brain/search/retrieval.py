from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

from ..vault.chunker import format_breadcrumb
from .embeddings import EmbeddingProvider, EmbeddingUnavailable, cosine_similarity, pack_vector, unpack_vector
from .lexical import search as lexical_search
from .ranking import RankedHit, reciprocal_rank_fusion
from .reranking import Reranker

logger = logging.getLogger(__name__)

SEMANTIC_MIN_SCORE = 0.75
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'`\[])")


def split_into_sentences(text: str) -> list[str]:
    clean = text.strip()
    if clean.startswith("[Doc:"):
        lines = clean.splitlines()
        clean = "\n".join(lines[1:]).strip() if len(lines) > 1 else clean

    results: list[str] = []
    paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
    for para in paragraphs:
        lines = [line.strip() for line in para.splitlines() if line.strip()]
        for line in lines:
            if line.startswith(("- ", "* ", "> ", "#")) or re.match(r"^\d+\.\s", line):
                results.append(line)
            else:
                sentences = _SENTENCE_SPLIT_RE.split(line)
                for s in sentences:
                    s = s.strip()
                    if s:
                        results.append(s)
    return results


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
    def __init__(
        self,
        connection: sqlite3.Connection,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
        semantic_min_score: float | None = None,
        reranker_min_score: float = -7.0,
        context_compression: bool = False,
        max_compressed_sentences: int = 8,
    ):
        self.connection = connection
        self.embedding_provider = embedding_provider
        self.reranker = reranker
        self.reranker_min_score = reranker_min_score
        self.context_compression = context_compression
        self.max_compressed_sentences = max_compressed_sentences
        if semantic_min_score is not None:
            self.semantic_min_score = semantic_min_score
        elif reranker is not None:
            self.semantic_min_score = 0.40
        else:
            self.semantic_min_score = SEMANTIC_MIN_SCORE

    def _compress_to_sentences(self, query: str, top_hits: list[RankedHit], limit: int) -> tuple[list[Citation], list[RankedHit]]:
        records: list[tuple[int, int, str]] = []  # (hit_idx, sent_idx, sentence)
        for hit_idx, hit in enumerate(top_hits):
            sents = split_into_sentences(hit.content)
            for sent_idx, sent in enumerate(sents):
                records.append((hit_idx, sent_idx, sent))

        if not records:
            return [], []

        if len(records) <= self.max_compressed_sentences or self.reranker is None:
            selected_records = records[:self.max_compressed_sentences]
        else:
            texts = [r[2] for r in records]
            scores = self.reranker.rerank(query, texts)
            ranked_records = sorted(zip(records, scores), key=lambda x: x[1], reverse=True)
            top_records = [item[0] for item in ranked_records[:self.max_compressed_sentences]]
            # Re-sort to maintain original reading order: by hit_idx, then sent_idx
            selected_records = sorted(top_records, key=lambda x: (x[0], x[1]))

        # Group sentences by hit_idx
        by_hit: dict[int, list[str]] = {}
        for hit_idx, _, sent in selected_records:
            by_hit.setdefault(hit_idx, []).append(sent)

        citations: list[Citation] = []
        selected_hits: list[RankedHit] = []
        for hit_idx, sents in by_hit.items():
            if len(citations) >= limit:
                break
            hit = top_hits[hit_idx]
            breadcrumb = format_breadcrumb(hit.relative_path, [h for h in hit.heading_path.split(" / ") if h])
            body = "\n".join(sents)
            formatted_content = f"{breadcrumb}\n{body}"
            selected_hits.append(hit)
            citations.append(Citation(
                citation_id=f"S{len(citations) + 1}",
                section_id=hit.section_id,
                relative_path=hit.relative_path,
                title=hit.title,
                heading_path=hit.heading_path,
                content=formatted_content,
                relationship=", ".join(hit.reasons + ["compressed"]),
            ))
        return citations, selected_hits

    def search(self, query: str, *, limit: int = 8, context_tokens: int = 4000) -> RetrievalResult:
        lexical = lexical_search(self.connection, query, limit=max(limit * 3, 20))
        semantic = self._semantic_search(query, limit=max(limit * 3, 20))

        # Identify primary target notes and sections from top direct hits
        primary_section_ids = [hit.section_id for hit in lexical[:10]] + [hit.section_id for hit in semantic[:10]]
        explicit_tag_words = [w.lstrip("#").lower() for w in query.split() if w.startswith("#") and len(w) > 1]
        if not primary_section_ids and not explicit_tag_words:
            return RetrievalResult(query, (), (), 0)

        primary_note_ids, primary_paths = self._primary_note_identifiers(primary_section_ids)

        backlinks = self._fetch_backlinks(primary_note_ids, primary_paths, limit=max(limit * 2, 10))
        outgoing = self._fetch_outgoing_links(primary_section_ids, limit=max(limit * 2, 10))
        tag_matches = self._fetch_tag_matches(primary_note_ids, query, limit=max(limit * 2, 10))

        # Rank all channels in parallel via Reciprocal Rank Fusion with direct channels weighted above graph expansion
        rrf_limit = max(limit * 3, 20) if self.reranker else max(limit * 2, 16)
        ranked = reciprocal_rank_fusion(
            lexical, semantic, backlinks, outgoing, tag_matches,
            weights=(1.0, 1.0, 0.5, 0.5, 0.5),
            limit=rrf_limit,
        )

        if self.reranker and ranked:
            candidate_texts = [
                f"{format_breadcrumb(hit.relative_path, [h for h in hit.heading_path.split(' / ') if h])}\n{hit.content}"
                for hit in ranked
            ]
            try:
                scores = self.reranker.rerank(query, candidate_texts)
                scored_hits = []
                for hit, score in zip(ranked, scores):
                    if score >= self.reranker_min_score:
                        hit.score = score
                        hit.reasons.append(f"cross-encoder: {score:.2f}")
                        scored_hits.append(hit)
                ranked = sorted(scored_hits, key=lambda item: item.score, reverse=True)
            except Exception as exc:
                logger.warning("reranker failed, falling back to RRF: %s", exc)

        if self.context_compression and self.reranker and ranked:
            try:
                compressed_citations, compressed_hits = self._compress_to_sentences(query, ranked[:3], limit=limit)
                if compressed_citations:
                    used_tokens = sum(max(1, len(c.content.split())) for c in compressed_citations)
                    return RetrievalResult(
                        query,
                        tuple(compressed_hits),
                        tuple(compressed_citations),
                        max(0, sum(max(1, len(hit.content.split())) for hit in ranked) - used_tokens),
                    )
            except Exception as exc:
                logger.warning("Context compression failed, falling back to full sections: %s", exc)

        selected: list[RankedHit] = []
        citations: list[Citation] = []
        used_tokens = 0
        for hit in ranked:
            breadcrumb = format_breadcrumb(hit.relative_path, [h for h in hit.heading_path.split(" / ") if h])
            formatted_content = f"{breadcrumb}\n{hit.content}" if not hit.content.startswith("[Doc:") else hit.content
            estimate = max(1, len(formatted_content.split()))
            if selected and used_tokens + estimate > context_tokens:
                continue
            used_tokens += estimate
            selected.append(hit)
            citations.append(Citation(
                citation_id=f"S{len(citations) + 1}", section_id=hit.section_id,
                relative_path=hit.relative_path, title=hit.title, heading_path=hit.heading_path,
                content=formatted_content, relationship=", ".join(hit.reasons) or "retrieved",
            ))
            if len(selected) >= limit:
                break
        return RetrievalResult(query, tuple(selected), tuple(citations), max(0, sum(max(1, len(hit.content.split())) for hit in ranked) - used_tokens))

    def validate_citations(self, result: RetrievalResult, citation_ids: list[str]) -> bool:
        allowed = {citation.citation_id for citation in result.citations}
        if not allowed:
            return True
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
            if score >= self.semantic_min_score:
                scored.append(RankedHit(row[0], row[1], row[2], " / ".join(json.loads(row[3])), row[4], score, ["semantic match"]))
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    def _primary_note_identifiers(self, section_ids: list[str]) -> tuple[list[str], list[str]]:
        if not section_ids:
            return [], []
        placeholders = ",".join("?" for _ in section_ids)
        rows = self.connection.execute(
            f"SELECT DISTINCT note_id, relative_path FROM sections WHERE section_id IN ({placeholders})",
            section_ids,
        ).fetchall()
        note_ids = [row[0] for row in rows]
        paths = [row[1] for row in rows]
        return note_ids, paths

    def _fetch_backlinks(self, note_ids: list[str], paths: list[str], limit: int) -> list[RankedHit]:
        if not note_ids and not paths:
            return []
        note_placeholders = ",".join("?" for _ in note_ids) if note_ids else "NULL"
        path_targets = [p.removesuffix(".md") for p in paths] + paths
        path_placeholders = ",".join("?" for _ in path_targets) if path_targets else "NULL"
        params = list(note_ids) + [t.lower() for t in path_targets] + [limit]
        rows = self.connection.execute(
            f"""SELECT DISTINCT s.section_id, s.relative_path, n.title, s.heading_path_json, s.content
                FROM links l
                JOIN sections s ON s.section_id = l.source_section_id
                JOIN notes n ON n.note_id = s.note_id
                WHERE l.resolved_note_id IN ({note_placeholders})
                   OR lower(l.target) IN ({path_placeholders})
                LIMIT ?""",
            params,
        ).fetchall()
        return [
            RankedHit(row[0], row[1], row[2], " / ".join(json.loads(row[3])), row[4], 0.0, ["1-hop backlink"])
            for row in rows
        ]

    def _fetch_outgoing_links(self, section_ids: list[str], limit: int) -> list[RankedHit]:
        if not section_ids:
            return []
        placeholders = ",".join("?" for _ in section_ids)
        rows = self.connection.execute(
            f"""SELECT DISTINCT s.section_id, s.relative_path, n.title, s.heading_path_json, s.content
                FROM links l
                JOIN sections s ON s.note_id = l.resolved_note_id
                JOIN notes n ON n.note_id = s.note_id
                WHERE l.source_section_id IN ({placeholders})
                LIMIT ?""",
            section_ids + [limit],
        ).fetchall()
        return [
            RankedHit(row[0], row[1], row[2], " / ".join(json.loads(row[3])), row[4], 0.0, ["outgoing link"])
            for row in rows
        ]

    def _fetch_tag_matches(self, note_ids: list[str], query: str, limit: int) -> list[RankedHit]:
        hits: list[RankedHit] = []
        # 1. Notes sharing tags with primary notes
        if note_ids:
            placeholders = ",".join("?" for _ in note_ids)
            rows = self.connection.execute(
                f"""SELECT DISTINCT s.section_id, s.relative_path, n.title, s.heading_path_json, s.content
                    FROM tags t1
                    JOIN tags t2 ON t2.tag = t1.tag
                    JOIN sections s ON s.note_id = t2.note_id
                    JOIN notes n ON n.note_id = s.note_id
                    WHERE t1.note_id IN ({placeholders})
                      AND t2.note_id NOT IN ({placeholders})
                    LIMIT ?""",
                note_ids + note_ids + [limit],
            ).fetchall()
            hits.extend([
                RankedHit(row[0], row[1], row[2], " / ".join(json.loads(row[3])), row[4], 0.0, ["tag match"])
                for row in rows
            ])

        # 2. Notes matching tags in query words
        explicit_tags = [w.lstrip("#").lower() for w in query.split() if w.startswith("#") and len(w) > 1]
        target_tags = explicit_tags if not note_ids else [w.lstrip("#").lower() for w in query.split() if len(w) > 2]
        if target_tags:
            tag_placeholders = ",".join("?" for _ in target_tags)
            rows = self.connection.execute(
                f"""SELECT DISTINCT s.section_id, s.relative_path, n.title, s.heading_path_json, s.content
                    FROM tags t
                    JOIN sections s ON s.note_id = t.note_id
                    JOIN notes n ON n.note_id = s.note_id
                    WHERE lower(t.tag) IN ({tag_placeholders})
                    LIMIT ?""",
                target_tags + [limit],
            ).fetchall()
            existing_ids = {h.section_id for h in hits}
            for row in rows:
                if row[0] not in existing_ids:
                    hits.append(RankedHit(row[0], row[1], row[2], " / ".join(json.loads(row[3])), row[4], 0.0, ["tag match"]))
        return hits[:limit]


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
