from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tiny_brain.db import Database
from tiny_brain.search.ranking import RankedHit
from tiny_brain.search.retrieval import Retriever, split_into_sentences
from tiny_brain.vault.indexer import VaultIndexer


class MockReranker:
    model_name = "mock-reranker"

    def __init__(self, keyword_scores: dict[str, float] | None = None):
        self.keyword_scores = keyword_scores or {}
        self.calls: list[list[str]] = []

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append(list(documents))
        scores = []
        for doc in documents:
            # Score higher if query or keywords match
            score = 0.0
            for word in query.lower().split():
                if word in doc.lower():
                    score += 2.0
            for kw, val in self.keyword_scores.items():
                if kw.lower() in doc.lower():
                    score += val
            scores.append(score)
        return scores


def test_split_into_sentences():
    text = (
        "We utilize high-speed NVMe solid state drives. "
        "Distributed Redis in-memory caches are deployed across all nodes.\n\n"
        "- Read latency is single-digit milliseconds.\n"
        "- Write operations sync through write-back queues.\n\n"
        "1. First step.\n"
        "2. Second step."
    )
    sentences = split_into_sentences(text)
    assert len(sentences) == 6
    assert sentences[0] == "We utilize high-speed NVMe solid state drives."
    assert sentences[1] == "Distributed Redis in-memory caches are deployed across all nodes."
    assert sentences[2] == "- Read latency is single-digit milliseconds."
    assert sentences[3] == "- Write operations sync through write-back queues."
    assert sentences[4] == "1. First step."
    assert sentences[5] == "2. Second step."


def test_split_into_sentences_strips_existing_breadcrumb():
    text = "[Doc: storage.md > Section: Cache]\nFirst sentence. Second sentence."
    sentences = split_into_sentences(text)
    assert len(sentences) == 2
    assert sentences[0] == "First sentence."
    assert sentences[1] == "Second sentence."


def test_retriever_context_compression_prunes_to_top_sentences(tmp_path: Path):
    db = Database(tmp_path / "test_compress.sqlite")
    db.migrate()
    vault = tmp_path / "vault"
    vault.mkdir()

    # Create a long note with many sentences
    note_content = (
        "# Distributed Architecture\n\n"
        "## Cache Policies\n"
        "Sentence 1 about general background. "
        "Sentence 2 about ancient history. "
        "Redis cluster handles all hot read queries with sub-millisecond response times. "
        "Sentence 4 about unrelated topic. "
        "Sentence 5 about lunch schedule. "
        "Sentence 6 about weather. "
        "In-memory Redis cache invalidation occurs via write-through events. "
        "Sentence 8 about office temperature. "
        "Sentence 9 about desk ergonomics. "
        "Sentence 10 about team meetings."
    )
    (vault / "Architecture.md").write_text(note_content, encoding="utf-8")
    indexer = VaultIndexer(vault, db)
    indexer.full_scan()

    reranker = MockReranker(keyword_scores={"redis": 5.0})

    with db.connect() as conn:
        # 1. Uncompressed retrieval: returns full section (10 sentences)
        uncompressed = Retriever(conn, reranker=reranker, context_compression=False).search("Redis", limit=1)
        assert len(uncompressed.citations) == 1
        full_text = uncompressed.citations[0].content
        assert "Sentence 1 about general background" in full_text
        assert "Sentence 8 about office temperature" in full_text

        # 2. Compressed retrieval: prunes to top scoring sentences (e.g. max 2 sentences)
        compressed = Retriever(
            conn,
            reranker=reranker,
            context_compression=True,
            max_compressed_sentences=2,
        ).search("Redis", limit=1)
        assert len(compressed.citations) == 1
        comp_text = compressed.citations[0].content

        # Should retain breadcrumb
        assert "[Doc: Architecture.md > Section: Distributed Architecture > Cache Policies]" in comp_text
        # Should retain high-scoring Redis sentences
        assert "Redis cluster handles all hot read queries" in comp_text
        assert "In-memory Redis cache invalidation occurs" in comp_text
        # Should PRUNE low-scoring filler sentences
        assert "Sentence 8 about office temperature" not in comp_text
        assert "Sentence 5 about lunch schedule" not in comp_text

        # Word/token count should be dramatically lower (reduced by >= 50%)
        assert len(comp_text.split()) <= len(full_text.split()) * 0.5


def test_retriever_compression_falls_back_gracefully_on_error(tmp_path: Path):
    db = Database(tmp_path / "test_fallback.sqlite")
    db.migrate()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text("# Title\n## Section\nImportant content.", encoding="utf-8")
    indexer = VaultIndexer(vault, db)
    indexer.full_scan()

    class FailingReranker:
        model_name = "failing"
        def rerank(self, query: str, documents: list[str]) -> list[float]:
            raise RuntimeError("Reranking engine out of memory")

    with db.connect() as conn:
        retriever = Retriever(conn, reranker=FailingReranker(), context_compression=True)
        # Should not raise; falls back gracefully to standard uncompressed search
        res = retriever.search("Important", limit=1)
        assert len(res.citations) == 1
        assert "Important content." in res.citations[0].content
