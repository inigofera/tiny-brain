from pathlib import Path

import pytest

from tiny_brain.db import Database
from tiny_brain.vault.indexer import VaultIndexer
from tiny_brain.vault.paths import UnsafePath, resolve_vault_path, validate_relative_path
from tiny_brain.vault.parser import parse_note


def test_parser_ignores_fenced_content():
    note = parse_note(
        "Notes/one.md",
        "---\ntags: [alpha]\naliases: [First]\n---\n# Title\nBody #body [[Other|alias]]\n```\n# fake [[ignored]]\n```\n",
    )
    assert note.title == "Title"
    assert note.tags == ["alpha", "body"]
    assert [(link.target, link.alias) for link in note.links] == [("Other", "alias")]


def test_paths_are_relative_and_inside_vault(tmp_path: Path):
    assert validate_relative_path("Notes/one.md") == "Notes/one.md"
    with pytest.raises(UnsafePath):
        validate_relative_path("../escape.md")
    with pytest.raises(UnsafePath):
        validate_relative_path("/absolute.md")
    assert resolve_vault_path(tmp_path, "Notes/one.md") == tmp_path / "Notes/one.md"


def test_index_is_incremental_and_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Notes").mkdir()
    (vault / "Notes/one.md").write_text("# One\nSee [[Two]]\n#topic", encoding="utf-8")
    (vault / "Notes/two.md").write_text("# Two\nReference", encoding="utf-8")
    database = Database(tmp_path / "data.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)

    assert indexer.full_scan() == {"indexed": 2, "skipped": 0}
    assert indexer.index_path("Notes/one.md") is False
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 1
        row = connection.execute("SELECT relative_path FROM fts_sections WHERE fts_sections MATCH 'Reference'").fetchone()
        assert row[0] == "Notes/two.md"

    (vault / "Notes/one.md").write_text("# One changed\nNew text", encoding="utf-8")
    assert indexer.index_path("Notes/one.md") is True
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0
