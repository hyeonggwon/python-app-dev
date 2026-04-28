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


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_toolchain(path: Path | None) -> dict:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def make_command(gate: str, toolchain: dict, workspace: Path, options: dict | None = None) -> list[str] | None:
    """Return the shell command (as argv) for a gate, or None to skip.

    Honors detected toolchain when available; falls back to harness standard.
    `options` carries per-run toggles materialized from effective_thresholds
    (e.g. ``mypy_strict``, ``pytest_parallel``). Unknown keys are ignored.
    """
    options = options or {}
    mypy_strict = bool(options.get("mypy_strict", False))
    pytest_parallel = bool(options.get("pytest_parallel", False))

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
        # sanity gate must have something to run — a missing tests/sanity/ is a
        # fail, not a silent pass. design loopback will surface the gap.
        is_passed = gate != "sanity"
        result = {
            "name": gate,
            "command": None,
            "exit_code": None,
            "passed": is_passed,
            "skipped": True,
            "skip_reason": (
                f"sanity gate has nothing to run — workspace lacks tests/sanity/ "
                f"or toolchain test={toolchain.get('test')!r} not supported"
                if gate == "sanity"
                else f"no command for gate '{gate}' with toolchain "
                     f"{toolchain.get('test')}/{toolchain.get('linter')}"
            ),
            "started_at": started,
            "finished_at": now_iso(),
            "summary": {},
        }
        return result

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
    if gate == "coverage" and threshold is not None and "total" in summary:
        passed = passed and (summary["total"] >= threshold)
        summary["threshold"] = threshold

    return {
        "name": gate,
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
