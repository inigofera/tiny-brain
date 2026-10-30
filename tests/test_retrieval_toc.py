from pathlib import Path

from tiny_brain.db import Database
from tiny_brain.search.lexical import toc_search
from tiny_brain.search.retrieval import Retriever
from tiny_brain.vault.indexer import VaultIndexer


def test_toc_search_matches_heading_and_returns_section(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    doc = vault / "network.md"
    doc.write_text(
        "# Network Topologies\nBroad overview.\n\n## WireGuard VPN Configuration\nKeys and interface setup.\n\n## BGP Peering\nAutonomous system routing.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    indexer.full_scan()

    with database.connect() as connection:
        hits = toc_search(connection, "WireGuard VPN")
        assert len(hits) >= 1
        assert hits[0].relative_path == "network.md"
        assert "WireGuard VPN Configuration" in hits[0].heading_path
        assert "WireGuard" in hits[0].reason


def test_retrieval_incorporates_toc_and_formats_outline_siblings(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    doc = vault / "specs.md"
    doc.write_text(
        "# Specifications\nSystem specs.\n\n## Subsystem Alpha\nAlpha details here.\n\n## Subsystem Beta\nBeta details here.\n\n## Subsystem Gamma\nGamma details here.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    indexer.full_scan()

    with database.connect() as connection:
        retriever = Retriever(connection)
        result = retriever.search("Subsystem Beta", limit=3)
        assert len(result.citations) >= 1
        top = result.citations[0]
        assert top.relative_path == "specs.md"
        assert "Subsystem Beta" in top.heading_path

        # Verify sibling outline context is injected
        assert "Siblings:" in top.outline_context
        assert "Subsystem Alpha" in top.outline_context or "Subsystem Gamma" in top.outline_context

        # Verify formatted context contains outline information
        context_str = result.context()
        assert "Outline Context: Siblings:" in context_str

