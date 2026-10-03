from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from ethos.config import load_config
from ethos.engine.rhythm import RhythmController
from ethos.gateway.embeddings import HashingEmbedder
from ethos.memory.consolidate import hdbscan_like_cluster
from ethos.memory.retrieval import (
    HybridRetriever,
    bm25_scores,
    cosine,
    mmr_select,
    normalize_bm25,
    recency_component,
    score_memory,
    tokenize,
)
from ethos.schemas.memory import MemoryHit


@pytest.fixture()
def memory_config():
    return load_config("config").memory


def test_tokenize():
    assert tokenize("Hello, World-2!") == ["hello", "world", "2"]


def test_cosine_identical_and_orthogonal():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([0.0], [0.0]) == 0.0


def test_bm25_ranks_relevant_doc_higher():
    query = ["ethos", "memory"]
    docs = [tokenize("ethos keeps its memory alive across restarts"),
            tokenize("the quick brown fox jumps over the lazy dog")]
    scores = bm25_scores(query, docs)
    assert scores[0] > scores[1]
    normalized = normalize_bm25(scores)
    assert normalized[0] == pytest.approx(1.0)


def test_recency_component_half_life(memory_config):
    now = datetime.now(UTC)
    fresh = recency_component(now - timedelta(hours=1), now, 168)
    old = recency_component(now - timedelta(hours=336), now, 168)
    assert fresh == pytest.approx(0.5 ** (1 / 168))
    assert old == pytest.approx(0.5 ** (336 / 168)) == pytest.approx(0.25)


def test_score_memory_exact_formula(memory_config):
    w = memory_config.retrieval.weights
    query_vec = [1.0, 0.0]
    embedding = [0.5, 0.5]
    now = datetime.now(UTC)
    total, components = score_memory(
        query_vec=query_vec,
        embedding=embedding,
        ts=now - timedelta(hours=168),
        importance=0.8,
        strength=0.6,
        weights=w,
        recency_half_life_h=168,
        bm25hat=0.5,
        now=now,
    )
    expected = (
        w.cosine * components["cosine"]
        + w.bm25 * 0.5
        + w.recency * 0.5
        + w.importance * 0.8
        + w.strength * 0.6
    )
    assert total == pytest.approx(expected)


def test_mmr_prefers_diverse_results(memory_config):
    embedder = HashingEmbedder(1024)
    near_a = MemoryHit(id="1", kind="episode", summary="alpha thing",
                       embedding=embedder.embed(["alpha thing"])[0], score=0.9)
    near_a2 = MemoryHit(id="2", kind="episode", summary="alpha thing",
                       embedding=embedder.embed(["alpha thing"])[0], score=0.85)
    different = MemoryHit(id="3", kind="episode", summary="zulu topic",
                          embedding=embedder.embed(["zulu topic"])[0], score=0.6)
    selected = mmr_select([near_a, near_a2, different], k=3, lambda_=0.7)
    ids = [h.id for h in selected]
    assert ids[0] == "1"
    assert "3" in ids
    assert ids.index("3") < 2 or near_a2.embedding == near_a.embedding


def test_mmr_lambda_one_is_pure_relevance():
    hits = [
        MemoryHit(id="a", kind="episode", summary="x", score=0.9, embedding=[1.0, 0.0]),
        MemoryHit(id="b", kind="episode", summary="y", score=0.5, embedding=[0.0, 1.0]),
    ]
    selected = mmr_select(hits, k=2, lambda_=1.0)
    assert [h.id for h in selected] == ["a", "b"]


def test_hdbscan_like_cluster_clean_clusters():
    def norm(v):
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]

    cluster_a = [norm([1, 0.01 * i, 0]) for i in range(4)]
    cluster_b = [norm([0, 1, 0.01 * i]) for i in range(4)]
    cluster_c = [norm([0.01 * i, 0, 1]) for i in range(4)]
    labels = hdbscan_like_cluster(cluster_a + cluster_b + cluster_c, min_cluster_size=3)
    assert len(set(labels[:4])) == 1
    assert len(set(labels[4:8])) == 1
    assert len(set(labels[8:])) == 1
    assert len(set(labels)) == 3


def test_hdbscan_noise_for_singleton_points():
    def norm(v):
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]

    labels = hdbscan_like_cluster([norm([1, 0, 0])], min_cluster_size=3)
    assert labels == [-1]


def test_decay_config_values(memory_config):
    assert memory_config.spacing.strength_boost == 0.3
    assert memory_config.spacing.half_life_multiplier == 1.5
    assert memory_config.decay.demote_strength == 0.05
    assert memory_config.decay.demote_importance == 0.3
    assert memory_config.decay.archive_after_days == 365
    assert memory_config.retrieval.mmr_lambda == 0.7


