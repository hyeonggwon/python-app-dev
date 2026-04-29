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
# Stage tables (single source of truth — scripts/tests/test_invariants.py cross-checks these)
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
    "pr-publish":    "phase-{N}",
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
    ],
    "pr-publish": [
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
    "code-review": ["review.md", "verdict.json"],
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
    "pr-publish":    "pr-url.txt",
    "delivery":      "delivery.md",
}

# Files (glob patterns) each stage owns under its stage_dir. Used by
# clear_stage_outputs on backtrack — phase-level stages share the same
# stage_dir, so we cannot rmtree the directory. Instead we unlink only the
# files this stage produced. Patterns are glob-relative to stage_dir.
STAGE_OWNED_PATTERNS = {
    "planning":      ["planning.md"],
    "requirements":  ["requirements.md"],
    "phase-split":   ["phases.md"],
    "design":        ["design.md"],
    "branch-create": ["branch.txt"],
    "implement":     ["implementation.md"],
    "lint-test": [
        "lint-test.md",
        "headless-round-*.log",
        "gates/install.json", "gates/install.stdout.txt", "gates/install.stderr.txt",
        "gates/lint.json",    "gates/lint.stdout.txt",    "gates/lint.stderr.txt",
        "gates/format.json",  "gates/format.stdout.txt",  "gates/format.stderr.txt",
        "gates/types.json",   "gates/types.stdout.txt",   "gates/types.stderr.txt",
        "gates/tests.json",   "gates/tests.stdout.txt",   "gates/tests.stderr.txt",
        "gates/coverage.json","gates/coverage.stdout.txt","gates/coverage.stderr.txt",
    ],
    "code-review":   ["review.md", "verdict.json"],
    "sanity-test": [
        "sanity.md",
        "gates/sanity.json", "gates/sanity.stdout.txt", "gates/sanity.stderr.txt",
    ],
    "document":      ["docs-changes.md"],
    "pr-create":     ["pr.md"],
    "pr-publish":    ["pr-url.txt"],
    "delivery":      ["delivery.md"],
}

STAGE_MARKER = {k: f"{k.replace('-', '_').upper()}_DONE" for k in STAGE_DIRS}

RUN_LEVEL_STAGES = {"planning", "requirements", "phase-split", "delivery"}
PHASE_LEVEL_STAGES = set(STAGE_DIRS) - RUN_LEVEL_STAGES


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

VALID_VERDICT_LABELS = {"pass", "minor", "major", "critical"}
VALID_DESIGN_VERDICT_LABELS = {"pass", "needs_revision"}
VALID_LOOP_TARGETS = {"none", "implement", "design", "escalation"}

VERDICT_TO_LOOP = {
    "pass":     {"none"},
    "minor":    {"implement"},
    "major":    {"design"},
    "critical": {"escalation"},
}

# Gate status enum (run_gate.py is the writer; orchestrator is the reader).
# Distinguishing skipped_ok from skipped_fail is what closed the
# "tests/coverage skipped → silent pass" hole: previously every skip was
# `passed=True`, so a workspace with no `tests/` directory looked identical
# to a workspace whose tests passed.
GATE_PASSING_STATUSES = {"passed", "skipped_ok"}
GATE_FAILING_STATUSES = {"failed", "skipped_fail"}


def gate_is_passing(gate: dict) -> bool:
    """Return True iff a gate JSON represents a pass for routing purposes.

    Reads the new ``status`` field if present; falls back to the legacy
    ``passed`` bool for forward/back compat with older gate JSONs that may
    still be on disk from a previous run.
    """
    status = (gate.get("status") or "").strip()
    if status in GATE_PASSING_STATUSES:
        return True
    if status in GATE_FAILING_STATUSES:
        return False
    return bool(gate.get("passed"))


def gates_all_passing(gates: list[dict]) -> bool:
    return all(gate_is_passing(g) for g in gates)


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


_TRUTHY_STRINGS = {"true", "yes", "on", "1"}
_FALSY_STRINGS = {"false", "no", "off", "0", ""}


