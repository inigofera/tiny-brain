from __future__ import annotations

import argparse
import asyncio
import json
import time

from tiny_brain.agent.llama import LlamaClient


async def benchmark(base_url: str, model: str, prompt: str) -> None:
    client = LlamaClient(base_url, model)
    started = time.perf_counter()
    response = await client.complete([{"role": "user", "content": prompt}])
    elapsed = time.perf_counter() - started
    answer = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(json.dumps({"elapsed_seconds": elapsed, "answer_chars": len(answer), "response": response}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="Spark-X2.5-1.7B-Q6_K.gguf")
    parser.add_argument("--prompt", default="Reply with one short sentence confirming local inference.")
    args = parser.parse_args()
    asyncio.run(benchmark(args.base_url, args.model, args.prompt))


if __name__ == "__main__":
    main()
