"""Two-stage dynamic tool narrowing: route, sheet, and the pinned meta-tool."""
from __future__ import annotations

from typing import Any

from clio.tools.catalog import (
    SEARCH_TOOLS_SPEC,
    TOP_K,
    ToolCatalog,
    entry_from_tool_spec,
    narrowed_step_tools,
    pinned_prefix_tools,
)


def _catalog(n: int = 40) -> ToolCatalog:
    catalog = ToolCatalog()
    for i in range(n):
        catalog.register({
            "name": f"fs.tool{i}" if i % 2 else f"shell.tool{i}",
            "description": f"tool {i}: edit files and run commands for purpose {i}",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        })
    catalog.register({
        "name": "memory.search",
        "description": "search remembered episodes and facts",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    })
    return catalog


def test_route_caps_the_sheet_at_twelve():
    catalog = _catalog(60)
    sheet = catalog.route("edit the files and run the commands", k=TOP_K)
    assert len(sheet) <= TOP_K


def test_route_scores_a_name_mention_highest():
    catalog = _catalog(20)
    sheet = catalog.route("please use memory.search for the remembered facts")
    assert sheet[0].name == "memory.search"


def test_route_ignores_a_step_that_names_no_tool_words():
    catalog = ToolCatalog()
    catalog.register({"name": "a.thing", "description": "zzz qqq", "input_schema": {}})
    # No overlap, no mention: nothing scores, nothing is chosen.
    assert catalog.route("completely unrelated prose about weather") == []


def test_pinned_entries_always_make_the_sheet():
    catalog = _catalog(30)
    sheet = catalog.route("something unrelated entirely", k=TOP_K, pinned=["memory.search"])
    assert [e.name for e in sheet][:1] == ["memory.search"]


def test_the_sheet_renders_full_schemas_and_names_the_meta_tool():
    catalog = _catalog(20)
    text = narrowed_step_tools(catalog, "edit the files")
    assert "tools.search" in text
    assert '"type": "object"' in text, "the schema is rendered, not described"
    assert "of 21 registered tools" in text


def test_search_returns_complete_schemas():
    catalog = _catalog(10)
    results = catalog.search("edit files")
    assert results
    for tool in results:
        assert set(tool) >= {"name", "description", "input_schema"}


def test_search_falls_back_to_a_prefix_match_when_nothing_scores():
    catalog = ToolCatalog()
    catalog.register({"name": "browser.visit", "description": "unrelated words", "input_schema": {}})
    results = catalog.search("browser", k=5)
    assert [t["name"] for t in results] == ["browser.visit"]


def test_pinned_prefix_block_is_stable_across_calls():
    first = pinned_prefix_tools()
    second = pinned_prefix_tools()
    assert first == second
    assert first[0]["name"] == SEARCH_TOOLS_SPEC["name"]


def test_register_replaces_a_tool_of_the_same_name():
    catalog = ToolCatalog()
    catalog.register({"name": "x.y", "description": "one", "input_schema": {}})
    catalog.register({"name": "x.y", "description": "two", "input_schema": {}})
    assert len(catalog) == 1
    assert catalog.get("x.y").description == "two"


def test_a_nameless_tool_is_refused():
    import pytest

    catalog = ToolCatalog()
    with pytest.raises(ValueError):
        catalog.register({"name": "", "description": "nope", "input_schema": {}})


def test_entry_from_a_tool_object():
    class FakeTool:
        name = "fake.tool"
        description = "a fake tool"
        input_schema = {"type": "object", "properties": {}}

    entry = entry_from_tool_spec(FakeTool())
    assert entry.name == "fake.tool" and entry.source == "builtin"


def test_narrowed_sheet_for_an_empty_registry_pins_only_the_meta_tool():
    catalog = ToolCatalog()
    text = narrowed_step_tools(catalog, "edit the files")
    # Even an empty registry offers the way back: the sheet is the pinned
    # meta-tool and the note that nothing else is registered.
    assert "tools.search" in text
