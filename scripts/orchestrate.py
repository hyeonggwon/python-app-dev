#!/usr/bin/env python3
"""Single-entrypoint orchestrator for the python-app-dev harness (Mode B).

Usage:
  scripts/orchestrate.py <stage> [--run-dir <path>] [--phase <N>] [--resume]

Run-level stages (no --phase):
  planning | requirements | phase-split | delivery

Phase-level stages (require --phase):
  design | branch-create | implement | lint-test | code-review | sanity-test
  | document | pr-create

Behavior:
  1. Load state.json, validate transition.
  2. Materialize stage prompt (token resolve) → {run_dir}/.prompts/<stage>[--p<N>].md.
  3. For lint-test: pre-run gates, embed JSON paths into prompt feedback.
  4. Invoke `claude -p` with stage-specific --allowed-tools and --permission-mode.
  5. Validate marker line + required aux outputs + verdict shape (if applicable).
  6. Compute routing decision (PASS / loopback / escalation / awaiting_user).
  7. Update state.json, write feedback.md if looping back, write escalation.md if escalating.
  8. Print result line for the main session.

The main session reads the printed line and decides what to do next.
This script does NOT loop on its own — it executes a single stage transition.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib  # noqa: F401  (kept for parity with detect_toolchain)


# ---------------------------------------------------------------------------
# Stage tables (single source of truth — validate_harness.py cross-checks these)
# ---------------------------------------------------------------------------

STAGE_DIRS = {
    "planning":      "",                # run_dir root
    "requirements":  "",
    "phase-split":   "",
    "design":        "phase-{N}",
    "branch-create": "phase-{N}",
    "implement":     "phase-{N}",
    "lint-test":     "phase-{N}",
    "code-review":   "phase-{N}",
    "sanity-test":   "phase-{N}",
    "document":      "phase-{N}",
    "pr-create":     "phase-{N}",
    "delivery":      "",
}

STAGE_TOOLS = {
    "planning":      ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "Write"],
    "requirements":  ["Read", "Glob", "Grep", "Write"],
    "phase-split":   ["Read", "Write"],
    "design": [
        "Read", "Glob", "Grep", "Write", "Task",
    ],
    "branch-create": [
        "Read", "Write",
        "Bash(git checkout:*)", "Bash(git switch:*)", "Bash(git branch:*)",
        "Bash(git ls-remote:*)", "Bash(git for-each-ref:*)", "Bash(git rev-parse:*)",
    ],
    "implement": [
        "Read", "Write", "Edit", "Glob", "Grep",
        "Bash(uv:*)", "Bash(ruff:*)", "Bash(mypy:*)", "Bash(pytest:*)",
        "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status)", "Bash(git status:*)",
        "Bash(git log:*)", "Bash(git diff:*)", "Bash(git rev-parse:*)",
    ],
    "lint-test": [
        "Read", "Edit", "Write",
        "Bash(uv:*)", "Bash(ruff:*)", "Bash(mypy:*)", "Bash(pytest:*)",
        "Bash(git add:*)", "Bash(git commit:*)",
    ],
    "code-review": [
        "Read", "Write", "Glob", "Grep", "Task",
        "Bash(git diff:*)", "Bash(git log:*)", "Bash(git rev-parse:*)",
    ],
    "sanity-test": [
        "Read", "Write", "Edit", "Task",
        "Bash(uv:*)", "Bash(pytest:*)",
        "Bash(git add:*)", "Bash(git commit:*)",
    ],
    "document": [
        "Read", "Write", "Edit", "Glob", "Grep",
        "Bash(git add:*)", "Bash(git commit:*)",
        "Bash(git status)", "Bash(git status:*)", "Bash(git diff:*)",
    ],
    "pr-create": [
        "Read", "Write",
        "Bash(git push:*)", "Bash(git rev-parse:*)",
        "Bash(gh pr:*)", "Bash(gh repo:*)",
    ],
    "delivery": [
        "Read", "Write", "Glob", "Grep",
        "Bash(git log:*)", "Bash(git rev-parse:*)",
    ],
}

# Stages that produce more than one required aux file beyond their primary output
STAGE_REQUIRED_AUX_OUTPUTS = {
    "code-review": ["review.md", "verdict.yaml"],
    # other stages have a single primary output: enforced by stage-specific checks
}

STAGE_PRIMARY_OUTPUT = {
    "planning":      "planning.md",
    "requirements":  "requirements.md",
    "phase-split":   "phases.md",
    "design":        "design.md",
    "branch-create": "branch.txt",
    "implement":     "implementation.md",
    "lint-test":     "lint-test.md",
    "code-review":   "review.md",
    "sanity-test":   "sanity.md",
    "document":      "docs-changes.md",
    "pr-create":     "pr.md",
    "delivery":      "delivery.md",
}

STAGE_MARKER = {k: f"{k.replace('-', '_').upper()}_DONE" for k in STAGE_DIRS}

RUN_LEVEL_STAGES = {"planning", "requirements", "phase-split", "delivery"}
PHASE_LEVEL_STAGES = set(STAGE_DIRS) - RUN_LEVEL_STAGES


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

VALID_VERDICT_LABELS = {"pass", "minor", "major", "critical"}
VALID_LOOP_TARGETS = {"none", "implement", "design", "escalation"}

VERDICT_TO_LOOP = {
    "pass":     {"none"},
    "minor":    {"implement"},
    "major":    {"design"},
    "critical": {"escalation"},
}


# ---------------------------------------------------------------------------
# Paths and config
# ---------------------------------------------------------------------------

def harness_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_config() -> dict:
    cfg_path = harness_root() / "scripts" / "config.yaml"
    if not cfg_path.exists():
        return {}
    text = cfg_path.read_text(encoding="utf-8")
    # Minimal YAML reader (stdlib-only, supports the simple structure used in config.yaml).
    # Accepts: key: value, nested sections via 2-space indent, comments with #.
    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, val = line.lstrip().partition(":")
        key = key.strip()
        val = val.strip()
        # pop stack to current indent
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if val == "":
            new: dict = {}
            parent[key] = new
            stack.append((indent, new))
        else:
            if val.lower() in ("true", "false"):
                parent[key] = val.lower() == "true"
            else:
                try:
                    if "." in val:
                        parent[key] = float(val)
                    else:
                        parent[key] = int(val)
                except ValueError:
                    parent[key] = val.strip("'").strip('"')
    return root


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def load_state(run_dir: Path) -> dict:
    return json.loads(state_path(run_dir).read_text(encoding="utf-8"))


def save_state(run_dir: Path, state: dict) -> None:
    state_path(run_dir).write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def ensure_phase_counters(state: dict, phase: int) -> None:
    keys = [
        "lint_test_self_correct", "code_review_minor", "code_review_major",
        "sanity", "install", "design_self", "branch_create",
        "document", "pr_create",
    ]
    for k in keys:
        ck = f"{k}__phase_{phase}"
        if ck not in state["counters"]:
            state["counters"][ck] = 0


# ---------------------------------------------------------------------------
# Stage path resolution
# ---------------------------------------------------------------------------

def stage_dir(run_dir: Path, stage: str, phase: int | None) -> Path:
    tmpl = STAGE_DIRS[stage]
    if not tmpl:
        return run_dir
    if "{N}" in tmpl:
        if phase is None:
            raise ValueError(f"stage '{stage}' requires --phase")
        return run_dir / tmpl.replace("{N}", str(phase))
    return run_dir / tmpl


def stage_primary_output(run_dir: Path, stage: str, phase: int | None) -> Path:
    return stage_dir(run_dir, stage, phase) / STAGE_PRIMARY_OUTPUT[stage]


# ---------------------------------------------------------------------------
# Token materialization (0-1)
# ---------------------------------------------------------------------------

def materialize_prompt(stage: str, run_dir: Path, phase: int | None, state: dict, effective_thresholds: dict) -> Path:
    """Resolve {tokens} in the canonical prompt and write a per-call copy.

    Per harness convention, str.replace (not str.format) is used so that example
    code's curly braces survive intact.
    """
    canonical = harness_root() / "scripts" / "prompts" / f"{stage}.md"
    if not canonical.exists():
        raise FileNotFoundError(f"missing canonical prompt: {canonical}")
    text = canonical.read_text(encoding="utf-8")

    s_dir = stage_dir(run_dir, stage, phase)
    s_dir.mkdir(parents=True, exist_ok=True)

    workspace = resolve_workspace(state)

    replacements = {
        "{HARNESS_ROOT}": str(harness_root()),
        "{run_dir}": str(run_dir),
        "{run_id}": state["run_id"],
        "{stage_dir}": str(s_dir),
        "{phase}": str(phase) if phase is not None else "",
        "{N}": str(phase) if phase is not None else "",
        "{prev}": str(phase - 1) if phase is not None and phase > 1 else "",
        "{workspace}": str(workspace) if workspace else "",
        "{thresholds_path}": str(run_dir / "effective_thresholds.json"),
        "{spec_path}": str(run_dir / "interview" / "spec.md"),
        "{planning_path}": str(run_dir / "planning.md"),
        "{requirements_path}": str(run_dir / "requirements.md"),
        "{phases_path}": str(run_dir / "phases.md"),
        "{tacit_knowledge_path}": str(harness_root() / "docs" / "tacit-knowledge.md"),
        "{task_spec_path}": str(harness_root() / "docs" / "task-spec.md"),
        "{coverage_threshold}": str(effective_thresholds.get("coverage_threshold", 70)),
        "{security_review}": str(effective_thresholds.get("security_review", False)).lower(),
        "{sanity_scenarios_per_phase}": str(effective_thresholds.get("sanity_scenarios_per_phase", 2)),
    }
    for token, value in replacements.items():
        text = text.replace(token, value)

    suffix = f"--p{phase}" if phase is not None else ""
    out = run_dir / ".prompts" / f"{stage}{suffix}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def resolve_workspace(state: dict) -> Path | None:
    spec = state.get("interview_spec", {})
    mode = spec.get("mode")
    if mode == "new":
        run_id = state["run_id"]
        return harness_root() / "outputs" / run_id / "workspace"
    if mode == "maintenance":
        path = spec.get("project_path")
        if path:
            return Path(path)
    return None


def ensure_workspace_repo(ws: Path, state: dict) -> None:
    """Idempotently make sure the workspace exists and is its own git repo.

    The harness's own .git is at `harness_root()`. The workspace must NOT
    walk up to that — every git command must terminate at the workspace's
    own .git. For mode=new we own the directory and run `git init`. For
    mode=maintenance the user's project is expected to already be a repo;
    we only validate that and refuse if it would inherit our .git.
    """
    spec = state.get("interview_spec", {})
    mode = spec.get("mode")
    ws.mkdir(parents=True, exist_ok=True)

    git_dir = ws / ".git"
    if git_dir.exists():
        # Already a repo — confirm it terminates here (not inherited from harness)
        return

    if mode == "new":
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ws), check=True)
        # Initial commit so branch-create can create branches from a real ref
        gitignore = ws / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("__pycache__/\n*.pyc\n.venv/\n.pytest_cache/\n.coverage\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=str(ws), check=True)
        subprocess.run(
            ["git", "-c", "user.email=harness@local", "-c", "user.name=python-app-dev",
             "commit", "-q", "-m", "chore: initial workspace scaffold"],
            cwd=str(ws), check=True,
        )
        return

    if mode == "maintenance":
        # The user-supplied project_path must be a real git repo on its own.
        # If walking up from ws lands at harness_root's .git, refuse.
        try:
            top = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(ws), capture_output=True, text=True, check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            raise RuntimeError(
                f"maintenance workspace {ws} is not a git repo and harness will not auto-init it"
            )
        if Path(top).resolve() == harness_root().resolve():
            raise RuntimeError(
                f"maintenance workspace {ws} resolves to the harness repo itself. "
                f"Refusing to operate on the harness's own git."
            )
        return


# ---------------------------------------------------------------------------
# Effective thresholds
# ---------------------------------------------------------------------------

def merge_effective_thresholds(state: dict) -> dict:
    cfg = load_config()
    thresholds = dict(cfg.get("thresholds", {}))
    caps = dict(cfg.get("caps", {}))

    spec = state.get("interview_spec", {})
    if "coverage_threshold" in spec:
        thresholds["coverage_threshold"] = spec["coverage_threshold"]
    if "mypy_strict" in spec:
        thresholds["mypy_strict"] = spec["mypy_strict"]
    if "pytest_parallel" in spec:
        thresholds["pytest_parallel"] = spec["pytest_parallel"]
    if "sanity_scenarios_per_phase" in spec:
        thresholds["sanity_scenarios_per_phase"] = spec["sanity_scenarios_per_phase"]
    if spec.get("security_review"):
        thresholds["security_review"] = True

    # Per-run cap overrides
    for k, v in (state.get("overrides", {}) or {}).items():
        caps[k] = v

    return {"thresholds": thresholds, "caps": caps, **thresholds}


def write_effective_thresholds(run_dir: Path, eff: dict) -> None:
    (run_dir / "effective_thresholds.json").write_text(
        json.dumps(eff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Multi-writer feedback (0-4)
# ---------------------------------------------------------------------------

FEEDBACK_SEPARATOR = "\n\n---\n<!-- previous entries below -->\n\n"


def write_feedback(stage_dir_path: Path, source: str, body: str) -> Path:
    """Prepend a new entry; preserve previous content below a separator."""
    fb = stage_dir_path / "feedback.md"
    stage_dir_path.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    new_entry = f"<!-- {source} @ {timestamp} -->\n{body.strip()}\n"
    if fb.exists():
        previous = fb.read_text(encoding="utf-8")
        fb.write_text(new_entry + FEEDBACK_SEPARATOR + previous, encoding="utf-8")
    else:
        fb.write_text(new_entry, encoding="utf-8")
    return fb


# ---------------------------------------------------------------------------
# Headless invocation
# ---------------------------------------------------------------------------

def invoke_claude(prompt_path: Path, stage: str, run_dir: Path, phase: int | None, log_path: Path, state: dict) -> tuple[int, str]:
    tools = STAGE_TOOLS[stage]
    allowed = ",".join(tools)

    # The wrapper is a pointer; it tells the LLM where the canonical prompt is.
    wrapper = (
        f"You are running the '{stage}' stage of the python-app-dev harness.\n"
        f"Run id: {run_dir.name}\n"
        f"Phase: {phase if phase is not None else 'N/A'}\n"
        f"Read and follow the materialized prompt at:\n"
        f"  {prompt_path}\n"
        f"Output the marker line `{STAGE_MARKER[stage]}: <path>` on the last line of stdout.\n"
    )

    cmd = [
        "claude",
        "-p", wrapper,
        "--allowedTools", allowed,
        "--permission-mode", "acceptEdits",
    ]

    # Workspace separation (CRITICAL): phase-level stages run in the project workspace,
    # NOT the harness root. Prevents git operations from leaking into the harness repo.
    cwd: Path
    if stage in PHASE_LEVEL_STAGES:
        ws = resolve_workspace(state)
        if ws is None:
            return 2, "ERROR: phase-level stage requires resolvable workspace"
        ensure_workspace_repo(ws, state)
        cwd = ws
    else:
        # Run-level stages don't touch git; safe to run from run_dir for output locality
        cwd = run_dir

    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 30, cwd=str(cwd))
    except FileNotFoundError:
        return 127, "ERROR: `claude` CLI not found in PATH"
    except subprocess.TimeoutExpired:
        return 124, "ERROR: stage timed out (30 min)"

    log_path.write_text(
        f"# stage: {stage}\n# returncode: {proc.returncode}\n\n"
        f"## stdout\n{proc.stdout}\n\n## stderr\n{proc.stderr}\n",
        encoding="utf-8",
    )
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def check_marker(stdout: str, marker: str) -> bool:
    for line in reversed(stdout.splitlines()):
        if line.strip().startswith(marker + ":"):
            return True
    return False


# ---------------------------------------------------------------------------
# Verdict shape-check
# ---------------------------------------------------------------------------

def parse_simple_yaml_file(path: Path) -> dict:
    return _parse_simple_yaml(path.read_text(encoding="utf-8"))


def validate_verdict(verdict_path: Path) -> tuple[bool, str, dict]:
    if not verdict_path.exists():
        return False, f"verdict file missing: {verdict_path}", {}
    try:
        data = parse_simple_yaml_file(verdict_path)
    except Exception as e:
        return False, f"verdict parse error: {e}", {}

    label = (data.get("verdict") or "").strip().lower()
    loop_target = (data.get("loop_target") or "").strip().lower()

    if label not in VALID_VERDICT_LABELS:
        return False, f"verdict label invalid: '{label}' (valid: {sorted(VALID_VERDICT_LABELS)})", data
    if loop_target not in VALID_LOOP_TARGETS:
        return False, f"loop_target invalid: '{loop_target}' (valid: {sorted(VALID_LOOP_TARGETS)})", data
    if loop_target not in VERDICT_TO_LOOP[label]:
        return False, (
            f"label/loop_target mismatch: verdict='{label}' allows {sorted(VERDICT_TO_LOOP[label])} "
            f"but loop_target='{loop_target}'"
        ), data

    return True, "ok", data


# ---------------------------------------------------------------------------
# Backtrack — clear LLM and orchestrator outputs
# ---------------------------------------------------------------------------

def clear_stage_outputs(run_dir: Path, stages: list[str], phase: int | None) -> None:
    """Remove both LLM artifacts and orchestrator artifacts for the given stages."""
    for stage in stages:
        sd = stage_dir(run_dir, stage, phase)
        if sd.exists() and sd != run_dir:
            shutil.rmtree(sd, ignore_errors=True)
        else:
            # run-level: only remove this stage's primary output and feedback dir
            primary = stage_primary_output(run_dir, stage, phase)
            primary.unlink(missing_ok=True)
            (run_dir / stage).mkdir(parents=True, exist_ok=True)
            shutil.rmtree(run_dir / stage, ignore_errors=True)


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

def write_escalation(run_dir: Path, trigger: str, context: dict) -> Path:
    esc = run_dir / "escalation.md"
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    body = [
        f"# Escalation @ {timestamp}",
        "",
        f"- **trigger**: `{trigger}`",
        f"- **stage**: `{context.get('stage')}`",
        f"- **phase**: `{context.get('phase')}`",
        "",
        "## State",
        "```json",
        json.dumps(context, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Cross-run history (recent N=10)",
    ]
    body.extend(_recent_escalations_block(trigger))
    body += [
        "",
        "## Awaiting human decision",
        "Edit `escalation.decision.md` (sibling file) with one of:",
        "```yaml",
        "decision: continue | abort | resume_from_<stage>",
        "feedback: |",
        "  free text (will be propagated to resume target's feedback.md)",
        "```",
    ]
    esc.write_text("\n".join(body) + "\n", encoding="utf-8")
    return esc


def _recent_escalations_block(trigger: str) -> list[str]:
    index = harness_root() / "outputs" / ".index.jsonl"
    if not index.exists():
        return ["- (no prior runs)"]
    matches: list[dict] = []
    for line in reversed(index.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        triggers = entry.get("escalation_triggers", []) or []
        if trigger in triggers:
            matches.append(entry)
        if len(matches) >= 10:
            break
    if not matches:
        return ["- (no prior runs hit this trigger)"]
    return [f"- run {e['run_id']} ({e.get('final_status')}): triggers={e.get('escalation_triggers')}" for e in matches]


# ---------------------------------------------------------------------------
# Result printing
# ---------------------------------------------------------------------------

def emit_result(decision: str, **fields) -> None:
    """Single-line machine-readable line consumed by the main session."""
    payload = {"decision": decision, **fields}
    print(f"ORCHESTRATE_RESULT: {json.dumps(payload, ensure_ascii=False)}")


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

def run_stage(stage: str, run_dir: Path, phase: int | None, resume: bool) -> int:
    state = load_state(run_dir)
    eff = merge_effective_thresholds(state)
    write_effective_thresholds(run_dir, eff)

    if phase is not None:
        ensure_phase_counters(state, phase)

    state["current_stage"] = stage
    state["current_phase"] = phase
    state["status"] = "running"
    state["counters"]["total_stages"] += 1
    save_state(run_dir, state)

    # lint-test owns its own self-correction loop (gate-run → LLM round → re-run → cap).
    if stage == "lint-test":
        return run_lint_test_loop(run_dir, phase, state, eff)

    prompt = materialize_prompt(stage, run_dir, phase, state, eff)

    log_path = stage_dir(run_dir, stage, phase) / "headless.log"
    rc, output = invoke_claude(prompt, stage, run_dir, phase, log_path, state)

    if rc != 0:
        state["status"] = "escalated"
        save_state(run_dir, state)
        write_escalation(run_dir, "headless_failed", {
            "stage": stage, "phase": phase, "returncode": rc, "tail": output[-1000:]
        })
        state["escalation_triggers"].append("headless_failed")
        save_state(run_dir, state)
        emit_result("escalated", trigger="headless_failed", stage=stage, phase=phase)
        return 3

    if not check_marker(output, STAGE_MARKER[stage]):
        write_escalation(run_dir, "missing_marker", {"stage": stage, "phase": phase})
        state["escalation_triggers"].append("missing_marker")
        state["status"] = "escalated"
        save_state(run_dir, state)
        emit_result("escalated", trigger="missing_marker", stage=stage, phase=phase)
        return 3

    # Required aux outputs
    sd = stage_dir(run_dir, stage, phase)
    aux = STAGE_REQUIRED_AUX_OUTPUTS.get(stage, [STAGE_PRIMARY_OUTPUT[stage]])
    if STAGE_PRIMARY_OUTPUT[stage] not in aux:
        aux = aux + [STAGE_PRIMARY_OUTPUT[stage]]
    missing = [a for a in aux if not (sd / a).exists()]
    if missing:
        write_escalation(run_dir, "missing_aux", {"stage": stage, "phase": phase, "missing": missing})
        state["escalation_triggers"].append("missing_aux")
        state["status"] = "escalated"
        save_state(run_dir, state)
        emit_result("escalated", trigger="missing_aux", stage=stage, phase=phase, missing=missing)
        return 3

    # sanity-test owns the deterministic post-LLM gate (route() reads gates/sanity.json).
    if stage == "sanity-test":
        run_sanity_gate(run_dir, phase, state)

    # Stage-specific routing
    state["stage_outputs"][stage_key(stage, phase)] = str(sd / STAGE_PRIMARY_OUTPUT[stage])
    save_state(run_dir, state)

    return route(stage, run_dir, phase, state, eff)


def stage_key(stage: str, phase: int | None) -> str:
    return f"{stage}__phase_{phase}" if phase is not None else stage


# ---------------------------------------------------------------------------
# Gate runners
# ---------------------------------------------------------------------------

LINT_TEST_GATES = ["install", "lint", "format", "types", "tests", "coverage"]


def run_lint_test_gates(run_dir: Path, phase: int | None, state: dict, eff: dict) -> list[dict]:
    workspace = resolve_workspace(state)
    if workspace is None or not workspace.exists():
        # No workspace yet — return empty; caller treats this as gates-incomplete (escalates).
        return []
    toolchain_path = run_dir / "interview" / "toolchain.json"
    threshold = eff.get("coverage_threshold", 70)
    results: list[dict] = []
    for gate in LINT_TEST_GATES:
        cmd = [
            sys.executable,
            str(harness_root() / "scripts" / "run_gate.py"),
            gate,
            "--run-dir", str(run_dir),
            "--phase", str(phase),
            "--workspace", str(workspace),
        ]
        if toolchain_path.exists():
            cmd += ["--toolchain", str(toolchain_path)]
        if gate == "coverage":
            cmd += ["--threshold", str(threshold)]
        subprocess.run(cmd, capture_output=True, text=True)
        gate_path = run_dir / f"phase-{phase}" / "gates" / f"{gate}.json"
        if gate_path.exists():
            data = json.loads(gate_path.read_text(encoding="utf-8"))
            data["_path"] = str(gate_path)
            results.append(data)
    return results


def run_sanity_gate(run_dir: Path, phase: int | None, state: dict) -> dict | None:
    """Run the deterministic sanity gate after the sanity-test LLM stage finishes.

    The LLM authors and runs the tests itself, but route() consumes
    gates/sanity.json — only this function writes it. Returns the gate dict
    (or None when the workspace is unresolvable).
    """
    workspace = resolve_workspace(state)
    if workspace is None or not workspace.exists():
        return None
    toolchain_path = run_dir / "interview" / "toolchain.json"
    cmd = [
        sys.executable,
        str(harness_root() / "scripts" / "run_gate.py"),
        "sanity",
        "--run-dir", str(run_dir),
        "--phase", str(phase),
        "--workspace", str(workspace),
    ]
    if toolchain_path.exists():
        cmd += ["--toolchain", str(toolchain_path)]
    subprocess.run(cmd, capture_output=True, text=True)
    gate_path = run_dir / f"phase-{phase}" / "gates" / "sanity.json"
    if gate_path.exists():
        return json.loads(gate_path.read_text(encoding="utf-8"))
    return None


def run_lint_test_loop(run_dir: Path, phase: int | None, state: dict, eff: dict) -> int:
    """LLM-driven self-correction loop with a hard cap.

    Each round:
      1. Orchestrator runs all 6 gates.
      2. If any expected gate JSON is missing → escalate (fail-closed).
      3. If all pass → emit pass.
      4. If counter+1 > cap → backtrack to design.
      5. Otherwise: write feedback, invoke LLM round, validate marker/aux,
         increment counter, then re-run gates.
    """
    stage = "lint-test"
    caps = eff.get("caps", {})
    cap = int(caps.get("lint_test_self_correct", 5))
    counter_key = f"lint_test_self_correct__phase_{phase}"
    sd = stage_dir(run_dir, stage, phase)
    sd.mkdir(parents=True, exist_ok=True)
    primary = stage_primary_output(run_dir, stage, phase)

    while True:
        gate_results = run_lint_test_gates(run_dir, phase, state, eff)
        names = {g["name"] for g in gate_results}
        missing_gates = sorted(set(LINT_TEST_GATES) - names)
        if missing_gates:
            return _escalate(
                run_dir, state, "lint_test_gates_incomplete", stage, phase,
                extra={"missing_gates": missing_gates,
                       "hint": "run_gate.py failed to write JSON for these gates"},
            )

        all_passed = all(g["passed"] for g in gate_results)
        if all_passed:
            if not primary.exists():
                primary.write_text(
                    "# Lint/Test 자가 교정 로그\n\n"
                    "All gates passed without LLM intervention.\n",
                    encoding="utf-8",
                )
            state["stage_outputs"][stage_key(stage, phase)] = str(primary)
            save_state(run_dir, state)
            emit_result("pass", stage=stage, phase=phase, output=str(primary))
            return 0

        round_n = state["counters"][counter_key] + 1
        if round_n > cap:
            failing = [g["name"] for g in gate_results if not g["passed"]]
            _backtrack_to(
                run_dir, state, "design", phase, source="lint-test-cap",
                body=f"lint-test self-correct cap ({cap}) reached.\nFailing gates: {failing}",
            )
            state["escalation_triggers"].append("lint_test_cap")
            save_state(run_dir, state)
            emit_result("loopback", to="design", phase=phase,
                        count=round_n - 1, trigger="lint_test_cap")
            return 0

        feedback_body = (
            f"Gate results (round {round_n}):\n"
            + "\n".join(
                f"- {g['name']}: passed={g['passed']} (file: {g['_path']})"
                for g in gate_results
            )
        )
        write_feedback(sd, f"gate-results-round-{round_n}", feedback_body)

        prompt = materialize_prompt(stage, run_dir, phase, state, eff)
        log_path = sd / f"headless-round-{round_n}.log"
        rc, output = invoke_claude(prompt, stage, run_dir, phase, log_path, state)

        if rc != 0:
            return _escalate(
                run_dir, state, "headless_failed", stage, phase,
                extra={"round": round_n, "returncode": rc, "tail": output[-1000:]},
            )
        if not check_marker(output, STAGE_MARKER[stage]):
            return _escalate(
                run_dir, state, "missing_marker", stage, phase,
                extra={"round": round_n},
            )
        if not primary.exists():
            return _escalate(
                run_dir, state, "missing_aux", stage, phase,
                extra={"round": round_n, "missing": [STAGE_PRIMARY_OUTPUT[stage]]},
            )

        state["counters"][counter_key] = round_n
        save_state(run_dir, state)
        # loop continues — orchestrator re-runs gates next iteration


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route(stage: str, run_dir: Path, phase: int | None, state: dict, eff: dict) -> int:
    caps = eff.get("caps", {})

    # User intervention toggle: emit awaiting_user if applicable for this stage
    spec = state.get("interview_spec", {})
    interventions = spec.get("interventions", {}) or {}

    intervention_map = {
        "planning":     interventions.get("planning", True),
        "requirements": interventions.get("requirements", True),
        "phase-split":  interventions.get("phase_split", True),
        "design":       interventions.get("design_per_phase", False),
        "pr-create":    interventions.get("pr_per_phase", True),  # actually triggered before, but treated here as post-stage approval
    }

    # code-review: read verdict and route
    if stage == "code-review":
        verdict_path = stage_dir(run_dir, stage, phase) / "verdict.yaml"
        ok, msg, data = validate_verdict(verdict_path)
        if not ok:
            write_escalation(run_dir, "verdict_invalid", {
                "stage": stage, "phase": phase, "reason": msg
            })
            state["escalation_triggers"].append("verdict_invalid")
            state["status"] = "escalated"
            save_state(run_dir, state)
            emit_result("escalated", trigger="verdict_invalid", reason=msg)
            return 3

        # Cross-check: any failed gate but verdict pass → escalate (objective wins)
        gates_dir = run_dir / f"phase-{phase}" / "gates"
        if gates_dir.exists() and data["verdict"] == "pass":
            for gp in gates_dir.glob("*.json"):
                gd = json.loads(gp.read_text(encoding="utf-8"))
                if gd.get("passed") is False:
                    write_escalation(run_dir, "gate_vs_verdict", {
                        "stage": stage, "phase": phase, "failed_gate": gd["name"]
                    })
                    state["escalation_triggers"].append("gate_vs_verdict")
                    state["status"] = "escalated"
                    save_state(run_dir, state)
                    emit_result("escalated", trigger="gate_vs_verdict")
                    return 3

        state["verdict_history"].append({
            "stage": stage, "phase": phase, "verdict": data["verdict"]
        })

        v = data["verdict"]
        if v == "pass":
            save_state(run_dir, state)
            emit_result("pass", stage=stage, phase=phase, next="sanity-test")
            return 0
        if v == "minor":
            ck = f"code_review_minor__phase_{phase}"
            state["counters"][ck] += 1
            if state["counters"][ck] > caps.get("code_review_minor_loop", 3):
                return _escalate(run_dir, state, "code_review_minor_cap", stage, phase)
            _backtrack_to(run_dir, state, "implement", phase, source="code-review-minor",
                          body=_render_issues(data, "issues_minor"))
            save_state(run_dir, state)
            emit_result("loopback", to="implement", phase=phase, count=state["counters"][ck])
            return 0
        if v == "major":
            ck = f"code_review_major__phase_{phase}"
            state["counters"][ck] += 1
            if state["counters"][ck] > caps.get("code_review_major_loop", 2):
                return _escalate(run_dir, state, "code_review_major_cap", stage, phase)
            _backtrack_to(run_dir, state, "design", phase, source="code-review-major",
                          body=_render_issues(data, "issues_major"))
            save_state(run_dir, state)
            emit_result("loopback", to="design", phase=phase, count=state["counters"][ck])
            return 0
        if v == "critical":
            return _escalate(run_dir, state, "code_review_critical", stage, phase, extra=data)

    # design: self-loop on `verdict: needs_revision` in front matter
    if stage == "design":
        primary = stage_primary_output(run_dir, stage, phase)
        verdict_in_design = _read_front_matter_field(primary, "verdict") or "pass"
        if verdict_in_design == "needs_revision":
            ck = f"design_self__phase_{phase}"
            state["counters"][ck] += 1
            if state["counters"][ck] > caps.get("design_self", 2):
                return _escalate(run_dir, state, "design_self_cap", stage, phase)
            write_feedback(stage_dir(run_dir, stage, phase), "design-self",
                           "architect-reviewer requested revision; see design.md issues")
            save_state(run_dir, state)
            emit_result("loopback", to="design", phase=phase, count=state["counters"][ck])
            return 0

    # sanity-test: gate passed/failed
    if stage == "sanity-test":
        gate_path = run_dir / f"phase-{phase}" / "gates" / "sanity.json"
        if not gate_path.exists():
            return _escalate(run_dir, state, "sanity_gate_missing", stage, phase)
        gd = json.loads(gate_path.read_text(encoding="utf-8"))
        if not gd.get("passed"):
            ck = f"sanity__phase_{phase}"
            state["counters"][ck] += 1
            if state["counters"][ck] > caps.get("sanity_loop", 2):
                return _escalate(run_dir, state, "sanity_cap", stage, phase)
            _backtrack_to(run_dir, state, "design", phase, source="sanity-fail",
                          body=f"sanity test failed: {gate_path}")
            save_state(run_dir, state)
            emit_result("loopback", to="design", phase=phase, count=state["counters"][ck])
            return 0

    # User intervention (post-stage approval gate, when toggled on)
    if stage in intervention_map and intervention_map[stage]:
        state["status"] = "awaiting_user"
        state["awaiting_input_schema"] = _intervention_schema_for(stage)
        save_state(run_dir, state)
        emit_result("awaiting_user", stage=stage, phase=phase, schema=_intervention_schema_for(stage))
        return 0

    # Default: pass
    state["status"] = "running"
    save_state(run_dir, state)
    emit_result("pass", stage=stage, phase=phase, next=_default_next_stage(stage, phase))
    return 0


def _intervention_schema_for(stage: str) -> dict:
    if stage == "planning":
        return {"decision": "approve|reject|revise", "feedback": "string"}
    if stage == "requirements":
        return {"decision": "approve|reject|revise", "feedback": "string",
                "add_requirements": "list", "remove_ids": "list"}
    if stage == "phase-split":
        return {"decision": "approve|reject|revise", "feedback": "string",
                "merge_phases": "list[list]", "split_phase": "int", "reorder": "list[int]"}
    if stage == "design":
        return {"decision": "approve|revise", "feedback": "string"}
    if stage == "pr-create":
        return {"decision": "approve|revise|set_mode", "mode": "auto|manual", "feedback": "string"}
    return {}


def _default_next_stage(stage: str, phase: int | None) -> str | None:
    seq_run = ["planning", "requirements", "phase-split"]
    seq_phase = ["design", "branch-create", "implement", "lint-test", "code-review",
                 "sanity-test", "document", "pr-create"]
    if stage in seq_run:
        idx = seq_run.index(stage)
        if idx + 1 < len(seq_run):
            return seq_run[idx + 1]
        return "design"  # next is first phase's design
    if stage in seq_phase:
        idx = seq_phase.index(stage)
        if idx + 1 < len(seq_phase):
            return seq_phase[idx + 1]
        return "next-phase-or-delivery"
    if stage == "delivery":
        return None
    return None


def _read_front_matter_field(path: Path, field: str) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    fm = text[4:end]
    for line in fm.splitlines():
        if line.startswith(field + ":"):
            return line.split(":", 1)[1].strip()
    return None


def _render_issues(verdict: dict, key: str) -> str:
    issues = verdict.get(key, []) or []
    if not issues:
        return f"{key}: (none)"
    lines = [f"# {key}"]
    for i, iss in enumerate(issues, 1):
        lines.append(f"\n## {i}. {iss}")
    return "\n".join(lines)


def _backtrack_to(run_dir: Path, state: dict, target_stage: str, phase: int | None, source: str, body: str) -> None:
    # 1. Decide which downstream stages to clear
    seq_phase = ["design", "branch-create", "implement", "lint-test", "code-review",
                 "sanity-test", "document", "pr-create"]
    if target_stage not in seq_phase:
        return
    idx = seq_phase.index(target_stage)
    to_clear = seq_phase[idx + 1:]  # everything after the target

    # 2. Clear LLM artifacts AND orchestrator-owned artifacts
    clear_stage_outputs(run_dir, to_clear, phase)
    # gates dir is orchestrator-owned and tied to the phase, not a stage
    # but we keep gates/ since later stages will rewrite them as needed.

    # 3. Pop last verdict (history-appending stage re-entry)
    if state["verdict_history"]:
        state["verdict_history"].pop()

    # 4. Write feedback for the resume target stage
    write_feedback(stage_dir(run_dir, target_stage, phase), source, body)

    # 5. Reset stage_outputs entries for cleared stages
    for s in to_clear:
        state["stage_outputs"].pop(stage_key(s, phase), None)


def _escalate(run_dir: Path, state: dict, trigger: str, stage: str, phase: int | None, extra: dict | None = None) -> int:
    write_escalation(run_dir, trigger, {
        "stage": stage, "phase": phase, "extra": extra or {}
    })
    state["escalation_triggers"].append(trigger)
    state["status"] = "escalated"
    save_state(run_dir, state)
    emit_result("escalated", trigger=trigger, stage=stage, phase=phase)
    return 3


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=sorted(STAGE_DIRS))
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", type=int, default=None)
    parser.add_argument("--resume", action="store_true",
                        help="Resume after a user intervention (re-runs the same stage with feedback applied)")
    args = parser.parse_args(argv)

    if args.stage in PHASE_LEVEL_STAGES and args.phase is None:
        print(f"ERROR: stage '{args.stage}' requires --phase N", file=sys.stderr)
        return 2
    if args.stage in RUN_LEVEL_STAGES and args.phase is not None and args.stage != "delivery":
        print(f"ERROR: stage '{args.stage}' must not have --phase", file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir).resolve()
    if not (run_dir / "state.json").exists():
        print(f"ERROR: no state.json in {run_dir}", file=sys.stderr)
        return 2

    return run_stage(args.stage, run_dir, args.phase, args.resume)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
