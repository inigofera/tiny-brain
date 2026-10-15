from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..changes.service import ChangeService
from ..db import Database
from ..memory.extractor import MemoryExtractor
from ..memory.service import MemoryService
from ..search.embeddings import EmbeddingProvider
from ..search.reranking import Reranker
from .llama import LlamaClient
from .post_chat_queue import PostChatJob, PostChatQueue
from .vault_review import (
    MAX_NOTE_CHARS,
    MAX_OPEN_DRAFTS,
    MIN_REVIEW_CHARS,
    REVIEW_CREATE_PREFIX,
    REVIEW_DESCRIPTION_PREFIXES,
    REVIEW_UPDATE_PREFIX,
    CandidateNote,
    _ensure_sections,
    build_review_messages,
    decide_vault_change,
    find_candidates,
    normalize_decision,
    note_titles,
    splice_section_paragraph,
    validate_decision,
)

logger = logging.getLogger(__name__)


class PostChatWorker:
    def __init__(
        self,
        database: Database,
        llama: LlamaClient,
        memory: MemoryService,
        changes: ChangeService,
        vault_root: Path,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
        *,
        idle_threshold: float = 10.0,
        check_interval: float = 0.5,
    ):
        self.database = database
        self.llama = llama
        self.memory = memory
        self.changes = changes
        self.vault_root = vault_root
        self.embedding_provider = embedding_provider
        self.reranker = reranker
        self.idle_threshold = idle_threshold
        self.check_interval = check_interval
        self.queue = PostChatQueue(database)
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def enqueue_memory_extraction(self, message: str, source_message_id: str | None = None, session_id: str | None = None) -> str:
        return self.queue.enqueue(
            "memory_extraction",
            {"message": message, "source_message_id": source_message_id},
            session_id=session_id,
        )

    def enqueue_vault_review(self, question: str, answer: str, context: str | None = None, session_id: str | None = None) -> str:
        return self.queue.enqueue(
            "vault_review",
            {"question": question, "answer": answer, "context": context},
            session_id=session_id,
        )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                processed = await self.process_next_job(force=False)
                if not processed:
                    await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in background worker loop")
                await asyncio.sleep(self.check_interval)

    async def process_next_job(self, force: bool = False) -> bool:
        if not force and not self.llama.is_idle(self.idle_threshold):
            return False

        job = self.queue.fetch_next_pending()
        if job is None:
            return False

        try:
            if job.job_type == "memory_extraction":
                await self._handle_memory_extraction(job)
            elif job.job_type == "vault_review":
                await self._handle_vault_review(job)
            else:
                raise ValueError(f"Unknown job type: {job.job_type}")
            self.queue.mark_completed(job.job_id)
            return True
        except Exception as exc:
            logger.exception("Background job %s failed: %s", job.job_id, exc)
            self.queue.mark_failed(job.job_id, str(exc))
            return True

    async def _handle_memory_extraction(self, job: PostChatJob) -> None:
        message = job.payload.get("message", "")
        source_message_id = job.payload.get("source_message_id")
        extractor = MemoryExtractor(self.llama)
        candidates = await extractor.extract(message, source_message_id=source_message_id)
        for candidate in candidates:
            self.memory.process(candidate)
        logger.info("Background memory extraction processed %d candidates for job %s", len(candidates), job.job_id)

    def _open_drafts(self) -> list[CandidateNote]:
        rows = self.changes.pending_with_description(REVIEW_DESCRIPTION_PREFIXES, MAX_OPEN_DRAFTS)
        drafts = [CandidateNote(str(row["target_path"]), str(row["proposed_content"]), change_id=str(row["change_id"])) for row in rows]
        return [draft for draft in drafts if len(draft.content) <= MAX_NOTE_CHARS]

    async def _handle_vault_review(self, job: PostChatJob) -> None:
        question = job.payload.get("question", "")
        answer = job.payload.get("answer", "")
        context = job.payload.get("context")

        if len(question) + len(answer) < MIN_REVIEW_CHARS:
            return

        with self.database.connect() as connection:
            candidates = find_candidates(
                connection, self.embedding_provider, self.vault_root,
                f"{question} {answer[:400]}", self._open_drafts(), reranker=self.reranker,
            )
            titles = note_titles(connection)

        decision = await decide_vault_change(self.llama, build_review_messages(question, answer, context, candidates))
        if decision is None:
            logger.info("Background vault review: no usable decision for job %s", job.job_id)
            return

        decision = normalize_decision(decision, candidates, titles)
        problem = validate_decision(decision, candidates, self.vault_root)
        if problem is not None:
            logger.info("Background vault review: %s for %s (job %s)", problem, decision.path, job.job_id)
            return

        draft = next((item for item in candidates if item.change_id and item.path == decision.path), None)
        candidate = next((item for item in candidates if item.path == decision.path), None)

        if decision.action in ("append", "replace_section") and candidate is not None:
            sections = _ensure_sections(candidate)
            spliced_content = splice_section_paragraph(candidate.content, sections, decision.action, decision.section_id, decision.content)
            decision = replace(decision, content=spliced_content, action="update")
        elif decision.action == "create_note":
            decision = replace(decision, action="create")

        if draft is not None:
            self.changes.revise_pending(draft.change_id or "", decision.content)
            logger.info("Background vault review: revised open draft %s for job %s", decision.path, job.job_id)
            return

        if self.changes.has_pending_for(decision.path):
            logger.info("Background vault review: %s already has pending proposal for job %s", decision.path, job.job_id)
            return

        is_update = decision.action == "update"
        description = (REVIEW_UPDATE_PREFIX if is_update else REVIEW_CREATE_PREFIX) + decision.path
        change_id = self.changes.create_proposal(
            operation="propose_edit" if is_update else "create_note",
            target_path=decision.path,
            proposed_content=decision.content,
            description=description,
            reason=decision.reason or "Suggested from the conversation (background review)",
            session_id=job.session_id,
        )
        logger.info("Background vault review: created proposal %s for job %s", change_id, job.job_id)
