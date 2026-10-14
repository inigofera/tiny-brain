from pathlib import Path

from tiny_brain.db import Database
from tiny_brain.search.retrieval import Retriever
from tiny_brain.vault.indexer import VaultIndexer


class StubEmbeddingProvider:
    model_name = "stub"
    runtime = "test"
    dimension = 2

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if any(term in text.lower() for term in ("buddhism", "spiritual", "mindfulness")) else [0.0, 1.0] for text in texts]


class ThresholdEmbeddingProvider:
    model_name = "threshold"
    runtime = "test"
    dimension = 2

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "target" in text.lower() else [0.7, 0.71414284] for text in texts]


def test_retrieval_returns_valid_citations(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "one.md").write_text("# Model\nSpark-X2.5 runs locally. [[two]]", encoding="utf-8")
    (vault / "two.md").write_text("# Hardware\nThe NUC has 16 GB RAM.", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    VaultIndexer(vault, database).full_scan()
    with database.connect() as connection:
        result = Retriever(connection).search("Spark-X2.5", limit=3, context_tokens=100)
        assert result.citations
        assert result.citations[0].relative_path == "one.md"
        assert Retriever(connection).validate_citations(result, [result.citations[0].citation_id])
        assert not Retriever(connection).validate_citations(result, ["S99"])
        assert "[SOURCE S1]" in result.context()


def test_empty_retrieval_is_explicit(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "unrelated.md").write_text("Notes about hiking equipment.", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    VaultIndexer(vault, database).full_scan()
    with database.connect() as connection:
        result = Retriever(connection).search("dialectical materialism", limit=3)
    assert not result.citations
    assert "NO MATCHING VAULT SOURCES" in result.context()


def test_natural_language_query_finds_keyword_match(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "buddhism.md").write_text("# Buddhism\nNotes on suffering, compassion, and practice.", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    VaultIndexer(vault, database).full_scan()
    with database.connect() as connection:
        result = Retriever(connection).search("what have i written about buddhism?", limit=3)
    assert result.citations
    assert result.citations[0].relative_path == "buddhism.md"


def test_semantic_embeddings_are_indexed_and_retrieved(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "buddhism.md").write_text("# Buddhism\nMindfulness and compassion as spiritual practice.", encoding="utf-8")
    (vault / "cooking.md").write_text("# Cooking\nRecipes and kitchen notes.", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    provider = StubEmbeddingProvider()
    VaultIndexer(vault, database, provider).full_scan()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM section_embeddings").fetchone()[0] == 2
        result = Retriever(connection, provider).search("spiritual practice", limit=1)
    assert result.citations[0].relative_path == "buddhism.md"


def test_low_signal_semantic_hits_are_excluded(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "target.md").write_text("Target concept.", encoding="utf-8")
    (vault / "unrelated.md").write_text("Unrelated concept.", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    provider = ThresholdEmbeddingProvider()
    VaultIndexer(vault, database, provider).full_scan()
    with database.connect() as connection:
        result = Retriever(connection, provider).search("target", limit=3)
    assert [citation.relative_path for citation in result.citations] == ["target.md"]


def test_1_hop_backlinks_and_tag_matches_are_retrieved(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "core.md").write_text("# Core Topic\nKey information here. #architecture", encoding="utf-8")
    (vault / "linking.md").write_text("# Linking Note\nSee [[core]] for details.", encoding="utf-8")
    (vault / "tagged.md").write_text("# Tagged Note\nAnother note with #architecture.", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    VaultIndexer(vault, database).full_scan()
    with database.connect() as connection:
        result = Retriever(connection).search("Core Topic", limit=5)
        paths = [c.relative_path for c in result.citations]
        assert "core.md" in paths
        assert "linking.md" in paths
        assert "tagged.md" in paths

