from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from clio import PRODUCT_NAME, __version__
from clio.config import ClioConfig
from clio.gateway.secrets import redact
from clio.guardian.classifier import classify_tool
from clio.guardian.gate import GateDecision, GuardianGate
from clio.guardian.journal import (
    STATUS_INTERRUPTED,
    STATUS_OK,
    ActionJournal,
    DuplicateAction,
)
from clio.observability import metrics
from clio.observability.logger import get_logger
from clio.schemas.events import EventKind
from clio.schemas.tools import ToolErrorCode, ToolResult

logger = get_logger("clio.toolhost")

# What kind of ending each journal status is, in the stops vocabulary (NA-P3.4).
#
# The journal already distinguishes the statuses; this maps each to the
# disposition a person would name when asking why something stopped. `rejected`
# is a guardian decision -- a stop, not a malfunction -- and `timeout` is a
# ceiling being hit, which is a limit rather than an error. They were all
# "failed" to every reader that was not this file.
STATUS_DISPOSITIONS = {
    "ok": "completed",
    "rejected": "stopped",
    "timeout": "limit_reached",
    "failed": "error",
    "interrupted": "error",
}


class ToolContext(BaseModel):
    thread_id: str = "self"
    intention_id: str | None = None
    reason: str = ""
    idempotency_key: str | None = None
    commissioned_by: str | None = None
    consequential: bool = False
    cwd: str | None = None
    timeout_s: float | None = None
    inside_subagent: str | None = None
    # Clio Agent 3.3 project leadership: which container this call belongs to.
    # `project_id` is stamped onto the journal row and onto auto-registered
    # artifacts, and `workspace_lease_id` is the fence the gate enforces the
    # call against — two projects never share a write path.
    project_id: str | None = None
    root_intention_id: str | None = None
    workspace_lease_id: str | None = None


class ExecuteRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    context: ToolContext = ToolContext()


class Tool(ABC):
    name: str = "abstract"
    description: str = ""
    timeout_s: float = 90.0

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any: ...


