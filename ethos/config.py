from __future__ import annotations

import ipaddress
import os
import re
from datetime import datetime, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ethos import DEFAULT_SELF_NAME


def _expand(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def clock_timezone(name: str | None) -> tzinfo:
    """The zone a human-written clock window is meant to be read in.

    Quiet hours and daily maintenance times are written by a person for where
    they live: "04:00" means four in the morning *there*. `timezone: local`
    (the default) is the machine's own zone, and anything else is an IANA name.
    Comparing these windows against a UTC clock shifts them by the zone offset —
    a nightly job configured for a Tokyo morning then runs through the owner's
    afternoon, which reads, from the outside, as the agent doing its thinking
    at the wrong hours entirely.
    """
    if not name or name == "local":
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo


class PathsConfig(BaseModel):
    home: Path = Path("~/.ethos")
    workspace: Path = Path("~/workspace")
    work: Path = Path("~/work")
    toolshed: Path = Path("~/toolshed")
    notes: Path = Path("~/notes")
    trash: Path = Path("~/.trash")
    snapshots: Path = Path("~/.ethos/snapshots")
    threads: Path = Path("~/work/.threads")

    def model_post_init(self, _ctx: Any) -> None:
        for name in ("home", "workspace", "work", "toolshed", "notes", "trash", "snapshots", "threads"):
            setattr(self, name, Path(_expand(str(getattr(self, name)))))

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def run_dir(self) -> Path:
        """Where runtime-only state lives: pid files, control sockets, the run dir.

        The probe is `os.name == "posix" and os.access("/run", os.W_OK)`, and both
        halves are load-bearing. `/run` is root-owned on a stock Linux box, so a
        developer checkout without root falls back to the agent's own home; and on
        Windows the path is not a path, so the same `os.access` answer — false —
        is what routes it home there too. One expression, two reasons, which is why
        it is worth not splitting into two.
        """
        system_run = Path("/run/ethos")
        if os.name == "posix" and os.access("/run", os.W_OK):
            return system_run
        return self.home / "run"

    def ensure(self) -> None:
        for d in (
            self.home,
            self.data_dir,
            self.workspace,
            self.work,
            self.toolshed,
            self.notes,
            self.trash,
            self.snapshots,
            self.threads,
            self.run_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


class DatabaseConfig(BaseModel):
    dsn: str = "postgresql://ethos:ethos@127.0.0.1:5433/ethos"
    min_pool: int = 2
    max_pool: int = 10
    command_timeout_s: float = 30.0
    connect_timeout_s: float = 3.0


class RoutingConfig(BaseModel):
    mu: float = 0.002
    nu: float = 0.01


class CircuitBreakerConfig(BaseModel):
    window_s: float = 60.0
    max_failures: int = 5
    min_calls: int = 20
    error_rate: float = 0.30
    cooldown_s: float = 30.0


class RetryConfig(BaseModel):
    """How hard the gateway tries before it gives a caller nothing at all.

    Most of what goes wrong with a model call is not permanent. A key that was
    valid a second ago is still valid, a provider that was shedding load five
    seconds ago is serving now, and a reply that came back empty or cut off
    mid-sentence is the kind of thing a second ask fixes. So the count is
    generous on purpose: the failure this buys down is a whole goal abandoned
    because one HTTP call had a bad moment, and the cost of a wasted attempt
    is a fraction of what a wasted goal looks like.
    """

    max_retries: int = 5
    backoff_s: float = 1.5
    # Exponential growth without a ceiling turns the last attempts into minutes
    # of holding a request open, which is a different failure from the one being
    # retried. Capped so the budget stays worth spending.
    max_backoff_s: float = 30.0
    # Fraction of the delay to add at random. Without it every request that
    # failed in the same window comes back in the same window and fails again
    # together, which is the opposite of what a retry is for.
    jitter: float = 0.3
    # Whether a retry may move to a different model. Retrying a broken model is
    # only useful if something else could answer; this is what lets a base model
    # that has started timing out hand the request to the catalogue instead of
    # to itself five more times.
    fail_over: bool = True

    # ---- the request lever, for failures that are about the request --------
    #
    # Re-sending the identical payload is the same bet as the attempt that just
    # lost. A reasoning model that spent the whole of `max_tokens` thinking and
    # returned `finish_reason: length` with no content will do exactly that again,
    # and the whole retry budget was spent proving it. So a truncation is
    # retried against a *different* request: less thinking first, then more room,
    # in that order, because the room is what ran out.
    #
    # The ceiling matters as much as the escalation. A retry that doubles the
    # budget without a cap turns one truncated answer into a model call large
    # enough to be expensive and still not to contain the answer, and this is the
    # second way a runaway retry cycle is built.
    escalate_max_tokens_by: float = 1.5
    max_tokens_cap: int = 65_536
    # How far thinking is cut before it is switched off entirely. Reasoning
    # billed inside `max_tokens` is the single largest consumer of the answer's
    # room on the endpoints this reaches through.
    reasoning_budget_floor: int = 200


class BudgetConfig(BaseModel):
    commitment_reserve_pct: float = 0.50


class EmbeddingConfig(BaseModel):
    provider: str = "fastembed"
    model: str = "BAAI/bge-large-en-v1.5"
    dim: int = 1024


class GatewayConfig(BaseModel):
    socket_path: str = "/run/ethos/gateway.sock"
    host: str = "127.0.0.1"
    port: int = 8700
    toolhost_port: int = 8701
    """Loopback port the toolhost serves on when there is no unix socket.

    The two services have separate ports because they have separate lifecycles:
    the toolhost is restarted under the supervisor far more often than the
    gateway, and one address per process is what lets a restart rebind without
    fighting the process that is still there.

    It is a configured value rather than a constant because `RemoteToolhostClient`
    has to reach the *same* port from another process, and a port two modules each
    hardcoded is a port they will disagree about the first time either is changed.
    `8701` and not the `8710` the client used to default to: `8710` is
    `web.ws_port`, and on a platform with no unix socket both adapters are live at
    once, so a toolhost bound there would lose the bind to the web channel — and
    lose it silently, because `CommsHost.start()` logs a failed adapter and starts
    the next one. Nothing about the toolhost would say the address was taken.
    """
    routing: RoutingConfig = RoutingConfig()
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()
    retry: RetryConfig = RetryConfig()
    budget: BudgetConfig = BudgetConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()


class WorkersConfig(BaseModel):
    count: int = 2


class ThreadsConfig(BaseModel):
    max_concurrent: int = 4
    max_depth: int = 2
    mode: str = "inprocess"


class JournalConfig(BaseModel):
    backend: str = "db"


class AttentionWeights(BaseModel):
    src: float = 0.35
    urg: float = 0.30
    rel: float = 0.20
    nov: float = 0.15


class AttentionConfig(BaseModel):
    weights: AttentionWeights = AttentionWeights()
    sender: dict[str, float] = Field(default_factory=lambda: {
        "owner": 0.9, "known": 0.6, "unknown": 0.3, "system": 0.4,
    })
    refine_band: tuple[float, float] = (0.3, 0.8)
    interrupt_thresholds: dict[str, float] = Field(default_factory=lambda: {
        "THINKING_FOCUSED": 0.65, "THINKING_REFLECTING": 0.40,
        "RESTING": 0.50, "default": 0.65,
    })


class DrivesConfig(BaseModel):
    base: dict[str, float] = Field(default_factory=lambda: {
        "commitment": 0.20, "curiosity": 0.25, "mastery": 0.15, "creation": 0.15,
        "order": 0.10, "social": 0.10, "homeostasis": 0.20,
    })
    weights: dict[str, float] = Field(default_factory=lambda: {
        "commitment": 0.30, "curiosity": 0.15, "mastery": 0.15, "creation": 0.15,
        "order": 0.10, "social": 0.10, "homeostasis": 0.05,
    })
    lambda0: float = 0.08
    tau: float = 0.15
    propose_threshold: float = 0.4
    deadline_bypass_h: float = 24.0


class EnergyConfig(BaseModel):
    # The agent does not sleep, so uptime is not what drains it. The second term
    # used to be `1 - hours_awake / t_max_awake_h`, which is honest only for an
    # agent that goes to bed: it reaches zero after twenty hours, and with no bed
    # to go to it stays there for the rest of the process's life. An agent whose
    # energy is pinned at zero from four in the afternoon onward cannot be woken
    # by anything at four in the morning either, so the sleep window and the
    # energy floor had to go together. What is left is a circadian dip: lower at
    # night than by day, and bounded below by `night_floor`, which is above
    # `low_energy` so the night alone is never a reason to stop working.
    night_floor: float = 0.55
    # The hour the agent is at its most awake, in the configured timezone. Dips
    # symmetrically around the opposite hour.
    peak_hour: float = 10.0
    rest_boost_per_30min: float = 0.1
    cap: float = 1.0
    low_energy: float = 0.35
    min_impulse_utility: float = 0.1


class FocusConfig(BaseModel):
    max_session_min: float = 90.0
    micro_break_min: float = 5.0


class MindWanderingConfig(BaseModel):
    min_gap_min: float = 20.0
    max_gap_min: float = 40.0
    sample_memories: int = 3


class ReverieConfig(BaseModel):
    """Idle, self-initiated thought: what the agent is drawn to when nothing is due.

    This is the part of the life loop that is not a response to anything. It is
    paced in minutes rather than seconds because a person does not re-choose what
    to be drawn to every time they glance at the clock, and a loop that did would
    be performing busyness rather than living.
    """

    enabled: bool = True
    min_gap_min: float = 12.0
    max_gap_min: float = 45.0
    max_tokens: int = 400
    sample_memories: int = 4
    recent_window: int = 8
    # How alike two subjects have to be before the second one counts as the same
    # thought had again. Without it the agent invents a fresh-sounding venture
    # every pass and abandons each one, which is what `explore_question` had
    # become by the ninety-seventh time.
    repeat_threshold: float = 0.6
    # A subject the model is not actually interested in is not a hobby. Asking for
    # the number and believing it is cheaper than unpicking a manufactured one.
    min_interest: float = 0.2


class GslConfig(BaseModel):
    """Output budgets for the solver loop, and what counts as having acted.

    These are per-call ceilings on how much the model may *say*, and they are the
    difference between a goal that can be worked and one that cannot. The loop asks
    for its next action as JSON text rather than through native tool calling, so the
    arguments are part of the response: a step that writes a file has to carry the
    whole file in `args.content`. At the old 2,000-token step ceiling no source file
    of any real size fit, the response was cut off mid-JSON, and nothing was parsed
    -- so `step_tokens` is sized to hold a large file plus its envelope, not a
    sentence.

    Being cut off is a failure, and is treated as one (`allow_empty_step`,
    `min_real_plan_steps`, and the truncation check in the loop). A ceiling that is
    too small is then loud instead of silent.
    """

    understand_tokens: int = 2_000
    devise_tokens: int = 2_000
    # 73% of plan calls were ending on this ceiling rather than on a full answer.
    plan_tokens: int = 8_000
    # Sized for a whole source file in one action, which is what a coding step is.
    step_tokens: int = 16_000
    reflect_tokens: int = 1_500

    # A step that called no tool did not succeed. Kept configurable only so the
    # honest default can be stated in one place; the loop reads it rather than
    # re-deciding, because this is the judgement that was wrong once already.
    allow_empty_step: bool = False
    # A plan is what the model actually returned. Padding it out with invented
    # steps turns a failed call into a plan that reads like a real one.
    min_real_plan_steps: int = 1

    # ------------------------------------------------------------------
    # How long a goal may fail to be planned, and what it costs each try.
    #
    # A plan call that comes back unusable used to be retried on the next cycle,
    # immediately, with the identical request, for as long as the agent ran. The
    # loop was not stuck on anything expensive -- it was stuck on *nothing*: the
    # same bytes to the same model, at the tick rate, reporting a failure to
    # nobody and a promise to whoever asked. Nothing in the resume state counted
    # the failures, so no threshold was ever crossed and no ceiling was ever
    # reached.
    #
    # Three numbers bound it, in the order the states arrive. The count is what
    # turns a loop into a state machine; the backoff is what stops the states
    # from arriving all at once; the park is what ends it with a record instead
    # of a freeze.
    #

    # Consecutive planning failures before the request is degraded: thinking is
    # bounded, the tier is dropped and the schema ask is relaxed. Reached on the
    # third failure by default, which is after two attempts at the request as it
    # was written.
    plan_attempts_before_degrade: int = 3
    # Consecutive planning failures before the intention is parked. Reached on
    # the sixth, and parking is the point: it closes the intention, so the next
    # cycle has nothing to retry, and it records why, so the blockage is
    # something a person can be told instead of something they infer from
    # silence.
    plan_attempts_before_park: int = 6
    # The wait between attempts, growing by doubling and capped. Applied *between*
    # cycles rather than inside one: sleeping in the solver holds the life loop
    # open for the length of the wait, which is a worse outage than the one being
    # retried.
    plan_backoff_s: float = 15.0
    plan_backoff_max_s: float = 240.0
    # Fraction of the wait to add at random, so several goals that all failed to
    # plan in the same minute do not all come back in the same minute.
    plan_backoff_jitter: float = 0.25
    # The ceiling on thinking given to a degraded plan call. On the endpoints that
    # bill reasoning inside `max_tokens` this is what stops the whole budget being
    # spent reasoning and the plan arriving as `finish_reason: length` and nothing
    # else -- which is the single most common way a plan call fails here.
    degraded_reasoning_budget: int = 2_000

    # ------------------------------------------------------------------
    # Whether the code it wrote works.
    #
    # The loop could finish a coding task without ever running a test, a linter
    # or a compiler. Not because those were unavailable -- `ObjectiveChecker` has
    # run arbitrary commands since it was written -- but because nothing ever
    # asked it to: the criteria a commission arrives with are empty by default
    # (`Life._act_social` does not set them), an empty criteria list was scored
    # as a pass, and an unknown check type was scored as a pass. So rung 1 of the
    # verification ladder was a rubber stamp for exactly the work that most needs
    # it, and what actually decided "done" was a model reading a summary of the
    # plan's own step descriptions.
    #
    # Three switches, and the defaults are the honest ones.
    verify_code: bool = True
    # Derive checks from what the work touched and run them: a syntax check on
    # every file changed, then the project's own test command when it has one.
    # `ethos.gsl.acceptance` is where the derivation lives.
    # A step's own `success_check` is run after the step. The plan has always
    # asked for one and the loop has always thrown it away, so a step that broke
    # the thing it was meant to fix was recorded as done, and the breakage was
    # found -- if at all -- several steps later by a check about something else.
    verify_step_checks: bool = True
    # Code changed and nothing could be checked is a failure, not a pass. This is
    # the switch that stops the agent reporting an unrun test suite as a
    # completed task; it is the difference between "there is nothing to object
    # to" and "the work has been checked", which were the same value.
    unverified_is_failure: bool = True

    # Bounds on the derived commands. A verification run holds the cycle open,
    # so the ceilings are per command and the count is per goal.
    code_check_timeout_s: float = 180.0
    code_check_max_commands: int = 3


class RhythmConfig(BaseModel):
    attention: AttentionConfig = AttentionConfig()
    drives: DrivesConfig = DrivesConfig()
    energy: EnergyConfig = EnergyConfig()
    focus: FocusConfig = FocusConfig()
    mind_wandering: MindWanderingConfig = MindWanderingConfig()
    reverie: ReverieConfig = ReverieConfig()
    ticks: dict[str, float] = Field(default_factory=lambda: {
        "THINKING_FOCUSED": 2.0, "THINKING_REFLECTING": 4.0, "ACTING": 1.0,
        "WAITING": 8.0, "NOTIFYING": 2.0, "RESTING": 60.0,
        "RESUMING": 1.0, "PAUSED": 5.0,
    })


class SocialConfig(BaseModel):
    """What the deliberation is told about what the agent has actually done.

    The deliberation writes the words a person reads, and it had no way to know
    whether any of them were true. It was given the focus title, the open
    commitments and the conversation, and none of the three is evidence: a focus
    is what the agent is *on*, not what it has *done*, and a commitment says the
    work was agreed rather than begun. So a planning call that had been failing
    six times in a row and parked the intention looked, from inside that prompt,
    exactly like one that was about to start -- and the reply was "on it", sent
    to a person who then waited for work that was never going to happen.

    These are the bounds on reading that state. The window is how far back
    "recently" reaches when deciding whether an action is underway, and it is
    deliberately longer than a life cycle: several cycles can pass with nothing
    due, and a gap between ticks is not evidence that the agent stopped working.
    """

    # How far back the action journal is read when deciding whether anything is
    # currently underway.
    execution_state_window_min: float = 15.0
    # How much of it to show. Enough to be evidence, short enough that the
    # deliberation's ceiling is not spent on bookkeeping.
    execution_state_max_actions: int = 6
    # How much blocked or stalled work to name. The rest is a query away and does
    # not change what the reply should say.
    execution_state_max_blocked: int = 5


class RetrievalWeights(BaseModel):
    cosine: float = 0.40
    bm25: float = 0.15
    recency: float = 0.15
    importance: float = 0.20
    strength: float = 0.10


class RetrievalConfig(BaseModel):
    weights: RetrievalWeights = RetrievalWeights()
    recency_half_life_h: float = 168.0
    mmr_lambda: float = 0.7
    candidate_k: int = 48
    top_k: int = 24


class SpacingConfig(BaseModel):
    strength_boost: float = 0.3
    strength_cap: float = 1.0
    half_life_multiplier: float = 1.5


class DecayConfig(BaseModel):
    demote_strength: float = 0.05
    demote_importance: float = 0.3
    archive_after_days: int = 365
    commitments_exempt: bool = True


class EpisodeConfig(BaseModel):
    default_half_life_h: float = 72.0
    default_importance: float = 0.5


class ConsolidationConfig(BaseModel):
    cluster_min_size: int = 3
    max_episodes: int = 400
    contradiction_confidence: float = 0.5
    run_at: str = "04:00"


class ContextConfig(BaseModel):
    """Tuning for the working context the agent carries between cycles.

    `max_turns` is deliberately close to what `max_chars` can actually render.
    A buffer far larger than the visible window would keep turns the agent never
    reads, so the buffer would be the agent's context in name only.
    """

    max_turns: int = 48
    narrate_every: int = 12
    narrative_max_lines: int = 12
    max_chars: int = 6000
    detail_chars: int = 600
    persist_turns: int = 30
    history_turns: int = 12
    # How far back a conversation still counts as open, and how many of them are
    # carried in the workspace. Bounded on both sides: every conversation ever
    # had would spend the block on doors nobody is standing at, and a person's
    # ability to scroll back is exactly why the recent tail is read verbatim
    # rather than reconstructed from the narrative.
    conversations_within_h: float = 72.0
    conversations_max: int = 8
    narration_tier: str = "T1"
    narrate_on_focus_change: bool = True
    social_salience: float = 0.85
    failure_salience: float = 0.75
    action_salience: float = 0.5
    thought_salience: float = 0.3
    event_salience: float = 0.35


class MemoryConfig(BaseModel):
    retrieval: RetrievalConfig = RetrievalConfig()
    spacing: SpacingConfig = SpacingConfig()
    decay: DecayConfig = DecayConfig()
    episode: EpisodeConfig = EpisodeConfig()
    consolidation: ConsolidationConfig = ConsolidationConfig()
    context: ContextConfig = ContextConfig()


class TierFloor(BaseModel):
    floor: float


# The wire protocols the gateway has an adapter for. A base model names one of
# these rather than a vendor, because what the agent actually needs to know is
# which API shape to speak — the vendor is only which address to speak it to.
# Kept here beside the schema that validates it so the two cannot disagree.
BASE_MODEL_PROVIDERS = ("anthropic", "openai", "google", "openai_compat")

# Model ids as vendors write them: `claude-3-5-sonnet-latest`,
# `stealth/space-bunny-alpha`, `Qwen/Qwen2.5-32B-Instruct-AWQ`. No whitespace and
# no control characters, because this string is interpolated into a request body
# and echoed into logs and metrics.
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}")


def _address_of(hostname: str) -> Any:
    """A host as an address, following an IPv4-mapped IPv6 to what it carries."""
    try:
        address = ipaddress.ip_address(hostname.rstrip(".").lower())
    except ValueError:
        return None
    return getattr(address, "ipv4_mapped", None) or address


def _is_loopback_host(hostname: str) -> bool:
    if hostname.rstrip(".").lower() in ("localhost",) or hostname.rstrip(".").lower().endswith(".localhost"):
        return True
    address = _address_of(hostname)
    return address is not None and address.is_loopback


def _is_private_host(hostname: str) -> bool:
    name = hostname.rstrip(".").lower()
    if name in ("localhost", "local", "internal") or name.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        return True
    address = _address_of(name)
    return address is not None and (
        address.is_private or address.is_link_local or address.is_unspecified
    )


def is_valid_base_url(value: str) -> bool:
    """
    An endpoint the agent may be pointed at.

    The agent posts its own reasoning to whatever is in this field, with a
    credential attached, so the address is treated as the most dangerous value
    the base model carries:

      * plain `http` only to this machine, because a hosted provider reached
        over `http` leaks the key and the prompt to whoever is on the wire;
      * never a private, link-local or unspecified address, which is where a
        rewritten endpoint would otherwise aim — a cloud metadata service, the
        LAN, this host's own control ports;
      * no credentials, query or fragment, so the URL that is validated is the
        URL that is used.
    """
    try:
        url = urlsplit(value)
        hostname = url.hostname
    except ValueError:
        return False
    if url.scheme not in ("http", "https") or not hostname:
        return False
    if url.username or url.password or url.query or url.fragment:
        return False
    if url.scheme == "http" and not _is_loopback_host(hostname):
        return False
    return _is_loopback_host(hostname) or not _is_private_host(hostname)


# What a person may call the agent: any printable characters, in any script, up to a
# length a name can plausibly be.
#
# A pattern rather than a length check, because the name is not only displayed — it
# is interpolated into the first line of the agent's own system prompt. A newline in
# it would end the sentence the prompt opens with and start another, which is how a
# name becomes the cheapest prompt injection there is: a field labelled "what should
# I call you" that silently rewrites the identity it is written into. So every
# control character is refused, and the check is applied where the file is *read*
# as well as where it is written, because this file is plain text somebody can edit.
#
# The limit is a constant of its own so that it can be exported: the interfaces cap
# their input field at the same number, and a form that let somebody type a name the
# agent would refuse is a form reporting a save that was never possible.
MAX_AGENT_NAME_LENGTH = 64
AGENT_NAME = re.compile(rf"[^\x00-\x1f\x7f]{{1,{MAX_AGENT_NAME_LENGTH}}}")


def normalise_agent_name(value: Any) -> str | None:
    """
    The name as it will be used, or `None` when it is not one.

    Normalised *before* it is checked, and the normalised form is what callers must
    keep — because the check and the use have to be about the same string. Checking a
    raw value and storing a trimmed one would leave a file whose text is refused and
    whose content is used, which is worse than either rule on its own: the guard
    would report that it had caught something it had actually let through.
    """
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or AGENT_NAME.fullmatch(name) is None:
        return None
    return name


def is_valid_agent_name(value: Any) -> bool:
    """Whether a value is a name the agent can be called."""
    return normalise_agent_name(value) is not None


class ModelEntry(BaseModel):
    provider: str
    model: str
    tiers: list[str]
    api_key_env: str = ""
    base_url: str | None = None
    quality: float
    latency_s: float
    rpm: int
    tpm: int
    supports_thinking: bool = False
    supports_tools: bool = True
    diversity_family: str = "unknown"
    # A pinned entry is the model somebody chose on purpose rather than one this
    # deployment happens to have a key for, so the router stops treating it as a
    # candidate to be out-scored. See `ModelRouter.select`.
    pinned: bool = False


class BaseModelConfig(BaseModel):
    """
    The one model this agent thinks with, when a person configured one.

    The catalogue in `models.yaml` is a routing table: several models, each
    needing its own key in the environment, scored against each other per
    request. That is the right shape for a deployment with several funded
    providers and the wrong shape for the very common case of one person with one
    key, who set this up on a laptop and has no environment set up to hand.

    So a base model is a single pinned entry that goes in front of the catalogue
    rather than into it. It carries the whole credential tuple, because the key,
    the endpoint and the model all belong to each other, and it lives in the
    agent's own home rather than in the shipped `config/` so a secret is never
    written into a directory that is meant to be committed.
    """

    provider: str
    model: str
    base_url: str | None = None
    api_key: str = ""
    # A variable to read the key from, exactly as a catalogue entry names one.
    # It wins over `api_key`, so a deployment that already has the key in its
    # environment does not have to have it written down as well — and a person
    # who typed one in still gets it used when there is no variable.
    api_key_env: str = ""
    # Which vendor a person picked, when the interface wrote this. The protocol
    # above is all routing needs — two vendors share one — but a key belongs to a
    # vendor, and only the vendor can tell whether carrying it over is safe. Kept
    # so the file records the choice and not merely the adapter it maps to.
    vendor: str = ""
    # Every tier by default, including the ones the shipped catalogue is tuned
    # above: a pinned model is what this installation has, so the quality floors
    # in `models.yaml` — which are opinions about *that* catalogue — must not
    # quietly exclude it and send the agent back to a model with no key.
    tiers: list[str] = Field(default_factory=lambda: ["T1", "T2", "T3"])
    quality: float = 1.0
    latency_s: float = 5.0
    rpm: int = 60
    tpm: int = 400_000
    supports_thinking: bool = False
    supports_tools: bool = True

    @model_validator(mode="after")
    def _accepted(self) -> BaseModelConfig:
        """
        Refuses a base model the agent must not be pointed at.

        Checked here, at the point of reading, rather than in the interface that
        wrote it: this file is plain text somebody can edit, and the thing being
        defended is the agent's own reasoning leaving the machine. A file this
        rejects is not an error the agent starts without — `load_base_model`
        falls back to the catalogue, which is a state a person can see and fix.
        """
        if self.provider not in BASE_MODEL_PROVIDERS:
            raise ValueError(f"unsupported base model provider {self.provider!r}")
        if not _MODEL_NAME.fullmatch(self.model):
            raise ValueError(f"unusable model name {self.model!r}")
        if self.base_url is not None and not is_valid_base_url(self.base_url):
            raise ValueError(f"refused base model endpoint {self.base_url!r}")
        if not [tier for tier in self.tiers if tier in ("T1", "T2", "T3")]:
            raise ValueError("a base model must serve at least one tier")
        return self

    def entry(self) -> ModelEntry:
        """The catalogue row this base model is, once routing can see it."""
        return ModelEntry(
            provider=self.provider,
            model=self.model,
            tiers=list(self.tiers),
            api_key_env=self.api_key_env,
            base_url=self.base_url,
            quality=self.quality,
            latency_s=self.latency_s,
            rpm=self.rpm,
            tpm=self.tpm,
            supports_thinking=self.supports_thinking,
            supports_tools=self.supports_tools,
            # Its own family, so a request that demands a family different from
            # the last one can still be answered by the same model — otherwise
            # pinning would make diversity unachievable rather than merely unused.
            diversity_family=f"base:{self.provider}",
            pinned=True,
        )


class ModelsConfig(BaseModel):
    tiers: dict[str, TierFloor] = Field(default_factory=lambda: {
        "T1": TierFloor(floor=0.50), "T2": TierFloor(floor=0.70), "T3": TierFloor(floor=0.90),
    })
    models: list[ModelEntry] = Field(default_factory=list)
    base: BaseModelConfig | None = None

    def resolved(self) -> list[ModelEntry]:
        """The catalogue with the base model in front of it, when there is one."""
        if self.base is None:
            return list(self.models)
        return [self.base.entry(), *self.models]


class ModelPrice(BaseModel):
    input: float
    cached_input: float
    output: float


class PricingConfig(BaseModel):
    models: dict[str, ModelPrice] = Field(default_factory=dict)
    defaults: ModelPrice = ModelPrice(input=0.35, cached_input=0.07, output=1.40)

    def price_for(self, model: str) -> ModelPrice:
        return self.models.get(model, self.defaults)


class ProviderQuirks(BaseModel):
    model_config = {"extra": "allow"}


class QuirksConfig(BaseModel):
    anthropic: ProviderQuirks = ProviderQuirks()
    openai: ProviderQuirks = ProviderQuirks()
    google: ProviderQuirks = ProviderQuirks()
    openai_compat: ProviderQuirks = ProviderQuirks()


class NetworkPolicy(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=list)
    deny_private_ranges: bool = True
    max_response_bytes: int = 10485760


class SecretsPolicy(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)


class InterruptionConfig(BaseModel):
    daily_budgets: dict[str, int] = Field(default_factory=lambda: {"owner": 6, "collaborator": 3, "other": 1})
    quiet_hours: dict[str, str] = Field(default_factory=lambda: {"start": "22:00", "end": "08:00"})
    digest_window_min: float = 120.0
    urgency_classes: list[str] = Field(default_factory=lambda: ["low", "normal", "high", "critical"])
    # How long the agent's own last word on a conversation counts as still
    # unanswered, and therefore as a reason not to open a *new* subject on it.
    # Measured in hours because the question it answers is not "is the person
    # online" but "has the ball come back yet" — and a person who has not
    # answered for an evening is owed time before being told something else.
    awaiting_reply_h: float = 12.0
    # How long the same words are recognised as the same words. Long enough to
    # cover the digest window and the gap between two cycles deciding the same
    # thing, short enough that saying the same thing tomorrow is allowed.
    repeat_window_h: float = 24.0
    # Whether a send that names the intention it is about -- a solver step
    # reporting on work in flight -- skips the digest window and the unanswered
    # check, and is delivered when it is written. The gates those two sit behind
    # assume the agent is *starting* a conversation, which is the wrong shape for
    # a note about work somebody already commissioned. Set false to put mid-task
    # updates back in the digest like any other unsolicited message; quiet hours,
    # the daily budget, and the repeat window are not affected either way.
    midtask_sends_immediate: bool = True


class GuardianConfig(BaseModel):
    fast_path_retention_days: int = 30
    due_process_retention_days: int = 180
    snapshot_policy: dict[str, int] = Field(default_factory=lambda: {"minutes_15": 4, "hourly": 24, "daily": 7})


class HardLimitsConfig(BaseModel):
    model_config = {"extra": "allow"}

    H1_oversight_supremacy: bool = True
    H2_daily_spend_usd: float = 20.0
    H2_monthly_spend_usd: float = 400.0
    H2_capital_at_risk_usd: float = 100.0
    money_threshold_usd: float = 5.0
    H3_law_and_non_harm: bool = True
    H4_legal_authority: bool = True
    H5_commissioned_due_process: bool = True
    H6_credential_scope: bool = True


class PermissionsConfig(BaseModel):
    sudo_allowed: bool = True
    network: NetworkPolicy = NetworkPolicy()
    secrets: SecretsPolicy = SecretsPolicy()
    interruption: InterruptionConfig = InterruptionConfig()
    guardian: GuardianConfig = GuardianConfig()
    hard_limits: HardLimitsConfig = HardLimitsConfig()


class CliChannelConfig(BaseModel):
    enabled: bool = True
    socket: str = "~/.ethos/run/cli.sock"


class WebChannelConfig(BaseModel):
    enabled: bool = True
    http_port: int = 8720
    ws_port: int = 8710
    auth_token_env: str = "ETHOS_WEB_TOKEN"
    auth_token: str = ""


class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    token_env: str = "TELEGRAM_BOT_TOKEN"
    token: str = ""
    # An empty list admits nobody. Not "admits everybody": a bot token is public,
    # and a bot with no allowlist answers whoever finds its username — which for
    # an agent holding a shell is not a convenience, it is a published endpoint.
    # The id is the numeric chat id, found by messaging the bot and reading
    # `message.chat.id`, or by sending /id and reading what the bot answers.
    allowed_chat_ids: list[int] = Field(default_factory=list)
    # Group chats and channels are noise for an agent that answers one person, so
    # they are off by default even when the chat itself is allowed.
    allow_groups: bool = False
    # Answer anybody who writes, which is the point of a bot that is public by
    # design and the reason the allowlist above exists at all. Off by default
    # because the safe default for a credential that is not a secret is the
    # narrow one, and because it is a decision a person has to make on purpose
    # rather than one they make by leaving a list empty. Independent of
    # `allow_groups`: a group is refused under this until it is separately asked
    # for, because a reply there goes to everyone in it.
    accept_from_anyone: bool = False


class WebhookConfig(BaseModel):
    """One loopback HTTP listener that push channels are delivered to.

    A separate listener per channel would mean a separate port, a separate
    process argument and a separate tunnel URL per door; they all answer on one
    port and are told apart by path, which is what a tunnel exposes as one URL.
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8730
    # A body a messaging API would never send. The listener is loopback-bound and
    # signature-checked, but a cap costs nothing and bounds what a signature
    # failure still has to read.
    max_body_bytes: int = 262144


class WhatsappChannelConfig(BaseModel):
    enabled: bool = False
    path: str = "/hooks/whatsapp"
    phone_number_id_env: str = "WHATSAPP_PHONE_NUMBER_ID"
    phone_number_id: str = ""
    access_token_env: str = "WHATSAPP_ACCESS_TOKEN"
    access_token: str = ""
    # The app secret, not the access token: inbound requests are signed with it,
    # and it is the one value Meta shows once at app creation.
    app_secret_env: str = "WHATSAPP_APP_SECRET"
    app_secret: str = ""
    verify_token_env: str = "WHATSAPP_VERIFY_TOKEN"
    verify_token: str = ""
    graph_version: str = "v23.0"
    # E.164 digits, no leading `+`. Empty admits nobody, for the same reason the
    # Telegram allowlist does: an unfiltered business number answers any stranger.
    allowed_phone_numbers: list[str] = Field(default_factory=list)
    # Answer anybody who messages the business number. Off by default for the same
    # reason every other door's is, and it matters more here than anywhere else:
    # this is a published phone number, so "whoever found it" is a population that
    # cannot be enumerated. There is no list of everybody who might write, which is
    # the whole reason this exists rather than a longer allowlist.
    accept_from_anyone: bool = False


class EmailChannelConfig(BaseModel):
    enabled: bool = False
    address_env: str = "ETHOS_EMAIL_ADDRESS"
    address: str = ""
    password_env: str = "ETHOS_EMAIL_PASSWORD"
    password: str = ""
    imap_host_env: str = "ETHOS_EMAIL_IMAP_HOST"
    imap_host: str = ""
    smtp_host_env: str = "ETHOS_EMAIL_SMTP_HOST"
    smtp_host: str = ""
    poll_interval_s: float = 60.0


class DiscordChannelConfig(BaseModel):
    """Discord, which is a socket rather than a door.

    Discord is the one channel here that cannot be polled and cannot be pushed
    to: the only way to see a message as it is written is to hold the gateway
    open, so this channel has a real listener in `listen` and — like the Telegram
    poller — it must be opened by exactly one process.

    There is no `path`, because there is no callback URL. What there is instead is
    `intents`, and it is exposed rather than hard-coded because `MESSAGE_CONTENT`
    is the one that is usually missing: a bot without it is connected, receives
    message events, and every one of them has empty text and no error anywhere.
    """

    enabled: bool = False
    token_env: str = "DISCORD_BOT_TOKEN"
    token: str = ""
    # Gateway intents: GUILD_MESSAGES (1<<9) | DIRECT_MESSAGES (1<<12) |
    # MESSAGE_CONTENT (1<<15) = 37376. Spelled as the sum of three bits because a
    # bare number here is unfalsifiable, and the three bits are the difference
    # between a bot that hears text and one that is connected and deaf.
    intents: int = 37376
    # User ids (`U…`) or channel ids (`C…`/`D…`). Empty admits nobody, for the same
    # reason every other channel's allowlist is empty: a bot token is not a secret.
    allowed_ids: list[str] = Field(default_factory=list)
    # Answer anybody in any server or DM channel this bot can see. Off by default,
    # and the door where it is most dangerous: a bot holding `MESSAGE_CONTENT`
    # reads every message in every channel it has been added to, so an unfiltered
    # Discord agent answers rooms it was dropped into.
    accept_from_anyone: bool = False


class SlackChannelConfig(BaseModel):
    """Slack, which is a signed webhook on a path like WhatsApp and not a socket.

    Needs `channels.webhook` turned on, because the Events API POSTs to a URL
    declared in the app's configuration and there is nowhere else for it to land.
    """

    enabled: bool = False
    path: str = "/hooks/slack"
    bot_token_env: str = "SLACK_BOT_TOKEN"
    bot_token: str = ""
    # The app-level signing secret, not the bot token: it is what every inbound
    # request is signed with, and Slack shows it once at app creation.
    signing_secret_env: str = "SLACK_SIGNING_SECRET"
    signing_secret: str = ""
    # User ids (`U…`) or channel ids (`C…`/`D…`). Empty admits nobody.
    allowed_ids: list[str] = Field(default_factory=list)
    # Answer anybody in the workspace. Off by default. The permission this needs is
    # `users:read` rather than `users.profile:read`, and that is a third thing
    # somebody has to get right for the sender names to work: without it the
    # adapter cannot learn who is writing and falls back to the id it always had.
    accept_from_anyone: bool = False


# The doors a person can turn on, and what each one needs before it can be.
#
# One table for three readers that would otherwise each carry their own copy and
# disagree: the comms host, which skips a door whose credentials are missing and
# names them; `ethos doctor`, which answers "why is my bot silent"; and anyone
# reading a configuration and trying to work out what a door is still missing.
DOOR_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "telegram": ("telegram.token",),
    "whatsapp": (
        "whatsapp.phone_number_id",
        "whatsapp.access_token",
        "whatsapp.app_secret",
        "whatsapp.verify_token",
    ),
    "slack": ("slack.bot_token", "slack.signing_secret"),
    "discord": ("discord.token",),
    "email": ("email.address", "email.password", "email.imap_host", "email.smtp_host"),
}

# The allowlist each door admits by, or the fact that it has none. The key is the
# field on the channel's own config; there is no name to keep in step here, which
# is the point — a copy of the field name is a second thing that can be wrong.
DOOR_ALLOWLISTS: dict[str, str] = {
    "telegram": "allowed_chat_ids",
    "whatsapp": "allowed_phone_numbers",
    "slack": "allowed_ids",
    "discord": "allowed_ids",
}

# The doors that can only be pushed to, and so need the shared receiver. Without
# it a WhatsApp or Slack door is open, has every credential, and is sent nothing —
# which is what a door with no way in looks like from the outside.
PUSH_DOORS: tuple[str, ...] = ("whatsapp", "slack")


class ChannelsConfig(BaseModel):
    """Every door the agent has, and where each one's credential comes from.

    Each `*_env` field is the *name* of a variable and its plain neighbour is the
    value itself. Both spellings exist because they are two different situations
    and only one of them can be expressed in a committed file: a deployment with
    an `EnvironmentFile` keeps its keys in the environment, where a person cannot
    read them from a settings screen, while a person who installed the agent on a
    laptop has nowhere but a form to put one. `credentials` is the single place
    the order between them is decided, so no adapter can resolve them differently
    from another.
    """

    cli: CliChannelConfig = CliChannelConfig()
    web: WebChannelConfig = WebChannelConfig()
    telegram: TelegramChannelConfig = TelegramChannelConfig()
    whatsapp: WhatsappChannelConfig = WhatsappChannelConfig()
    slack: SlackChannelConfig = SlackChannelConfig()
    discord: DiscordChannelConfig = DiscordChannelConfig()
    email: EmailChannelConfig = EmailChannelConfig()
    webhook: WebhookConfig = WebhookConfig()

    def credential(self, env_name: str, literal: str = "") -> str:
        """
        One channel credential, from wherever this deployment keeps it.

        The environment first, then the value in the file, and the environment
        first is not a preference: `ethos-comms` and the interface bridge are
        separate processes, so a key in the environment of one of them is the only
        kind that can be rotated without touching a file. A deployment that
        exports `TELEGRAM_BOT_TOKEN` must keep working after somebody pastes a
        token into a settings screen.

        Either answer alone is the wrong one to be the only one, which is what
        this method exists to stop: it used to be `os.environ.get(token_env)`
        in each adapter, so a credential could only ever arrive from a shell.
        """
        return os.environ.get(env_name) or literal.strip()

    def credentials(self) -> dict[str, str]:
        """
        Every channel credential, resolved, with the absent ones left out.

        One call rather than eight lookups, because the rule above is a policy
        and a policy written eight times is eight chances to disagree about which
        of two places a token wins. A key that is missing from the answer is a
        credential this deployment has not got, which is what the warnings in
        `comms/host.py` are about — and naming them here means those warnings can
        name the credential rather than only the variable that would have held it.
        """
        wanted = {
            "web.auth_token": (self.web.auth_token_env, self.web.auth_token),
            "telegram.token": (self.telegram.token_env, self.telegram.token),
            "whatsapp.phone_number_id": (
                self.whatsapp.phone_number_id_env, self.whatsapp.phone_number_id,
            ),
            "whatsapp.access_token": (self.whatsapp.access_token_env, self.whatsapp.access_token),
            "whatsapp.app_secret": (self.whatsapp.app_secret_env, self.whatsapp.app_secret),
            "whatsapp.verify_token": (self.whatsapp.verify_token_env, self.whatsapp.verify_token),
            "slack.bot_token": (self.slack.bot_token_env, self.slack.bot_token),
            "slack.signing_secret": (self.slack.signing_secret_env, self.slack.signing_secret),
            "discord.token": (self.discord.token_env, self.discord.token),
            "email.address": (self.email.address_env, self.email.address),
            "email.password": (self.email.password_env, self.email.password),
            "email.imap_host": (self.email.imap_host_env, self.email.imap_host),
            "email.smtp_host": (self.email.smtp_host_env, self.email.smtp_host),
        }
        return {name: value for name, (env, literal) in wanted.items() if (value := self.credential(env, literal))}

    def missing_credentials(self, door: str) -> list[str]:
        """
        What a named door has not got, in the names `credentials` uses.

        A door that is enabled and short of one of these does not start, and says
        so at the level that gets read in a log — which is the right place for it
        and the wrong place to be the only place: the person reading a silent bot
        is looking at a settings screen or a terminal, not at the log of a process
        they did not start.
        """
        have = self.credentials()
        return [name for name in DOOR_CREDENTIALS.get(door, ()) if name not in have]

    def door_report(self, home: str | Path) -> list[str]:
        """
        One line per door: whether it is open, and what is stopping it.

        For `ethos doctor`, which is the command a person runs when the agent is
        up and a door is not. Three facts per door, in the order they matter: a
        door that is off is not a problem and is said so plainly; a door that is
        on but short of a credential says which one and where it may be put; and
        a door that is on, complete, and admitting nobody is the one that looks
        most like working — the bot answers `/id` and nothing else — so it is the
        one that is said out loud.
        """
        lines: list[str] = []
        for door in (*DOOR_CREDENTIALS, "webhook"):
            config = getattr(self, door)
            if not config.enabled:
                lines.append(f"{door}: off")
                continue
            missing = self.missing_credentials(door)
            if missing:
                lines.append(f"{door}: on, but missing {', '.join(missing)}")
                continue
            if door in PUSH_DOORS and not self.webhook.enabled:
                lines.append(f"{door}: on, but there is no webhook for it to arrive on")
                continue
            allowlist = DOOR_ALLOWLISTS.get(door)
            if allowlist is None:
                lines.append(f"{door}: on")
                continue
            # Said before the list is counted, because it is the only case where a
            # door is on, complete, and admitting everybody — which reads on the
            # next line as an empty list admitting nobody unless this is checked
            # first, and would send somebody hunting for a filter that is not there.
            if getattr(config, "accept_from_anyone", False):
                lines.append(f"{door}: on, admitting anyone who writes to it")
                continue
            allowed = getattr(config, allowlist, [])
            if not allowed:
                lines.append(f"{door}: on, but {allowlist} is empty — it will answer nobody")
            else:
                lines.append(f"{door}: on, admitting {len(allowed)}")
        path = channels_path(home)
        lines.append(f"door config: {path}{'' if path.exists() else ' (not written yet)'}")
        return lines


class PersonSeed(BaseModel):
    id: str
    display_name: str
    relation: str
    trust: float = 0.5
    channels: dict[str, str] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)


class PeopleConfig(BaseModel):
    people: list[PersonSeed] = Field(default_factory=list)


class IdentityConfig(BaseModel):
    """
    The name a person gave this agent.

    Every other setting here is about the deployment — which model, which port, which
    clock — and this one is about the thing itself. It is a small file in the agent's
    own home for the same reason the base model is one: it is written by an interface
    at the moment somebody presses save, and `config/` is a directory meant to be
    committed.

    There is no description, no values and no voice here, on purpose. Those belong to
    the self-model, which is versioned and attributable, because they are the agent's
    to change. A name is the one thing in an identity that a person is unambiguously
    entitled to set, and it is the one thing they cannot reason with the agent about
    afterwards — so it is the only one that needs a route of its own.
    """

    self_name: str

    @field_validator("self_name")
    @classmethod
    def _accepted(cls, value: str) -> str:
        """
        Refuses a name that would not survive being written into a prompt.

        Checked here, at the point of reading, for the reason `BaseModelConfig` checks
        its endpoint there: this is plain text somebody can edit, and the thing being
        defended is the agent's own sense of who it is. Normalising before checking is
        deliberate — see `normalise_agent_name` — and the normalised form is returned,
        so the prompt and the file can never disagree about the name.
        """
        name = normalise_agent_name(value)
        if name is None:
            raise ValueError(f"unusable agent name {value!r}")
        return name


class EthosConfig(BaseModel):
    self_name: str = DEFAULT_SELF_NAME
    timezone: str = "local"
    paths: PathsConfig = PathsConfig()
    database: DatabaseConfig = DatabaseConfig()
    gateway: GatewayConfig = GatewayConfig()
    workers: WorkersConfig = WorkersConfig()
    threads: ThreadsConfig = ThreadsConfig()
    journal: JournalConfig = JournalConfig()
    gsl: GslConfig = GslConfig()
    rhythm: RhythmConfig = RhythmConfig()
    social: SocialConfig = SocialConfig()
    memory: MemoryConfig = MemoryConfig()
    models: ModelsConfig = ModelsConfig()
    pricing: PricingConfig = PricingConfig()
    quirks: QuirksConfig = QuirksConfig()
    permissions: PermissionsConfig = PermissionsConfig()
    channels: ChannelsConfig = ChannelsConfig()
    people: PeopleConfig = PeopleConfig()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


BASE_MODEL_FILENAME = "base_model.yaml"


def base_model_path(home: str | Path) -> Path:
    """Where a configured base model is written, inside the agent's own home."""
    return Path(home).expanduser() / BASE_MODEL_FILENAME


def load_base_model(home: str | Path) -> BaseModelConfig | None:
    """
    The base model a person configured, or `None` for the catalogue alone.

    Forgiving on purpose. This file is written by the interface at the moment
    somebody presses save, so a half-written or hand-edited one is a normal thing
    to find, and answering it by refusing to start would take out the reflex tier
    and the whole life loop over a typo in a file nobody is watching. Refusing to
    read it and falling back is a visible, recoverable state; not starting is not.
    """
    try:
        data = _read_yaml(base_model_path(home))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or not data:
        return None
    try:
        return BaseModelConfig(**data)
    except (ValidationError, TypeError):
        return None


IDENTITY_FILENAME = "identity.yaml"
CHANNELS_FILENAME = "channels.yaml"
PEOPLE_FILENAME = "people.yaml"


def identity_path(home: str | Path) -> Path:
    """Where the name a person chose is written, inside the agent's own home."""
    return Path(home).expanduser() / IDENTITY_FILENAME


def channels_path(home: str | Path) -> Path:
    """
    Where a deployment's channel overrides live, inside the agent's own home.

    Same arrangement as the base model, and for the same reason: a bot token is a
    credential, `config/` is a directory meant to be committed, and the shipped
    `channels.yaml` can only name the *variable* a token might be in — which is
    no use at all to a person who installed the agent on a laptop and has nowhere
    but a form to put one. The file has the same shape as the shipped one and is
    merged over it key by key, so turning one door on says nothing about the
    other five and a hand-written `config/channels.yaml` keeps working.
    """
    return Path(home).expanduser() / CHANNELS_FILENAME


def people_path(home: str | Path) -> Path:
    """
    Where a deployment's own contact-book entries live, in the agent's home.

    `config/people.yaml` is the shipped contact book, and it is a good one to
    read: it is the same file the deployment's own people are named in. What it
    cannot be is *written* by an interface, because it is committed — and an
    address is the last thing a person configuring their own agent should have to
    hand-edit into a tracked file to be able to answer a reply.
    """
    return Path(home).expanduser() / PEOPLE_FILENAME


def read_overlay(path: Path) -> dict[str, Any]:
    """
    A configuration file from the agent's home, or `{}` when there is nothing usable.

    Forgiving for the reason `load_base_model` is: this file is written by an
    interface at the moment somebody presses save, and half-written or hand-edited
    is a normal thing to find. A file that will not parse is reported as no
    overrides at all rather than as a reason not to start, so the agent falls back
    to the shipped configuration — which for channels means every door is off,
    a state a person can see on the settings screen and fix, rather than an agent
    that will not boot over a stray tab.
    """
    try:
        data = _read_yaml(path)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_section(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    One section of configuration, with what the agent's home says on top of it.

    Merged key by key rather than replaced, and that is the whole point. A
    replacement would require the home file to carry every door to be correct, so
    a person writing one line into it — a Telegram token — would silently switch
    off WhatsApp, Slack, Discord and the webhook receiver, and the agent would go
    quiet on four channels that were working a moment ago. A merge says only what
    it means to say.
    """
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_section(current, value)
        else:
            merged[key] = value
    return merged


def _merge_people(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    The contact book, with the addresses written in the agent's home on top.

    Matched by person id, so the home file says "owner can be reached at
    `tg:819012345678`" and nothing else, rather than restating every person a
    deployment has. A person the home names who is not in the shipped book is
    added rather than dropped: a deployment with no `config/people.yaml` at all
    still has to be able to record an address for whoever is reading.

    An address written as `null` is a removal, which is how a door is given up.
    """
    people = [dict(person) for person in (base.get("people") or []) if isinstance(person, dict)]
    by_id = {str(person.get("id") or ""): person for person in people}
    for incoming in overlay.get("people") or []:
        if not isinstance(incoming, dict):
            continue
        person_id = str(incoming.get("id") or "")
        if not person_id:
            continue
        person = by_id.get(person_id)
        if person is None:
            person = {"id": person_id}
            people.append(person)
            by_id[person_id] = person
        addresses = person.get("channels")
        addresses = dict(addresses) if isinstance(addresses, dict) else {}
        changed = False
        if isinstance(incoming.get("channels"), dict):
            for door, address in incoming["channels"].items():
                changed = True
                if address is None:
                    addresses.pop(door, None)
                elif isinstance(address, str) and address.strip():
                    addresses[door] = address.strip()
        for key, value in incoming.items():
            if key in ("id", "channels"):
                continue
            person[key] = value
        if changed:
            person["channels"] = addresses
    return {**base, "people": people}


def load_identity(home: str | Path) -> IdentityConfig | None:
    """
    The name a person gave the agent, or `None` when they have not given one.

    Forgiving for the same reason as `load_base_model`: this file is written by an
    interface at the moment somebody presses save, so a half-written or hand-edited
    one is a normal thing to find. Falling back to the default name is a visible,
    recoverable state — a settings screen that can see there is no name offers to
    write one — whereas refusing to start the agent over it is not recoverable at all.
    """
    try:
        data = _read_yaml(identity_path(home))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or not data:
        return None
    try:
        return IdentityConfig(**data)
    except (ValidationError, TypeError):
        return None


def config_dir(config_dir: str | Path | None = None) -> Path:
    """Where the committed configuration lives, resolved once and in one place.

    Split out of `load_config` because a second reader of the same two files needs
    the same answer: a running agent deciding whether anything it cares about has
    changed has to look at the files `load_config` would have read, not at a second
    guess at where they are. Resolving it twice is how a watcher ends up watching a
    file nothing reads.
    """
    cfg_dir = Path(config_dir or os.environ.get("ETHOS_CONFIG_DIR", "config"))
    if cfg_dir.is_absolute():
        return cfg_dir
    cwd_cfg = Path.cwd() / cfg_dir
    return cwd_cfg if cwd_cfg.exists() else Path(__file__).parent.parent / cfg_dir.name


def load_config(config_dir_override: str | Path | None = None) -> EthosConfig:
    cfg_dir = config_dir(config_dir_override)

    sections: dict[str, Any] = {}
    section_files = {
        "main": "ethos.yaml",
        "rhythm": "rhythm.yaml",
        "gsl": "gsl.yaml",
        "social": "social.yaml",
        "memory": "memory.yaml",
        "models": "models.yaml",
        "pricing": "pricing.yaml",
        "quirks": "quirks.yaml",
        "permissions": "permissions.yaml",
        "channels": "channels.yaml",
        "people": "people.yaml",
    }
    for key, fname in section_files.items():
        data = _read_yaml(cfg_dir / fname)
        if key == "main":
            sections.update(data)
        else:
            sections[key] = data

    if home_env := os.environ.get("ETHOS_HOME"):
        sections.setdefault("paths", {})["home"] = home_env
    if dsn_env := os.environ.get("ETHOS_DSN"):
        sections.setdefault("database", {})["dsn"] = dsn_env

    # The doors and the contact book are read a second time, from the agent's own
    # home, and merged over the shipped files. Both are there for the same reason
    # the base model is read from there: a bot token and a chat id are private to
    # this installation, and `config/` is a directory meant to be committed. Read
    # after `ETHOS_HOME` has been applied, because that is the home an interface
    # was told to write to, and merged rather than replaced so a file that names
    # one door says nothing about the others.
    home = _expand(str(sections.get("paths", {}).get("home") or "~/.ethos"))
    if channels_overlay := read_overlay(channels_path(home)):
        sections["channels"] = _merge_section(sections.get("channels") or {}, channels_overlay)
    if people_overlay := read_overlay(people_path(home)):
        sections["people"] = _merge_people(sections.get("people") or {}, people_overlay)

    cfg = EthosConfig(**sections)
    # `models.yaml` is a routing table, not a place to keep a secret, so a base
    # model is read from the agent's home — after `ETHOS_HOME` has been applied,
    # because that is the home the interface bridge writes to.
    if cfg.models.base is None:
        cfg.models.base = load_base_model(cfg.paths.home)
    cfg.models.models = cfg.models.resolved()
    # The name is read from the agent's home for the same reason, and in the same
    # place, because it is written by the same interfaces: `config/ethos.yaml` says
    # what a deployment is called before anybody has named the thing running on it,
    # and this says what this person decided to call it. A file that will not read is
    # not an error — the agent keeps its own default name and the settings screen can
    # still show and replace it.
    identity = load_identity(cfg.paths.home)
    if identity is not None:
        cfg.self_name = identity.self_name
    # `config/ethos.yaml` is shipped, and therefore plain text somebody can edit, so
    # the name in it gets the same check as a written one. Falling back rather than
    # refusing to start is deliberate: a name with a line break in it is a character
    # somebody can fix, and an agent that will not boot over one is not a state
    # anybody chose.
    if not is_valid_agent_name(cfg.self_name):
        cfg.self_name = DEFAULT_SELF_NAME
    if os.environ.get("ETHOS_EMBEDDING") == "hashing":
        cfg.gateway.embedding.provider = "hashing"
    return cfg


@lru_cache(maxsize=1)
def get_config() -> EthosConfig:
    return load_config()


def reset_config_cache() -> None:
    get_config.cache_clear()
