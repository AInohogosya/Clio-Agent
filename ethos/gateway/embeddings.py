from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections.abc import Sequence
from typing import Any, Protocol

from ethos.observability.logger import get_logger

logger = get_logger("ethos.gateway.embeddings")

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Processes that answer `/v1/embed` with their own model rather than loading one.
#
# This is the list that took six ONNX sessions down to two. `ethos-toolhost` and
# every `ethos-workers-N` used to call `build_embedder` and each got its own
# ~1.2 GB `bge-large` arena, purely to turn one short string into a vector — work
# the gateway process was already doing for its own `/v1/embed` route. The
# capability being delegated is small and the cost of duplicating it was the
# single largest thing in the supervisor's resident set.
#
# `gateway` and `core` are absent on purpose. `core` is the process the agent
# thinks in: it embeds a focus title and a percept summary on every cycle, and a
# loopback round trip per call buys nothing that a 0.067 GB model would not. The
# gateway is absent because it is the thing serving the requests.
REMOTE_ROLES = frozenset({"toolhost", "worker"})


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


def _model_dim(model_name: str) -> int | None:
    """What fastembed says this model's vectors are, or None if it cannot say.

    Only used to refuse a mismatch before it becomes a corrupted database, so a
    model fastembed does not know about — a local directory, a private mirror —
    has to answer None rather than raise.
    """
    try:
        from fastembed import TextEmbedding

        for entry in TextEmbedding.list_supported_models():
            if entry.get("model") == model_name:
                return int(entry["dim"])
    except Exception:
        return None
    return None


def assert_model_dim(model_name: str, dim: int) -> None:
    """Refuse a model whose width the `vector(...)` columns cannot hold.

    `episodes.embedding` and its three siblings are declared `vector(1024)`, and
    that is a schema fact rather than a preference: swapping in
    `BAAI/bge-small-en-v1.5` for its 0.067 GB instead of 1.2 GB is the cheapest
    memory win available, and it is also 384 dimensions that Postgres will refuse
    on insert. The failure without this check is not a crash — it is every write
    to `record_episode` raising, or worse, a fallback embedder quietly filling a
    store that is no longer one space.

    So the check happens once, at construction, and says what to do about it.
    """
    declared = _model_dim(model_name)
    if declared is None or declared == dim:
        return
    raise ValueError(
        f"embedding model {model_name!r} produces {declared}-dimensional vectors but "
        f"gateway.embedding.dim is {dim}. The embedding columns are vector({dim}); a "
        f"narrower model needs `ALTER TABLE ... ALTER COLUMN embedding TYPE vector({declared})` "
        f"plus a re-embed, and a wider one cannot be stored at all. Set both to match, or "
        f"keep the model that is already in the store."
    )


class FastembedEmbedder:
    """Production local embeddings via fastembed (ONNX, no external API)."""

    def __init__(
        self,
        model_name: str,
        dim: int,
        *,
        cache_dir: str | None = None,
        threads: int = 1,
    ):
        from fastembed import TextEmbedding

        assert_model_dim(model_name, dim)

        if cache_dir:
            # fastembed's default is a per-user temp directory on some platforms
            # and `~/.cache/fastembed` on others, neither of which is a promise.
            # A cache the agent owns means the 1.2 GB of weights are downloaded
            # once and survive every restart, every rebuild of the container, and
            # every deployment that shares the volume.
            os.makedirs(cache_dir, exist_ok=True)

        # The model is fetched on first use and it is not small, so this is where
        # a cold install spends its first minutes. The agent is constructed after
        # this returns and publishes nothing until it does, so a reader waiting on
        # the link sees no agent at all — and the one question worth answering
        # about that silence is why, which is this line and not a stack.
        logger.info("embedder.loading", model=model_name, threads=threads,
                    cache_dir=cache_dir or "<fastembed default>")
        try:
            self._model = TextEmbedding(
                model_name=model_name, cache_dir=cache_dir, threads=threads,
            )
        except Exception:
            logger.exception("embedder.load_failed", model=model_name)
            raise
        self.dim = dim
        logger.info("embedder.ready", model=model_name, dim=dim, threads=threads)

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


