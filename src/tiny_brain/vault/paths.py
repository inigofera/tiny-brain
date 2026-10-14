from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


class UnsafePath(ValueError):
    """Raised when a user or model supplied path leaves the vault policy."""


def validate_relative_path(value: str, *, markdown_only: bool = True) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise UnsafePath("path must be a non-empty string without NUL bytes")
    if "\\" in value:
        raise UnsafePath("backslash paths are not accepted")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafePath("path must be relative and normalized")
    if any(part.lower() in {".git", ".obsidian"} for part in path.parts):
        raise UnsafePath("service directories are not knowledge paths")
    normalized = path.as_posix()
    if markdown_only and Path(normalized).suffix.lower() != ".md":
        raise UnsafePath("only Markdown paths are supported")
    return normalized


def resolve_vault_path(vault_root: Path, relative_path: str, *, markdown_only: bool = True) -> Path:
    normalized = validate_relative_path(relative_path, markdown_only=markdown_only)
    root = vault_root.expanduser().resolve()
    candidate = root / Path(*PurePosixPath(normalized).parts)
    existing_parent = candidate if candidate.exists() else candidate.parent
    resolved_parent = existing_parent.resolve()
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise UnsafePath("path resolves outside the vault") from exc
    if candidate.exists() and candidate.is_symlink():
        raise UnsafePath("symlink targets are not accepted")
    return candidate


def is_indexable_markdown(vault_root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(vault_root.resolve())
    except ValueError:
        return False
    if path.is_symlink() or path.suffix.lower() != ".md":
        return False
    parts = {part.lower() for part in relative.parts}
    if parts.intersection({".git", ".obsidian"}):
        return False
    name = path.name.lower()
    return not (name.startswith(".~") or name.endswith("~") or ".sync-conflict-" in name)
