"""The background screen: a read-only live view of an APEX sub-agent run.

When this agent delegates desktop work to APEX, the run happens on a surface
the person is not watching -- on macOS the driven application is hidden with
``kAXHidden`` and driven through accessibility, so nothing APEX does is ever
composited on the display in front of them. That is the safety property. Its
cost is that a person who wants to know what the agent is doing to their
machine has nothing to look at.

This module is the answer: while an in-process APEX run is under way, a small
loopback-only HTTP server streams that hidden surface to the interface's
Background Screen panel. Two honest kinds of picture exist, and the session
says which one it is serving:

* **pixels** -- where the surface has a raster target (visible mode anywhere;
  hidden mode on a Linux Xvfb framebuffer) the same platform adapter the agent
  reasons through is asked for a capture, and it is streamed JPEG-encoded.
* **semantic** -- where the surface has no raster target (macOS and Windows
  hidden mode) there is no picture to take, and none is invented. The live
  accessibility tree is streamed instead, and the panel renders its structure:
  the windows, controls and text the agent actually sees, in their real
  positions.

Three rules bound everything here, and they are why the file is small.

**The view is read-only at every layer.** The server registers GET routes and
nothing else -- there is no control endpoint to refuse, because a refusal is
still a route a confused client could aim at. The interface proxies exactly
these GETs, and the panel renders with pointer events disabled. A viewer that
cannot ask for input cannot get it.

**Nothing is shown before the agent may see it.** Hidden-mode perception is
scoped: before the run has isolated an application of its own, its first
observation is deliberately empty rather than a transcript of the user's
desktop. The view obeys the same fence -- until the controller's application
scope is established, no semantic frame leaves this process.

**A missing picture is stated, not substituted.** When the surface has no
pixels the session says so in ``detail``; when a capture fails the failure is
reported once. The viewer never paints a plausible rectangle over an absence.

The wire vocabulary is the desk-agent's (``hello``/``frame``/``activity``/
``status``/``keepalive``/``bye``, one JSON object per SSE ``data:`` line) with
one addition: the ``surface`` event, whose payload is the semantic mirror.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import queue
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from clio.env import env
from clio.observability.logger import get_logger

logger = get_logger("clio.subagents.apex_screen")

#: The loopback port the interface's proxy reaches this relay on. The relay
#: binds the first free port from here upward, so a second concurrent run is
#: still streamed -- on the next port, where the proxy's short scan finds it.
DEFAULT_PORT = 8788
PORT_SPAN = 8

#: How often the view is re-read while the run is working. A semantic capture
#: is a real accessibility enumeration, so it is bounded like the pixels path
#: is: often enough to watch, rarely enough to never contend with the agent.
DEFAULT_FPS = 2.0

#: A frame in hand is re-sent after this long even when nothing changed, so a
#: still background screen reads as still rather than as gone.
IDLE_REPEAT_SECONDS = 2.0

#: The step ledger is polled at the desk agent's cadence; a poll that misses a
#: transition reports the truth one beat late instead of a lie on time.
TASK_POLL_SECONDS = 0.25

#: How deep a viewer's queue may back up before frames are dropped rather than
#: buffered: a stale picture of a moving screen is worse than a short gap.
SUBSCRIBER_DEPTH = 8

#: Text bounds. The tree is already capped by APEX's own node budget; these
#: bound what one label can add to a frame that carries several hundred nodes.
MAX_TEXT = 160
MAX_NODES = 600
MAX_DEPTH = 12

#: Said once, in the session detail, when the surface has no raster target.
NO_PIXELS_DETAIL = (
    "this background surface has no pixels; the structure view is its live "
    "accessibility tree"
)

#: Desk-agent status vocabulary, so both ends of the stream say the same words.
_STATUS_FOR_TASK = {
    "NEW": "starting",
    "RUNNING": "running",
    "PAUSED": "paused",
    "STOPPING": "stopping",
    "STOPPED": "stopped",
    "COMPLETED": "stopped",
    "BLOCKED": "error",
    "BLOCKED_UNKNOWN": "error",
    "FAILED": "error",
}
_TERMINAL_TASK_MODES = frozenset(
    {"COMPLETED", "STOPPED", "BLOCKED", "BLOCKED_UNKNOWN", "FAILED"}
)
#: APEX operations as the feed names them; ``invoke`` is an accessibility
#: action, but it is what a watcher reads as the thing being pressed.
_KIND_FOR_OPERATION = {
    "click": "click",
    "type_text": "type",
    "press_keys": "key",
    "focus": "move",
    "scroll": "move",
    "invoke": "click",
    "launch_app": "launch",
    "await_condition": "wait",
}
_TERMINAL_ACTION_STATUSES = frozenset(
    {
        "EFFECT_CONFIRMED",
        "EFFECT_UNVERIFIED",
        "FAILED",
        "OUTCOME_UNKNOWN",
    }
)

_PLATFORMS = {
    "darwin": "macos",
    "win32": "windows",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean_text(value: object, limit: int = MAX_TEXT) -> str:
    """Text that is safe to put in a label, with control characters removed."""

    if value is None:
        return ""
    text = str(value)
    return "".join(
        character
        for character in text
        if character in "\t\n" or not (ord(character) < 32 or ord(character) == 0x7F)
    ).strip()[:limit]


def _platform_name() -> str:
    return _PLATFORMS.get(sys.platform, "linux")


class _Subscriber:
    """One connected viewer, drained through a bounded queue.

    A full queue drops the frame instead of blocking the run: the agent's loop
    must never wait on a slow reader. The closed event is what actually ends
    the streaming thread, because a full queue cannot always take the sentinel.
    """

    __slots__ = ("closed", "queue")

    def __init__(self) -> None:
        self.queue: queue.Queue[dict[str, object] | None] = queue.Queue(
            maxsize=SUBSCRIBER_DEPTH,
        )
        self.closed = threading.Event()

    def offer(self, event: dict[str, object]) -> None:
        if self.closed.is_set():
            return
        with contextlib.suppress(queue.Full):
            self.queue.put_nowait(event)

    def close(self) -> None:
        self.closed.set()
        with contextlib.suppress(queue.Full):
            self.queue.put_nowait(None)


def encode_frame(raw: bytes, edge: int, quality: int) -> dict[str, object] | None:
    """Base64 a capture in a format a browser can paint.

    JPEG keeps a loopback socket carrying several frames a second. When Pillow
    is unavailable the PNG is sent as captured rather than not at all: larger
    and slower, and still the real screen.
    """

    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as opened:
            image = opened.convert("RGB")
            longest = max(image.size)
            if longest > edge:
                scale = edge / float(longest)
                image = image.resize(
                    (
                        max(1, round(image.width * scale)),
                        max(1, round(image.height * scale)),
                    ),
                    Image.Resampling.LANCZOS,
                )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=False)
        payload = buffer.getvalue()
        image_format = "jpeg"
        with Image.open(io.BytesIO(payload)) as sized:
            width, height = sized.size
    except Exception as error:  # Re-encoding is an optimisation, not a gate.
        logger.debug("jpeg encode unavailable, trying png: %s", error)
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as sized:
                width, height = sized.size
        except Exception:
            return None
        payload = raw
        image_format = "png"

    if not payload or width < 1 or height < 1:
        return None
    return {
        "width": int(width),
        "height": int(height),
        "format": image_format,
        "data": base64.b64encode(payload).decode("ascii"),
    }


class ApexScreenRelay:
    """Streams one APEX run's background surface to the interface.

    Construction touches no native resource and binds no port; ``attach``
    starts the server and the pumps, and ``close`` undoes both. Everything in
    between is read-only: perception reads, the ledger is polled, and the only
    write is to this process's own temporary capture file.
    """

    def __init__(
        self,
        plane: Any,
        *,
        goal: str = "",
        screen_mode: str = "hidden",
    ) -> None:
        self._plane = plane
        self._screen_mode = screen_mode
        pixels = bool(getattr(plane, "pixels_available", False))
        self._session: dict[str, object] = {
            "id": "",
            "mode": "sandbox" if screen_mode == "hidden" else "direct",
            "status": "starting",
            "platform": _platform_name(),
            "branch": "apex-local",
            "source": "agent",
            "detail": _clean_text(goal, 400),
            "startedAt": 0,
            "updatedAt": _now_ms(),
            "goal": _clean_text(goal, 2000),
            "taskId": "",
            "revisionId": 0,
            "stepsUsed": 0,
            # The panel reads this before it draws anything: the view is a
            # window, not a remote control, and the session is where that is
            # said on the wire rather than only in the interface's markup.
            "readOnly": True,
            "screenMode": screen_mode,
            "pixelsAvailable": pixels,
            "viewKind": "pixels" if pixels else "semantic",
            "surface": self._surface_description(),
        }
        self._subscribers: set[_Subscriber] = set()
        self._subscribers_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._task_id = ""
        self._seen_actions: dict[str, str] = {}
        self._active = False
        self._closing = False

        self._frame_seq = 0
        self._surface_seq = 0
        self._activity_seq = 0
        self._last_frame_digest: str | None = None
        self._cached_frame: dict[str, object] | None = None
        self._last_surface_digest: str | None = None
        self._cached_surface: dict[str, object] | None = None
        self._last_emit = 0.0
        self._failure_reported = False

        self._server: ThreadingHTTPServer | None = None
        self._pump_threads: list[threading.Thread] = []
        self._capture_destination: Path | None = None

    # ---- lifecycle ---------------------------------------------------------

    @classmethod
    def attach(
        cls,
        plane: Any,
        *,
        goal: str = "",
        screen_mode: str = "hidden",
    ) -> ApexScreenRelay:
        """Build the relay, bind it, and start its pumps."""

        relay = cls(plane, goal=goal, screen_mode=screen_mode)
        relay.start()
        return relay

    def start(self) -> None:
        self._pump_threads = [
            threading.Thread(
                target=self._pump_view,
                args=(self._fps(),),
                daemon=True,
                name="apex-screen-view",
            ),
            threading.Thread(target=self._pump_task, daemon=True, name="apex-screen-task"),
        ]
        for thread in self._pump_threads:
            thread.start()
        self._bind()

    def close(self) -> None:
        """Stop the pumps, say goodbye to the viewers, and free the port."""

        with self._state_lock:
            if self._closing:
                return
            self._closing = True
            self._active = False
        with self._subscribers_lock:
            targets = list(self._subscribers)
        for subscriber in targets:
            subscriber.offer({"type": "bye", "reason": "the background screen is closing"})
            subscriber.close()
        server = self._server
        if server is not None:
            with contextlib.suppress(Exception):
                server.shutdown()
            with contextlib.suppress(Exception):
                server.server_close()
        self._server = None
        self._discard_capture()
        for thread in self._pump_threads:
            if thread.is_alive():
                thread.join(timeout=2.0)

    @property
    def port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server is not None else 0

    def set_task(self, task_id: str) -> None:
        """Name the task whose ledger and surface this relay follows."""

        with self._state_lock:
            self._task_id = str(task_id or "")
            if self._task_id:
                self._session["taskId"] = self._task_id
                self._session["startedAt"] = _now_ms()
                self._session["updatedAt"] = _now_ms()
                self._active = True

    # ---- the session -------------------------------------------------------

    def _surface_description(self) -> str:
        # The plane is a mode over an adapter; the adapter is what states the
        # surface's real name ("hidden-accessibility-surface", an Xvfb
        # framebuffer, the interactive desktop).
        for source in (getattr(self._plane, "adapter", None), self._plane):
            if source is None:
                continue
            try:
                capabilities = source.capabilities
                if callable(capabilities):
                    capabilities = capabilities()
                surface = _clean_text(
                    getattr(capabilities, "surface", "") or "hidden-surface",
                    128,
                )
            except Exception:
                continue
            if surface:
                return surface
        return "hidden-surface" if self._screen_mode == "hidden" else "interactive-desktop"

    def snapshot(self) -> dict[str, object]:
        with self._state_lock:
            return dict(self._session)

    def _set_status(self, status: str, detail: str = "") -> None:
        with self._state_lock:
            previous = str(self._session.get("status", ""))
            previous_detail = str(self._session.get("detail", ""))
            self._session["status"] = status
            if detail:
                self._session["detail"] = _clean_text(detail, 400)
            self._session["updatedAt"] = _now_ms()
            changed = status != previous or (bool(detail) and detail != previous_detail)
        if changed:
            self.publish({"type": "status", "status": status, "detail": _clean_text(detail, 400)})

    def _report_failure(self, detail: str) -> None:
        """State a capture failure once, not once a frame."""

        if self._failure_reported:
            return
        self._failure_reported = True
        self._set_status(str(self._session.get("status", "running")), detail)

    # ---- the wire ----------------------------------------------------------

    def subscribe(self) -> _Subscriber:
        """Register a viewer and hand it the current state and picture.

        ``hello`` first, always: it is the only event that tells the page an
        agent exists, and a viewer that has to infer that from frames will
        decide it is looking at a placeholder.
        """

        subscriber = _Subscriber()
        with self._subscribers_lock:
            self._subscribers.add(subscriber)
        subscriber.offer({"type": "hello", "session": self.snapshot()})
        # A viewer that joins mid-run gets the current picture immediately,
        # rather than waiting out the next change or the idle repeat.
        with self._state_lock:
            cached_frame = self._cached_frame
            cached_surface = self._cached_surface
        if cached_frame is not None:
            subscriber.offer({"type": "frame", "frame": dict(cached_frame)})
        if cached_surface is not None:
            subscriber.offer({"type": "surface", "surface": dict(cached_surface)})
        return subscriber

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        with self._subscribers_lock:
            self._subscribers.discard(subscriber)
        subscriber.close()

    def publish(self, event: dict[str, object]) -> None:
        with self._subscribers_lock:
            targets = list(self._subscribers)
        for subscriber in targets:
            subscriber.offer(event)

    # ---- the pumps ---------------------------------------------------------

    def _fps(self) -> float:
        raw = env("APEX_SCREEN_FPS", "")
        try:
            value = float(raw) if raw else DEFAULT_FPS
        except ValueError:
            value = DEFAULT_FPS
        return max(0.5, min(8.0, value))

    def _pump_task(self) -> None:
        """Follow the step ledger and translate it into the feed."""

        while not self._closing:
            task_id = self._task_id
            if task_id and self._active:
                try:
                    snapshot = self._plane.get_task(task_id)
                except Exception as error:
                    logger.debug("task poll failed: %s", error)
                else:
                    self._follow(snapshot)
            time.sleep(TASK_POLL_SECONDS)

    def _follow(self, snapshot: Any) -> None:
        """Publish new ledger entries; stop the view when the run ends."""

        mode = str(getattr(getattr(snapshot, "mode", None), "value", getattr(snapshot, "mode", "")))
        with self._state_lock:
            self._session["stepsUsed"] = getattr(snapshot, "steps_used", 0)
            self._session["revisionId"] = getattr(snapshot, "revision_id", 0)
        for action in getattr(snapshot, "actions", ()) or ():
            action_id = str(getattr(action, "action_id", ""))
            status = str(
                getattr(getattr(action, "status", None), "value", getattr(action, "status", "")),
            )
            if not action_id:
                continue
            known = self._seen_actions.get(action_id)
            if known == status:
                continue
            if known is not None and status not in _TERMINAL_ACTION_STATUSES:
                continue
            self._seen_actions[action_id] = status
            self._emit_action(action, status)
        status = _STATUS_FOR_TASK.get(mode.upper())
        if status and status != self._session.get("status"):
            detail = _clean_text(getattr(snapshot, "last_error", "") or snapshot.goal, 400)
            self._set_status(status, detail)
        if mode.upper() in _TERMINAL_TASK_MODES and self._active:
            with self._state_lock:
                self._active = False
            kind = "done" if mode.upper() == "COMPLETED" else "warn"
            self._emit_activity(kind, f"Run {mode.lower()}", _clean_text(snapshot.goal, 400))

    def _emit_action(self, action: Any, status: str) -> None:
        operation = str(
            getattr(getattr(action, "operation", None), "value", getattr(action, "operation", "")),
        )
        kind = _KIND_FOR_OPERATION.get(operation, "read")
        if status == "OUTCOME_UNKNOWN":
            kind = "warn"
        elif status == "FAILED":
            kind = "error"
        self._emit_activity(
            kind,
            f"{operation or 'step'} (step {getattr(action, 'step', 0)})",
            _action_detail(action),
        )

    def _emit_activity(self, kind: str, label: str, detail: str = "") -> None:
        self._activity_seq += 1
        self.publish(
            {
                "type": "activity",
                "activity": {
                    "id": f"apex-screen-{self._activity_seq}",
                    "at": _now_ms(),
                    "kind": kind,
                    "label": _clean_text(label),
                    "detail": _clean_text(detail, 400),
                },
            }
        )

    def _pump_view(self, fps: float) -> None:
        """Read the surface the run is working on, while it works.

        Pixels where the surface has a raster target; the accessibility tree
        where it has none. Both paths stop once the task is terminal, so the
        relay is never capturing anything after the work is done.
        """

        period = 1.0 / max(0.5, fps)
        while not self._closing:
            if not self._active:
                time.sleep(period)
                continue
            try:
                if getattr(self._plane, "pixels_available", False):
                    self._capture_pixels()
                elif self._hidden_scope_ready():
                    self._capture_semantic()
            except Exception as error:  # A capture failure is reported, not hidden.
                logger.warning("background screen capture failed: %s", error)
                self._report_failure(f"capture failed: {error}")
            time.sleep(period)

    def _hidden_scope_ready(self) -> bool:
        """Whether the run has established what it may see.

        Hidden-mode perception is scoped to the applications the run isolated.
        Until that scope exists, the honest semantic frame is no frame: the
        relay states it is waiting rather than reading the user's own desktop.
        """

        scope = getattr(getattr(self._plane, "adapter", None), "app_scope", None)
        if scope is None:
            return True
        return bool(scope)

    def _capture_pixels(self) -> None:
        destination = self._capture_file()
        adapter = getattr(self._plane, "adapter", None)
        capture = getattr(adapter, "_capture_screenshot_sync", None)
        if destination is None or not callable(capture):
            return
        metadata = capture(destination)
        if not getattr(metadata, "pixels_available", True):
            self._set_status(str(self._session.get("status", "running")), NO_PIXELS_DETAIL)
            return
        raw = destination.read_bytes()
        if not raw:
            return
        digest = str(getattr(metadata, "sha256", "")) or hashlib.sha256(raw).hexdigest()
        changed = digest != self._last_frame_digest
        now = time.monotonic()
        if changed:
            self._last_frame_digest = digest
            self._cached_frame = encode_frame(raw, 1280, 72)
        if self._cached_frame is None:
            self._report_failure("the capture could not be encoded for the viewer")
            return
        if changed or (now - self._last_emit) >= IDLE_REPEAT_SECONDS:
            self._last_emit = now
            self._frame_seq += 1
            self.publish(
                {
                    "type": "frame",
                    "frame": {
                        **self._cached_frame,
                        "seq": self._frame_seq,
                        "at": _now_ms(),
                    },
                }
            )

    def _capture_semantic(self) -> None:
        adapter = getattr(self._plane, "adapter", None)
        capture = getattr(adapter, "_capture_tree_sync", None)
        if not callable(capture):
            return
        _tree_id, _foreground, nodes = capture()
        surface = self._semantic_surface(nodes)
        digest = hashlib.sha256(
            json.dumps(surface, sort_keys=True, default=str).encode("utf-8"),
        ).hexdigest()
        now = time.monotonic()
        if digest == self._last_surface_digest and (now - self._last_emit) < IDLE_REPEAT_SECONDS:
            return
        self._last_surface_digest = digest
        self._last_emit = now
        self._surface_seq += 1
        payload = {**surface, "seq": self._surface_seq, "at": _now_ms()}
        with self._state_lock:
            self._cached_surface = payload
        self.publish({"type": "surface", "surface": payload})

    def _semantic_surface(self, nodes: tuple[Any, ...]) -> dict[str, object]:
        """The accessibility tree as the viewer renders it.

        Only what a picture of the structure needs crosses the wire: role,
        label, value, bounds and the focus flag. Native references, identifiers
        and everything deeper than the display depth stay behind.
        """

        kept: list[dict[str, object]] = []
        left = top = None
        right = bottom = None
        for node in list(nodes)[:MAX_NODES]:
            depth = int(getattr(node, "depth", 0) or 0)
            if depth > MAX_DEPTH:
                continue
            bounds = getattr(node, "bounds", None)
            box: list[float] | None = None
            if bounds is not None:
                box = [
                    round(float(getattr(bounds, "left", 0.0)), 1),
                    round(float(getattr(bounds, "top", 0.0)), 1),
                    round(float(getattr(bounds, "right", 0.0)), 1),
                    round(float(getattr(bounds, "bottom", 0.0)), 1),
                ]
                node_left, node_top, node_right, node_bottom = box
                if node_right > node_left and node_bottom > node_top:
                    left = node_left if left is None else min(left, node_left)
                    top = node_top if top is None else min(top, node_top)
                    right = node_right if right is None else max(right, node_right)
                    bottom = node_bottom if bottom is None else max(bottom, node_bottom)
            kept.append(
                {
                    "id": _clean_text(getattr(node, "node_id", ""), 64),
                    "role": _clean_text(getattr(node, "role", ""), 64),
                    "name": _clean_text(getattr(node, "name", None)),
                    "value": _clean_text(getattr(node, "value", None)),
                    "window": _clean_text(getattr(node, "window_title", None), 80),
                    "bounds": box,
                    "focused": bool(getattr(node, "focused", False)),
                    "enabled": bool(getattr(node, "enabled", True)),
                    "depth": depth,
                }
            )
        width = (right - left) if (left is not None and right is not None) else 0.0
        height = (bottom - top) if (top is not None and bottom is not None) else 0.0
        return {
            "coordinateSpace": "hidden-accessibility-surface",
            "originLeft": round(float(left or 0.0), 1),
            "originTop": round(float(top or 0.0), 1),
            "width": round(float(width), 1),
            "height": round(float(height), 1),
            "nodes": kept,
            "truncated": len(nodes) > MAX_NODES,
        }

    # ---- the HTTP surface (GET-only; there is nothing to click) ------------

    def _bind(self) -> None:
        base = self._base_port()
        last_error: Exception | None = None
        for offset in range(PORT_SPAN):
            port = base + offset
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            except OSError as error:
                last_error = error
                continue
            self._server.relay = self  # type: ignore[attr-defined]
            threading.Thread(
                target=self._server.serve_forever,
                kwargs={"poll_interval": 0.2},
                daemon=True,
                name="apex-screen-http",
            ).start()
            logger.info(
                "subagent.apex_screen_listening",
                port=self.port,
                mode=self._screen_mode,
                view=str(self._session.get("viewKind")),
            )
            return
        logger.warning("subagent.apex_screen_bind_failed", base=base, error=str(last_error))
        self._pump_threads.clear()

    def _base_port(self) -> int:
        raw = env("APEX_SCREEN_PORT", "")
        try:
            return int(raw) if raw else DEFAULT_PORT
        except ValueError:
            return DEFAULT_PORT

    def _capture_file(self) -> Path | None:
        """The one overwritten capture file, owner-only, like the desk agent's.

        A frame is a picture of the machine the agent is driving, so the amount
        of it on disk must not depend on how cleanly the last process exited:
        one fixed path, cleared on close.
        """

        if self._capture_destination is not None:
            return self._capture_destination
        try:
            root = Path(tempfile.gettempdir()) / "apex-background-screen"
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with contextlib.suppress(OSError):
                root.chmod(0o700)
            self._capture_destination = root / "capture.png"
        except OSError:
            return None
        return self._capture_destination

    def _discard_capture(self) -> None:
        destination = self._capture_destination
        self._capture_destination = None
        if destination is not None:
            with contextlib.suppress(OSError):
                destination.unlink()


def _action_detail(action: Any) -> str:
    """One short line about what a step did, in the step's own words."""

    error = getattr(action, "error", None)
    if error:
        return _clean_text(error, 400)
    receipt = getattr(action, "receipt", None)
    if receipt is not None and getattr(receipt, "detail", ""):
        return _clean_text(receipt.detail, 400)
    target = getattr(action, "target", None)
    name = getattr(target, "name", None) if target is not None else None
    if name:
        return _clean_text(name, 400)
    return _clean_text(getattr(getattr(action, "status", None), "value", ""), 400)


