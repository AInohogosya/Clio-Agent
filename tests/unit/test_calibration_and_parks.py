"""Measured quality, environment blockages, and the park that asks a person.

Three mechanisms from the same field record (2026-11-08..10): a catalogue that
scored an unproven model 1.0 and routed everything to it; five parks of the
same goals with nobody told; and a reopen sweep that resurrected every one of
them on a backoff, to park again.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from ethos.config import EthosConfig
from ethos.gateway.calibration import (
    QualityCalibration,
    effective_quality,
    write_model_quality,
)
from ethos.gsl.loop import GeneralSolverLoop, _environment_blocked
from tests.conftest import make_config


# ------------------------------------------------------------- calibration


def test_effective_quality_scales_by_measured_error_rate():
    # A 0.90 opinion with a 30% error rate scores 0.765: below a healthy
    # 0.90 rival, above a 0.70 fallback. Half weight, on purpose.
    assert effective_quality(0.90, {"calls": 100, "ok": 70}) == 0.765
    # A perfect record leaves the opinion untouched.
    assert effective_quality(0.90, {"calls": 100, "ok": 100}) == 0.90
    # No record, no judgement.
    assert effective_quality(0.90, None) == 0.90
    assert effective_quality(0.90, {}) == 0.90
    # A model that failed everything loses half its opinion, not all of it:
    # it may still be the only thing that can serve a tier.
    assert effective_quality(0.90, {"calls": 40, "ok": 0}) == 0.45
    # Degenerate records are unjudged rather than dividing by zero.
    assert effective_quality(0.90, {"calls": 0, "ok": 0}) == 0.90


def test_the_calibration_file_round_trips(tmp_path: Path):
    stats = {"openai_compat::gemma4:31b-cloud": {"calls": 50, "ok": 45, "ok_rate": 0.9}}
    write_model_quality(tmp_path, stats)
    calibration = QualityCalibration(tmp_path)
    assert calibration.effective_quality(0.90, "openai_compat", "gemma4:31b-cloud") == 0.855
    # An entry with no record keeps its catalogue opinion.
    assert calibration.effective_quality(1.0, "openai_compat", "stealth/crouton-alpha") == 1.0


def test_a_fresh_file_is_seen_without_a_restart(tmp_path: Path):
    calibration = QualityCalibration(tmp_path)
    assert calibration.effective_quality(1.0, "p", "m") == 1.0
    write_model_quality(tmp_path, {"p::m": {"calls": 10, "ok": 5}})
    calibration._loaded_at = 0.0  # expire the reload window, as the timer would
    assert calibration.effective_quality(1.0, "p", "m") == 0.75


# ------------------------------------------------- environment blockages


def test_park_reasons_that_name_the_world_are_environment_blockages():
    assert _environment_blocked(
        "8 consecutive planning failures (understand): [openai_compat] HTTP 402: "
        '{"error":{"message":"Add usage credits"}}'
    )
    assert _environment_blocked("manual approval timeout: the command did not run")
    assert _environment_blocked("budget exhausted ($0.42 of $0.40)")
    assert _environment_blocked("no model is eligible for tier=T3")
    # The loop's own failures stay the loop's to retry.
    assert not _environment_blocked("verification failed after all strategies: the artifact ...")
    assert not _environment_blocked("8 consecutive planning failures (plan): unusable JSON")
    assert not _environment_blocked("")


# --------------------------------------------------------- park escalation


class _FakeMemory:
    def __init__(self, intention: SimpleNamespace | None = None) -> None:
        self.notices: list[tuple[str, str, str]] = []
        self.updated: list[dict[str, Any]] = []
        self.closed: list[tuple[str, str]] = []
        self.questions: list[str] = []
        self.intention = intention

    async def update_intention(self, intention_id, resume_state=None, **_: Any) -> None:
        self.updated.append({"id": intention_id, "resume_state": resume_state})
        if self.intention is not None and resume_state is not None:
            self.intention.resume_state = dict(resume_state)

    async def close_intention(self, intention_id, status, reason) -> None:
        self.closed.append((str(intention_id), status))

    async def open_question(self, text: str, **_: Any) -> None:
        self.questions.append(text)

    async def record_owner_decision_request(self, intention, stage: str, detail: str = ""):
        self.notices.append((str(intention.id), stage, detail))
        return SimpleNamespace(person_id="owner", stage=stage)

    async def record_stage_notice(self, intention, stage: str, detail: str = ""):
        return None


class _FakeConfig:
    def __init__(self) -> None:
        self.gsl = SimpleNamespace(park_reopen_max=3, plan_attempts_before_park=8)


def _intention(**over: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": uuid4(), "title": "build the thing", "resume_state": {},
    }
    fields.update(over)
    return SimpleNamespace(**fields)


def _loop() -> GeneralSolverLoop:
    loop = GeneralSolverLoop.__new__(GeneralSolverLoop)
    loop.config = _FakeConfig()
    loop.memory = _FakeMemory()
    return loop


async def test_an_environment_blocked_park_escalates_to_the_owner_and_is_flagged():
    loop = _loop()
    memory: _FakeMemory = loop.memory  # type: ignore[assignment]
    intention = _intention()
    await loop._park(
        intention,
        "8 consecutive planning failures (understand): [openai_compat] HTTP 402: add credits",
    )
    assert memory.updated[0]["resume_state"]["blocked_on_environment"] is True
    assert memory.updated[0]["resume_state"]["park_count"] == 1
    assert len(memory.notices) == 1
    assert memory.notices[0][1] == "parked-escalation-1"
    assert "402" in memory.notices[0][2]


async def test_the_final_park_escalates_even_without_an_environment_block():
    intention = _intention()
    loop = _loop()
    loop.memory = _FakeMemory(intention)  # type: ignore[assignment]
    memory: _FakeMemory = loop.memory  # type: ignore[assignment]
    for _ in range(3):
        await loop._park(intention, "verification failed after all strategies: the artifact ...")
    assert memory.updated[-1]["resume_state"]["park_count"] == 3
    assert len(memory.notices) == 1
    assert memory.notices[0][1] == "parked-escalation-3"


async def test_an_ordinary_first_park_escalates_to_nobody():
    loop = _loop()
    memory: _FakeMemory = loop.memory  # type: ignore[assignment]
    await loop._park(_intention(), "verification failed after all strategies: the artifact ...")
    assert memory.notices == []
    assert memory.updated[0]["resume_state"]["park_count"] == 1
    assert "blocked_on_environment" not in memory.updated[0]["resume_state"]
