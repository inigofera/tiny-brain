from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Protocol, Sequence


class EmbeddingProvider(Protocol):
    model_name: str
    runtime: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbeddingUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class FastEmbedProvider:
    model_name: str
    runtime: str = "fastembed"
    _model: object | None = None

    @property
    def dimension(self) -> int:
        self._ensure_model()
        return int(getattr(self._model, "embedding_size", 0))

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingUnavailable("fastembed is not installed") from exc
        self._model = TextEmbedding(model_name=self.model_name)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._ensure_model()
        return [list(vector) for vector in self._model.embed(list(texts))]


def pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> list[float]:
    if len(blob) % 4:
        raise ValueError("invalid float32 vector blob")
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    denominator = math.sqrt(sum(item * item for item in left) * sum(item * item for item in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0
