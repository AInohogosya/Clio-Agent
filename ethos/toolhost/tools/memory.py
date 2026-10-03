from __future__ import annotations

from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost


class MemoryRecordTool(Tool):
    name = "memory.record"
    description = "Record an episode into long-term memory."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "summary": {"type": "string"},
            "body": {"type": "object"},
            "importance": {"type": "number"},
        },
        "required": ["kind", "summary"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")
        episode_id = await memory.record_episode(
            kind=str(args["kind"]),
            summary=str(args["summary"]),
            body=args.get("body"),
            importance=args.get("importance"),
            intention_id=ctx.intention_id,
            thread_id=ctx.thread_id,
        )
        return {"episode_id": str(episode_id)}


class MemorySearchTool(Tool):
    name = "memory.search"
    description = "Hybrid search over long-term memory (vector + BM25 + recency + importance)."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer"},
        },
        "required": ["query"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")
        hits = await memory.search(str(args["query"]), k=args.get("k"))
        return [
            {"kind": h.kind, "summary": h.summary, "score": round(h.score, 4),
             "id": h.id, "ts": h.ts.isoformat() if h.ts else None}
            for h in hits
        ]


class MemoryRememberFactTool(Tool):
    name = "memory.remember_fact"
    description = "Store a durable fact with provenance."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "statement": {"type": "string"},
            "confidence": {"type": "number"},
            "field": {"type": "string"},
        },
        "required": ["subject", "statement"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")
        fact_id = await memory.record_fact(
            subject=str(args["subject"]),
            statement=str(args["statement"]),
            confidence=float(args.get("confidence", 0.7)),
            provenance={"thread_id": ctx.thread_id, "tool": "memory.remember_fact",
                        "intention_id": ctx.intention_id},
            field=args.get("field"),
        )
        return {"fact_id": str(fact_id)}


class OpenQuestionTool(Tool):
    name = "memory.open_question"
    description = "Open a new question worth pursuing later."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "priority": {"type": "number"},
        },
        "required": ["text"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")
        question_id = await memory.open_question(
            str(args["text"]), priority=float(args.get("priority", 0.5)),
        )
        return {"question_id": str(question_id)}


class RandomMemoryTool(Tool):
    name = "memory.random"
    description = "Sample random memories (for mind-wandering)."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")
        hits = await memory.random_memories(int(args.get("n", 3)))
        return [{"kind": h.kind, "summary": h.summary} for h in hits]


__all__ = [
    "MemoryRecordTool", "MemorySearchTool", "MemoryRememberFactTool",
    "OpenQuestionTool", "RandomMemoryTool",
]
