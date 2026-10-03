from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_s: float, capacity: float):
        self.rate = rate_per_s
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    async def acquire(self, amount: float = 1.0, max_wait_s: float = 60.0) -> bool:
        deadline = time.monotonic() + max_wait_s
        async with self._lock:
            while True:
                self._refill()
                if self.tokens >= amount:
                    self.tokens -= amount
                    return True
                wait = (amount - self.tokens) / self.rate if self.rate > 0 else max_wait_s
                if time.monotonic() + wait > deadline:
                    return False
                await asyncio.sleep(min(wait, deadline - time.monotonic()))


class RateLimiter:
    """Per provider+model token buckets for requests/min and tokens/min."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str, str], TokenBucket] = {}

    def _bucket(self, provider: str, model: str, kind: str, rate_per_s: float, capacity: float) -> TokenBucket:
        key = (provider, model, kind)
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(rate_per_s=rate_per_s, capacity=capacity)
        return self._buckets[key]

    async def acquire(self, provider: str, model: str, rpm: int, tpm: int, est_tokens: int) -> None:
        req_bucket = self._bucket(provider, model, "rpm", rpm / 60.0, max(1, rpm))
        tok_bucket = self._bucket(provider, model, "tpm", tpm / 60.0, max(1, tpm))
        ok = await req_bucket.acquire(1.0, max_wait_s=30.0)
        if not ok:
            raise TimeoutError(f"rate limit (requests) exceeded for {provider}/{model}")
        ok = await tok_bucket.acquire(max(1, est_tokens), max_wait_s=60.0)
        if not ok:
            raise TimeoutError(f"rate limit (tokens) exceeded for {provider}/{model}")
