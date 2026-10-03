from __future__ import annotations

import json
from typing import Any

from ethos.config import EthosConfig
from ethos.db import Database
from ethos.observability.logger import get_logger

logger = get_logger("ethos.social.relationships")

STREAK_REVIEW_THRESHOLD = 3


class RelationshipStore:
    """People memory: relations, trust, preferences, and the ignore-streak
    detector that triggers a T3 self-review after 3 consecutive ignores [D-05]."""

    def __init__(self, config: EthosConfig, db: Database | None = None):
        self.config = config
        self.db = db
        self._streaks: dict[str, dict[str, Any]] = {}

    async def seed_people(self) -> None:
        if self.db is None:
            return
        for person in self.config.people.people:
            existing = await self.db.fetchrow("SELECT id FROM people WHERE id = $1", person.id)
            if existing is None:
                await self.db.execute(
                    "INSERT INTO people (id, display_name, relation, channels, preferences, trust, "
                    "last_contact, notes) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, NULL, '')",
                    person.id, person.display_name, person.relation,
                    json.dumps(person.channels), json.dumps(person.preferences), person.trust,
                )

    async def get(self, person_id: str) -> dict[str, Any] | None:
        if self.db is None:
            return None
        row = await self.db.fetchrow("SELECT * FROM people WHERE id = $1", person_id)
        if row is None:
            return None
        channels = row["channels"]
        if isinstance(channels, str):
            channels = json.loads(channels)
        preferences = row["preferences"]
        if isinstance(preferences, str):
            preferences = json.loads(preferences)
        return {
            "id": row["id"], "display_name": row["display_name"], "relation": row["relation"],
            "channels": dict(channels or {}), "preferences": dict(preferences or {}),
            "trust": float(row["trust"] or 0.5), "last_contact": row["last_contact"],
            "notes": row["notes"] or "",
        }

    async def upsert(self, person_id: str, display_name: str, relation: str = "known",
                     trust: float = 0.5, channels: dict | None = None,
                     preferences: dict | None = None) -> None:
        if self.db is None:
            return
        await self.db.execute(
            "INSERT INTO people (id, display_name, relation, channels, preferences, trust, last_contact, notes) "
            "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, NULL, '') "
            "ON CONFLICT (id) DO UPDATE SET display_name = $2, relation = $3",
            person_id, display_name, relation,
            json.dumps(channels or {}), json.dumps(preferences or {}), trust,
        )

    async def note_contact(self, person_id: str | None, direction: str) -> None:
        if self.db is None or person_id is None:
            return
        await self.db.execute(
            "UPDATE people SET last_contact = now() WHERE id = $1", person_id,
        )
        if direction == "outbound":
            self._streaks.pop(person_id, None)
        else:
            self._streaks.pop(person_id, None)

    async def adjust_trust(self, person_id: str, delta: float) -> float:
        if self.db is None:
            return 0.5
        row = await self.db.fetchrow(
            "UPDATE people SET trust = GREATEST(0.0, LEAST(1.0, trust + $1)) WHERE id = $2 "
            "RETURNING trust",
            delta, person_id,
        )
        return float(row["trust"]) if row else 0.5

    async def record_ignore(self, person_id: str | None, reason: str | None) -> dict[str, Any]:
        if person_id is None:
            return {"count": 0, "review": False}
        streak = self._streaks.setdefault(person_id, {"count": 0, "reasons": []})
        streak["count"] += 1
        if reason:
            streak["reasons"].append(reason)
        review_due = streak["count"] >= STREAK_REVIEW_THRESHOLD
        result = {"count": streak["count"], "reasons": list(streak["reasons"]), "review": review_due}
        if review_due:
            streak["count"] = 0
            streak["reasons"] = []
        return result
