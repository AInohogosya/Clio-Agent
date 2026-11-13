from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApprovalRequest:
    id: str
    tool: str
    args_redacted: dict[str, Any]
    reason: str
    thread_id: str = "self"
    intention_id: str | None = None
    safety_level: int = 3
    inside_subagent: str | None = None
    created_at: float = field(default_factory=time.time)
    status: str = "pending"
    decided_by: str | None = None
    decided_at: float | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args_redacted,
            "reason": self.reason,
            "thread_id": self.thread_id,
            "intention_id": self.intention_id,
            "safety_level": self.safety_level,
            "inside_subagent": self.inside_subagent,
            "created_at": self.created_at,
            "status": self.status,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
        }


class ApprovalManager:
    def __init__(self, default_timeout_s: float = 300.0) -> None:
        self.default_timeout_s = default_timeout_s
        self.pending: dict[str, ApprovalRequest] = {}

    async def request_and_wait(
        self,
        tool: str,
        args_redacted: dict[str, Any],
        *,
        reason: str,
        thread_id: str = "self",
        intention_id: str | None = None,
        safety_level: int = 3,
        inside_subagent: str | None = None,
        timeout_s: float | None = None,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            tool=tool,
            args_redacted=args_redacted,
            reason=reason,
            thread_id=thread_id,
            intention_id=intention_id,
            safety_level=safety_level,
            inside_subagent=inside_subagent,
        )
        self.pending[request.id] = request
        timeout = self.default_timeout_s if timeout_s is None else timeout_s
        try:
            await asyncio.wait_for(request.event.wait(), timeout=timeout)
        except TimeoutError:
            request.status = "timeout"
            request.decided_at = time.time()
            request.event.set()
        finally:
            self.pending.pop(request.id, None)
        return request

    def create_pending(
        self,
        tool: str,
        args_redacted: dict[str, Any],
        *,
        reason: str,
        thread_id: str = "self",
        intention_id: str | None = None,
        safety_level: int = 3,
        inside_subagent: str | None = None,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            tool=tool,
            args_redacted=args_redacted,
            reason=reason,
            thread_id=thread_id,
            intention_id=intention_id,
            safety_level=safety_level,
            inside_subagent=inside_subagent,
        )
        self.pending[request.id] = request
        return request

    def approve(self, request_id: str, *, by: str = "owner") -> ApprovalRequest | None:
        request = self.pending.get(request_id)
        if request is None:
            return None
        request.status = "approved"
        request.decided_by = by
        request.decided_at = time.time()
        request.event.set()
        return request

    def deny(self, request_id: str, *, by: str = "owner") -> ApprovalRequest | None:
        request = self.pending.get(request_id)
        if request is None:
            return None
        request.status = "denied"
        request.decided_by = by
        request.decided_at = time.time()
        request.event.set()
        return request

    def list_pending(self) -> list[dict[str, Any]]:
        return [request.to_dict() for request in self.pending.values()]

    def get_pending(self, request_id: str) -> dict[str, Any] | None:
        request = self.pending.get(request_id)
        return request.to_dict() if request is not None else None
