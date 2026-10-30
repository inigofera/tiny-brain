from pathlib import Path

from tiny_brain.db import Database
from tiny_brain.vault.chunker import chunk_note, link_toc_to_sections
from tiny_brain.vault.indexer import VaultIndexer
from tiny_brain.vault.parser import build_table_of_contents, parse_note


def test_build_table_of_contents_hierarchy():
    content = """# Title
Introductory text.

## Architecture
Overview of the architecture.

### Services
Detailed service breakdown.

#### Caddy
Proxy information.

### Storage
SQLite WAL storage.

## Deployment
Docker compose deployment.
"""
    note = parse_note("docs/system.md", content)
    toc = build_table_of_contents(note)

    assert len(toc) == 6
    # Title (H1)
    assert toc[0].heading_text == "Title"
    assert toc[0].heading_level == 1
    assert toc[0].parent_toc_id is None
    assert toc[0].heading_path == ("Title",)

    # Architecture (H2)
    assert toc[1].heading_text == "Architecture"
    assert toc[1].heading_level == 2
    assert toc[1].parent_toc_id == toc[0].toc_id
    assert toc[1].heading_path == ("Title", "Architecture")

    # Services (H3)
    assert toc[2].heading_text == "Services"
    assert toc[2].heading_level == 3
    assert toc[2].parent_toc_id == toc[1].toc_id
    assert toc[2].heading_path == ("Title", "Architecture", "Services")

    # Caddy (H4)
    assert toc[3].heading_text == "Caddy"
    assert toc[3].heading_level == 4
    assert toc[3].parent_toc_id == toc[2].toc_id
    assert toc[3].heading_path == ("Title", "Architecture", "Services", "Caddy")

    # Storage (H3) - should pop Caddy and be child of Architecture
    assert toc[4].heading_text == "Storage"
    assert toc[4].heading_level == 3
    assert toc[4].parent_toc_id == toc[1].toc_id
    assert toc[4].heading_path == ("Title", "Architecture", "Storage")

    # Deployment (H2) - should pop Storage & Architecture and be child of Title
    assert toc[5].heading_text == "Deployment"
    assert toc[5].heading_level == 2
    assert toc[5].parent_toc_id == toc[0].toc_id
    assert toc[5].heading_path == ("Title", "Deployment")


def test_link_toc_to_sections():
    content = """# First Note
Introduction.

## Details
Some details here.
"""
    note = parse_note("note.md", content)
    sections = chunk_note(note)
    toc = build_table_of_contents(note)
    linked = link_toc_to_sections(toc, sections)

    assert len(linked) == 2
    assert linked[0].section_id == sections[0].section_id
    assert linked[1].section_id == sections[1].section_id


def test_indexer_populates_note_toc_and_fts_toc(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    doc = vault / "guide.md"
    doc.write_text(
        "# User Guide\nWelcome to Tiny Brain.\n\n## Installation\nRun pip install.\n\n## Usage\nRun the app.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    indexer.full_scan()

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT heading_level, heading_text, heading_path_json, section_id FROM note_toc ORDER BY ordinal"
        ).fetchall()
        assert len(rows) == 3
        assert rows[0][1] == "User Guide"
        assert rows[1][1] == "Installation"
        assert rows[2][1] == "Usage"
        assert rows[1][3] is not None

        # Check FTS index on ToC
        fts_rows = connection.execute(
            "SELECT heading_text, heading_path FROM fts_toc WHERE fts_toc MATCH '\"installation\"*'"
        ).fetchall()
        assert len(fts_rows) == 1
        assert fts_rows[0][0] == "Installation"
        assert "User Guide" in fts_rows[0][1]


def test_indexer_cleans_up_toc_on_file_removal(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    doc = vault / "temp.md"
    doc.write_text("# Temporary\n\n## Subtopic\nContent.", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    indexer.full_scan()

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM note_toc").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM fts_toc").fetchone()[0] == 2

    # Delete file and invoke index_path (which triggers remove_path)
    doc.unlink()
    assert indexer.index_path("temp.md") is True

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM note_toc").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM fts_toc").fetchone()[0] == 0
