"""Routing-related pure-function tests.

Pins the contract that SKILL.md describes for the `next` field of
`ORCHESTRATE_RESULT`:

  * `null` (None) at phase-end means "advance to next phase or delivery".
  * The orchestrator must NOT return a sentinel string like
    "next-phase-or-delivery" — that's not a known stage to the main session
    and silently breaks the documented `next` contract (SKILL.md §1).
  * pr_mode == "manual" skips pr-publish, and the next field becomes None
    (not a sentinel).
"""

from __future__ import annotations

import orchestrate


def _state_with_pr_mode(pr_mode: str) -> dict:
    return {"interview_spec": {"pr_mode": pr_mode}}


def test_default_next_run_level_chain():
    assert orchestrate._default_next_stage("planning", None, None) == "requirements"
    assert orchestrate._default_next_stage("requirements", None, None) == "phase-split"
    assert orchestrate._default_next_stage("phase-split", None, None) == "design"


def test_default_next_phase_chain():
    assert orchestrate._default_next_stage("design", 1, None) == "branch-create"
    assert orchestrate._default_next_stage("branch-create", 1, None) == "implement"
    assert orchestrate._default_next_stage("implement", 1, None) == "lint-test"
    assert orchestrate._default_next_stage("lint-test", 1, None) == "code-review"
    assert orchestrate._default_next_stage("code-review", 1, None) == "sanity-test"
    assert orchestrate._default_next_stage("sanity-test", 1, None) == "document"
    assert orchestrate._default_next_stage("document", 1, None) == "pr-create"


def test_pr_create_auto_routes_to_pr_publish():
    assert orchestrate._default_next_stage("pr-create", 1, _state_with_pr_mode("auto")) == "pr-publish"


def test_pr_create_manual_skips_pr_publish_returning_none():
    """In manual mode the next stage at phase-end is undetermined (advance to
    next phase or delivery — main session decides). Must be None, not a
    sentinel string. SKILL.md §1: '없으면 다음 phase 또는 delivery'."""
    assert orchestrate._default_next_stage("pr-create", 1, _state_with_pr_mode("manual")) is None


def test_pr_publish_returns_none_at_phase_end():
    """End of phase chain in auto mode."""
    assert orchestrate._default_next_stage("pr-publish", 1, _state_with_pr_mode("auto")) is None


def test_delivery_returns_none():
    assert orchestrate._default_next_stage("delivery", None, None) is None


def test_default_next_never_returns_sentinel_string():
    """Negative test: scan all stage names and confirm no result is the legacy
    'next-phase-or-delivery' sentinel."""
    cases = [
        ("planning", None, None),
        ("requirements", None, None),
        ("phase-split", None, None),
        ("design", 1, None),
        ("branch-create", 1, None),
        ("implement", 1, None),
        ("lint-test", 1, None),
        ("code-review", 1, None),
        ("sanity-test", 1, None),
        ("document", 1, None),
        ("pr-create", 1, _state_with_pr_mode("auto")),
        ("pr-create", 1, _state_with_pr_mode("manual")),
        ("pr-publish", 1, _state_with_pr_mode("auto")),
        ("delivery", None, None),
    ]
    for stage, phase, state in cases:
        nxt = orchestrate._default_next_stage(stage, phase, state)
        assert nxt != "next-phase-or-delivery", f"{stage} returned legacy sentinel"
