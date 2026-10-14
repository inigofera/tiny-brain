from pathlib import Path

import pytest

from tiny_brain.changes.audit import AuditLog
from tiny_brain.changes.git import GitService
from tiny_brain.changes.merge import three_way_merge
from tiny_brain.changes.service import ChangeService
from tiny_brain.db import Database
from tiny_brain.memory.classifier import MemoryCandidate
from tiny_brain.memory.gates import MemoryGate
from tiny_brain.vault.indexer import VaultIndexer


def test_three_way_merge_separates_and_rejects_overlapping_edits():
    assert three_way_merge("a\nb\nc\n", "A\nb\nc\n", "a\nb\nC\n").content == "A\nb\nC\n"
    assert not three_way_merge("a\nb\n", "A\nb\n", "B\nb\n").clean


def test_change_approval_is_audited_indexed_and_committed(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("# Note\nold\n", encoding="utf-8")
    database = Database(tmp_path / "data.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    indexer.full_scan()
    GitService(vault)._run("init")
    GitService(vault)._run("config", "user.email", "tiny-brain@test.invalid")
    GitService(vault)._run("config", "user.name", "Tiny Brain Test")
    GitService(vault).commit_file("note.md", "initial")
    service = ChangeService(vault, database, indexer, AuditLog(database, tmp_path / "data"), GitService(vault))

    change_id = service.create_proposal(operation="propose_edit", target_path="note.md", proposed_content="# Note\nnew\n", description="update note", reason="test")
    commit = service.approve(change_id)
    assert len(commit) >= 7
    assert note.read_text(encoding="utf-8") == "# Note\nnew\n"
    with database.connect() as connection:
        assert connection.execute("SELECT state FROM change_proposals WHERE change_id = ?", (change_id,)).fetchone()[0] == "committed"
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] >= 2


def test_stale_conflict_does_not_overwrite(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("a\nb\n", encoding="utf-8")
    database = Database(tmp_path / "data.sqlite")
    database.migrate()
    indexer = VaultIndexer(vault, database)
    indexer.full_scan()
    service = ChangeService(vault, database, indexer, AuditLog(database, tmp_path / "data"))
    change_id = service.create_proposal(operation="propose_edit", target_path="note.md", proposed_content="A\nb\n", description="change", reason="test")
    note.write_text("B\nb\n", encoding="utf-8")
    assert service.approve(change_id) == "conflict"
    assert note.read_text(encoding="utf-8") == "B\nb\n"


def test_memory_gate_is_conservative():
    gate = MemoryGate(0.9)
    assert not gate.evaluate(MemoryCandidate("idea", 0.99, "maybe")).accepted
    assert not gate.evaluate(MemoryCandidate("decision", 0.89, "decided")).accepted
    assert gate.evaluate(MemoryCandidate("decision", 0.95, "Use Q6")).accepted
    assert not gate.evaluate(MemoryCandidate("decision", 0.95, "Use Q6"), duplicate=True).accepted
