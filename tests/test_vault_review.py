import json
from pathlib import Path

from fastapi.testclient import TestClient

from tiny_brain.agent.llama import StreamChunk
from tiny_brain.agent.vault_review import CandidateNote, ReviewDecision, find_candidates, fix_wikilinks, make_diff, parse_decision, validate_decision
from tiny_brain.config import Settings
from tiny_brain.agent.prompts import SEARCH_PLANNER_PROMPT
from tiny_brain.web.app import create_app

QUESTION = "Tell me about Quartz Harbor tides."
ANSWER = ("Quartz Harbor tides repeat about every twelve hours, so the best time to walk the pier is near low water in the late afternoon. " * 2).strip()
ORIGINAL = "# Quartz Harbor\n\nA port city with tide notes.\n"
UPDATED = "# Quartz Harbor\n\nA port city with tide notes.\n\n## Tides\n\nTides repeat about every twelve hours.\n"


def _settings(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return Settings(vault, tmp_path / "data", tmp_path / "data/db.sqlite", tmp_path / "models", "http://127.0.0.1:9/v1", "model", "embed", "none", 8192, 4, "tiny-brain.test", True, .9, True, "127.0.0.1", 8000)


class FakeLlama:
    def __init__(self):
        self.replies: list[str | Exception] = []

    async def stream(self, messages, **kwargs):
        yield StreamChunk(content=ANSWER)

    async def complete(self, messages, **kwargs):
        if messages[0]["content"] == SEARCH_PLANNER_PROMPT:
            return {"choices": [{"message": {"content": "NONE"}}]}
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"choices": [{"message": {"content": reply}}]}


class FakeGit:
    def commit_file(self, relative_path: str, message: str) -> str:
        return "test-commit"


def _stream_events(client: TestClient, message: str) -> list[dict]:
    response = client.post("/chat/stream", json={"message": message, "thinking": False})
    assert response.status_code == 200
    events = []
    for block in response.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


def _app_with_note(tmp_path: Path, replies: list[str | Exception]):
    settings = _settings(tmp_path)
    note = settings.vault_root / "Quartz Harbor.md"
    note.write_text(ORIGINAL, encoding="utf-8")
    app = create_app(settings)
    app.state.indexer.full_scan()
    app.state.changes.git = FakeGit()
    llama = FakeLlama()
    llama.replies = list(replies)
    app.state.llama = llama
    return app, settings, note


def _update_reply(content: str = UPDATED, significance: str = "high") -> str:
    return json.dumps({"action": "update", "significance": significance, "path": "Quartz Harbor.md", "reason": "Adds tide timing", "content": content})


def test_parse_decision_tolerates_fences_and_prose():
    raw = 'Here you go:\n```json\n{"action": "create", "significance": "high", "path": "Quartz Harbor", "reason": "Tide facts", "content": "```markdown\\n# Quartz Harbor\\n```"}\n```'
    decision = parse_decision(raw)
    assert decision is not None
    assert decision.action == "create"
    assert decision.path == "Quartz Harbor.md"
    assert decision.content == "# Quartz Harbor\n"


def test_parse_decision_rejects_garbage():
    assert parse_decision("no json here") is None
    assert parse_decision("{not json}") is None


