from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from tiny_brain.agent.llama import LlamaClient
from tiny_brain.agent.post_chat_queue import PostChatQueue
from tiny_brain.config import Settings
from tiny_brain.db import Database
from tiny_brain.vault.indexer import VaultIndexer
from tiny_brain.web.app import create_app


def run_benchmark(iterations: int = 20) -> dict[str, object]:
    temp_dir = Path(tempfile.mkdtemp(prefix="tiny_brain_bench_queue_"))
    try:
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir(parents=True)
        db_path = temp_dir / "db.sqlite"

        (vault_dir / "Projects.md").write_text(
            "# Active Projects\n## Agent Pipeline\nWorking on background task queues for local LLM.",
            encoding="utf-8",
        )

        db = Database(db_path)
        db.migrate()
        indexer = VaultIndexer(vault_dir, db)
        indexer.full_scan()

        # Measure 1: Raw SQLite Queue enqueue & dequeue latency
        queue = PostChatQueue(db)
        t0 = time.perf_counter()
        for i in range(iterations):
            session_id = f"sess_{i}"
            queue.enqueue(
                "memory_extraction",
                {"user_message": "Remember I like dark roast coffee.", "assistant_response": "Got it."},
                session_id=session_id,
            )
            queue.enqueue(
                "vault_review",
                {"user_message": "Remember I like dark roast coffee.", "assistant_response": "Got it.", "retrieved_note_paths": ["Projects.md"]},
                session_id=session_id,
            )
        enqueue_elapsed_ms = ((time.perf_counter() - t0) / (iterations * 2)) * 1000

        t1 = time.perf_counter()
        dequeued_count = 0
        while True:
            job = queue.fetch_next_pending()
            if not job:
                break
            queue.mark_completed(job.job_id)
            dequeued_count += 1
        dequeue_elapsed_ms = ((time.perf_counter() - t1) / dequeued_count) * 1000

        simulated_chat_latency_s = 0.05
        simulated_post_chat_latency_s = 0.20

        class MockBenchLlama:
            def __init__(self, chat_delay: float = 0.05, complete_delay: float = 0.20):
                self.chat_delay = chat_delay
                self.complete_delay = complete_delay
                self._last_active_time = time.time()
                self._active_requests = 0

            @property
            def is_busy(self) -> bool:
                return self._active_requests > 0

            @property
            def idle_seconds(self) -> float:
                return 15.0

            def is_idle(self, min_idle_seconds: float = 10.0) -> bool:
                return True

            def touch_activity(self):
                self._last_active_time = time.time()

            async def stream(self, messages, **kwargs):
                self._active_requests += 1
                try:
                    time.sleep(self.chat_delay)
                    from tiny_brain.agent.llama import StreamChunk
                    yield StreamChunk(
                        content="Here is a detailed explanation of our storage architecture and project plan that exceeds review chars. "
                        "We utilize high-speed NVMe solid state drives backed by distributed Redis in-memory caches to maintain single-digit millisecond latency across clusters."
                    )
                finally:
                    self._active_requests = max(0, self._active_requests - 1)

            async def complete(self, messages, **kwargs):
                self._active_requests += 1
                try:
                    time.sleep(self.complete_delay)
                    return {"choices": [{"message": {"content": "NONE"}}]}
                finally:
                    self._active_requests = max(0, self._active_requests - 1)

        mock_llama = MockBenchLlama(chat_delay=simulated_chat_latency_s, complete_delay=simulated_post_chat_latency_s)

        data_dir = temp_dir / "data"
        data_dir.mkdir(parents=True)
        models_dir = temp_dir / "models"
        models_dir.mkdir(parents=True)

        # App with async_post_chat = False (Synchronous)
        sync_settings = Settings(
            vault_dir, data_dir, db_path, models_dir,
            "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4,
            "tiny-brain.test", True, 0.9, True, "127.0.0.1", 8000,
            async_post_chat=False,
        )
        sync_app = create_app(sync_settings)
        sync_app.state.llama = mock_llama
        sync_app.state.indexer.full_scan()

        # App with async_post_chat = True (Asynchronous queue)
        async_settings = Settings(
            vault_dir, data_dir, db_path, models_dir,
            "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4,
            "tiny-brain.test", True, 0.9, True, "127.0.0.1", 8000,
            async_post_chat=True,
            post_chat_idle_seconds=0.01,
        )
        async_app = create_app(async_settings)
        async_app.state.llama = mock_llama
        async_app.state.indexer.full_scan()

        # Measure synchronous chat streaming response completion time
        sync_durations = []
        with TestClient(sync_app) as client:
            for _ in range(5):
                start = time.perf_counter()
                resp = client.post(
                    "/chat/stream",
                    json={"message": "Please explain our storage architecture in full detail with caching.", "sync_review": True},
                )
                assert resp.status_code == 200
                sync_durations.append((time.perf_counter() - start) * 1000)

        # Measure asynchronous chat streaming response completion time
        async_durations = []
        with TestClient(async_app) as client:
            for _ in range(5):
                start = time.perf_counter()
                resp = client.post(
                    "/chat/stream",
                    json={"message": "Please explain our storage architecture in full detail with caching.", "sync_review": False},
                )
                assert resp.status_code == 200
                async_durations.append((time.perf_counter() - start) * 1000)

        avg_sync_latency_ms = sum(sync_durations) / len(sync_durations)
        avg_async_latency_ms = sum(async_durations) / len(async_durations)
        latency_reduction_pct = (
            (avg_sync_latency_ms - avg_async_latency_ms) / avg_sync_latency_ms
        ) * 100

        results = {
            "iterations": iterations,
            "sqlite_enqueue_avg_ms": round(enqueue_elapsed_ms, 4),
            "sqlite_dequeue_complete_avg_ms": round(dequeue_elapsed_ms, 4),
            "simulated_sync_chat_latency_ms": round(avg_sync_latency_ms, 2),
            "async_queued_chat_latency_ms": round(avg_async_latency_ms, 2),
            "latency_reduction_percent": round(latency_reduction_pct, 1),
            "jobs_processed": dequeued_count,
        }
        return results
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark Section 2B background post-chat queue")
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()

    results = run_benchmark(iterations=args.iterations)
    print("\n=== Background Post-Chat Queue Benchmark Results ===")
    print(f"SQLite Enqueue Avg Latency:       {results['sqlite_enqueue_avg_ms']} ms/job")
    print(f"SQLite Dequeue & Complete Avg:    {results['sqlite_dequeue_complete_avg_ms']} ms/job")
    print(f"Sync Chat Response Latency:       {results['simulated_sync_chat_latency_ms']} ms")
    print(f"Async Queued Chat Response:       {results['async_queued_chat_latency_ms']} ms")
    print(f"Latency Reduction:                {results['latency_reduction_percent']}%")
    print("\nJSON Output:")
    print(json.dumps(results, indent=2))