def test_retrieval_weights_sum(memory_config):
    w = memory_config.retrieval.weights
    assert w.cosine + w.bm25 + w.recency + w.importance + w.strength == pytest.approx(1.0)


class _FakeDB:
    async def fetch(self, *args, **kwargs):
        return []


async def test_retriever_no_rows(memory_config):
    retriever = HybridRetriever(_FakeDB(), HashingEmbedder(1024), memory_config)
    result = await retriever._candidates([0.1] * 1024, 10)
    assert result == []


# --------------------------------------------------------------------------
# The read is total


def _body_row(body: str):
    return {
        "id": "11111111-1111-1111-1111-111111111111", "kind": "episode",
        "summary": "a consolidation question", "body": body,
        "ts": datetime.now(UTC), "importance": 0.5, "strength": 0.5,
        "emb": "[0.1]", "source": "episode",
    }


def test_a_body_that_parses_to_something_that_is_not_an_object_is_still_a_memory():
    """One seeded episode held a bare JSON string, and it cost a whole afternoon.

    `episodes.body` is declared to hold an object and every writer puts one
    there, but a column can still hold a string. The trap was that
    `json.loads` on a JSON string *succeeds* — so the `except` around the parse
    caught every unreadable body and passed through precisely the one that
    mattered, a body that parsed cleanly and was not a dict. `MemoryHit` then
    rejected it, and because retrieval runs inside the goal loop on every step,
    that one row raised out of the whole life cycle 1,524 times over.

    The row's text is still worth retrieving. A memory is not discarded because
    its body is the wrong shape; the shape is normalised around it.
    """
    hit = HybridRetriever._row_to_hit(_body_row(
        '"Question auction (1-5):\\n1) What exact state transition distinguishes '
        'successful from failed consolidation?"'
    ))
    assert hit.body is not None
    assert "Question auction" in str(hit.body)
    assert hit.summary == "a consolidation question", "the memory itself survives"


@pytest.mark.parametrize("body,expected", [
    ('{"note": "an object"}', {"note": "an object"}),
    # A bare JSON array is a list, not the object the schema wants, so it is
    # carried under `items` rather than dropped.
    ("[1, 2, 3]", {"items": [1, 2, 3]}),
    # A JSON *string* parses to a string, which is the case that used to slip
    # past the `except` -- including one that merely looks like a number.
    ("42", {"raw": 42}),
    ('"42"', {"raw": "42"}),
    ('"not json at all"', {"raw": "not json at all"}),
    ("", None),
    (None, None),
])
def test_every_body_shape_becomes_a_usable_body(body, expected):
    """No shape of that column may raise, because every shape is in the table."""
    hit = HybridRetriever._row_to_hit(_body_row(body))
    assert hit.body == expected


def test_a_dict_body_is_passed_through_untouched():
    """The normal case must not be repackaged."""
    hit = HybridRetriever._row_to_hit(_body_row('{"plan": ["a", "b"]}'))
    assert hit.body == {"plan": ["a", "b"]}


def test_rhythm_energy_formula(config):
    config.timezone = "UTC"
    rhythm = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    energy = rhythm.energy(spent_discretionary_usd=2.5, now=now)
    budget = 20.0 * 0.5
    first = 1.0 - 2.5 / budget
    # 10:00 is the configured peak, so the circadian term is at its ceiling.
    assert energy == pytest.approx(min(first, 1.0))


def _at_the_peak(config):
    """A controller whose clock is pinned to the hour energy is highest.

    The rest-boost tests below are about the boost, not about the hour, and the
    circadian term made them depend on what time it was when the suite ran — a
    test that passes at ten in the morning and fails at four in the afternoon is
    a test of the clock, not of what it claims to check.
    """
    config.timezone = "UTC"
    at_peak = datetime(2026, 1, 1, int(config.rhythm.energy.peak_hour), 0, tzinfo=UTC)
    return RhythmController(config, wall_clock=lambda: at_peak)


def test_rhythm_energy_rest_boost(config):
    # Pinned for the same reason as the formula test above: energy now carries a
    # circadian term that depends on the hour it is read at, so a test with no
    # `now` asserts against whatever time of day it happens to run.
    config.timezone = "UTC"
    rhythm = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    rhythm.mark_awake()
    rhythm.add_rest(65.0)
    energy = rhythm.energy(
        spent_discretionary_usd=0.0,
        now=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
    )
    assert energy == pytest.approx(min(config.rhythm.energy.cap, 1.0 + 0.2))
    assert energy <= config.rhythm.energy.cap


