from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .embeddings import EmbeddingUnavailable


@dataclass(slots=True)
class SentenceTransformerProvider:
    model_name: str
    runtime: str = "sentence-transformers"
    _model: object | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailable("sentence-transformers is not installed") from exc
        self._model = SentenceTransformer(self.model_name)

    @property
    def dimension(self) -> int:
        self._ensure_model()
        return int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._ensure_model()
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(vector) for vector in vectors]
