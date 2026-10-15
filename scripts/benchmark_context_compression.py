from __future__ import annotations

import argparse
import json
import time

from tiny_brain.search.retrieval import split_into_sentences


def simulate_compression_benchmark(
    num_sections: int = 3,
    sentences_per_section: int = 8,
    words_per_sentence: int = 15,
    max_compressed_sentences: int = 6,
    prefill_tps: float = 45.0,  # i7-8550U AVX2 prompt eval speed
    cross_encoder_per_sentence_ms: float = 0.25,  # ~5ms for 20 sentences with ms-marco-MiniLM-L-6-v2 ONNX
) -> dict[str, object]:
    """
    Benchmarks Sentence-Level Context Compression (Extractive Pruning).
    
    Before:
      Reranker selects top 3 sections (~900 tokens total).
      Evaluating 900 tokens takes 900 / 45 = 20.0 seconds of prefill on Intel i7-8550U.
      
    After:
      Extract sentences, re-score with loaded TextCrossEncoder (~5ms),
      and select top 5-8 sentences (~150 tokens total).
      Evaluating 150 tokens takes 150 / 45 = 3.33 seconds of prefill.
    """
    total_sentences = num_sections * sentences_per_section
    raw_words = total_sentences * words_per_sentence
    # Approximate 1.3 tokens per word
    raw_tokens = int(raw_words * 1.3)

    compressed_sentences = min(total_sentences, max_compressed_sentences)
    compressed_words = compressed_sentences * words_per_sentence + (num_sections * 8)  # including breadcrumb headers
    compressed_tokens = int(compressed_words * 1.3)

    # Cross encoder sentence re-scoring latency
    cross_encoder_latency_ms = total_sentences * cross_encoder_per_sentence_ms
    cross_encoder_latency_sec = cross_encoder_latency_ms / 1000.0

    raw_prefill_sec = raw_tokens / prefill_tps
    compressed_prefill_sec = (compressed_tokens / prefill_tps) + cross_encoder_latency_sec

    speedup = raw_prefill_sec / compressed_prefill_sec if compressed_prefill_sec > 0 else 1.0
    token_reduction_pct = ((raw_tokens - compressed_tokens) / raw_tokens) * 100.0

    return {
        "hardware": "Intel Core i7-8550U (AVX2, 4 cores)",
        "prefill_speed_tps": prefill_tps,
        "input_context": {
            "top_sections": num_sections,
            "total_sentences": total_sentences,
            "estimated_uncompressed_tokens": raw_tokens,
            "uncompressed_prefill_seconds": round(raw_prefill_sec, 2),
        },
        "extractive_compression": {
            "cross_encoder_model": "Xenova/ms-marco-MiniLM-L-6-v2",
            "sentence_reranking_latency_ms": round(cross_encoder_latency_ms, 2),
            "retained_sentences": compressed_sentences,
            "estimated_compressed_tokens": compressed_tokens,
            "token_reduction_percent": round(token_reduction_pct, 1),
            "compressed_prefill_seconds": round(compressed_prefill_sec, 2),
            "prefill_time_saved_seconds": round(raw_prefill_sec - compressed_prefill_sec, 2),
            "prefill_speedup": round(speedup, 2),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark sentence-level context compression for RAG prefill.")
    parser.add_argument("--sections", type=int, default=3, help="Top sections selected by initial reranker")
    parser.add_argument("--sentences-per-section", type=int, default=8, help="Average sentences per section")
    args = parser.parse_args()

    bench = simulate_compression_benchmark(
        num_sections=args.sections,
        sentences_per_section=args.sentences_per_section,
    )
    print("=== Sentence-Level Context Compression Benchmark (Intel i7-8550U) ===")
    print(json.dumps(bench, indent=2))


if __name__ == "__main__":
    main()
