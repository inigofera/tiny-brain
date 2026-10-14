from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..db import Database
from ..vault.indexer import VaultIndexer
from ..vault.paths import resolve_vault_path
from .audit import AuditLog
from .git import GitService
from .merge import three_way_merge

ALLOWED_OPERATIONS = {"create_note", "append_to_note", "update_frontmatter", "propose_edit", "inbox_append"}


class ChangeService:
    def __init__(self, vault_root: Path, database: Database, indexer: VaultIndexer, audit: AuditLog, git: GitService | None = None):
        self.vault_root = vault_root
        self.database = database
        self.indexer = indexer
        self.audit = audit
        self.git = git or GitService(vault_root)

    def create_proposal(self, *, operation: str, target_path: str, proposed_content: str, description: str, reason: str, session_id: str | None = None, source_message_id: str | None = None) -> str:
        if operation not in ALLOWED_OPERATIONS or operation == "inbox_append":
            raise ValueError("operation is not allowed for a normal proposal")
        if len(proposed_content) > 1_000_000:
            raise ValueError("proposed content is too large")
        path = resolve_vault_path(self.vault_root, target_path)
        base_content = path.read_text(encoding="utf-8") if path.exists() else ""
        change_id = str(uuid.uuid4())
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO change_proposals(change_id, session_id, source_message_id, operation, target_path,
                   description, reason, base_hash, base_content, proposed_content, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (change_id, session_id, source_message_id, operation, target_path, description, reason,
                 _hash(base_content), base_content, proposed_content, now, now),
            )
        self.audit.record(str(uuid.uuid4()), "proposal_created", {"change_id": change_id, "target_path": target_path, "operation": operation, "base_hash": _hash(base_content)}, change_id=change_id)
        return change_id

    def reject(self, change_id: str, reason: str = "rejected by user") -> None:
        self._set_state(change_id, "rejected")
        self.audit.record(str(uuid.uuid4()), "proposal_rejected", {"change_id": change_id, "reason": reason}, change_id=change_id)

    def revise_pending(self, change_id: str, proposed_content: str) -> None:
        proposal = self._get(change_id)
        if proposal is None:
            raise KeyError(change_id)
        if proposal["state"] != "pending":
            raise ValueError(f"proposal is {proposal['state']}")
        if not proposed_content.strip() or len(proposed_content) > 1_000_000:
            raise ValueError("proposed content is empty or too large")
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE change_proposals SET proposed_content = ?, updated_at = ? WHERE change_id = ?",
                (proposed_content, _now(), change_id),
            )
        self.audit.record(str(uuid.uuid4()), "proposal_revised", {"change_id": change_id, "proposed_hash": _hash(proposed_content)}, change_id=change_id)

    def has_pending_for(self, target_path: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM change_proposals WHERE target_path = ? AND state = 'pending' LIMIT 1", (target_path,)
            ).fetchone()
        return row is not None

    def pending_with_description(self, prefixes: tuple[str, ...], limit: int) -> list[dict[str, object]]:
        clause = " OR ".join("description LIKE ?" for _ in prefixes)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM change_proposals WHERE state = 'pending' AND ({clause}) ORDER BY updated_at DESC LIMIT ?",
                (*(prefix + "%" for prefix in prefixes), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def revert(self, change_id: str) -> str:
        proposal = self._get(change_id)
        if proposal is None:
            raise KeyError(change_id)
        if proposal["state"] != "committed" or proposal["base_content"] is None:
            raise ValueError("only committed proposals with stored before-state can be reverted")
        revert_id = self.create_proposal(
            operation="propose_edit", target_path=proposal["target_path"],
            proposed_content=proposal["base_content"], description=f"revert {change_id}",
            reason="audited revert of an approved change", session_id=proposal["session_id"],
            source_message_id=proposal["source_message_id"],
        )
        commit = self.approve(revert_id)
        self._set_state(change_id, "reverted", git_commit_id=commit)
        self.audit.record(str(uuid.uuid4()), "proposal_reverted", {"change_id": change_id, "revert_change_id": revert_id, "git_commit_id": commit}, change_id=change_id)
        return commit

    def approve(self, change_id: str) -> str:
        proposal = self._get(change_id)
        if proposal is None:
            raise KeyError(change_id)
        if proposal["state"] != "pending":
            raise ValueError(f"proposal is {proposal['state']}")
        path = resolve_vault_path(self.vault_root, proposal["target_path"])
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        current_hash = _hash(current)
        content = proposal["proposed_content"]
        if current_hash != proposal["base_hash"]:
            merge = three_way_merge(proposal["base_content"] or "", current, content)
            if not merge.clean:
                self._set_state(change_id, "conflict", current_hash=current_hash, conflict_data=merge.conflict)
                self.audit.record(str(uuid.uuid4()), "proposal_conflict", {"change_id": change_id, "current_hash": current_hash, "conflict": merge.conflict}, change_id=change_id)
                return "conflict"
            content = merge.content
        self._set_state(change_id, "approved", current_hash=current_hash)
        _atomic_write(path, content)
        self._set_state(change_id, "write_applied", current_hash=_hash(content))
        try:
            self.indexer.index_path(proposal["target_path"])
            self._set_state(change_id, "indexed", current_hash=_hash(content))
            commit = self.git.commit_file(proposal["target_path"], f"tiny-brain: {proposal['description']}")
        except Exception as exc:
            self._set_state(change_id, "recovery_required", current_hash=_hash(content), conflict_data=str(exc))
            self.audit.record(str(uuid.uuid4()), "proposal_recovery_required", {"change_id": change_id, "error": str(exc)}, change_id=change_id)
            raise
        self._set_state(change_id, "committed", current_hash=_hash(content), git_commit_id=commit)
        self.audit.record(str(uuid.uuid4()), "proposal_committed", {"change_id": change_id, "target_path": proposal["target_path"], "git_commit_id": commit}, change_id=change_id)
        return commit

    def _get(self, change_id: str):
        with self.database.connect() as connection:
            return connection.execute("SELECT * FROM change_proposals WHERE change_id = ?", (change_id,)).fetchone()

    def _set_state(self, change_id: str, state: str, *, current_hash: str | None = None, conflict_data: str | None = None, git_commit_id: str | None = None) -> None:
        fields = ["state = ?", "updated_at = ?"]
        values: list[object] = [state, _now()]
        for name, value in (("current_hash", current_hash), ("conflict_data", conflict_data), ("git_commit_id", git_commit_id)):
            if value is not None:
                fields.append(f"{name} = ?")
                values.append(value)
        values.append(change_id)
        with self.database.connect() as connection:
            connection.execute(f"UPDATE change_proposals SET {', '.join(fields)} WHERE change_id = ?", values)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tiny-brain-{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
