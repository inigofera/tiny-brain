import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiny_brain.agent.llama import LlamaClient, StreamChunk
from tiny_brain.agent.post_chat_queue import PostChatQueue
from tiny_brain.agent.post_chat_worker import PostChatWorker
from tiny_brain.agent.vault_review import make_diff
from tiny_brain.changes.audit import AuditLog
from tiny_brain.changes.git import GitService
from tiny_brain.changes.service import ChangeService
from tiny_brain.config import Settings
from tiny_brain.db import Database
from tiny_brain.memory.service import MemoryService
from tiny_brain.vault.indexer import VaultIndexer
from tiny_brain.web.app import create_app


class MockLlamaForWorker:
    def __init__(self):
        self._last_active_time = time.time() - 20.0  # start idle
        self._active_requests = 0
        self.replies = []

    @property
    def is_busy(self) -> bool:
        return self._active_requests > 0

    @property
    def idle_seconds(self) -> float:
        return 0.0 if self.is_busy else max(0.0, time.time() - self._last_active_time)

    def is_idle(self, min_idle_seconds: float = 10.0) -> bool:
        return not self.is_busy and self.idle_seconds >= min_idle_seconds

    def touch_activity(self):
        self._last_active_time = time.time()

    async def complete(self, messages, **kwargs):
        self._active_requests += 1
        self._last_active_time = time.time()
        try:
            if self.replies:
                rep = self.replies.pop(0)
                if isinstance(rep, Exception):
                    raise rep
                return {"choices": [{"message": {"content": rep}}]}
            # Default response
            return {"choices": [{"message": {"content": "acknowledged"}}]}
        finally:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_active_time = time.time()

    async def stream(self, messages, **kwargs):
        self._active_requests += 1
        self._last_active_time = time.time()
        try:
            yield StreamChunk(content="stream answer")
        finally:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_active_time = time.time()


def test_post_chat_queue_fifo_lifecycle(tmp_path: Path):
    db = Database(tmp_path / "test_queue.sqlite")
    db.migrate()
    queue = PostChatQueue(db)

    assert queue.pending_count() == 0

    j1 = queue.enqueue("memory_extraction", {"text": "hello"}, session_id="s1")
    j2 = queue.enqueue("vault_review", {"text": "world"}, session_id="s1")

    assert queue.pending_count() == 2

    # FIFO ordering
    job1 = queue.fetch_next_pending()
    assert job1 is not None and job1.job_id == j1
    assert job1.state == "running"
    assert queue.pending_count() == 1

    queue.mark_completed(j1)
    jobs = queue.list_jobs()
    completed_j1 = next(j for j in jobs if j.job_id == j1)
    assert completed_j1.state == "completed"
    assert completed_j1.completed_at is not None

    job2 = queue.fetch_next_pending()
    assert job2 is not None and job2.job_id == j2
    queue.mark_failed(j2, "Simulated failure")

    jobs = queue.list_jobs()
    failed_j2 = next(j for j in jobs if j.job_id == j2)
    assert failed_j2.state == "failed"
    assert failed_j2.error == "Simulated failure"
    assert failed_j2.attempts == 1


def test_llama_idle_tracking():
    llama = LlamaClient("http://127.0.0.1:8080/v1", "test-model")
    llama._last_active_time = time.time() - 15.0

    assert not llama.is_busy
    assert llama.idle_seconds >= 14.9
    assert llama.is_idle(min_idle_seconds=10.0)
    assert not llama.is_idle(min_idle_seconds=30.0)

    # Touch activity
    llama.touch_activity()
    assert llama.idle_seconds < 1.0
    assert not llama.is_idle(min_idle_seconds=10.0)


