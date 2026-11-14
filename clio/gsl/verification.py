from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clio.config import ClioConfig
from clio.gsl.acceptance import derive_checks, is_code, uncovered_paths
from clio.gsl.evaluator import evaluate_objective
from clio.observability.logger import get_logger
from clio.parse import parse_json_block
from clio.prompts import VERIFIER_PROMPT, dumps
from clio.schemas.gsl import EvaluationResult, VerificationResult
from clio.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("clio.gsl.verification")


def gate_children(
    goal: str, gate_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The verifies + synthesizes children a gate creates, if gated.

    Returns [] when there is no gate policy. The verifies child is judged by
    an independent diversity-family T3 model or peer resident; the
    synthesizes child only unlocks after verification passes is enforced by
    the caller ordering them via `depends_on` edges.
    """
    policy = gate_policy or {}
    if not policy:
        return []
    family = str(policy.get("verifier_family") or "independent")
    return [
        {
            "title": f"Verify (gate): {goal}"[:200],
            "desired_end_state": f"Independent T3 verification of {goal} (family={family})",
            "edge_type": "verifies",
            "gate_policy": dict(policy),
        },
        {
            "title": f"Synthesize (gate): {goal}"[:200],
            "desired_end_state": f"Synthesize verified outcome of {goal}",
            "edge_type": "synthesizes",
            "gate_policy": {},
        },
    ]


def gate_blocks_completion(
    verification: Any, gate_policy: dict[str, Any] | None = None,
) -> str | None:
    """Why a gate holds completion, or None when it does not.

    The invariant: a model judge can never overturn failing objective checks.
    A `pass` verdict alongside `checks_failed` is a block, not a pass.
    """
    policy = gate_policy or {}
    if not policy:
        return None
    if verification is None:
        return "gate requires a verification result"
    objective = getattr(verification, "objective", None)
    notes = list(getattr(objective, "notes", []) or []) if objective is not None else []
    checks_failed = [n for n in notes if n.startswith("failed check:")]
    verifier = getattr(verification, "verifier", None) or {}
    verdict = str(verifier.get("verdict", ""))
    if checks_failed and verdict == "pass":
        return (
            "gate: the independent verifier passed over failing objective checks; "
            "the failing checks stand and completion is blocked"
        )
    if policy.get("require_verifier_pass", True) and verdict in ("fail", "needs_human"):
        return f"gate: verifier verdict is {verdict!r}, not pass"
    return None


def diverse_provider_hint(config: ClioConfig, producer_family: str | None) -> str | None:
    """A provider from a different diversity family than the producer's, or None.

    The anti-echo rule for rung 2: the judge should not share a model family
    with the model that produced the work, or a family's shared blind spots
    become the verification's blind spots too. Answering `None` keeps the
    request routable anywhere.
    """
    if not producer_family:
        return None
    for entry in config.models.models:
        if entry.diversity_family != producer_family and "T3" in entry.tiers:
            return entry.provider
    return None


async def run_model_verifier(
    gateway: Any,
    config: ClioConfig,
    *,
    goal: str,
    criteria: list[dict[str, Any]],
    artifact_text: str,
    producer_family: str | None,
    purpose: str,
    thread_id: str | None = None,
    model_hint: str | None = None,
    extra_messages: list[str] | None = None,
) -> dict[str, Any]:
    """The rung-2 judge, as one model call shared by every path that reaches it.

    The gateway fallback and the verifier thread were two copies of this call,
    and they had already drifted — the thread read its diversity hint under a
    key the ladder was not sending, so on the thread path the judge silently
    lost the "different model family" instruction and echoed the producer's
    family. One implementation is the fix for that class of drift: same prompt,
    same tier, same parse, and the same refusal to guess — a verifier that
    cannot be asked answers `needs_human`, never a verdict it did not hear.

    `artifact_text` arrives whole — chunking and coverage are the caller's
    contract (`clio.gsl.verifier_inspect`), and this function truncates
    nothing. `extra_messages` is the chunk framing the map pass hands its
    judges: goal restated for the chunk, the place this chunk holds in the
    whole, and the rule that an unreadable chunk is `needs_human`.
    """
    system = VERIFIER_PROMPT.format(
        goal=goal,
        criteria=dumps(criteria),
        artifact=artifact_text,
    )
    if extra_messages:
        system = system + "\n\n" + "\n".join(extra_messages)
    request = ModelRequest(
        tier=Tier.T3,
        purpose=purpose,
        thread_id=thread_id,
        provider_hint=diverse_provider_hint(config, producer_family),
        model_hint=model_hint,
        messages=[
            ModelMessage(role=Role.system, content=system),
            ModelMessage(role=Role.user, content="Judge the artifact."),
        ],
        max_tokens=1200,
    )
    try:
        response = await gateway.complete(request)
        data = parse_json_block(response.text or "")
        if data:
            return data
    except Exception:
        logger.exception("verification.verifier_call_failed")
    return {"verdict": "needs_human", "issues": ["verifier call failed"], "confidence": 0.0}


class VerificationLadder:
    """Three-rung verification ladder [D-06, 6.2]:

    1. Objective checks (linters, tests, types, schema validation).
    2. Independent Verifier Thread: alternative model provider, sees goal and
       artifact only — never the producer's reasoning.
    3. Human review for commissioned criteria of type 'human'.

    Consequential work MUST reach T3 either in the deciding call or at rung 2
    (capability floor, P4).

    Three rules hold the ladder together, and each of them is about the *order*
    of authority between a machine and a model.

    **A check that ran outranks a model that says so.** Rung 2 used to be able
    to overturn a failed rung 1 outright: `verdict == "pass"` set
    `result.passed = True` whatever the checks had said. So a test suite that
    failed was overruled by a model reading a summary of the plan's own step
    descriptions, and the goal was closed. A judge may now add a verdict where
    there was no evidence to contradict, and may not overturn one. The
    contradiction is recorded rather than dropped, so the two answers are both
    visible.

    **Nothing checked is not passed.** Code changed, no check runnable, and
    `passed: True` — that is how a coding task reached "done" with its tests
    never executed, since a commission arrives with no success criteria and an
    empty criteria list was scored as a pass. `unverified_is_failure` makes that
    a failure, with the reason naming what could not be run.

    **The judge is shown the code.** The artifact used to be a dict of step
    descriptions and the last few tool results, which is a description of the
    work rather than the work; a judge asked to rule on a description will rule
    in its favour. The diffs each edit returned and the output of the checks
    that ran are in it now, so rung 2 has something to disagree with.
    """

    def __init__(
        self,
        config: ClioConfig,
        gateway: Any,
        threads: Any,
        comms_out: Any = None,
        audit: Any = None,
    ):
        self.config = config
        self.gateway = gateway
        self.threads = threads
        self.comms_out = comms_out
        self.audit = audit

    def _derive(
        self, changed: list[Path], criteria: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Checks derived from the files the work touched, if the switch is on.

        Not derived when the work has already stated its own checks: a criterion
        that says `pytest tests/unit` knows more about the project's own
        expectations than a marker file does, and running both spends the cycle
        twice to learn the same thing. What is always derived is the syntax
        check, which no criterion states and which is the one that catches a
        file that will not load at all.
        """
        cfg = self.config.gsl
        if not changed or not getattr(cfg, "verify_code", True):
            return [], []
        try:
            derived = derive_checks(
                changed,
                timeout_s=cfg.code_check_timeout_s,
                max_commands=cfg.code_check_max_commands,
            )
        except OSError as exc:
            # Derivation globs the filesystem and reads markers. A directory it
            # cannot read is a fact about the machine, not a reason to unwind
            # through `_finish_goal`, `advance`, `act` and `_cycle` -- and an
            # intention whose verification raised is an intention the loop can
            # no longer make progress on, so it would be stuck on the same step
            # for as long as the agent ran.
            logger.exception("verification.derive_failed")
            return [], [f"the checks for this work could not be derived: {exc}"]
        has_stated_test = any(
            str(c.get("type")) == "command" for c in criteria or []
        )
        checks = list(derived.checks)
        if has_stated_test:
            checks = [c for c in checks if c.get("type") == "syntax"]
        return checks, derived.notes

    async def verify(
        self,
        *,
        intention: Any,
        criteria: list[dict[str, Any]],
        artifact: Any,
        evidence: dict[str, Any] | None = None,
        producer_family: str | None = None,
        cwd: Any = None,
        changed_paths: list[Path] | None = None,
    ) -> VerificationResult:
        result = VerificationResult()
        result.rung_reached = 0

        objective_checks = [c for c in criteria if c.get("type") not in ("human", "none", "")]
        derived, derive_notes = self._derive(list(changed_paths or []), criteria)
        result.changed_files = [str(p) for p in (changed_paths or [])]
        result.derived_checks = [
            str(c.get("label") or c.get("type")) for c in derived
        ]
        try:
            objective = await evaluate_objective([*objective_checks, *derived], artifact, cwd)
        except Exception:
            # The ladder is the last thing standing between the work and the
            # word "done", so an exception here cannot be allowed to become
            # either a crash or a pass. It becomes the one thing it is allowed to
            # be: unverified.
            logger.exception("verification.objective_failed")
            objective = EvaluationResult(
                passed=False, verified=False, checks_run=0,
                notes=["the objective checks could not be run at all"],
            )
        for note in derive_notes:
            if note not in objective.notes:
                objective.notes.append(note)
        result.objective = objective
        result.rung_reached = 1
        result.passed = objective.passed
        result.issues.extend(objective.notes)

        # The two ways a mechanical rung can be unsatisfied, kept apart because
        # they need different words in the report. One is a check that ran and
        # failed; the other is no check that could look at the work at all,
        # which is the case a bare `passed: bool` cannot tell apart from success.
        checks_failed = [n for n in objective.notes if n.startswith("failed check:")]
        code_changed = [p for p in (changed_paths or []) if is_code(p)]
        uncovered = uncovered_paths(code_changed, objective.evidence or {})
        unverified = bool(uncovered) or (bool(code_changed) and not objective.verified)
        # The third way a mechanical rung can be unsatisfied, and the one NA-P2.2
        # names directly: the work *stated* what would make it done, and no check
        # that ran looked at any of it. This used to slip through when the work
        # changed no code the ladder recognised -- a goal whose criteria said
        # "the deploy script runs green" and whose changed files were config and
        # prose had zero checks run, scored as a pass, and closed as verified on
        # the strength of a judge that had nothing to contradict. Stated
        # requirements that were never checked are unverified whether or not a
        # code file was touched, so they block the same way.
        stated = [c for c in criteria if c.get("type") not in ("human", "none", "")]
        stated_unchecked = bool(stated) and objective.checks_run == 0
        # Whether that is a failure or a note. Kept separate from `unverified`
        # because the switch has to reach the judge's vote as well: turning it off
        # means "let the judge decide this one", and leaving it out of the rung-2
        # gate made the switch a way to park work forever and nothing else.
        blocked = (
            (unverified or stated_unchecked) and self.config.gsl.unverified_is_failure
        )
        # Recorded whether or not it blocks. The gap is a fact about the work,
        # and an operator reading the result after turning the switch off needs
        # to see which files nothing looked at -- that is the whole cost of the
        # trade they just made.
        result.uncovered_files = [str(p) for p in uncovered]
        if stated_unchecked:
            result.issues.append(
                f"{len(stated)} stated requirement(s) were never checked by any "
                f"check that ran, so the work is unverified rather than verified"
            )
        if blocked:
            result.passed = False
            named = uncovered or code_changed
            if named:
                result.issues.append(
                    f"code was changed in {len(named)} file(s) and no check could "
                    f"look at {len(uncovered) if uncovered else len(named)} of them, "
                    f"so the work is unverified rather than verified: "
                    f"{', '.join(str(p) for p in named[:3])}"
                )

        human_criteria = [c for c in criteria if c.get("type") == "human"]
        consequential = (
            intention.commissioned_by is not None
            or getattr(intention, "kind", "") in ("venture", "commitment")
            or objective.difficulty >= 0.6
        )
        needs_rung2 = (
            not result.passed
            or consequential
            or bool(criteria) and not objective_checks
        )
        if needs_rung2:
            verifier_result = await self._independent_verifier(
                intention, criteria, artifact, producer_family,
            )
            result.verifier = verifier_result
            result.rung_reached = 2
            verdict = str(verifier_result.get("verdict", "needs_human"))
            if verdict == "pass" and not checks_failed and not blocked:
                result.passed = True
            elif verdict == "pass":
                # The judge and the machine disagree, and the machine is the one
                # that ran the tests. Recorded as an issue rather than resolved,
                # because a verdict that contradicts a failing test suite and
                # says nothing about it is the exact shape of the failure this
                # rule exists to stop.
                result.passed = False
                reason = (
                    "the independent verifier passed this, over "
                    + ("a failing check" if checks_failed else "work no check could examine")
                )
                result.issues.append(reason)
                await self._record_contradiction(intention, reason, objective, verifier_result)
            elif verdict == "needs_human" or human_criteria:
                result.human_pending = True
                result.passed = False
                result.issues.append("independent verifier requests human review")
            else:
                result.passed = False
                result.issues.extend(
                    [str(i) for i in verifier_result.get("issues", [])][:10]
                )

        if human_criteria:
            result.rung_reached = 3
            result.human_pending = True
            result.passed = False
            await self._record_human_review_pending(intention, human_criteria, artifact, evidence)

        if self.audit is not None:
            await self.audit.append(
                actor="gsl", category="verification",
                summary=f"verification rung {result.rung_reached} passed={result.passed}",
                details={
                    "intention_id": str(intention.id),
                    "rung": result.rung_reached,
                    "passed": result.passed,
                    "verified": objective.verified,
                    "checks_run": objective.checks_run,
                    "derived_checks": result.derived_checks,
                    "human_pending": result.human_pending,
                },
            )
        return result

    async def _record_contradiction(
        self, intention: Any, reason: str, objective: Any, verdict: dict[str, Any],
    ) -> None:
        """A model's `pass` over a failing check, written down where it can be found.

        Not announced to anybody. It is a fact about the verification, and the
        audit log is where this system keeps facts it has not been asked to
        speak.
        """
        if self.audit is None:
            return
        try:
            await self.audit.append(
                actor="gsl", category="verification",
                summary=f"verifier contradicted the checks: {intention.title}",
                details={
                    "intention_id": str(intention.id),
                    "reason": reason,
                    "failed_checks": [n for n in objective.notes
                                      if n.startswith("failed check:")][:10],
                    "verifier_verdict": verdict.get("verdict"),
                    "verifier_issues": [str(i) for i in verdict.get("issues", [])][:10],
                },
            )
        except Exception:
            logger.exception("verification.contradiction_record_failed")

    async def _independent_verifier(
        self, intention: Any, criteria: list[dict[str, Any]], artifact: Any,
        producer_family: str | None,
    ) -> dict[str, Any]:
        """Rung 2: an independent judge, and it reads all of the work.

        Every artifact goes through `verifier_inspect.inspect_artifacts`: the
        map pass chunks the deliverable so a judge call sees a whole-covering
        slice (never a fixed truncation), each chunk is judged by a model from
        a different diversity family than the producer's, and the reduce merges
        the chunk verdicts under a rule no chunk can bargain with. The single-
        call judge is the special case of one chunk. The verifier-thread path
        stays for the small-artifact case — a whole artifact that fits in one
        chunk still rides the thread for its process isolation, and the reduce
        holds the same rule either way.
        """
        from clio.gsl.verifier_inspect import (
            chunk_artifacts,
            inspect_artifacts,
            normalize_artifact_files,
        )

        goal = intention.desired_end_state or intention.title
        # A whole artifact that fits one chunk rides the verifier thread, whose
        # process isolation is worth keeping for the common case; anything that
        # takes a map goes through the chunked inspection.
        try:
            files = normalize_artifact_files(artifact)
            chunks = chunk_artifacts(files)
            if len(chunks) <= 1 and self.threads is not None:
                try:
                    handle = await self.threads.spawn_spec({
                        "goal": (
                            f"Verify artifact for intention '{intention.title}'. "
                            f"Return JSON verdict pass|fail|needs_human with issues."
                        ),
                        "framing": "independent verifier: judge only goal and artifact",
                        "role": "verifier",
                        "parent_intention_id": str(intention.id),
                        "verify_payload": {
                            "goal": goal,
                            "criteria": criteria,
                            "artifact": _artifact_text(artifact),
                            "producer_family": producer_family,
                        },
                        "depth": 1,
                        "spawner_thread_id": "gsl",
                    })
                    result = await self.threads.wait(handle, timeout_s=240)
                    if isinstance(result, dict) and result.get("verdict"):
                        return result
                except Exception:
                    logger.exception("verification.thread_failed")
            return await inspect_artifacts(
                self.gateway, self.config,
                goal=goal,
                criteria=criteria,
                artifact=artifact,
                producer_family=producer_family,
                purpose="gsl.verify",
            )
        except Exception:
            logger.exception("verification.inspect_failed")
        return {"verdict": "needs_human", "issues": ["verifier call failed"], "confidence": 0.0}

    async def _record_human_review_pending(
        self, intention: Any, criteria: list[dict[str, Any]], artifact: Any,
        evidence: dict[str, Any] | None,
    ) -> None:
        """A rung-3 review waiting on a person, recorded rather than announced.

        This used to write a fixed paragraph and send it to whoever had
        commissioned the work: the work's title and end state, its criteria, a
        slice of the artifact, and an instruction to say 'approved'. Every word
        of it was composed here, so the person received a message in the
        agent's voice that the agent had not chosen to write, arriving as an
        ordinary outbound message in an ordinary bubble — indistinguishable,
        at every layer from here on, from something it had actually said.

        The waiting itself is real and still has to be findable, so it goes
        where the rest of the system's facts go: the intention is left in
        `awaiting_review` by the caller, and the criteria and artifact are
        written to the audit log here. What is gone is only the sentence.
        Nothing is said on the person's behalf; the record says what is owed
        and to whom, and the agent can say it in its own words when asked.
        """
        if self.audit is None:
            return
        try:
            await self.audit.append(
                actor="self", category="verification",
                summary=f"human review pending: {intention.title}",
                details={
                    "intention_id": str(intention.id),
                    "commissioned_by": intention.commissioned_by,
                    "criteria": criteria,
                    "desired_end_state": intention.desired_end_state or "",
                    "artifact": _artifact_text(artifact)[:1500],
                    "evidence": evidence or {},
                },
            )
        except Exception:
            logger.exception("verification.human_review_record_failed")


def _artifact_text(artifact: Any) -> str:
    """The artifact whole — the ladder no longer truncates what its judge reads.

    Fixed windows (`artifact[:16000]`) verified a deliverable on its opening
    pages and said nothing about the pages past the edge. Coverage is now the
    chunker's contract: `_independent_verifier` routes every artifact through
    `verifier_inspect.inspect_artifacts`, whose map pass judges all of it and
    whose reduce never lets a silent slice decide. This helper stays for the
    one caller that shows a human a preview, and it truncates nothing either —
    what a person reads is their own screen's problem, not this file's.
    """
    if artifact is None:
        return "(no artifact)"
    if isinstance(artifact, str):
        return artifact
    try:
        return json.dumps(artifact, default=str)
    except (TypeError, ValueError):
        return str(artifact)
