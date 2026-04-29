#!/usr/bin/env python3
"""Run a deterministic verification gate and write a JSON result.

orchestrate.py invokes this between LLM stages — never the LLM itself. The LLM
review stages read the produced JSON file as input.

Usage:
  python3 scripts/run_gate.py <gate> --run-dir <path> --phase <N> [--workspace <path>]
                              [--toolchain <toolchain.json>]

Gates:
  install | lint | format | types | tests | coverage | sanity

Output:
  Writes {run_dir}/phase-{N}/gates/{gate}.json
  Writes stdout/stderr to .stdout.txt / .stderr.txt next to it.
  Exits 0 if the gate passed, 1 if it failed (regardless, the JSON is written
  so the LLM can read it).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

GATES = {"install", "lint", "format", "types", "tests", "coverage", "sanity"}

# Per-gate timeout in seconds. A hung tool (mypy/pytest looping on a bad fixture)
# would otherwise block the whole orchestrator. On expiry we record a fail JSON.
GATE_TIMEOUT_SECONDS = {
    "install":  600,
    "lint":     180,
    "format":   180,
    "types":    900,
    "tests":   1800,
    "coverage":1800,
    "sanity":   900,
}

# Status enum (orchestrator reads `status` field in each gate JSON).
#
#   passed       — command ran and exited 0 (and threshold met, where applicable)
#   failed       — command ran and exited non-zero, OR coverage below threshold,
#                  OR timed out
#   skipped_ok   — make_command returned None and that's expected/benign
#                  (e.g., the project's linter is something we don't recognize;
#                  the LLM code-review will still cover it)
#   skipped_fail — make_command returned None but the gate is essential and
#                  cannot be silently bypassed (no tests/ for tests/coverage,
#                  unknown packaging for install, no tests/sanity/ for sanity)
#
# Earlier revisions collapsed (skipped_ok, skipped_fail) into `passed=True,
# skipped=True`, which let workspaces with no unit tests pass through the
# lint-test gate set. The split closes that hole; orchestrator's
# gate_is_passing() now treats skipped_fail as failing.
GATE_STATUS_VALUES = {"passed", "failed", "skipped_ok", "skipped_fail"}

# Gates whose absence is not a fail. Anything not listed here, when its
# command is None, is `skipped_fail` (fail-closed). Sanity is always
# fail-closed so it isn't in the set; install is fail-closed because
# downstream gates depend on it.
SKIP_OK_WHEN_NO_COMMAND = {"lint", "format", "types"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_toolchain(path: Path | None) -> dict:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


_TRUTHY_STRINGS = {"true", "yes", "on", "1"}
_FALSY_STRINGS = {"false", "no", "off", "0", ""}


def _truthy(value: object, *, default: bool = False) -> bool:
    """Mirror of orchestrate._truthy. Kept local so run_gate stays
    importless of orchestrate (the two scripts are intentionally independent).

    `bool("false") == True` is the trap — effective_thresholds.json may carry
    a string for any boolean toggle if the merging step upstream wasn't strict
    about types. Coerce explicitly so an `off`/`false` string disables.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return default
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUTHY_STRINGS:
            return True
        if v in _FALSY_STRINGS:
            return False
        return default
    return default


