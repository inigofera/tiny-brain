from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
TAG_RE = re.compile(r"(?<![\w-])#([A-Za-z0-9_/-]+)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True, slots=True)
class Link:
    target: str
    alias: str | None = None
    heading: str | None = None
    kind: str = "wikilink"


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str
    start: int
    end: int


@dataclass(slots=True)
class ParsedNote:
    relative_path: str
    content: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    frontmatter_text: str = ""
    title: str = ""
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def parse_note(relative_path: str, content: str) -> ParsedNote:
    body, frontmatter, frontmatter_text = _split_frontmatter(content)
    headings: list[Heading] = []
    links: list[Link] = []
    tags: set[str] = set(_frontmatter_tags(frontmatter))
    in_fence = False
    fence_marker = ""
    offset = len(content) - len(body)
    cursor = offset
    first_heading: str | None = None
    for line in body.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        stripped = raw.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            cursor += len(line)
            continue
        if not in_fence:
            match = HEADING_RE.match(raw)
            if match:
                heading_text = match.group(2).strip()
                heading = Heading(len(match.group(1)), heading_text, cursor, cursor + len(raw))
                headings.append(heading)
                first_heading = first_heading or heading_text
            links.extend(_parse_wikilinks(raw))
            links.extend(Link(target=match.group(1), kind="markdown") for match in MARKDOWN_LINK_RE.finditer(raw))
            tags.update(
                match.group(1).lower()
                for match in TAG_RE.finditer(raw)
                if not re.match(r"^#{1,6}\s", raw.lstrip())
            )
        cursor += len(line)
    title = str(frontmatter.get("title") or first_heading or Path(relative_path).stem)
    return ParsedNote(
        relative_path=relative_path,
        content=content,
        frontmatter=frontmatter,
        frontmatter_text=frontmatter_text,
        title=title,
        headings=headings,
        links=links,
        tags=sorted(tags),
    )


def parse_file(vault_root: Path, relative_path: str) -> ParsedNote:
    path = vault_root / Path(*relative_path.split("/"))
    return parse_note(relative_path, path.read_text(encoding="utf-8"))


def _split_frontmatter(content: str) -> tuple[str, dict[str, Any], str]:
    if not content.startswith("---"):
        return content, {}, ""
    first_end = content.find("\n")
    if first_end < 0:
        return content, {}, ""
    marker = content[first_end + 1 :]
    close_match = re.search(r"^---\s*$", marker, flags=re.MULTILINE)
    if not close_match:
        return content, {}, ""
    yaml_text = marker[: close_match.start()]
    body_start = first_end + 1 + close_match.end()
    if content[body_start : body_start + 1] == "\n":
        body_start += 1
    parsed = yaml.safe_load(yaml_text) or {}
    if not isinstance(parsed, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return content[body_start:], dict(parsed), yaml_text


def _frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    raw = frontmatter.get("tags", [])
    if isinstance(raw, str):
        return [raw.lstrip("#").lower()]
    if isinstance(raw, list):
        return [str(item).lstrip("#").lower() for item in raw]
    return []


def _parse_wikilinks(line: str) -> list[Link]:
    results: list[Link] = []
    for match in WIKILINK_RE.finditer(line):
        value = match.group(1).strip()
        target, alias = (value.split("|", 1) + [None])[:2] if "|" in value else (value, None)
        target, heading = (target.split("#", 1) + [None])[:2] if "#" in target else (target, None)
        results.append(Link(target=target.strip(), alias=alias.strip() if alias else None, heading=heading.strip() if heading else None))
    return results
