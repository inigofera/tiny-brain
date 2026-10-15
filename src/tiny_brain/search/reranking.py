from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence

logger = logging.getLogger(__name__)


class RerankerUnavailable(RuntimeError):
    pass


class Reranker(Protocol):
    model_name: str

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]: ...


@dataclass(slots=True)
class FastEmbedReranker:
    model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    _model: object | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as exc:
            raise RerankerUnavailable("fastembed cross-encoder is not installed") from exc
        self._model = TextCrossEncoder(model_name=self.model_name)

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        self._ensure_model()
        try:
            scores = self._model.rerank(query, list(documents))
            return [float(score) for score in scores]
        except Exception as exc:
            logger.warning("FastEmbed cross-encoder reranking failed: %s", exc)
            raise
