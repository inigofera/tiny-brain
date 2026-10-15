from __future__ import annotations

import argparse
import json
import time


def simulate_slot_benchmark(
    num_chat_turns: int = 5,
    prompt_tokens_per_turn: int = 800,
    history_tokens_growth: int = 150,
    background_prompt_tokens: int = 1200,
    prefill_tps: float = 45.0,  # Intel i7-8550U AVX2 prompt eval speed
    generation_tps: float = 12.0,
    output_tokens: int = 120,
) -> dict[str, object]:
    """
    Simulates KV cache eviction and prompt evaluation latency on an Intel i7-8550U.
    
    In Single-Slot Mode (-np 1):
      Between chat turns, the asynchronous PostChatWorker runs memory_extraction and
      vault_review. In -np 1, this overwrites the single KV slot with the background prompt.
      Consequently, every follow-up chat turn suffers a cold prefill of (history + prompt) tokens.
      
    In Dual-Slot Pinning Mode (-np 2 with id_slot=0 for chat, id_slot=1 for background):
      The background worker operates purely in Slot 1. Slot 0 retains the system prompt
      and chat history prefix. Only new tokens (the new question and RAG context) require prefill.
    """
    results: dict[str, object] = {
        "hardware": "Intel Core i7-8550U (AVX2, 4 cores)",
        "prefill_speed_tps": prefill_tps,
        "generation_speed_tps": generation_tps,
        "single_slot_np1": {},
        "dual_slot_pinned_np2": {},
    }

    # 1. Single Slot (-np 1, background worker evicts chat cache)
    single_turn_latencies = []
    single_prefill_times = []
    cumulative_history = 0

    for turn in range(num_chat_turns):
        total_prompt_tokens = cumulative_history + prompt_tokens_per_turn
        # Single slot: Every turn has been evicted by background worker in between turns!
        tokens_to_prefill = total_prompt_tokens
        prefill_time = tokens_to_prefill / prefill_tps
        gen_time = output_tokens / generation_tps
        total_turn_time = prefill_time + gen_time
        ttft = prefill_time + (1.0 / generation_tps)

        single_prefill_times.append(prefill_time)
        single_turn_latencies.append({
            "turn": turn + 1,
            "total_prompt_tokens": total_prompt_tokens,
            "tokens_prefilled": tokens_to_prefill,
            "prefill_seconds": round(prefill_time, 2),
            "ttft_seconds": round(ttft, 2),
            "total_turn_seconds": round(total_turn_time, 2),
        })
        cumulative_history += history_tokens_growth

    # 2. Dual Slot Pinned (-np 2, Slot 0 chat, Slot 1 background)
    dual_turn_latencies = []
    dual_prefill_times = []
    cumulative_history = 0

    for turn in range(num_chat_turns):
        total_prompt_tokens = cumulative_history + prompt_tokens_per_turn
        # Dual slot pinned: Prefix (system prompt + history) remains in Slot 0!
        # Only new prompt tokens (current turn RAG + query) require prefill on follow-up turns
        if turn == 0:
            tokens_to_prefill = total_prompt_tokens
        else:
            tokens_to_prefill = prompt_tokens_per_turn  # cached prefix reused!
        
        prefill_time = tokens_to_prefill / prefill_tps
        gen_time = output_tokens / generation_tps
        total_turn_time = prefill_time + gen_time
        ttft = prefill_time + (1.0 / generation_tps)

        dual_prefill_times.append(prefill_time)
        dual_turn_latencies.append({
            "turn": turn + 1,
            "total_prompt_tokens": total_prompt_tokens,
            "tokens_prefilled": tokens_to_prefill,
            "prefill_seconds": round(prefill_time, 2),
            "ttft_seconds": round(ttft, 2),
            "total_turn_seconds": round(total_turn_time, 2),
        })
        cumulative_history += history_tokens_growth

    avg_single_prefill = sum(single_prefill_times) / len(single_prefill_times)
    avg_dual_prefill = sum(dual_prefill_times) / len(dual_prefill_times)
    speedup = avg_single_prefill / avg_dual_prefill if avg_dual_prefill > 0 else 1.0

    results["single_slot_np1"] = {
        "turns": single_turn_latencies,
        "avg_prefill_seconds": round(avg_single_prefill, 2),
        "total_prefill_seconds": round(sum(single_prefill_times), 2),
    }
    results["dual_slot_pinned_np2"] = {
        "turns": dual_turn_latencies,
        "avg_prefill_seconds": round(avg_dual_prefill, 2),
        "total_prefill_seconds": round(sum(dual_prefill_times), 2),
        "prefill_speedup": round(speedup, 2),
    }
    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark KV cache slot pinning vs single-slot eviction.")
    parser.add_argument("--turns", type=int, default=5, help="Number of simulated chat turns")
    args = parser.parse_args()

    bench = simulate_slot_benchmark(num_chat_turns=args.turns)
    print("=== KV Cache Eviction & Slot Pinning Benchmark (Intel i7-8550U) ===")
    print(json.dumps(bench, indent=2))


if __name__ == "__main__":
    main()
