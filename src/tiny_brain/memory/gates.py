from __future__ import annotations

from dataclasses import dataclass

from .classifier import MemoryCandidate


@dataclass(frozen=True, slots=True)
class GateDecision:
    accepted: bool
    decision: str
    reason: str


class MemoryGate:
    def __init__(self, confidence_threshold: float = 0.9):
        self.confidence_threshold = confidence_threshold

    def evaluate(self, candidate: MemoryCandidate, *, duplicate: bool = False) -> GateDecision:
        if candidate.category in {"ephemeral", "working_memory", "idea", "task", "reference"}:
            return GateDecision(False, "session_only", "category is not eligible for automatic Inbox writes")
        if not candidate.explicit:
            return GateDecision(False, "review", "candidate is not an explicit statement")
        if candidate.confidence < self.confidence_threshold:
            return GateDecision(False, "review", "confidence is below the automatic write threshold")
        if duplicate:
            return GateDecision(False, "review", "candidate duplicates or may contradict existing content")
        return GateDecision(True, "auto_inbox", "high-confidence explicit durable candidate")
