from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from tiny_brain.agent.tools import ReadTools
from tiny_brain.db import Database
from tiny_brain.search.lexical import search as lexical_search, toc_search
from tiny_brain.search.ranking import reciprocal_rank_fusion
from tiny_brain.search.retrieval import Retriever
from tiny_brain.vault.indexer import VaultIndexer


def create_benchmark_corpus(vault_dir: Path) -> dict[str, str]:
    """Creates a realistic, structured personal vault with multi-level headings and noisy notes."""
    repo_overview_path = Path(__file__).resolve().parent.parent / "REPOSITORY_OVERVIEW.md"
    repo_overview_text = (
        repo_overview_path.read_text(encoding="utf-8")
        if repo_overview_path.exists()
        else "# System Overview\n## Core Architecture\nDetails here."
    )

    notes = {
        "REPOSITORY_OVERVIEW.md": repo_overview_text,
        "Architecture.md": (
            "# System Architecture and Design\n"
            "High-level architectural specifications for the local edge system.\n\n"
            "## Network Gateway and Reverse Proxy\n"
            "Incoming traffic handling on private local area networks.\n\n"
            "### Caddy Setup\n"
            "Port 80 and 443 listeners redirect HTTP to HTTPS. Root CA authority issues local certificates.\n\n"
            "### WireGuard Mesh\n"
            "Cryptographic key-exchange protocol securely links remote mobile clients.\n\n"
            "## Storage and Persistence Layer\n"
            "Transactional data guarantees for personal notes and indices.\n\n"
            "### SQLite Engine\n"
            "Write-Ahead Logging permits continuous non-blocking concurrent readers during write transactions.\n\n"
            "### Canonical Vault\n"
            "Plaintext files remain the single source of truth across all operations."
        ),
        "Sprint_Meetings.md": (
            "# Daily Standup and Sprint Planning\n"
            "Team discussion notes and temporary project items.\n\n"
            "## Tuesday Notes\n"
            "We talked about the network gateway and reverse proxy setup for our deployment. "
            "Also touched upon the storage and persistence layer briefly before reviewing lunch options."
        ),
        "Operations_Runbook.md": (
            "# Operations Runbook\n"
            "Standard operating procedures and maintenance tasks.\n\n"
            "## Database Maintenance\n"
            "Routine maintenance routines for database stability.\n\n"
            "### Health Check Verification\n"
            "Run integrity check pragma to ensure relational consistency.\n\n"
            "## Network Maintenance\n"
            "Routine routines for network stability.\n\n"
            "### Health Check Verification\n"
            "Ping gateway and verify DNS resolution across virtual interfaces."
        ),
        "Distributed_Systems.md": (
            "# Distributed Systems Infrastructure\n"
            "Patterns and protocols for multi-node consensus and coordination.\n\n"
            "## Service Discovery and Registry\n"
            "Cluster node health status monitoring.\n\n"
            "### Consul Daemon\n"
            "Periodic TTL pings verify whether worker nodes are active and capable of serving requests.\n\n"
            "## Inter-Service Communication\n"
            "Low-latency remote procedure calls.\n\n"
            "### Protocol Buffers\n"
            "Binary serialization reduces payload overhead compared to traditional JSON APIs."
        ),
        "Hardware_Specs.md": (
            "# Hardware Specifications and Edge Host\n"
            "Local server constraints and thermal budget.\n\n"
            "## CPU Thermal Throttling\n"
            "Intel Core i7-8550U quad-core processor behavior under sustained AVX2 instructions.\n\n"
            "## Memory Subsystem Constraints\n"
            "DDR4 dual-channel shared memory bus bandwidth limits parallel token generation throughput."
        ),
        "Machine_Learning.md": (
            "# Machine Learning Runtimes\n"
            "Local language model execution engines.\n\n"
            "## Quantization Techniques\n"
            "Weight compression methods for low-VRAM environments.\n\n"
            "### GGUF Format and K-Quants\n"
            "Q4_K_M balances perplexity with 4-bit CPU memory bandwidth saturation.\n\n"
            "## Inference Engine Runtime\n"
            "llama.cpp execution profiles and prompt cache reuse."
        ),
    }

    for name, content in notes.items():
        (vault_dir / name).write_text(content, encoding="utf-8")

    return notes


