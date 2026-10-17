from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from ethos.observability.logger import get_logger

logger = get_logger("ethos.metrics")

MODEL_CALLS = Counter(
    "ethos_model_calls_total",
    "Model calls by provider/tier/purpose/status",
    # `purpose` is what makes this counter answer the question the outage raised.
    # Without it, `ethos_model_calls_total{status="error", model="..."}` mixes
    # every kind of call the agent makes: the plan call, the step call, the
    # deliberation that writes somebody's reply. Those three fail for different
    # reasons, are retried by different levers, and are fixed by different people
    # -- and a loop that was re-sending an identical plan payload five hundred
    # times looked, on provider and model alone, exactly like a busy afternoon.
    #
    # `gsl.plan` against `gsl.step` is the distinction that matters for the
    # freeze: the first is a request the agent makes about its own work and can
    # be retried with a *different* request; the second is the work itself. One
    # label on this counter separates "planning is looping" from "the agent is
    # working".
    ["provider", "model", "tier", "purpose", "status"],
)
MODEL_FAILURES = Counter(
    "ethos_model_failures_total",
    "Failed model calls by scope, which is which lever moved",
    # `scope` is `request`, `model` or `provider`, and it is the label that says
    # whether a retry changed the request, excluded a model, or simply waited.
    # A dashboard reading one `error` counter cannot tell a configuration fault
    # (401s, one per request, forever) from a workload fault (truncations, many
    # per request, escalating) -- and those have opposite fixes.
    ["provider", "model", "tier", "purpose", "scope"],
)
MODEL_RETRIES = Counter(
    "ethos_model_retries_total",
    "Retries by scope and whether the request was changed",
    # `changed_request` is the assertion that the retry was not a replay. A retry
    # that moved no lever is the incident; this is the counter that would have
    # shown it happening.
    ["scope", "purpose", "changed_request", "changed_model"],
)
MODEL_COST = Counter(
    "ethos_model_cost_usd_total",
    "Accumulated model spend in USD",
    ["provider", "model", "bucket"],
)
# A free call has to leave a trace. `MODEL_COST.inc(0)` is a no-op on a
# Prometheus counter by construction -- the series is only created when the
# counter moves -- so a model the vendor bills at zero produced no
# `ethos_model_cost_usd_total` series whatsoever. That reads identically to a
# model that was never called, which is the specific confusion that made free
# traffic look like spend: there was nothing in the scrape to contradict it.
# Observing the per-call cost in a histogram creates the series at 0.0, so
# "free" and "unused" are distinguishable in a dashboard.
MODEL_CALL_COST = Histogram(
    "ethos_model_call_cost_usd",
    "Cost of a single model call in USD",
    ["provider", "model", "bucket"],
    buckets=(0.0, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0),
)
MODEL_COST_SOURCE = Counter(
    "ethos_model_cost_source_total",
    "Model calls by where the price came from",
    # `source` is the answer to "is this number real". `provider` means the
    # vendor billed it and we recorded what it said; `config` means a checked-in
    # rate card priced it; `unknown` means a high-water mark stood in because no
    # price could be found. The last of those is money spent at a rate nobody
    # chose, and it should be countable rather than a number to squint at.
    ["source", "provider"],
)
MODEL_LATENCY = Histogram(
    "ethos_model_latency_seconds",
    "Model call latency",
    ["provider"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 45, 90, 180),
)
ACTIONS = Counter(
    "ethos_actions_total",
    "Tool actions by tool/status",
    ["tool", "status"],
)
BUS_EVENTS = Counter(
    "ethos_bus_events_total",
    "Bus events published",
    ["kind"],
)
CYCLES = Counter(
    "ethos_cycles_total",
    "Life cycles by state",
    ["thread_id", "state"],
)
CYCLE_DURATION = Histogram(
    "ethos_cycle_duration_seconds",
    "Life cycle duration",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
SALIENCE_MAX = Gauge("ethos_salience_max", "Highest salience of current percepts")
ENERGY = Gauge("ethos_energy", "Current energy level")
GUARDIAN_DECISIONS = Counter(
    "ethos_guardian_decisions_total",
    "Guardian gate decisions",
    ["path", "verdict"],
)
THREADS_ACTIVE = Gauge("ethos_threads_active", "Currently running delegated threads")
THREADS_FINISHED_KEPT = Gauge(
    "ethos_threads_finished_kept",
    "Finished thread handles still resident in the bounded retention cache",
)

_started: list[int] = []


def start_metrics_server(port: int) -> None:
    """Serves the metrics endpoint, or says why it is not being served.

    Never raises. A metrics port that is already taken — by the last run of this
    process whose socket has not gone away yet, or by anything else on the
    machine — took the whole agent down at startup with a traceback, because the
    bind was on the way to starting the life loop. The agent's first invariant is
    that the process does not exist only while it wants to, and losing it to a
    scrap endpoint is exactly that. The counters keep being counted either way;
    only the port is missing.
    """
    if port in _started:
        return
    try:
        start_http_server(port)
    except OSError:
        logger.warning("metrics.port_unavailable", port=port)
        return
    _started.append(port)


def observe_model_call(provider: str, model: str, tier: str, status: str,
                       cost_usd: float, bucket: str, latency_s: float,
                       purpose: str = "unknown", scope: str | None = None,
                       cost_source: str = "unknown",
                       ) -> None:
    MODEL_CALLS.labels(provider=provider, model=model, tier=tier,
                       purpose=purpose, status=status).inc()
    if status == "error" and scope:
        MODEL_FAILURES.labels(provider=provider, model=model, tier=tier,
                              purpose=purpose, scope=scope).inc()
    if cost_usd:
        MODEL_COST.labels(provider=provider, model=model, bucket=bucket).inc(cost_usd)
    # Observed unconditionally, including at zero, so a free model has a series
    # saying so rather than none at all.
    MODEL_CALL_COST.labels(provider=provider, model=model, bucket=bucket).observe(max(0.0, cost_usd))
    MODEL_COST_SOURCE.labels(source=cost_source, provider=provider).inc()
    MODEL_LATENCY.labels(provider=provider).observe(latency_s)


def observe_retry(scope: str, purpose: str, *, changed_request: bool,
                  changed_model: bool) -> None:
    """One retry, labelled by which lever moved.

    Separate from `observe_model_call` because a retry is not a call: the call
    that failed is already counted, and counting this as one too would make a
    request that burned its whole budget look like six requests' worth of
    traffic. What this exists to answer is "when a gateway is retrying, is it
    doing anything different" -- and the answer for the incident that motivated
    it was no, six times.
    """
    MODEL_RETRIES.labels(scope=scope, purpose=purpose,
                         changed_request=str(bool(changed_request)).lower(),
                         changed_model=str(bool(changed_model)).lower()).inc()


def observe_action(tool: str, status: str) -> None:
    ACTIONS.labels(tool=tool, status=status).inc()


def observe_cycle(thread_id: str, state: str, duration_s: float) -> None:
    CYCLES.labels(thread_id=thread_id, state=state).inc()
    CYCLE_DURATION.observe(duration_s)
