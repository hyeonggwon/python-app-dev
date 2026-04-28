"""Tests for orchestrator gate-vs-verdict and lint-test cross-checks.

The bug pattern: the orchestrator's only fail signal from a gate JSON was
`gd.get("passed") is False`. After introducing `status: skipped_fail`, both
the lint-test loop's `all_passed` aggregation and the code-review cross-check
must treat skipped_fail as a non-pass.
"""

from __future__ import annotations

import json

import orchestrate
from scripts.tests._helpers import (  # type: ignore[import-not-found]
    make_run_dir, parse_emit, read_state, write_state,
)


def _write_gate_json(run_dir, phase: int, name: str, *, status: str, passed: bool):
    gates_dir = run_dir / f"phase-{phase}" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "status": status,
        "passed": passed,
        "skipped": status.startswith("skipped"),
        "exit_code": None if status.startswith("skipped") else (0 if passed else 1),
        "summary": {},
    }
    (gates_dir / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_verdict_json(run_dir, phase: int, *, label: str, loop_target: str):
    sd = run_dir / f"phase-{phase}"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "verdict.json").write_text(
        json.dumps({"verdict": label, "loop_target": loop_target,
                    "issues_minor": [], "issues_major": [], "summary": ""}) + "\n",
        encoding="utf-8",
    )
    (sd / "review.md").write_text("# review\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# code-review cross-check
# ---------------------------------------------------------------------------

def test_code_review_pass_with_skipped_fail_gate_escalates(tmp_path, capsys):
    """A gate marked `status: skipped_fail` is a hard fail signal. The
    cross-check at code-review must catch verdict=pass conflicting with
    that, even though `passed=False` is also set."""
    run_dir = make_run_dir(tmp_path, phase=1)
    state = write_state(run_dir, current_phase=1)
    _write_gate_json(run_dir, 1, "tests", status="skipped_fail", passed=False)
    _write_gate_json(run_dir, 1, "lint",  status="passed",       passed=True)
    _write_verdict_json(run_dir, 1, label="pass", loop_target="none")

    eff = {"caps": {}}
    rc = orchestrate.route("code-review", run_dir, 1, state, eff)

    assert rc == 3
    out = parse_emit(capsys.readouterr().out)
    assert out["decision"] == "escalated"
    assert out["trigger"] == "gate_vs_verdict"


def test_code_review_pass_with_all_passed_or_skipped_ok_proceeds(tmp_path, capsys):
    """Counter-test: passed and skipped_ok gates do NOT trigger the
    cross-check (an unconfigured optional tool is benign)."""
    run_dir = make_run_dir(tmp_path, phase=1)
    state = write_state(run_dir, current_phase=1,
                        counters={"code_review_minor__phase_1": 0,
                                  "code_review_major__phase_1": 0,
                                  "total_stages": 0})
    _write_gate_json(run_dir, 1, "tests", status="passed",     passed=True)
    _write_gate_json(run_dir, 1, "lint",  status="skipped_ok", passed=True)
    _write_verdict_json(run_dir, 1, label="pass", loop_target="none")

    eff = {"caps": {}}
    rc = orchestrate.route("code-review", run_dir, 1, state, eff)

    assert rc == 0
    out = parse_emit(capsys.readouterr().out)
    assert out["decision"] == "pass"
    assert out["next"] == "sanity-test"


# ---------------------------------------------------------------------------
# lint-test loop aggregation
# ---------------------------------------------------------------------------

def test_lint_test_aggregator_treats_skipped_fail_as_non_pass():
    """The pure aggregation predicate used by run_lint_test_loop must
    distinguish skipped_fail from passed. We expose this as a helper so the
    loop can call it without growing branches."""
    gates = [
        {"name": "install",  "status": "passed",       "passed": True},
        {"name": "lint",     "status": "passed",       "passed": True},
        {"name": "format",   "status": "passed",       "passed": True},
        {"name": "types",    "status": "passed",       "passed": True},
        {"name": "tests",    "status": "skipped_fail", "passed": False},
        {"name": "coverage", "status": "skipped_fail", "passed": False},
    ]
    assert orchestrate.gates_all_passing(gates) is False


def test_lint_test_aggregator_accepts_skipped_ok():
    gates = [
        {"name": "install", "status": "passed",     "passed": True},
        {"name": "lint",    "status": "skipped_ok", "passed": True},
        {"name": "format",  "status": "skipped_ok", "passed": True},
        {"name": "types",   "status": "skipped_ok", "passed": True},
        {"name": "tests",   "status": "passed",     "passed": True},
        {"name": "coverage","status": "passed",     "passed": True},
    ]
    assert orchestrate.gates_all_passing(gates) is True


def test_lint_test_aggregator_treats_failed_as_non_pass():
    gates = [
        {"name": "install", "status": "passed", "passed": True},
        {"name": "lint",    "status": "failed", "passed": False},
    ]
    assert orchestrate.gates_all_passing(gates) is False
