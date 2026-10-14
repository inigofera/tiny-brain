from pathlib import Path

from fastapi.testclient import TestClient

from tiny_brain.config import Settings
from tiny_brain.agent.llama import StreamChunk
from tiny_brain.web.app import create_app
from tiny_brain.web.app import _conversation_context_for, _draft_topic, _is_draft_request


def test_draft_request_detection():
    assert _is_draft_request("create a note about dialectical materialism")
    assert _is_draft_request("create a new basic vault note about dialectical materialism")
    assert _is_draft_request("start a note for this topic")
    assert not _is_draft_request("what have I written about dialectical materialism?")
    assert _draft_topic("create a note about dialectical materialism for a first-year student") == "dialectical materialism"
    assert _is_draft_request("great. make a draft entry for it")
    assert _draft_topic("# Draft note\n\n## Dialectical Materialism\n\nAn explanation") == "Dialectical Materialism"


def test_unrelated_followup_drops_stale_assistant_context():
    context = "User: What is Objectivism?\nAssistant: A previous answer with bad citations."
    assert _conversation_context_for("what's 3+4", context, draft_mode=False) == context
    assert _conversation_context_for("who are they?", context, draft_mode=False) == context


def test_web_search_and_path_safety(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\nSpark-X2.5 local model", encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    app.state.indexer.full_scan()
    client = TestClient(app)
    response = client.post("/search", json={"query": "Spark-X2.5"})
    assert response.status_code == 200
    assert response.json()["results"][0]["path"] == "note.md"
    assert client.get("/notes/../secret.md").status_code in {400, 404}
    assert client.get("/").status_code == 200


def test_proposal_approval_writes_and_indexes_note(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)

    class FakeGit:
        def commit_file(self, relative_path: str, message: str) -> str:
            return "test-commit"

    app.state.changes.git = FakeGit()
    client = TestClient(app)
    proposal = client.post("/changes", json={
        "operation": "create_note",
        "target_path": "Dialectical Materialism.md",
        "proposed_content": "# Dialectical Materialism\n",
        "description": "Add drafted note",
        "reason": "approved in chat",
    })
    assert proposal.status_code == 200
    change_id = proposal.json()["change_id"]
    assert not (vault / "Dialectical Materialism.md").exists()

    approved = client.post(f"/changes/{change_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["git_commit_id"] == "test-commit"
    assert (vault / "Dialectical Materialism.md").read_text(encoding="utf-8") == "# Dialectical Materialism\n"


def test_draft_revision_endpoint_passes_suggestion_to_model(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            assert "make the explanation shorter" in messages[1]["content"]
            yield StreamChunk(content="# Revised\n\nShort version.")

    app.state.llama = FakeLlama()
    response = TestClient(app).post("/chat/draft/revise", json={
        "topic": "Dialectical Materialism",
        "draft": "# Draft note\n\nLong version.",
        "suggestion": "make the explanation shorter",
    })
    assert response.status_code == 200
    assert "# Draft note" in response.json()["answer"]
    assert "# Revised" in response.json()["answer"]


def test_chat_stream_passes_thinking_preference_to_model(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    received: dict[str, object] = {}

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            received.update(kwargs)
            yield StreamChunk(content="Direct answer")

    app.state.llama = FakeLlama()
    response = TestClient(app).post("/chat/stream", json={"message": "explain a concept", "thinking": False})
    assert response.status_code == 200
    assert received["thinking"] is False


def test_general_question_ignores_partial_vault_match(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Dialectical Materialism.md").write_text("# Dialectical Materialism\nMaterial conditions shape social life.", encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    app.state.indexer.full_scan()
    received: dict[str, object] = {}

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            received["user_message"] = messages[1]["content"]
            yield StreamChunk(content="An answer from general knowledge.")

    app.state.llama = FakeLlama()
    response = TestClient(app).post("/chat/stream", json={"message": "What is the Objectivist response to Dialectical Materialism?", "thinking": False})
    assert response.status_code == 200
    assert "[SOURCE" not in received["user_message"]


def test_planned_query_finds_topic_note(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Essentialism vs Dialectical Materialism.md").write_text("# Essentialism vs Dialectical Materialism\nEssentialism holds that each thing has a fixed nature.", encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    app.state.indexer.full_scan()
    received: dict[str, object] = {}

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "Essentialism"}}]}

        async def stream(self, messages, **kwargs):
            received["user_message"] = messages[1]["content"]
            yield StreamChunk(content="Essentialism is described in your note.")

    app.state.llama = FakeLlama()
    response = TestClient(app).post("/chat/stream", json={"message": "do we have anything written down about Essentialism? if so give me a quick summary", "thinking": False})
    assert response.status_code == 200
    assert "[SOURCE" in received["user_message"]
