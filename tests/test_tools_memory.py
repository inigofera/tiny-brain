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


def test_should_trigger_extraction_gate():
    from tiny_brain.memory.extractor import should_trigger_extraction

    assert not should_trigger_extraction("What is Tiny Brain?")
    assert not should_trigger_extraction("Draft a note about caching")
    assert not should_trigger_extraction("hello")
    assert not should_trigger_extraction("How does the P.A.R.A. framework work?")

    assert should_trigger_extraction("Remember: I decided to use Q6 for deployment.")
    assert should_trigger_extraction("I decided to switch to SQLite.")
    assert should_trigger_extraction("My preference is dark mode.")
    assert should_trigger_extraction("I prefer markdown for notes.")
    assert should_trigger_extraction("TODO: update the BIOS tomorrow")
    assert should_trigger_extraction("I am working on the search pipeline")


@pytest.mark.asyncio
async def test_memory_extractor_skips_untriggered_message():
    from tiny_brain.memory.extractor import MemoryExtractor

    class MockLlama:
        def __init__(self):
            self.called = False

        async def complete(self, *args, **kwargs):
            self.called = True
            return {}

    mock = MockLlama()
    extractor = MemoryExtractor(mock)
    candidates = await extractor.extract("What is the meaning of life?")
    assert candidates == []
    assert not mock.called


@pytest.mark.asyncio
async def test_memory_extractor_handles_gbnf_grammar_output():
    from tiny_brain.memory.extractor import MemoryExtractor

    class MockLlama:
        def __init__(self):
            self.passed_grammar = None

        async def complete(self, messages, **kwargs):
            self.passed_grammar = kwargs.get("grammar")
            return {
                "choices": [{
                    "message": {
                        "content": '{\n  "type": "decision",\n  "conf": 0.95,\n  "fact": "Use Q6 for deployment."\n}'
                    }
                }]
            }

    mock = MockLlama()
    extractor = MemoryExtractor(mock)
    candidates = await extractor.extract("Remember: I decided to use Q6 for deployment.", source_message_id="msg-1")
    assert len(candidates) == 1
    assert candidates[0].category == "decision"
    assert candidates[0].confidence == 0.95
    assert candidates[0].claim == "Use Q6 for deployment."
    assert candidates[0].source_message_id == "msg-1"
    assert mock.passed_grammar is not None

