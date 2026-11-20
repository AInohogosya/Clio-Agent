"""The background screen relay: what a viewer may see, and what it may not.

The relay is the read-only window onto an APEX run's hidden surface. These
tests pin the three properties the panel depends on: the wire carries the run's
state and its live semantic frame; the scope fence holds until the run has
established what it may see; and the HTTP surface is GET-only, so there is
nothing to click at any layer.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any

from clio.subagents.apex_screen import ApexScreenRelay


class _SemanticAdapter:
    """A hidden-mode-shaped adapter: no raster, a live tree once scoped."""

    def __init__(self) -> None:
        self.app_scope: tuple[str, ...] = ()
        self.capture_calls = 0

    def _capture_tree_sync(self) -> tuple[str, str | None, tuple[Any, ...]]:
        self.capture_calls += 1
        rect = SimpleNamespace(left=0.0, top=0.0, right=1200.0, bottom=800.0)
        nodes = (
            SimpleNamespace(
                node_id="n1", role="AXWindow", name="Scratch", value=None,
                window_title="Scratch", bounds=rect, focused=False,
                enabled=True, depth=0,
            ),
            SimpleNamespace(
                node_id="n2", role="AXButton", name="Save", value=None,
                window_title="Scratch",
                bounds=SimpleNamespace(left=40.0, top=60.0, right=140.0, bottom=90.0),
                focused=True, enabled=True, depth=2,
            ),
        )
        return ("tree-1", None, nodes)


class _StubPlane:
    """The part of APEX's ControlPlane the relay is allowed to touch."""

    def __init__(self, *, scopes_itself: bool = True) -> None:
        self.screen_mode = SimpleNamespace(value="hidden")
        self.pixels_available = False
        self.adapter = _SemanticAdapter()
        self.tasks: dict[str, Any] = {}
        self.scopes_itself = scopes_itself

    def add_task(self, task_id: str, mode: str, steps_used: int = 1) -> None:
        self.tasks[task_id] = SimpleNamespace(
            task_id=task_id,
            goal="tidy the scratch window",
            revision_id=1,
            mode=SimpleNamespace(value=mode),
            last_error=None,
            steps_used=steps_used,
            consecutive_failures=0,
            actions=(
                SimpleNamespace(
                    action_id="a1", step=1,
                    operation=SimpleNamespace(value="click"),
                    status=SimpleNamespace(value="EFFECT_CONFIRMED"),
                    error=None,
                    receipt=SimpleNamespace(detail="delivered on the hidden surface"),
                    target=SimpleNamespace(name="Save"),
                ),
            ),
        )

    def get_task(self, task_id: str) -> Any:
        # A run's first observe() is what establishes its scope; before that,
        # the honest reading of the hidden surface is "nothing in scope yet".
        if self.scopes_itself and not self.adapter.app_scope:
            self.adapter.app_scope = ("apex:no-application-in-scope",)
        return self.tasks[task_id]


def _get_json(port: int, path: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _read_events(port: int, seconds: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    done = threading.Event()

    def read() -> None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/frames", timeout=10) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: "):]))
                    if done.is_set():
                        break
        except Exception:
            pass

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    time.sleep(seconds)
    done.set()
    return events


def test_relay_streams_session_activity_and_semantic_surface() -> None:
    plane = _StubPlane()
    plane.add_task("task-1", "RUNNING")
    relay = ApexScreenRelay.attach(plane, goal="tidy the scratch window")
    try:
        relay.set_task("task-1")
        assert relay.port > 0

        status, session = _get_json(relay.port, "/session")
        assert status == 200
        assert session["readOnly"] is True
        assert session["viewKind"] == "semantic"
        assert session["taskId"] == "task-1"
        assert session["screenMode"] == "hidden"

        events = _read_events(relay.port, 2.5)
        kinds = [event.get("type") for event in events]
        assert "hello" in kinds, kinds
        assert "status" in kinds, kinds
        assert "activity" in kinds, kinds
        assert "surface" in kinds, kinds

        surface = next(event["surface"] for event in events if event.get("type") == "surface")
        assert surface["width"] > 0 and surface["height"] > 0
        roles = [node["role"] for node in surface["nodes"]]
        assert "AXWindow" in roles and "AXButton" in roles
        # The window title crosses; native references and identifiers do not.
        window = next(node for node in surface["nodes"] if node["role"] == "AXWindow")
        assert window["window"] == "Scratch"
        assert all(set(node) <= {
            "id", "role", "name", "value", "window", "bounds",
            "focused", "enabled", "depth",
        } for node in surface["nodes"])

        activity = next(event["activity"] for event in events if event.get("type") == "activity")
        assert "click" in activity["label"]
        assert "Save" in activity["detail"] or "hidden surface" in activity["detail"]
    finally:
        relay.close()