class Toolhost:
    """Primitive tool execution under the guardian gate with WAL journaling [Section 9]."""

    def __init__(
        self,
        config: ClioConfig,
        gate: GuardianGate,
        journal: ActionJournal,
        services: dict[str, Any] | None = None,
        bus: Any = None,
    ):
        self.config = config
        self.gate = gate
        self.journal = journal
        self.services: dict[str, Any] = services or {}
        self.bus = bus
        self.tools: dict[str, Tool] = {}
        self.background_jobs: dict[str, dict[str, Any]] = {}
        from clio.toolhost.browser import BrowserPool
        from clio.toolhost.desktop import DesktopController
        from clio.toolhost.kernels import KernelManager
        from clio.toolhost.pty_session import PtyManager
        from clio.toolhost.watchers import WatcherService

        self.pty_manager = PtyManager()
        self.kernel_manager = KernelManager()
        self.browser_pool = BrowserPool()
        self.watcher_service = WatcherService()
        self.desktop = DesktopController()
        self._register_builtin_tools()

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def catalog(self) -> list[dict[str, Any]]:
        from clio.guardian.safety import effective_level, usable_tool_names

        level = effective_level(self.config, self.gate.control if hasattr(self.gate, "control") else None)
        names = [tool.name for tool in sorted(self.tools.values(), key=lambda t: t.name)]
        allowed = set(usable_tool_names(names, level))
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in sorted(self.tools.values(), key=lambda t: t.name)
            if tool.name in allowed
        ]

    def service(self, name: str) -> Any:
        if name not in self.services:
            raise RuntimeError(f"service '{name}' is not available in this toolhost")
        return self.services[name]

    def _register_builtin_tools(self) -> None:
        from clio.subagents.tools import ApexRunTool, AxiomRunTool, FoundryRunTool, SubagentStatusTool
        from clio.toolhost.tools.browser_tools import (
            BrowserClickTool,
            BrowserCloseTool,
            BrowserExtractTool,
            BrowserScreenshotTool,
            BrowserTypeTool,
            BrowserVisitTool,
        )
        from clio.toolhost.tools.code import CodeExecTool, CodeResetTool
        from clio.toolhost.tools.comms import CommSendTool
        from clio.toolhost.tools.desktop_tools import (
            DesktopClickTool,
            DesktopKeyTool,
            DesktopMoveTool,
            DesktopScreenshotTool,
            DesktopTypeTool,
            DesktopWindowsTool,
        )
        from clio.toolhost.tools.fs import (
            FsDeleteTool,
            FsEditTool,
            FsListTool,
            FsMkdirTool,
            FsMoveTool,
            FsReadTool,
            FsStatTool,
            FsWriteTool,
        )
        from clio.toolhost.tools.history import HistoryBrowseTool
        from clio.toolhost.tools.http import HttpTool
        from clio.toolhost.tools.intent import (
            IntentCloseTool,
            IntentCreateTool,
            IntentGetTool,
            IntentListTool,
            IntentUpdateTool,
        )
        from clio.toolhost.tools.memory import (
            MemoryRecordTool,
            MemoryRememberFactTool,
            MemorySearchTool,
            OpenQuestionTool,
            RandomMemoryTool,
        )
        from clio.toolhost.tools.schedule import (
            ScheduleAtTool,
            ScheduleCancelTool,
            ScheduleEveryTool,
        )
        from clio.toolhost.tools.secrets import SecretsGetTool, SecretsListTool, SecretsSetTool
        from clio.toolhost.tools.shell import (
            ShellJobTool,
            ShellRunTool,
            ShellSessionCloseTool,
            ShellSessionOpenTool,
            ShellSessionReadTool,
            ShellSessionWriteTool,
        )
        from clio.toolhost.tools.threads import ThreadSpawnTool
        from clio.toolhost.tools.toolshed import ToolsListTool, ToolsRegisterTool, ToolsRunTool

        for tool in (
            ShellRunTool(), ShellJobTool(),
            ShellSessionOpenTool(), ShellSessionWriteTool(), ShellSessionReadTool(),
            ShellSessionCloseTool(),
            FsReadTool(), FsWriteTool(), FsEditTool(), FsMoveTool(), FsDeleteTool(),
            FsListTool(), FsStatTool(), FsMkdirTool(),
            CodeExecTool(), CodeResetTool(),
            HttpTool(),
            CommSendTool(),
            MemoryRecordTool(), MemorySearchTool(), MemoryRememberFactTool(),
            OpenQuestionTool(), RandomMemoryTool(),
            HistoryBrowseTool(),
            IntentCreateTool(), IntentGetTool(), IntentListTool(), IntentUpdateTool(),
            IntentCloseTool(),
            ThreadSpawnTool(),
            ScheduleAtTool(), ScheduleEveryTool(), ScheduleCancelTool(),
            ToolsRegisterTool(), ToolsRunTool(), ToolsListTool(),
            SecretsSetTool(), SecretsGetTool(), SecretsListTool(),
            BrowserVisitTool(), BrowserClickTool(), BrowserTypeTool(), BrowserExtractTool(),
            BrowserScreenshotTool(), BrowserCloseTool(),
            DesktopKeyTool(), DesktopTypeTool(), DesktopClickTool(), DesktopMoveTool(),
            DesktopScreenshotTool(), DesktopWindowsTool(),
            SubagentStatusTool(), FoundryRunTool(), ApexRunTool(), AxiomRunTool(),
        ):
            self.register(tool)

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        started = time.monotonic()
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False, error_code=ToolErrorCode.NOT_FOUND,
                error=f"unknown tool: {name}",
            )
        journal_id: str | None = None
        if ctx.idempotency_key:
            try:
                journal_id = await self.journal.begin(
                    thread_id=ctx.thread_id, intention_id=ctx.intention_id, tool=name,
                    args_redacted=_redacted_args(name, args), reason=ctx.reason,
                    idempotency_key=ctx.idempotency_key,
                    side_effects=sorted(classify_tool(name, args).categories),
                    project_id=ctx.project_id, root_intention_id=ctx.root_intention_id,
                )
            except DuplicateAction as exc:
                return self._replay(name, exc.prior, started)
        else:
            journal_id = await self.journal.begin(
                thread_id=ctx.thread_id, intention_id=ctx.intention_id, tool=name,
                args_redacted=_redacted_args(name, args), reason=ctx.reason,
                idempotency_key=None,
                side_effects=sorted(classify_tool(name, args).categories),
                project_id=ctx.project_id, root_intention_id=ctx.root_intention_id,
            )
        try:
            decision = await self.gate.review(
                name, args,
                reason=ctx.reason or "unspecified",
                intention_id=ctx.intention_id,
                commissioned_by=ctx.commissioned_by,
                thread_id=ctx.thread_id,
                inside_subagent=ctx.inside_subagent,
                project_id=ctx.project_id,
                workspace_lease_id=ctx.workspace_lease_id,
            )
            if not decision.allowed:
                await self._finish(journal_id, "rejected", {"reason": decision.reason}, None)
                metrics.observe_action(name, "rejected")
                await self._notify(decision)
                return ToolResult(
                    ok=False, error_code=ToolErrorCode.GUARDIAN_REJECT,
                    error=decision.reason, journal_id=journal_id,
                )
            run_tool = decision.transformed_tool or name
            run_args = decision.transformed_args if decision.transformed_args is not None else args
            run_impl = self.tools.get(run_tool)
            if run_impl is None:
                await self._finish(journal_id, "failed", {"error": f"transformed tool missing: {run_tool}"}, None)
                return ToolResult(ok=False, error_code=ToolErrorCode.NOT_FOUND,
                                  error=f"transformed tool missing: {run_tool}")
            try:
                timeout = ctx.timeout_s or tool.timeout_s
                content = await asyncio.wait_for(run_impl.run(run_args, ctx, self), timeout=timeout)
            except TimeoutError:
                await self._finish(journal_id, "timeout", {"error": "tool timeout"}, decision.undo_ref)
                metrics.observe_action(name, "timeout")
                return ToolResult(
                    ok=False, error_code=ToolErrorCode.TIMEOUT,
                    error=f"tool timed out after {ctx.timeout_s or tool.timeout_s}s",
                    journal_id=journal_id,
                )
            except Exception as exc:
                await self._finish(journal_id, "failed", {"error": str(exc)[:500]}, decision.undo_ref)
                metrics.observe_action(name, "failed")
                logger.exception("tool.failed tool=%s", name)
                return ToolResult(
                    ok=False, error_code=ToolErrorCode.EXECUTION,
                    error=str(exc), journal_id=journal_id,
                )
            # A tool reports its own outcome by returning a `ToolResult`. Every
            # other tool returns a bare value, which means success by having
            # returned at all -- but a tool that says `ok=False` in the value it
            # returned has said the work did not happen, and overwriting that
            # with `ok=True` here is what turned a failed sub-agent run into a
            # journalled success and a plan step marked done over work that
            # never occurred. The envelope now reports what the tool reported,
            # and the digest records the tool's payload rather than the envelope
            # it arrived in -- the digest is what a later replay reads back.
            if isinstance(content, ToolResult):
                inner = content
                digest = {"summary": _digest(inner.content if inner.ok else inner.error)}
                await self._finish(
                    journal_id, STATUS_OK if inner.ok else "failed",
                    digest, inner.undo_ref or decision.undo_ref,
                )
                metrics.observe_action(name, STATUS_OK if inner.ok else "failed")
                if inner.ok and ctx.commissioned_by:
                    await self._auto_register_commission(name, args, ctx)
                await self._publish_action(
                    journal_id, name, STATUS_OK if inner.ok else "failed",
                )
                return inner.model_copy(update={
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "journal_id": journal_id,
                })
            digest = {"summary": _digest(content)}
            await self._finish(journal_id, "ok", digest, decision.undo_ref)
            metrics.observe_action(name, "ok")
            if ctx.commissioned_by:
                await self._auto_register_commission(name, args, ctx)
            await self._publish_action(journal_id, name, "ok")
            return ToolResult(
                ok=True, content=content,
                duration_ms=int((time.monotonic() - started) * 1000),
                undo_ref=decision.undo_ref, journal_id=journal_id,
            )
        except Exception as exc:
            logger.exception("toolhost.execute_failed tool=%s", name)
            await self._finish(journal_id, "failed", {"error": str(exc)[:500]}, None)
            return ToolResult(ok=False, error_code=ToolErrorCode.EXECUTION, error=str(exc))

    def _replay(self, name: str, prior: dict[str, Any], started: float) -> ToolResult:
        """Answer a duplicate call from the journal instead of re-executing.

        The journal hands back every prior attempt on this key, not only the
        successful ones, because an interrupted attempt must not be re-run
        blind and a failed one must not be reported as a success. So the `ok` on
        the envelope is read off the prior row rather than assumed:

        * `ok` -- the work happened. This is the idempotency guarantee: the
          second call returns the first call's answer instead of doing it twice.
        * `failed` / `timeout` / `rejected` -- a decided non-success, released by
          the journal so the retry proceeds; reaching here means the retry is
          being told what happened rather than re-run blindly.
        * `running` / `interrupted` -- the attempt may or may not have taken
          effect. Reported as a failure carrying that, because `ok=True` here
          would mark a step done over work that may never have run.
        """
        status = str(prior.get("status") or "")
        digest = prior.get("result_digest")
        if isinstance(digest, str):
            try:
                digest = json.loads(digest)
            except ValueError:
                digest = {"summary": digest}
        prior = {**prior, "result_digest": digest}
        metrics.observe_action(name, f"replay_{status or 'unknown'}")
        if status == STATUS_OK:
            return ToolResult(
                ok=True, content={"idempotent": True, "prior": prior},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        summary = (digest or {}).get("summary") if isinstance(digest, dict) else None
        error = (
            f"a prior attempt of {name} ended as {status or 'an unknown status'}"
            + (f": {summary}" if summary else "")
            + "; this call did not run"
        )
        if status in (STATUS_INTERRUPTED, "running"):
            # The distinction the caller cannot recover on its own: this is not
            # known to have failed, it is known not to be known.
            error += ". Its effect is unknown - inspect before retrying"
        return ToolResult(
            ok=False, content={"prior": prior}, error_code=ToolErrorCode.EXECUTION,
            error=error,
        )

    async def _finish(self, journal_id: str | None, status: str, digest: dict[str, Any],
                      undo_ref: str | None) -> None:
        if journal_id is None:
            return
        # NA-P3.4: the disposition rides in the digest, so a row read back later
        # says what kind of ending it was without anyone re-deriving it from the
        # status vocabulary this module happens to use today.
        digest = {**digest, "disposition": STATUS_DISPOSITIONS.get(status, "error")}
        try:
            await self.journal.end(journal_id, status=status, result_digest=digest, undo_ref=undo_ref)
        except Exception:
            logger.exception("journal.end_failed")

    async def _notify(self, decision: GateDecision) -> None:
        if decision.notify is None:
            return
        sender = self.services.get("comms_send")
        if sender is None:
            return
        try:
            await sender(
                person_id=decision.notify.get("person_id"),
                channel="web",
                text=decision.notify.get("text", ""),
                urgency=decision.notify.get("urgency", "high"),
                conversation_key=None,
                in_reply_to=None,
            )
        except Exception:
            logger.exception("toolhost.notify_failed")

    async def _auto_register_commission(self, name: str, args: dict[str, Any], ctx: ToolContext) -> None:
        registry = self.services.get("registry")
        if registry is None:
            return
        target = args.get("path") or args.get("to_path")
        if not target:
            return
        try:
            await registry.auto_register_write(
                target, commissioned_by=ctx.commissioned_by, intention_id=ctx.intention_id,
                project_id=ctx.project_id,
            )
        except Exception:
            logger.exception("toolhost.auto_register_failed")

    async def _publish_action(self, journal_id: str | None, name: str, status: str) -> None:
        if self.bus is None or journal_id is None:
            return
        try:
            await self.bus.publish(EventKind.ACTION_UPDATE, {
                "id": journal_id, "tool": name, "status": status,
                # The distinguishable reason (NA-P3.1/3.4): every action update
                # names the kind of ending it carries, so a surface can show
                # "limit reached" as a wait and "error" as a failure instead of
                # reading both as the same word.
                "disposition": STATUS_DISPOSITIONS.get(status, "error"),
                "intention_id": None, "ts": time.time(),
            })
        except Exception:
            logger.exception("toolhost.publish_failed")

    async def aclose(self) -> None:
        self.watcher_service.stop_all()
        await self.browser_pool.close_all()
        for sid in list(self.pty_manager.sessions):
            self.pty_manager.close_session(sid)


def _digest(content: Any) -> Any:
    try:
        text = json.dumps(content, default=str)
    except (TypeError, ValueError):
        text = str(content)
    if len(text) > 2000:
        return text[:2000] + "…"
    return content


def _redacted_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return redact(dict(args))


def create_toolhost_app(host: Toolhost) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        # `Toolhost.aclose` has existed since the first version of this process
        # and nothing has ever called it, which is why a toolhost the supervisor
        # restarted left its Chromium sessions behind: the pool was closed by the
        # interpreter dying, not by anything deciding to close it. Under the
        # supervisor that is a real orphan — the new toolhost starts straight
        # away, and the browsers the old one was holding are not in its process
        # group, so nothing else reaps them either.
        #
        # Swallowed, because an exception here would replace a clean shutdown
        # with a stack trace and leave precisely the orphans this closes.
        try:
            await host.aclose()
        except Exception:
            logger.exception("toolhost.aclose_failed")

    app = FastAPI(title=f"{PRODUCT_NAME} toolhost", version=__version__, lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "tools": len(host.tools)}

    @app.get("/tools")
    async def catalog() -> list[dict[str, Any]]:
        return host.catalog()

    @app.post("/execute")
    async def execute(request: ExecuteRequest) -> ToolResult:
        return await host.execute(request.tool, request.args, request.context)

    @app.get("/approvals")
    async def list_approvals() -> list[dict[str, Any]]:
        manager = getattr(host.gate, "approvals", None)
        return manager.list_pending() if manager is not None else []

    @app.post("/approvals/{approval_id}/approve")
    async def approve(approval_id: str) -> dict[str, Any]:
        manager = getattr(host.gate, "approvals", None)
        if manager is None:
            return {"ok": False, "error": "no approval channel"}
        request = manager.approve(approval_id, by="owner")
        return {"ok": request is not None, "approval": request.to_dict() if request else None}

    @app.post("/approvals/{approval_id}/deny")
    async def deny(approval_id: str) -> dict[str, Any]:
        manager = getattr(host.gate, "approvals", None)
        if manager is None:
            return {"ok": False, "error": "no approval channel"}
        request = manager.deny(approval_id, by="owner")
        return {"ok": request is not None, "approval": request.to_dict() if request else None}

    return app
