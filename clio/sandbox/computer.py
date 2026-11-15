"""The virtual computer: display, browser and live screen takeover.

A project's browser automation must not steal the host's focus, must not leak
one project's cookies into another, and must not let a page talk the agent
into acting on the wrong element. This module is the three properties:

1. **Isolation.** Each project gets its own `Xvfb` virtual framebuffer
   (1280x800) and its own persistent Chromium profile directory inside its
   sandbox. Two projects never share a display, a cookie jar or a cache.

2. **Ref-addressed actions.** Browser actions target *references* from a
   fresh `ariaSnapshot`, resolved server-side inside the sandbox at the
   moment of the action. A reference from a previous snapshot of a page that
   has since navigated or re-rendered is rejected — `HTTP 409`, re-observe
   and try again — because a stale reference is exactly what
   prompt-injected content would use to make the agent click something other
   than what it believes it is clicking.

3. **Zero-exposure credentials and live takeover.** `{{secret:KEY}}`
   placeholders are substituted at runtime inside the sandbox's isolated DOM
   world; the raw secret never appears in the model's context, the logs, or
   a snapshot. When a login needs a person — CAPTCHA, 2FA, a decision — the
   live screen streams to them and they take control behind an **Action
   Barrier**: every in-flight agent input event drains to completion first,
   password fields are masked in logs and recordings, and clicking Hand Back
   hands control to the agent again cleanly.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.sandbox.computer")

DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 800

#: A reference older than this many snapshot generations is refused outright.
MAX_REF_GENERATIONS = 3

_SENSITIVE_KEY = re.compile(r"pass|secret|token|key|otp|pin", re.IGNORECASE)


class StaleRefError(Exception):
    """A browser action named a reference the page no longer holds (HTTP 409)."""

    status = 409

    def __init__(self, ref: str, reason: str) -> None:
        super().__init__(f"stale ariaSnapshot reference {ref!r}: {reason}; re-observe the page with a fresh snapshot and act on the new references")
        self.ref = ref
        self.reason = reason


class SandboxClosedError(Exception):
    """The sandbox holding the virtual computer is gone."""


@dataclass
class DisplaySpec:
    """The virtual framebuffer one sandbox runs."""

    sandbox_id: str
    display: str            # ":N"
    width: int = DISPLAY_WIDTH
    height: int = DISPLAY_HEIGHT
    profile_dir: str = ""
    xvfb_pid: int | None = None
    headless_native: bool = False

    @property
    def geometry(self) -> str:
        return f"{self.width}x{self.height}x24"


class VirtualDisplay:
    """Xvfb on Linux; the platform's own offscreen rendering elsewhere.

    On Linux an `Xvfb :N -screen 0 1280x800x24` process backs the display, and
    the browser attaches to it, so automation never touches the interactive
    session. On macOS there is no Xvfb and the browser's own headless
    compositor is the framebuffer — the isolation property (no host focus, no
    shared profile) holds either way, and the record says which one it is
    rather than implying an X server that is not there.
    """

    def __init__(self, sandbox_id: str, sandbox_root: str, *, display_number: int = 1) -> None:
        import platform

        self.spec = DisplaySpec(
            sandbox_id=sandbox_id,
            display=f":{display_number}",
            profile_dir=f"{sandbox_root}/.clio-computer/chromium-profile",
            headless_native=platform.system() != "Linux",
        )
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> DisplaySpec:
        import shutil

        if self.spec.headless_native or shutil.which("Xvfb") is None:
            self.spec.headless_native = True
            logger.info("computer.display_headless", sandbox=self.spec.sandbox_id)
            return self.spec
        self._proc = await asyncio.create_subprocess_exec(
            "Xvfb", self.spec.display, "-screen", "0", self.spec.geometry,
            "-nolisten", "tcp", "-noreset",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self.spec.xvfb_pid = self._proc.pid
        # Xvfb is ready when its socket appears; a fixed sleep is a guess and
        # this is not.
        import os

        for _ in range(50):
            if os.path.exists(f"/tmp/.X11-unix/X{self.spec.display.lstrip(':')}"):
                break
            await asyncio.sleep(0.1)
        logger.info("computer.display_started", sandbox=self.spec.sandbox_id,
                    display=self.spec.display, geometry=self.spec.geometry)
        return self.spec

    async def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except TimeoutError:
                self._proc.kill()
            self._proc = None


@dataclass
class SnapshotEntry:
    ref: str
    role: str
    name: str
    masked: bool = False
    value_preview: str = ""

    def as_dict(self) -> dict[str, Any]:
        entry = {"ref": self.ref, "role": self.role, "name": self.name,
                 "masked": self.masked}
        if self.masked:
            entry["value"] = "(masked)"
        elif self.value_preview:
            entry["value"] = self.value_preview
        return entry


class RefRegistry:
    """Snapshot generations and the server-side ref → element resolution.

    The registry is the thing that makes a ref mean something: `snapshot()`
    bumps the generation and stamps every interactive element with its ref
    inside the page; `resolve()` answers whether a ref is still the element
    the model believes it is, and a ref from too many generations back — or
    from a page that navigated — is a `StaleRefError`, never a best-effort
    lookup on a name.
    """

    def __init__(self) -> None:
        self.generation = 0
        self._refs: dict[str, int] = {}

    def begin_snapshot(self) -> int:
        self.generation += 1
        return self.generation

    def register(self, ref: str) -> None:
        self._refs[ref] = self.generation

    def resolve(self, ref: str) -> int:
        generation = self._refs.get(ref)
        if generation is None:
            raise StaleRefError(ref, "no snapshot of this page ever issued this reference")
        if self.generation - generation > MAX_REF_GENERATIONS:
            raise StaleRefError(
                ref,
                f"the reference is {self.generation - generation} snapshots old "
                f"(the limit is {MAX_REF_GENERATIONS})",
            )
        return generation

    def forget(self, ref: str) -> None:
        self._refs.pop(ref, None)


_SNAPSHOT_SCRIPT = """
() => {
  const out = [];
  const nodes = document.querySelectorAll(
    'a, button, input, select, textarea, [role="button"], [role="link"], [role="textbox"], [contenteditable="true"]'
  );
  let i = 0;
  for (const el of nodes) {
    const ref = 'r' + (++i) + '-' + %d;
    el.setAttribute('data-clio-ref', ref);
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const name = (el.getAttribute('aria-label') || el.innerText || el.getAttribute('name') || el.getAttribute('placeholder') || '').trim().slice(0, 120);
    const type = (el.getAttribute('type') || '').toLowerCase();
    const masked = type === 'password';
    out.push({ref, role, name, masked, tag: el.tagName.toLowerCase(), type});
  }
  return out;
}
"""


class RefBrowser:
    """The ref-addressed browser surface, over any page protocol.

    `page` speaks the small protocol the toolhost's Playwright sessions
    expose — `evaluate(script)`, `url`, `goto(url)`. Tests drive it with a
    fake; production wires the real session, and the ref semantics are
    identical either way because they live here, not in the page.
    """

    def __init__(self, page: Any, *, sandbox_id: str, secrets: Any = None,
                 audit: Any = None) -> None:
        self.page = page
        self.sandbox_id = sandbox_id
        self.registry = RefRegistry()
        self.secrets = secrets
        self.audit = audit
        self._last_url: str = ""

    async def snapshot(self) -> dict[str, Any]:
        """A fresh ARIA snapshot with server-issued references.

        Every call is a new generation: references from the previous snapshot
        remain resolvable for `MAX_REF_GENERATIONS` generations, which is the
        window a model has to act on what it just observed — and no longer.
        """
        generation = self.registry.begin_snapshot()
        raw = await self.page.evaluate(_SNAPSHOT_SCRIPT % generation)
        entries: list[SnapshotEntry] = []
        for item in raw or []:
            ref = str(item.get("ref") or "")
            if not ref:
                continue
            self.registry.register(ref)
            name = str(item.get("name") or "")
            masked = bool(item.get("masked")) or (
                item.get("type") == "password" or bool(_SENSITIVE_KEY.search(name))
            )
            entries.append(SnapshotEntry(
                ref=ref, role=str(item.get("role") or ""),
                name="(masked)" if masked and _SENSITIVE_KEY.search(name) and item.get("type") == "password"
                     and not item.get("name") else name,
                masked=masked,
            ))
        self._last_url = await self._url()
        return {
            "url": self._last_url,
            "generation": generation,
            "elements": [e.as_dict() for e in entries],
        }

    async def _url(self) -> str:
        url = getattr(self.page, "url", "")
        if callable(url):
            url = url()
        return str(url or "")

    async def resolve_ref(self, ref: str) -> str:
        """Server-side resolution: the DOM's current holder of this ref.

        A navigation between the snapshot and the action invalidates every
        ref by definition — the page they described does not exist — so the
        URL is part of the check, not just the generation counter.
        """
        self.registry.resolve(ref)
        if self._last_url and self._last_url != await self._url():
            raise StaleRefError(ref, "the page navigated since the snapshot was taken")
        found = await self.page.evaluate(
            "(ref) => { const el = document.querySelector('[data-clio-ref=\"' + ref + '\"]'); "
            "return el ? {tag: el.tagName.toLowerCase()} : null; }",
            ref,
        )
        if not found:
            raise StaleRefError(ref, "the element left the page since the snapshot was taken")
        return ref

    async def act(self, ref: str, action: str, *, text: str | None = None) -> dict[str, Any]:
        """Act on a reference: click, type, or select. 409 semantics on stale."""
        await self.resolve_ref(ref)
        selector = f'[data-clio-ref="{ref}"]'
        if action == "click":
            await self.page.click(selector)
        elif action in ("type", "fill"):
            value = text or ""
            if self.secrets is not None and "{{secret:" in value:
                value = await self._substitute_secrets(ref, value)
            await self.page.fill(selector, value)
        else:
            raise ValueError(f"unknown browser action {action!r}")
        await self._audit(ref, action, text)
        return {"ref": ref, "action": action, "ok": True}

    async def _substitute_secrets(self, ref: str, text: str) -> str:
        """Resolve `{{secret:KEY}}` at fill time, inside the sandbox only.

        The substitution happens here — the value goes straight into the
        DOM node — and never travels back through a result, a snapshot or a
        log. What the model sees is the placeholder it wrote and `ok: true`.
        """
        value = text
        for match in re.finditer(r"\{\{secret:([A-Za-z0-9_.-]+)\}\}", text):
            key = match.group(1)
            secret = None
            getter = getattr(self.secrets, "get_secret", None)
            if callable(getter):
                try:
                    secret = await asyncio.to_thread(getter, key)
                except Exception:
                    secret = None
            if secret is None:
                raise ValueError(f"secret {key!r} is not in the vault; nothing was typed")
            value = value.replace(match.group(0), secret)
        return value

    async def _audit(self, ref: str, action: str, text: str | None) -> None:
        if self.audit is None:
            return
        try:
            await self.audit.append(
                actor="computer", category="browser",
                summary=f"browser {action} on {ref} in {self.sandbox_id}",
                details={"sandbox_id": self.sandbox_id, "ref": ref, "action": action,
                         "url": self._last_url,
                         "text": mask_secrets(text or "", self.secrets) if text else ""},
            )
        except Exception:
            logger.exception("computer.audit_failed")


def mask_secrets(text: str, secrets: Any = None) -> str:
    """Replace any vault value occurring in `text` with its placeholder."""
    if secrets is None or not text:
        return text
    lister = getattr(secrets, "list_keys", None)
    if not callable(lister):
        return text
    try:
        keys = lister()
    except Exception:
        return text
    masked = text
    for key in keys or []:
        try:
            value = secrets.get_secret(key)
        except Exception:
            continue
        if value:
            masked = masked.replace(value, f"{{{{secret:{key}}}}}")
    return masked


def mask_password_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The recording-safe copy of a snapshot: password values masked, forever."""
    out = dict(snapshot)
    out["elements"] = [
        {**entry, "value": "(masked)"} if entry.get("masked") else entry
        for entry in snapshot.get("elements", [])
    ]
    return out


