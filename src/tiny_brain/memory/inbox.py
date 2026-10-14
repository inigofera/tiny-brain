from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

from .classifier import MemoryCandidate


def append_inbox(vault_root: Path, candidate: MemoryCandidate, provenance_id: str) -> Path:
    relative = "_AI/Inbox.md"
    path = vault_root / "_AI" / "Inbox.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        f"\n## {date.today().isoformat()}\n\n"
        f"### {candidate.category.replace('_', ' ').title()}: {candidate.subject or 'conversation note'}\n\n"
        f"{candidate.claim.strip()}\n\nProvenance: `{provenance_id}`\n"
    )
    old = path.read_text(encoding="utf-8") if path.exists() else "# AI Inbox\n"
    _atomic_replace(path, old + entry)
    return Path(relative)


def provenance_id(candidate: MemoryCandidate) -> str:
    digest = hashlib.sha256(f"{candidate.category}:{candidate.subject}:{candidate.claim}".encode("utf-8")).hexdigest()[:16]
    return f"memory-{digest}"


def _atomic_replace(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tiny-brain-{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