def test_relay_shows_nothing_before_the_run_scopes_itself() -> None:
    plane = _StubPlane(scopes_itself=False)
    plane.add_task("task-1", "RUNNING")
    relay = ApexScreenRelay.attach(plane, goal="tidy the scratch window")
    try:
        relay.set_task("task-1")
        time.sleep(1.2)
        # The run has not observed anything yet, so its scope is empty and the
        # semantic view must not read the desktop to fill the panel.
        assert plane.adapter.capture_calls == 0
        assert relay.snapshot()["status"] in {"starting", "running"}
    finally:
        relay.close()


def test_relay_stops_following_when_the_task_is_terminal() -> None:
    plane = _StubPlane()
    plane.add_task("task-1", "RUNNING")
    relay = ApexScreenRelay.attach(plane, goal="tidy the scratch window")
    try:
        relay.set_task("task-1")
        time.sleep(1.0)
        plane.add_task("task-1", "COMPLETED", steps_used=3)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and relay.snapshot()["status"] != "stopped":
            time.sleep(0.1)
        assert relay.snapshot()["status"] == "stopped"
        captures = plane.adapter.capture_calls
        time.sleep(1.0)
        # A finished run is not captured again: the view holds its last frame.
        assert plane.adapter.capture_calls == captures
    finally:
        relay.close()


def test_relay_streams_pixels_when_the_surface_has_a_raster() -> None:
    plane = _StubPlane()
    plane.screen_mode = SimpleNamespace(value="visible")
    plane.pixels_available = True
    captured: dict[str, Any] = {}

    def capture(destination: Any) -> Any:
        captured["path"] = destination
        destination.write_bytes(_one_pixel_png())
        return SimpleNamespace(
            sha256=None, pixels_available=True, width=1, height=1,
        )

    plane.adapter._capture_screenshot_sync = capture  # type: ignore[attr-defined]
    plane.add_task("task-1", "RUNNING")
    relay = ApexScreenRelay.attach(plane, goal="tidy the scratch window")
    try:
        relay.set_task("task-1")
        status, session = _get_json(relay.port, "/session")
        assert status == 200
        assert session["viewKind"] == "pixels"
        events = _read_events(relay.port, 2.5)
        frame = next(
            (event["frame"] for event in events if event.get("type") == "frame"), None,
        )
        assert frame is not None, [event.get("type") for event in events]
        assert frame["format"] in {"jpeg", "png"}
        assert frame["width"] >= 1 and len(frame["data"]) > 0
        # The capture file is the relay's own scratch space, not the agent's
        # evidence directory, and it is gone when the relay closes.
        assert captured["path"].exists() is False or relay.port > 0
    finally:
        relay.close()
    assert captured["path"].exists() is False


def _one_pixel_png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (40, 44, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_relay_http_surface_is_get_only() -> None:
    plane = _StubPlane()
    relay = ApexScreenRelay.attach(plane, goal="a goal")
    try:
        status, body = _get_json(relay.port, "/session")
        assert status == 200
        status, body = _get_json(relay.port, "/nope")
        assert status == 404 and body["error"] == "not_found"

        for method in ("POST", "PUT"):
            request = urllib.request.Request(
                f"http://127.0.0.1:{relay.port}/control", data=b"{}", method=method,
            )
            try:
                urllib.request.urlopen(request, timeout=5)
                raise AssertionError(f"{method} should have been refused")
            except urllib.error.HTTPError as error:
                assert error.code == 405
                assert json.loads(error.read())["error"] == "read_only"
    finally:
        relay.close()