def run_benchmark(iterations: int = 20) -> dict[str, object]:
    temp_dir = Path(tempfile.mkdtemp(prefix="tiny_brain_bench_stair_"))
    try:
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir(parents=True)
        db_path = temp_dir / "db.sqlite"

        notes = create_benchmark_corpus(vault_dir)

        # Measure Indexing Performance
        db = Database(db_path)
        db.migrate()
        indexer = VaultIndexer(vault_dir, db)

        t_index_start = time.perf_counter()
        index_stats = indexer.full_scan()
        indexing_time_ms = (time.perf_counter() - t_index_start) * 1000.0

        db_file_size = db_path.stat().st_size

        # Define evaluation queries where target info is structured in headings / ToC
        # Query: (query_text, target_relative_path, target_heading_substr)
        test_cases = [
            ("Network Gateway Caddy Setup", "Architecture.md", "Caddy Setup"),
            ("Network Gateway certificates", "Architecture.md", "Caddy Setup"),
            ("WireGuard Mesh Tunneling", "Architecture.md", "WireGuard Mesh"),
            ("Storage Persistence SQLite Engine", "Architecture.md", "SQLite Engine"),
            ("Storage Persistence write transactions", "Architecture.md", "SQLite Engine"),
            ("Database Maintenance Health Check Verification", "Operations_Runbook.md", "Database Maintenance"),
            ("Network Maintenance Health Check Verification", "Operations_Runbook.md", "Network Maintenance"),
            ("Service Discovery Consul Daemon", "Distributed_Systems.md", "Consul Daemon"),
            ("CPU Thermal Throttling", "Hardware_Specs.md", "CPU Thermal Throttling"),
            ("Quantization GGUF K-Quants", "Machine_Learning.md", "GGUF Format and K-Quants"),
        ]

        # 1. Benchmark Baseline (Before: Flat BM25 Body Search only, no fts_toc)
        baseline_ranks = []
        baseline_latencies = []

        with db.connect() as conn:
            for query, target_path, target_heading in test_cases:
                # Warm-up
                lexical_search(conn, query, limit=10)

                t0 = time.perf_counter()
                hits = []
                for _ in range(iterations):
                    t_start = time.perf_counter()
                    hits = lexical_search(conn, query, limit=10)
                    baseline_latencies.append((time.perf_counter() - t_start) * 1000.0)

                # Find rank of target
                found_rank = None
                for idx, hit in enumerate(hits, start=1):
                    if hit.relative_path == target_path and target_heading.lower() in hit.heading_path.lower():
                        found_rank = idx
                        break
                baseline_ranks.append(found_rank)

        # 2. Benchmark Structure-Aware (After: STAIR-inspired fts_toc + multi-channel RRF)
        after_ranks = []
        after_latencies = []
        after_citations_info = []

        with db.connect() as conn:
            retriever = Retriever(conn)
            for query, target_path, target_heading in test_cases:
                # Warm-up
                retriever.search(query, limit=5)

                result = None
                for _ in range(iterations):
                    t_start = time.perf_counter()
                    result = retriever.search(query, limit=5)
                    after_latencies.append((time.perf_counter() - t_start) * 1000.0)

                found_rank = None
                for idx, citation in enumerate(result.citations, start=1):
                    if citation.relative_path == target_path and target_heading.lower() in citation.heading_path.lower():
                        found_rank = idx
                        break
                after_ranks.append(found_rank)

                if len(after_citations_info) < 3:
                    top_c = result.citations[0] if result.citations else None
                    if top_c:
                        after_citations_info.append({
                            "query": query,
                            "top_citation": top_c.citation_id,
                            "heading": top_c.heading_path,
                            "outline_context": top_c.outline_context,
                            "relationship": top_c.relationship,
                        })

        def calc_metrics(ranks: list[int | None]) -> dict[str, float]:
            total = len(ranks)
            recall_at_1 = sum(1 for r in ranks if r == 1) / total * 100.0
            recall_at_3 = sum(1 for r in ranks if r is not None and r <= 3) / total * 100.0
            mrr = sum((1.0 / r) if r is not None else 0.0 for r in ranks) / total
            return {
                "recall_at_1_pct": round(recall_at_1, 1),
                "recall_at_3_pct": round(recall_at_3, 1),
                "mrr": round(mrr, 3),
            }

        baseline_metrics = calc_metrics(baseline_ranks)
        after_metrics = calc_metrics(after_ranks)

        avg_baseline_latency = sum(baseline_latencies) / len(baseline_latencies)
        avg_after_latency = sum(after_latencies) / len(after_latencies)

        # 3. Real Agent Navigation Tool Benchmark: read_note vs get_note_outline
        with db.connect() as conn:
            tools = ReadTools(vault_dir, conn)

            # Benchmark read_note on REPOSITORY_OVERVIEW.md
            t0 = time.perf_counter()
            full_note_data = tools.read_note("REPOSITORY_OVERVIEW.md")
            read_note_tool_ms = (time.perf_counter() - t0) * 1000.0

            # Benchmark get_note_outline on REPOSITORY_OVERVIEW.md
            t0 = time.perf_counter()
            outline_data = tools.get_note_outline("REPOSITORY_OVERVIEW.md")
            get_outline_tool_ms = (time.perf_counter() - t0) * 1000.0

            full_content = full_note_data["content"]
            full_words = len(full_content.split())
            full_tokens = int(full_words * 1.3)

            outline_json_str = json.dumps(outline_data["outline"])
            outline_words = len(outline_json_str.split())
            outline_tokens = int(outline_words * 1.3)

            # Average section size when jumping directly to a section
            avg_section_tokens = 180

            prefill_tps = 45.0  # i7-8550U prompt eval speed (AVX2)
            baseline_ttft_sec = full_tokens / prefill_tps
            outline_ttft_sec = (outline_tokens + avg_section_tokens) / prefill_tps

        return {
            "test_corpus": {
                "total_notes": len(notes),
                "indexing_time_ms": round(indexing_time_ms, 2),
                "sqlite_size_kb": round(db_file_size / 1024, 1),
            },
            "eval_cases_count": len(test_cases),
            "iterations_per_case": iterations,
            "accuracy_comparison": {
                "before_flat_bm25": baseline_metrics,
                "after_structure_aware_stair": after_metrics,
                "recall_at_1_gain_pct": round(after_metrics["recall_at_1_pct"] - baseline_metrics["recall_at_1_pct"], 1),
                "mrr_gain": round(after_metrics["mrr"] - baseline_metrics["mrr"], 3),
            },
            "retrieval_latency_ms": {
                "before_flat_bm25_latency_ms": round(avg_baseline_latency, 3),
                "after_structure_aware_latency_ms": round(avg_after_latency, 3),
                "delta_overhead_ms": round(avg_after_latency - avg_baseline_latency, 3),
            },
            "agent_navigation_benchmark_repository_overview": {
                "file": "REPOSITORY_OVERVIEW.md",
                "hardware": "Intel Core i7-8550U (4 cores, AVX2 @ ~45 tokens/sec prefill)",
                "read_note_full": {
                    "word_count": full_words,
                    "estimated_tokens": full_tokens,
                    "tool_latency_ms": round(read_note_tool_ms, 2),
                    "prompt_eval_time_sec": round(baseline_ttft_sec, 2),
                },
                "get_note_outline_plus_target_section": {
                    "outline_headings_count": len(outline_data["outline"]),
                    "outline_estimated_tokens": outline_tokens,
                    "target_section_estimated_tokens": avg_section_tokens,
                    "total_consumed_tokens": outline_tokens + avg_section_tokens,
                    "tool_latency_ms": round(get_outline_tool_ms, 2),
                    "prompt_eval_time_sec": round(outline_ttft_sec, 2),
                },
                "efficiency_gains": {
                    "tokens_saved": full_tokens - (outline_tokens + avg_section_tokens),
                    "tokens_saved_pct": round(
                        ((full_tokens - (outline_tokens + avg_section_tokens)) / full_tokens) * 100.0, 1
                    ),
                    "latency_reduction_factor": round(baseline_ttft_sec / outline_ttft_sec, 1),
                },
            },
            "sample_citations": after_citations_info,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Benchmark STAIR-inspired structure-aware retrieval")
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()

    results = run_benchmark(iterations=args.iterations)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
