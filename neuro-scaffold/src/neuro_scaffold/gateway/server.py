"""FastAPI gateway server for Clio Agent integration."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from neuro_scaffold.agent.models import AgentPhase, AgentState
from neuro_scaffold.agent.state_machine import AgentStateMachine, ToolRegistry
from neuro_scaffold.config.settings import Settings, get_settings
from neuro_scaffold.security.auth import (
    AuthManager,
    AuthenticationError,
    SessionExpiredError,
    SessionManager,
)

logger = structlog.get_logger(__name__)
_security = HTTPBearer(auto_error=False)


class GatewayState:
    """Shared state for the gateway."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.auth_manager = AuthManager(settings)
        self.session_manager = SessionManager(settings)
        self.active_agents: dict[str, AgentStateMachine] = {}
        self.tool_registry = ToolRegistry()


_gateway_state: GatewayState | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global _gateway_state
    settings = get_settings()
    _gateway_state = GatewayState(settings)
    logger.info("Neuro-Scaffold gateway starting", port=settings.gateway_port)
    yield
    logger.info("Neuro-Scaffold gateway shutting down")
    _gateway_state = None


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Neuro-Scaffold Gateway",
        version="1.0.0",
        description="AI coding agent subagent gateway for Clio Agent",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app


app = create_app()


def get_gateway_state() -> GatewayState:
    if _gateway_state is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway not initialized",
        )
    return _gateway_state


async def verify_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_security),
    gw: GatewayState = Depends(get_gateway_state),
) -> dict[str, Any]:
    """Verify JWT token from Authorization header."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )
    try:
        payload = gw.auth_manager.verify_token(credentials.credentials)
        return payload
    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    gw = get_gateway_state()
    return {
        "status": "healthy",
        "version": "1.0.0",
        "active_sessions": gw.session_manager.active_session_count,
        "active_agents": len(gw.active_agents),
    }


@app.post("/auth")
async def authenticate(request: Request) -> dict[str, Any]:
    """Authenticate with API key and get a JWT token."""
    gw = get_gateway_state()
    body = await request.json()
    api_key = body.get("api_key", "")
    agent_id = body.get("agent_id", "unknown")
    permissions = body.get("permissions", ["read", "execute"])

    try:
        token = gw.auth_manager.authenticate(
            __import__("neuro_scaffold.agent.models", fromlist=["AuthPayload"]).AuthPayload(
                api_key=api_key,
                agent_id=agent_id,
                task="",
                permissions=permissions,
            )
        )
        session = gw.session_manager.create_session(agent_id, permissions)
        return {
            "token": token,
            "session_id": session.session_id,
            "expires_at": session.expires_at.isoformat(),
        }
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )


@app.post("/agent/spawn")
async def spawn_agent(
    request: Request,
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    """Spawn a new agent instance for a task."""
    gw = get_gateway_state()
    body = await request.json()
    task = body.get("task", "")
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task is required",
        )

    agent_id = body.get("agent_id", f"agent-{len(gw.active_agents) + 1}")
    agent = AgentStateMachine(gw.settings, gw.tool_registry)
    await agent.initialize(task)
    gw.active_agents[agent_id] = agent

    logger.info("Agent spawned", agent_id=agent_id, task=task[:100])
    return {
        "agent_id": agent_id,
        "session_id": agent.state.session_id,
        "phase": agent.state.phase.value,
        "plan_steps": len(agent.state.scratchpad.plan),
    }


@app.post("/agent/{agent_id}/step")
async def agent_step(
    agent_id: str,
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    """Execute one iteration of the agent loop."""
    gw = get_gateway_state()
    agent = gw.active_agents.get(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    state, done = await agent.run_iteration()
    return {
        "agent_id": agent_id,
        "phase": state.phase.value,
        "iteration": state.iteration,
        "done": done,
        "current_step": state.scratchpad.current_step,
        "total_steps": len(state.scratchpad.plan),
        "last_observation": state.scratchpad.observations[-1] if state.scratchpad.observations else None,
        "last_reflection": state.scratchpad.reflections[-1] if state.scratchpad.reflections else None,
    }


@app.post("/agent/{agent_id}/run")
async def agent_run(
    agent_id: str,
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    """Run the agent loop to completion."""
    gw = get_gateway_state()
    agent = gw.active_agents.get(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    final_state = await agent.run(agent.state.scratchpad.task)
    return {
        "agent_id": agent_id,
        "phase": final_state.phase.value,
        "iterations": final_state.iteration,
        "observations": final_state.scratchpad.observations,
        "reflections": final_state.scratchpad.reflections,
    }


@app.get("/agent/{agent_id}/status")
async def agent_status(
    agent_id: str,
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    """Get the current status of an agent."""
    gw = get_gateway_state()
    agent = gw.active_agents.get(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    state = agent.state
    return {
        "agent_id": agent_id,
        "phase": state.phase.value,
        "iteration": state.iteration,
        "error_count": state.error_count,
        "current_step": state.scratchpad.current_step,
        "total_steps": len(state.scratchpad.plan),
        "task": state.scratchpad.task,
    }


@app.delete("/agent/{agent_id}")
async def destroy_agent(
    agent_id: str,
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    """Destroy an agent instance."""
    gw = get_gateway_state()
    if agent_id not in gw.active_agents:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    del gw.active_agents[agent_id]
    logger.info("Agent destroyed", agent_id=agent_id)
    return {"agent_id": agent_id, "status": "destroyed"}


@app.get("/tools")
async def list_tools(
    auth: dict[str, Any] = Depends(verify_auth),
) -> dict[str, Any]:
    """List available tools."""
    gw = get_gateway_state()
    return {
        "tools": [t.value for t in gw.tool_registry.available_tools],
    }


def main() -> None:
    """Entry point for running the gateway server."""
    settings = get_settings()
    uvicorn.run(
        "neuro_scaffold.gateway.server:app",
        host=settings.gateway_host,
        port=settings.gateway_port,
        workers=settings.gateway_workers,
        log_level=settings.log_level.value.lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
