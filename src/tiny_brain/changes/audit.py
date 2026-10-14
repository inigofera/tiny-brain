from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import Database


class AuditLog:
    def __init__(self, database: Database, data_root: Path):
        self.database = database
        self.json_root = data_root / "audit"

    def record(self, event_id: str, event_type: str, payload: dict[str, Any], *, change_id: str | None = None) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO audit_events(event_id, change_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (event_id, change_id, event_type, serialized, created_at),
            )
        target = self.json_root / datetime.now(timezone.utc).strftime("%Y/%m") / f"change-{event_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"event_id": event_id, "event_type": event_type, "created_at": created_at, **payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