def test_rhythm_energy_rest_boost_uncapped_contribution():
    from ethos.config import EthosConfig

    cfg = EthosConfig()
    cfg.rhythm.energy.cap = 2.0
    rhythm = _at_the_peak(cfg)
    rhythm.mark_awake()
    rhythm.add_rest(65.0)
    assert rhythm.energy(0.0) == pytest.approx(1.2)


def test_rhythm_energy_capped(config):
    rhythm = _at_the_peak(config)
    rhythm.add_rest(600)
    energy = rhythm.energy(0.0)
    assert energy <= config.rhythm.energy.cap


def test_the_small_hours_do_not_drain_the_agent(config):
    """4am is not a state the agent is less able to be woken in than noon.

    Energy used to be `1 - hours_awake/20`, a countdown to a bedtime. With no
    bedtime left, an agent up since morning reads as exhausted from the
    afternoon onward and stays that way — so a message at four in the morning
    met a loop that had stopped proposing anything. What replaced it is a
    circadian dip with a floor, and the floor is what this is about: the night is
    quieter, never absent.
    """
    config.timezone = "UTC"
    rhythm = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    night = rhythm.energy(spent_discretionary_usd=0.0, now=datetime(2026, 1, 1, 4, 0, tzinfo=UTC))
    noon = rhythm.energy(spent_discretionary_usd=0.0, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert night < noon, "the dip is a dip, not a flat line"
    assert night >= config.rhythm.energy.night_floor
    assert night > config.rhythm.energy.low_energy, \
        "the night alone must never put the agent below the rest threshold"
    assert not rhythm.should_rest(energy=night, max_utility=0.5)


def test_energy_does_not_decay_with_uptime(config):
    """The failure this replaces: energy at zero from the afternoon onwards, forever.

    Uptime cannot be what drains an agent that never sleeps, and this is the
    check that it does not — forty hours up is worth what one hour up is worth.
    """
    rhythm = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    rhythm.awake_since = datetime.now(UTC) - timedelta(hours=40)
    after_forty_hours = rhythm.energy(spent_discretionary_usd=0.0)

    fresh = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    just_awake = fresh.energy(spent_discretionary_usd=0.0)
    assert after_forty_hours == pytest.approx(just_awake)


def test_energy_is_the_same_hour_tomorrow(config):
    config.timezone = "UTC"
    rhythm = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    tonight = rhythm.energy(0.0, now=datetime(2026, 3, 1, 4, 0, tzinfo=UTC))
    tomorrow = rhythm.energy(0.0, now=datetime(2026, 3, 2, 4, 0, tzinfo=UTC))
    assert tonight == pytest.approx(tomorrow), "a daily rhythm, not a countdown"


def test_the_daily_job_is_keyed_to_a_clock_and_not_to_sleep(config):
    config.timezone = "UTC"
    before = RhythmController(
        config, wall_clock=lambda: datetime(2026, 1, 2, 3, 0, tzinfo=UTC))
    assert before.due_daily("04:00", None) is False, "not yet"

    after = RhythmController(
        config, wall_clock=lambda: datetime(2026, 1, 2, 5, 0, tzinfo=UTC))
    assert after.due_daily("04:00", None) is True, "once the hour arrives"
    assert after.due_daily("04:00", "2026-01-02") is False, "and not twice on one day"
    assert after.due_daily("04:00", "2026-01-01") is True, "and again the next day"


def test_the_daily_job_is_read_in_the_configured_timezone(config):
    """The hour a person wrote is an hour where they live.

    04:00 in a Tokyo configuration is four in the morning in Tokyo. Read against
    the UTC instant, a nightly job lands at one in the afternoon local — the one
    reading nobody writes a time of day intending.
    """
    config.timezone = "Asia/Tokyo"
    rhythm = RhythmController(
        config, wall_clock=lambda: datetime(2026, 1, 1, 18, 30, tzinfo=UTC))
    assert rhythm.local_time().hour == 3
    assert rhythm.due_daily("04:00", None) is False

    later = RhythmController(
        config, wall_clock=lambda: datetime(2026, 1, 1, 19, 30, tzinfo=UTC))
    assert later.local_time().hour == 4
    assert later.due_daily("04:00", None) is True


def test_rhythm_should_rest(config):
    rhythm = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    assert rhythm.should_rest(energy=0.2, max_utility=0.9)
    assert rhythm.should_rest(energy=0.9, max_utility=0.01)
    assert not rhythm.should_rest(energy=0.9, max_utility=0.9)


def test_mind_wander_gap_within_bounds(config):
    rhythm = RhythmController(config, wall_clock=lambda: datetime.now(UTC))
    for _ in range(20):
        gap = rhythm._draw_wander_gap_min()
        assert config.rhythm.mind_wandering.min_gap_min <= gap
        assert gap <= config.rhythm.mind_wandering.max_gap_min
