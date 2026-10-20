"""Per-model token prices, and the honest answer to "what did that call cost".

Three things were wrong with the old calculator, and they were wrong in ways
that all pointed the same direction -- upward.

1. A model's price was a flat `dict.get(model, defaults)`. Anything not spelled
   out in `pricing.yaml` was billed at an invented mid-range rate. The base
   model this deployment actually thinks with was not in that file, so the
   majority of every day's spend was a guess wearing a dollar sign. Resolution
   is now layered: a live provider price, then the checked-in table, then a
   deliberately pessimistic high-water mark that is logged rather than passed
   off as a fact.

2. Token counts were summed as if every provider reported them the same way.
   They do not. Anthropic's `input_tokens` excludes cached tokens; OpenAI's
   `prompt_tokens` and Google's `promptTokenCount` *include* them. Adding both
   numbers billed the cached tokens twice. The same is true of reasoning
   tokens inside `completion_tokens`. The counting convention is now a property
   of the provider adapter, so `Usage` means one thing everywhere downstream.

3. Anthropic cache *writes* were collapsed to a boolean and priced at nothing,
   while every other vendor charges 1.25x-2x the input rate to store a prefix.

Free stays free. A model the provider bills at zero bills at zero here; the
old table charged the invented default for `:free` routes, which is where
"money was spent" on a free model came from.

Prices are USD per million tokens, which is how every vendor publishes them.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ethos.observability.logger import get_logger

logger = get_logger("ethos.pricing")

TOKENS_PER_UNIT = 1_000_000

# Where a price came from, in descending order of trust. Surfaced on every
# breakdown so a wrong number can be traced to its origin without a debugger.
SOURCE_LIVE = "live"
SOURCE_CONFIG = "config"
SOURCE_FREE = "free"
SOURCE_UNKNOWN = "unknown"

# Vendors publish per-token decimals on their pricing endpoints; these are the
# only two field names seen for each, and both arrive as strings.
_LIVE_FIELDS = {
    "input": ("prompt", "input", "input_tokens_per_million"),
    "output": ("completion", "output", "output_tokens_per_million"),
    "cached_input": ("input_cache_read", "cache_read", "cached_input"),
    "cache_write": ("input_cache_write", "cache_write", "cached_input_write"),
}


# Prices are quoted per million, so six decimals is a millionth of a dollar per
# million tokens -- finer than any vendor invoice resolves. Rounding here keeps
# a per-token decimal such as 0.00000089 from surfacing as 0.8899999999999999.
PRICE_PRECISION = 6


def _price(value: float) -> float:
    return round(value, PRICE_PRECISION)


def _num(value: Any) -> float | None:
    """A finite non-negative float, or None if the value is not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:  # NaN or negative: not a price
        return None
    return number


