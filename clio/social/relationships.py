from __future__ import annotations

import json
from typing import Any

from clio.config import ClioConfig
from clio.db import Database
from clio.observability.logger import get_logger

logger = get_logger("clio.social.relationships")

STREAK_REVIEW_THRESHOLD = 3

PRINCIPAL_KINDS = frozenset({"human", "resident", "service"})


def check_principal_kind(kind: str) -> str:
    kind = (kind or "human").lower()
    if kind not in PRINCIPAL_KINDS:
        raise ValueError(f"unknown principal kind: {kind!r}")
    return kind


class RelationshipStore:
    """People memory: relations, trust, preferences, and the ignore-streak
    detector that triggers a T3 self-review after 3 consecutive ignores [D-05]."""

    def __init__(self, config: ClioConfig, db: Database | None = None):
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
        capabilities = row["capabilities"] if "capabilities" in row.keys() else {}
        if isinstance(capabilities, str):
            capabilities = json.loads(capabilities)
        authority_scope = row["authority_scope"] if "authority_scope" in row.keys() else {}
        if isinstance(authority_scope, str):
            authority_scope = json.loads(authority_scope)
        kind = "human"
        endpoint_uri = None
        try:
            kind = row["kind"] or "human"
        except (KeyError, TypeError):
            pass
        try:
            endpoint_uri = row["endpoint_uri"]
        except (KeyError, TypeError):
            pass
        return {
            "id": row["id"], "display_name": row["display_name"], "relation": row["relation"],
            "channels": dict(channels or {}), "preferences": dict(preferences or {}),
            "trust": float(row["trust"] or 0.5), "last_contact": row["last_contact"],
            "notes": row["notes"] or "",
            "kind": kind,
            "capabilities": dict(capabilities or {}),
            "authority_scope": dict(authority_scope or {}),
            "endpoint_uri": endpoint_uri,
        }

    async def register_resident(
        self, principal_id: str, display_name: str, *, capabilities: dict[str, Any] | None = None,
        authority_scope: dict[str, Any] | None = None, endpoint_uri: str | None = None,
        trust: float = 0.5, channels: dict[str, Any] | None = None,
    ) -> None:
        """Register or update a peer Clio resident principal."""
        if self.db is None:
            return
        await self.db.execute(
            "INSERT INTO people (id, display_name, relation, channels, preferences, trust, "
            "last_contact, notes, kind, capabilities, authority_scope, endpoint_uri) "
            "VALUES ($1, $2, 'resident', $3::jsonb, '{}'::jsonb, $4, NULL, '', 'resident', $5::jsonb, $6::jsonb, $7) "
            "ON CONFLICT (id) DO UPDATE SET display_name = $2, kind = 'resident', "
            "capabilities = $5::jsonb, authority_scope = $6::jsonb, endpoint_uri = $7",
            principal_id, display_name, json.dumps(channels or {}), trust,
            json.dumps(capabilities or {}), json.dumps(authority_scope or {}), endpoint_uri,
        )

    async def list_residents(self, capability: str | None = None) -> list[dict[str, Any]]:
        """Peer residents, optionally filtered to one capability."""
        if self.db is None:
            return []
        try:
            rows = await self.db.fetch(
                "SELECT * FROM people WHERE kind = 'resident' ORDER BY display_name",
            )
        except Exception:
            logger.exception("relationships.list_residents_failed")
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            person = await self.get(row["id"])
            if person is None:
                continue
            if capability is not None:
                caps = person.get("capabilities") or {}
                has = capability in caps or capability in (caps.get("skills") or [])
                if not has:
                    continue
            out.append(person)
        return out

    async def match_resident(
        self, *, required_capability: str | None = None,
        min_trust: float | None = None, max_budget_usd: float | None = None,
    ) -> dict[str, Any] | None:
        """Best peer resident for a commission, or None.

        Filters by capability, trust floor and the resident's commission
        budget ceiling, then picks the highest-trust match.
        """
        residents = await self.list_residents(required_capability)
        scored: list[tuple[float, dict[str, Any]]] = []
        for resident in residents:
            trust = float(resident.get("trust") or 0.0)
            if min_trust is not None and trust < min_trust:
                continue
            scope = resident.get("authority_scope") or {}
            ceiling = scope.get("max_budget_usd")
            if max_budget_usd is not None and ceiling is not None:
                try:
                    if float(ceiling) < float(max_budget_usd):
                        continue
                except (TypeError, ValueError):
                    pass
            scored.append((trust, resident))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    async def set_preference(self, person_id: str, key: str, value: Any) -> None:
        if self.db is None or not person_id:
            return
        row = await self.db.fetchrow(
            "SELECT preferences FROM people WHERE id = $1", person_id,
        )
        prefs: dict[str, Any] = {}
        if row is not None:
            raw = row["preferences"]
            if isinstance(raw, str):
                try:
                    prefs = json.loads(raw) or {}
                except ValueError:
                    prefs = {}
            elif isinstance(raw, dict):
                prefs = dict(raw)
        prefs[key] = value
        await self.db.execute(
            "INSERT INTO people (id, display_name, relation, channels, preferences, trust, last_contact, notes) "
            "VALUES ($1, '', 'known', '{}'::jsonb, $2::jsonb, 0.5, NULL, '') "
            "ON CONFLICT (id) DO UPDATE SET preferences = $2::jsonb",
            person_id, json.dumps(prefs),
        )

    async def get_preference(self, person_id: str, key: str, default: Any = None) -> Any:
        person = await self.get(person_id) if person_id else None
        if person is None:
            return default
        return (person.get("preferences") or {}).get(key, default)

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
