"""`tools.search`: the pinned meta-tool of the two-stage narrowed catalog.

The full registry used to be rendered into every prompt; it is narrowed now,
and this tool is the way back. It is pinned in the cached prefix — byte-stable
across steps, which is the property that lets the narrowing live below the
cache breakpoint — and answers a few words with the complete schemas of the
matching tools, drawn from the whole registry regardless of what the current
step's sheet shows.
"""

from __future__ import annotations

from typing import Any

from clio.toolhost.server import ToolContext, Toolhost
from clio.toolhost.tools.toolshed import Tool
from clio.tools.catalog import TOP_K, ToolCatalog, entry_from_tool_spec


class SearchToolsTool(Tool):
    """The catalog search every step is offered, whether or not it needs it."""

    name = "tools.search"
    description = (
        "Search the full tool registry for tools matching a few words about "
        "what the step needs, and receive their complete calling schemas. Use "
        "it when the tool you need is not on this step's sheet."
    )
    timeout_s = 10.0
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "A few words about what the step needs to do."},
            "k": {"type": "integer", "description": "How many tools to return (default 8)."},
        },
        "required": ["query"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required", "tools": []}
        try:
            k = max(1, min(int(args.get("k") or 8), TOP_K))
        except (TypeError, ValueError):
            k = 8
        catalog = _catalog_for(host)
        results = catalog.search(query, k=k)
        return {
            "query": query,
            "tools": results,
            "note": (
                "These schemas are complete and callable now. Register nothing; "
                "call the tool by name with arguments matching its schema."
            ),
        }


def _catalog_for(host: Toolhost) -> ToolCatalog:
    """The whole registry, as one catalog.

    Built from the host's own `catalog()` — the same entries the stable
    prefix used to render in full — so search and the sheet can never
    disagree about what exists.
    """
    catalog = ToolCatalog()
    for entry in host.catalog():
        catalog.register(entry_from_tool_spec(entry))
    return catalog


__all__ = ["SearchToolsTool"]
