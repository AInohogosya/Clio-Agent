"""clio-api: the stateless control plane over REST and WebSocket.

One FastAPI application, mountable behind any number of replicas (the state
is all in PostgreSQL, scoped by RLS; the app holds none). It serves:

- **The control plane** — lifecycle verbs (`pause`, `resume`, `stop`), safety
  levels, and the read models the interfaces render: status, projects with
  their progress ledgers, recent messages, the journal, pending approvals.
- **ClioUI streams** — the typed components from `clio/ui/components.py`,
  rendered natively for Web/Desktop/Mobile clients, and the action endpoint
  that executes a card's decision (`/api/components/action`).
- **Live screen takeover** — `POST /api/sandboxes/{id}/control/take` and
  `.../control/hand-back`, behind the Action Barrier
  (`clio/sandbox/computer.py`). The display itself streams over the existing
  bridge; these endpoints are the control half: who holds the sandbox.
- **OAuth callbacks** — `GET /api/connectors/{provider}/callback`, the one
  interactive step of the connector broker, completing authorization for a
  person who clicked the URL.

The app is deliberately thin: it validates, it scopes, it forwards. Nothing
here executes a tool directly — tool execution lives in the toolhost behind
the guardian — so a compromised API replica can post a control verb and read
what its space may read, and that is the whole of its power.
"""

from __future__ import annotations

# FastAPI's dependency-injection idiom is a call in the signature default.
# ruff: noqa: B008
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from clio.observability.logger import get_logger

logger = get_logger("clio.api")


