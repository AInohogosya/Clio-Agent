from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolErrorCode:
    INVALID_ARGS = "invalid_args"
    UNSAFE = "unsafe"
    ENVIRONMENT = "environment"
    EXECUTION = "execution"
    TIMEOUT = "timeout"
    GUARDIAN_REJECT = "guardian_reject"
    NOT_FOUND = "not_found"


class ToolResult(BaseModel):
    call_id: str | None = None
    ok: bool = True
    content: Any = None
    error_code: str | None = None
    error: str | None = None
    duration_ms: int = 0
    undo_ref: str | None = None
    journal_id: str | None = None
