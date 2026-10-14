from __future__ import annotations

import argparse
import json
import time

from tiny_brain.search.embeddings import FastEmbedProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--text", action="append", default=["Tiny Brain indexes local Obsidian notes."])
    args = parser.parse_args()
    provider = FastEmbedProvider(args.model)
    started = time.perf_counter()
    vectors = provider.embed(args.text)
    elapsed = time.perf_counter() - started
    print(json.dumps({"model": args.model, "runtime": provider.runtime, "count": len(vectors), "dimension": len(vectors[0]), "elapsed_seconds": elapsed}, indent=2))


if __name__ == "__main__":
    main()
