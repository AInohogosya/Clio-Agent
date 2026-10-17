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

from ethos import PRODUCT_NAME, __version__
from ethos.config import EthosConfig
from ethos.gateway.secrets import redact
from ethos.guardian.classifier import classify_tool
from ethos.guardian.gate import GateDecision, GuardianGate
from ethos.guardian.journal import (
    STATUS_INTERRUPTED,
    STATUS_OK,
    ActionJournal,
    DuplicateAction,
)
from ethos.observability import metrics
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind
from ethos.schemas.tools import ToolErrorCode, ToolResult

logger = get_logger("ethos.toolhost")


class ToolContext(BaseModel):
    thread_id: str = "self"
    intention_id: str | None = None
    reason: str = ""
    idempotency_key: str | None = None
    commissioned_by: str | None = None
    consequential: bool = False
    cwd: str | None = None
    timeout_s: float | None = None


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
        config: EthosConfig,
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
        from ethos.toolhost.browser import BrowserPool
        from ethos.toolhost.desktop import DesktopController
        from ethos.toolhost.kernels import KernelManager
        from ethos.toolhost.pty_session import PtyManager
        from ethos.toolhost.watchers import WatcherService

        self.pty_manager = PtyManager()
        self.kernel_manager = KernelManager()
        self.browser_pool = BrowserPool()
        self.watcher_service = WatcherService()
        self.desktop = DesktopController()
        self._register_builtin_tools()

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in sorted(self.tools.values(), key=lambda t: t.name)
        ]

    def service(self, name: str) -> Any:
        if name not in self.services:
            raise RuntimeError(f"service '{name}' is not available in this toolhost")
        return self.services[name]

    def _register_builtin_tools(self) -> None:
        from ethos.subagents.tools import ApexRunTool, FoundryRunTool, SubagentStatusTool
        from ethos.toolhost.tools.browser_tools import (
            BrowserClickTool,
            BrowserCloseTool,
            BrowserExtractTool,
            BrowserScreenshotTool,
            BrowserTypeTool,
            BrowserVisitTool,
        )
        from ethos.toolhost.tools.code import CodeExecTool, CodeResetTool
        from ethos.toolhost.tools.comms import CommSendTool
        from ethos.toolhost.tools.desktop_tools import (
            DesktopClickTool,
            DesktopKeyTool,
            DesktopMoveTool,
            DesktopScreenshotTool,
            DesktopTypeTool,
            DesktopWindowsTool,
        )
        from ethos.toolhost.tools.fs import (
            FsDeleteTool,
            FsEditTool,
            FsListTool,
            FsMoveTool,
            FsReadTool,
            FsStatTool,
            FsWriteTool,
        )
        from ethos.toolhost.tools.http import HttpTool
        from ethos.toolhost.tools.intent import (
            IntentCloseTool,
            IntentCreateTool,
            IntentGetTool,
            IntentListTool,
            IntentUpdateTool,
        )
        from ethos.toolhost.tools.memory import (
            MemoryRecordTool,
            MemoryRememberFactTool,
            MemorySearchTool,
            OpenQuestionTool,
            RandomMemoryTool,
        )
        from ethos.toolhost.tools.schedule import (
            ScheduleAtTool,
            ScheduleCancelTool,
            ScheduleEveryTool,
        )
        from ethos.toolhost.tools.secrets import SecretsGetTool, SecretsListTool, SecretsSetTool
        from ethos.toolhost.tools.shell import (
            ShellJobTool,
            ShellRunTool,
            ShellSessionCloseTool,
            ShellSessionOpenTool,
            ShellSessionReadTool,
            ShellSessionWriteTool,
        )
        from ethos.toolhost.tools.threads import ThreadSpawnTool
        from ethos.toolhost.tools.toolshed import ToolsListTool, ToolsRegisterTool, ToolsRunTool

        for tool in (
            ShellRunTool(), ShellJobTool(),
            ShellSessionOpenTool(), ShellSessionWriteTool(), ShellSessionReadTool(),
            ShellSessionCloseTool(),
            FsReadTool(), FsWriteTool(), FsEditTool(), FsMoveTool(), FsDeleteTool(),
            FsListTool(), FsStatTool(),
            CodeExecTool(), CodeResetTool(),
            HttpTool(),
            CommSendTool(),
            MemoryRecordTool(), MemorySearchTool(), MemoryRememberFactTool(),
            OpenQuestionTool(), RandomMemoryTool(),
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
            SubagentStatusTool(), FoundryRunTool(), ApexRunTool(),
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
                )
            except DuplicateAction as exc:
                return self._replay(name, exc.prior, started)
        else:
            journal_id = await self.journal.begin(
                thread_id=ctx.thread_id, intention_id=ctx.intention_id, tool=name,
                args_redacted=_redacted_args(name, args), reason=ctx.reason,
                idempotency_key=None,
                side_effects=sorted(classify_tool(name, args).categories),
            )
        try:
            decision = await self.gate.review(
                name, args,
                reason=ctx.reason or "unspecified",
                intention_id=ctx.intention_id,
                commissioned_by=ctx.commissioned_by,
                thread_id=ctx.thread_id,
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
            )
        except Exception:
            logger.exception("toolhost.auto_register_failed")

    async def _publish_action(self, journal_id: str | None, name: str, status: str) -> None:
        if self.bus is None or journal_id is None:
            return
        try:
            await self.bus.publish(EventKind.ACTION_UPDATE, {
                "id": journal_id, "tool": name, "status": status,
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

    return app
