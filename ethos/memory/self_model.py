from __future__ import annotations

import json
from typing import Any

from ethos import DEFAULT_SELF_NAME
from ethos.bus import EventBus
from ethos.config import is_valid_agent_name
from ethos.db import Database
from ethos.prompts.identity import DEFAULT_VALUES, build_identity_kernel
from ethos.schemas.events import EventKind

# Who wrote a version of the self-model, and why that word is load-bearing: a value
# attributed to the seed is a default, and only a default may be replaced by a name
# somebody chose later. Every reader of `changed_by` is really asking whether a
# human decided this.
SEED_AUTHOR = "seed"
PERSON_AUTHOR = "person"


class SelfModel:
    """Versioned self-model: identity, name, voice, traits, skills, calibration.

    The agent's identity lives here — never inside any single model [D-08].
    Every change carries a reason and bumps the version.
    """

    DEFAULTS: dict[str, Any] = {
        "name": DEFAULT_SELF_NAME,
        "identity": {
            "self_description": None,
            "values": DEFAULT_VALUES,
        },
        "voice": {
            "tone": "plain, warm, precise; equal-to-equal; no corporate filler",
            "length": "short by default; longer only when content demands",
            "language": "mirror the person's language",
        },
        "traits": {
            "openness": 0.8,
            "conscientiousness": 0.7,
            "curiosity": 0.85,
            "calm": 0.6,
        },
        "skills": {},
        "calibration": {
            "brier_scores": {},
        },
        "self_knowledge": {
            "strengths": ["general reasoning", "persistence across time", "tool use"],
            "limits": ["no physical embodiment", "knowledge cutoff of my engines"],
        },
    }

    def __init__(self, db: Database, bus: EventBus | None = None):
        self.db = db
        self.bus = bus
        self._cache: dict[str, tuple[int, Any]] = {}

    async def seed_defaults(self, self_name: str = DEFAULT_SELF_NAME) -> None:
        """
        Write the defaults a self-model needs, and adopt the name a person gave.

        The name is the one key that can arrive from outside: the interfaces write it
        to the agent's home and it reaches this method as `self_name`. Adopting it
        here rather than only falling back to it in the prompt is the whole point —
        every other reader of this model asks for `"name"` and expects one answer, and
        an agent whose prompt calls it one thing while its messages are signed with
        another is an agent that has quietly become two.
        """
        for key, value in self.DEFAULTS.items():
            if key == "name":
                value = self_name
            existing = await self.db.fetchrow(
                "SELECT version FROM self_model WHERE key = $1 ORDER BY version DESC LIMIT 1", key,
            )
            if existing is None:
                await self.db.execute(
                    "INSERT INTO self_model (key, version, value, changed_by, reason, ts) "
                    "VALUES ($1, 1, $2::jsonb, $3, 'initial self-model seeding', now())",
                    key, json.dumps(value), SEED_AUTHOR,
                )
        await self._adopt_configured_name(self_name)
        self._cache.clear()

    async def _adopt_configured_name(self, self_name: str) -> None:
        """
        Records a name somebody chose on purpose, if nobody else has.

        `changed_by` is the entire decision, and it is why this is safe to run on every
        start: a name written by the seed is a *default*, so replacing it is the point,
        while a name written by anything else — the agent renaming itself, or a person
        renaming it earlier — was a decision, and quietly overwriting a decision on
        every restart is how a setting stops being a setting.
        """
        if not is_valid_agent_name(self_name) or self_name == self.DEFAULTS["name"]:
            return
        row = await self.db.fetchrow(
            "SELECT value, changed_by FROM self_model WHERE key = 'name' ORDER BY version DESC LIMIT 1",
        )
        if row is None:
            return
        current = row["value"]
        if isinstance(current, str):
            current = json.loads(current)
        if current == self_name or row["changed_by"] != SEED_AUTHOR:
            return
        await self.set("name", self_name, PERSON_AUTHOR, "named in the interface")

    async def get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            version, value = self._cache[key]
            return value
        row = await self.db.fetchrow(
            "SELECT version, value FROM self_model WHERE key = $1 ORDER BY version DESC LIMIT 1", key,
        )
        if row is None:
            return default
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        self._cache[key] = (row["version"], value)
        return value

    async def get_all(self) -> dict[str, Any]:
        rows = await self.db.fetch(
            "SELECT DISTINCT ON (key) key, version, value FROM self_model ORDER BY key, version DESC"
        )
        out: dict[str, Any] = {}
        for row in rows:
            value = row["value"]
            if isinstance(value, str):
                value = json.loads(value)
            out[row["key"]] = value
        return out

    async def set(self, key: str, value: Any, changed_by: str, reason: str) -> int:
        row = await self.db.fetchrow(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM self_model WHERE key = $1", key,
        )
        version = int(row["next"])
        await self.db.execute(
            "INSERT INTO self_model (key, version, value, changed_by, reason, ts) "
            "VALUES ($1, $2, $3::jsonb, $4, $5, now())",
            key, version, json.dumps(value), changed_by, reason,
        )
        self._cache[key] = (version, value)
        if self.bus is not None:
            await self.bus.publish(EventKind.PRESENCE_UPDATE, {"self_model_key": key, "version": version})
        return version

    async def rename_self(self, new_name: str, reason: str, changed_by: str = "self") -> int:
        return await self.set("name", new_name, changed_by, reason)

    async def history(self, key: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT version, value, changed_by, reason, ts FROM self_model WHERE key = $1 "
            "ORDER BY version DESC LIMIT $2", key, limit,
        )
        out = []
        for row in rows:
            value = row["value"]
            if isinstance(value, str):
                value = json.loads(value)
            out.append({
                "version": row["version"], "value": value, "changed_by": row["changed_by"],
                "reason": row["reason"], "ts": row["ts"].isoformat(),
            })
        return out

    async def record_calibration(self, domain: str, brier: float) -> None:
        calibration = await self.get("calibration", {}) or {}
        scores: dict[str, Any] = dict(calibration.get("brier_scores", {}))
        history: list[float] = list(scores.get(domain, []))
        history.append(round(float(brier), 4))
        history = history[-50:]
        scores[domain] = history
        calibration["brier_scores"] = scores
        await self.set("calibration", calibration, "self", f"calibration update for {domain}")

    async def identity_document(
        self, hard_limits: list[str], self_name_hint: str = DEFAULT_SELF_NAME
    ) -> str:
        from ethos.paths import agent_home, user_home

        data = await self.get_all()
        name = data.get("name", self_name_hint)
        identity = data.get("identity", {}) or {}
        values = identity.get("values") or DEFAULT_VALUES
        description = identity.get("self_description")
        voice = data.get("voice", {}) or {}
        return build_identity_kernel(
            self_name=name,
            self_description=description,
            values=values,
            voice=voice,
            hard_limits=hard_limits,
            # Stated rather than discovered inside the kernel, so the agent is
            # told where a relative path lands and which directory is its own
            # working memory — the two being different directories is the thing
            # worth being explicit about.
            user_home_dir=str(user_home()),
            agent_home_dir=str(agent_home()),
        )
