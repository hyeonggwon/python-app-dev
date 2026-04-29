"""Regression tests for the lint-test → design loopback cap.

Background: `lint-test` has two budgets.

  1. In-stage `lint_test_self_correct` (cap=5): bounds one pass — how many
     LLM rounds before giving up and backtracking. Reset by `_backtrack_to`
     because each re-entry is a fresh attempt.

  2. Cumulative `lint_test_design` (cap=2): bounds how many times the same
     phase can re-enter lint-test via a design backtrack triggered by (1)'s
     cap. Without this, the in-stage counter resets on each backtrack and the
     design ↔ implement ↔ lint-test cycle is bounded only by `total_stages`
     (=200), which the docs never advertised.

These tests pin (2): they were absent in the earlier review and the doc
(`docs/tacit-knowledge.md §4-2`, `docs/stages.md` P4 lint-test row) already
documented this cap as if it were enforced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrate


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _make_run_dir(tmp_path: Path) -> Path:
    rd = tmp_path / "run"
    (rd / "phase-1" / "gates").mkdir(parents=True)
    (rd / "phase-1" / "lint-test").mkdir(parents=True)
    return rd


def _initial_state() -> dict:
    return {
        "schema_version": 1,
        "run_id": "test",
        "harness": "python-app-dev",
        "status": "running",
        "current_stage": "lint-test",
        "current_phase": 1,
        "awaiting_input_schema": None,
        "user_input": {},
        "stage_outputs": {},
        "counters": {
            "planning_revise": 0,
            "requirements_revise": 0,
            "phase_split_revise": 0,
            "total_stages": 50,
        },
        "overrides": {},
        "verdict_history": [],
        "escalation_triggers": [],
        "interview_spec": {"workspace": ""},
    }


def _save_state(run_dir: Path, state: dict) -> None:
    (run_dir / "state.json").write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )


def _failing_gates() -> list[dict]:
    """Six gate results, all failing — drives the cap branch immediately when
    the in-stage counter is already at cap."""
    return [
        {
            "name": name,
            "status": "failed",
            "passed": False,
            "exit_code": 1,
            "_path": f"phase-1/gates/{name}.json",
        }
        for name in orchestrate.LINT_TEST_GATES
    ]


def _force_self_correct_at_cap(state: dict) -> None:
    """Pre-set the in-stage counter to cap so the first gates-fail immediately
    enters the cap branch without invoking the LLM round path."""
    cap = 5  # config.yaml: lint_test_self_correct
    state["counters"]["lint_test_self_correct__phase_1"] = cap


@pytest.fixture
def patched_loop(monkeypatch, tmp_path):
    """Run-lint-test-gates → always failing; emit_result silenced."""
    monkeypatch.setattr(orchestrate, "run_lint_test_gates",
                        lambda *a, **kw: _failing_gates())
    # emit_result writes JSONL to stdout; harmless but noisy in pytest.
    monkeypatch.setattr(orchestrate, "emit_result", lambda *a, **kw: None)
    rd = _make_run_dir(tmp_path)
    state = _initial_state()
    orchestrate.ensure_phase_counters(state, 1)
    _save_state(rd, state)
    eff = {"caps": {
        "lint_test_self_correct": 5,
        "lint_test_design_loop": 2,
        "total_stages": 200,
    }}
    return rd, state, eff


# ---------------------------------------------------------------------------
# 1. Counter is initialized
# ---------------------------------------------------------------------------

def test_ensure_phase_counters_initializes_lint_test_design():
    state = _initial_state()
    orchestrate.ensure_phase_counters(state, 1)
    assert "lint_test_design__phase_1" in state["counters"]
    assert state["counters"]["lint_test_design__phase_1"] == 0


# ---------------------------------------------------------------------------
# 2. lint_test_design must NOT be reset by _backtrack_to (cumulative)
# ---------------------------------------------------------------------------

def test_lint_test_design_not_in_in_stage_retry_counters():
    """If this counter were reset on backtrack, the cap would be unreachable —
    the same backtrack that just incremented it would clear it."""
    assert "lint_test_design" not in set(
        orchestrate.IN_STAGE_RETRY_COUNTERS.values()
    ), (
        "lint_test_design counter must be cumulative (NOT reset on backtrack); "
        "backtrack-driven re-entry would otherwise wipe the cap counter."
    )


def test_backtrack_preserves_lint_test_design_counter(tmp_path, monkeypatch):
    """Direct verification: _backtrack_to must not zero out this key."""
    monkeypatch.setattr(orchestrate, "emit_result", lambda *a, **kw: None)
    rd = _make_run_dir(tmp_path)
    state = _initial_state()
    orchestrate.ensure_phase_counters(state, 1)
    state["counters"]["lint_test_design__phase_1"] = 1
    state["counters"]["lint_test_self_correct__phase_1"] = 5
    _save_state(rd, state)

    orchestrate._backtrack_to(rd, state, "design", 1, source="lint-test-cap",
                              body="test")

    assert state["counters"]["lint_test_design__phase_1"] == 1, (
        "lint_test_design counter must be cumulative across backtracks"
    )
    assert state["counters"]["lint_test_self_correct__phase_1"] == 0, (
        "lint_test_self_correct should be reset (in IN_STAGE_RETRY_COUNTERS)"
    )


# ---------------------------------------------------------------------------
# 3. Cap behavior: 2 backtracks allowed, 3rd attempt escalates
# ---------------------------------------------------------------------------

def test_first_cap_hit_backtracks_to_design(patched_loop):
    rd, state, eff = patched_loop
    _force_self_correct_at_cap(state)

    rc = orchestrate.run_lint_test_loop(rd, 1, state, eff)

    assert rc == 0, "first cap hit should backtrack (return 0), not escalate"
    assert state["counters"]["lint_test_design__phase_1"] == 1
    assert state["status"] != "escalated"
    assert state["escalation_triggers"] == []


def test_second_cap_hit_still_backtracks(patched_loop):
    rd, state, eff = patched_loop
    state["counters"]["lint_test_design__phase_1"] = 1
    _force_self_correct_at_cap(state)

    rc = orchestrate.run_lint_test_loop(rd, 1, state, eff)

    assert rc == 0, "second cap hit (counter→2, equals cap) should still backtrack"
    assert state["counters"]["lint_test_design__phase_1"] == 2
    assert state["status"] != "escalated"


def test_third_cap_hit_escalates_with_distinct_trigger(patched_loop):
    rd, state, eff = patched_loop
    state["counters"]["lint_test_design__phase_1"] = 2
    _force_self_correct_at_cap(state)

    rc = orchestrate.run_lint_test_loop(rd, 1, state, eff)

    assert rc == 3, "third cap hit (counter→3 > cap=2) must escalate (rc=3)"
    assert state["status"] == "escalated"
    assert "lint_test_design_cap" in state["escalation_triggers"], (
        "escalation must use a distinct trigger so cross-run analytics can "
        "tell this apart from a generic lint_test_cap loopback"
    )
    assert state["counters"]["lint_test_design__phase_1"] == 3


# ---------------------------------------------------------------------------
# 4. Cap is configurable via overrides
# ---------------------------------------------------------------------------

def test_cap_respects_per_run_override(patched_loop):
    """Per-run override of `lint_test_design_loop` in spec.md.caps must take
    effect — the orchestrator reads `eff.caps.lint_test_design_loop`."""
    rd, state, eff = patched_loop
    eff["caps"]["lint_test_design_loop"] = 0  # zero tolerance
    _force_self_correct_at_cap(state)

    rc = orchestrate.run_lint_test_loop(rd, 1, state, eff)

    assert rc == 3
    assert state["status"] == "escalated"
    assert "lint_test_design_cap" in state["escalation_triggers"]
