from __future__ import annotations

import argparse
from pathlib import Path

from tiny_brain.db import Database
from tiny_brain.vault.indexer import VaultIndexer


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or refresh the Tiny Brain vault index")
    parser.add_argument("vault", type=Path)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    database = Database(args.database)
    database.migrate()
    print(VaultIndexer(args.vault, database).full_scan())


if __name__ == "__main__":
    main()
