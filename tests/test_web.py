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


def test_chat_stream_defaults_thinking_to_false(tmp_path: Path):
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
    response = TestClient(app).post("/chat/stream", json={"message": "explain a concept"})
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


def test_changes_endpoints_include_diff_and_conflict_support(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Original\nOld content\n", encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    client = TestClient(app)

    # Create proposal to edit
    resp = client.post("/changes", json={
        "operation": "propose_edit",
        "target_path": "note.md",
        "proposed_content": "# Original\nUpdated content\n",
        "description": "Update note",
        "reason": "testing diff",
    })
    assert resp.status_code == 200
    change_id = resp.json()["change_id"]

    # Verify list /changes includes diff
    changes = client.get("/changes?state=pending").json()
    assert len(changes) == 1
    assert "diff" in changes[0]
    assert any("+Updated content" in line for line in changes[0]["diff"])
    assert changes[0]["diff_truncated"] is False

    # Verify single /changes/{id} includes diff
    single = client.get(f"/changes/{change_id}").json()
    assert "diff" in single
    assert any("-Old content" in line for line in single["diff"])

    # Verify changes HTML page renders successfully
    html_resp = client.get("/ui/changes")
    assert html_resp.status_code == 200
    assert "filter-tabs" in html_resp.text
    assert "View Diff / Proposed Content" in html_resp.text


def test_chat_stream_includes_sources_in_done_event(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Guide.md").write_text("# Guide\nTiny Brain provides local grounding.\n", encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    app.state.indexer.full_scan()

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "Guide"}}]}

        async def stream(self, messages, **kwargs):
            yield StreamChunk(content="According to [S1], Tiny Brain provides local grounding.")

    app.state.llama = FakeLlama()
    response = TestClient(app).post("/chat/stream", json={"message": "what does Guide say?", "thinking": False})
    assert response.status_code == 200
    raw_text = response.text
    assert "data: " in raw_text

    import json
    done_payload = None
    for line in raw_text.splitlines():
        if line.startswith("data: "):
            ev = json.loads(line[6:])
            if ev.get("type") == "done":
                done_payload = ev
                break

    assert done_payload is not None
    assert "sources" in done_payload
    assert len(done_payload["sources"]) >= 1
    assert done_payload["sources"][0]["citation_id"] == "S1"
    assert done_payload["sources"][0]["relative_path"] == "Guide.md"
    assert "Tiny Brain provides local grounding" in done_payload["sources"][0]["content"]


def test_chat_session_persistence_and_new_session(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Guide.md").write_text("# Guide\nTiny Brain provides local grounding.\n", encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    app.state.indexer.full_scan()

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "Guide"}}]}

        async def stream(self, messages, **kwargs):
            yield StreamChunk(content="Here is the stored answer.")

    app.state.llama = FakeLlama()
    client = TestClient(app)

    # Initially empty history
    history = client.get("/chat/history").json()
    assert history["messages"] == []
    initial_session_id = history["session_id"]

    # Stream a chat turn
    client.post("/chat/stream", json={"message": "First question", "thinking": False})

    # History should now contain 2 messages
    history = client.get("/chat/history").json()
    assert history["session_id"] == initial_session_id
    assert len(history["messages"]) == 2
    assert history["messages"][0]["role"] == "user"
    assert history["messages"][0]["content"] == "First question"
    assert history["messages"][1]["role"] == "assistant"
    assert history["messages"][1]["content"] == "Here is the stored answer."

    # Starting a new session creates a fresh active session
    new_sess = client.post("/chat/new").json()
    assert new_sess["status"] == "created"
    assert new_sess["session_id"] != initial_session_id

    # New session history is empty
    fresh_history = client.get("/chat/history").json()
    assert fresh_history["session_id"] == new_sess["session_id"]
    assert fresh_history["messages"] == []


