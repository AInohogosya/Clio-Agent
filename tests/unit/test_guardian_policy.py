"""The CEL policy engine and run assertions: deny-before-allow, fail-closed."""
from __future__ import annotations

import pytest

from clio.guardian.policy import (
    PolicyEngine,
    PolicyError,
    assertion_admits,
    call_environment,
    evaluate_policy_expression,
    evaluate_strict,
    high_impact_external_side_effect,
    mint_assertion,
    verify_assertion,
)

ENV = {
    "tool": "shell.run",
    "args": {"command": "rm -rf /tmp/x", "path": "/tmp/x"},
    "space_id": "primary",
    "delegation_depth": 2,
    "initiator_kind": "gsl_step",
    "project_id": None,
}


class FakePolicyDB:
    def __init__(self, rules: list[dict]) -> None:
        self.rules = rules

    async def fetch(self, query: str, *args):
        return [
            {**{"priority": 100, "description": "", "version": 1}, **r}
            for r in self.rules if r.get("enabled", True)
        ]

    async def execute(self, query: str, *args):
        return "OK"


# ---- the expression language ------------------------------------------------


def test_a_deny_expression_matches():
    expr = 'tool == "shell.run" && args.command.matches("rm\\\\s+-rf")'
    assert evaluate_policy_expression(expr, ENV) is True


def test_membership_and_method_calls():
    assert evaluate_policy_expression('tool in ["shell.run", "fs.write"]', ENV) is True
    assert evaluate_policy_expression('args.path.startsWith("/tmp/")', ENV) is True
    assert evaluate_policy_expression('has(args.path) && args.path.contains("tmp")', ENV) is True
    assert evaluate_policy_expression("size(args) > 1", ENV) is True


def test_a_broken_expression_fails_closed():
    for broken in ('tool === "x"', "args.nope.deep", "tool == ", 'unknown_var == "x"',
                   "size(5) > 1", "", 'tool.matches("([")'):
        assert evaluate_policy_expression(broken, ENV) is False, broken


def test_a_missing_attribute_denies_even_inside_a_call():
    assert evaluate_policy_expression('args.channel == "slack"', ENV) is False
    assert evaluate_policy_expression("args.channel.startsWith('s')", ENV) is False


def test_has_is_the_one_call_that_may_ask_about_a_missing_attribute():
    assert evaluate_policy_expression("has(args.channel)", ENV) is False
    assert evaluate_policy_expression("has(args.path)", ENV) is True


def test_evaluate_strict_raises_for_the_error_path():
    with pytest.raises(PolicyError):
        evaluate_strict("args.nope.deep", ENV)


# ---- the engine: deny before allow ------------------------------------------


async def test_deny_is_evaluated_before_allow():
    db = FakePolicyDB([
        {"policy_id": "allow-shell", "effect": "allow", "expression": 'tool == "shell.run"'},
        {"policy_id": "deny-rm", "effect": "deny", "expression": 'args.command.matches("rm\\\\s")'},
    ])
    engine = PolicyEngine(db)
    await engine.load()
    decision = await engine.decide(call_environment("shell.run", {"command": "rm -rf /"}, project_id=None, space_id="primary", initiator_kind="gsl_step", delegation_depth=0))
    assert decision.effect == "deny"
    assert decision.rule_id == "deny-rm"


async def test_an_allow_rule_pre_authorizes():
    db = FakePolicyDB([
        {"policy_id": "allow-deploy", "effect": "allow",
         "expression": 'tool == "shell.run" && args.command.matches("deploy")',
         "description": "project pre-authorized its own deploy script"},
    ])
    engine = PolicyEngine(db)
    await engine.load()
    decision = await engine.decide(call_environment("shell.run", {"command": "./deploy.sh"}, project_id=None, space_id="primary", initiator_kind="gsl_step", delegation_depth=0))
    assert decision.effect == "allow" and decision.rule_id == "allow-deploy"


async def test_an_erroring_deny_rule_still_denies_and_an_erroring_allow_never_allows():
    db = FakePolicyDB([
        {"policy_id": "broken-allow", "effect": "allow", "expression": "args.nope.deep"},
        {"policy_id": "broken-deny", "effect": "deny", "expression": "tool === "},
    ])
    engine = PolicyEngine(db)
    await engine.load()
    decision = await engine.decide(ENV)
    assert decision.effect == "deny", "the deny rule could not be evaluated, so deny"


