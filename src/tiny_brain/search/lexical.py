from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


TERM_RE = re.compile(r"[\w][\w.+-]*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class LexicalHit:
    section_id: str
    relative_path: str
    title: str
    heading_path: str
    content: str
    rank: float
    reason: str = "lexical match"


def search(connection: sqlite3.Connection, query: str, limit: int = 20) -> list[LexicalHit]:
    raw_terms = TERM_RE.findall(query)
    if not raw_terms:
        return []

    from ..agent.search_planner import STOPWORDS

    # Strip punctuation and filter stopwords
    clean_terms: list[str] = []
    for term in raw_terms:
        cleaned = term.strip(".-_").lower().replace('"', "")
        if cleaned and cleaned not in STOPWORDS:
            clean_terms.append(cleaned)

    # If all terms were stopwords or punctuation, fall back to single raw token if 1-term, else empty
    if not clean_terms:
        if len(raw_terms) == 1 and raw_terms[0].strip(".-_"):
            clean_terms = [raw_terms[0].strip(".-_").lower().replace('"', "")]
        else:
            return []

    # 1. First try strict AND matching for high-precision hits
    and_query = " AND ".join(f'"{term}"*' for term in clean_terms[:16])
    rows = connection.execute(
        """SELECT section_id, relative_path, title, heading_path, content, bm25(fts_sections) AS rank
           FROM fts_sections WHERE fts_sections MATCH ? ORDER BY rank LIMIT ?""",
        (and_query, max(1, min(limit, 100))),
    ).fetchall()
    if rows:
        return [LexicalHit(*row) for row in rows]

    # Single-term queries with no match have no fallback
    if len(clean_terms) <= 1:
        return []

    # 2. For multi-term queries where no single note contains ALL terms, evaluate OR matches
    # but require that a document matches at least 2 distinct query terms (preventing 1-word spurious hits)
    min_required_matches = min(2, len(clean_terms))
    or_query = " OR ".join(f'"{term}"*' for term in clean_terms[:16])
    candidate_rows = connection.execute(
        """SELECT section_id, relative_path, title, heading_path, content, bm25(fts_sections) AS rank
           FROM fts_sections WHERE fts_sections MATCH ? ORDER BY rank LIMIT ?""",
        (or_query, max(1, min(limit * 3, 100))),
    ).fetchall()

    filtered_hits: list[LexicalHit] = []
    for row in candidate_rows:
        hit = LexicalHit(*row)
        doc_text = f"{hit.relative_path} {hit.title} {hit.heading_path} {hit.content}".lower()
        matched_count = sum(1 for term in clean_terms[:16] if term in doc_text)
        if matched_count >= min_required_matches:
            filtered_hits.append(hit)
            if len(filtered_hits) >= limit:
                break

    return filtered_hits
