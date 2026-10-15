from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tiny_brain.agent.llama import LlamaClient
from tiny_brain.agent.post_chat_worker import PostChatWorker
from tiny_brain.agent.vault_review import decide_vault_change
from tiny_brain.changes.audit import AuditLog
from tiny_brain.changes.git import GitService
from tiny_brain.changes.service import ChangeService
from tiny_brain.db import Database
from tiny_brain.memory.extractor import MemoryExtractor
from tiny_brain.memory.service import MemoryService
from tiny_brain.vault.indexer import VaultIndexer


class SlotRecordingLlama:
    def __init__(self):
        self._last_active_time = 0.0
        self.calls: list[dict] = []

    @property
    def is_busy(self) -> bool:
        return False

    @property
    def idle_seconds(self) -> float:
        return 999.0

    def is_idle(self, min_idle_seconds: float = 10.0) -> bool:
        return True

    def touch_activity(self):
        pass

    async def complete(self, messages, **kwargs):
        self.calls.append({"method": "complete", "messages": messages, "kwargs": kwargs})
        # If grammar is present or looks like memory extraction
        if "grammar" in kwargs:
            return {"choices": [{"message": {"content": '{"type": "decision", "conf": 0.9, "fact": "Use slot pinning"}'}}]}
        # Otherwise for vault review
        return {"choices": [{"message": {"content": '{"action": "none", "significance": "low"}'}}]}

    async def stream(self, messages, **kwargs):
        self.calls.append({"method": "stream", "messages": messages, "kwargs": kwargs})
        from tiny_brain.agent.llama import StreamChunk
        yield StreamChunk(content="answer")


@pytest.mark.asyncio
async def test_llama_client_slot_pinning_payload():
    client = LlamaClient("http://fake:8080/v1", "test-model")
    
    # Mock httpx AsyncClient post and stream to verify the JSON payload
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        from unittest.mock import MagicMock
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        # Default slot should be 0
        await client.complete([{"role": "user", "content": "hi"}])
        assert mock_post.call_count == 1
        payload = mock_post.call_args.kwargs["json"]
        assert payload["id_slot"] == 0

        # Explicit slot should be forwarded
        await client.complete([{"role": "user", "content": "hi"}], id_slot=1)
        assert mock_post.call_count == 2
        payload = mock_post.call_args.kwargs["json"]
        assert payload["id_slot"] == 1


@pytest.mark.asyncio
async def test_post_chat_worker_pins_jobs_to_slot_1(tmp_path: Path):
    db = Database(tmp_path / "test_slots.sqlite")
    db.migrate()
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "test.md").write_text("# Test\nContent here", encoding="utf-8")

    indexer = VaultIndexer(vault_root, db)
    indexer.full_scan()

    audit = AuditLog(db, tmp_path / "data")
    changes = ChangeService(vault_root, db, indexer, audit, GitService(vault_root))
    memory = MemoryService(vault_root, db, indexer, audit)
    llama = SlotRecordingLlama()

    worker = PostChatWorker(
        database=db,
        llama=llama,
        memory=memory,
        changes=changes,
        vault_root=vault_root,
        idle_threshold=0.0,
    )

    # 1. Enqueue and process memory extraction
    worker.enqueue_memory_extraction("I decided to adopt dedicated slot pinning.")
    processed_mem = await worker.process_next_job(force=True)
    assert processed_mem is True
    assert len(llama.calls) >= 1
    mem_call = llama.calls[-1]
    assert mem_call["kwargs"].get("id_slot") == 1

    # 2. Enqueue and process vault review
    worker.enqueue_vault_review(
        "How do we configure llama.cpp?",
        "We configure llama.cpp with -np 2 and pin slot 0 for chat and slot 1 for background jobs.",
    )
    processed_review = await worker.process_next_job(force=True)
    assert processed_review is True
    review_call = llama.calls[-1]
    assert review_call["kwargs"].get("id_slot") == 1


def test_compose_yaml_has_two_slots_and_cache_reuse():
    compose_path = Path(__file__).parent.parent / "compose.yaml"
    content = compose_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    cmd = parsed["services"]["llama"]["command"]
    
    assert "-np" in cmd
    np_index = cmd.index("-np")
    assert cmd[np_index + 1] == "2"

    assert "--cache-reuse" in cmd
    reuse_index = cmd.index("--cache-reuse")
    assert cmd[reuse_index + 1] == "64"