@pytest.mark.asyncio
async def test_background_worker_defers_when_not_idle(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    indexer = VaultIndexer(vault, db)
    audit = AuditLog(db, tmp_path / "data")
    changes = ChangeService(vault, db, indexer, audit, GitService(vault))
    memory = MemoryService(vault, db, indexer, audit)

    mock_llama = MockLlamaForWorker()
    worker = PostChatWorker(db, mock_llama, memory, changes, vault, idle_threshold=10.0)

    # Enqueue a job
    worker.enqueue_memory_extraction("Remember: dark mode preference", session_id="sess-1")
    assert worker.queue.pending_count() == 1

    # Simulate recent activity (active 2 seconds ago)
    mock_llama.touch_activity()
    processed = await worker.process_next_job(force=False)
    assert not processed
    assert worker.queue.pending_count() == 1

    # Now simulate idle > 10 seconds
    mock_llama._last_active_time = time.time() - 12.0
    mock_llama.replies = ['{"type": "decision", "conf": 0.95, "fact": "Dark mode preference"}']

    processed = await worker.process_next_job(force=False)
    assert processed
    assert worker.queue.pending_count() == 0


@pytest.mark.asyncio
async def test_background_worker_processes_memory_extraction(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    indexer = VaultIndexer(vault, db)
    audit = AuditLog(db, tmp_path / "data")
    changes = ChangeService(vault, db, indexer, audit, GitService(vault))
    memory = MemoryService(vault, db, indexer, audit, threshold=0.9)

    mock_llama = MockLlamaForWorker()
    mock_llama.replies = ['{"type": "decision", "conf": 0.95, "fact": "Deploy with Q4_K_M model"}']
    worker = PostChatWorker(db, mock_llama, memory, changes, vault, idle_threshold=0.0)

    job_id = worker.enqueue_memory_extraction("Remember: I decided to deploy with Q4_K_M model.", session_id="s1")
    processed = await worker.process_next_job(force=True)

    assert processed
    job = next(j for j in worker.queue.list_jobs() if j.job_id == job_id)
    assert job.state == "completed"

    inbox = vault / "_AI/Inbox.md"
    assert inbox.exists()
    content = inbox.read_text(encoding="utf-8")
    assert "Deploy with Q4_K_M model" in content


@pytest.mark.asyncio
async def test_background_worker_processes_vault_review(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Storage.md").write_text("# Storage System\nDetails about NVMe and SSDs.\n", encoding="utf-8")

    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    indexer = VaultIndexer(vault, db)
    indexer.full_scan()
    audit = AuditLog(db, tmp_path / "data")
    changes = ChangeService(vault, db, indexer, audit, GitService(vault))
    memory = MemoryService(vault, db, indexer, audit)

    mock_llama = MockLlamaForWorker()
    # Mock vault review decision appending new paragraph to Storage.md
    mock_llama.replies = [
        json.dumps({
            "action": "append",
            "significance": "high",
            "path": "Storage.md",
            "reason": "Adds distributed caching details",
            "paragraph": "## Caching\nRedis distributed caching reduces NVMe read IOPS.",
        })
    ]

    worker = PostChatWorker(db, mock_llama, memory, changes, vault, idle_threshold=0.0)
    job_id = worker.enqueue_vault_review(
        question="How can we optimize storage read IOPS?",
        answer="Using Redis in-memory caching in front of NVMe drives significantly reduces read load. " * 3,
        session_id="s1",
    )

    processed = await worker.process_next_job(force=True)
    assert processed
    job = next(j for j in worker.queue.list_jobs() if j.job_id == job_id)
    assert job.state == "completed"

    # Verify change proposal was created in database
    pending = changes.pending_with_description(("Update from conversation:",), 10)
    assert len(pending) == 1
    assert pending[0]["target_path"] == "Storage.md"
    assert "Redis distributed caching" in str(pending[0]["proposed_content"])


def test_chat_stream_decouples_post_chat_work(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Storage.md").write_text("# Storage\nNVMe notes.\n", encoding="utf-8")

    settings = Settings(
        vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models",
        "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4,
        "tiny-brain.test", True, 0.9, True, "127.0.0.1", 8000,
        async_post_chat=True,  # Enable asynchronous post-chat queue
    )

    app = create_app(settings)
    app.state.indexer.full_scan()

    class ImmediateStreamLlama:
        def __init__(self):
            self._last_active_time = time.time()
            self._active_requests = 0

        @property
        def is_busy(self): return False
        @property
        def idle_seconds(self): return 10.0
        def is_idle(self, *a, **kw): return True
        def touch_activity(self): pass

        async def stream(self, messages, **kwargs):
            yield StreamChunk(content="This is the answer about NVMe caching and distributed reads. " * 3)

        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

    app.state.llama = ImmediateStreamLlama()
    client = TestClient(app)

    response = client.post(
        "/chat/stream",
        json={"message": "Remember: I decided to use NVMe caching for all nodes."},
    )
    assert response.status_code == 200

    events = [
        json.loads(line[5:].strip())
        for line in response.text.split("\n")
        if line.startswith("data:")
    ]

    # Verify streaming stream returned immediately with 'done' and did NOT block on synchronous vault_proposal
    assert not any(item["type"] == "vault_proposal" for item in events)
    assert events[-1]["type"] == "done"

    # Verify background jobs were enqueued into SQLite post_chat_jobs queue!
    jobs_response = client.get("/jobs")
    assert jobs_response.status_code == 200
    jobs = jobs_response.json()
    job_types = [j["job_type"] for j in jobs]
    assert "memory_extraction" in job_types
    assert "vault_review" in job_types
