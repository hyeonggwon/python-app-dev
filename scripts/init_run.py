#!/usr/bin/env python3
"""Initialize a new run directory for the python-app-dev harness.

Creates outputs/<run-id>/ with the initial state.json schema specific to this
harness. Called by the orchestrating skill before deep-interview.

Usage:
  python3 scripts/init_run.py [--run-id <id>]

Prints the absolute path of the created run directory to stdout (last line).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path


def harness_root() -> Path:
    return Path(__file__).resolve().parent.parent


def make_run_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def initial_state(run_id: str) -> dict:
    """Initial state.json schema for this harness.

    Follows the formal Mode-A/B schema (status / current_stage / awaiting_input_schema /
    user_input / stage_outputs / counters / overrides) extended with harness-specific
    counter keys and run metadata.
    """
    return {
        "schema_version": 1,
        "run_id": run_id,
        "harness": "python-app-dev",
        "status": "initialized",  # initialized | interviewing | running | awaiting_user | done | escalated
        "current_stage": None,
        "current_phase": None,  # 1..N when in phase-level stages, else None
        "awaiting_input_schema": None,
        "user_input": {},
        "stage_outputs": {},  # {"planning": "outputs/<run>/planning.md", ...}
        "counters": {
            # Run-level revise loops (incremented by orchestrator's resume path
            # when the user picks `decision: revise` on a planning/requirements/
            # phase-split intervention).
            "planning_revise": 0,
            "requirements_revise": 0,
            "phase_split_revise": 0,
            # Global runaway cap
            "total_stages": 0,
            # Per-phase counters added lazily by ensure_phase_counters when a
            # phase first enters a phase-level stage. Keys:
            #   "lint_test_self_correct__phase_N": gate self-correction rounds
            #   "code_review_minor__phase_N":      cumulative minor verdicts
            #   "code_review_major__phase_N":      cumulative major verdicts
            #   "sanity__phase_N":                 sanity-test re-runs
            #   "design_self__phase_N":            architect-reviewer + user revise
            #   "pr_create_revise__phase_N":       pr-create user revise
        },
        "overrides": {
            # Per-run cap overrides loaded from interview/spec.md `caps:` go here.
            # Empty by default; orchestrator merges defaults <- overrides.
        },
        "verdict_history": [],  # appended by review/lint-test stages; cleared on backtrack
        "escalation_triggers": [],  # list, run-end appended to .index.jsonl as one line
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Initialize a run directory.")
    parser.add_argument("--run-id", default=None, help="Override run id (default: timestamp)")
    args = parser.parse_args(argv)

    root = harness_root()
    run_id = args.run_id or make_run_id()
    run_dir = root / "outputs" / run_id

    if run_dir.exists():
        print(f"ERROR: run directory already exists: {run_dir}", file=sys.stderr)
        return 2

    # idempotent mkdir of parents and core subdirs
    (run_dir / "interview").mkdir(parents=True, exist_ok=True)
    (run_dir / ".prompts").mkdir(parents=True, exist_ok=True)

    state = initial_state(run_id)
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # touch a placeholder so the orchestrator can detect a fresh run
    (run_dir / "interview" / ".pending").write_text("", encoding="utf-8")

    print(f"INIT_RUN_DONE: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
