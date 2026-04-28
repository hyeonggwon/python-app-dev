"""State-machine tests for orchestrate._handle_resume.

These pin the contract between SKILL.md (`continue` / `--resume`) and the
orchestrator. Earlier breakage that motivated this file:

  * Empty user_input + state.status == "escalated" silently emitted `pass` and
    advanced to the next stage (escalation `continue` did NOT re-run the
    failed stage). Documented behavior: re-run with cap counters intact.
  * Empty user_input + state.status == "awaiting_user" was also silent-pass —
    a main-session bug that forgets to merge user_input would silently bypass
    the intervention gate. Must fail-closed.
  * `decision: revise` on a stage with an in-stage retry counter
    (design.design_arch_self) did not reset the counter, so a phase that had
    already self-looped close to the cap would escalate immediately on the
    next architect-reviewer pass after a user revise.
"""

from __future__ import annotations

import json

import orchestrate
from scripts.tests._helpers import (  # type: ignore[import-not-found]
    make_run_dir, parse_emit, read_state, write_state,
)


def _caps_default() -> dict:
    return {
        "design_arch_self": 2,
        "design_revise": 2,
        "pr_create_revise": 2,
        "planning_revise": 2,
        "requirements_revise": 2,
        "phase_split_revise": 2,
    }


# ---------------------------------------------------------------------------
# Approve path
# ---------------------------------------------------------------------------

def test_approve_emits_pass_and_routes_to_next(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path, phase=1)
    (run_dir / "phase-1").mkdir(parents=True, exist_ok=True)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|revise"},
                user_input={"decision": "approve"})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("design", run_dir, 1, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc == 0
    out = parse_emit(capsys.readouterr().out)
    assert out is not None
    assert out["decision"] == "pass"
    assert out["next"] == "branch-create"


def test_approve_with_revise_only_keys_escalates_ambiguous(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|revise"},
                user_input={"decision": "approve",
                            "add_requirements": ["US-99"]})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("requirements", run_dir, None, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc == 3
    out = parse_emit(capsys.readouterr().out)
    assert out["decision"] == "escalated"
    assert out["trigger"] == "ambiguous_user_input"


# ---------------------------------------------------------------------------
# Revise path: fall-through + counter increments + IN_STAGE_RETRY reset
# ---------------------------------------------------------------------------

def test_revise_returns_none_and_increments_revise_counter(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path, phase=1)
    orchestrate.ensure_phase_counters(json.loads((run_dir / "state.json").read_text()), 1)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|revise"},
                user_input={"decision": "revise", "feedback": "rework module split"},
                counters={"design_revise__phase_1": 0,
                          "design_arch_self__phase_1": 0,
                          "total_stages": 0})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("design", run_dir, 1, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc is None  # fall through to re-run
    final = read_state(run_dir)
    assert final["counters"]["design_revise__phase_1"] == 1
    assert final["status"] == "running"
    assert final["user_input"] == {}
    # feedback prepended
    fb = (run_dir / "phase-1" / "feedback.md").read_text(encoding="utf-8")
    assert "rework module split" in fb


def test_revise_resets_in_stage_retry_counter_for_design(tmp_path):
    """A user revise on design must zero design_arch_self__phase_N — otherwise
    a phase with prior architect-reviewer self-loops would pre-burn the cap."""
    run_dir = make_run_dir(tmp_path, phase=1)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|revise"},
                user_input={"decision": "revise", "feedback": "split module X"},
                counters={"design_revise__phase_1": 0,
                          "design_arch_self__phase_1": 2,  # near cap
                          "total_stages": 0})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("design", run_dir, 1, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc is None
    final = read_state(run_dir)
    assert final["counters"]["design_arch_self__phase_1"] == 0


def test_revise_exceeds_revise_cap_escalates(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path, phase=1)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|revise"},
                user_input={"decision": "revise", "feedback": "yet another rework"},
                counters={"design_revise__phase_1": 2,  # at cap
                          "design_arch_self__phase_1": 0,
                          "total_stages": 0})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("design", run_dir, 1, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc == 3
    out = parse_emit(capsys.readouterr().out)
    assert out["trigger"] == "design_revise_cap"


# ---------------------------------------------------------------------------
# Reject path
# ---------------------------------------------------------------------------

def test_reject_escalates_user_rejected(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|reject|revise"},
                user_input={"decision": "reject", "feedback": "wrong direction"})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("planning", run_dir, None, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc == 3
    out = parse_emit(capsys.readouterr().out)
    assert out["trigger"] == "user_rejected"


# ---------------------------------------------------------------------------
# Empty / unknown decision: must fail-closed
# ---------------------------------------------------------------------------

def test_awaiting_user_with_empty_decision_escalates_missing_input(tmp_path, capsys):
    """Defending against a main-session bug that forgets to merge user_input.
    An empty decision must NOT silently approve."""
    run_dir = make_run_dir(tmp_path)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|reject|revise"},
                user_input={})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("planning", run_dir, None, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc == 3
    out = parse_emit(capsys.readouterr().out)
    assert out["trigger"] == "missing_user_decision"


def test_unknown_decision_escalates(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path)
    write_state(run_dir, status="awaiting_user",
                awaiting_input_schema={"decision": "approve|reject|revise"},
                user_input={"decision": "yolo"})
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("planning", run_dir, None, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    assert rc == 3
    out = parse_emit(capsys.readouterr().out)
    assert out["trigger"] == "unknown_user_decision"


# ---------------------------------------------------------------------------
# Escalation `continue` flow — empty user_input + state.status == "escalated"
# means the user wrote escalation.decision.md with `decision: continue`.
# Main session calls `--resume` without populating state.user_input. The
# orchestrator must re-run the stage, NOT silently emit pass.
# ---------------------------------------------------------------------------

def test_resume_after_escalation_with_empty_input_falls_through(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path, phase=1)
    write_state(run_dir, status="escalated",
                awaiting_input_schema=None,
                user_input={},
                escalation_triggers=["lint_test_self_correct_cap"])
    state = read_state(run_dir)

    rc = orchestrate._handle_resume("lint-test", run_dir, 1, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    # rc is None means the caller must fall through and re-run the stage —
    # this is what "continue" is supposed to do.
    assert rc is None
    out = parse_emit(capsys.readouterr().out)
    # Crucially, no `pass` was emitted from _handle_resume.
    assert out is None or out["decision"] != "pass"
    final = read_state(run_dir)
    assert final["status"] == "running"


def test_resume_after_escalation_does_not_reset_caps(tmp_path):
    """`continue` re-runs the stage but cap counters stay tripped — if the
    cap re-trips, the same trigger escalates again. SKILL.md §3."""
    run_dir = make_run_dir(tmp_path, phase=1)
    write_state(run_dir, status="escalated",
                awaiting_input_schema=None,
                user_input={},
                counters={"lint_test_self_correct__phase_1": 5,
                          "design_arch_self__phase_1": 0,
                          "total_stages": 0},
                escalation_triggers=["lint_test_self_correct_cap"])
    state = read_state(run_dir)

    orchestrate._handle_resume("lint-test", run_dir, 1, state, _caps_default())
    orchestrate.save_state(run_dir, state)

    final = read_state(run_dir)
    assert final["counters"]["lint_test_self_correct__phase_1"] == 5
