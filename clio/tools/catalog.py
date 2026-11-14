"""The unified tool catalog and its two-stage dynamic narrowing.

Every tool the runtime can execute — built-in, toolshed, MCP, OAuth connector,
OpenAPI/GraphQL import — enters one catalog shaped the same way: name,
description, JSON-schema input, and the source that registered it. What a step
*sees* is narrowed in two stages, because the two failure modes of a tool
prompt are symmetrical:

- A full registry in every prompt costs thousands of tokens per turn and
  buries the four tools this step needs among sixty it does not.
- A registry narrowed once and fixed in the prefix strands the step without a
  tool it discovers it needs halfway through.

Stage 1 is a lightweight router (`route`): no model call, a scored ranking of
the catalog against the step's own text — name hits, description hits,
connector affinity — capped at twelve. Stage 2 is the pinned `tools.search`
meta-tool: it sits in the cached prompt prefix where the full registry used
to, and any tool outside the top twelve is one call away, its complete schema
hydrated into the step context on demand. The prefix stays byte-stable across
steps — the narrowing lives in the volatile sheet below the cache breakpoint —
so prompt caching survives the narrowing that makes it affordable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Stage 1's cap. Twelve fully-rendered schemas is the sheet a step can
#: actually read; everything past it is reachable through `tools.search`.
TOP_K = 12

_WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{2,}")

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "into", "then", "than",
    "tool", "tools", "using", "use", "make", "made", "have", "has", "had",
})


@dataclass
class CatalogEntry:
    """One tool, in the shape every source registers into the catalog."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "source": self.source,
        }


def entry_from_tool_spec(spec: Any, source: str = "builtin") -> CatalogEntry:
    """A catalog entry from a ToolSpec, a Tool instance or a rendered dict."""
    if isinstance(spec, CatalogEntry):
        return spec
    if isinstance(spec, dict):
        return CatalogEntry(
            name=str(spec.get("name") or ""),
            description=str(spec.get("description") or ""),
            input_schema=dict(spec.get("input_schema") or {}),
            source=str(spec.get("source") or source),
            keywords=tuple(str(k) for k in (spec.get("keywords") or ())),
        )
    name = str(getattr(spec, "name", "") or "")
    description = str(getattr(spec, "description", "") or "")
    schema = getattr(spec, "input_schema", None) or getattr(spec, "schema", None) or {}
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    return CatalogEntry(name=name, description=description,
                        input_schema=dict(schema or {}), source=source)


