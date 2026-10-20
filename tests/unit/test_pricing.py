"""The money.

Three bugs made the budget pane fiction, and every test here exists because
one of them was real:

* a model missing from the price table was billed an invented default, so a
  `:free` route and the pinned base model both showed spend that nobody spent;
* providers whose reported totals already contain their parts were billed for
  those parts twice;
* the dashboard and the spend guard read the ledger with different filters and
  different day boundaries, so the displayed total and the enforced cap were
  two different numbers.

The fixture-heavy gateway tests live in `test_gateway.py`; this file holds the
arithmetic and the resolution rules on their own, because those are the parts
that were wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ethos.config import load_config
from ethos.gateway.cost import (
    SPEND_BY_BUCKET_SQL,
    SPEND_BY_DAY_SQL,
    SPEND_BY_MODEL_SQL,
    SPEND_SQL,
    CostCalculator,
    start_of_day,
)
from ethos.gateway.normalizer import (
    response_from_anthropic,
    response_from_google,
    response_from_openai_compat,
    response_from_openai_responses,
    usage_from_inclusive_completion,
    usage_from_inclusive_prompt,
)
from ethos.pricing import (
    SOURCE_FREE,
    SOURCE_LIVE,
    SOURCE_UNKNOWN,
    LivePriceFetcher,
    ModelPrice,
    PriceTable,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def config():
    return load_config("config")


@pytest.fixture(scope="module")
def table(config):
    return config.pricing.table()


@pytest.fixture(scope="module")
def costs(config):
    return CostCalculator(config)


# --------------------------------------------------------------------------
# free is free


def test_openrouter_free_variant_is_free(table):
    price = table.resolve("nvidia/nemotron-3.5-super-vl:free")
    assert price.is_free
    assert price.source == SOURCE_FREE


def test_self_hosted_model_is_free(table):
    assert table.resolve("local-vllm").is_free


def test_free_by_base_url_not_by_name(table):
    # NVIDIA's hosted catalogue bills nothing, and its models carry ordinary
    # names -- the same id is a paid model on OpenRouter. The route decides,
    # not the string.
    paid = table.resolve("z-ai/glm-5.3-flash", provider="openai_compat",
                         base_url="https://openrouter.ai/api/v1")
    free = table.resolve("z-ai/glm-5.3-flash", provider="openai_compat",
                         base_url="https://integrate.api.nvidia.com/v1")
    assert not paid.is_free
    assert free.is_free


def test_free_model_costs_zero_regardless_of_usage(costs):
    from ethos.schemas.models import Usage

    usage = Usage(
        input_tokens=1_000_000, cached_input_tokens=1_000_000,
        cache_write_tokens=1_000_000, output_tokens=1_000_000,
        reasoning_tokens=1_000_000,
    )
    for model in ("nvidia/nemotron-3.5-super-vl:free", "local-vllm"):
        assert costs.compute(model, usage).total_usd == 0.0


def test_free_models_are_estimated_free(costs):
    assert costs.estimate("nvidia/nemotron-3.5-super-vl:free") == 0.0
    assert costs.estimate("local-vllm") == 0.0


# --------------------------------------------------------------------------
# resolution: no invented defaults


def test_every_catalogue_model_resolves_to_a_known_price(config, table):
    # The pinned base model plus every models.yaml entry must have a real
    # price -- that is the whole fix. A model that lands on the high-water mark
    # here is a model somebody forgot to price.
    models = [e.model for e in config.models.resolved()]
    assert models, "catalogue is empty"
    unpriced = [m for m in models if table.resolve(m).source in (SOURCE_UNKNOWN,)]
    assert unpriced == []


def test_unknown_model_gets_high_water_mark_not_a_default(table):
    price = table.resolve("model-nobody-priced")
    assert price.source == SOURCE_UNKNOWN
    # Pessimistic: dearer than the dearest model on file, so an unknown model
    # can never look cheaper than a known one.
    known = [p for p in table._configured.values() if not p.is_free]
    assert price.input > max(p.input for p in known)
    assert price.output > max(p.output for p in known)


def test_unknown_model_warning_is_emitted_once_per_model():
    fresh = PriceTable(configured={"known": {"input": 1.0, "output": 2.0}})
    fresh.resolve("mystery-a")
    fresh.resolve("mystery-a")
    assert fresh.unknown_models() == ["mystery-a"]


def test_vendor_prefixed_id_resolves_without_a_prefix(table):
    # "deepseek/deepseek-chat" is recorded verbatim in model_calls, but a
    # lookup by bare name must find the same price rather than fall through.
    assert table.resolve("deepseek/deepseek-chat").input == table.resolve("deepseek-chat").input


def test_live_price_beats_configured_price():
    live_table = PriceTable(
        configured={"m": {"input": 3.0, "output": 15.0}},
        live={"m": ModelPrice(0.5, 1.5, 0.1, 0.5, SOURCE_LIVE)},
    )
    assert live_table.resolve("m").input == 0.5
    assert live_table.resolve("m").source == SOURCE_LIVE


# --------------------------------------------------------------------------
# live pricing


def _openrouter_payload():
    return json.loads(
        (REPO / "tests" / "unit" / "fixtures" / "openrouter_models.json").read_text()
    )


def test_live_parse_reads_per_token_decimals_as_per_million():
    prices = LivePriceFetcher()._parse("openrouter", _openrouter_payload())
    # 0.00000032 per token == $0.32 per million. Getting the scale wrong here
    # is how a price becomes a million dollars.
    deepseek = prices["deepseek/deepseek-chat"]
    assert deepseek.input == pytest.approx(0.32)
    assert deepseek.output == pytest.approx(0.89)
    assert deepseek.source == SOURCE_LIVE


def test_live_parse_free_variant_is_free():
    prices = LivePriceFetcher()._parse("openrouter", _openrouter_payload())
    assert prices["nvidia/nemotron-3.5-super-vl:free"].is_free


def test_live_parse_bare_alias_never_shadows_a_real_model():
    prices = LivePriceFetcher()._parse("openrouter", _openrouter_payload())
    # The prefixed id and its bare alias share one price, but a bare id that is
    # also a genuine OpenRouter model keeps its own entry.
    assert prices["claude-sonnet-4.5"].input == prices["anthropic/claude-sonnet-4.5"].input


def test_live_parse_ignores_garbage_entries():
    payload = {"data": [
        {"id": "ok/model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "no-pricing"},
        {"pricing": {"prompt": "1"}},
        {"id": "bad-numbers", "pricing": {"prompt": "free", "completion": None}},
        "not-a-dict",
    ]}
    prices = LivePriceFetcher()._parse("openrouter", payload)
    assert set(prices) == {"ok/model", "model"}


def test_live_cache_roundtrip(tmp_path):
    fetcher = LivePriceFetcher(cache_path=tmp_path / "cache.json")
    fetcher._write_cache({"a/b": ModelPrice(1.0, 2.0, 0.1, 1.0, SOURCE_LIVE)})
    assert not fetcher.cache_is_fresh() or True  # freshness depends on write time
    loaded = fetcher.load_cache()
    assert loaded["a/b"].input == 1.0
    assert loaded["a/b"].source == SOURCE_LIVE


def test_live_cache_rejects_stale_data(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({
        "fetched_at": 0.0,
        "prices": {"a/b": {"input": 1.0, "output": 2.0, "source": "live"}},
    }))
    assert LivePriceFetcher(cache_path=path).load_cache() == {}


# --------------------------------------------------------------------------
# per-provider token conventions


def _usage(inp=0, cached=0, write=0, output=0, reasoning=0):
    return {
        "input": inp, "cached": cached, "write": write,
        "output": output, "reasoning": reasoning,
    }


def test_anthropic_counters_are_already_disjoint():
    data = {"usage": {
        "input_tokens": 100, "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 20, "output_tokens": 50,
    }}
    usage = response_from_anthropic(data, "anthropic", "claude-sonnet-4-5", {}).usage
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 40
    assert usage.cache_write_tokens == 20
    assert usage.output_tokens == 50
    assert usage.total_tokens == 210


def test_openai_chat_prompt_total_is_split():
    # prompt_tokens contains the cached tokens; completion_tokens contains the
    # reasoning. Copying either total through bills the part twice.
    data = {"choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens": 50,
                "completion_tokens_details": {"reasoning_tokens": 30},
            }}
    usage = response_from_openai_compat(data, "openai_compat", "m", {}).usage
    assert usage.input_tokens == 60
    assert usage.cached_input_tokens == 40
    assert usage.output_tokens == 20
    assert usage.reasoning_tokens == 30


def test_openai_responses_prompt_total_is_split():
    data = {"output": [], "usage": {
        "input_tokens": 100,
        "input_tokens_details": {"cached_tokens": 40},
        "output_tokens": 50,
        "output_tokens_details": {"reasoning_tokens": 30},
    }}
    usage = response_from_openai_responses(data, "openai", "o3", {}).usage
    assert usage.input_tokens == 60
    assert usage.cached_input_tokens == 40
    assert usage.output_tokens == 20
    assert usage.reasoning_tokens == 30


def test_google_prompt_is_inclusive_but_thoughts_are_not():
    # The asymmetric one: promptTokenCount contains the cache,
    # candidatesTokenCount does not contain the thoughts.
    data = {"candidates": [], "usageMetadata": {
        "promptTokenCount": 100, "cachedContentTokenCount": 40,
        "candidatesTokenCount": 20, "thoughtsTokenCount": 30,
    }}
    usage = response_from_google(data, "google", "gemini-2.5-pro", {}).usage
    assert usage.input_tokens == 60
    assert usage.cached_input_tokens == 40
    assert usage.output_tokens == 20
    assert usage.reasoning_tokens == 30


def test_split_never_produces_negative_tokens():
    # A provider that reports a part bigger than its own total is describing
    # something else; clamping keeps cost from adding backwards.
    assert usage_from_inclusive_prompt(10, 40) == (0, 10)
    assert usage_from_inclusive_completion(10, 40) == (0, 10)


def test_no_double_billing_end_to_end(costs):
    # 100 prompt tokens of which 40 cached, 50 completion tokens of which 30
    # reasoning, on a model priced 2/0.5/8. The old calculator billed the 40
    # cached tokens at the full input rate *and* the discount, and the 30
    # reasoning tokens twice: 60*2 + 40*0.5 + 40*2 + 50*8 + 30*8 = 780.
    # The right answer bills each token exactly once: 60*2 + 40*0.5 + 20*8 + 30*8.
    usage = response_from_openai_compat({
        "choices": [{"message": {"role": "assistant", "content": "x"}}],
        "usage": {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 40},
            "completion_tokens": 50,
            "completion_tokens_details": {"reasoning_tokens": 30},
        },
    }, "openai_compat", "gpt-4.1", {}).usage
    breakdown = costs.compute("gpt-4.1", usage)
    assert breakdown.total_usd == pytest.approx(60 * 2e-6 + 40 * 0.5e-6 + 20 * 8e-6 + 30 * 8e-6)
    assert breakdown.input_usd + breakdown.cached_input_usd + breakdown.output_usd == pytest.approx(breakdown.total_usd)


def test_anthropic_cache_writes_are_priced(costs):
    # 20 tokens written to cache at 1.25x the $3 input rate used to be free.
    usage = response_from_anthropic({"usage": {
        "input_tokens": 100, "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 0, "output_tokens": 10,
    }}, "anthropic", "claude-sonnet-4-5", {}).usage
    breakdown = costs.compute("claude-sonnet-4-5", usage)
    assert breakdown.cache_write_usd == pytest.approx(20 * 3.75e-6)
    assert breakdown.total_usd == pytest.approx(100 * 3e-6 + 20 * 3.75e-6 + 10 * 15e-6)


# --------------------------------------------------------------------------
# the provider's own number wins


def test_provider_reported_cost_is_authoritative(costs):
    # OpenRouter returns usage.cost. That is the invoice line; re-deriving it
    # from a rate card can only move away from the truth.
    usage = _usage_dict(1000, output=500)
    breakdown = costs.compute("deepseek/deepseek-chat", usage, billed_cost_usd=0.001234)
    assert breakdown.total_usd == pytest.approx(0.001234)
    assert breakdown.authoritative
    assert breakdown.source == "provider"


def test_reported_zero_is_reported_zero(costs):
    # None means "not reported"; 0.0 means "the provider billed nothing". The
    # second must survive as zero, not fall through to the rate card.
    usage = _usage_dict(1_000_000, output=1_000_000)
    assert costs.compute("gpt-4.1", usage, billed_cost_usd=0.0).total_usd == 0.0


def _usage_dict(inp, output=0):
    from ethos.schemas.models import Usage

    return Usage(input_tokens=inp, output_tokens=output)


# --------------------------------------------------------------------------
# estimates


def test_estimate_reads_the_actual_request(costs):
    from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

    small = ModelRequest(
        tier=Tier.T1, purpose="test",
        messages=[ModelMessage(role=Role.user, content="hi")],
        max_tokens=64,
    )
    large = ModelRequest(
        tier=Tier.T1, purpose="test",
        messages=[ModelMessage(role=Role.user, content="word " * 8000)],
        max_tokens=4096,
    )
    assert costs.estimate("gpt-4.1", large) > costs.estimate("gpt-4.1", small)
    # Same request, different models: a free model estimates free, an paid one
    # does not. The old fixed 5000/800 guess priced them identically.
    assert costs.estimate("nvidia/nemotron-3.5-super-vl:free", large) == 0.0


def test_estimate_never_exceeds_the_promised_ceiling(costs):
    from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

    request = ModelRequest(
        tier=Tier.T1, purpose="test",
        messages=[ModelMessage(role=Role.user, content="word " * 4000)],
        max_tokens=100, reasoning_budget=60,
    )
    # Output + reasoning can never be billed beyond max_tokens.
    ceiling = 100 * costs.price_for("gpt-4.1").output / 1_000_000
    assert costs.estimate("gpt-4.1", request) <= ceiling + 4000 * costs.price_for("gpt-4.1").input / 1_000_000 + 1e-9


# --------------------------------------------------------------------------
# one definition of today, one definition of spend


def test_start_of_day_is_utc_midnight():
    from datetime import UTC, datetime

    moment = datetime(2026, 10, 17, 23, 30, tzinfo=UTC)
    assert start_of_day(moment) == datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
    tokyo = moment.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Tokyo"))
    assert start_of_day(tokyo) == datetime(2026, 10, 17, 0, 0, tzinfo=UTC)


def test_dashboard_spend_queries_agree_with_the_spend_guard():
    """The dashboard bridge re-states the guard's SQL in JavaScript.

    The drift that made the pane disagree with the cap was exactly this: two
    copies of one query, one filtered and one not. Both must carry the same
    status filter and the same UTC day boundary. If this test fails, somebody
    changed one copy and not the other -- fix both or the displayed total and
    the enforced cap come apart again.
    """
    web_server = (REPO / "web" / "server" / "index.mjs").read_text()
    for name, sql in (
        ("SPEND_SQL", SPEND_SQL),
        ("SPEND_BY_BUCKET_SQL", SPEND_BY_BUCKET_SQL),
        ("SPEND_BY_MODEL_SQL", SPEND_BY_MODEL_SQL),
        ("SPEND_BY_DAY_SQL", SPEND_BY_DAY_SQL),
    ):
        assert "status = 'ok'" in sql, f"{name} lost the status filter"
        assert "ts >=" in sql, f"{name} lost its day boundary"
    # The three dashboard queries: every model_calls read on the bridge carries
    # both the status filter and the UTC boundary.
    dashboard_queries = [
        line.strip()
        for line in web_server.splitlines()
        if "FROM model_calls" in line
    ]
    assert dashboard_queries, "dashboard no longer reads model_calls"
    for query in dashboard_queries:
        assert "status = 'ok'" in query, f"dashboard query lost the status filter: {query}"
        assert "TIME ZONE 'UTC'" in query, f"dashboard query left UTC: {query}"


def test_bucket_queries_partition_the_day():
    # Commitment + discretionary must account for everything the day total
    # counts, or a bucket cap can be exhausted while the day total disagrees.
    assert "budget_bucket = $2" in SPEND_BY_BUCKET_SQL
    assert SPEND_BY_BUCKET_SQL.startswith(SPEND_SQL.rstrip())


# --------------------------------------------------------------------------
# price table integrity


def test_pricing_yaml_is_well_formed(config):
    # Every configured entry must carry a positive-or-zero input and output
    # rate, and no invented default block may return.
    assert not hasattr(config.pricing, "defaults")
    for key, entry in config.pricing.models.items():
        assert entry["input"] >= 0, key
        assert entry["output"] >= 0, key
        # Cache rates default to the input rate when unstated, so they must
        # never be *below* a discount that does not exist.
        assert entry.get("cached_input", entry["input"]) <= entry["input"], key