def make_command(gate: str, toolchain: dict, workspace: Path, options: dict | None = None) -> list[str] | None:
    """Return the shell command (as argv) for a gate, or None to skip.

    Honors detected toolchain when available; falls back to harness standard.
    `options` carries per-run toggles materialized from effective_thresholds
    (e.g. ``mypy_strict``, ``pytest_parallel``). Unknown keys are ignored.
    """
    options = options or {}
    mypy_strict = _truthy(options.get("mypy_strict"), default=False)
    pytest_parallel = _truthy(options.get("pytest_parallel"), default=False)

    packaging = toolchain.get("packaging", "uv")
    linter = toolchain.get("linter", "ruff") or "ruff"
    formatter = toolchain.get("formatter", "ruff-format") or "ruff-format"
    typechecker = toolchain.get("typechecker", "mypy") or "mypy"
    test = toolchain.get("test", "pytest") or "pytest"

    use_uv_run = packaging == "uv"
    prefix = ["uv", "run"] if use_uv_run else []
    parallel_args = ["-n", "auto"] if pytest_parallel else []

    if gate == "install":
        if packaging == "uv":
            return ["uv", "sync"]
        if packaging == "poetry":
            return ["poetry", "install", "--no-interaction"]
        if packaging == "pipenv":
            return ["pipenv", "install"]
        if packaging in ("pip-requirements", "setuptools"):
            req = next(workspace.glob("requirements*.txt"), None)
            if req:
                return ["pip", "install", "-r", str(req)]
            return ["pip", "install", "-e", "."]
        return None  # unknown — skip silently

    if gate == "lint":
        if linter == "ruff":
            return [*prefix, "ruff", "check", "."]
        if linter == "flake8":
            return [*prefix, "flake8", "."]
        if linter == "pylint":
            return [*prefix, "pylint", "src/"]
        return None

    if gate == "format":
        if formatter in ("ruff-format", "ruff"):
            return [*prefix, "ruff", "format", "--check", "."]
        if formatter == "black":
            return [*prefix, "black", "--check", "."]
        return None

    if gate == "types":
        if typechecker == "mypy":
            target = "src/" if (workspace / "src").exists() else "."
            cmd = [*prefix, "mypy"]
            if mypy_strict:
                cmd.append("--strict")
            cmd.append(target)
            return cmd
        if typechecker == "pyright":
            cmd = [*prefix, "pyright"]
            if mypy_strict:
                cmd.append("--strict")
            return cmd
        return None

    if gate == "tests":
        if test == "pytest":
            target = "tests/unit" if (workspace / "tests" / "unit").exists() else "tests"
            if not (workspace / target).exists():
                return None
            return [*prefix, "pytest", target, "-v", "-m", "not sanity", *parallel_args]
        if test == "tox":
            return ["tox"]
        return None

    if gate == "coverage":
        if test == "pytest":
            target = "tests/unit" if (workspace / "tests" / "unit").exists() else "tests"
            if not (workspace / target).exists():
                return None
            src = "src" if (workspace / "src").exists() else "."
            return [
                *prefix,
                "pytest",
                target,
                "--cov=" + src,
                "--cov-report=json:.coverage.json",
                "-q",
                "-m",
                "not sanity",
                *parallel_args,
            ]
        return None

    if gate == "sanity":
        if test == "pytest":
            target = "tests/sanity"
            if not (workspace / target).exists():
                return None
            return [*prefix, "pytest", target, "-v", "-m", "sanity", *parallel_args]
        return None

    return None


_DIAG_LINE_RE = re.compile(r"^[^:]+:\d+:\d+:")


def parse_summary(gate: str, stdout: str, workspace: Path) -> dict:
    """Best-effort summary extraction. Never raises — always returns a dict."""
    summary: dict = {}
    if gate == "lint" or gate == "format":
        errs = sum(1 for line in stdout.splitlines() if _DIAG_LINE_RE.match(line))
        summary["errors"] = errs
    if gate == "tests":
        for line in reversed(stdout.splitlines()):
            if "passed" in line or "failed" in line:
                summary["last_line"] = line.strip()
                break
    if gate == "coverage":
        cov_file = workspace / ".coverage.json"
        if cov_file.exists():
            try:
                data = json.loads(cov_file.read_text(encoding="utf-8"))
                pct = data.get("totals", {}).get("percent_covered")
                if pct is not None:
                    summary["total"] = round(float(pct), 2)
            except Exception:
                pass
    return summary


