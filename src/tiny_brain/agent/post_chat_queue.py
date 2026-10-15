from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..db import Database


@dataclass(frozen=True, slots=True)
class PostChatJob:
    job_id: str
    job_type: str
    session_id: str | None
    payload: dict[str, Any]
    state: str
    attempts: int
    error: str | None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostChatQueue:
    def __init__(self, database: Database):
        self.database = database

    def enqueue(self, job_type: str, payload: dict[str, Any], session_id: str | None = None) -> str:
        job_id = str(uuid.uuid4())
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO post_chat_jobs(job_id, job_type, session_id, payload_json, state, attempts, created_at)
                   VALUES (?, ?, ?, ?, 'pending', 0, ?)""",
                (job_id, job_type, session_id, json.dumps(payload), now),
            )
        return job_id

    def fetch_next_pending(self) -> PostChatJob | None:
        now = _now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """SELECT job_id, job_type, session_id, payload_json, state, attempts, error, created_at, started_at, completed_at
                       FROM post_chat_jobs
                       WHERE state = 'pending'
                       ORDER BY created_at ASC
                       LIMIT 1"""
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                job_id = str(row[0])
                connection.execute(
                    "UPDATE post_chat_jobs SET state = 'running', started_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
                connection.execute("COMMIT")
                return PostChatJob(
                    job_id=job_id,
                    job_type=str(row[1]),
                    session_id=str(row[2]) if row[2] else None,
                    payload=json.loads(str(row[3])),
                    state="running",
                    attempts=int(row[5]),
                    error=str(row[6]) if row[6] else None,
                    created_at=str(row[7]),
                    started_at=now,
                    completed_at=str(row[9]) if row[9] else None,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_completed(self, job_id: str) -> None:
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE post_chat_jobs SET state = 'completed', completed_at = ? WHERE job_id = ?",
                (now, job_id),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE post_chat_jobs SET state = 'failed', error = ?, completed_at = ?, attempts = attempts + 1 WHERE job_id = ?",
                (error[:2000], now, job_id),
            )

    def pending_count(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM post_chat_jobs WHERE state = 'pending'").fetchone()
            return int(row[0]) if row else 0

    def list_jobs(self, limit: int = 50, state: str | None = None) -> list[PostChatJob]:
        with self.database.connect() as connection:
            if state:
                rows = connection.execute(
                    "SELECT job_id, job_type, session_id, payload_json, state, attempts, error, created_at, started_at, completed_at FROM post_chat_jobs WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT job_id, job_type, session_id, payload_json, state, attempts, error, created_at, started_at, completed_at FROM post_chat_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            PostChatJob(
                job_id=str(row[0]),
                job_type=str(row[1]),
                session_id=str(row[2]) if row[2] else None,
                payload=json.loads(str(row[3])),
                state=str(row[4]),
                attempts=int(row[5]),
                error=str(row[6]) if row[6] else None,
                created_at=str(row[7]),
                started_at=str(row[8]) if row[8] else None,
                completed_at=str(row[9]) if row[9] else None,
            )
            for row in rows
        ]