class ToolCatalog:
    """The registry as one catalog, with the router and the search entry."""

    def __init__(self, entries: list[Any] | None = None, *, embedder: Any = None):
        self.entries: list[CatalogEntry] = []
        self.embedder = embedder
        for spec in entries or []:
            self.register(spec)

    def register(self, spec: Any, source: str = "builtin") -> CatalogEntry:
        entry = entry_from_tool_spec(spec, source)
        if not entry.name:
            raise ValueError("a tool without a name cannot enter the catalog")
        self.entries = [e for e in self.entries if e.name != entry.name]
        self.entries.append(entry)
        return entry

    def __len__(self) -> int:
        return len(self.entries)

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def get(self, name: str) -> CatalogEntry | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None

    # ---- stage 1: the lightweight router ---------------------------------

    def route(self, step_text: str, *, k: int = TOP_K, pinned: list[str] | None = None) -> list[CatalogEntry]:
        """The top-k catalog entries for one step's text, pinned first.

        The router is deliberately not a model call: it scores name and
        description words against the step's words, with an exact tool-name
        mention ranking highest and connector sources next. A T1 call would
        spend a model round trip to decide what a token overlap already knows
        for the common case — "edit the file" points at fs.edit — and a router
        that cannot answer without a network is a router the step boundary can
        rarely afford.
        """
        words = {w.lower() for w in _WORD_RE.findall(step_text or "")} - _STOPWORDS
        pinned_entries = [self.get(p) for p in (pinned or []) if self.get(p)]
        scored: list[tuple[float, CatalogEntry]] = []
        for entry in self.entries:
            if any(entry.name == p.name for p in pinned_entries):
                continue
            score = self._score(entry, words, step_text or "")
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        top = [entry for _score, entry in scored[: max(0, k - len(pinned_entries))]]
        return [*pinned_entries, *top]

    def _score(self, entry: CatalogEntry, words: set[str], text: str) -> float:
        score = 0.0
        name_words = set(_WORD_RE.findall(entry.name.lower()))
        # An exact tool-name mention in the step text is the strongest signal.
        if entry.name.lower() in text.lower():
            score += 10.0
        overlap = name_words & words
        score += 3.0 * len(overlap)
        desc_words = {w.lower() for w in _WORD_RE.findall(entry.description.lower())} - _STOPWORDS
        score += 1.0 * len(desc_words & words)
        score += 0.5 * len(set(entry.keywords) & words)
        # Connector and imported sources carry their own affinity: a project
        # with a GitHub connector is asking about GitHub tools most steps.
        if entry.source not in ("builtin",):
            score += 0.25
        return score

    # ---- stage 2: search ----------------------------------------------------

    def search(self, query: str, k: int = 8) -> list[dict[str, Any]]:
        """Full schemas for the tools matching a query — the meta-tool's body."""
        entries = self.route(query, k=max(1, k))
        if not entries:
            # A query that scores nothing still deserves an answer: fall back
            # to prefix matching on names, then to the catalog's first tools.
            lowered = (query or "").lower().strip()
            entries = [e for e in self.entries if e.name.lower().startswith(lowered)]
        return [e.as_dict() for e in entries[:max(1, k)]]

    # ---- rendering ------------------------------------------------------------

    def render_sheet(self, entries: list[CatalogEntry], *, total: int | None = None) -> str:
        """The narrowed tool sheet: full schemas, and the meta-tool's promise."""
        total = total if total is not None else len(self.entries)
        lines = [
            "## Tools for this step",
            "",
            f"{len(entries)} of {total} registered tools are on this sheet, chosen for "
            "this step. Every tool on it is callable now, with the exact schema shown. "
            "Anything else in the registry is reachable through `tools.search`: call it "
            "with a few words about what you need and it returns the complete schemas "
            "of the matching tools. Do not guess a tool name or a parameter — search.",
            "",
        ]
        for entry in entries:
            schema = json.dumps(entry.input_schema or {"type": "object", "properties": {}}, default=str)
            description = " ".join(str(entry.description or "").split())
            lines.append(f"- `{entry.name}` ({entry.source}) — {description}")
            lines.append(f"  schema: {schema}")
        return "\n".join(lines)


SEARCH_TOOLS_SPEC = {
    "name": "tools.search",
    "description": (
        "Search the tool catalog for tools matching a few words, and receive their "
        "complete calling schemas. Use this whenever the step needs a tool that is "
        "not on the current sheet — the registry holds more than the sheet shows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A few words about what the step needs to do."},
            "k": {"type": "integer", "description": "How many tools to return (default 8)."},
        },
        "required": ["query"],
    },
}


def pinned_prefix_tools() -> list[dict[str, Any]]:
    """Block 2 of the stable prefix: the pinned meta-tool, and only it.

    Byte-stable across steps — which is what makes it safe in the cached
    prefix — because it does not depend on what any step is doing.
    """
    return [SEARCH_TOOLS_SPEC]


def narrowed_step_tools(catalog: ToolCatalog, step_text: str, *,
                        k: int = TOP_K, pinned: list[str] | None = None) -> str:
    """Stage 1 + rendering for one step's context sheet."""
    entries = catalog.route(step_text, k=k, pinned=pinned)
    if not any(e.name == SEARCH_TOOLS_SPEC["name"] for e in entries):
        entries = [entry_from_tool_spec(SEARCH_TOOLS_SPEC), *entries][: max(k, 1)]
    return catalog.render_sheet(entries)
