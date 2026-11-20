from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from clio.config import ClioConfig
from clio.db import Database
from clio.env import env
from clio.gateway.normalizer import estimate_tokens
from clio.observability.logger import get_logger
from clio.pricing import (
    TOKENS_PER_UNIT,
    CostBreakdown,
    LivePriceFetcher,
    ModelPrice,
    PriceTable,
)
from clio.schemas.models import ModelCallRecord, ModelRequest, Usage
from clio.schemas.tools import ToolErrorCode

logger = get_logger("clio.gateway.cost")


# One definition of "today" for the whole system. The dashboard and the spend
# guard were reading the same ledger through two different day boundaries --
# `date_trunc('day', ts)` against the database session's timezone here, UTC
# midnight there -- so a deployment whose database runs on local time showed a
# dashboard total for a different day than the cap it was drawn against. UTC
# because that is what the caps were always written against.
def start_of_day(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def start_of_month(now: datetime | None = None) -> datetime:
    return start_of_day(now).replace(day=1)


# Trailing windows for the sub-daily caps. Daily and monthly stay calendar
# windows; these are "the last N seconds" so a per-minute cap bites during a
# burst and releases when the burst ends, rather than at a clock boundary.

WINDOW_SECONDS: dict[str, float] = {
    "per_minute": 60.0,
    "per_10_minutes": 600.0,
    "per_hour": 3600.0,
    "per_5_hours": 18000.0,
}


def _aware(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def window_start(window: str, now: datetime | None = None) -> datetime:
    """The instant a window's spend starts being counted from."""
    moment = _aware(now)
    if window in WINDOW_SECONDS:
        return moment - timedelta(seconds=WINDOW_SECONDS[window])
    if window == "daily":
        return start_of_day(moment)
    if window == "monthly":
        return start_of_month(moment)
    raise ValueError(f"unknown spend window {window!r}")


# The single spend query. `SpendGuard` and the dashboard bridge both go through
# these, because when they each wrote their own SQL they stopped agreeing: the
# guard filtered `status = 'ok'` and the dashboard did not, so a day with
# failures showed the dashboard a higher total than the cap that was actually
# being enforced against it.
#
# Failed calls are excluded because they are not what this column means. They
# still cost money, and `_note_failure` still writes the row with its cost --
# but a failed attempt that produced no answer is a separate question, and
# mixing it into the number a person reads as "what did today cost" makes that
# number disagree with the number the agent is defending itself against.
SPEND_SQL = """
SELECT COALESCE(SUM(cost_usd), 0) AS total
FROM model_calls
WHERE ts >= $1 AND status = 'ok'
"""

SPEND_BY_BUCKET_SQL = SPEND_SQL + " AND budget_bucket = $2"

SPEND_BY_MODEL_SQL = """
SELECT model, COALESCE(SUM(cost_usd), 0) AS total
FROM model_calls
WHERE ts >= $1 AND status = 'ok'
GROUP BY model
ORDER BY total DESC
LIMIT $2
"""

SPEND_BY_DAY_SQL = """
SELECT to_char(date_trunc('day', ts AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS day,
       COALESCE(SUM(cost_usd), 0) AS total
FROM model_calls
WHERE ts >= $1 AND status = 'ok'
GROUP BY 1
ORDER BY 1
"""

# Token-count spend, on the same `status = 'ok'` terms as SPEND_SQL. Total
# tokens is every billed token bucket added together: prompt, cache reads,
# cache writes, completion and reasoning.
TOKEN_SQL = """
SELECT COALESCE(SUM(
  COALESCE(input_tokens, 0)
  + COALESCE(cached_input_tokens, 0)
  + COALESCE(cache_write_tokens, 0)
  + COALESCE(output_tokens, 0)
  + COALESCE(reasoning_tokens, 0)
), 0) AS total
FROM model_calls
WHERE ts >= $1 AND status = 'ok'
"""

TOKEN_BY_BUCKET_SQL = TOKEN_SQL + " AND budget_bucket = $2"


async def spend_totals(db: Database | None, since: datetime) -> dict[str, float]:
    """Day and month totals from the ledger, on the guard's own terms."""
    if db is None:
        return {"day_total": 0.0, "month_total": 0.0}
    day = await db.fetchval(SPEND_SQL, since) or 0.0
    month = await db.fetchval(SPEND_SQL, start_of_month(since)) or 0.0
    return {"day_total": float(day), "month_total": float(month)}


async def token_totals(db: Database | None, since: datetime) -> float:
    """Token total since `since`, on the guard's own terms."""
    if db is None:
        return 0.0
    return float(await db.fetchval(TOKEN_SQL, since) or 0.0)


class BudgetExceededError(RuntimeError):
    def __init__(self, message: str, bucket: str, spent: float, cap: float,
                 window: str | None = None, unit: str | None = None):
        super().__init__(message)
        self.bucket = bucket
        self.spent = spent
        self.cap = cap
        self.window = window
        self.unit = unit


class CostCalculator:
    """Turns a `Usage` into a dollar figure, and can say how it got there.

    `compute` returns a `CostBreakdown` rather than a float. The old signature
    returned a float, and a float cannot answer "why is this number what it
    is" -- which is the question every person who has looked at a wrong spend
    total actually has. The breakdown carries the price used, where that price
    came from, and the four component costs.

    Two rules hold everywhere below:

    * A provider that reports what it billed wins. `usage.cost` from
      OpenRouter is the invoice line, not an estimate of one, so when it is
      present the rate card is not consulted at all.
    * A model priced at zero costs zero. Not "zero, unless the id is spelled
      a particular way in the table" -- the resolution in `clio/pricing.py`
      already established what the route costs, and a free route that produced
      a non-zero total was the bug this replaces.
    """

    def __init__(self, config: ClioConfig, table: PriceTable | None = None) -> None:
        self.config = config
        self.pricing = config.pricing
        self._table = table or config.pricing.table()

    @property
    def table(self) -> PriceTable:
        return self._table

    def attach_live(self, prices: Mapping[str, ModelPrice]) -> None:
        """Fold freshly fetched live prices into the table already in use."""
        self._table.set_live(prices)

    def price_for(self, model: str, provider: str = "", base_url: str = "") -> ModelPrice:
        return self._table.resolve(model, provider=provider, base_url=base_url)

    def compute(
        self,
        model: str,
        usage: Usage,
        provider: str = "",
        base_url: str = "",
        billed_cost_usd: float | None = None,
    ) -> CostBreakdown:
        if billed_cost_usd is not None:
            # The provider has already done this arithmetic, on its own rate
            # card, including any batch discount or tier it negotiated. Our
            # number would only be an estimate of a number we were given.
            return CostBreakdown(
                total_usd=round(billed_cost_usd, 6),
                input_usd=0.0, cached_input_usd=0.0, cache_write_usd=0.0, output_usd=0.0,
                source="provider", price=ModelPrice.free(),
                authoritative=True, note=f"provider-reported cost for {provider or 'call'}",
            )

        price = self.price_for(model, provider=provider, base_url=base_url)
        input_usd = usage.input_tokens * price.input / TOKENS_PER_UNIT
        cached_usd = usage.cached_input_tokens * price.cached_input / TOKENS_PER_UNIT
        write_usd = usage.cache_write_tokens * price.cache_write / TOKENS_PER_UNIT
        output_usd = (usage.output_tokens + usage.reasoning_tokens) * price.output / TOKENS_PER_UNIT
        total = input_usd + cached_usd + write_usd + output_usd
        return CostBreakdown(
            total_usd=round(total, 6),
            input_usd=input_usd,
            cached_input_usd=cached_usd,
            cache_write_usd=write_usd,
            output_usd=output_usd,
            source=price.source,
            price=price,
        )

    def estimate(
        self,
        model: str,
        request: ModelRequest | None = None,
        provider: str = "",
        base_url: str = "",
    ) -> float:
        """What the next call is expected to cost, before it is made.

        Used by the router to rank candidates and by the spend guard to decide
        whether there is room. Both want a number before any tokens exist, so
        this reads the request it was given: the prompt is measurable now, and
        the ceiling the caller set is the worst case they have promised to pay.
        That is a very different question from the old one, which asked what a
        hypothetical 5,000-token prompt into an 800-token completion would cost
        on every model regardless of what was actually being sent -- a cheap
        model and an expensive one got the same guess.
        """
        price = self.price_for(model, provider=provider, base_url=base_url)
        if price.is_free:
            return 0.0
        if request is None:
            # No request to read: fall back to a mid-sized call rather than
            # inventing a size. Deliberately modest, since this feeds a cap.
            est_input, est_output, est_reasoning = 4_000, 800, 0
        else:
            est_input = estimate_tokens(request)
            est_reasoning = min(request.reasoning_budget or 0, request.max_tokens)
            # What is left of the ceiling once reasoning has taken its share is
            # the most the answer can occupy, so bounding by it is exact
            # rather than a guess at typical verbosity.
            est_output = max(0, min(request.max_tokens, 800) - est_reasoning)
        cached = int(est_input * 0.5) if request is not None else 0
        return round(
            (
                (est_input - cached) * price.input
                + cached * price.cached_input
                + (est_output + est_reasoning) * price.output
            ) / TOKENS_PER_UNIT,
            6,
        )

    def estimate_tokens(
        self,
        request: ModelRequest | None = None,
    ) -> int:
        """What the next call is expected to consume in tokens, before it is made.

        The token twin of `estimate`: the same request read the same way, but
        answered as a token count so a cap configured in tokens can be checked
        before any tokens exist.
        """
        if request is None:
            return 4_800
        est_input = estimate_tokens(request)
        est_reasoning = min(request.reasoning_budget or 0, request.max_tokens)
        est_output = max(0, min(request.max_tokens, 800) - est_reasoning)
        return est_input + est_output + est_reasoning

    def token_usage_of(self, usage: Usage) -> int:
        """Total tokens a finished call consumed, all buckets combined."""
        return (
            usage.input_tokens
            + usage.cached_input_tokens
            + usage.cache_write_tokens
            + usage.output_tokens
            + usage.reasoning_tokens
        )


class SpendGuard:
    """Enforces H2 (financial exposure caps) at the gateway boundary.

    In the default field configuration the caps are advisories (`on_exceed:
    warn`): the breach is logged loudly and shown on the dashboard, which
    reads the same ledger, but no call is refused -- a budget that stops the
    agent stops it for the rest of the day, which for a tool meant to carry
    someone's whole working day is the one failure the money was supposed to
    prevent. `on_exceed: block` restores the hard raise.

    Every window is individually enableable and all are disabled by default
    (see `HardLimitsConfig.spend_limits`). A `None` cap enforces nothing and
    costs no query; a set cap is checked on each call in the configured
    `budget_unit` (USD cost or token count).
    """

    WARN_INTERVAL_S = 300.0

    def __init__(self, db: Database | None, config: ClioConfig):
        self.db = db
        self.config = config
        limits = config.permissions.hard_limits
        self.daily_cap = limits.H2_daily_spend_usd
        self.monthly_cap = limits.H2_monthly_spend_usd
        self.unit = (getattr(limits, "budget_unit", "usd") or "usd").strip().lower()
        reserve_pct = config.gateway.budget.commitment_reserve_pct
        if self.daily_cap is not None:
            self.commitment_cap = round(self.daily_cap * reserve_pct, 6)
            self.discretionary_cap = round(self.daily_cap - self.commitment_cap, 6)
        else:
            self.commitment_cap = None
            self.discretionary_cap = None
        self._last_warn: dict[str, float] = {}

    async def _start_of_day(self) -> datetime:
        return start_of_day()

    async def _start_of_month(self) -> datetime:
        return start_of_month()

    async def spend(self, bucket: str | None = None) -> dict[str, float]:
        if self.db is None:
            return {"day_total": 0.0, "month_total": 0.0, "day_commitment": 0.0, "day_discretionary": 0.0}
        start_day = await self._start_of_day()
        start_month = await self._start_of_month()
        day_total = await self.db.fetchval(SPEND_SQL, start_day) or 0.0
        month_total = await self.db.fetchval(SPEND_SQL, start_month) or 0.0
        day_commitment = await self.db.fetchval(SPEND_BY_BUCKET_SQL, start_day, "commitment") or 0.0
        day_discretionary = await self.db.fetchval(SPEND_BY_BUCKET_SQL, start_day, "discretionary") or 0.0
        return {
            "day_total": float(day_total),
            "month_total": float(month_total),
            "day_commitment": float(day_commitment),
            "day_discretionary": float(day_discretionary),
        }

    async def window_spends(self) -> dict[str, dict[str, float]]:
        """Spend per enabled window, in both units.

        Returns ``{window: {"usd": float, "tokens": float}}`` for every
        enabled window only. Disabled windows are absent, so a caller can
        tell "nothing spent" from "window off".
        """
        enabled = self.config.permissions.hard_limits.enabled_limits()
        if self.db is None or not enabled:
            return {}
        now = _aware()
        totals: dict[str, dict[str, float]] = {}
        for window in enabled:
            start = window_start(window, now)
            usd = float(await self.db.fetchval(SPEND_SQL, start) or 0.0)
            tokens = float(await self.db.fetchval(TOKEN_SQL, start) or 0.0)
            totals[window] = {"usd": usd, "tokens": tokens}
        return totals

    def _fmt(self, value: float) -> str:
        if self.unit == "tokens":
            return f"{value:,.0f}"
        return f"{value:.2f}"

    def _soft_mode(self) -> bool:
        """Whether a breach warns rather than raises.

        Read per call so a restart or a settings change is picked up
        without rebuilding the guard; `CLIO_BUDGET_MODE` overrides the
        file for a deployment that wants one without the other.
        """
        mode = env("BUDGET_MODE")
        if not mode:
            mode = str(getattr(self.config.permissions.hard_limits, "on_exceed", "block"))
        return mode.strip().lower() == "warn"

    async def check(self, bucket: str, est_cost: float,
                    est_tokens: int | None = None) -> None:
        if self.db is None:
            return
        enabled = self.config.permissions.hard_limits.enabled_limits()
        # With every window disabled (the default) there is nothing to
        # protect against and the check costs zero queries.
        if not enabled:
            return
        # Nothing to protect against when the next call is expected to be
        # free in the active unit. Skipping the query matters more than it
        # looks: on a deployment whose whole traffic is free models this is
        # the difference between four round-trips to Postgres before every
        # request and none.
        if self.unit == "tokens":
            if est_tokens is not None and est_tokens <= 0:
                return
            if est_tokens is None and est_cost <= 0.0:
                return
        elif est_cost <= 0.0:
            # A free call in USD mode can still be a token spend, so when
            # token mode is also relevant we only skip if both are zero. In
            # pure USD mode a zero-cost call cannot breach a USD cap.
            if est_tokens is None:
                return
        estimate = float(est_tokens) if (self.unit == "tokens" and est_tokens is not None) else float(est_cost)
        if estimate <= 0.0:
            return
        totals = await self.window_spends()
        breaches: list[tuple[str, str, float, float]] = []
        for window, cap in enabled.items():
            spent = totals.get(window, {}).get(self.unit, 0.0)
            if spent + estimate > cap:
                label = window.replace("_", " ")
                breaches.append((
                    label,
                    f"{label} {self.unit} cap exceeded: {self._fmt(spent)} + {self._fmt(estimate)} > {self._fmt(cap)}",
                    spent, cap,
                ))
        # Commitment/discretionary split only applies when the daily cap
        # that defines it is enabled.
        if self.daily_cap is not None:
            if self.unit == "tokens":
                start_day = await self._start_of_day()
                commitment_total = float(await self.db.fetchval(TOKEN_BY_BUCKET_SQL, start_day, "commitment") or 0.0)
                discretionary_total = float(await self.db.fetchval(TOKEN_BY_BUCKET_SQL, start_day, "discretionary") or 0.0)
            else:
                spend = await self.spend()
                commitment_total = spend["day_commitment"]
                discretionary_total = spend["day_discretionary"]
            if bucket == "commitment" and self.commitment_cap is not None and commitment_total + estimate > self.commitment_cap:
                breaches.append((
                    "commitment",
                    f"commitment reserve exceeded: {self._fmt(commitment_total)} + {self._fmt(estimate)} > {self._fmt(self.commitment_cap)}",
                    commitment_total, self.commitment_cap,
                ))
            if bucket == "discretionary" and self.discretionary_cap is not None and discretionary_total + estimate > self.discretionary_cap:
                breaches.append((
                    "discretionary",
                    f"discretionary budget exhausted: {self._fmt(discretionary_total)} + {self._fmt(estimate)} > {self._fmt(self.discretionary_cap)}",
                    discretionary_total, self.discretionary_cap,
                ))
        if not breaches:
            return
        if not self._soft_mode():
            b_bucket, message, spent, cap = breaches[0]
            raise BudgetExceededError(message, bucket=b_bucket, spent=spent, cap=cap, window=b_bucket, unit=self.unit)
        # Soft mode: say it once per bucket per interval rather than on every
        # call, so a working afternoon past the cap is one visible line on the
        # dashboard, not a wall of them in the log the person stopped reading.
        now = time.monotonic()
        for b_bucket, message, spent, cap in breaches:
            if now - self._last_warn.get(b_bucket, 0.0) < self.WARN_INTERVAL_S:
                continue
            self._last_warn[b_bucket] = now
            logger.warning(
                "spend.soft_cap_exceeded", bucket=b_bucket,
                spent=round(spent, 4), cap=cap, est_cost=round(est_cost, 6),
                est_tokens=est_tokens, unit=self.unit,
                message=message, action="allowed (on_exceed: warn)",
            )

    async def record(self, record: ModelCallRecord) -> None:
        if self.db is None:
            return
        await self.db.execute(
            "INSERT INTO model_calls (ts, provider, model, tier, purpose, thread_id, intention_id, "
            "budget_bucket, input_tokens, cached_input_tokens, cache_write_tokens, "
            "output_tokens, reasoning_tokens, cost_usd, price_source, latency_ms, status, error) "
            "VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)",
            record.provider, record.model, record.tier, record.purpose, record.thread_id,
            record.intention_id, record.budget_bucket, record.input_tokens,
            record.cached_input_tokens, record.cache_write_tokens,
            record.output_tokens, record.reasoning_tokens,
            record.cost_usd, record.price_source, record.latency_ms, record.status, record.error,
        )

    async def capital_at_risk(self) -> float:
        if self.db is None:
            return 0.0
        start_day = await self._start_of_day()
        rows = await self.db.fetch(
            "SELECT metric, value FROM value_ledger WHERE metric IN ('capital_at_risk', 'realized_pnl') AND ts >= $1",
            start_day,
        )
        exposure = 0.0
        for row in rows:
            exposure += float(row["value"])
        return max(0.0, exposure)


class ToolResultBudget:
    pass


async def refresh_live_prices(
    config: ClioConfig, table: PriceTable, *, fetch: bool = False,
) -> int:
    """Fold live vendor prices into `table`, and report how many landed.

    Called once at gateway startup. A pricing page being unreachable is not an
    error: the checked-in table is a complete fallback by design, so this logs
    what it could not reach and returns rather than raising. That is the whole
    point of having both.

    The cache is consulted first and only refetched when asked or when the
    cached copy has gone stale, so a gateway that restarts hourly does not
    refetch hourly.
    """
    if not config.pricing.live_pricing:
        return 0
    fetcher = LivePriceFetcher(
        cache_path=config.pricing.cache_file(),
        timeout_s=config.pricing.live_timeout_s,
    )
    prices = fetcher.fetch() if fetch else fetcher.prices()
    if not prices:
        logger.warning(
            "pricing.no_live_prices",
            models_in_table=len(table.known_models()),
            action="using the checked-in pricing table",
        )
        return 0
    table.set_live(prices)
    unknown = table.unknown_models()
    logger.info(
        "pricing.live_prices_loaded",
        live_models=len(prices),
        unknown_models=unknown,
    )
    return len(prices)


def error_code_for(exc: Exception) -> str:
    if isinstance(exc, BudgetExceededError):
        return "budget_exceeded"
    if isinstance(exc, TimeoutError):
        return ToolErrorCode.TIMEOUT
    return ToolErrorCode.EXECUTION


def dumps_for_log(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)
