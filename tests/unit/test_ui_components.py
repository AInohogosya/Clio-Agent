"""ClioUI components: typed cards, native rendering, channel fallbacks."""
from __future__ import annotations

from types import SimpleNamespace

from clio.ui.components import (
    ApprovalCard,
    DelegationCard,
    RigExplorer,
    SecretDropzone,
    delegation_card_from_spec,
    fallback_text,
    render,
    rig_explorer_from_state,
    strategy_card_from_candidates,
    undo_entries_from_journal,
)


def test_native_surfaces_get_the_typed_component():
    card = ApprovalCard(request_id="r1", tool="shell.run",
                        reason="deploy to production", command_diff="$ ./deploy.sh prod",
                        risk="high")
    payload = render(card, surface="web")
    assert payload["render"] == "native"
    assert payload["component"]["kind"] == "approval_card"
    assert {a["id"].split(":")[0] for a in payload["component"]["actions"]} == {
        "approve_once", "approve_project", "reject"}


def test_text_channels_get_actionable_fallbacks_not_summaries():
    card = ApprovalCard(request_id="r1", tool="shell.run",
                        reason="production deploy", command_diff="$ ./deploy.sh prod",
                        risk="high")
    for channel in ("slack", "discord", "telegram", "teams", "whatsapp", "line", "email"):
        text = fallback_text(card, channel=channel)
        assert "/approve r1" in text, channel
        assert "/deny r1" in text, channel
        assert "./deploy.sh prod" in text, channel


def test_a_delegation_card_carries_resolution_and_secret_dropzones():
    intention = SimpleNamespace(
        id="node-7", title="Wire the deploy",
        instruction_spec={
            "prerequisites": ["Bridge repo access", "Create the token"],
            "secrets_needed": ["DEPLOY_TOKEN"],
            "secret_labels": {"DEPLOY_TOKEN": "deploy token"},
            "action_required": "Add the token, then mark complete.",
            "resolves_human_node_id": "node-7",
        },
    )
    card = delegation_card_from_spec(intention)
    assert card.resolves_human_node_id == "node-7"
    assert [s["text"] for s in card.steps] == ["Bridge repo access", "Create the token"]
    assert card.secrets[0].key == "DEPLOY_TOKEN"
    actions = card.actions()
    assert actions[0].payload["resolves_human_node_id"] == "node-7"
    assert "Mark complete" in actions[0].label


def test_the_fallback_never_asks_for_a_secret_in_chat():
    card = DelegationCard(
        card_id="d1", title="Connect the API", node_id="n1", resolves_human_node_id="n1",
        steps=[{"text": "Create an API key", "done": False}],
        secrets=[SecretDropzone(key="API_KEY", label="api key")],
    )
    text = fallback_text(card, channel="whatsapp")
    assert "settings screen" in text
    assert "transcript" in text, "the reason must be stated"
    assert "/complete n1" in text


def test_rig_explorer_counts_workers_and_gates_and_shows_ledger_metrics():
    ledger = {
        "nodes_done": 4, "nodes_total": 9, "artifacts_touched": ["a.py", "b.py"],
        "budget_spent_usd": 12.5, "blocking_human_tasks": ["Create the DNS record"],
    }
    card = rig_explorer_from_state(
        {"project_id": "p1", "title": "Helmet search"},
        [
            {"id": "n1", "title": "crawl", "status": "done"},
            {"id": "n2", "title": "compare", "status": "in_progress", "worker": "w1"},
            {"id": "n3", "title": "ask owner", "status": "awaiting_human", "is_human_gate": True},
            {"id": "n4", "title": "verify", "status": "in_progress", "worker": "w1"},
        ],
        ledger=ledger,
    )
    data = card.as_dict()
    assert data["active_workers"] == 1
    assert data["human_gates"] == 1
    text = fallback_text(card, channel="telegram")
    assert "4/9 done" in text
    assert "$12.50" in text
    assert "Create the DNS record" in text


def test_strategy_cards_rank_by_value_per_dollar_and_drop_uncited_findings():
    card = strategy_card_from_candidates(
        "s1", "Three ways to automate the report",
        [
            {"approach_id": "a", "summary": "script it", "expected_value": 8, "estimated_cost_usd": 2},
            {"approach_id": "b", "summary": "buy it", "expected_value": 6, "estimated_cost_usd": 4},
            {"approach_id": "c", "summary": "hand it over", "expected_value": 2, "estimated_cost_usd": 1},
        ],
        findings=[
            {"claim": "the script path reuses the existing parser",
             "basis": [{"kind": "artifact", "ref": "art-123"}]},
            {"claim": "vibes say b is best", "basis": []},
        ],
    )
    data = card.as_dict()
    assert data["recommended_id"] == "a", "8/2 beats 6/4 and 2/1"
    assert [f["claim"] for f in data["findings"]] == [
        "the script path reuses the existing parser"
    ]
    assert data["findings_without_basis_dropped"] == 1, \
        "a claim without a citation is not rendered as a finding"
    text = fallback_text(card, channel="slack")
    assert "basis: art-123" in text
    assert "vibes" not in text


def test_undo_entries_emit_undo_actions_only_when_there_is_something_to_undo():
    rows = [
        {"id": "act-1", "tool": "fs.write", "ts_end": "2026-11-15T01:00:00",
         "args_redacted": '{"path": "/tmp/x"}', "undo_ref": "trash-1",
         "result_digest": '{"summary": "wrote config.json"}'},
        {"id": "act-2", "tool": "http.request", "ts_end": "2026-11-15T01:01:00",
         "args_redacted": "{}", "undo_ref": None, "result_digest": "{}"},
    ]
    entries = undo_entries_from_journal(rows)
    first, second = entries
    assert first.as_dict()["actions"][0]["command"] == "/undo act-1"
    assert second.as_dict()["actions"] == [], "nothing restorable, no undo button"
    text = fallback_text(first, channel="discord")
    assert "/undo act-1" in text


def test_render_fallback_keeps_the_native_component_for_the_clients():
    card = RigExplorer(project_id="p", title="t", nodes=[], metrics={})
    payload = render(card, surface="wha" + "tsapp")
    assert payload["render"] == "fallback"
    assert payload["component"]["kind"] == "rig_explorer"
