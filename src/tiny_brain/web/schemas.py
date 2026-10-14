from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=30)
    context_tokens: int = Field(default=4000, ge=100, le=32000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    session_id: str | None = Field(default=None, max_length=100)
    context: str | None = Field(default=None, max_length=20000)
    thinking: bool = True


class DraftRevisionRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    draft: str = Field(min_length=1, max_length=50000)
    suggestion: str = Field(min_length=1, max_length=4000)


class ProposalRequest(BaseModel):
    operation: str
    target_path: str
    proposed_content: str = Field(max_length=1_000_000)
    description: str = Field(max_length=500)
    reason: str = Field(max_length=2000)


class ProposalRevisionRequest(BaseModel):
    suggestion: str = Field(min_length=1, max_length=4000)
