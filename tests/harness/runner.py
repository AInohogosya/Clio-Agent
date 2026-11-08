"""Harness runner: drives real components, writes timestamped bundles (§4).

Each scenario is a deterministic function of the scripted gateway + components.
Metrics follow §6. Bundle = transcript, tool log, state snapshots, metrics,
PASS/FAIL per assertion. Repeat runs (>=3) are handled by --repeat.
"""
from __future__ import annotations

import asyncio, hashlib, json, time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from ethos.config import EthosConfig
from ethos.memory.context import ContextLayer
from ethos.schemas.context import TurnKind
from ethos.schemas.models import NormalizedResponse, Usage

class MechanicalGateway:
    """Deterministic narrator: mechanical line, no extraction.

    This is the correct fallback under C8 (honest tally). The harness can
    swap in a ScriptedGateway to model a narrator that extracts anchors.
    """
    def __init__(self, extract: bool = False):
        self.extract = extract
        self.requests: list[Any] = []
        self.calls = 0
    async def complete(self, request: Any) -> NormalizedResponse:
        self.requests.append(request)
        self.calls += 1
        # Render turns from the user message
        content = "\n".join(str(m.content) for m in request.messages)
        if self.extract:
            # Extract facts verbatim (what a perfect narrator would do)
            important = []
            for line in content.splitlines():
                if "F" in line and ":" in line:
                    pass
            text = json.dumps({"line": f"narrated {self.calls} turns",
                               "open_loops": [], "important": important})
        else:
            text = json.dumps({"line": f"did some work, pass {self.calls}",
                               "open_loops": []})
        return NormalizedResponse(text=text, cost_usd=0.001,
                                  usage=Usage(input_tokens=50, output_tokens=5))

def bundle_dir(base: Path, name: str) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    d = base / f"{ts}-{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def write_bundle(d: Path, payload: dict) -> Path:
    (d / "bundle.json").write_text(json.dumps(payload, indent=2, default=str))
    return d

def hash_state(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]
