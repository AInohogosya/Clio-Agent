"""clio-api: the control plane, ClioUI streams, takeover and OAuth callbacks."""
from __future__ import annotations

from typing import Any

import httpx

from clio.api.app import ControlPlaneFacade, create_app
from clio.sandbox.computer import TakeoverController


class FakeControl:
    def __init__(self) -> None:
        self.paused = False
        self.stopped = False
        self.safety_level = 3
        self.applied: list[tuple[str, str]] = []

    async def apply_command(self, action: str, by: str) -> None:
        self.applied.append((action, by))
        if action in ("stop", "emergency_stop"):
            self.stopped = True


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict, str | None]] = []
        self.handlers: dict[str, list[Any]] = {}

    async def publish(self, kind: str, payload: dict | None = None, *, lane: str | None = None, **_: Any) -> int:
        self.published.append((kind, payload or {}, lane))
        return len(self.published)

    def subscribe(self, kind: str | None, handler: Any) -> None:
        self.handlers.setdefault(kind or "*", []).append(handler)

    def unsubscribe(self, kind: str | None, handler: Any) -> None:
        self.handlers.get(kind or "*", []).remove(handler)


class FakeLife:
    active_project_id = "11111111-1111-1111-1111-111111111111"
    cycles = 42

    class focus:  # noqa: N801 - shaped like the runtime's focus object
        title = "Helmet search"


class FakeRuntime:
    def __init__(self) -> None:
        self.control = FakeControl()
        self.bus = FakeBus()
        self.life = FakeLife()
        self.takeover = TakeoverController()
        self.connectors = None
        self.memory = None
        self.guardian = None


def _client(facade: ControlPlaneFacade) -> httpx.AsyncClient:
    app = create_app(facade)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://api.test")


async def test_health_and_status_over_http():
    runtime = FakeRuntime()
    async with _client(ControlPlaneFacade(runtime)) as client:
        health = await client.get("/api/health")
        assert health.json() == {"ok": True, "service": "clio-api"}
        status = (await client.get("/api/status")).json()
        assert status["agent"] == "running"
        assert status["focus"] == "Helmet search"
        assert status["cycles"] == 42


async def test_a_control_verb_is_applied_and_published_out_of_band():
    runtime = FakeRuntime()
    async with _client(ControlPlaneFacade(runtime)) as client:
        response = await client.post("/api/control/pause", json={"by": "phone"})
        assert response.json()["ok"] is True
        assert runtime.control.applied == [("pause", "phone")]
        kind, payload, lane = runtime.bus.published[-1]
        assert kind == "control.update" and lane == "control"
        assert payload["action"] == "pause"


async def test_an_unknown_control_verb_is_refused():
    runtime = FakeRuntime()
    async with _client(ControlPlaneFacade(runtime)) as client:
        response = await client.post("/api/control/self_destruct")
        assert response.status_code == 400


async def test_takeover_take_and_hand_back_over_http():
    runtime = FakeRuntime()
    async with _client(ControlPlaneFacade(runtime)) as client:
        take = await client.post("/api/sandboxes/sbx-1/control/take", json={"requester": "owner"})
        assert take.status_code == 200
        token = take.json()["token"]
        assert runtime.takeover.holder("sbx-1") == "owner"
        # A second person cannot take it while it is held.
        again = await client.post("/api/sandboxes/sbx-1/control/take", json={"requester": "other"})
        assert again.json()["token"] == token
        back = await client.post("/api/sandboxes/sbx-1/control/hand-back", json={"token": token})
        assert back.json()["ok"] is True
        assert runtime.takeover.holder("sbx-1") is None
        # With nobody holding it, a hand-back is a 409 — its token is gone.
        stale = await client.post("/api/sandboxes/sbx-1/control/hand-back", json={"token": token})
        assert stale.status_code == 409


async def test_a_stateless_replica_asks_the_core_over_the_bus_for_takeover():
    bus = FakeBus()

    async def core_reply(kind: str, payload: dict, lane: str | None) -> None:
        if kind == "control.update" and payload.get("action") == "sandbox_take":
            for handler in bus.handlers.get("sandbox.control", []):
                result = handler(type("E", (), {"payload": {
                    "request_id": payload["request_id"], "sandbox_id": payload["sandbox_id"],
                    "granted": True, "token": "tok-from-core", "drained_events": 2,
                }})())
                if hasattr(result, "__await__"):
                    await result

    facade = ControlPlaneFacade(db=None, config=None, bus=bus)
    original_publish = bus.publish

    async def publish(kind, payload=None, *, lane=None, **kw):
        result = await original_publish(kind, payload, lane=lane, **kw)
        await core_reply(kind, payload or {}, lane)
        return result

    bus.publish = publish  # type: ignore[method-assign]
    async with _client(facade) as client:
        take = await client.post("/api/sandboxes/sbx-9/control/take", json={"requester": "owner"})
        assert take.status_code == 200
        assert take.json()["token"] == "tok-from-core"
        assert take.json()["drained_events"] == 2


async def test_oauth_callback_without_a_broker_is_a_clear_503():
    facade = ControlPlaneFacade(db=None, config=None, bus=None)
    async with _client(facade) as client:
        response = await client.get("/api/connectors/github/callback?code=x&state=y")
        assert response.status_code == 503
        missing = await client.get("/api/connectors/github/callback?state=y")
        assert missing.status_code == 400


async def test_component_builders_refuse_unknown_kinds():
    runtime = FakeRuntime()
    facade = ControlPlaneFacade(runtime)
    async with _client(facade) as client:
        response = await client.get("/api/components/nonsense")
        assert response.status_code == 400
        # approval without an id → 404, not a crash
        response = await client.get("/api/components/approval")
        assert response.status_code in (404, 503)


async def test_component_action_completes_a_human_node():

    runtime = FakeRuntime()

    class Memory:
        def __init__(self) -> None:
            self.closed: list[tuple] = []

        async def close_intention(self, node_id, status, reason) -> None:
            self.closed.append((str(node_id), status, reason))

    runtime.memory = Memory()
    runtime.bus = FakeBus()
    facade = ControlPlaneFacade(runtime)
    async with _client(facade) as client:
        node = "22222222-2222-2222-2222-222222222222"
        response = await client.post(
            f"/api/components/action?action_id=complete:{node}",
            json={"resolves_human_node_id": node},
        )
        assert response.status_code == 200
        assert runtime.memory.closed == [(node, "done", "marked complete by the person")]
        # Undo rides the guardian lane.
        response = await client.post(
            "/api/components/action?action_id=undo:act-3", json={},
        )
        assert response.status_code == 200
        kind, payload, lane = runtime.bus.published[-1]
        assert kind == "guardian.undo" and lane == "guardian"
        assert payload["action_id"] == "act-3"


async def test_project_ledger_endpoint_uses_the_runtime_memory():
    runtime = FakeRuntime()

    class Ledger:
        project_id = "p1"

        def as_dict(self):
            return {"project_id": "p1", "nodes_done": 3, "nodes_total": 5}

    class Memory:
        async def get_project_progress_ledger(self, **_: Any):
            return [Ledger()]

    runtime.memory = Memory()
    async with _client(ControlPlaneFacade(runtime)) as client:
        rows = (await client.get("/api/projects")).json()
        assert rows == [{"project_id": "p1", "nodes_done": 3, "nodes_total": 5}]
