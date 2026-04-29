"""pytest config for harness self-tests.

These tests import scripts/orchestrate.py and scripts/run_gate.py as modules
to exercise pure functions (state machine transitions, gate enum mapping,
front-matter parsing, routing decisions) without spawning `claude` or shelling
out to real toolchains.

The harness has no `pyproject.toml` of its own — it's a meta-tool, not a
package — so we rely on conftest.py to make the scripts/ dir importable.

Session-scoped fixtures here parse orchestrate.py once for the cross-file
invariant tests in test_invariants.py.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS.parent
ORCHESTRATOR = SCRIPTS / "orchestrate.py"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _parse_orchestrator_tables() -> dict:
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    wanted = {
        "STAGE_DIRS", "STAGE_TOOLS", "STAGE_REQUIRED_AUX_OUTPUTS",
        "STAGE_OWNED_PATTERNS",
        "STAGE_PRIMARY_OUTPUT", "STAGE_MARKER",
        "RUN_LEVEL_STAGES", "PHASE_LEVEL_STAGES",
        "VALID_VERDICT_LABELS", "VALID_LOOP_TARGETS", "VERDICT_TO_LOOP",
        "VALID_DESIGN_VERDICT_LABELS",
        "GATE_PASSING_STATUSES", "GATE_FAILING_STATUSES",
    }
    out: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id in wanted:
                try:
                    out[tgt.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    if "STAGE_MARKER" not in out and "STAGE_DIRS" in out:
        out["STAGE_MARKER"] = {
            k: f"{k.replace('-', '_').upper()}_DONE" for k in out["STAGE_DIRS"]
        }
    if "PHASE_LEVEL_STAGES" not in out and {"STAGE_DIRS", "RUN_LEVEL_STAGES"} <= set(out):
        out["PHASE_LEVEL_STAGES"] = (
            set(out["STAGE_DIRS"]) - set(out["RUN_LEVEL_STAGES"])
        )
    return out


def _parse_replacements_keys() -> set[str]:
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "materialize_prompt":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    for tgt in sub.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == "replacements":
                            if isinstance(sub.value, ast.Dict):
                                for k in sub.value.keys:
                                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                        s = k.value
                                        if s.startswith("{") and s.endswith("}"):
                                            keys.add(s[1:-1])
    return keys


@pytest.fixture(scope="session")
def orch() -> dict:
    return _parse_orchestrator_tables()


@pytest.fixture(scope="session")
def replacements() -> set[str]:
    return _parse_replacements_keys()
