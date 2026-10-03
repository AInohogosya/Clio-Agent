from __future__ import annotations

from typing import Any

from ethos.config import EthosConfig
from ethos.db import Database
from ethos.memory.self_model import SelfModel
from ethos.observability.audit import AuditLog, DbAuditSink
from ethos.observability.logger import get_logger
from ethos.schemas.gsl import IntentionStatus
from ethos.social.relationships import RelationshipStore

logger = get_logger("ethos.core.seed")


async def seed(config: EthosConfig, db: Database) -> dict[str, Any]:
    """Idempotent bootstrap: self-model defaults, people, audit genesis,
    and the agent's first intentions."""
    self_model = SelfModel(db)
    await self_model.seed_defaults(config.self_name)
    relationships = RelationshipStore(config, db)
    await relationships.seed_people()

    audit = AuditLog(DbAuditSink(db))
    existing_audit = await db.fetchval("SELECT COUNT(*) FROM audit_log")
    if int(existing_audit or 0) == 0:
        await audit.append(
            actor="system", category="lifecycle",
            summary="audit chain initialized (genesis)",
            details={"self_name": config.self_name},
        )

    existing_intentions = await db.fetchval("SELECT COUNT(*) FROM intentions")
    created: list[str] = []
    if int(existing_intentions or 0) == 0:
        first = await db.fetchval(
            "INSERT INTO intentions (id, parent_id, kind, title, desired_end_state, "
            "success_criteria, constraints, origin, status, priority, created_at, updated_at) "
            "VALUES (gen_random_uuid(), NULL, 'task', $1, $2, '[]'::jsonb, '{}'::jsonb, 'self', $3, 0.6, now(), now()) "
            "RETURNING id::text",
            "Bootstrap: verify my machine and introduce myself",
            "All my services are healthy, my memory is reachable, and the owner knows I am awake.",
            IntentionStatus.active.value,
        )
        created.append(first or "")
        habit = await db.fetchval(
            "INSERT INTO intentions (id, parent_id, kind, title, desired_end_state, "
            "success_criteria, constraints, origin, status, priority, created_at, updated_at) "
            "VALUES (gen_random_uuid(), NULL, 'habit', $1, $2, '[]'::jsonb, '{}'::jsonb, 'self', $3, 0.4, now(), now()) "
            "RETURNING id::text",
            "Maintenance: keep my machine healthy",
            "Disk, services, trash retention and integrity checks are all green.",
            IntentionStatus.active.value,
        )
        created.append(habit or "")

    logger.info("core.seeded", intentions_created=len(created))
    return {"seeded": True, "intentions_created": created}
