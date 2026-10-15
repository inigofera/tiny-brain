from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from tiny_brain.db import Database
from tiny_brain.search.reranking import FastEmbedReranker
from tiny_brain.search.retrieval import Retriever
from tiny_brain.vault.indexer import VaultIndexer


def run_benchmark(iterations: int = 10, use_fastembed_reranker: bool = True) -> dict[str, object]:
    temp_dir = Path(tempfile.mkdtemp(prefix="tiny_brain_bench_retrieval_"))
    try:
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir(parents=True)
        db_path = temp_dir / "db.sqlite"

        # Populate a corpus of realistic multi-heading notes
        (vault_dir / "Caching.md").write_text(
            "# Distributed Caching Architecture\n"
            "## In-Memory Key Value Stores\n"
            "Redis and Memcached provide low latency sub-millisecond data access for distributed applications.\n"
            "## Eviction Policies\n"
            "LRU and LFU cache eviction policies prevent memory exhaustion under high throughput.",
            encoding="utf-8",
        )
        (vault_dir / "Microservices.md").write_text(
            "# Microservices Infrastructure\n"
            "## Service Discovery\n"
            "Consul and Eureka manage active node registries across clusters.\n"
            "## Distributed Systems Communication\n"
            "gRPC and protocol buffers ensure high speed inter-service communication.\n"
            "See [[Caching]] for data caching patterns.",
            encoding="utf-8",
        )
        (vault_dir / "Hardware.md").write_text(
            "# Hardware Specifications\n"
            "## CPU Architecture\n"
            "Intel Core i7-8550U has 4 physical cores, 8 threads, and 8MB cache.\n"
            "## Memory Bandwidth\n"
            "DDR4 dual-channel shared memory bus throttles under heavy parallel AVX2 execution.",
            encoding="utf-8",
        )

        db = Database(db_path)
        db.migrate()
        indexer = VaultIndexer(vault_dir, db)
        index_stats = indexer.full_scan()

        reranker = FastEmbedReranker() if use_fastembed_reranker else None
        query = "distributed caching and memory eviction"

        with db.connect() as conn:
            # 1. Warm-up
            retriever = Retriever(conn, reranker=reranker)
            retriever.search(query, limit=3)

            # 2. Benchmark standard RRF retrieval (without cross-encoder)
            retriever_rrf = Retriever(conn, reranker=None)
            rrf_times = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                res_rrf = retriever_rrf.search(query, limit=3)
                rrf_times.append((time.perf_counter() - t0) * 1000)

            # 3. Benchmark cross-encoder reranked retrieval
            rerank_times = []
            res_rerank = None
            if reranker:
                for _ in range(iterations):
                    t0 = time.perf_counter()
                    res_rerank = retriever.search(query, limit=3)
                    rerank_times.append((time.perf_counter() - t0) * 1000)

        avg_rrf = sum(rrf_times) / len(rrf_times)
        avg_rerank = sum(rerank_times) / len(rerank_times) if rerank_times else 0.0

        sample_citations = [
            {
                "citation_id": c.citation_id,
                "path": c.relative_path,
                "relationship": c.relationship,
                "has_breadcrumb": c.content.startswith("[Doc:"),
                "first_line": c.content.splitlines()[0] if c.content else "",
            }
            for c in (res_rerank.citations if res_rerank else res_rrf.citations)
        ]

        return {
            "indexed_notes": index_stats["indexed"],
            "query": query,
            "iterations": iterations,
            "rrf_latency_ms": round(avg_rrf, 2),
            "cross_encoder_rerank_latency_ms": round(avg_rerank, 2) if avg_rerank else None,
            "reranker_model": reranker.model_name if reranker else None,
            "top_citations": sample_citations,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Benchmark Tiny Brain retrieval pipeline")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--no-reranker", action="store_true")
    args = parser.parse_args()

    results = run_benchmark(iterations=args.iterations, use_fastembed_reranker=not args.no_reranker)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
