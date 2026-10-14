from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MergeResult:
    clean: bool
    content: str
    conflict: str | None = None


def three_way_merge(base: str, current: str, proposed: str) -> MergeResult:
    if current == base:
        return MergeResult(True, proposed)
    if proposed == base or current == proposed:
        return MergeResult(True, current)
    base_lines = base.splitlines(keepends=True)
    current_changes = _changes(base_lines, current.splitlines(keepends=True))
    proposed_changes = _changes(base_lines, proposed.splitlines(keepends=True))
    for left in current_changes:
        for right in proposed_changes:
            if _overlaps(left[0], left[1], right[0], right[1]):
                return MergeResult(False, current, "conflicting edits overlap")
    merged = base_lines[:]
    all_changes = [(start, end, replacement) for start, end, replacement in current_changes + proposed_changes]
    for start, end, replacement in sorted(all_changes, reverse=True):
        merged[start:end] = replacement
    return MergeResult(True, "".join(merged))


def _changes(base: list[str], updated: list[str]) -> list[tuple[int, int, list[str]]]:
    return [
        (start, end, updated[new_start:new_end])
        for tag, start, end, new_start, new_end in difflib.SequenceMatcher(None, base, updated).get_opcodes()
        if tag != "equal"
    ]


def _overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    if left_start == left_end == right_start == right_end:
        return True
    if left_start == left_end:
        return right_start <= left_start < right_end
    if right_start == right_end:
        return left_start <= right_start < left_end
    return max(left_start, right_start) < min(left_end, right_end)
