"""
The tool catalog as the agent is shown it.

Every tool carries a JSON Schema, and the catalog block used to throw all of it away:
each tool was rendered as its description and a bare list of parameter names, on the
stated grounds that the details "live in tool-call plumbing". They did live there, and
they were being dropped on the floor of the function that had been handed them.

That is invisible from the outside and expensive from the inside. `fs.edit` was
described as taking `path, search, replace, operations, diff`, which is true and
useless — `operations` is a list of `{search, replace}` pairs, and a model given only
the name has a one-in-several guess about what to put in it. `intent.close` was
described as taking `intention_id, status, reason`, so an agent could call it without
the reason and be refused by a schema it had been told was optional. And `fs.write` was
described as taking `path, content, append, binary_b64` — four scalars, the last one
read as a boolean — when `binary_b64` is a string holding base64.

None of that is recoverable by inference, and the failure is attributed by the schema
validator to a field the prompt said was something else, which is the worst kind of
wrong: confident, specific and wrong. So these tests are mostly about what the block
must still be able to say.

The last group is the one that would have caught the regression rather than the bug.
It renders every tool the agenthost actually registers, because the schemas that
matter are the ones somebody has to write by hand, and a renderer that quietly drops a
construct some future tool uses is the same bug in a new costume.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

import pytest

import ethos.toolhost.tools as tools_package
from ethos.prompts.identity import build_tool_catalog_block
from ethos.toolhost.server import Tool


def render(tools: list[dict[str, Any]]) -> str:
    return build_tool_catalog_block(tools)


def signature_of(name: str, **kwargs: Any) -> str:
    """The rendered argument list for one tool, for assertions about its parameters."""
    tools = [{"name": name, "description": kwargs.get("description", ""),
              "input_schema": kwargs["input_schema"]}]
    return render(tools).split("\n")[-1]


def params_of(line: str) -> str:
    """The argument list of a rendered tool line, without the description after it.

    Descriptions carry commas of their own — "interruption budgets, quiet hours,
    digest batching" — and so do two parameter annotations, so a rendered line is not
    something to take apart on a comma. Everything up to the em dash is the signature.
    """
    head = line.split(" — ")[0]
    return head[head.index("(") + 1:head.rindex(")")]


def split_params(params: str) -> list[str]:
    """The top-level parameters of a signature, one entry each.

    Depth-aware because a nested shape is rendered inline: `fs.edit` has an optional
    `search` of its own and a required `search` inside each of its `operations`, so
    "is `search*` in the signature" is a question with two answers and the wrong one
    is how a test comes to accept a dropped parameter.
    """
    entries, depth, current = [], 0, ""
    for char in params:
        if char in "{<":
            depth += 1
        elif char in "}>":
            depth -= 1
        if char == "," and depth == 0:
            entries.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        entries.append(current.strip())
    return entries


def flat(block: str) -> str:
    """A block as one line of prose, for assertions on wording rather than layout.

    These prompts are hand-wrapped at about eighty columns, so a phrase can straddle a
    newline, and an assertion on the raw text would be asserting on where somebody
    happened to break a line.
    """
    return " ".join(block.split())


def registered_tools() -> list[dict[str, Any]]:
    """Every tool the toolhost registers, exactly as `Toolhost.catalog` would."""
    found: dict[str, dict[str, Any]] = {}
    for module in pkgutil.iter_modules(tools_package.__path__):
        mod = importlib.import_module(f"ethos.toolhost.tools.{module.name}")
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Tool) and obj is not Tool and getattr(obj, "name", None) \
                    and hasattr(obj, "input_schema"):
                found[obj.name] = {
                    "name": obj.name,
                    "description": obj.description,
                    "input_schema": obj.input_schema,
                }
    return sorted(found.values(), key=lambda t: t["name"])


# ----------------------------------------------------------------- the shapes


def test_a_parameter_carries_its_type():
    """The whole point. A name with no type is a name to guess at."""
    assert signature_of("fs.read", input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }) == "- `fs.read`(path*: string)"


def test_required_and_optional_are_distinguished():
    """
    `required` is the difference between a call that works and one that is refused.

    A prompt that lists six parameters with no marker gives the agent no way to tell
    which of them it must supply, so it either supplies all six — several of them
    meaningful, all of them wrong — or supplies one and is refused. Both happen; the
    second is the one that ends in an error message about a field it was told it
    could leave out.
    """
    assert signature_of("intent.close", input_schema={
        "type": "object",
        "properties": {
            "intention_id": {"type": "string"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["intention_id", "status", "reason"],
    }) == "- `intent.close`(intention_id*: string, status*: string, reason*: string)"


def test_an_enum_is_rendered_as_the_only_values_allowed():
    """A free string where there is a list of three is a call that gets refused."""
    assert 'method: "GET"|"POST"' in signature_of("http.request", input_schema={
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "PATCH"]},
        },
    })


def test_a_parameter_description_survives():
    """
    Three schemas carry one, and one of them is the only thing explaining that
    `spend_usd` is a declaration to the H2 check rather than a payment.
    """
    assert "line to start from" in signature_of("fs.read", input_schema={
        "type": "object",
        "properties": {
            "offset": {"type": "integer", "description": "line to start from"},
        },
    })


def test_an_array_of_objects_keeps_the_shape_of_its_items():
    """
    The one distinction the old one-line format could not carry at all.

    `operations` was rendered as a bare word between two names that were strings. An
    agent could only guess it was a string itself, or a list of strings, and neither
    is the shape — so a multi-edit meant writing the file once per operation, or
    falling back to a diff it had not been told how to compose.
    """
    line = signature_of("fs.edit", input_schema={
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"search": {"type": "string"},
                                   "replace": {"type": "string"}},
                    "required": ["search", "replace"],
                },
            },
        },
    })
    assert "array<object{search*: string, replace*: string}>" in line


def test_a_free_form_object_is_not_an_empty_one():
    """
    `headers` and `json_body` take any keys, and an object rendered as `object{}`
    reads as an object with no keys at all — the one shape that is never right.
    """
    assert "headers: object{free-form}" in signature_of("http.request", input_schema={
        "type": "object",
        "properties": {"headers": {"type": "object"}},
    })


def test_an_array_of_scalars():
    assert "statuses: array<string>" in signature_of("intent.list", input_schema={
        "type": "object",
        "properties": {
            "statuses": {"type": "array", "items": {"type": "string"}},
        },
    })


def test_a_field_whose_type_is_not_what_its_neighbours_look_like():
    """
    `fs.write.binary_b64` is a string sitting among three booleans, and the old
    format listed all four as bare names — so it read as a fourth flag. An agent
    writing a binary file passed `true`, and the error named a parameter it had been
    told was a switch.
    """
    assert "append: boolean, binary_b64: string" in signature_of("fs.write", input_schema={
        "type": "object",
        "properties": {
            "append": {"type": "boolean"},
            "binary_b64": {"type": "string"},
        },
    })


def test_a_tool_that_takes_no_arguments_is_shown_as_taking_none():
    """Not `[-]`, which is what four of them used to render as."""
    assert render([{"name": "secrets.list", "description": "List keys.",
                    "input_schema": {"type": "object", "properties": {}}}]
                  ).split("\n")[-1] == "- `secrets.list`() — List keys."


def test_a_schema_with_no_type_is_not_guessed_at():
    """
    `any` rather than an inference.

    A wrong type here is worse than an absent one: the block is read as
    authoritative, so a confident wrong answer becomes a refused call whose error
    message contradicts the prompt it was given. A toolshed tool is written by hand
    against a schema the renderer has never seen, so this is reachable.
    """
    assert "payload: any" in signature_of("tools.run", input_schema={
        "type": "object", "properties": {"payload": {"description": "anything"}},
    })


def test_a_missing_schema_does_not_raise():
    """The one caller that can pass nothing at all is `assemble` with no toolhost."""
    assert "- `secrets.get`(key*: string)" in render([
        {"name": "secrets.get", "description": "", "input_schema": {"type": "object", "properties": {
            "key": {"type": "string"}}, "required": ["key"]}},
        {"name": "bare", "description": "No schema at all."},
    ])


def test_no_tools_is_said_plainly():
    assert render([]) == "## Tools\n(none registered)"


# -------------------------------------------------------------- the whole block


def test_the_block_explains_the_call_and_the_result():
    """
    Neither half was stated anywhere the agent could act on it.

    The call shape is in `STEP_PROMPT`, which is not in front of the agent when it is
    choosing what to do, and the result shape is in neither: the agent gets
    `ok: true, content` or `ok: false, error_code, error` and has to infer from an
    example in a per-call prompt written for a different job.
    """
    block = render(registered_tools())
    assert '"tool"' in block and '"args"' in block
    assert "ok: true" in block and "content" in block
    assert "ok: false" in block and "error_code" in block and "error" in block


def test_the_block_says_a_refusal_is_an_answer():
    """
    Without this, a guardian refusal is one more `ok: false` and the agent has three
    bad options: report the work as done, retry the call it was just refused, or
    rephrase the same action until it slips past the classifier. The third is the one
    H1–H6 exist to prevent, and it is the only one available when nothing tells the
    agent the refusal was deliberate.
    """
    lowered = flat(render(registered_tools())).lower()
    assert "refused" in lowered
    assert "not a malfunction" in lowered
    assert "do the permitted thing instead" in lowered


def test_every_registered_tool_appears_with_its_own_name():
    """
    A tool dropped from the block is a tool the agent does not know exists.

    Rendered from the same classes the toolhost registers, so this fails the day a
    tool is added and the block is not taught to include it — which it cannot be,
    since it takes whatever the toolhost hands it. The check is that nothing is lost
    in between.
    """
    block = render(registered_tools())
    for tool in registered_tools():
        assert f"`{tool['name']}`" in block, tool["name"]


def test_no_registered_tool_renders_an_empty_signature_by_accident():
    """
    The regression this whole file exists for, in the one form that is checkable.

    A tool whose properties failed to render would appear in the block looking
    perfectly well formed — right name, right description, no parameters — and the
    agent would read that as a tool that takes none. So every declared parameter has
    to be present, marked required exactly when the schema says so.
    """
    for tool in registered_tools():
        entries = split_params(params_of(render([tool]).split("\n")[-1]))
        declared = (tool["input_schema"] or {}).get("properties") or {}
        required = set((tool["input_schema"] or {}).get("required") or ())
        assert len(entries) == len(declared), tool["name"]
        for name in declared:
            entry = next((e for e in entries
                          if e.startswith((f"{name}:", f"{name}*:"))), None)
            assert entry is not None, f"{tool['name']}.{name}"
            assert entry.startswith(f"{name}*:") is (name in required), \
                f"{tool['name']}.{name} rendered as {entry!r}"


def test_a_tool_with_no_parameters_is_the_only_one_with_an_empty_signature():
    """The check that the one above cannot make: that the empty case means empty."""
    for tool in registered_tools():
        declared = (tool["input_schema"] or {}).get("properties") or {}
        rendered_empty = params_of(render([tool]).split("\n")[-1]).strip() == ""
        assert rendered_empty is (len(declared) == 0), tool["name"]


def test_the_catalog_is_not_much_larger_than_it_has_to_be():
    """
    The block is in the cached prefix, so its size is paid on every turn of every
    thread at the cache-read price. It grew from a thousand tokens to nearly two
    thousand when the schemas came back, and that is a trade worth making once — a
    refused call costs a whole round trip — but not one worth making twice. This is
    the guard on the trade, and it fails if a tool with a paragraph of description is
    added to a registry that has no reason to hold it.
    """
    assert len(render(registered_tools())) // 4 < 2500


@pytest.mark.parametrize("tool", registered_tools(), ids=lambda t: t["name"])
def test_a_tool_never_renders_onto_two_lines(tool):
    """
    One line per tool, so a description cannot run into the next tool's signature.

    A tool description that ends mid-sentence — a truncated description, or one
    containing a newline from a hand-written toolshed manifest — would otherwise
    push its signature onto a line of its own where it reads as prose.
    """
    block = render([tool])
    assert len([ln for ln in block.splitlines() if ln.startswith("- ")]) == 1
