from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitService:
    def __init__(self, vault_root: Path):
        self.vault_root = vault_root

    def commit_file(self, relative_path: str, message: str) -> str:
        self._run("add", "--", relative_path)
        result = self._run("commit", "-m", message)
        commit = self._run("rev-parse", "HEAD").strip()
        if not commit:
            raise GitError("git commit did not produce a commit id")
        return commit

    def status(self) -> str:
        return self._run("status", "--short")

    def _run(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.vault_root), *args],
                check=True, capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise GitError(detail.strip() or "git operation failed") from exc
        return result.stdout