def test_validate_decision_gates(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    base = "# Quartz Harbor\n\n" + "Tide notes. " * 20
    (vault / "Quartz Harbor.md").write_text(base, encoding="utf-8")
    candidates = [CandidateNote("Quartz Harbor.md", base)]

    def update(**overrides) -> ReviewDecision:
        values = {"action": "update", "significance": "high", "path": "Quartz Harbor.md", "reason": "r", "content": base + "\nNew fact.\n"}
        values.update(overrides)
        return ReviewDecision(**values)

    assert validate_decision(update(), candidates, vault) is None
    assert validate_decision(update(significance="low"), candidates, vault) is not None
    assert validate_decision(update(action="none"), candidates, vault) is not None
    assert validate_decision(update(path="Other.md"), candidates, vault) is not None
    assert validate_decision(update(content=base[:10]), candidates, vault) is not None
    assert validate_decision(update(content=base), candidates, vault) is not None
    assert validate_decision(ReviewDecision("create", "high", "Tides.md", "r", "# Tides\n"), candidates, vault) is None
    assert validate_decision(ReviewDecision("create", "high", "Quartz Harbor.md", "r", "# X\n"), candidates, vault) is not None
    assert validate_decision(ReviewDecision("create", "high", "_AI/Inbox.md", "r", "# X\n"), candidates, vault) is not None
    assert validate_decision(ReviewDecision("create", "high", "../escape.md", "r", "# X\n"), candidates, vault) is not None


def test_make_diff_marks_added_lines():
    lines, truncated = make_diff("# A\n", "# A\n\nNew line.\n", "A.md")
    assert "+New line." in lines
    assert not truncated


def test_find_candidates_skips_reserved_paths(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.vault_root / "Quartz Harbor.md").write_text("# Quartz Harbor\nTide notes.", encoding="utf-8")
    (settings.vault_root / "_AI").mkdir()
    (settings.vault_root / "_AI" / "Inbox.md").write_text("# Inbox\nQuartz Harbor tide idea.", encoding="utf-8")
    app = create_app(settings)
    app.state.indexer.full_scan()
    with app.state.database.connect() as connection:
        candidates = find_candidates(connection, None, settings.vault_root, "Quartz Harbor tides")
    assert [item.path for item in candidates] == ["Quartz Harbor.md"]


def test_chat_proposes_update_and_accept_writes_it(tmp_path: Path):
    app, _, note = _app_with_note(tmp_path, [_update_reply()])
    client = TestClient(app)
    events = _stream_events(client, QUESTION)
    proposal = next(item["proposal"] for item in events if item["type"] == "vault_proposal")
    assert events[-1]["type"] == "done"
    assert proposal["operation"] == "propose_edit"
    assert proposal["state"] == "pending"
    assert "+## Tides" in proposal["diff"]
    assert note.read_text(encoding="utf-8") == ORIGINAL

    approved = client.post(f"/changes/{proposal['change_id']}/approve")
    assert approved.status_code == 200
    assert note.read_text(encoding="utf-8") == UPDATED


def test_insignificant_decision_creates_no_proposal(tmp_path: Path):
    reply = json.dumps({"action": "create", "significance": "low", "path": "Tides.md", "reason": "minor", "content": "# Tides\n\nSmall detail.\n"})
    app, _, _ = _app_with_note(tmp_path, [reply])
    events = _stream_events(TestClient(app), QUESTION)
    assert not any(item["type"] == "vault_proposal" for item in events)
    assert TestClient(app).get("/changes").json() == []


def test_review_failure_does_not_break_chat(tmp_path: Path):
    app, _, _ = _app_with_note(tmp_path, [RuntimeError("model down")])
    events = _stream_events(TestClient(app), QUESTION)
    assert events[-1]["type"] == "done"
    assert not any(item["type"] == "vault_proposal" for item in events)


def test_create_proposal_can_be_refused_without_writing(tmp_path: Path):
    reply = json.dumps({"action": "create", "significance": "high", "path": "Quartz Tides", "reason": "New topic", "content": "# Quartz Tides\n\nTide schedule notes.\n"})
    app, settings, _ = _app_with_note(tmp_path, [reply])
    client = TestClient(app)
    events = _stream_events(client, QUESTION)
    proposal = next(item["proposal"] for item in events if item["type"] == "vault_proposal")
    assert proposal["is_new"] is True
    assert proposal["target_path"] == "Quartz Tides.md"

    refused = client.post(f"/changes/{proposal['change_id']}/reject")
    assert refused.status_code == 200
    assert not (settings.vault_root / "Quartz Tides.md").exists()
    assert client.get(f"/changes/{proposal['change_id']}").json()["state"] == "rejected"


def test_propose_edit_revises_pending_proposal(tmp_path: Path):
    app, _, note = _app_with_note(tmp_path, [_update_reply()])
    client = TestClient(app)
    events = _stream_events(client, QUESTION)
    change_id = next(item["proposal"]["change_id"] for item in events if item["type"] == "vault_proposal")

    app.state.llama.replies = ["```markdown\n" + UPDATED.replace("tide notes.", "tide notes and sunset times.") + "```"]
    revised = client.post(f"/changes/{change_id}/revise", json={"suggestion": "mention sunset times"})
    assert revised.status_code == 200
    assert any("sunset times" in line for line in revised.json()["diff"])
    assert "sunset times" in client.get(f"/changes/{change_id}").json()["proposed_content"]
    assert note.read_text(encoding="utf-8") == ORIGINAL


def test_revise_rejects_truncated_output(tmp_path: Path):
    app, _, _ = _app_with_note(tmp_path, [_update_reply()])
    client = TestClient(app)
    events = _stream_events(client, QUESTION)
    change_id = next(item["proposal"]["change_id"] for item in events if item["type"] == "vault_proposal")

    app.state.llama.replies = ["# Quartz"]
    revised = client.post(f"/changes/{change_id}/revise", json={"suggestion": "shorten it"})
    assert revised.status_code == 422


def test_fix_wikilinks_keeps_known_targets_and_unwraps_unknown():
    content = "See [[Tides]], [[Quartz Harbor#Piers|the pier]] and [[Unknown Place]]."
    assert fix_wikilinks(content, {"Tides", "Quartz Harbor"}) == "See [[Tides]], [[Quartz Harbor#Piers|the pier]] and Unknown Place."


def test_open_draft_is_revised_by_later_exchange(tmp_path: Path):
    app, _, note = _app_with_note(tmp_path, [_update_reply()])
    client = TestClient(app)
    first = next(item["proposal"] for item in _stream_events(client, QUESTION) if item["type"] == "vault_proposal")

    later = UPDATED + "\nSunset times vary by season.\n"
    app.state.llama.replies = [_update_reply(later)]
    events = _stream_events(client, "Also, when is sunset at Quartz Harbor in winter?")
    second = next(item["proposal"] for item in events if item["type"] == "vault_proposal")

    assert second["change_id"] == first["change_id"]
    assert "Sunset times vary" in client.get(f"/changes/{first['change_id']}").json()["proposed_content"]
    assert len(client.get("/changes").json()) == 1
    assert note.read_text(encoding="utf-8") == ORIGINAL


def test_open_draft_leaves_drafts_list_when_refused(tmp_path: Path):
    app, _, _ = _app_with_note(tmp_path, [_update_reply()])
    client = TestClient(app)
    proposal = next(item["proposal"] for item in _stream_events(client, QUESTION) if item["type"] == "vault_proposal")
    assert [item["change_id"] for item in client.get("/vault-drafts").json()] == [proposal["change_id"]]

    assert client.post(f"/changes/{proposal['change_id']}/reject").status_code == 200
    assert client.get("/vault-drafts").json() == []


def test_splice_section_paragraph_operations():
    from tiny_brain.agent.vault_review import splice_section_paragraph
    from tiny_brain.vault.chunker import chunk_note
    from tiny_brain.vault.parser import parse_note

    base = "# Title\n\nIntroduction paragraph.\n\n## Section One\n\nBody of section one.\n\n## Section Two\n\nBody of section two.\n"
    note = parse_note("test.md", base)
    sections = chunk_note(note)

    # 1. Append to Section One
    sec_one = next(s for s in sections if "Section One" in s.heading_path)
    appended = splice_section_paragraph(base, sections, "append", sec_one.section_id, "Additional detail for section one.")
    assert "Body of section one.\n\nAdditional detail for section one.\n\n## Section Two" in appended
    assert appended.startswith("# Title\n\nIntroduction paragraph.\n\n## Section One")
    assert appended.endswith("## Section Two\n\nBody of section two.\n")

    # 2. Replace Section Two
    sec_two = next(s for s in sections if "Section Two" in s.heading_path)
    replaced = splice_section_paragraph(base, sections, "replace_section", sec_two.section_id, "## Section Two\n\nCompletely revised body two.")
    assert "Completely revised body two." in replaced
    assert "Body of section two." not in replaced
    assert "Body of section one." in replaced

    # 3. Create Note
    created = splice_section_paragraph("", [], "create_note", None, "# New Note\n\nContent.")
    assert created == "# New Note\n\nContent.\n"


def test_chat_proposes_section_append_and_generates_exact_diff(tmp_path: Path):
    from tiny_brain.vault.chunker import chunk_note
    from tiny_brain.vault.parser import parse_note

    base_note = "# Quartz Harbor\n\nA port city with tide notes.\n"
    sections = chunk_note(parse_note("Quartz Harbor.md", base_note))
    target_section_id = sections[0].section_id

    # 2B model outputs ONLY the section_id, action='append', and small paragraph
    section_append_reply = json.dumps({
        "action": "append",
        "significance": "high",
        "path": "Quartz Harbor.md",
        "section_id": target_section_id,
        "reason": "Adds tide timing paragraph to main section",
        "paragraph": "## Tides\n\nTides repeat about every twelve hours.",
    })

    app, _, note = _app_with_note(tmp_path, [section_append_reply])
    client = TestClient(app)
    events = _stream_events(client, QUESTION)
    proposal = next(item["proposal"] for item in events if item["type"] == "vault_proposal")

    assert proposal["operation"] == "propose_edit"
    assert proposal["state"] == "pending"
    # Diff generated by Python's standard difflib shows only the added lines
    assert "+## Tides" in proposal["diff"]
    assert "+Tides repeat about every twelve hours." in proposal["diff"]
    assert note.read_text(encoding="utf-8") == ORIGINAL

    # Approve and verify spliced file write
    approved = client.post(f"/changes/{proposal['change_id']}/approve")
    assert approved.status_code == 200
    expected = "# Quartz Harbor\n\nA port city with tide notes.\n\n## Tides\n\nTides repeat about every twelve hours.\n"
    assert note.read_text(encoding="utf-8") == expected
