from __future__ import annotations

import asyncio
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from ethos.config import EmbeddingConfig, EthosConfig, PathsConfig
from ethos.db import Database
from ethos.gateway.embeddings import HashingEmbedder
from ethos.schemas.models import (
    ModelMessage,
    ModelRequest,
    NormalizedResponse,
    Role,
    Tier,
    Usage,
)
from ethos.schemas.tools import ToolSpec

os.environ.setdefault("ETHOS_EMBEDDING", "hashing")

# The agent's own database, derived rather than repeated: `ETHOS_DSN` is what the
# agent and the bridge are pointed at, and the suite must never write into it.
AGENT_DSN = os.environ.get("ETHOS_DSN", "postgresql://ethos:ethos@127.0.0.1:5433/ethos")

# The database the suites are allowed to write into.
#
# It used to be the agent's own, and that was not a small thing: every
# integration and scenario test recorded episodes, questions, intentions and
# *messages* in the agent's real memory, so a `pytest` run left rows in the
# transcript the owner reads — and the agent then deliberated on test messages
# and answered them. It also made the suite fail against a database that had been
# used: `open_questions` and `list_intentions` are bounded and one of them is
# ordered by `RANDOM()`, so a question the test had just written lost its place
# among the agent's own hundred and the assertion failed on data the test never
# touched. A suite that has to be run against a pristine database to be believed
# is a suite nobody can run.
#
# So the tests get a database of their own, created and migrated on demand.
# `ETHOS_TEST_DSN` still wins: pointing the suite at a database is how a
# deployment runs it against a throwaway cluster.
TEST_DATABASE = os.environ.get("ETHOS_TEST_DB", "ethos_test")


def test_database_dsn() -> str:
    parts = urlsplit(AGENT_DSN)
    return urlunsplit((parts.scheme, parts.netloc, f"/{TEST_DATABASE}", "", ""))


def provision_test_database() -> bool:
    """Creates and migrates the suite's own database, once per run.

    False when it cannot be provisioned, which is the caller's cue to skip
    rather than to fall back: writing into the agent's memory is worse than
    running nothing.
    """
    import asyncio

    from alembic import command
    from alembic.config import Config as AlembicConfig

    dsn = test_database_dsn()
    name = TEST_DATABASE

    async def _create() -> bool:
        import asyncpg

        parts = urlsplit(AGENT_DSN)
        admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
        try:
            conn = await asyncpg.connect(admin, timeout=5)
        except Exception:
            return False
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()
        return True

    try:
        if not asyncio.run(_create()):
            return False
        root = Path(__file__).resolve().parent.parent
        cfg = AlembicConfig(str(root / "migrations" / "alembic.ini"))
        # `migrations/env.py` reads the DSN from the environment and overrides
        # whatever the config was given, so this is the only way to point it at
        # the suite's database — and the same way `ethos db upgrade` is pointed
        # at one by hand.
        previous = os.environ.get("ETHOS_DSN")
        os.environ["ETHOS_DSN"] = dsn
        try:
            command.upgrade(cfg, "head")
        finally:
            if previous is None:
                os.environ.pop("ETHOS_DSN", None)
            else:
                os.environ["ETHOS_DSN"] = previous
    except Exception:
        return False
    return True


def drop_test_database() -> None:
    """Ends the run by leaving nothing behind."""
    import asyncio

    import asyncpg

    parts = urlsplit(AGENT_DSN)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))

    async def _drop() -> None:
        try:
            conn = await asyncpg.connect(admin, timeout=5)
        except Exception:
            return
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                TEST_DATABASE,
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}"')
        except Exception:
            pass
        finally:
            await conn.close()

    try:
        asyncio.run(_drop())
    except Exception:
        pass


def port_free(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class FakeProvider:
    """Scripted provider for tests: real interface, no network."""

    def __init__(self, responses: list[NormalizedResponse] | None = None,
                 errors: list[Exception | None] | None = None,
                 available: bool = True):
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.available_flag = available
        self.requests: list[ModelRequest] = []
        self.calls = 0

    def available(self) -> bool:
        return self.available_flag

    async def complete(self, request: ModelRequest, model: str) -> NormalizedResponse:
        self.requests.append(request)
        self.calls += 1
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        if self.responses:
            response = self.responses.pop(0)
        else:
            response = NormalizedResponse(text="{}", usage=Usage(input_tokens=100, output_tokens=10))
        response.provider = "fake"
        response.model = model
        return response

    def text(self, value: str) -> None:
        self.responses.append(NormalizedResponse(text=value, usage=Usage(input_tokens=100, output_tokens=10)))


class ScriptedGateway:
    """Duck-typed gateway returning scripted JSON texts in order.

    `finish_reasons` scripts the provider's own word for how each response ended,
    which is the only way to reproduce a response cut off mid-answer: an unfinished
    JSON object does not parse, so a truncated reply is indistinguishable from a
    malformed one unless the reason says which it was.
    """

    def __init__(self, texts: list[str], costs: list[float] | None = None,
                 finish_reasons: list[str | None] | None = None):
        self.texts = list(texts)
        self.costs = list(costs or [])
        self.finish_reasons = list(finish_reasons or [])
        self.requests: list[ModelRequest] = []
        self.spend = 0.0

    async def complete(self, request: ModelRequest) -> NormalizedResponse:
        self.requests.append(request)
        text = self.texts.pop(0) if self.texts else "{}"
        cost = self.costs.pop(0) if self.costs else 0.01
        finish = self.finish_reasons.pop(0) if self.finish_reasons else None
        self.spend += cost
        return NormalizedResponse(text=text, cost_usd=cost,
                                  usage=Usage(input_tokens=100, output_tokens=20),
                                  finish_reason=finish)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return HashingEmbedder(1024).embed(texts)

    async def budget(self) -> dict[str, Any]:
        return {
            "day_total": 0.0, "month_total": 0.0, "day_commitment": 0.0,
            "day_discretionary": 0.0, "daily_cap": 20.0, "monthly_cap": 400.0,
            "commitment_cap": 10.0, "discretionary_cap": 10.0,
        }

    async def healthz(self) -> dict[str, Any]:
        return {"status": "ok"}


class ListAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, actor: str, category: str, summary: str,
                     details: dict | None = None) -> None:
        self.entries.append({"actor": actor, "category": category,
                             "summary": summary, "details": details or {}})

    async def verify_chain(self) -> bool:
        return True

    async def anchor_state(self) -> dict[str, Any]:
        return {"seq": len(self.entries)}