def test_note_inspection_page_and_search_integration(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Concept.md").write_text("# Concept\nImportant ideas [[Other]].\n", encoding="utf-8")
    (vault / "Other.md").write_text("# Other\nBacklinks here.\n", encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    app.state.indexer.full_scan()
    client = TestClient(app)

    # Note page exists and renders content, metadata, and Obsidian URI
    note_resp = client.get("/ui/notes/Concept.md")
    assert note_resp.status_code == 200
    assert "Concept" in note_resp.text
    assert "obsidian://open?file=Concept.md" in note_resp.text
    assert "Important ideas" in note_resp.text

    # Backlink is detected for Other.md
    other_resp = client.get("/ui/notes/Other.md")
    assert other_resp.status_code == 200
    assert "Concept.md" in other_resp.text

    # 404 for missing note and 400 for unsafe note
    assert client.get("/ui/notes/NonExistent.md").status_code == 404
    assert client.get("/ui/notes/../unsafe.md").status_code in {400, 404}

    # Search page renders Obsidian links and query handling
    search_page_resp = client.get("/ui/search")
    assert search_page_resp.status_code == 200
    assert "obsidian://" in search_page_resp.text
    assert "Read Note" in search_page_resp.text


def test_unified_proposal_in_draft_chat_stream(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)

    class FakeGit:
        def commit_file(self, relative_path: str, message: str) -> str:
            return "commit-stoicism"

    app.state.changes.git = FakeGit()

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            yield StreamChunk(content="## Stoicism\nVirtue is the sole good.")

    app.state.llama = FakeLlama()
    client = TestClient(app)

    response = client.post("/chat/stream", json={"message": "create a note about Stoicism", "thinking": False})
    assert response.status_code == 200

    import json
    proposal_event = None
    for line in response.text.splitlines():
        if line.startswith("data: "):
            ev = json.loads(line[6:])
            if ev.get("type") == "vault_proposal":
                proposal_event = ev["proposal"]
                break

    assert proposal_event is not None
    assert proposal_event["target_path"] == "Stoicism.md"
    assert proposal_event["is_new"] is True
    assert "diff" in proposal_event
    assert any("+Virtue is the sole good." in l for l in proposal_event["diff"])

    # Verified in pending changes
    pending = client.get("/changes?state=pending").json()
    assert len(pending) == 1
    assert pending[0]["target_path"] == "Stoicism.md"

    # Approve and verify written
    apprv = client.post(f"/changes/{proposal_event['change_id']}/approve")
    assert apprv.status_code == 200
    assert (vault / "Stoicism.md").exists()
    assert "Virtue is the sole good." in (vault / "Stoicism.md").read_text(encoding="utf-8")


def test_home_renders_advanced_mode_and_model_configs(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "gemma-test-model.gguf", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="advanced-mode"' in html
    assert 'id="advanced-panel"' in html
    assert 'id="context-window"' in html
    assert 'id="reasoning-budget"' in html
    assert 'id="temperature"' in html
    assert 'id="system-prompt"' in html
    assert 'id="thinking"' in html
    assert 'id="btn-save-advanced"' in html
    assert "gemma-test-model.gguf" in html
    assert "REASONING MODE" in html


def test_api_config_endpoint(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "test-chat-model.gguf", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    client = TestClient(app)
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "You are Tiny Brain" in data["system_prompt"]
    assert data["context_tokens"] == 8192
    assert data["chat_model"] == "test-chat-model.gguf"
    assert "max_output_tokens" in data
    assert "fast_chat_max_tokens" in data


def test_chat_stream_passes_custom_system_prompt_to_model(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    received_messages = []

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            nonlocal received_messages
            received_messages = list(messages)
            yield StreamChunk(content="Direct answer with custom prompt.")

    app.state.llama = FakeLlama()
    custom_prompt = "You are a specialized biology assistant. Answer only in Latin names."
    response = TestClient(app).post("/chat/stream", json={
        "message": "what is this plant?",
        "system_prompt": custom_prompt,
        "thinking": False,
    })
    assert response.status_code == 200
    assert len(received_messages) >= 1
    assert received_messages[0]["role"] == "system"
    assert received_messages[0]["content"] == custom_prompt


def test_chat_stream_passes_custom_reasoning_budget_and_temperature(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    received_kwargs = {}

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "NONE"}}]}

        async def stream(self, messages, **kwargs):
            nonlocal received_kwargs
            received_kwargs = dict(kwargs)
            yield StreamChunk(content="Custom budget reply.")

    app.state.llama = FakeLlama()
    response = TestClient(app).post("/chat/stream", json={
        "message": "deep problem",
        "thinking": True,
        "reasoning_budget": 1500,
        "temperature": 0.45,
    })
    assert response.status_code == 200
    assert received_kwargs.get("max_tokens") == 1500
    assert received_kwargs.get("temperature") == 0.45
    assert received_kwargs.get("thinking") is True


def test_chat_endpoint_supports_custom_system_prompt_and_budget(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    captured = {}

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            captured["messages"] = list(messages)
            captured["kwargs"] = dict(kwargs)
            return {"choices": [{"message": {"content": "Non-streaming answer"}}]}

    app.state.llama = FakeLlama()
    custom_prompt = "Custom system instructions"
    response = TestClient(app).post("/chat", json={
        "message": "test question",
        "system_prompt": custom_prompt,
        "reasoning_budget": 800,
        "temperature": 0.35,
        "thinking": True,
    })
    assert response.status_code == 200
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][0]["content"] == custom_prompt
    assert captured["kwargs"]["max_tokens"] == 800
    assert captured["kwargs"]["temperature"] == 0.35
    assert captured["kwargs"]["thinking"] is True


def test_chat_custom_context_tokens_limits_retrieval(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n" + "Word " * 2000, encoding="utf-8")
    settings = Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 16384, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)
    app = create_app(settings)
    app.state.indexer.full_scan()

    class FakeLlama:
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "Note"}}]}

        async def stream(self, messages, **kwargs):
            yield StreamChunk(content="Answer")

    app.state.llama = FakeLlama()
    client = TestClient(app)

    # Pass small context window (e.g. 200 tokens -> budget 100 tokens)
    response = client.post("/chat", json={
        "message": "tell me about Note",
        "context_tokens": 200,
        "thinking": False,
    })
    assert response.status_code == 200
    assert "sources" in response.json()