def _per_million(value: Any) -> float | None:
    """Read a live endpoint's per-token decimal as a per-million price.

    Vendor pricing endpoints quote `0.00000032` for a million tokens at $0.32.
    Scaling is applied here and only here, because the heuristic that decides
    whether a number is per-token or per-million is wrong for a per-million
    table: most models cost less than a dollar per million, so "below 1 means
    per-token" would multiply a legitimate $0.14/M input price by a million.
    """
    number = _num(value)
    return _price(number * TOKENS_PER_UNIT) if number is not None else None


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per million tokens for one model on one route."""

    input: float
    output: float
    cached_input: float = 0.0
    cache_write: float = 0.0
    source: str = SOURCE_UNKNOWN

    @classmethod
    def free(cls) -> ModelPrice:
        return cls(0.0, 0.0, 0.0, 0.0, SOURCE_FREE)

    @property
    def is_free(self) -> bool:
        return not (self.input or self.output or self.cached_input or self.cache_write)

    def with_source(self, source: str) -> ModelPrice:
        return ModelPrice(self.input, self.output, self.cached_input, self.cache_write, source)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "cached_input": self.cached_input,
            "cache_write": self.cache_write,
            "output": self.output,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], default_source: str = SOURCE_CONFIG) -> ModelPrice | None:
        """Build from a per-million pricing payload.

        A source that publishes no cache rate is not saying cached tokens are
        free -- it is saying it does not discount them. Defaulting the cache
        rates to the full input price keeps an unstated cache rate on the
        conservative side instead of quietly valuing a cache hit at nothing.
        """
        if not isinstance(data, Mapping):
            return None
        input_price = _num(data.get("input"))
        output_price = _num(data.get("output"))
        if input_price is None and output_price is None:
            return None
        # A model can be free on output and paid on input, or the reverse; an
        # absent half means zero rather than "inherit from the other half".
        base_in = _price(input_price or 0.0)
        cached = _num(data.get("cached_input"))
        write = _num(data.get("cache_write"))
        return cls(
            input=base_in,
            output=_price(output_price or 0.0),
            cached_input=base_in if cached is None else _price(cached),
            cache_write=base_in if write is None else _price(write),
            source=str(data.get("source") or default_source),
        )


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """What a call cost, and enough detail to argue about it later."""

    total_usd: float
    input_usd: float
    cached_input_usd: float
    cache_write_usd: float
    output_usd: float
    source: str
    price: ModelPrice
    authoritative: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_usd": self.total_usd,
            "input_usd": round(self.input_usd, 8),
            "cached_input_usd": round(self.cached_input_usd, 8),
            "cache_write_usd": round(self.cache_write_usd, 8),
            "output_usd": round(self.output_usd, 8),
            "source": self.source,
            "authoritative": self.authoritative,
            "price": self.price.as_dict(),
            "note": self.note,
        }


class PriceTable:
    """Resolved prices for one catalogue, with the resolution order baked in."""

    def __init__(
        self,
        configured: Mapping[str, Mapping[str, Any]] | None = None,
        live: Mapping[str, ModelPrice] | None = None,
        *,
        unknown_price: ModelPrice | None = None,
        free_base_urls: Iterable[str] = (),
    ) -> None:
        self._configured: dict[str, ModelPrice] = {}
        for key, payload in (configured or {}).items():
            price = ModelPrice.from_dict(payload)
            if price is not None:
                self._configured[_norm_key(key)] = price
        # A vendor-prefixed id also answers to its bare name, so
        # "deepseek/deepseek-chat" resolves whether a ledger row recorded it
        # with or without the prefix. `setdefault` keeps an explicitly
        # configured bare entry from being shadowed by an alias.
        for key in list(self._configured):
            if "/" in key:
                self._configured.setdefault(key.split("/", 1)[1], self._configured[key])
        self._live: dict[str, ModelPrice] = dict(live or {})
        self._free_base_urls = {u.rstrip("/").lower() for u in free_base_urls}
        self._unknown = unknown_price or self._derive_high_water_mark()
        self._warned: set[str] = set()

    def _derive_high_water_mark(self) -> ModelPrice:
        """A pessimistic stand-in, scaled off the most expensive price on file.

        Scaling off the configured table rather than hardcoding a number means
        the fallback stays above everything the deployment can actually reach,
        and tightens on its own when the expensive models leave the catalogue.
        `safety_factor` leaves room for a model that is dearer than the dearest
        one already configured.
        """
        known = list(self._configured.values()) + list(self._live.values())
        known = [p for p in known if not p.is_free]
        factor = 1.5
        if not known:
            return ModelPrice(15.0 * factor, 75.0 * factor, 15.0 * factor, 30.0 * factor, SOURCE_UNKNOWN)
        return ModelPrice(
            input=round(max(p.input for p in known) * factor, 6),
            output=round(max(p.output for p in known) * factor, 6),
            cached_input=round(max(p.cached_input for p in known) * factor, 6),
            cache_write=round(max(p.cache_write for p in known) * factor, 6),
            source=SOURCE_UNKNOWN,
        )

    @property
    def unknown_price(self) -> ModelPrice:
        return self._unknown

    def free_price(self) -> ModelPrice:
        return ModelPrice.free()

    def set_live(self, prices: Mapping[str, ModelPrice]) -> None:
        self._live.update(prices)

    # -- resolution ---------------------------------------------------------

    def resolve(self, model: str, provider: str = "", base_url: str = "") -> ModelPrice:
        """The price for one model on one route, never a silent guess.

        Order: an explicit free verdict, a live provider price, the configured
        table (bare model id, then `provider/model`), then the high-water mark
        with a warning. Every branch returns a price; the difference is which
        one and whether anybody was told.
        """
        if _is_free_route(model, base_url, self._free_base_urls):
            return self.free_price()

        for key in self._keys(model, provider):
            live = self._live.get(key)
            if live is not None:
                return live
        for key in self._keys(model, provider):
            configured = self._configured.get(key)
            if configured is not None:
                return configured

        label = f"{provider}/{model}" if provider else model
        if label not in self._warned:
            self._warned.add(label)
            logger.warning(
                "pricing.unknown_model",
                provider=provider or "unknown",
                model=model,
                base_url=base_url or "default",
                charged_per_million=self._unknown.as_dict(),
                action="charging a high-water mark; add the model to pricing.yaml to price it exactly",
            )
        return self._unknown

    def _keys(self, model: str, provider: str) -> list[str]:
        """Lookup keys for a route, most specific first."""
        keys: list[str] = []
        if provider and model:
            keys.append(_norm_key(f"{provider}/{model}"))
        if model:
            keys.append(_norm_key(model))
            # OpenRouter-style ids carry the vendor prefix in the model itself
            # ("deepseek/deepseek-chat"), which is also how a first-party
            # catalogue entry would be written. Both are tried.
            if "/" in model:
                keys.append(_norm_key(model.split("/", 1)[1]))
        return [k for i, k in enumerate(keys) if k and k not in keys[:i]]

    def unknown_models(self) -> list[str]:
        return sorted(self._warned)

    def known_models(self) -> list[str]:
        """Every model this table can currently price, from any source."""
        return sorted(set(self._live) | set(self._configured))


def _norm_key(key: str) -> str:
    return key.strip().lower()


def _is_free_route(model: str, base_url: str, free_base_urls: Iterable[str]) -> bool:
    """Whether this route is billed at zero.

    Three ways a route is free, and all three have to be recognised or a free
    model gets billed at a guess: the provider's own `:free` variant, a
    self-hosted endpoint, and a vendor free tier configured as a free base URL.
    """
    name = (model or "").strip().lower()
    if name.endswith(":free") or ":free-" in name:
        return True
    if name.startswith("local-") or name in {"local", "local-vllm", "local-llama"}:
        return True
    host = (base_url or "").rstrip("/").lower()
    return bool(host) and host in set(free_base_urls)


class LivePriceFetcher:
    """Reads real prices off the providers that publish them.

    Only OpenRouter publishes a machine-readable price for arbitrary hosted
    models, and it is also the route most of this catalogue's third-party
    models take. First-party vendors publish theirs as documentation, which is
    what `pricing.yaml` holds and why the checked-in table stays necessary.
    """

    ENDPOINTS: tuple[tuple[str, str, str], ...] = (
        ("openrouter", "https://openrouter.ai/api/v1/models", "openrouter.ai"),
    )
    TTL_S = 6 * 3600
    MAX_AGE_S = 30 * 24 * 3600

    def __init__(self, cache_path: Path | None = None, timeout_s: float = 10.0) -> None:
        self.cache_path = cache_path
        self.timeout_s = timeout_s

    def cache_is_fresh(self) -> bool:
        if self.cache_path is None or not self.cache_path.exists():
            return False
        try:
            payload = json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return (time.time() - float(payload.get("fetched_at", 0))) < self.TTL_S

    def load_cache(self) -> dict[str, ModelPrice]:
        if self.cache_path is None or not self.cache_path.exists():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        age = time.time() - float(payload.get("fetched_at", 0))
        if age > self.MAX_AGE_S:
            logger.warning("pricing.cache_stale", age_h=round(age / 3600, 1))
            return {}
        out: dict[str, ModelPrice] = {}
        for key, raw in (payload.get("prices") or {}).items():
            price = ModelPrice.from_dict(raw, default_source=SOURCE_LIVE)
            if price is not None:
                out[_norm_key(key)] = price
        return out

    def _write_cache(self, prices: Mapping[str, ModelPrice]) -> None:
        if self.cache_path is None:
            return
        payload = {
            "fetched_at": time.time(),
            "prices": {k: v.as_dict() for k, v in prices.items()},
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic: a half-written cache file is a cache file that reads back
            # as corrupt, and the next start would treat that as "no prices".
            with tempfile.NamedTemporaryFile(
                "w", dir=self.cache_path.parent, delete=False, suffix=".tmp"
            ) as handle:
                json.dump(payload, handle)
                tmp = Path(handle.name)
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            logger.warning("pricing.cache_unwritable", path=str(self.cache_path), error=str(exc))

    def fetch(self) -> dict[str, ModelPrice]:
        """Every price the live endpoints publish, keyed by model id."""
        import httpx

        merged: dict[str, ModelPrice] = {}
        for source, url, _host in self.ENDPOINTS:
            try:
                response = httpx.get(url, timeout=self.timeout_s)
                response.raise_for_status()
                merged.update(self._parse(source, response.json()))
                logger.info("pricing.fetched", source=source, models=len(merged))
            except Exception as exc:  # noqa: BLE001 - a pricing page being down
                # is a reason to use the checked-in table, not a reason to stop.
                logger.warning("pricing.fetch_failed", source=source, error=str(exc)[:200])
        if merged:
            self._write_cache(merged)
        return merged

    @staticmethod
    def _parse(source: str, payload: Any) -> dict[str, ModelPrice]:
        out: dict[str, ModelPrice] = {}
        entries = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(entries, list):
            return out
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            model_id = entry.get("id")
            raw = entry.get("pricing")
            if not isinstance(model_id, str) or not isinstance(raw, Mapping):
                continue
            fields = {
                name: _per_million(_first(raw, names))
                for name, names in _LIVE_FIELDS.items()
            }
            input_price, output_price = fields["input"], fields["output"]
            if input_price is None and output_price is None:
                continue
            base_in = input_price or 0.0
            price = ModelPrice(
                input=base_in,
                output=output_price or 0.0,
                cached_input=fields["cached_input"] if fields["cached_input"] is not None else base_in,
                cache_write=fields["cache_write"] if fields["cache_write"] is not None else base_in,
                source=SOURCE_LIVE,
            )
            out[_norm_key(model_id)] = price
            # A vendor-prefixed id also answers to the bare name, so a
            # first-party catalogue entry ("gemini-2.5-pro") resolves without a
            # second entry. Never let a bare key overwrite a real model id.
            if "/" in model_id:
                bare = _norm_key(model_id.split("/", 1)[1])
                out.setdefault(bare, price)
        return out

    def prices(self, prefer_cache: bool = True) -> dict[str, ModelPrice]:
        """Cached prices when they are fresh, a live fetch otherwise."""
        if prefer_cache and self.cache_is_fresh():
            cached = self.load_cache()
            if cached:
                return cached
        fetched = self.fetch()
        return fetched or self.load_cache()


def _first(raw: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if raw.get(name) is not None:
            return raw[name]
    return None
