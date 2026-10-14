from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

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


def reciprocal_rank_fusion(*hit_lists: Sequence[Any], limit: int = 20, constant: float = 60.0) -> list[RankedHit]:
    """Multi-channel Reciprocal Rank Fusion across lexical, semantic, backlink, and tag hits."""
    merged: dict[str, RankedHit] = {}
    for hit_list in hit_lists:
        if not hit_list:
            continue
        for rank, hit in enumerate(hit_list, start=1):
            if isinstance(hit, LexicalHit):
                reasons = ["lexical match"]
            elif isinstance(hit, RankedHit):
                reasons = list(hit.reasons) if hit.reasons else ["match"]
            else:
                reasons = list(getattr(hit, "reasons", ["match"]))

            item = merged.setdefault(
                hit.section_id,
                RankedHit(hit.section_id, hit.relative_path, hit.title, hit.heading_path, hit.content, 0.0, []),
            )
            item.score += 1.0 / (constant + rank)
            for reason in reasons:
                if reason not in item.reasons:
                    item.reasons.append(reason)

    return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]