@dataclass
class TakeoverSession:
    sandbox_id: str
    token: str
    requester: str
    started_at: float
    handed_back_at: float | None = None
    drained_events: int = 0


class TakeoverController:
    """Live screen takeover with an Action Barrier.

    The barrier is the whole mechanism: an agent input event is only sent if
    it *began* before the takeover started, and granting control waits for
    exactly those in-flight events to drain (bounded — a wedged event does
    not hold a person hostage). While a person holds the sandbox, new agent
    input is refused with the takeover's reason; Hand Back clears the state
    and the agent's next input goes through.
    """

    def __init__(self, *, drain_timeout_s: float = 5.0) -> None:
        self.drain_timeout_s = drain_timeout_s
        self._sessions: dict[str, TakeoverSession] = {}
        self._inflight: dict[str, set[asyncio.Task]] = {}
        self._lock = asyncio.Lock()

    async def take(self, sandbox_id: str, requester: str) -> TakeoverSession:
        async with self._lock:
            existing = self._sessions.get(sandbox_id)
            if existing is not None and existing.handed_back_at is None:
                return existing
            session = TakeoverSession(
                sandbox_id=sandbox_id, token=uuid.uuid4().hex[:16],
                requester=requester, started_at=time.time(),
            )
            # The Action Barrier: no human input flows until every agent
            # mouse/keyboard event already in flight has completed. Bounded,
            # so a stuck event cannot deadlock the handover.
            inflight = self._inflight.get(sandbox_id, set())
            if inflight:
                done, pending = await asyncio.wait(
                    list(inflight), timeout=self.drain_timeout_s,
                )
                for task in pending:
                    task.cancel()
                session.drained_events = len(done) + len(pending)
            self._sessions[sandbox_id] = session
            logger.info("computer.takeover_started", sandbox=sandbox_id,
                        requester=requester, drained=session.drained_events)
            return session

    async def hand_back(self, sandbox_id: str, token: str) -> bool:
        async with self._lock:
            session = self._sessions.get(sandbox_id)
            if session is None or session.token != token or session.handed_back_at is not None:
                return False
            session.handed_back_at = time.time()
            logger.info("computer.takeover_ended", sandbox=sandbox_id)
            return True

    def holder(self, sandbox_id: str) -> str | None:
        session = self._sessions.get(sandbox_id)
        if session is not None and session.handed_back_at is None:
            return session.requester
        return None

    async def guarded_input(self, sandbox_id: str, event: Any) -> Any:
        """Run one agent input event behind the barrier.

        Refused while a person holds the sandbox — the refusal names the
        human, because "input ignored" without a holder is the bug this
        controller exists to stop being possible.
        """
        holder = self.holder(sandbox_id)
        if holder is not None:
            raise PermissionError(
                f"{holder} is holding live control of {sandbox_id}; agent input "
                "resumes when they click Hand Back"
            )
        task = asyncio.current_task()
        if task is not None:
            self._inflight.setdefault(sandbox_id, set()).add(task)
        try:
            if asyncio.iscoroutine(event):
                return await event
            return event
        finally:
            if task is not None:
                self._inflight.get(sandbox_id, set()).discard(task)


__all__ = [
    "DISPLAY_HEIGHT",
    "DISPLAY_WIDTH",
    "DisplaySpec",
    "RefBrowser",
    "RefRegistry",
    "SandboxClosedError",
    "SnapshotEntry",
    "StaleRefError",
    "TakeoverController",
    "TakeoverSession",
    "VirtualDisplay",
    "mask_password_fields",
    "mask_secrets",
]
