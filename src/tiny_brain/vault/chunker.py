from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .parser import ParsedNote


@dataclass(frozen=True, slots=True)
class Section:
    section_id: str
    relative_path: str
    title: str
    heading_path: tuple[str, ...]
    heading_level: int
    ordinal: int
    content: str
    start_offset: int
    end_offset: int
    token_estimate: int
    content_hash: str


def chunk_note(note: ParsedNote, *, max_chars: int = 6000, overlap_chars: int = 240) -> list[Section]:
    boundaries = [heading.start for heading in note.headings]
    starts = [0, *boundaries]
    ends = boundaries[1:] + [len(note.content)]
    sections: list[Section] = []
    heading_stack: list[tuple[int, str]] = []
    heading_by_start = {heading.start: heading for heading in note.headings}
    ordinal = 0
    for start, end in zip(starts, ends):
        heading = heading_by_start.get(start)
        if heading:
            while heading_stack and heading_stack[-1][0] >= heading.level:
                heading_stack.pop()
            heading_stack.append((heading.level, heading.text))
        content = note.content[start:end].strip()
        if not content:
            continue
        pieces = _split(content, max_chars=max_chars, overlap_chars=overlap_chars)
        for piece_index, piece in enumerate(pieces):
            section_start = start + content.find(piece) if piece_index == 0 else start
            section_id = hashlib.sha256(f"{note.relative_path}:{ordinal}:{piece}".encode("utf-8")).hexdigest()[:24]
            sections.append(Section(
                section_id=section_id,
                relative_path=note.relative_path,
                title=note.title,
                heading_path=tuple(item[1] for item in heading_stack),
                heading_level=heading.level if heading else 0,
                ordinal=ordinal,
                content=piece,
                start_offset=section_start,
                end_offset=min(end, section_start + len(piece)),
                token_estimate=max(1, len(piece.split())),
                content_hash=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
            ))
            ordinal += 1
    return sections


def _split(content: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if len(content) <= max_chars:
        return [content]
    pieces: list[str] = []
    start = 0
    while start < len(content):
        end = min(len(content), start + max_chars)
        if end < len(content):
            paragraph_break = content.rfind("\n\n", start, end)
            if paragraph_break > start + max_chars // 2:
                end = paragraph_break
        piece = content[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(content):
            break
        start = max(start + 1, end - overlap_chars)
    return pieces
