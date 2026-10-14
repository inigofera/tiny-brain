from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..agent.citations import validate_answer
from ..agent.chat import ChatAgent
from ..agent.llama import LlamaClient
from ..agent.prompts import SYSTEM_PROMPT, VAULT_REVISION_PROMPT
from ..agent.search_planner import plan_vault_search
from ..agent.tools import ReadTools
from ..agent.vault_review import (
    MAX_NOTE_CHARS,
    MAX_OPEN_DRAFTS,
    MIN_REVIEW_CHARS,
    REVIEW_CREATE_PREFIX,
    REVIEW_DESCRIPTION_PREFIXES,
    REVIEW_UPDATE_PREFIX,
    CandidateNote,
    build_review_messages,
    check_content,
    decide_vault_change,
    find_candidates,
    make_diff,
    normalize_decision,
    note_titles,
    strip_fence,
    validate_decision,
)
from ..changes.audit import AuditLog
from ..changes.git import GitService
from ..changes.service import ChangeService
from ..config import Settings
from ..db import Database
from ..memory.service import MemoryService
from ..search.embeddings import FastEmbedProvider
from ..search.retrieval import RetrievalResult, Retriever
from ..vault.indexer import VaultIndexer, iter_markdown_paths
from ..vault.paths import UnsafePath, resolve_vault_path
from .schemas import ChatRequest, DraftRevisionRequest, ProposalRequest, ProposalRevisionRequest, SearchRequest

TEMPLATE_ROOT = Path(__file__).parent / "templates"
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


def _is_draft_request(message: str) -> bool:
    normalized = message.lower()
    has_note_target = any(noun in normalized for noun in ("note", "entry", "page"))
    has_draft_intent = any(verb in normalized for verb in ("draft", "create", "start", "add", "write"))
    return has_note_target and has_draft_intent


def _conversation_context_for(message: str, context: str | None, *, draft_mode: bool) -> str:
    if not context:
        return "[none]"
    return context


