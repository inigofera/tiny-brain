from __future__ import annotations

import logging
from typing import Any

from .prompts import SEARCH_PLANNER_PROMPT

logger = logging.getLogger(__name__)


async def plan_vault_search(llama: Any, message: str, context: str | None = None) -> str | None:
    """Return the vault search query the model chose, or None when no lookup is needed."""
    user_content = f"Conversation so far:\n{context}\n\nQuestion: {message}" if context else message
    try:
        response = await llama.complete(
            [{"role": "system", "content": SEARCH_PLANNER_PROMPT}, {"role": "user", "content": user_content}],
            temperature=0.0,
            thinking=False,
        )
        text = (response["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        logger.exception("search planner failed; searching with the question itself")
        return message
    if not text:
        return None
    query = text.splitlines()[0].strip(" \"'`")
    if not query or query.upper() == "NONE":
        return None
    return query
