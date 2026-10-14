from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def run(vault: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def has_commit(vault: Path) -> bool:
    result = subprocess.run(["git", "-C", str(vault), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True)
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotently initialize the vault Git repository")
    parser.add_argument("vault", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    if not (args.vault / ".git").exists():
        run(args.vault, "init")
    run(args.vault, "config", "user.name", args.name)
    run(args.vault, "config", "user.email", args.email)
    if not has_commit(args.vault):
        run(args.vault, "add", "--all")
        run(args.vault, "commit", "-m", "tiny-brain: initial vault import")
    print(run(args.vault, "rev-parse", "HEAD"))


if __name__ == "__main__":
    main()