class ControlPlaneFacade:
    """What clio-api needs from the runtime, as one narrow seam.

    The API is served in-process with the core (a task in `clio-core`) and/or
    as its own stateless replica (`clio-api`); either way it reaches the
    runtime through this facade rather than by holding its objects. A replica
    without a runtime in it serves read models from PostgreSQL and asks the
    core for the two operations that really are in-process — screen takeover
    and connector authorization — over the out-of-band control lane.
    """

    def __init__(self, runtime: Any = None, *, db: Any = None, config: Any = None,
                 bus: Any = None) -> None:
        self.runtime = runtime
        self.db = db
        self.config = config
        self.bus = bus

    async def _bus_round_trip(self, payload: dict[str, Any], *, expect: str,
                              timeout_s: float = 10.0) -> dict[str, Any]:
        """Ask the core a question over the bus and await its reply."""
        import asyncio
        import uuid

        if self.bus is None:
            raise HTTPException(status_code=503, detail="no runtime or bus connection on this replica")
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        def _on_reply(event: Any) -> None:
            payload = dict(event.payload or {})
            if payload.get("request_id") == request_id and not future.done():
                future.set_result(payload)

        self.bus.subscribe(expect, _on_reply)
        try:
            await self.bus.publish(
                "control.update", {**payload, "request_id": request_id}, lane="control",
            )
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="the core did not answer in time") from exc
        finally:
            self.bus.unsubscribe(expect, _on_reply)

    async def status(self) -> dict[str, Any]:
        if self.runtime is not None:
            life = getattr(self.runtime, "life", None)
            control = getattr(self.runtime, "control", None)
            return {
                "agent": "running",
                "paused": bool(getattr(control, "paused", False)),
                "stopped": bool(getattr(control, "stopped", False)),
                "safety_level": getattr(control, "safety_level", None),
                "focus": getattr(getattr(life, "focus", None), "title", None),
                "active_project_id": getattr(life, "active_project_id", None),
                "cycles": getattr(life, "cycles", None),
            }
        # A stateless replica: the answers live in the control row and the
        # heartbeat file, both of which this process can read for itself.
        if self.db is None:
            return {"agent": "unavailable", "detail": "no runtime and no database on this replica"}
        import json

        row = await self.db.fetchrow(
            "SELECT attrs FROM env_entities WHERE key = 'control.state' LIMIT 1"
        )
        attrs: dict[str, Any] = {}
        if row is not None:
            attrs = row["attrs"] if not isinstance(row["attrs"], str) else json.loads(row["attrs"] or "{}")
        heartbeat_age = None
        try:
            import time

            from clio.core.supervisor import CORE_HEARTBEAT_FILE

            heartbeat = self.config.paths.run_dir / CORE_HEARTBEAT_FILE
            heartbeat_age = round(time.time() - heartbeat.stat().st_mtime, 1)
        except Exception:
            heartbeat_age = None
        alive = heartbeat_age is not None and heartbeat_age < 1800
        return {
            "agent": "running" if alive else "not running",
            "paused": bool(attrs.get("paused", False)),
            "stopped": bool(attrs.get("stopped", False)),
            "safety_level": attrs.get("safety_level"),
            "heartbeat_age_s": heartbeat_age,
            "replica": "stateless",
        }

    async def apply_control(self, action: str, *, by: str) -> dict[str, Any]:
        if self.runtime is None:
            raise HTTPException(status_code=503, detail="no runtime connected")
        control = self.runtime.control
        await control.apply_command(action, by)
        # Out-of-band lane (control.update), so every process hears it at once.
        await self.runtime.bus.publish(
            "control.update", {"action": action, "by": by}, lane="control",
        )
        return {"ok": True, "action": action}

    async def projects(self) -> list[dict[str, Any]]:
        if self.runtime is not None:
            ledgers = await self.runtime.memory.get_project_progress_ledger(
                current_focus_project_id=getattr(self.runtime.life, "active_project_id", None),
            )
            return [ledger.as_dict() for ledger in ledgers]
        if self.db is None or self.config is None:
            return []
        from clio.gateway.embeddings import build_role_embedder
        from clio.memory.store import MemoryStore

        memory = MemoryStore(self.db, build_role_embedder("api", self.config), self.config)
        ledgers = await memory.get_project_progress_ledger()
        return [ledger.as_dict() for ledger in ledgers]

    async def component(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        """Build one ClioUI component from live state."""
        from clio.ui import components as ui

        if kind == "approval":
            request = None
            guardian = getattr(self.runtime, "guardian", None) if self.runtime else None
            approvals = getattr(guardian, "approvals", None) if guardian else None
            if approvals is not None:
                request = approvals.pending.get(params.get("request_id"))
            if request is None:
                raise HTTPException(status_code=404, detail="no such approval request")
            return ui.approval_card_from_request(request).as_dict()
        if kind == "rig":
            project_id = params.get("project_id")
            project = await self.runtime.memory.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="no such project")
            subgraph = await self.runtime.memory.get_intention_subgraph(
                project.get("root_intention_id")
            ) if project.get("root_intention_id") else {"intentions": []}
            nodes = [
                {
                    "id": str(node.id), "title": node.title, "status": node.status,
                    "worker": str(node.assigned_principal_id or ""),
                    "is_human_gate": str(getattr(node, "executor_kind", "")) == "human",
                }
                for node in subgraph.get("intentions", [])
            ]
            ledger = next(
                (item for item in await self.runtime.memory.get_project_progress_ledger()
                 if item.project_id == str(project_id)),
                None,
            )
            return ui.rig_explorer_from_state(project, nodes, ledger=ledger).as_dict()
        raise HTTPException(status_code=400, detail=f"unknown component kind {kind!r}")

    async def component_action(self, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a component's action: the same verbs the CLI spells."""
        verb, _, target = action_id.partition(":")
        if verb in ("approve_once", "reject", "approve_project"):
            approvals = self.runtime.guardian.approvals
            request = approvals.pending.get(target)
            if request is None:
                raise HTTPException(status_code=404, detail="no such approval request")
            if verb == "reject":
                request.deny("rejected from a card")
            elif verb == "approve_project":
                if hasattr(approvals, "approve_for_project"):
                    approvals.approve_for_project(request, payload.get("project_id"))
                request.approve("approved for the project from a card")
            else:
                request.approve("approved once from a card")
            return {"ok": True, "action": action_id}
        if verb == "complete":
            node_id = target or str(payload.get("resolves_human_node_id") or "")
            if not node_id:
                raise HTTPException(status_code=400, detail="no node to complete")
            from uuid import UUID

            await self.runtime.memory.close_intention(
                UUID(node_id), "done", "marked complete by the person",
            )
            return {"ok": True, "action": action_id}
        if verb == "undo":
            await self.runtime.bus.publish(
                "guardian.undo", {"action_id": target, "requested_by": "ui"}, lane="guardian",
            )
            return {"ok": True, "action": action_id}
        raise HTTPException(status_code=400, detail=f"unknown action {action_id!r}")

    async def take_screen(self, sandbox_id: str, *, requester: str) -> dict[str, Any]:
        controller = getattr(self.runtime, "takeover", None) if self.runtime else None
        if controller is not None:
            session = await controller.take(sandbox_id, requester)
            return {"sandbox_id": sandbox_id, "token": session.token,
                    "drained_events": session.drained_events, "holder": requester}
        # A stateless replica: the takeover controller — and the Action
        # Barrier that must drain in-flight input — lives with the virtual
        # computer in the core. The ask travels the out-of-band lane and the
        # grant travels back the same way.
        reply = await self._bus_round_trip(
            {"action": "sandbox_take", "sandbox_id": sandbox_id, "requester": requester},
            expect="sandbox.control",
        )
        if not reply.get("granted"):
            raise HTTPException(status_code=409, detail=reply.get("reason") or "takeover refused")
        return {"sandbox_id": sandbox_id, "token": reply.get("token"),
                "drained_events": reply.get("drained_events", 0), "holder": requester}

    async def hand_back_screen(self, sandbox_id: str, token: str) -> dict[str, Any]:
        controller = getattr(self.runtime, "takeover", None) if self.runtime else None
        if controller is not None:
            ok = await controller.hand_back(sandbox_id, token)
            if not ok:
                raise HTTPException(status_code=409, detail="this token does not hold the sandbox")
            return {"sandbox_id": sandbox_id, "ok": True}
        reply = await self._bus_round_trip(
            {"action": "sandbox_handback", "sandbox_id": sandbox_id, "token": token},
            expect="sandbox.control",
        )
        if not reply.get("granted"):
            raise HTTPException(status_code=409, detail=reply.get("reason") or "hand-back refused")
        return {"sandbox_id": sandbox_id, "ok": True}

    async def complete_oauth(self, provider: str, code: str, state: str) -> dict[str, Any]:
        broker = getattr(self.runtime, "connectors", None) if self.runtime else None
        if broker is None and self.db is not None and self.config is not None:
            # The broker is database-backed; a stateless replica can complete
            # the exchange itself. Token custody is the envelope's, not the
            # process's, which is what makes that true.
            from clio.security.envelope import build_envelope
            from clio.toolhost.connectors import ConnectorBroker, providers_from_config

            broker = ConnectorBroker(
                self.db, build_envelope(self.db, self.config),
                providers=providers_from_config(self.config),
                redirect_base=self.config.connectors.redirect_base,
            )
        if broker is None:
            raise HTTPException(status_code=503, detail="no connector broker on this replica")
        return await broker.complete_authorization(provider, code, state)


def create_app(facade: ControlPlaneFacade | None = None) -> FastAPI:
    facade = facade or ControlPlaneFacade()
    app = FastAPI(
        title="Clio Agent 3.1 API",
        version="3.1",
        description="Control plane, ClioUI streams, screen takeover and OAuth callbacks.",
    )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "clio-api"}

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(await facade.status())

    @app.post("/api/control/{action}")  # noqa: B008
    async def control(action: str, request: Request,
                      body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        if action not in ("pause", "resume", "stop", "emergency_stop", "preview_on", "preview_off", "pause_all"):
            raise HTTPException(status_code=400, detail=f"unknown control action {action!r}")
        by = str(body.get("by") or (request.client.host if request.client else "api"))
        return await facade.apply_control(action, by=by)

    @app.get("/api/projects")
    async def projects() -> list[dict[str, Any]]:
        return await facade.projects()

    @app.get("/api/components/{kind}")
    async def component(kind: str, request: Request) -> dict[str, Any]:
        params = dict(request.query_params)
        return await facade.component(kind, params)

    @app.post("/api/components/action")
    async def component_action(
        action_id: str = Query(...),
        payload: dict[str, Any] = Body(default={}),
    ) -> dict[str, Any]:
        return await facade.component_action(action_id, payload)

    @app.post("/api/sandboxes/{sandbox_id}/control/take")
    async def takeover(sandbox_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        requester = str(body.get("requester") or "owner")
        return await facade.take_screen(sandbox_id, requester=requester)

    @app.post("/api/sandboxes/{sandbox_id}/control/hand-back")
    async def hand_back(sandbox_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        token = str(body.get("token") or "")
        return await facade.hand_back_screen(sandbox_id, token)

    @app.get("/api/connectors/{provider}/callback")
    async def oauth_callback(provider: str, code: str = Query(""), state: str = Query("")) -> dict[str, Any]:
        if not code:
            raise HTTPException(status_code=400, detail="missing ?code")
        return await facade.complete_oauth(provider, code, state)

    return app


__all__ = ["ControlPlaneFacade", "create_app"]
