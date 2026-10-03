from __future__ import annotations

from typing import Any

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
                       ) -> None:
    MODEL_CALLS.labels(provider=provider, model=model, tier=tier,
                       purpose=purpose, status=status).inc()
    if status == "error" and scope:
        MODEL_FAILURES.labels(provider=provider, model=model, tier=tier,
                              purpose=purpose, scope=scope).inc()
    if cost_usd:
        MODEL_COST.labels(provider=provider, model=model, bucket=bucket).inc(cost_usd)
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


def set_presence(state: str, energy: float, salience: float, **_extra: Any) -> None:
    ENERGY.set(energy)
    SALIENCE_MAX.set(salience)
