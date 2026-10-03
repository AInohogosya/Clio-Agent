from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

from ethos.observability.logger import get_logger

logger = get_logger("ethos.gateway.embeddings")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic, dependency-free embedder used for tests and offline mode.

    Tokens are hashed into `dim` buckets with sublinear term weighting and
    L2 normalization; identical texts always produce identical vectors.
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            counts: dict[str, int] = {}
            for token in _TOKEN_RE.findall(text.lower()):
                counts[token] = counts.get(token, 0) + 1
            for token, tf in counts.items():
                weight = 1.0 + math.log(tf)
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest, "big") % self.dim
                sign = 1.0 if digest[0] % 2 == 0 else -1.0
                vec[bucket] += sign * weight
                second = int.from_bytes(hashlib.blake2b(("2" + token).encode(), digest_size=8).digest(), "big") % self.dim
                vec[second] += sign * weight * 0.5
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FastembedEmbedder:
    """Production local embeddings via fastembed (ONNX, no external API)."""

    def __init__(self, model_name: str, dim: int):
        from fastembed import TextEmbedding

        # The model is fetched on first use and it is not small, so this is where
        # a cold install spends its first minutes. The agent is constructed after
        # this returns and publishes nothing until it does, so a reader waiting on
        # the link sees no agent at all — and the one question worth answering
        # about that silence is why, which is this line and not a stack.
        logger.info("embedder.loading", model=model_name)
        try:
            self._model = TextEmbedding(model_name=model_name)
        except Exception:
            logger.exception("embedder.load_failed", model=model_name)
            raise
        self.dim = dim
        logger.info("embedder.ready", model=model_name, dim=dim)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = list(self._model.embed(list(texts), batch_size=32))
        out: list[list[float]] = []
        for v in vectors:
            vec = [float(x) for x in v]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


def build_embedder(provider: str, model_name: str, dim: int) -> EmbeddingProvider:
    if provider == "fastembed":
        try:
            return FastembedEmbedder(model_name, dim)
        except Exception:
            # Hashed vectors are a *different space*, not a smaller one: memories
            # already in the store were embedded by the model that could not load,
            # so falling back quietly would leave every later search comparing
            # vectors that were never in the same space. Said out loud, it is a
            # thing somebody can fix; said not at all, it is a retrieval that
            # mysteriously stopped working.
            logger.warning("embedder.fallback_hashing", model=model_name, dim=dim)
            return HashingEmbedder(dim)
    return HashingEmbedder(dim)