def _draft_topic(message: str) -> str:
    match = re.search(r"\b(?:about|on|explain|describe|define)\s+(.+?)(?:\s+for\s+|\s+from\s+|\s+in\s+|\s+using\s+|[.;,]|$)", message, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    headings = re.findall(r"(?m)^#{1,3}\s+(.+?)\s*$", message)
    for heading in headings:
        if heading.strip().lower() != "draft note":
            return heading.strip()
    return ""


def get_or_create_active_session(connection) -> str:
    row = connection.execute("SELECT session_id FROM sessions WHERE active = 1 ORDER BY updated_at DESC LIMIT 1").fetchone()
    if row:
        return str(row[0])
    now = datetime.now(timezone.utc).isoformat()
    session_id = str(uuid.uuid4())
    connection.execute("INSERT INTO sessions(session_id, created_at, updated_at, active) VALUES (?, ?, ?, 1)", (session_id, now, now))
    return session_id


def record_chat_message(connection, session_id: str, role: str, content: str, *, classification: dict | None = None, retrieval: dict | None = None) -> str:
    now = datetime.now(timezone.utc).isoformat()
    message_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO messages(message_id, session_id, role, content, created_at, classification_json, retrieval_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message_id, session_id, role, content, now, json.dumps(classification) if classification else None, json.dumps(retrieval) if retrieval else None),
    )
    connection.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
    return message_id


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    database = Database(settings.database)
    database.migrate()
    embedding_provider = FastEmbedProvider(settings.embedding_model) if settings.embedding_runtime.lower() == "fastembed" else None
    indexer = VaultIndexer(settings.vault_root, database, embedding_provider)
    audit = AuditLog(database, settings.data_root)
    changes = ChangeService(settings.vault_root, database, indexer, audit, GitService(settings.vault_root))
    memory = MemoryService(settings.vault_root, database, indexer, audit, enabled=settings.auto_inbox_enabled, threshold=settings.auto_inbox_confidence)
    llama = LlamaClient(settings.llama_base_url, settings.chat_model, timeout=settings.llama_timeout, max_tokens=settings.max_output_tokens)
    templates = Jinja2Templates(directory=str(TEMPLATE_ROOT))

    app = FastAPI(title="Tiny Brain", version="0.1.0")
    app.state.settings = settings
    app.state.database = database
    app.state.indexer = indexer
    app.state.embedding_provider = embedding_provider
    app.state.changes = changes
    app.state.llama = llama
    app.state.memory = memory
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    def proposal_view(change_id: str) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM change_proposals WHERE change_id = ?", (change_id,)).fetchone()
        if row is None:
            raise KeyError(change_id)
        lines, truncated = make_diff(row["base_content"] or "", row["proposed_content"], row["target_path"])
        return {
            "change_id": change_id, "operation": row["operation"], "target_path": row["target_path"],
            "reason": row["reason"], "is_new": row["operation"] == "create_note", "state": row["state"],
            "diff": lines, "diff_truncated": truncated,
        }

    def open_drafts() -> list[CandidateNote]:
        rows = changes.pending_with_description(REVIEW_DESCRIPTION_PREFIXES, MAX_OPEN_DRAFTS)
        drafts = [CandidateNote(str(row["target_path"]), str(row["proposed_content"]), change_id=str(row["change_id"])) for row in rows]
        return [draft for draft in drafts if len(draft.content) <= MAX_NOTE_CHARS]

    async def vault_proposal_for(question: str, answer: str, context: str | None) -> dict[str, object] | None:
        # Best effort: a failure here must never affect the chat answer.
        if len(question) + len(answer) < MIN_REVIEW_CHARS:
            return None
        try:
            with database.connect() as connection:
                candidates = find_candidates(connection, embedding_provider, settings.vault_root, f"{question} {answer[:400]}", open_drafts())
                titles = note_titles(connection)
            decision = await decide_vault_change(app.state.llama, build_review_messages(question, answer, context, candidates))
            if decision is None:
                logger.info("vault review: no usable decision")
                return None
            decision = normalize_decision(decision, candidates, titles)
            problem = validate_decision(decision, candidates, settings.vault_root)
            if problem is not None:
                logger.info("vault review: %s for %s (action=%s, significance=%s)", problem, decision.path, decision.action, decision.significance)
                return None
            draft = next((item for item in candidates if item.change_id and item.path == decision.path), None)
            if draft is not None:
                changes.revise_pending(draft.change_id or "", decision.content)
                logger.info("vault review: revised open draft %s", decision.path)
                return proposal_view(draft.change_id or "")
            if changes.has_pending_for(decision.path):
                logger.info("vault review: %s already has a pending proposal", decision.path)
                return None
            is_update = decision.action == "update"
            description = (REVIEW_UPDATE_PREFIX if is_update else REVIEW_CREATE_PREFIX) + decision.path
            change_id = changes.create_proposal(
                operation="propose_edit" if is_update else "create_note",
                target_path=decision.path,
                proposed_content=decision.content,
                description=description,
                reason=decision.reason or "Suggested from the conversation",
            )
            logger.info("vault review: proposed %s for %s", decision.action, decision.path)
            return proposal_view(change_id)
        except Exception:
            logger.exception("vault review failed")
            return None

    @app.get("/health")
    async def health() -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("SELECT 1")
        return {"status": "ok", "llama": await llama.health()}

    @app.get("/status")
    async def status() -> dict[str, object]:
        with database.connect() as connection:
            notes = connection.execute("SELECT COUNT(*) FROM notes WHERE index_status = 'indexed'").fetchone()[0]
            sections = connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
            changes_count = connection.execute("SELECT COUNT(*) FROM change_proposals WHERE state = 'pending'").fetchone()[0]
            failures = connection.execute("SELECT COUNT(*) FROM notes WHERE index_status = 'failed'").fetchone()[0]
        return {
            "application": "ok", "llama": await llama.health(), "notes": notes,
            "sections": sections, "pending_changes": changes_count, "index_failures": failures,
            "vault_root": str(settings.vault_root), "runtime_offline": settings.runtime_offline,
            "no_auth_warning": "This first release trusts the LAN and has no application authentication.",
        }

    @app.post("/search")
    async def search(request: SearchRequest) -> dict[str, object]:
        with database.connect() as connection:
            result = Retriever(connection, embedding_provider).search(request.query, limit=request.limit, context_tokens=request.context_tokens)
        return {"query": result.query, "omitted_tokens": result.omitted_tokens, "results": [
            {"citation_id": citation.citation_id, "path": citation.relative_path, "title": citation.title,
             "heading": citation.heading_path, "relationship": citation.relationship, "content": citation.content}
            for citation in result.citations
        ]}

    @app.post("/chat")
    async def chat(request: ChatRequest) -> JSONResponse:
        with database.connect() as connection:
            session_id = get_or_create_active_session(connection)
            record_chat_message(connection, session_id, "user", request.message)
        query = await plan_vault_search(app.state.llama, request.message)
        with database.connect() as connection:
            retrieval = (Retriever(connection, embedding_provider).search(query, limit=8, context_tokens=settings.context_tokens // 2)
                         if query else RetrievalResult("", (), (), 0))
            answer_retrieval = retrieval
            try:
                result = await ChatAgent(llama, settings.max_tool_loops).answer(request.message, answer_retrieval, tools=None, thinking=request.thinking)
                answer = result.answer
            except Exception as exc:
                raise HTTPException(status_code=503, detail="local model is unavailable") from exc
        valid, citations = validate_answer(answer, answer_retrieval)
        sources_list = [asdict(citation) for citation in retrieval.citations]
        with database.connect() as connection:
            record_chat_message(
                connection, session_id, "assistant", answer,
                retrieval={"sources": sources_list, "citations": citations, "citation_valid": valid}
            )
        return JSONResponse({"answer": answer, "reasoning": result.reasoning, "citations": citations, "citation_valid": valid,
                             "sources": sources_list})

    @app.post("/chat/stream")
    async def chat_stream(request: ChatRequest):
        with database.connect() as connection:
            session_id = get_or_create_active_session(connection)
            record_chat_message(connection, session_id, "user", request.message)
        draft_request = _is_draft_request(request.message)
        draft_topic = _draft_topic(request.message) if draft_request else ""
        if draft_request and not draft_topic and request.context:
            draft_topic = _draft_topic(request.context)
        retrieval_query = draft_topic if draft_request else await plan_vault_search(app.state.llama, request.message, request.context)
        with database.connect() as connection:
            retrieval = (Retriever(connection, embedding_provider).search(retrieval_query, limit=8, context_tokens=settings.context_tokens // 2)
                         if retrieval_query else RetrievalResult("", (), (), 0))
        draft_mode = draft_request
        answer_retrieval = retrieval
        draft_prefix = "# Draft note\n\n"
        conversation_context = _conversation_context_for(request.message, request.context, draft_mode=draft_mode)
        source_context = answer_retrieval.context() if answer_retrieval.citations else ""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Vault sources:\n{source_context}\n\nConversation history:\n{conversation_context}\n\nCurrent question: {request.message}"},
        ]
        if draft_mode:
            messages.append({"role": "assistant", "content": draft_prefix})

        async def events():
            answer_parts: list[str] = [draft_prefix] if draft_mode else []
            reasoning_parts: list[str] = []
            try:
                if draft_mode:
                    yield f"data: {json.dumps({'type': 'token', 'content': draft_prefix})}\n\n"
                async for chunk in app.state.llama.stream(
                    messages,
                    temperature=0.1 if draft_mode else 0.2,
                    max_tokens=1024 if draft_mode else None,
                    reasoning_effort="none" if draft_mode else None,
                    thinking=False if draft_mode else request.thinking,
                ):
                    if chunk.reasoning:
                        reasoning_parts.append(chunk.reasoning)
                        yield f"data: {json.dumps({'type': 'reasoning', 'content': chunk.reasoning})}\n\n"
                    if chunk.content:
                        answer_parts.append(chunk.content)
                        yield f"data: {json.dumps({'type': 'token', 'content': chunk.content})}\n\n"
                answer = "".join(answer_parts)
                valid, citations = validate_answer(answer, answer_retrieval)
                if draft_mode and answer.strip():
                    clean_content = re.sub(r"^(?:\s*#\s+Draft note\s*)+", "", answer, flags=re.IGNORECASE).strip() + "\n"
                    topic = draft_topic or "Draft note"
                    target_path = re.sub(r'[\\/:*?"<>|]', "", topic).strip() or "Draft note"
                    if not target_path.lower().endswith(".md"):
                        target_path += ".md"
                    existing = changes.pending_with_description(REVIEW_DESCRIPTION_PREFIXES, 10)
                    existing_draft = next((d for d in existing if d["target_path"] == target_path), None)
                    if existing_draft:
                        changes.revise_pending(str(existing_draft["change_id"]), clean_content)
                        yield f"data: {json.dumps({'type': 'vault_proposal', 'proposal': proposal_view(str(existing_draft['change_id']))})}\n\n"
                    elif not changes.has_pending_for(target_path):
                        change_id = changes.create_proposal(
                            operation="create_note",
                            target_path=target_path,
                            proposed_content=clean_content,
                            description=REVIEW_CREATE_PREFIX + target_path,
                            reason="Drafted from conversation",
                        )
                        yield f"data: {json.dumps({'type': 'vault_proposal', 'proposal': proposal_view(change_id)})}\n\n"
                elif not draft_mode:
                    proposal = await vault_proposal_for(request.message, answer, request.context)
                    if proposal is not None:
                        yield f"data: {json.dumps({'type': 'vault_proposal', 'proposal': proposal})}\n\n"
                sources = [asdict(citation) for citation in answer_retrieval.citations]
                with database.connect() as connection:
                    record_chat_message(
                        connection, session_id, "assistant", answer,
                        retrieval={"sources": sources, "citations": citations, "citation_valid": valid}
                    )
                yield f"data: {json.dumps({'type': 'done', 'reasoning': ''.join(reasoning_parts), 'citation_valid': valid, 'citations': citations, 'sources': sources, 'draft_mode': draft_mode, 'draft_topic': draft_topic if draft_mode else None})}\n\n"
            except Exception:
                yield f"data: {json.dumps({'type': 'error', 'detail': 'local model is unavailable'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/chat/history")
    async def chat_history() -> dict[str, object]:
        with database.connect() as connection:
            session_id = get_or_create_active_session(connection)
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
            ).fetchall()
        messages = []
        for row in rows:
            retrieval = json.loads(row["retrieval_json"]) if row["retrieval_json"] else {}
            messages.append({
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "sources": retrieval.get("sources", []),
                "citations": retrieval.get("citations", []),
                "citation_valid": retrieval.get("citation_valid", True),
            })
        return {"session_id": session_id, "messages": messages}

    @app.post("/chat/new")
    async def new_chat_session() -> dict[str, str]:
        now = datetime.now(timezone.utc).isoformat()
        with database.connect() as connection:
            connection.execute("UPDATE sessions SET active = 0 WHERE active = 1")
            new_id = str(uuid.uuid4())
            connection.execute("INSERT INTO sessions(session_id, created_at, updated_at, active) VALUES (?, ?, ?, 1)", (new_id, now, now))
        return {"session_id": new_id, "status": "created"}

    @app.post("/chat/draft/revise")
    async def revise_draft(request: DraftRevisionRequest) -> dict[str, str]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Topic: {request.topic}\n\nCurrent draft:\n{request.draft}\n\nSuggested edit:\n{request.suggestion}"},
            {"role": "assistant", "content": "# Draft note\n\n"},
        ]
        answer_parts = ["# Draft note\n\n"]
        try:
            async for chunk in app.state.llama.stream(messages, temperature=0.1, max_tokens=1024, reasoning_effort="none"):
                if chunk.content:
                    answer_parts.append(chunk.content)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="local model is unavailable") from exc
        return {"answer": "".join(answer_parts), "draft_topic": request.topic}

    @app.get("/notes/{relative_path:path}")
    async def note(relative_path: str) -> dict[str, object]:
        try:
            path = resolve_vault_path(settings.vault_root, relative_path)
        except UnsafePath as exc:
            raise HTTPException(status_code=400, detail="unsafe note path") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="note not found")
        content = path.read_text(encoding="utf-8")
        if len(content) > 2_000_000:
            raise HTTPException(status_code=413, detail="note is too large")
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM notes WHERE relative_path = ?", (relative_path,)).fetchone()
            backlinks = connection.execute(
                """SELECT DISTINCT s.relative_path FROM links l JOIN sections s ON s.section_id = l.source_section_id
                   WHERE lower(l.target) IN (lower(?), lower(?))""",
                (relative_path, relative_path.removesuffix(".md")),
            ).fetchall()
        return {"path": relative_path, "content": content, "metadata": dict(row) if row else None,
                "backlinks": [item[0] for item in backlinks]}

    @app.get("/changes")
    async def list_changes(state: str | None = None) -> list[dict[str, object]]:
        with database.connect() as connection:
            if state:
                rows = connection.execute("SELECT * FROM change_proposals WHERE state = ? ORDER BY updated_at DESC LIMIT 100", (state,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM change_proposals ORDER BY updated_at DESC LIMIT 100").fetchall()
        result = []
        for row in rows:
            data = dict(row)
            diff_lines, truncated = make_diff(row["base_content"] or "", row["proposed_content"], row["target_path"])
            data["diff"] = diff_lines
            data["diff_truncated"] = truncated
            result.append(data)
        return result

    @app.get("/vault-drafts")
    async def vault_drafts() -> list[dict[str, object]]:
        rows = changes.pending_with_description(REVIEW_DESCRIPTION_PREFIXES, 20)
        return [proposal_view(str(row["change_id"])) for row in rows]

    @app.get("/changes/{change_id}")
    async def get_change(change_id: str) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM change_proposals WHERE change_id = ?", (change_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="change not found")
        data = dict(row)
        diff_lines, truncated = make_diff(row["base_content"] or "", row["proposed_content"], row["target_path"])
        data["diff"] = diff_lines
        data["diff_truncated"] = truncated
        return data

    @app.post("/changes")
    async def create_change(request: ProposalRequest) -> dict[str, str]:
        try:
            change_id = changes.create_proposal(**request.model_dump())
        except (ValueError, UnsafePath) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"change_id": change_id, "state": "pending"}

    @app.post("/changes/{change_id}/approve")
    async def approve_change(change_id: str) -> dict[str, str]:
        try:
            result = changes.approve(change_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="change not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result == "conflict":
            raise HTTPException(status_code=409, detail="proposal conflicts with current note")
        return {"change_id": change_id, "state": "committed", "git_commit_id": result}

    @app.post("/changes/{change_id}/reject")
    async def reject_change(change_id: str) -> dict[str, str]:
        try:
            changes.reject(change_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="change not found") from exc
        return {"change_id": change_id, "state": "rejected"}

    @app.post("/changes/{change_id}/revise")
    async def revise_change(change_id: str, request: ProposalRevisionRequest) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM change_proposals WHERE change_id = ?", (change_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="change not found")
        if row["state"] != "pending":
            raise HTTPException(status_code=409, detail=f"proposal is {row['state']}")
        messages = [
            {"role": "system", "content": VAULT_REVISION_PROMPT},
            {"role": "user", "content": f"Note: {row['target_path']}\n\nCurrent proposed note:\n{row['proposed_content']}\n\nRequested edit:\n{request.suggestion}"},
        ]
        try:
            response = await app.state.llama.complete(messages, temperature=0.1, thinking=False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="local model is unavailable") from exc
        text = response.get("choices", [{}])[0].get("message", {}).get("content") or ""
        revised = strip_fence(str(text)).strip()
        revised = revised + "\n" if revised else ""
        problem = check_content(row["base_content"] or "", revised, is_update=row["operation"] == "propose_edit")
        if problem is not None:
            raise HTTPException(status_code=422, detail=problem)
        try:
            changes.revise_pending(change_id, revised)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return proposal_view(change_id)

    @app.post("/changes/{change_id}/revert")
    async def revert_change(change_id: str) -> dict[str, str]:
        try:
            commit = changes.revert(change_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="change not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"change_id": change_id, "state": "reverted", "git_commit_id": commit}

    @app.post("/index/rebuild")
    async def rebuild_index() -> dict[str, object]:
        return indexer.full_scan()

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "host": settings.lan_host})

    @app.get("/ui/search", response_class=HTMLResponse)
    async def search_page(request: Request):
        return templates.TemplateResponse(request=request, name="search.html", context={"request": request, "host": settings.lan_host})

    @app.get("/ui/changes", response_class=HTMLResponse)
    async def changes_page(request: Request):
        return templates.TemplateResponse(request=request, name="changes.html", context={"request": request, "host": settings.lan_host})

    @app.get("/ui/notes/{relative_path:path}", response_class=HTMLResponse)
    async def note_page(request: Request, relative_path: str):
        try:
            path = resolve_vault_path(settings.vault_root, relative_path)
        except UnsafePath as exc:
            raise HTTPException(status_code=400, detail="unsafe note path") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="note not found")
        content = path.read_text(encoding="utf-8")
        if len(content) > 2_000_000:
            raise HTTPException(status_code=413, detail="note is too large")
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM notes WHERE relative_path = ?", (relative_path,)).fetchone()
            backlinks = connection.execute(
                """SELECT DISTINCT s.relative_path FROM links l JOIN sections s ON s.section_id = l.source_section_id
                   WHERE lower(l.target) IN (lower(?), lower(?))""",
                (relative_path, relative_path.removesuffix(".md")),
            ).fetchall()
        tags = json.loads(row["tags_json"]) if row and row["tags_json"] else []
        title = row["title"] if row else path.stem
        obsidian_uri = f"obsidian://open?file={relative_path}"
        return templates.TemplateResponse(
            request=request,
            name="note.html",
            context={
                "request": request,
                "note": {"title": title, "path": relative_path, "metadata": dict(row) if row else None},
                "content": content,
                "tags": tags,
                "backlinks": [item[0] for item in backlinks],
                "obsidian_uri": obsidian_uri,
                "host": settings.lan_host,
            },
        )

    return app


app = create_app()
