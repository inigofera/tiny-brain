from pathlib import Path

import pytest

from tiny_brain.agent.tools import ReadTools, ToolError
from tiny_brain.db import Database
from tiny_brain.vault.indexer import VaultIndexer


def test_get_note_outline_returns_structured_hierarchy(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    doc = vault / "design.md"
    doc.write_text(
        "# System Design\nIntro.\n\n## Architecture\nOverview.\n\n### Database\nSQLite details.\n\n## Future Work\nRoadmap.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    indexer.full_scan()

    with database.connect() as connection:
        tools = ReadTools(vault, connection)

        # Test direct call with .md
        res = tools.get_note_outline("design.md")
        assert res["path"] == "design.md"
        assert res["title"] == "System Design"
        assert len(res["outline"]) == 4

        outline = res["outline"]
        assert outline[0]["level"] == 1
        assert outline[0]["heading"] == "System Design"
        assert outline[1]["level"] == 2
        assert outline[1]["heading"] == "Architecture"
        assert outline[2]["level"] == 3
        assert outline[2]["heading"] == "Database"
        assert outline[2]["heading_path"] == "System Design > Architecture > Database"

        # Test call without extension
        res_no_ext = tools.get_note_outline("design")
        assert res_no_ext["path"] == "design.md"
        assert len(res_no_ext["outline"]) == 4

        # Test via invoke
        invoked = tools.invoke("get_note_outline", {"path": "design.md"})
        assert invoked["title"] == "System Design"

        # Test schema presence
        schema_names = [s["function"]["name"] for s in tools.schemas]
        assert "get_note_outline" in schema_names


def test_get_note_outline_error_cases(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    database = Database(tmp_path / "db.sqlite")
    database.migrate()

    with database.connect() as connection:
        tools = ReadTools(vault, connection)

        # Unsafe path
        with pytest.raises(ToolError, match="Unsafe note path"):
            tools.get_note_outline("../secret.md")

        # Non-existent note
        with pytest.raises(ToolError, match="not found in vault"):
            tools.get_note_outline("missing.md")