class _Handler(BaseHTTPRequestHandler):
    """GET-only. ``/frames`` is the SSE stream; ``/session`` is the state.

    There is deliberately no other route. A viewer that cannot even ask for
    control cannot be confused about whether the view is interactive, and the
    relay cannot become an input path no matter what calls it.
    """

    server_version = "apex-screen/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        relay = getattr(self.server, "relay", None)
        if relay is None:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "no_relay"})
            return
        path = self.path.split("?", 1)[0]
        if path == "/frames":
            self._stream(relay)
            return
        if path == "/session":
            self._json(HTTPStatus.OK, relay.snapshot())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only"})

    def do_PUT(self) -> None:  # noqa: N802
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.debug("apex_screen_http", request=format % args)

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(Exception):
            self.wfile.write(body)

    def _stream(self, relay: ApexScreenRelay) -> None:
        subscriber = relay.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "keep-alive")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            relay.unsubscribe(subscriber)
            return
        try:
            while not subscriber.closed.is_set():
                try:
                    event = subscriber.queue.get(timeout=5.0)
                except queue.Empty:
                    # A keepalive written by hand: the queue may be empty
                    # because nothing changed, and a viewer must be able to
                    # tell that from a dead wire.
                    self.wfile.write(
                        f"data: {json.dumps({'type': 'keepalive', 'at': _now_ms()})}\n\n".encode(),
                    )
                    self.wfile.flush()
                    continue
                if event is None:
                    break
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            relay.unsubscribe(subscriber)


__all__ = ["ApexScreenRelay", "DEFAULT_PORT"]
