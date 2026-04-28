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
import shlex
import subprocess
import sys
from pathlib import Path

GATES = {"install", "lint", "format", "types", "tests", "coverage", "sanity"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_toolchain(path: Path | None) -> dict:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def make_command(gate: str, toolchain: dict, workspace: Path) -> list[str] | None:
    """Return the shell command (as argv) for a gate, or None to skip.

    Honors detected toolchain when available; falls back to harness standard.
    """
    packaging = toolchain.get("packaging", "uv")
    linter = toolchain.get("linter", "ruff") or "ruff"
    formatter = toolchain.get("formatter", "ruff-format") or "ruff-format"
    typechecker = toolchain.get("typechecker", "mypy") or "mypy"
    test = toolchain.get("test", "pytest") or "pytest"

    use_uv_run = packaging == "uv"
    prefix = ["uv", "run"] if use_uv_run else []

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
            return [*prefix, "mypy", target]
        if typechecker == "pyright":
            return [*prefix, "pyright"]
        return None

    if gate == "tests":
        if test == "pytest":
            target = "tests/unit" if (workspace / "tests" / "unit").exists() else "tests"
            if not (workspace / target).exists():
                return None
            return [*prefix, "pytest", target, "-v", "-m", "not sanity"]
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
            ]
        return None

    if gate == "sanity":
        if test == "pytest":
            target = "tests/sanity"
            if not (workspace / target).exists():
                return None
            return [*prefix, "pytest", target, "-v", "-m", "sanity"]
        return None

    return None


def parse_summary(gate: str, stdout: str, workspace: Path) -> dict:
    """Best-effort summary extraction. Never raises — always returns a dict."""
    summary: dict = {}
    if gate == "lint" or gate == "format":
        # Count lines that look like "file:line:col: code message"
        errs = sum(1 for line in stdout.splitlines() if ": " in line and any(
            c.isdigit() for c in line.split(":", 3)[1] if line.split(":", 3)[1:2]
        ))
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


def run_gate(gate: str, run_dir: Path, phase: int, workspace: Path, toolchain: dict, threshold: float | None) -> dict:
    cmd = make_command(gate, toolchain, workspace)
    started = now_iso()

    if cmd is None:
        result = {
            "name": gate,
            "command": None,
            "exit_code": None,
            "passed": True,  # skipped == passed (no signal)
            "skipped": True,
            "skip_reason": f"no command for gate '{gate}' with toolchain {toolchain.get('test')}/{toolchain.get('linter')}",
            "started_at": started,
            "finished_at": now_iso(),
            "summary": {},
        }
        return result

    proc = subprocess.run(
        cmd,
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )

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
    result = run_gate(args.gate, run_dir, args.phase, workspace, toolchain, args.threshold)

    stdout_path.write_text(result.pop("_stdout", "") or "", encoding="utf-8")
    stderr_path.write_text(result.pop("_stderr", "") or "", encoding="utf-8")
    result["stdout_path"] = str(stdout_path.relative_to(run_dir))
    result["stderr_path"] = str(stderr_path.relative_to(run_dir))
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"GATE_DONE: {out_path} passed={result['passed']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
