from __future__ import annotations

import argparse
import difflib
import json
import time

from tiny_brain.agent.vault_review import make_diff, splice_section_paragraph
from tiny_brain.vault.chunker import chunk_note
from tiny_brain.vault.parser import parse_note

SAMPLE_NOTE = """# Large Technical Specification
## Executive Summary
This document outlines the architecture for distributed real-time knowledge retrieval on constrained edge hardware.
It balances prompt token budget with low latency local inference.

## Ingestion Subsystem
The ingestion pipeline monitors filesystem events using inotify and filesystem hooks.
It parses markdown documents, strips frontmatter metadata, and computes cryptographic content hashes.

## Storage and Indexing Subsystem
SQLite serves as the relational and lexical backbone using FTS5 virtual tables.
Embeddings are computed via fast ONNX runtime models and indexed into vector tables.
Reciprocal rank fusion combines hits from multiple channels including links, tags, and semantic cosine similarity.

## LLM Inference & Memory
Inference runs locally via llama.cpp with Flash Attention enabled.
Prompt prefilling is optimized using deterministic keyword extraction and single-slot KV cache persistence.
"""


def benchmark_vault_review_splicing(iterations: int = 100) -> dict[str, object]:
    note = parse_note("spec.md", SAMPLE_NOTE)
    sections = chunk_note(note)
    target_section = sections[2]  # Storage and Indexing Subsystem

    new_paragraph = "Cross-encoder reranking via ms-marco-MiniLM-L-6-v2 scores candidates in under 20ms on CPU."

    # 1. Benchmark splicing latency
    t0 = time.perf_counter()
    for _ in range(iterations):
        spliced = splice_section_paragraph(
            SAMPLE_NOTE,
            sections,
            "append",
            target_section.section_id,
            new_paragraph,
        )
    splice_elapsed_ms = ((time.perf_counter() - t0) / iterations) * 1000

    # 2. Benchmark diff generation latency
    t1 = time.perf_counter()
    for _ in range(iterations):
        diff_lines, truncated = make_diff(SAMPLE_NOTE, spliced, "spec.md")
    diff_elapsed_ms = ((time.perf_counter() - t1) / iterations) * 1000

    # 3. Token footprint comparison:
    # Full note rewrite would require generating all tokens in the note (~250 words, ~350 tokens)
    # Section append only requires generating the target section_id and new paragraph (~15 words, ~25 tokens)
    full_note_words = len(SAMPLE_NOTE.split())
    section_append_words = len(new_paragraph.split())
    token_saving_ratio = full_note_words / section_append_words

    return {
        "iterations": iterations,
        "splice_latency_ms": round(splice_elapsed_ms, 4),
        "diff_generation_latency_ms": round(diff_elapsed_ms, 4),
        "full_note_words_burned": full_note_words,
        "section_paragraph_words_generated": section_append_words,
        "token_efficiency_gain": f"{token_saving_ratio:.1f}x fewer tokens generated",
        "diff_preview": [line for line in diff_lines if line.startswith(("+", "-"))][:5],
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark Section 2C vault review section diffing")
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()

    results = benchmark_vault_review_splicing(iterations=args.iterations)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