class RemoteEmbedder:
    """Embeddings borrowed from the gateway process over its own socket.

    Same duck-type as every other embedder here, and the reason it is a separate
    class rather than a flag: the vector space is chosen by whichever process
    owns the model, so this one must never be configured to change it. It has a
    `dim` and it reports the gateway's, and if it cannot reach the gateway it
    raises rather than substituting hashed vectors. A fallback here would be the
    exact failure the local `build_embedder` refuses to commit — memories written
    in a different space, retrievable and wrong.

    The call is synchronous because `EmbeddingProvider.embed` is, and every one
    of its callers is a coroutine that already awaits the database round trip
    that follows. It blocks this process for one loopback request, a few
    milliseconds, on a path that embeds one summary per memory write.
    """

    def __init__(
        self,
        socket_path: str | None,
        host: str = "127.0.0.1",
        port: int = 8700,
        dim: int = 1024,
        *,
        timeout_s: float = 30.0,
        retries: int = 3,
        retry_delay_s: float = 0.5,
    ):
        import httpx

        from ethos.platform import SUPPORTS_UNIX_SOCKETS

        self.dim = dim
        self._retries = max(0, retries)
        self._retry_delay_s = retry_delay_s
        # Asked once, here, rather than per call: the supervisor starts the
        # gateway before anything that would ask it for a vector, so the socket is
        # either there or the deployment has a problem worth reporting once.
        self._use_socket = bool(socket_path) and SUPPORTS_UNIX_SOCKETS and os.path.exists(str(socket_path))
        if self._use_socket:
            self._base = "http://gateway.local"
            self._http = httpx.Client(
                transport=httpx.HTTPTransport(uds=str(socket_path)),
                base_url=self._base, timeout=timeout_s,
            )
        else:
            self._base = f"http://{host}:{port}"
            self._http = httpx.Client(base_url=self._base, timeout=timeout_s)
        self._closed = False
        logger.info("embedder.remote", via=self._base, dim=dim)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        texts = list(texts)
        if not texts:
            return []
        if self._closed:
            raise RuntimeError("remote embedder is closed")
        for attempt in range(self._retries + 1):
            try:
                response = self._http.post("/v1/embed", json={"texts": texts})
                response.raise_for_status()
                payload = response.json()
                break
            except Exception as exc:
                # Retried, but never swapped. A gateway that is not answering is
                # the one condition that must not be answered with hashed
                # vectors, so this ends in a raised error rather than a fallback:
                # the tool call fails visibly and nothing is written to the store.
                if attempt == self._retries:
                    raise RuntimeError(
                        f"gateway embedding unavailable at {self._base} after "
                        f"{attempt + 1} attempts: {exc}. Embedding is not done locally "
                        f"in this process on purpose; a hashed fallback here would "
                        f"write vectors from a different space into the same store."
                    ) from exc
                logger.warning("embedder.remote_retry", attempt=attempt + 1,
                               of=self._retries + 1, error=str(exc)[:200])
                time.sleep(self._retry_delay_s * (attempt + 1))

        vectors = payload["vectors"]
        # The gateway's own width wins. A drift between the two is a
        # configuration bug, and reading the vectors rather than the declared dim
        # keeps the caller writing numbers that are at least self-consistent.
        self.dim = int(payload.get("dim") or self.dim)
        return vectors

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._http.close()
            except Exception:
                pass


def build_embedder(
    provider: str,
    model_name: str,
    dim: int,
    *,
    cache_dir: str | None = None,
    threads: int = 1,
) -> EmbeddingProvider:
    if provider == "fastembed":
        try:
            return FastembedEmbedder(model_name, dim, cache_dir=cache_dir, threads=threads)
        except Exception:
            # Hashed vectors are a *different space*, not a smaller one: memories
            # already in the store were embedded by the model that could not load,
            # so falling back quietly would leave every later search comparing
            # vectors that were never in the same space. Said out loud, it is a
            # thing somebody can fix; said not at all, it is a retrieval that
            # mysteriously stopped working.
            #
            # A width mismatch is the one failure that must not fall back. That
            # fallback is a configuration mistake with a fix, and swallowing it
            # here is how a store ends up holding two spaces with no record of it.
            if _model_dim(model_name) not in (None, dim):
                raise
            logger.warning("embedder.fallback_hashing", model=model_name, dim=dim)
            return HashingEmbedder(dim)
    return HashingEmbedder(dim)


def build_role_embedder(role: str, config: Any) -> EmbeddingProvider:
    """The embedder `role` should hold, decided in one place.

    `role` is the process — `gateway`, `core`, `toolhost`, `worker` — and this is
    the only place that knows which of them are allowed to instantiate a model.
    Every caller asks, so adding a process cannot quietly re-add a copy: the
    question is asked in one function instead of at four call sites that each
    answer it for themselves.
    """
    embedding = config.gateway.embedding
    if role in REMOTE_ROLES and embedding.provider not in ("hashing",):
        return RemoteEmbedder(
            config.gateway.socket_path,
            host=config.gateway.host,
            port=config.gateway.port,
            dim=embedding.dim,
            timeout_s=embedding.remote_timeout_s,
            retries=embedding.remote_retries,
        )
    return build_embedder(
        embedding.provider, embedding.model, embedding.dim,
        cache_dir=embedding.resolve_cache_dir(config),
        threads=embedding.threads,
    )
