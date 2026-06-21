"""Core domain models for the Neuro-Scaffold agent state machine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentPhase(str, Enum):
    """Phases of the Plan -> Execute -> Observe -> Reflect loop."""
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_INPUT = "awaiting_input"


class ToolName(str, Enum):
    """All available tool names for the agent."""
    SHELL_EXEC = "shell_exec"
    SHELL_PERSISTENT = "shell_persistent"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_EDIT = "file_edit"
    FILE_DELETE = "file_delete"
    AST_QUERY = "ast_query"
    AST_SEARCH = "ast_search"
    LINT_CHECK = "lint_check"
    TEST_RUN = "test_run"
    GIT_STATUS = "git_status"
    GIT_DIFF = "git_diff"
    GIT_COMMIT = "git_commit"
    CONTEXT_SEARCH = "context_search"
    SCRATCHPAD_READ = "scratchpad_read"
    SCRATCHPAD_WRITE = "scratchpad_write"
    ASK_USER = "ask_user"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ToolCall(BaseModel):
    """A single tool invocation request from the agent."""
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    tool: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(default=False)
    timeout_seconds: int | None = None


class ToolResult(BaseModel):
    """Result of a tool invocation."""
    call_id: str
    tool: ToolName
    success: bool
    output: str = ""
    error: str | None = None
    exit_code: int | None = None
    duration_ms: float = 0.0
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    """A single step in the agent's plan."""
    step_id: int
    description: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    completed: bool = False
    result_summary: str | None = None
    error: str | None = None


class Scratchpad(BaseModel):
    """Persistent working memory for the agent."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task: str = ""
    plan: list[PlanStep] = Field(default_factory=list)
    current_step: int = 0
    observations: list[str] = Field(default_factory=list)
    reflections: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def add_observation(self, text: str) -> None:
        self.observations.append(text)
        self.updated_at = datetime.now(timezone.utc)

    def add_reflection(self, text: str) -> None:
        self.reflections.append(text)
        self.updated_at = datetime.now(timezone.utc)

    def current_plan_step(self) -> PlanStep | None:
        if 0 <= self.current_step < len(self.plan):
            return self.plan[self.current_step]
        return None

    def advance(self) -> None:
        self.current_step += 1
        self.updated_at = datetime.now(timezone.utc)

    def is_complete(self) -> bool:
        return self.current_step >= len(self.plan) and len(self.plan) > 0


class AgentState(BaseModel):
    """Full snapshot of the agent's current state."""
    state_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    phase: AgentPhase = AgentPhase.IDLE
    scratchpad: Scratchpad = Field(default_factory=Scratchpad)
    iteration: int = 0
    max_iterations: int = 50
    tool_calls_this_iteration: int = 0
    max_tool_calls_per_iteration: int = 10
    last_tool_result: ToolResult | None = None
    error_count: int = 0
    max_consecutive_errors: int = 3
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def transition_to(self, phase: AgentPhase) -> None:
        self.phase = phase
        self.last_activity = datetime.now(timezone.utc)

    def record_tool_call(self) -> None:
        self.tool_calls_this_iteration += 1
        self.last_activity = datetime.now(timezone.utc)

    def start_new_iteration(self) -> None:
        self.iteration += 1
        self.tool_calls_this_iteration = 0
        self.last_activity = datetime.now(timezone.utc)

    def record_error(self) -> None:
        self.error_count += 1
        self.last_activity = datetime.now(timezone.utc)

    def reset_error_count(self) -> None:
        self.error_count = 0

    def is_iteration_limit_reached(self) -> bool:
        return self.iteration >= self.max_iterations

    def is_tool_call_limit_reached(self) -> bool:
        return self.tool_calls_this_iteration >= self.max_tool_calls_per_iteration

    def is_error_limit_reached(self) -> bool:
        return self.error_count >= self.max_consecutive_errors


class LintIssue(BaseModel):
    """A single lint or syntax issue."""
    file: str
    line: int
    column: int
    severity: Severity
    message: str
    rule_id: str | None = None
    source: str = ""


class LintResult(BaseModel):
    """Result of a lint/syntax check pass."""
    issues: list[LintIssue] = Field(default_factory=list)
    files_checked: int = 0
    duration_ms: float = 0.0

    @property
    def has_errors(self) -> bool:
        return any(i.severity in (Severity.ERROR, Severity.CRITICAL) for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)


class SymbolType(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    MODULE = "module"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"
    DECORATOR = "decorator"


class Symbol(BaseModel):
    """A single code symbol extracted by the AST mapper."""
    name: str
    symbol_type: SymbolType
    file_path: str
    line_start: int
    line_end: int
    column_start: int = 0
    column_end: int = 0
    signature: str | None = None
    docstring: str | None = None
    parent: str | None = None
    children: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    language: str = "python"


class ASTMap(BaseModel):
    """Complete AST skeleton of a codebase."""
    root_path: str
    symbols: dict[str, Symbol] = Field(default_factory=dict)
    file_index: dict[str, list[str]] = Field(default_factory=dict)
    language_stats: dict[str, int] = Field(default_factory=dict)
    total_files: int = 0
    total_symbols: int = 0
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContextChunk(BaseModel):
    """A chunk of code context retrieved for the LLM."""
    file_path: str
    start_line: int
    end_line: int
    content: str
    symbols: list[str] = Field(default_factory=list)
    relevance_score: float = 0.0
    token_estimate: int = 0


class ContextRetrievalResult(BaseModel):
    """Result of a context retrieval query."""
    chunks: list[ContextChunk] = Field(default_factory=list)
    total_tokens: int = 0
    query: str = ""
    duration_ms: float = 0.0


class SessionInfo(BaseModel):
    """Authenticated session information."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    permissions: list[str] = Field(default_factory=lambda: ["read", "execute"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthPayload(BaseModel):
    """Authentication payload from Clio Agent."""
    api_key: str
    agent_id: str
    task: str
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
