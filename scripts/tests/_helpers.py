"""Shared fixtures for harness self-tests.

Tests need to construct a minimal valid state.json and run-dir layout. They
must NOT mutate the harness's own outputs/ tree — every test gets its own
tmp_path-rooted run_dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import init_run  # noqa: E402  (made importable by conftest.py)


def make_run_dir(tmp_path: Path, run_id: str = "test-run", phase: int | None = None) -> Path:
    run_dir = tmp_path / run_id
    (run_dir / "interview").mkdir(parents=True, exist_ok=True)
    state = init_run.initial_state(run_id)
    if phase is not None:
        state["current_phase"] = phase
    (run_dir / "state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return run_dir


def write_state(run_dir: Path, **patch) -> dict:
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state.update(patch)
    (run_dir / "state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return state


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def parse_emit(captured_out: str) -> dict | None:
    """Pull the last `ORCHESTRATE_RESULT: {...}` line and parse its JSON."""
    for line in reversed(captured_out.splitlines()):
        if line.startswith("ORCHESTRATE_RESULT: "):
            return json.loads(line[len("ORCHESTRATE_RESULT: "):])
    return None
