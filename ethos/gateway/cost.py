from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ethos.config import EthosConfig
from ethos.db import Database
from ethos.gateway.normalizer import estimate_tokens
from ethos.observability.logger import get_logger
from ethos.pricing import (
    TOKENS_PER_UNIT,
    CostBreakdown,
    LivePriceFetcher,
    ModelPrice,
    PriceTable,
)
from ethos.schemas.models import ModelCallRecord, ModelRequest, Usage
from ethos.schemas.tools import ToolErrorCode

logger = get_logger("ethos.gateway.cost")


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


async def spend_totals(db: Database | None, since: datetime) -> dict[str, float]:
    """Day and month totals from the ledger, on the guard's own terms."""
    if db is None:
        return {"day_total": 0.0, "month_total": 0.0}
    day = await db.fetchval(SPEND_SQL, since) or 0.0
    month = await db.fetchval(SPEND_SQL, start_of_month(since)) or 0.0
    return {"day_total": float(day), "month_total": float(month)}


class BudgetExceededError(RuntimeError):
    def __init__(self, message: str, bucket: str, spent: float, cap: float):
        super().__init__(message)
        self.bucket = bucket
        self.spent = spent
        self.cap = cap


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
      a particular way in the table" -- the resolution in `ethos/pricing.py`
      already established what the route costs, and a free route that produced
      a non-zero total was the bug this replaces.
    """

    def __init__(self, config: EthosConfig, table: PriceTable | None = None) -> None:
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


class SpendGuard:
    """Enforces H2 (financial exposure caps) at the gateway boundary."""

    def __init__(self, db: Database | None, config: EthosConfig):
        self.db = db
        self.config = config
        self.daily_cap = config.permissions.hard_limits.H2_daily_spend_usd
        self.monthly_cap = config.permissions.hard_limits.H2_monthly_spend_usd
        reserve_pct = config.gateway.budget.commitment_reserve_pct
        self.commitment_cap = round(self.daily_cap * reserve_pct, 6)
        self.discretionary_cap = round(self.daily_cap - self.commitment_cap, 6)

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

    async def check(self, bucket: str, est_cost: float) -> None:
        if self.db is None:
            return
        # Nothing to protect against when the next call is expected to be free.
        # Skipping the query matters more than it looks: on a deployment whose
        # whole traffic is free models this is the difference between four
        # round-trips to Postgres before every request and none.
        if est_cost <= 0.0:
            return
        spend = await self.spend()
        if spend["month_total"] + est_cost > self.monthly_cap:
            raise BudgetExceededError(
                f"monthly spend cap exceeded: {spend['month_total']:.2f} + {est_cost:.4f} > {self.monthly_cap:.2f}",
                bucket="month", spent=spend["month_total"], cap=self.monthly_cap,
            )
        if spend["day_total"] + est_cost > self.daily_cap:
            raise BudgetExceededError(
                f"daily spend cap exceeded: {spend['day_total']:.2f} + {est_cost:.4f} > {self.daily_cap:.2f}",
                bucket="day", spent=spend["day_total"], cap=self.daily_cap,
            )
        if bucket == "commitment" and spend["day_commitment"] + est_cost > self.commitment_cap:
            raise BudgetExceededError(
                f"commitment reserve exceeded: {spend['day_commitment']:.2f} + {est_cost:.4f} > {self.commitment_cap:.2f}",
                bucket="commitment", spent=spend["day_commitment"], cap=self.commitment_cap,
            )
        if bucket == "discretionary" and spend["day_discretionary"] + est_cost > self.discretionary_cap:
            raise BudgetExceededError(
                f"discretionary budget exhausted: {spend['day_discretionary']:.2f} + {est_cost:.4f} > {self.discretionary_cap:.2f}",
                bucket="discretionary", spent=spend["day_discretionary"], cap=self.discretionary_cap,
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
    config: EthosConfig, table: PriceTable, *, fetch: bool = False,
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
