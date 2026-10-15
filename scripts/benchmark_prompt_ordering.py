from __future__ import annotations

import argparse
import json


def simulate_prompt_ordering_benchmark(
    turns: int = 5,
    system_prompt_tokens: int = 80,
    history_tokens_per_turn: int = 150,
    rag_tokens_per_turn: int = 700,
    query_tokens: int = 30,
    prefill_tps: float = 45.0,  # i7-8550U prompt eval tokens/sec
    generation_tps: float = 12.0,
) -> dict[str, object]:
    """
    Simulates prompt prefix matching and KV cache reuse between:
    1. Suboptimal Ordering:
       [System Prompt] -> [Current Turn RAG Chunks] -> [Conversation History] -> [User Query]
       Because the dynamic RAG chunks appear before Conversation History, any change in
       retrieved RAG chunks breaks the prefix right after the System Prompt.
       llama.cpp only reuses the System Prompt tokens (~80 tokens).
       Everything else (RAG + History + Query) must be evaluated from scratch on EVERY turn.

    2. Optimal Ordering:
       [System Prompt] -> [Conversation History] -> [Current Turn RAG Chunks] -> [User Query]
       The static System Prompt and monotonic Conversation History form a stable prefix.
       llama.cpp reuses 100% of the KV cache for the System Prompt and prior turns!
       Only the new RAG chunks and current query require prefill.
    """
    suboptimal_turns = []
    optimal_turns = []

    cumulative_history = 0

    for turn in range(turns):
        turn_num = turn + 1
        history_len = cumulative_history

        total_prompt_tokens = system_prompt_tokens + history_len + rag_tokens_per_turn + query_tokens

        # 1. Suboptimal:
        # Prefix matched: Only system prompt! (RAG tokens immediately follow system prompt and change every turn)
        suboptimal_cached_tokens = system_prompt_tokens if turn > 0 else 0
        suboptimal_eval_tokens = total_prompt_tokens - suboptimal_cached_tokens
        suboptimal_prefill_sec = suboptimal_eval_tokens / prefill_tps
        suboptimal_ttft_sec = suboptimal_prefill_sec + (1.0 / generation_tps)

        suboptimal_turns.append({
            "turn": turn_num,
            "total_prompt_tokens": total_prompt_tokens,
            "cached_prefix_tokens": suboptimal_cached_tokens,
            "evaluated_tokens": suboptimal_eval_tokens,
            "prefill_seconds": round(suboptimal_prefill_sec, 2),
            "ttft_seconds": round(suboptimal_ttft_sec, 2),
        })

        # 2. Optimal:
        # Prefix matched: System Prompt + All prior conversation history!
        optimal_cached_tokens = (system_prompt_tokens + history_len) if turn > 0 else 0
        optimal_eval_tokens = total_prompt_tokens - optimal_cached_tokens
        optimal_prefill_sec = optimal_eval_tokens / prefill_tps
        optimal_ttft_sec = optimal_prefill_sec + (1.0 / generation_tps)

        optimal_turns.append({
            "turn": turn_num,
            "total_prompt_tokens": total_prompt_tokens,
            "cached_prefix_tokens": optimal_cached_tokens,
            "evaluated_tokens": optimal_eval_tokens,
            "prefill_seconds": round(optimal_prefill_sec, 2),
            "ttft_seconds": round(optimal_ttft_sec, 2),
        })

        cumulative_history += history_tokens_per_turn

    suboptimal_total_prefill = sum(t["prefill_seconds"] for t in suboptimal_turns)
    optimal_total_prefill = sum(t["prefill_seconds"] for t in optimal_turns)
    speedup = suboptimal_total_prefill / optimal_total_prefill if optimal_total_prefill > 0 else 1.0

    return {
        "hardware": "Intel Core i7-8550U (AVX2, 4 cores)",
        "prefill_speed_tps": prefill_tps,
        "turns_simulated": turns,
        "suboptimal_ordering": {
            "description": "[System Prompt] -> [RAG Chunks] -> [Conversation History] -> [User Query]",
            "turns": suboptimal_turns,
            "total_prefill_seconds": round(suboptimal_total_prefill, 2),
            "avg_ttft_seconds": round(sum(t["ttft_seconds"] for t in suboptimal_turns) / turns, 2),
        },
        "optimal_ordering": {
            "description": "[System Prompt] -> [Conversation History] -> [RAG Chunks] -> [User Query]",
            "turns": optimal_turns,
            "total_prefill_seconds": round(optimal_total_prefill, 2),
            "avg_ttft_seconds": round(sum(t["ttft_seconds"] for t in optimal_turns) / turns, 2),
            "overall_prefill_speedup": round(speedup, 2),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark KV cache reuse with optimal vs suboptimal prompt ordering.")
    parser.add_argument("--turns", type=int, default=5, help="Number of simulated conversation turns")
    args = parser.parse_args()

    bench = simulate_prompt_ordering_benchmark(turns=args.turns)
    print("=== Prompt Structure Ordering & KV Cache Reuse Benchmark ===")
    print(json.dumps(bench, indent=2))


if __name__ == "__main__":
    main()
