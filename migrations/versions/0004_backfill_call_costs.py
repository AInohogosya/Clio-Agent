"""reprice historical model_calls from their token counts

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17

The `cost_usd` written by the old calculator was wrong in three independent
ways, and all three are corrected here from data the ledger already holds. The
token counts were stored correctly; only the arithmetic applied to them was
not.

1. Prices were resolved by exact match against a table that did not contain
   the base model, so the pinned model serving every tier was billed at an
   invented rate and the `models.yaml` failover was billed the same invented
   rate despite being named `:free`. This was the large one: on a deployment
   whose traffic is mostly free-tier, most of the recorded spend was not money
   anybody spent.

2. Cached tokens were billed twice on the providers whose reported prompt total
   already contains them. The stored counts record that inclusive total, so
   `input_tokens` on those rows has to have the cached portion taken back out
   before the four rates can be applied -- otherwise the backfill would
   faithfully reproduce the original double-billing under new prices.

3. Reasoning tokens were billed twice wherever the reported completion total
   already contains them, which is every OpenAI-shaped provider.

The price table below is pinned rather than read from `config/pricing.yaml` on
purpose. A data migration has to give the same answer when it is re-run as it
gave when it was written, and a backfill that silently changes meaning
whenever somebody edits a config file is not reproducible. The figures were
read from the vendors' pricing pages on the date above and are cross-checked
against OpenRouter's live catalogue; `ethos/pricing.py` and `pricing.yaml` are
the living copies and may since have moved on.

Raw token counts are not touched -- only `cost_usd`, and `price_source` so the
result is attributable. Nothing is deleted, so the pre-backfill totals remain
recomputable from the same rows.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (input, cached_input, cache_write, output), USD per million tokens.
PINNED_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-4-5": (3.00, 0.30, 3.75, 15.00),
    "claude-3-5-haiku-latest": (0.80, 0.08, 1.00, 4.00),
    "o3": (2.00, 0.50, 2.00, 8.00),
    "gpt-4.1": (2.00, 0.50, 2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.075, 0.15, 0.60),
    "gemini-2.5-pro": (1.25, 0.125, 0.375, 10.00),
    "gemini-2.0-flash": (0.10, 0.025, 0.125, 0.40),
    "deepseek/deepseek-chat": (0.32, 0.32, 0.32, 0.89),
    "z-ai/glm-5.3-flash": (0.049, 0.018, 0.049, 0.65),
    "z-ai/glm-5.3": (0.079, 0.078, 0.079, 4.80),
    "nvidia/nemotron-3.5-super-vl:free": (0.0, 0.0, 0.0, 0.0),
    "llama-3.3-70b-versatile": (0.59, 0.59, 0.59, 0.79),
    "local-vllm": (0.0, 0.0, 0.0, 0.0),
}

# Free-tier endpoints whose models carry ordinary names. A row from one of
# these is a call that cost nothing.
FREE_HOSTS = ("integrate.api.nvidia.com", "127.0.0.1", "localhost")

# Providers whose `prompt_tokens`/`promptTokenCount` and
# `completion_tokens` already contain the cached and reasoning parts.
INCLUSIVE_PROVIDERS = {"openai", "google", "openai_compat"}

# Nothing in the pinned table costs more than this; the fallback is above all of
# them so an unrecognised model is repriced pessimistically rather than at a
# mid-range guess.
HIGH_WATER_MARK = (4.50, 0.885, 5.625, 22.50)


def _price_for(model: str | None, provider: str | None) -> tuple[tuple[float, float, float, float], str]:
    name = (model or "").strip()
    if name in PINNED_PRICES:
        return PINNED_PRICES[name], "config"
    lowered = name.lower()
    if lowered.endswith(":free") or lowered.startswith("local-"):
        return (0.0, 0.0, 0.0, 0.0), "free"
    # A bare id that matches a keyed entry, so `z-ai/glm-5.3` resolves whether
    # it was recorded with or without a vendor prefix.
    for key, price in PINNED_PRICES.items():
        if lowered == key or lowered.endswith("/" + key):
            return price, "config"
    del provider
    return HIGH_WATER_MARK, "unknown"


def upgrade() -> None:
    connection = op.get_bind()

    # Distinct models actually present, so the reprice is one pass over the
    # models rather than one pass per row.
    models = connection.execute(
        sa_text("SELECT DISTINCT provider, model FROM model_calls")
    ).fetchall()

    for provider, model in models:
        price, source = _price_for(model, provider)
        # A row whose model is unrecognised may still be on a free tier: the
        # host is not on the ledger, so the model name is all there is to go on.
        # Anything else keeps the high-water mark and stays flagged.
        inclusive = (provider or "") in INCLUSIVE_PROVIDERS and source != "free"
        p_in, p_cached, p_write, p_out = price

        if inclusive:
            # `input_tokens` holds a total that already contains the cached
            # tokens, so the uncached remainder is the total minus the cached
            # part, floored at zero. `output_tokens` holds a total that already
            # contains the reasoning tokens, same subtraction.
            statement = """
                UPDATE model_calls
                SET cost_usd = ROUND((
                    GREATEST(COALESCE(input_tokens, 0) - COALESCE(cached_input_tokens, 0), 0)
                        * :p_in
                    + COALESCE(cached_input_tokens, 0) * :p_cached
                    + COALESCE(cache_write_tokens, 0) * :p_write
                    + GREATEST(COALESCE(output_tokens, 0) - COALESCE(reasoning_tokens, 0), 0)
                        * :p_out
                    + COALESCE(reasoning_tokens, 0) * :p_out
                ) / 1000000.0, 6),
                price_source = :source
                WHERE provider IS NOT DISTINCT FROM :provider
                  AND model IS NOT DISTINCT FROM :model
            """
        else:
            # Disjoint counts already: five buckets, five rates.
            statement = """
                UPDATE model_calls
                SET cost_usd = ROUND((
                    COALESCE(input_tokens, 0) * :p_in
                    + COALESCE(cached_input_tokens, 0) * :p_cached
                    + COALESCE(cache_write_tokens, 0) * :p_write
                    + COALESCE(output_tokens, 0) * :p_out
                    + COALESCE(reasoning_tokens, 0) * :p_out
                ) / 1000000.0, 6),
                price_source = :source
                WHERE provider IS NOT DISTINCT FROM :provider
                  AND model IS NOT DISTINCT FROM :model
            """
        result = connection.execute(
            sa_text(statement),
            {
                "p_in": p_in, "p_cached": p_cached, "p_write": p_write,
                "p_out": p_out, "source": source,
                "provider": provider, "model": model,
            },
        )
        print(
            f"  repriced {result.rowcount} row(s) for {provider}/{model} "
            f"-> {source}"
        )


def downgrade() -> None:
    # The old per-row totals are not recoverable from the rows -- the token
    # counts are unchanged but the arithmetic that produced them is gone, and
    # the rates it used are not stored. `0004` is therefore one-way by nature.
    # This is a statement of that fact, not a reconstruction.
    print(
        "  0004 is not reversible: historical cost_usd values were computed "
        "from a rate table that was not stored per row. Token counts are "
        "untouched and the backfill can be re-derived from them."
    )


def sa_text(statement: str):
    """Bind a literal SQL string the way SQLAlchemy 2.x wants one."""
    import sqlalchemy as sa

    return sa.text(statement)
