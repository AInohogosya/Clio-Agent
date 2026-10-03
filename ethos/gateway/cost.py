from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from ethos.config import EthosConfig
from ethos.db import Database
from ethos.schemas.models import ModelCallRecord, Usage
from ethos.schemas.tools import ToolErrorCode


class BudgetExceededError(RuntimeError):
    def __init__(self, message: str, bucket: str, spent: float, cap: float):
        super().__init__(message)
        self.bucket = bucket
        self.spent = spent
        self.cap = cap


class CostCalculator:
    def __init__(self, config: EthosConfig):
        self.pricing = config.pricing

    def compute(self, model: str, usage: Usage) -> float:
        price = self.pricing.price_for(model)
        cost = (
            usage.input_tokens * price.input / 1_000_000
            + usage.cached_input_tokens * price.cached_input / 1_000_000
            + usage.output_tokens * price.output / 1_000_000
            + usage.reasoning_tokens * price.output / 1_000_000
        )
        return round(cost, 6)

    def estimate(self, model: str, est_input: int = 5000, est_output: int = 800) -> float:
        price = self.pricing.price_for(model)
        return round((est_input * price.input + est_output * price.output) / 1_000_000, 6)


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
        return datetime.combine(date.today(), datetime.min.time(), tzinfo=UTC)

    async def _start_of_month(self) -> datetime:
        today = date.today()
        return datetime.combine(today.replace(day=1), datetime.min.time(), tzinfo=UTC)

    async def spend(self, bucket: str | None = None) -> dict[str, float]:
        if self.db is None:
            return {"day_total": 0.0, "month_total": 0.0, "day_commitment": 0.0, "day_discretionary": 0.0}
        start_day = await self._start_of_day()
        start_month = await self._start_of_month()
        day_total = await self.db.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls WHERE ts >= $1 AND status = 'ok'",
            start_day,
        ) or 0.0
        month_total = await self.db.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls WHERE ts >= $1 AND status = 'ok'",
            start_month,
        ) or 0.0
        day_commitment = await self.db.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls WHERE ts >= $1 AND status = 'ok' AND budget_bucket = 'commitment'",
            start_day,
        ) or 0.0
        day_discretionary = await self.db.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls WHERE ts >= $1 AND status = 'ok' AND budget_bucket = 'discretionary'",
            start_day,
        ) or 0.0
        del bucket
        return {
            "day_total": float(day_total),
            "month_total": float(month_total),
            "day_commitment": float(day_commitment),
            "day_discretionary": float(day_discretionary),
        }

    async def check(self, bucket: str, est_cost: float) -> None:
        if self.db is None:
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
            "budget_bucket, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, "
            "cost_usd, latency_ms, status, error) "
            "VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)",
            record.provider, record.model, record.tier, record.purpose, record.thread_id,
            record.intention_id, record.budget_bucket, record.input_tokens,
            record.cached_input_tokens, record.output_tokens, record.reasoning_tokens,
            record.cost_usd, record.latency_ms, record.status, record.error,
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


def error_code_for(exc: Exception) -> str:
    if isinstance(exc, BudgetExceededError):
        return "budget_exceeded"
    if isinstance(exc, TimeoutError):
        return ToolErrorCode.TIMEOUT
    return ToolErrorCode.EXECUTION


def dumps_for_log(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)
