"""run_gate.py status-enum tests.

The earlier behavior collapsed two distinct outcomes into `passed=True`:
  (a) gate ran successfully (intended pass)
  (b) gate had no command to run (silent skip)

Outcome (b) was acceptable for tools that may legitimately not be configured
(lint/format/types when the project uses a different toolchain), but it was
broken for `tests`/`coverage`: a project with no `tests/` directory would
silent-pass the lint-test gate set even though it had no unit tests.

Fix: a single `status` field with four values:
  passed | failed | skipped_ok | skipped_fail

Mapping rules:
  * lint/format/types with unrecognized tool        → skipped_ok
  * tests/coverage with no test target              → skipped_fail (was: passed)
  * sanity with no tests/sanity                     → skipped_fail (unchanged)
  * install with unrecognized packaging             → skipped_fail (was: passed)
  * timed out                                       → failed
  * non-zero exit code                              → failed
  * coverage exit 0 but pct missing or below thresh → failed
"""

from __future__ import annotations

from pathlib import Path

import run_gate


def _make_workspace(tmp_path: Path, *, with_tests: bool = False, with_sanity: bool = False) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    if with_tests:
        (ws / "tests" / "unit").mkdir(parents=True)
        (ws / "tests" / "unit" / "test_x.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    if with_sanity:
        (ws / "tests" / "sanity").mkdir(parents=True)
    return ws


# ---------------------------------------------------------------------------
# make_command: what gates produce a None command
# ---------------------------------------------------------------------------

def test_make_command_tests_with_no_tests_dir_returns_none(tmp_path):
    ws = _make_workspace(tmp_path)
    assert run_gate.make_command("tests", {"test": "pytest"}, ws) is None


def test_make_command_coverage_with_no_tests_dir_returns_none(tmp_path):
    ws = _make_workspace(tmp_path)
    assert run_gate.make_command("coverage", {"test": "pytest"}, ws) is None


def test_make_command_sanity_with_no_sanity_dir_returns_none(tmp_path):
    ws = _make_workspace(tmp_path, with_tests=True)
    assert run_gate.make_command("sanity", {"test": "pytest"}, ws) is None


def test_make_command_lint_unknown_linter_returns_none(tmp_path):
    ws = _make_workspace(tmp_path)
    assert run_gate.make_command("lint", {"linter": "blorgon-lint"}, ws) is None


# ---------------------------------------------------------------------------
# run_gate: status enum on skip
# ---------------------------------------------------------------------------

def test_tests_gate_with_no_tests_is_skipped_fail(tmp_path):
    ws = _make_workspace(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    result = run_gate.run_gate("tests", run_dir, 1, ws,
                               toolchain={"test": "pytest"},
                               threshold=None, options={})
    assert result["status"] == "skipped_fail"
    assert result["passed"] is False
    assert result["skipped"] is True
    assert "skip_reason" in result


def test_coverage_gate_with_no_tests_is_skipped_fail(tmp_path):
    ws = _make_workspace(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    result = run_gate.run_gate("coverage", run_dir, 1, ws,
                               toolchain={"test": "pytest"},
                               threshold=70.0, options={})
    assert result["status"] == "skipped_fail"
    assert result["passed"] is False


def test_sanity_gate_with_no_sanity_dir_is_skipped_fail(tmp_path):
    ws = _make_workspace(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    result = run_gate.run_gate("sanity", run_dir, 1, ws,
                               toolchain={"test": "pytest"},
                               threshold=None, options={})
    assert result["status"] == "skipped_fail"
    assert result["passed"] is False


def test_install_gate_with_unknown_packaging_is_skipped_fail(tmp_path):
    """If we don't know how to install deps, downstream tooling will fail
    obscurely. Better to fail loud at install. Was: skipped silently with
    passed=True."""
    ws = _make_workspace(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    result = run_gate.run_gate("install", run_dir, 1, ws,
                               toolchain={"packaging": "blorgon"},
                               threshold=None, options={})
    assert result["status"] == "skipped_fail"
    assert result["passed"] is False


def test_lint_gate_with_unknown_linter_is_skipped_ok(tmp_path):
    """Optional gates: an unrecognized lint tool means the project doesn't
    use one we recognize. That's not a hard fail (the LLM can still review),
    but it must be observably distinct from `passed`."""
    ws = _make_workspace(tmp_path, with_tests=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    result = run_gate.run_gate("lint", run_dir, 1, ws,
                               toolchain={"linter": "blorgon-lint"},
                               threshold=None, options={})
    assert result["status"] == "skipped_ok"
    assert result["skipped"] is True


def test_status_enum_values_are_valid(tmp_path):
    """All gate JSONs must carry one of the four enum values."""
    valid = {"passed", "failed", "skipped_ok", "skipped_fail"}
    ws = _make_workspace(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    for gate in ("install", "tests", "coverage", "sanity", "lint"):
        result = run_gate.run_gate(gate, run_dir, 1, ws,
                                   toolchain={"test": "pytest", "linter": "blorgon"},
                                   threshold=70.0 if gate == "coverage" else None,
                                   options={})
        assert result["status"] in valid, f"{gate}: status={result['status']!r}"
