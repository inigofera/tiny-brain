from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MemoryCategory = Literal[
    "ephemeral", "working_memory", "user_fact", "decision", "project_information",
    "idea", "task", "reference", "permanent_knowledge",
]


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    category: MemoryCategory
    confidence: float
    claim: str
    subject: str = ""
    related_paths: tuple[str, ...] = ()
    explicit: bool = True
    source_message_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category, "confidence": self.confidence, "claim": self.claim,
            "subject": self.subject, "related_paths": list(self.related_paths),
            "explicit": self.explicit, "source_message_id": self.source_message_id,
        }