def _truthy(value: object, *, default: bool = False) -> bool:
    """Coerce a config-like value to bool with explicit string handling.

    Why this exists: spec.md is YAML and the LLM merges it into
    state.interview_spec as JSON. JSON has no on/off literal, so a careless
    merge can land ``"planning": "off"`` (string). A naive ``if x:`` then sees
    "off" as truthy and fires the very intervention the user disabled. Same
    risk for ``security_review``, ``mypy_strict``, ``pytest_parallel``.

    Recognized:
      - bool / int / float           → bool(value)
      - "true"/"yes"/"on"/"1"        → True   (case- and whitespace-insensitive)
      - "false"/"no"/"off"/"0"/""    → False
      - None                         → ``default``
      - any other string             → ``default`` (don't guess intent)

    Pass ``default=True`` for keys whose missing-or-malformed semantics is "on"
    (e.g. user-confirmation interventions) so that an unrecognized string
    doesn't silently disable a guard the user asked for.
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
            # YAML 1.1 boolean spellings — kept narrow so that `1`/`0` still
            # parse as int (a YAML 1.2 contributor writing `coverage_threshold: 1`
            # must not get the bool True). `_truthy` is broader because it has
            # to handle stringly-typed values that flow in via JSON state, but
            # *here* we own the parse.
            low = val.lower()
            if low in {"true", "on", "yes"}:
                parent[key] = True
            elif low in {"false", "off", "no"}:
                parent[key] = False
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
        "lint_test_self_correct", "lint_test_design",
        "code_review_minor", "code_review_major",
        "sanity", "design_arch_self", "design_revise", "pr_create_revise",
    ]
    for k in keys:
        ck = f"{k}__phase_{phase}"
        if ck not in state["counters"]:
            state["counters"][ck] = 0


# In-stage retry counters reset on backtrack (one fresh attempt per re-entry).
# Verdict-type counters (code_review_minor/major) and user-revise counters
# (design_revise, pr_create_revise) are intentionally cumulative.
IN_STAGE_RETRY_COUNTERS = {
    "design":      "design_arch_self",
    "lint-test":   "lint_test_self_correct",
    # sanity__phase_N is intentionally cumulative across backtracks: each
    # sanity-fail → design loop iteration is a round of the *same* sanity
    # loop, not a fresh in-stage attempt. Resetting it here would make the
    # `sanity_loop` cap unreachable (the counter would be cleared on the very
    # backtrack that just incremented it).
}


def _revise_counter_keys(stage: str, phase: int | None) -> tuple[str, str] | None:
    """Return (counter_key, cap_key) for a user-revise on this stage, or None.

    For phase-level stages with a per-phase counter, returns the resolved key
    (e.g. ``design_revise__phase_2``). For run-level stages, returns the bare key.
    """
    if stage == "planning":
        return ("planning_revise", "planning_revise")
    if stage == "requirements":
        return ("requirements_revise", "requirements_revise")
    if stage == "phase-split":
        return ("phase_split_revise", "phase_split_revise")
    if stage == "design" and phase is not None:
        return (f"design_revise__phase_{phase}", "design_revise")
    if stage == "pr-create" and phase is not None:
        return (f"pr_create_revise__phase_{phase}", "pr_create_revise")
    return None


def _render_user_feedback(user_input: dict) -> str:
    parts: list[str] = []
    if user_input.get("feedback"):
        parts.append("## 사용자 피드백")
        parts.append(str(user_input["feedback"]).strip())
    extras = {k: v for k, v in user_input.items() if k not in ("decision", "feedback")}
    if extras:
        parts.append("## 사용자 입력 (구조화)")
        parts.append("```json")
        parts.append(json.dumps(extras, indent=2, ensure_ascii=False))
        parts.append("```")
    return "\n\n".join(parts) if parts else "(사용자가 revise 만 표시; 자유 본문 없음)"


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
        # phase=1 has no prior phase; resolve to a non-numeric sentinel so the
        # path becomes obviously invalid (`phase-__none__/...`) instead of an
        # empty-segment path (`phase-/...`) that looks like a real typo'd ref
        # and might confuse the LLM. The design.md prompt's `(if N > 1)`
        # qualifier already tells the LLM to skip this branch for phase=1.
        "{prev}": str(phase - 1) if phase is not None and phase > 1 else "__none__",
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

    # In maintenance mode the workspace must already exist as a git repo. We
    # refuse to mkdir it because a typo'd project_path would otherwise leave
    # an empty directory on the user's filesystem and pass on the next run.
    if mode == "maintenance":
        if not ws.exists():
            raise RuntimeError(
                f"maintenance workspace {ws} does not exist. "
                f"check interview_spec.project_path."
            )
        if not (ws / ".git").exists():
            raise RuntimeError(
                f"maintenance workspace {ws} is not a git repo and harness will not auto-init it"
            )
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

    # mode == "new" (or anything else we own): safe to mkdir.
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
    # Boolean toggles run through `_truthy` so a string "false"/"off" merged
    # into spec doesn't survive into effective_thresholds.json as a truthy
    # string for downstream readers (run_gate.make_command, code-review prompt).
    if "mypy_strict" in spec:
        thresholds["mypy_strict"] = _truthy(spec["mypy_strict"], default=False)
    if "pytest_parallel" in spec:
        thresholds["pytest_parallel"] = _truthy(spec["pytest_parallel"], default=False)
    if "sanity_scenarios_per_phase" in spec:
        thresholds["sanity_scenarios_per_phase"] = spec["sanity_scenarios_per_phase"]
    if _truthy(spec.get("security_review"), default=False):
        thresholds["security_review"] = True

    # Per-run cap overrides. Two sources, in this precedence:
    #   1. interview/spec.md.caps (parsed by deep-interview into
    #      state.interview_spec.caps) — the documented user-facing knob
    #      (CLAUDE.md, interview-guide.md §6/§7).
    #   2. state.overrides — programmatic last-word override.
    # Earlier revisions only honored (2), which silently dropped any caps
    # the user set in spec.md. Apply both, with state.overrides winning.
    for k, v in (spec.get("caps", {}) or {}).items():
        caps[k] = v
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

    # Workspace separation (CRITICAL): any stage that may invoke git must run
    # in the project workspace, NOT the harness root. Otherwise `git log` /
    # `git rev-parse` walks up to the harness repo's own .git and returns
    # harness commits — violating the workspace-vs-harness separation that
    # CLAUDE.md emphasizes.
    #
    # All phase-level stages need the workspace cwd. delivery is run-level but
    # also reads phase commit history (its STAGE_TOOLS includes Bash(git log:*)
    # / Bash(git rev-parse:*) and its prompt declares Workspace cwd).
    cwd: Path
    needs_workspace_cwd = stage in PHASE_LEVEL_STAGES or stage == "delivery"
    if needs_workspace_cwd:
        ws = resolve_workspace(state)
        if ws is None:
            return 2, f"ERROR: stage '{stage}' requires resolvable workspace"
        ensure_workspace_repo(ws, state)
        cwd = ws
    else:
        # Pure run-level stages (planning/requirements/phase-split) don't touch git;
        # safe to run from run_dir for output locality.
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

def validate_verdict(verdict_path: Path) -> tuple[bool, str, dict]:
    if not verdict_path.exists():
        return False, f"verdict file missing: {verdict_path}", {}
    try:
        data = json.loads(verdict_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"verdict parse error: {e}", {}
    if not isinstance(data, dict):
        return False, f"verdict file is not a JSON object: top-level is {type(data).__name__}", {}

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
    """Remove only the files each stage owns under its stage_dir.

    Phase-level stages share `phase-{N}/` as their stage_dir, so we cannot
    rmtree the directory — that would also delete sibling stages' outputs we
    want to preserve, and the shared `feedback.md`. Instead we glob each
    pattern in STAGE_OWNED_PATTERNS relative to stage_dir and unlink matches.
    """
    for stage in stages:
        sd = stage_dir(run_dir, stage, phase)
        if not sd.exists():
            continue
        for pattern in STAGE_OWNED_PATTERNS.get(stage, []):
            for match in sd.glob(pattern):
                if match.is_dir():
                    shutil.rmtree(match, ignore_errors=True)
                else:
                    match.unlink(missing_ok=True)


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


def _append_run_index(state: dict, final_status: str) -> None:
    """Append exactly one line per terminating run to outputs/.index.jsonl.

    Called from terminal points (delivery success, abort, or unrecoverable
    escalation acknowledgement). Idempotent within a run via state["index_written"].
    """
    if state.get("index_written"):
        return
    index = harness_root() / "outputs" / ".index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    spec = state.get("interview_spec", {}) or {}
    entry = {
        "run_id": state.get("run_id"),
        "harness": state.get("harness", "python-app-dev"),
        "mode": spec.get("mode"),
        "project_kind": spec.get("project_kind"),
        "jira_ticket": spec.get("jira_ticket"),
        "total_phases": state.get("total_phases"),
        "final_status": final_status,
        "escalation_triggers": list(state.get("escalation_triggers", [])),
        "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    with index.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    state["index_written"] = True


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
    caps = eff.get("caps", {})

    if phase is not None:
        ensure_phase_counters(state, phase)

    # Handle --resume: consume user_input from a prior awaiting_user gate.
    # approve → emit pass + route to next; revise → write feedback, increment
    # revise counter, fall through to re-run the stage; reject → escalate.
    if resume:
        rc = _handle_resume(stage, run_dir, phase, state, caps)
        if rc is not None:
            return rc
        # rc is None → revise path: state has been updated, fall through to re-run

    state["current_stage"] = stage
    state["current_phase"] = phase
    state["status"] = "running"
    state["counters"]["total_stages"] += 1
    save_state(run_dir, state)

    # Runaway cap: any stage transition counts; escalate if exceeded.
    total_cap = int(caps.get("total_stages", 200))
    if state["counters"]["total_stages"] > total_cap:
        return _escalate(
            run_dir, state, "total_stages_cap", stage, phase,
            extra={"total_stages": state["counters"]["total_stages"], "cap": total_cap},
        )

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


def ensure_toolchain_json(run_dir: Path, state: dict) -> Path:
    """Materialize {run_dir}/interview/toolchain.json from interview_spec.

    Deep-interview merges detect_toolchain results into
    state.interview_spec.detected_toolchain. run_gate.py reads a JSON file path
    via --toolchain. This helper bridges the two: it always (re)writes the
    file from current state so subsequent gate runs see the latest values.

    For mode=new (no detect step), interview_spec.detected_toolchain is
    typically null; we write an empty object so run_gate falls back to its
    built-in defaults (uv / ruff / mypy / pytest).
    """
    path = run_dir / "interview" / "toolchain.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    detected = state.get("interview_spec", {}).get("detected_toolchain") or {}
    path.write_text(json.dumps(detected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_lint_test_gates(run_dir: Path, phase: int | None, state: dict, eff: dict) -> list[dict]:
    workspace = resolve_workspace(state)
    if workspace is None or not workspace.exists():
        # No workspace yet — return empty; caller treats this as gates-incomplete (escalates).
        return []
    toolchain_path = ensure_toolchain_json(run_dir, state)
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
            "--toolchain", str(toolchain_path),
        ]
        if gate == "coverage":
            cmd += ["--threshold", str(threshold)]
        # Outer guard: run_gate.py has its own per-gate timeout (max 1800s for
        # tests/coverage). We add ~120s headroom for json/file I/O before
        # giving up, so a wedged child process can't pin the orchestrator.
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=1920)
        except subprocess.TimeoutExpired:
            pass
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
    toolchain_path = ensure_toolchain_json(run_dir, state)
    cmd = [
        sys.executable,
        str(harness_root() / "scripts" / "run_gate.py"),
        "sanity",
        "--run-dir", str(run_dir),
        "--phase", str(phase),
        "--workspace", str(workspace),
        "--toolchain", str(toolchain_path),
    ]
    # See run_lint_test_gates: outer timeout = inner cap (900s for sanity) + headroom.
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=1020)
    except subprocess.TimeoutExpired:
        pass
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
    total_cap = int(caps.get("total_stages", 200))
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

        all_passed = gates_all_passing(gate_results)
        if all_passed:
            if not primary.exists():
                primary.write_text(
                    "# Lint/Test 자가 교정 로그\n\n"
                    "All gates passed without LLM intervention.\n",
                    encoding="utf-8",
                )
            state["stage_outputs"][stage_key(stage, phase)] = str(primary)
            save_state(run_dir, state)
            emit_result("pass", stage=stage, phase=phase, next="code-review", output=str(primary))
            return 0

        round_n = state["counters"][counter_key] + 1
        if round_n > cap:
            failing = [g["name"] for g in gate_results if not gate_is_passing(g)]
            # Two-level cap. The in-stage `lint_test_self_correct` counter
            # bounds one pass; the cumulative `lint_test_design` counter bounds
            # how many times the same phase can re-enter lint-test via a
            # design backtrack. `lint_test_design__phase_N` is NOT in
            # IN_STAGE_RETRY_COUNTERS, so _backtrack_to does not reset it.
            design_loop_key = f"lint_test_design__phase_{phase}"
            design_loop_cap = int(caps.get("lint_test_design_loop", 2))
            state["counters"][design_loop_key] = (
                state["counters"].get(design_loop_key, 0) + 1
            )
            if state["counters"][design_loop_key] > design_loop_cap:
                return _escalate(
                    run_dir, state, "lint_test_design_cap", stage, phase,
                    extra={"failing_gates": failing,
                           "design_loops": state["counters"][design_loop_key],
                           "cap": design_loop_cap},
                )
            _backtrack_to(
                run_dir, state, "design", phase, source="lint-test-cap",
                body=f"lint-test self-correct cap ({cap}) reached.\nFailing gates: {failing}",
            )
            # NOTE: do NOT append to escalation_triggers here. This is a
            # loopback to design (a soft recovery path), not an escalation.
            # delivery's final_status check treats any non-empty
            # escalation_triggers as `escalated_recovered`, which would mark
            # otherwise-clean runs as escalated and pollute outputs/.index.jsonl
            # cross-run analytics.
            save_state(run_dir, state)
            emit_result("loopback", to="design", phase=phase,
                        count=round_n - 1, trigger="lint_test_cap")
            return 0

        feedback_body = (
            f"Gate results (round {round_n}):\n"
            + "\n".join(
                f"- {g['name']}: status={g.get('status', '?')} (file: {g['_path']})"
                for g in gate_results
            )
        )
        write_feedback(sd, f"gate-results-round-{round_n}", feedback_body)

        # Each LLM self-correction round is its own stage transition for the
        # purposes of the global runaway cap. Without this, an LLM that loops
        # forever fixing-then-breaking lint issues could rack up dozens of
        # claude invocations while the outer cap (200) sits idle, since the
        # outer cap is only hit on stage *re-entry* (run_stage line ~746).
        state["counters"]["total_stages"] += 1
        save_state(run_dir, state)
        if state["counters"]["total_stages"] > total_cap:
            return _escalate(
                run_dir, state, "total_stages_cap", stage, phase,
                extra={"total_stages": state["counters"]["total_stages"], "cap": total_cap,
                       "context": f"lint-test self-correct round {round_n}"},
            )

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

    # Coerce every value through `_truthy` — spec.md may store toggles as
    # YAML on/off which can land as the JSON string "off" after a careless
    # merge into state.interview_spec; plain truthy checks would then fire
    # the gate the user explicitly disabled.
    intervention_map = {
        "planning":     _truthy(interventions.get("planning"),         default=True),
        "requirements": _truthy(interventions.get("requirements"),     default=True),
        "phase-split":  _truthy(interventions.get("phase_split"),      default=True),
        "design":       _truthy(interventions.get("design_per_phase"), default=False),
        "pr-create":    _truthy(interventions.get("pr_per_phase"),     default=True),  # post-stage approval of pr.md draft (push happens in pr-publish)
    }

    # code-review: read verdict and route
    if stage == "code-review":
        verdict_path = stage_dir(run_dir, stage, phase) / "verdict.json"
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

        # Cross-check: any failed gate but verdict pass → escalate (objective wins).
        # `failed` here means status in {failed, skipped_fail}: a gate that
        # was supposed to run but couldn't (e.g., tests/ missing) is just as
        # damning as a gate that ran and exited non-zero. See
        # GATE_FAILING_STATUSES for the full enum.
        gates_dir = run_dir / f"phase-{phase}" / "gates"
        if gates_dir.exists() and data["verdict"] == "pass":
            for gp in gates_dir.glob("*.json"):
                try:
                    gd = json.loads(gp.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    write_escalation(run_dir, "gate_vs_verdict", {
                        "stage": stage, "phase": phase, "failed_gate": gp.stem,
                        "reason": f"gate JSON unreadable: {e}",
                    })
                    state["escalation_triggers"].append("gate_vs_verdict")
                    state["status"] = "escalated"
                    save_state(run_dir, state)
                    emit_result("escalated", trigger="gate_vs_verdict")
                    return 3
                if not gate_is_passing(gd):
                    write_escalation(run_dir, "gate_vs_verdict", {
                        "stage": stage, "phase": phase,
                        "failed_gate": gd.get("name", gp.stem),
                        "gate_status": gd.get("status"),
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
        raw_verdict = _read_front_matter_field(primary, "verdict")
        if raw_verdict is None:
            # Fail-closed: missing front matter or absent verdict field means
            # the architect-reviewer step was likely skipped. Defaulting to
            # "pass" here would let a half-written design.md flow into
            # branch-create. Surface it.
            return _escalate(
                run_dir, state, "verdict_invalid", stage, phase,
                extra={"verdict": None, "reason": "front-matter `verdict` field missing",
                       "valid": sorted(VALID_DESIGN_VERDICT_LABELS)},
            )
        verdict_in_design = raw_verdict.strip().lower()
        if verdict_in_design not in VALID_DESIGN_VERDICT_LABELS:
            return _escalate(
                run_dir, state, "verdict_invalid", stage, phase,
                extra={"verdict": verdict_in_design, "valid": sorted(VALID_DESIGN_VERDICT_LABELS)},
            )
        if verdict_in_design == "needs_revision":
            ck = f"design_arch_self__phase_{phase}"
            state["counters"][ck] += 1
            if state["counters"][ck] > caps.get("design_arch_self", 2):
                return _escalate(run_dir, state, "design_arch_self_cap", stage, phase)
            write_feedback(stage_dir(run_dir, stage, phase), "design-self",
                           "architect-reviewer requested revision; see design.md issues")
            # Clear the just-failed design.md so a partial overwrite on the next
            # iteration cannot leave stale `verdict: pass` front matter visible
            # to this routing block. Same rationale as _backtrack_to step 2.
            # feedback.md is not in STAGE_OWNED_PATTERNS and survives.
            clear_stage_outputs(run_dir, ["design"], phase)
            state["stage_outputs"].pop(stage_key("design", phase), None)
            save_state(run_dir, state)
            emit_result("loopback", to="design", phase=phase, count=state["counters"][ck])
            return 0

    # sanity-test: gate passed/failed
    if stage == "sanity-test":
        gate_path = run_dir / f"phase-{phase}" / "gates" / "sanity.json"
        if not gate_path.exists():
            return _escalate(run_dir, state, "sanity_gate_missing", stage, phase)
        gd = json.loads(gate_path.read_text(encoding="utf-8"))
        if not gate_is_passing(gd):
            ck = f"sanity__phase_{phase}"
            state["counters"][ck] += 1
            if state["counters"][ck] > caps.get("sanity_loop", 2):
                return _escalate(run_dir, state, "sanity_cap", stage, phase)
            if gd.get("status") == "skipped_fail" or gd.get("skipped"):
                fb = (
                    f"sanity gate skipped — {gd.get('skip_reason')}. "
                    f"Add executable sanity tests under tests/sanity/."
                )
            else:
                fb = (
                    f"sanity test failed (exit_code={gd.get('exit_code')}). "
                    f"see {gate_path} and stdout/stderr siblings."
                )
            _backtrack_to(run_dir, state, "design", phase, source="sanity-fail", body=fb)
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

    # Run termination: delivery success means the whole run is done.
    if stage == "delivery":
        if state.get("status") == "aborted":
            final_status = "aborted"
        elif state.get("escalation_triggers"):
            final_status = "escalated_recovered"
            state["status"] = "done"
        else:
            final_status = "done"
            state["status"] = "done"
        _append_run_index(state, final_status)
        save_state(run_dir, state)
        emit_result("pass", stage=stage, phase=phase, next=None, final_status=final_status)
        return 0

    # Default: pass
    state["status"] = "running"
    save_state(run_dir, state)
    emit_result("pass", stage=stage, phase=phase, next=_default_next_stage(stage, phase, state))
    return 0


def _handle_resume(stage: str, run_dir: Path, phase: int | None, state: dict, caps: dict) -> int | None:
    """Consume state.user_input after a prior awaiting_user gate, OR re-run
    the stage on `continue` from an escalation.

    Two distinct entry conditions both arrive here via `--resume`:

      A. state.status == "awaiting_user" — main session merged user_input
         (decision: approve | revise | reject) and is asking us to consume
         it. Empty/unknown decision is fail-closed (a main-session bug must
         not silently bypass an intervention gate).

      B. state.status == "escalated" — user wrote escalation.decision.md
         with `decision: continue`; main session calls --resume without
         populating state.user_input. We re-run the stage with cap counters
         intact (SKILL.md §3: "cap 카운터는 그대로. cap 이 다시 초과되면
         또 escalate"). Falls through to caller's re-run path.

    Returns:
      - 0  on approve (emits pass + route to next stage)
      - 3  on reject / unknown decision / cap exceeded / missing input
      - None on revise OR escalation-continue (caller falls through and re-runs)
    """
    user_input = state.get("user_input") or {}
    decision = (user_input.get("decision") or "").strip().lower()
    prior_status = state.get("status")

    # Always consume the stale awaiting_user / escalation state — even if input
    # is empty. After this _handle_resume call returns, the caller resets
    # state.status to "running" via run_stage's normal flow.
    state["awaiting_input_schema"] = None
    state["user_input"] = {}

    # ── Path B: continue from escalation ────────────────────────────────
    # No decision in user_input, status was "escalated". Re-run the stage
    # without resetting cap counters.
    if not decision and prior_status == "escalated":
        state["status"] = "running"
        save_state(run_dir, state)
        return None  # fall through to re-run

    # ── Path A: awaiting_user resume ────────────────────────────────────
    # Empty decision here means the main session forgot to merge user_input
    # before calling --resume. Silent-passing would bypass the intervention
    # gate without explicit consent — fail-closed.
    if not decision:
        return _escalate(
            run_dir, state, "missing_user_decision", stage, phase,
            extra={"prior_status": prior_status,
                   "hint": "main session must merge state.user_input.decision before --resume"},
        )

    # Reject ambiguous "approve + structured-revise-keys" (e.g.,
    # `decision: approve` together with `add_requirements: [...]`). The
    # structured keys are only honored on revise; silently dropping them
    # would lose the user's intent. Surface it as an escalation so the
    # user can re-submit with a clear decision.
    if decision == "approve":
        revise_only = _REVISE_ONLY_KEYS.get(stage, set())
        offending = sorted(k for k in user_input if k in revise_only)
        if offending:
            return _escalate(
                run_dir, state, "ambiguous_user_input", stage, phase,
                extra={"decision": "approve", "revise_only_keys_present": offending,
                       "hint": "remove these keys, or change decision to 'revise'"},
            )

    if decision == "approve":
        # Run-level stages share run_dir as their stage_dir. If the user
        # revised once and then approved, feedback.md from the revise round
        # would still be visible to the *next* run-level stage. Clear it.
        # Phase-level stages legitimately share feedback within the phase dir
        # across stages and loops, so leave those alone.
        if stage in RUN_LEVEL_STAGES:
            (stage_dir(run_dir, stage, phase) / "feedback.md").unlink(missing_ok=True)
        state["status"] = "running"
        save_state(run_dir, state)
        emit_result("pass", stage=stage, phase=phase, next=_default_next_stage(stage, phase, state))
        return 0

    if decision == "reject":
        return _escalate(
            run_dir, state, "user_rejected", stage, phase,
            extra={"feedback": user_input.get("feedback")},
        )

    if decision == "revise":
        keys = _revise_counter_keys(stage, phase)
        if keys is not None:
            counter_key, cap_key = keys
            state["counters"].setdefault(counter_key, 0)
            state["counters"][counter_key] += 1
            cap = int(caps.get(cap_key, 2))
            if state["counters"][counter_key] > cap:
                return _escalate(
                    run_dir, state, f"{cap_key}_cap", stage, phase,
                    extra={"count": state["counters"][counter_key], "cap": cap},
                )
        # Reset the in-stage retry counter for this stage. A user revise is
        # a fresh attempt at the stage from the user's perspective; without
        # this, prior architect-reviewer self-loops in the same phase would
        # pre-burn the cap and force an escalation on the very next pass.
        # Mirrors the reset logic in _backtrack_to (which clears these on
        # any backtrack into this stage) — keeping the two paths symmetric.
        if phase is not None:
            counter_base = IN_STAGE_RETRY_COUNTERS.get(stage)
            if counter_base:
                state["counters"][f"{counter_base}__phase_{phase}"] = 0
        sd = stage_dir(run_dir, stage, phase)
        write_feedback(sd, f"{stage}-user-revise", _render_user_feedback(user_input))
        # Clear stale outputs so the LLM regenerates them this round.
        # Use STAGE_OWNED_PATTERNS via clear_stage_outputs for parity with
        # the backtrack path — STAGE_REQUIRED_AUX_OUTPUTS is only populated
        # for code-review, so the previous primary+aux loop missed e.g.
        # pr-create's pr-url.txt. feedback.md is not in any owned-patterns
        # set, so the just-written user-revise feedback survives.
        clear_stage_outputs(run_dir, [stage], phase)
        state["stage_outputs"].pop(stage_key(stage, phase), None)
        state["status"] = "running"
        save_state(run_dir, state)
        return None  # fall through to re-run

    return _escalate(
        run_dir, state, "unknown_user_decision", stage, phase,
        extra={"decision": decision, "valid": ["approve", "revise", "reject"]},
    )


_REVISE_ONLY_KEYS = {
    "requirements": {"add_requirements", "remove_ids"},
    "phase-split":  {"merge_phases", "split_phase", "reorder"},
}


def _intervention_schema_for(stage: str) -> dict:
    # Note: structured keys other than `decision` and `feedback` are only
    # consumed when `decision == revise`. Submitting them with `approve` is
    # rejected by _handle_resume to prevent silent data loss.
    if stage == "planning":
        return {"decision": "approve|reject|revise", "feedback": "string"}
    if stage == "requirements":
        return {"decision": "approve|reject|revise", "feedback": "string",
                "add_requirements": "list (revise only)",
                "remove_ids": "list (revise only)"}
    if stage == "phase-split":
        return {"decision": "approve|reject|revise", "feedback": "string",
                "merge_phases": "list[list] (revise only)",
                "split_phase": "int (revise only)",
                "reorder": "list[int] (revise only)"}
    if stage == "design":
        return {"decision": "approve|revise", "feedback": "string"}
    if stage == "pr-create":
        return {"decision": "approve|revise", "feedback": "string"}
    return {}


def _default_next_stage(stage: str, phase: int | None, state: dict | None = None) -> str | None:
    """Next stage in the canonical flow, or None when the phase/run ends.

    `None` is the documented signal (SKILL.md §1: "없으면 다음 phase 또는
    delivery") for the main session to advance the phase counter or invoke
    delivery. Earlier revisions returned a sentinel string
    `"next-phase-or-delivery"` which is not a valid stage name and silently
    broke the contract — main session would interpret it as a stage to call.
    """
    seq_run = ["planning", "requirements", "phase-split"]
    seq_phase = ["design", "branch-create", "implement", "lint-test", "code-review",
                 "sanity-test", "document", "pr-create", "pr-publish"]
    if stage in seq_run:
        idx = seq_run.index(stage)
        if idx + 1 < len(seq_run):
            return seq_run[idx + 1]
        return "design"  # next is first phase's design
    if stage in seq_phase:
        idx = seq_phase.index(stage)
        if idx + 1 < len(seq_phase):
            nxt = seq_phase[idx + 1]
            # Skip pr-publish entirely in manual pr_mode — there's nothing to
            # push and no PR to open. The user does it themselves from pr.md.
            if nxt == "pr-publish":
                pr_mode = ((state or {}).get("interview_spec") or {}).get("pr_mode", "auto")
                if str(pr_mode).lower() == "manual":
                    return None
            return nxt
        return None
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
            value = line.split(":", 1)[1]
            # Strip YAML trailing comment. Prompt examples sometimes carry
            # ``verdict: pass            # pass | needs_revision`` and an LLM
            # may keep the comment when updating only the value, which would
            # otherwise fail enum membership checks downstream.
            if "#" in value:
                value = value.split("#", 1)[0]
            value = value.strip()
            # Strip a single matched pair of YAML quotes. ``verdict: "pass"``
            # is legal YAML; without this the enum check sees `'"pass"'` and
            # fails, escalating verdict_invalid for what is semantically valid.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            return value
    return None


def _render_issues(verdict: dict, key: str) -> str:
    """Render an `issues_*` array from verdict.json as readable Markdown.

    Each issue is expected to be a dict with location/description/suggestion
    (the schema the code-review prompt teaches), but plain-string issues are
    handled too for forward compatibility.
    """
    issues = verdict.get(key, []) or []
    if not issues:
        return f"# {key}\n\n(none)"
    lines = [f"# {key}"]
    for i, iss in enumerate(issues, 1):
        if isinstance(iss, dict):
            location = iss.get("location") or "(location unspecified)"
            description = (iss.get("description") or "").strip()
            suggestion = (iss.get("suggestion") or "").strip()
            lines.append(f"\n## {i}. `{location}`")
            if description:
                lines.append(f"\n**문제**: {description}")
            if suggestion:
                lines.append(f"\n**제안**: {suggestion}")
        else:
            lines.append(f"\n## {i}. {iss}")
    summary = (verdict.get("summary") or "").strip()
    if summary:
        lines.append(f"\n---\n\n## 종합 요약\n\n{summary}")
    return "\n".join(lines)


def _backtrack_to(run_dir: Path, state: dict, target_stage: str, phase: int | None, source: str, body: str) -> None:
    # 1. Decide which stages to clear: target itself + everything after.
    #    Clearing the target's owned outputs makes backtrack consistent with
    #    the user-revise path (which also wipes the stage's outputs before
    #    re-running) and prevents stale primary outputs (e.g., a previous
    #    `verdict: pass` design.md) from short-circuiting post-stage routing
    #    if the LLM fails to fully overwrite them.
    seq_phase = ["design", "branch-create", "implement", "lint-test", "code-review",
                 "sanity-test", "document", "pr-create", "pr-publish"]
    if target_stage not in seq_phase:
        return
    idx = seq_phase.index(target_stage)
    downstream = seq_phase[idx + 1:]
    to_clear = [target_stage, *downstream]

    # 2. Clear LLM artifacts AND orchestrator-owned artifacts.
    #    feedback.md is shared across stages within a phase dir and is NOT in
    #    any STAGE_OWNED_PATTERNS, so it survives this — we then append to it
    #    in step 4 below.
    #    EXCEPTION — branch-create's branch.txt: a backtrack to design within
    #    the same phase must reuse the same git branch (each phase = one PR;
    #    new commits land on the same ref). Wiping branch.txt would force
    #    branch-create to allocate a new NN suffix on re-entry and leave the
    #    prior branch dangling. The prompt's "Backtrack-safe reuse" path
    #    requires branch.txt to survive. So we drop branch-create from the
    #    file-clear list (its only owned pattern is branch.txt) but still
    #    treat it as "in flight" for stage_outputs/counter resets below.
    to_clear_files = [s for s in to_clear if s != "branch-create"]
    clear_stage_outputs(run_dir, to_clear_files, phase)
    # gates dir is orchestrator-owned and tied to the phase, not a stage —
    # later stages rewrite their owned gate files as needed.

    # 3. Pop last verdict only when the backtrack was triggered by a
    #    verdict-appending stage. code-review is the only such stage today;
    #    lint-test cap and sanity-fail backtrack without appending, so popping
    #    here would clobber an unrelated prior verdict (e.g., the previous
    #    phase's code-review entry).
    if source.startswith("code-review-") and state["verdict_history"]:
        state["verdict_history"].pop()

    # 4. Write feedback for the resume target stage
    write_feedback(stage_dir(run_dir, target_stage, phase), source, body)

    # 5. Reset stage_outputs entries for stages whose files we wiped.
    #    branch-create's stage_outputs entry stays because branch.txt is
    #    unchanged and still points to the live branch.
    for s in to_clear_files:
        state["stage_outputs"].pop(stage_key(s, phase), None)

    # 6. Reset in-stage retry counters for the target stage and all cleared
    #    stages. These count "in-stage attempts" — re-entering means a fresh
    #    attempt count, otherwise a phase that already self-corrected once
    #    would immediately escalate on the next pass through the same stage.
    #    Verdict-type counters (code_review_minor/major) and user-revise
    #    counters intentionally stay cumulative within the phase.
    if phase is not None:
        for s in to_clear:
            counter_base = IN_STAGE_RETRY_COUNTERS.get(s)
            if counter_base:
                state["counters"][f"{counter_base}__phase_{phase}"] = 0


_VERDICT_DERIVED_ESCALATIONS = {
    "code_review_critical",
    "code_review_minor_cap",
    "code_review_major_cap",
}


def _escalate(run_dir: Path, state: dict, trigger: str, stage: str, phase: int | None, extra: dict | None = None) -> int:
    # Pop verdict_history when the escalation was triggered by a verdict that
    # didn't get to complete its loop cycle. _backtrack_to already pops on
    # successful backtrack (minor/major); without this, critical and cap-hit
    # paths would leave the tail entry behind and skew per-phase analytics
    # (the entry would look like a "settled outcome" even though it aborted).
    if trigger in _VERDICT_DERIVED_ESCALATIONS and state.get("verdict_history"):
        state["verdict_history"].pop()
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
