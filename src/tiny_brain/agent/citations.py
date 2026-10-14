from __future__ import annotations

import re

from ..search.retrieval import RetrievalResult


CITATION_RE = re.compile(r"\[?(S\d+)\]?")


def extract_citation_ids(answer: str) -> list[str]:
    return list(dict.fromkeys(CITATION_RE.findall(answer)))


def validate_answer(answer: str, retrieval: RetrievalResult) -> tuple[bool, list[str]]:
    citation_ids = extract_citation_ids(answer)
    allowed = {citation.citation_id for citation in retrieval.citations}
    return bool(citation_ids) and set(citation_ids).issubset(allowed), citation_ids
