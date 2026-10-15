from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tiny_brain.agent.chat import ChatAgent
from tiny_brain.agent.llama import LlamaClient, StreamChunk
from tiny_brain.agent.prompts import SYSTEM_PROMPT
from tiny_brain.config import Settings
from tiny_brain.search.retrieval import Citation, RetrievalResult
from tiny_brain.web.app import build_chat_messages, create_app


def test_build_chat_messages_ordering_without_history():
    messages = build_chat_messages(
        SYSTEM_PROMPT,
        "What is Objectivism?",
        rag_context="[SOURCE S1] Ayn Rand philosophy [/SOURCE S1]",
    )
    assert len(messages) == 2
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1]["role"] == "user"
    assert "Vault sources:\n[SOURCE S1]" in messages[1]["content"]
    assert "Current question: What is Objectivism?" in messages[1]["content"]


def test_build_chat_messages_ordering_with_history_preserves_prefix():
    history = "Earlier user questions:\n1. Who is Ayn Rand?\n\nRecent assistant answers:\n1. Ayn Rand was a writer."
    rag = "[SOURCE S1] Objectivism core principles [/SOURCE S1]"
    messages = build_chat_messages(
        SYSTEM_PROMPT,
        "What are its tenets?",
        rag_context=rag,
        conversation_context=history,
    )

    # Ordering MUST be:
    # 1. System Prompt
    # 2. Conversation History (User + Assistant acknowledgement)
    # 3. Dynamic RAG context + Current Question
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_PROMPT

    assert messages[1]["role"] == "user"
    assert "Conversation history:" in messages[1]["content"]
    assert "Who is Ayn Rand?" in messages[1]["content"]

    assert messages[2]["role"] == "assistant"
    assert "conversation context" in messages[2]["content"]

    assert messages[3]["role"] == "user"
    assert "Vault sources:\n[SOURCE S1]" in messages[3]["content"]
    assert "Current question: What are its tenets?" in messages[3]["content"]

    # Crucially, the dynamic RAG context is in the final turn, so messages[0..2]
    # are completely independent of the dynamic RAG chunks and reuse the KV cache!
    assert "Vault sources:" not in messages[1]["content"]


@pytest.mark.asyncio
async def test_chat_agent_answer_places_history_before_sources():
    recorded_messages = []

    class MockLlama:
        async def complete(self, messages, **kwargs):
            nonlocal recorded_messages
            recorded_messages = list(messages)
            return {"choices": [{"message": {"content": "Answer based on tenets"}}]}

    retrieval = RetrievalResult(
        query="tenets",
        hits=(),
        citations=(Citation("S1", "sec1", "tenets.md", "Tenets", "Core", "Metaphysics and Epistemology", "match"),),
        omitted_tokens=0,
    )

    agent = ChatAgent(MockLlama())
    await agent.answer(
        "Explain metaphysics",
        retrieval,
        context="User asked about philosophy previously.",
    )

    assert len(recorded_messages) == 4
    assert recorded_messages[0]["role"] == "system"
    assert recorded_messages[1]["role"] == "user"
    assert "Conversation history:\nUser asked about philosophy previously." in recorded_messages[1]["content"]
    assert recorded_messages[2]["role"] == "assistant"
    assert recorded_messages[3]["role"] == "user"
    assert "Sources:" in recorded_messages[3]["content"]
    assert "Question: Explain metaphysics" in recorded_messages[3]["content"]


def test_chat_stream_preserves_optimal_message_structure(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(
        vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models",
        "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4,
        "tiny-brain.test", True, 0.9, True, "127.0.0.1", 8000,
    )
    app = create_app(settings)

    captured_stream_messages = []

    class StreamCapturingLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            nonlocal captured_stream_messages
            captured_stream_messages = messages
            yield StreamChunk(content="Objectivism holds that reality exists independent of consciousness.")

    app.state.llama = StreamCapturingLlama()
    client = TestClient(app)

    response = client.post(
        "/chat/stream",
        json={
            "message": "What is its view of reality?",
            "context": "Earlier user questions:\n1. What is Objectivism?",
            "thinking": False,
        },
    )
    assert response.status_code == 200
    assert len(captured_stream_messages) == 4
    assert captured_stream_messages[0]["role"] == "system"
    assert captured_stream_messages[1]["role"] == "user"
    assert "Conversation history:" in captured_stream_messages[1]["content"]
    assert captured_stream_messages[2]["role"] == "assistant"
    assert captured_stream_messages[3]["role"] == "user"
    assert "Current question: What is its view of reality?" in captured_stream_messages[3]["content"]
