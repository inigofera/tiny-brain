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
    terms = TERM_RE.findall(query)
    if not terms:
        return []
    # Quoting every token prevents user input from becoming FTS syntax.
    match_query = " OR ".join(f'"{term.replace(chr(34), "")}"*' for term in terms[:16])
    rows = connection.execute(
        """SELECT section_id, relative_path, title, heading_path, content, bm25(fts_sections) AS rank
           FROM fts_sections WHERE fts_sections MATCH ? ORDER BY rank LIMIT ?""",
        (match_query, max(1, min(limit, 100))),
    ).fetchall()
    return [LexicalHit(*row) for row in rows]
