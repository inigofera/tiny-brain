import argparse
import json
import sys
import time
import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")



def benchmark_chat_response(app_base_url: str, message: str, thinking: bool = False):
    """Test Chat Response Rate of the Tiny Brain application with live streaming feedback."""
    print(f"\n--- 1. Application Chat Response (thinking={thinking}) ---", flush=True)
    print(f"Message: {message}", flush=True)
    print("Connecting and awaiting first token (includes search planning + prompt prefill)...", flush=True)

    start = time.perf_counter()
    first_token_time = None
    first_reasoning_time = None
    reasoning_tokens = []
    answer_tokens = []
    sources = []
    citations = []

    with httpx.Client(base_url=app_base_url, timeout=300.0) as client:
        with client.stream("POST", "/chat/stream", json={"message": message, "thinking": thinking}) as response:
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[5:].strip())
                event_type = data.get("type")
                current_time = time.perf_counter()

                if event_type == "reasoning":
                    if first_reasoning_time is None:
                        first_reasoning_time = current_time - start
                        print(f"\n[+{first_reasoning_time:.1f}s] Reasoning started: ", end="", flush=True)
                    reasoning_tokens.append(data.get("content", ""))
                    print(".", end="", flush=True)
                elif event_type == "token":
                    if first_token_time is None:
                        first_token_time = current_time - start
                        print(f"\n[+{first_token_time:.1f}s] Answer tokens started: ", end="", flush=True)
                    answer_tokens.append(data.get("content", ""))
                    print(data.get("content", ""), end="", flush=True)
                elif event_type == "done":
                    sources = data.get("sources", [])
                    citations = data.get("citations", [])

    total_time = time.perf_counter() - start
    first_activity = first_reasoning_time or first_token_time
    gen_time = (total_time - first_activity) if first_activity else 0.0
    total_tokens = len(reasoning_tokens) + len(answer_tokens)
    creation_rate = total_tokens / gen_time if gen_time > 0 else 0.0

    result = {
        "operation": "chat_response",
        "thinking": thinking,
        "total_time_s": round(total_time, 2),
        "time_to_first_activity_s": round(first_activity, 2) if first_activity else None,
        "time_to_first_answer_token_s": round(first_token_time, 2) if first_token_time else None,
        "generation_time_s": round(gen_time, 2),
        "reasoning_chunks": len(reasoning_tokens),
        "answer_chunks": len(answer_tokens),
        "total_token_chunks": total_tokens,
        "creation_rate_chunks_per_sec": round(creation_rate, 2),
        "answer_characters": len("".join(answer_tokens)),
        "sources_retrieved": len(sources),
    }
    print("\n\n" + json.dumps(result, indent=2), flush=True)
    return result


def benchmark_note_creation(app_base_url: str, prompt: str):
    """Test Note Creation Rate of the Tiny Brain application with live streaming feedback."""
    print(f"\n--- 2. Application Note Creation Rate ---", flush=True)
    print(f"Prompt: {prompt}", flush=True)
    print("Connecting and awaiting draft generation...", flush=True)

    start = time.perf_counter()
    first_token_time = None
    answer_tokens = []
    proposal = None

    with httpx.Client(base_url=app_base_url, timeout=300.0) as client:
        with client.stream("POST", "/chat/stream", json={"message": prompt, "thinking": False}) as response:
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[5:].strip())
                event_type = data.get("type")
                current_time = time.perf_counter()

                if event_type == "token":
                    if first_token_time is None:
                        first_token_time = current_time - start
                        print(f"\n[+{first_token_time:.1f}s] Note drafting stream: ", end="", flush=True)
                    content = data.get("content", "")
                    answer_tokens.append(content)
                    print(content, end="", flush=True)
                elif event_type == "vault_proposal":
                    proposal = data.get("proposal")
                    print(f"\n[+{current_time - start:.1f}s] Vault proposal created: {proposal.get('target_path')}", flush=True)

    total_time = time.perf_counter() - start
    gen_time = (total_time - first_token_time) if first_token_time else 0.0
    total_tokens = len(answer_tokens)
    creation_rate = total_tokens / gen_time if gen_time > 0 else 0.0

    result = {
        "operation": "note_creation",
        "total_time_s": round(total_time, 2),
        "time_to_first_token_s": round(first_token_time, 2) if first_token_time else None,
        "generation_time_s": round(gen_time, 2),
        "draft_token_chunks": total_tokens,
        "draft_creation_rate_chunks_per_sec": round(creation_rate, 2),
        "draft_characters": len("".join(answer_tokens)),
        "proposal_created": proposal is not None,
        "target_path": proposal.get("target_path") if proposal else None,
    }
    print("\n\n" + json.dumps(result, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-url", default="http://127.0.0.1")
    parser.add_argument("--skip-note", action="store_true", help="Skip note creation test")
    parser.add_argument("--skip-chat", action="store_true", help="Skip chat test")
    args = parser.parse_args()

    results = {}
    print("========================================", flush=True)
    print("APPLICATION BENCHMARK SUITE", flush=True)
    print("========================================", flush=True)

    if not args.skip_chat:
        r1 = benchmark_chat_response(args.app_url, "What is Tiny Brain and how does it work?", thinking=False)
        results["chat_response"] = r1

    if not args.skip_note:
        r2 = benchmark_note_creation(args.app_url, "Draft a note about distributed caching")
        results["note_creation"] = r2

    print("\n========================================", flush=True)
    print("BENCHMARK SUMMARY", flush=True)
    print("========================================", flush=True)
    print(json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    main()