class FakeRegistry:
    def __init__(self, protected: dict[str, dict[str, Any]] | None = None):
        self.protected = protected or {}
        self.registered: list[tuple[str, str | None]] = []

    async def is_protected(self, path) -> dict[str, Any] | None:
        from pathlib import Path

        target = str(Path(path))
        for prefix, value in self.protected.items():
            if target.startswith(prefix):
                return value
        return None

    async def auto_register_write(self, path, *, commissioned_by: str | None,
                                 intention_id: str | None, kind: str = "file") -> str | None:
        self.registered.append((str(path), commissioned_by))
        return str(uuid4())


class FakeVerifier:
    def __init__(self, supported: bool = True, why: str = "evidence supports it"):
        self.supported = supported
        self.why = why
        self.calls: list[dict[str, Any]] = []

    async def check_consistency(self, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"reason": reason, "evidence": evidence})
        return {"supported": self.supported, "why": self.why}


class NoopControl:
    def __init__(self) -> None:
        self.pause_actions = False
        self.paused = False
        self.stopped = False
        self.emergency = False
        self.preview = False

    async def wait_resume(self) -> None:
        await asyncio.sleep(3600)


class NoopSnapshots:
    async def create(self, reason: str, source: Path | None = None) -> dict[str, Any]:
        return {"name": f"snap-{time.time()}", "reason": reason}

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    async def restore(self, name: str) -> Path:
        raise NotImplementedError


def make_config(tmp_dir: Path) -> EthosConfig:
    home = tmp_dir / "ethos-home"
    config = EthosConfig()
    config.paths = PathsConfig(
        home=home,
        workspace=home / "workspace",
        work=home / "work",
        toolshed=home / "toolshed",
        notes=home / "notes",
        trash=home / "trash",
        snapshots=home / "snapshots",
        threads=home / "work" / ".threads",
    )
    config.gateway.embedding = EmbeddingConfig(provider="hashing", dim=1024)
    config.database.dsn = os.environ.get("ETHOS_TEST_DSN", test_database_dsn())
    return config


@pytest.fixture()
def config(tmp_path: Path) -> EthosConfig:
    cfg = make_config(tmp_path)
    cfg.paths.ensure()
    return cfg


@pytest.fixture()
def embedder() -> HashingEmbedder:
    return HashingEmbedder(1024)


@pytest.fixture(scope="session")
def test_dsn() -> str:
    """The DSN the database-backed suites may write into, provisioned once.

    The database is dropped again at the end of the run, so each run starts from
    nothing: a suite that leaves state behind needs a pristine database before it
    can be believed, and a database nobody cleans is a database somebody has to.
    A DSN the operator chose is never dropped — that one was not ours.
    """
    configured = os.environ.get("ETHOS_TEST_DSN")
    if configured:
        yield configured
        return
    if not provision_test_database():
        pytest.skip(f"could not provision the suite's own database ({TEST_DATABASE})")
    yield test_database_dsn()
    drop_test_database()


@pytest.fixture()
async def db(test_dsn: str):
    database = await Database.try_connect(test_dsn, timeout_s=3.0)
    if database is None:
        pytest.skip("PostgreSQL not available for integration test")
    yield database
    try:
        await database.close()
    except Exception:
        pass


@pytest.fixture()
def fake_provider() -> FakeProvider:
    return FakeProvider()


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(name="fs.read", description="read a file",
                 input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                               "required": ["path"], "additionalProperties": False}),
        ToolSpec(name="shell.run", description="run a command",
                 input_schema={"type": "object", "properties": {"command": {"type": "string"}},
                               "required": ["command"]}),
    ]


def simple_request(**overrides: Any) -> ModelRequest:
    base = dict(
        tier=Tier.T2,
        purpose="test",
        messages=[
            ModelMessage(role=Role.system, content="identity"),
            ModelMessage(role=Role.system, content="tools"),
            ModelMessage(role=Role.system, content="limits"),
            ModelMessage(role=Role.user, content="hello"),
        ],
        tools=tool_specs(),
        cache_prefix_blocks=3,
        max_tokens=1024,
    )
    base.update(overrides)
    return ModelRequest(**base)


@pytest.fixture()
def quirks() -> Any:
    from ethos.config import QuirksConfig

    return QuirksConfig().anthropic
