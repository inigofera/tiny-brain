from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..changes.audit import AuditLog
from ..db import Database
from ..vault.indexer import VaultIndexer
from .classifier import MemoryCandidate
from .gates import MemoryGate
from .inbox import append_inbox, provenance_id


class MemoryService:
    def __init__(self, vault_root: Path, database: Database, indexer: VaultIndexer, audit: AuditLog, *, enabled: bool = True, threshold: float = 0.9):
        self.vault_root = vault_root
        self.database = database
        self.indexer = indexer
        self.audit = audit
        self.enabled = enabled
        self.gate = MemoryGate(threshold)

    def process(self, candidate: MemoryCandidate) -> dict[str, object]:
        duplicate = self._is_duplicate(candidate)
        decision = self.gate.evaluate(candidate, duplicate=duplicate)
        candidate_id = str(uuid.uuid4())
        inbox_change_id = None
        if self.enabled and decision.accepted:
            inbox_change_id = provenance_id(candidate)
            append_inbox(self.vault_root, candidate, inbox_change_id)
            self.indexer.index_path("_AI/Inbox.md")
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO memory_candidates(candidate_id, source_message_id, category, confidence, subject,
                   claim, related_paths_json, gate_decision, gate_reason, duplicate_match, inbox_change_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate_id, candidate.source_message_id, candidate.category, candidate.confidence, candidate.subject,
                 candidate.claim, json.dumps(candidate.related_paths), decision.decision, decision.reason,
                 "duplicate" if duplicate else None, inbox_change_id, now),
            )
        self.audit.record(str(uuid.uuid4()), "memory_classified", {"candidate_id": candidate_id, **candidate.as_dict(), "gate": decision.decision, "reason": decision.reason}, change_id=inbox_change_id)
        return {"candidate_id": candidate_id, "gate": decision.decision, "accepted": decision.accepted, "reason": decision.reason, "provenance_id": inbox_change_id}

    def _is_duplicate(self, candidate: MemoryCandidate) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM fts_sections WHERE fts_sections MATCH ? LIMIT 1",
                (f'"{candidate.claim.replace(chr(34), "")}"',),
            ).fetchone()
        return row is not None
