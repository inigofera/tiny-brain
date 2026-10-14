from __future__ import annotations

from dataclasses import dataclass

from .lexical import LexicalHit


@dataclass(slots=True)
class RankedHit:
    section_id: str
    relative_path: str
    title: str
    heading_path: str
    content: str
    score: float
    reasons: list[str]


def reciprocal_rank_fusion(lexical: list[LexicalHit], semantic: list[RankedHit], *, limit: int = 20) -> list[RankedHit]:
    merged: dict[str, RankedHit] = {}
    constant = 60.0
    for rank, hit in enumerate(lexical, start=1):
        item = merged.setdefault(hit.section_id, RankedHit(hit.section_id, hit.relative_path, hit.title, hit.heading_path, hit.content, 0.0, []))
        item.score += 1.0 / (constant + rank)
        if "lexical match" not in item.reasons:
            item.reasons.append("lexical match")
    for rank, hit in enumerate(semantic, start=1):
        item = merged.setdefault(hit.section_id, hit)
        item.score += 1.0 / (constant + rank)
        for reason in hit.reasons:
            if reason not in item.reasons:
                item.reasons.append(reason)
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]
