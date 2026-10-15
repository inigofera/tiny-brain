from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    vault_root: Path
    data_root: Path
    database: Path
    model_root: Path
    llama_base_url: str
    chat_model: str
    embedding_model: str
    embedding_runtime: str
    context_tokens: int
    max_tool_loops: int
    lan_host: str
    auto_inbox_enabled: bool
    auto_inbox_confidence: float
    runtime_offline: bool
    app_host: str
    app_port: int
    max_output_tokens: int = 4096
    llama_timeout: float = 300.0
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    reranker_enabled: bool = False
    reranker_min_score: float = -7.0
    async_post_chat: bool = False
    post_chat_idle_seconds: float = 10.0
    context_compression: bool = True
    max_compressed_sentences: int = 8
    lookup_ngram_min: int = 2
    fast_chat_max_tokens: int = 250

    @classmethod
    def from_env(cls) -> "Settings":
        data_root = Path(os.getenv("TINY_BRAIN_DATA_ROOT", "./data")).expanduser()
        return cls(
            vault_root=Path(os.getenv("TINY_BRAIN_VAULT_ROOT", "./vault")).expanduser(),
            data_root=data_root,
            database=Path(os.getenv("TINY_BRAIN_DATABASE", str(data_root / "database.sqlite"))).expanduser(),
            model_root=Path(os.getenv("TINY_BRAIN_MODEL_ROOT", "./models")).expanduser(),
            llama_base_url=os.getenv("TINY_BRAIN_LLAMA_BASE_URL", "http://llama:8080/v1").rstrip("/"),
            chat_model=os.getenv("TINY_BRAIN_CHAT_MODEL", "gemma-4-E2B-it-q4_0.gguf"),
            embedding_model=os.getenv("TINY_BRAIN_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            embedding_runtime=os.getenv("TINY_BRAIN_EMBEDDING_RUNTIME", "unconfigured"),
            context_tokens=_positive_int("TINY_BRAIN_CONTEXT_TOKENS", 16384),
            max_tool_loops=_positive_int("TINY_BRAIN_MAX_TOOL_LOOPS", 2),
            lan_host=os.getenv("TINY_BRAIN_LAN_HOST", "tiny-brain.home.arpa"),
            auto_inbox_enabled=_bool("TINY_BRAIN_AUTO_INBOX_ENABLED", True),
            auto_inbox_confidence=_confidence(os.getenv("TINY_BRAIN_AUTO_INBOX_CONFIDENCE", "0.9")),
            runtime_offline=_bool("TINY_BRAIN_RUNTIME_OFFLINE", True),
            app_host=os.getenv("TINY_BRAIN_APP_HOST", "0.0.0.0"),
            app_port=_positive_int("TINY_BRAIN_APP_PORT", 8000),
            max_output_tokens=_positive_int("TINY_BRAIN_MAX_OUTPUT_TOKENS", 4096),
            llama_timeout=_positive_float("TINY_BRAIN_LLAMA_TIMEOUT", 300.0),
            reranker_model=os.getenv("TINY_BRAIN_RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2"),
            reranker_enabled=_bool("TINY_BRAIN_RERANKER_ENABLED", False),
            reranker_min_score=float(os.getenv("TINY_BRAIN_RERANKER_MIN_SCORE", "-7.0")),
            async_post_chat=_bool("TINY_BRAIN_ASYNC_POST_CHAT", True),
            post_chat_idle_seconds=_positive_float("TINY_BRAIN_POST_CHAT_IDLE_SECONDS", 10.0),
            context_compression=_bool("TINY_BRAIN_CONTEXT_COMPRESSION", True),
            max_compressed_sentences=_positive_int("TINY_BRAIN_MAX_COMPRESSED_SENTENCES", 8),
            lookup_ngram_min=_positive_int("TINY_BRAIN_LOOKUP_NGRAM_MIN", 2),
            fast_chat_max_tokens=_positive_int("TINY_BRAIN_FAST_CHAT_MAX_TOKENS", 250),
        )

    def ensure_directories(self) -> None:
        self.vault_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.model_root.mkdir(parents=True, exist_ok=True)


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _confidence(value: str) -> float:
    levels = {"low": 0.6, "medium": 0.8, "high": 0.9}
    normalized = value.strip().lower()
    result = levels[normalized] if normalized in levels else float(value)
    if not 0 <= result <= 1:
        raise ValueError("TINY_BRAIN_AUTO_INBOX_CONFIDENCE must be between 0 and 1")
    return result
