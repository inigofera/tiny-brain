from pathlib import Path

import pytest

from tiny_brain.agent.tools import ReadTools, ToolError
from tiny_brain.changes.audit import AuditLog
from tiny_brain.db import Database
from tiny_brain.memory.classifier import MemoryCandidate
from tiny_brain.memory.service import MemoryService
from tiny_brain.vault.indexer import VaultIndexer


def test_read_tools_reject_escape_and_return_notes(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n#tag", encoding="utf-8")
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    VaultIndexer(vault, database).full_scan()
    with database.connect() as connection:
        tools = ReadTools(vault, connection)
        assert tools.read_note("note.md")["content"].startswith("# Note")
        assert tools.find_notes_by_tag("#tag")["notes"][0]["path"] == "note.md"
        with pytest.raises(ToolError):
            tools.read_note("../secret.md")
        with pytest.raises(ToolError):
            tools.invoke("run_command", {})


def test_accepted_memory_is_provenanced_and_indexed(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    service = MemoryService(vault, database, indexer, AuditLog(database, tmp_path / "data"), threshold=.9)
    result = service.process(MemoryCandidate("decision", .95, "Use Q6 for the first deployment", "chat model"))
    assert result["accepted"] is True
    inbox = vault / "_AI/Inbox.md"
    assert inbox.exists() and "Provenance:" in inbox.read_text(encoding="utf-8")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM sections WHERE relative_path = '_AI/Inbox.md'").fetchone()[0] >= 1