def run_gate(gate: str, run_dir: Path, phase: int, workspace: Path, toolchain: dict, threshold: float | None, options: dict | None = None) -> dict:
    cmd = make_command(gate, toolchain, workspace, options)
    started = now_iso()

    # Coverage emits ``.coverage.json`` to workspace root. If a previous round
    # crashed before pytest could rewrite it, parse_summary would happily reuse
    # stale data and fake a pass. Always clear it first.
    if gate == "coverage":
        stale = workspace / ".coverage.json"
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass

    if cmd is None:
        # Decide skipped_ok vs skipped_fail:
        #   - lint/format/types → skipped_ok (project may legitimately not
        #     use the recognized tool; LLM code-review still covers quality)
        #   - everything else → skipped_fail (essential gate; missing means
        #     we cannot certify correctness — surface it to the orchestrator
        #     so design loopback can address the gap)
        if gate in SKIP_OK_WHEN_NO_COMMAND:
            status = "skipped_ok"
            is_passed = True
        else:
            status = "skipped_fail"
            is_passed = False

        if gate == "sanity":
            skip_reason = (
                f"sanity gate has nothing to run — workspace lacks tests/sanity/ "
                f"or toolchain test={toolchain.get('test')!r} not supported"
            )
        elif gate in ("tests", "coverage"):
            skip_reason = (
                f"{gate} gate has nothing to run — workspace lacks unit test target "
                f"(tests/unit/ or tests/) for toolchain test={toolchain.get('test')!r}"
            )
        elif gate == "install":
            skip_reason = (
                f"install gate cannot run — toolchain packaging "
                f"={toolchain.get('packaging')!r} is not supported"
            )
        else:
            skip_reason = (
                f"no command for gate '{gate}' with toolchain "
                f"{toolchain.get('test')}/{toolchain.get('linter')}"
            )

        return {
            "name": gate,
            "status": status,
            "command": None,
            "exit_code": None,
            "passed": is_passed,
            "skipped": True,
            "skip_reason": skip_reason,
            "started_at": started,
            "finished_at": now_iso(),
            "summary": {},
        }

    timeout = GATE_TIMEOUT_SECONDS.get(gate, 600)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "name": gate,
            "status": "failed",
            "command": " ".join(shlex.quote(c) for c in cmd),
            "exit_code": None,
            "passed": False,
            "skipped": False,
            "started_at": started,
            "finished_at": now_iso(),
            "summary": {"timeout_seconds": timeout},
            "timed_out": True,
            "_stdout": (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or ""),
            "_stderr": (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or ""),
        }

    finished = now_iso()
    passed = proc.returncode == 0

    # coverage threshold check
    summary = parse_summary(gate, proc.stdout, workspace)
    if gate == "coverage" and threshold is not None:
        summary["threshold"] = threshold
        if "total" in summary:
            passed = passed and (summary["total"] >= threshold)
        else:
            # Fail-closed: pytest --cov exited 0 but .coverage.json was not
            # produced or unparseable. Treating that as pass would silently
            # bypass the configured threshold (e.g., when --cov-report config
            # is overridden by a project conftest). Mark fail and surface the
            # missing total so design loopback can address it.
            passed = False
            summary["threshold_missing_total"] = True

    return {
        "name": gate,
        "status": "passed" if passed else "failed",
        "command": " ".join(shlex.quote(c) for c in cmd),
        "exit_code": proc.returncode,
        "passed": passed,
        "skipped": False,
        "started_at": started,
        "finished_at": finished,
        "summary": summary,
        "_stdout": proc.stdout,
        "_stderr": proc.stderr,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=sorted(GATES))
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", type=int, required=True)
    parser.add_argument("--workspace", required=True, help="Path to the project workspace")
    parser.add_argument("--toolchain", default=None, help="Path to detected toolchain JSON")
    parser.add_argument("--threshold", type=float, default=None, help="Numeric threshold for gates that need one (coverage)")
    parser.add_argument("--effective-thresholds", default=None, help="Path to effective_thresholds.json (per-run options)")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"ERROR: workspace is not a directory: {workspace}", file=sys.stderr)
        return 2

    gates_dir = run_dir / f"phase-{args.phase}" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)
    out_path = gates_dir / f"{args.gate}.json"
    stdout_path = gates_dir / f"{args.gate}.stdout.txt"
    stderr_path = gates_dir / f"{args.gate}.stderr.txt"

    toolchain = load_toolchain(Path(args.toolchain).resolve() if args.toolchain else None)

    # Default to {run_dir}/effective_thresholds.json when not given explicitly.
    eff_path = (
        Path(args.effective_thresholds).resolve()
        if args.effective_thresholds
        else run_dir / "effective_thresholds.json"
    )
    options: dict = {}
    if eff_path.exists():
        try:
            options = json.loads(eff_path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            options = {}

    result = run_gate(args.gate, run_dir, args.phase, workspace, toolchain, args.threshold, options)

    stdout_path.write_text(result.pop("_stdout", "") or "", encoding="utf-8")
    stderr_path.write_text(result.pop("_stderr", "") or "", encoding="utf-8")
    result["stdout_path"] = str(stdout_path.relative_to(run_dir))
    result["stderr_path"] = str(stderr_path.relative_to(run_dir))
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"GATE_DONE: {out_path} passed={result['passed']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
