from __future__ import annotations

import time
from pathlib import Path

from .indexer import VaultIndexer, iter_markdown_paths


class PollingWatcher:
    """Small dependency-free watcher suitable for a low-volume local vault."""

    def __init__(self, vault_root: Path, indexer: VaultIndexer, interval: float = 2.0):
        self.vault_root = vault_root
        self.indexer = indexer
        self.interval = interval
        self._snapshot: dict[str, tuple[int, int]] = {}

    def scan_once(self) -> dict[str, int]:
        current: dict[str, tuple[int, int]] = {}
        changed = 0
        for relative in iter_markdown_paths(self.vault_root):
            path = self.vault_root / Path(*relative.split("/"))
            stat = path.stat()
            current[relative] = (stat.st_size, stat.st_mtime_ns)
            if self._snapshot.get(relative) != current[relative]:
                self.indexer.index_path(relative)
                changed += 1
        for deleted in set(self._snapshot) - set(current):
            self.indexer.remove_path(deleted)
            changed += 1
        self._snapshot = current
        return {"changed": changed}

    def run_forever(self) -> None:
        while True:
            self.scan_once()
            time.sleep(self.interval)