async def test_unmatched_calls_fall_through_to_the_ladder():
    db = FakePolicyDB([
        {"policy_id": "deny-email", "effect": "deny", "expression": 'tool == "comms.send"'},
    ])
    engine = PolicyEngine(db)
    await engine.load()
    decision = await engine.decide(ENV)
    assert decision.effect == "allow" and decision.rule_id is None


async def test_a_true_deny_rule_is_default_deny():
    db = FakePolicyDB([{"policy_id": "paranoia", "effect": "deny", "expression": "true"}])
    engine = PolicyEngine(db)
    await engine.load()
    decision = await engine.decide(ENV)
    assert decision.effect == "deny"


# ---- run assertions -----------------------------------------------------------


def test_a_minted_assertion_verifies():
    token = mint_assertion(project_id="p1", space_id="primary",
                           initiator_kind="gsl_step", delegation_depth=2)
    assertion = verify_assertion(token)
    assert assertion is not None
    assert assertion.project_id == "p1"
    assert assertion.initiator_kind == "gsl_step"
    assert assertion.delegation_depth == 2


def test_an_assertion_is_bound_to_its_initiator_kind():
    token = mint_assertion(project_id=None, space_id="primary",
                           initiator_kind="subagent", delegation_depth=3)
    assertion = verify_assertion(token)
    assert assertion.initiator_kind == "subagent"
    with pytest.raises(ValueError):
        mint_assertion(project_id=None, space_id="primary",
                       initiator_kind="root", delegation_depth=0)


def test_an_expired_assertion_is_no_assertion():
    token = mint_assertion(project_id=None, space_id="primary",
                           initiator_kind="human_chat", delegation_depth=0, ttl_s=-1.0)
    assert verify_assertion(token) is None


def test_a_tampered_assertion_is_no_assertion():
    token = mint_assertion(project_id=None, space_id="primary",
                           initiator_kind="human_chat", delegation_depth=0)
    payload, signature = token.rsplit(".", 1)
    assert verify_assertion(f"{payload}.{'0' * len(signature)}") is None
    assert verify_assertion(token[:-2] + "zz") is None


def test_assertion_admission_checks_depth_and_space():
    token = mint_assertion(project_id="p", space_id="primary",
                           initiator_kind="subagent", delegation_depth=3)
    assertion = verify_assertion(token)
    assert assertion_admits(assertion, required_depth=3, space_id="primary")
    assert not assertion_admits(assertion, required_depth=2, space_id="primary"), \
        "depth can only shrink as work is handed down"
    assert not assertion_admits(assertion, required_depth=3, space_id="other")


def test_a_wrong_key_mints_a_token_the_runtime_refuses():
    token = mint_assertion(project_id=None, space_id="primary",
                           initiator_kind="human_chat", delegation_depth=0,
                           key=b"\x00" * 32)
    assert verify_assertion(token) is None


# ---- high-impact classification ------------------------------------------------


def test_production_deploys_need_a_card():
    assert high_impact_external_side_effect("shell.run", {"command": "./deploy.sh production"}) == "production deploy"
    assert high_impact_external_side_effect("shell.run", {"command": "kubectl apply -f x.yaml"}) == "production deploy"
    assert high_impact_external_side_effect("shell.run", {"command": "npm test"}) is None


def test_mounts_and_unapproved_channels_need_a_card():
    assert high_impact_external_side_effect("shell.run", {"command": "mount /dev/sdb1 /mnt"}) == "host-boundary mount"
    assert high_impact_external_side_effect(
        "comms.send", {"channel": "email"}, approved_channels={"cli", "web"},
    ) == "external message over unapproved channel 'email'"
    assert high_impact_external_side_effect(
        "comms.send", {"channel": "web"}, approved_channels={"cli", "web"},
    ) is None


def test_external_writes_to_production_endpoints_need_a_card():
    assert high_impact_external_side_effect(
        "http.request", {"url": "https://prod.example.com/api/reset", "method": "POST"},
    ) == "external write to a production endpoint"
    assert high_impact_external_side_effect(
        "http.request", {"url": "https://api.example.com/", "method": "GET"},
    ) is None
