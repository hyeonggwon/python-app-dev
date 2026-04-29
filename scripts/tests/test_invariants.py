"""Cross-file invariant tests for the python-app-dev harness.

What it checks:

  1.  All canonical stages are registered in every orchestrator table.
  2.  Each canonical prompt under `scripts/prompts/<stage>.md` exists.
  3.  Bash commands quoted in prompts (`uv ...`, `git ...`, `gh ...`,
      `pytest ...`, `ruff ...`, `mypy ...`) are covered by that stage's
      `Bash(<prefix>:*)` patterns in STAGE_TOOLS. Shell builtins and
      negative-example commands (e.g. `git push --force` shown as forbidden)
      are exempt.
  4.  Every `{token}` in a prompt is in materialize_prompt's replacement map.
  5.  Multi-output stages: prompts mention every file in
      STAGE_REQUIRED_AUX_OUTPUTS.
  6.  code-review prompt teaches the exact verdict labels and loop_target
      values the orchestrator routes on (VALID_VERDICT_LABELS, VALID_LOOP_TARGETS,
      VERDICT_TO_LOOP).
  7.  design prompt teaches the exact verdict labels route() enforces on
      design.md front-matter (VALID_DESIGN_VERDICT_LABELS).
  8.  Stage directory names in STAGE_DIRS match the kebab-case stage keys.
  9.  Marker line strings in STAGE_MARKER appear in their respective prompts.
  10. Authority paths cited in CLAUDE.md and stage prompts (`docs/...`)
      exist on disk.
  11. No absolute paths (`/home/...`, `/Users/...`, `/opt/...`) in any
      canonical file under `docs/` or `scripts/prompts/`.
  12. `_intervention_schema_for` is total over the user-toggleable stages.
  13. Gate status enum: `run_gate.GATE_STATUS_VALUES` partitions exactly into
      `orchestrate.GATE_PASSING_STATUSES` ∪ `GATE_FAILING_STATUSES`.
  14. `route()` consults gate pass/fail through `gate_is_passing()`, never
      by reading the legacy `passed` bool directly.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR = ROOT / "scripts" / "orchestrate.py"
RUN_GATE = ROOT / "scripts" / "run_gate.py"
PROMPTS_DIR = ROOT / "scripts" / "prompts"
DOCS_DIR = ROOT / "docs"
CLAUDE_MD = ROOT / "CLAUDE.md"

NEGATIVE_EXAMPLE_RE = re.compile(
    r"(?:Never|Do not|don't|금지|금지한다|하지 (?:마라|말 것))[^\n]*?`([^`]+)`",
    re.IGNORECASE,
)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

GRANTABLE_BIN_PREFIXES = {"uv", "ruff", "mypy", "pytest", "git", "gh"}


# ---------------------------------------------------------------------------
# Helpers (shared across tests)
# ---------------------------------------------------------------------------

def _module_constants(path: Path, names: set[str]) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id in names:
                try:
                    out[tgt.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return out


def _strip_negative_examples(text: str) -> str:
    forbidden = [m.group(1) for m in NEGATIVE_EXAMPLE_RE.finditer(text)]
    out = text
    for seg in forbidden:
        out = out.replace(f"`{seg}`", "")
    return out


def _extract_inline_commands(text: str) -> list[str]:
    """Return inline-code segments that look like grantable shell commands."""
    cmds: list[str] = []
    in_test_plan_block = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("## Test plan") or stripped.startswith("## test plan"):
            in_test_plan_block = True
            continue
        if in_test_plan_block:
            if stripped.startswith("## ") or stripped.startswith("Refs:"):
                in_test_plan_block = False
        if in_test_plan_block:
            continue
        if "[ ]" in line or "[x]" in line.lower():
            continue
        for m in INLINE_CODE_RE.finditer(line):
            snippet = m.group(1).strip()
            if not snippet:
                continue
            tokens = snippet.split()
            if len(tokens) < 2:
                continue
            first = tokens[0].lstrip("$")
            if first in GRANTABLE_BIN_PREFIXES:
                cmds.append(snippet)
    return cmds


def _stage_tool_subprefixes(tool_entries: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for entry in tool_entries:
        if not entry.startswith("Bash(") or not entry.endswith(")"):
            continue
        inner = entry[5:-1]
        if ":" in inner:
            head, _ = inner.rsplit(":", 1)
            parts = head.split(maxsplit=1)
            binname = parts[0]
            sub = parts[1] if len(parts) > 1 else "*"
        else:
            parts = inner.split(maxsplit=1)
            binname = parts[0]
            sub = (parts[1] + "_exact") if len(parts) > 1 else "_exact"
        out.setdefault(binname, set()).add(sub)
    return out


def _command_is_covered(cmd: str, prefixes: dict[str, set[str]]) -> bool:
    parts = cmd.split()
    if not parts:
        return True
    binname = parts[0].lstrip("$")
    if binname not in prefixes:
        return False
    allowed = prefixes[binname]
    if "*" in allowed:
        return True
    sub = parts[1] if len(parts) > 1 else ""
    if sub in allowed or (sub + "_exact") in allowed:
        return True
    if len(parts) > 2 and (parts[1] + " " + parts[2]) in allowed:
        return True
    return False


# ---------------------------------------------------------------------------
# Parametrize sources — defined at import time so collection sees per-stage tests
# ---------------------------------------------------------------------------

def _stages() -> list[str]:
    """Read STAGE_DIRS at collection time so parametrize ids show stage names."""
    consts = _module_constants(ORCHESTRATOR, {"STAGE_DIRS"})
    return sorted(consts.get("STAGE_DIRS") or [])


def _aux_pairs() -> list[tuple[str, str]]:
    consts = _module_constants(ORCHESTRATOR, {"STAGE_REQUIRED_AUX_OUTPUTS"})
    out = []
    for stage, aux_list in (consts.get("STAGE_REQUIRED_AUX_OUTPUTS") or {}).items():
        for aux in aux_list:
            out.append((stage, aux))
    return sorted(out)


# ---------------------------------------------------------------------------
# 1. Stage table consistency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table_name", [
    "STAGE_TOOLS", "STAGE_PRIMARY_OUTPUT", "STAGE_MARKER", "STAGE_OWNED_PATTERNS",
])
def test_stage_table_keys_match_stage_dirs(orch, table_name):
    keys = set(orch["STAGE_DIRS"])
    diff = keys.symmetric_difference(set(orch[table_name]))
    assert not diff, f"{table_name} differs from STAGE_DIRS: {sorted(diff)}"


@pytest.mark.parametrize("stage", _stages())
def test_primary_output_in_owned_patterns(orch, stage):
    primary = orch["STAGE_PRIMARY_OUTPUT"][stage]
    patterns = orch["STAGE_OWNED_PATTERNS"][stage]
    assert primary in patterns, (
        f"[{stage}] primary output `{primary}` not in STAGE_OWNED_PATTERNS — "
        f"backtrack will not clear it"
    )


@pytest.mark.parametrize("stage,aux", _aux_pairs())
def test_required_aux_in_owned_patterns(orch, stage, aux):
    patterns = orch["STAGE_OWNED_PATTERNS"][stage]
    assert aux in patterns, (
        f"[{stage}] required aux `{aux}` not in STAGE_OWNED_PATTERNS — "
        f"backtrack will not clear it"
    )


def test_run_level_phase_level_partition(orch):
    rl = set(orch["RUN_LEVEL_STAGES"])
    pl = set(orch["PHASE_LEVEL_STAGES"])
    keys = set(orch["STAGE_DIRS"])
    assert not (rl & pl), f"stage in both RUN_LEVEL and PHASE_LEVEL: {sorted(rl & pl)}"
    assert (rl | pl) == keys, (
        f"RUN_LEVEL | PHASE_LEVEL != STAGE_DIRS: "
        f"missing={sorted(keys - (rl|pl))}, extra={sorted((rl|pl) - keys)}"
    )


# ---------------------------------------------------------------------------
# 2. Canonical prompts exist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", _stages())
def test_canonical_prompt_exists(stage):
    p = PROMPTS_DIR / f"{stage}.md"
    assert p.exists(), f"missing canonical prompt: scripts/prompts/{stage}.md"


# ---------------------------------------------------------------------------
# 3. Bash commands covered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", _stages())
def test_bash_commands_covered(orch, stage):
    prompt = PROMPTS_DIR / f"{stage}.md"
    if not prompt.exists():
        pytest.skip(f"prompt missing for {stage}")
    text = _strip_negative_examples(prompt.read_text(encoding="utf-8"))
    cmds = _extract_inline_commands(text)
    if not cmds:
        return
    prefixes = _stage_tool_subprefixes(orch["STAGE_TOOLS"][stage])
    uncovered = [c for c in cmds if not _command_is_covered(c, prefixes)]
    assert not uncovered, f"[{stage}] commands not granted by STAGE_TOOLS: {uncovered}"


# ---------------------------------------------------------------------------
# 4. Tokens covered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", _stages())
def test_tokens_covered(replacements, stage):
    assert replacements, "could not extract replacements dict from materialize_prompt"
    prompt = PROMPTS_DIR / f"{stage}.md"
    if not prompt.exists():
        pytest.skip(f"prompt missing for {stage}")
    text = prompt.read_text(encoding="utf-8")
    bad: list[str] = []
    for m in TOKEN_RE.finditer(text):
        tok = m.group(1)
        if len(tok) == 1 and tok != "N":
            continue
        if tok in replacements:
            continue
        if tok.lower() in {"id", "int", "list", "url"}:
            continue
        bad.append(tok)
    assert not bad, f"[{stage}] unknown {{tokens}}: {bad}"


# ---------------------------------------------------------------------------
# 5. Aux outputs mentioned in prompts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage,aux", _aux_pairs())
def test_aux_output_mentioned(stage, aux):
    prompt = PROMPTS_DIR / f"{stage}.md"
    if not prompt.exists():
        pytest.skip(f"prompt missing for {stage}")
    text = prompt.read_text(encoding="utf-8")
    assert aux in text, (
        f"[{stage}] aux output `{aux}` declared in STAGE_REQUIRED_AUX_OUTPUTS "
        f"but not mentioned in prompt body"
    )


# ---------------------------------------------------------------------------
# 6. code-review verdict enum agrees with prompt
# ---------------------------------------------------------------------------

def test_code_review_prompt_teaches_verdict_labels(orch):
    prompt = PROMPTS_DIR / "code-review.md"
    if not prompt.exists():
        pytest.skip("code-review.md missing")
    text = prompt.read_text(encoding="utf-8")
    missing = [lbl for lbl in orch["VALID_VERDICT_LABELS"] if lbl not in text]
    assert not missing, f"code-review prompt missing verdict labels: {missing}"


def test_code_review_prompt_teaches_loop_targets(orch):
    prompt = PROMPTS_DIR / "code-review.md"
    if not prompt.exists():
        pytest.skip("code-review.md missing")
    text = prompt.read_text(encoding="utf-8")
    missing = [t for t in orch["VALID_LOOP_TARGETS"] if t not in text]
    assert not missing, f"code-review prompt missing loop_targets: {missing}"


def test_code_review_prompt_pairs_verdict_with_loop_target(orch):
    prompt = PROMPTS_DIR / "code-review.md"
    if not prompt.exists():
        pytest.skip("code-review.md missing")
    text = prompt.read_text(encoding="utf-8")
    bad: list[str] = []
    for label, allowed in orch["VERDICT_TO_LOOP"].items():
        only = next(iter(allowed))
        for line in text.splitlines():
            if "|" in line and f"`{label}`" in line:
                if f"`{only}`" not in line:
                    bad.append(f"{label} should pair with {only}")
                break
    assert not bad, f"verdict/loop_target pairing mismatches: {bad}"


# ---------------------------------------------------------------------------
# 7. design verdict enum
# ---------------------------------------------------------------------------

def test_design_prompt_teaches_front_matter_verdict_labels(orch):
    prompt = PROMPTS_DIR / "design.md"
    if not prompt.exists():
        pytest.skip("design.md missing")
    labels = orch.get("VALID_DESIGN_VERDICT_LABELS")
    assert labels, "VALID_DESIGN_VERDICT_LABELS not parsed"
    text = prompt.read_text(encoding="utf-8")
    missing = [lbl for lbl in labels if lbl not in text]
    assert not missing, f"design prompt missing front-matter verdict labels: {missing}"


# ---------------------------------------------------------------------------
# 8. Stage dir naming
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", _stages())
def test_stage_dir_naming(stage):
    assert stage == stage.lower(), f"stage `{stage}` not lowercase kebab"
    assert "_" not in stage, f"stage `{stage}` uses underscore (must be kebab-case)"


# ---------------------------------------------------------------------------
# 9. Marker in prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", _stages())
def test_marker_in_prompt(orch, stage):
    marker = orch["STAGE_MARKER"][stage]
    prompt = PROMPTS_DIR / f"{stage}.md"
    if not prompt.exists():
        pytest.skip(f"prompt missing for {stage}")
    text = prompt.read_text(encoding="utf-8")
    assert marker in text, (
        f"[{stage}] prompt does not contain marker line `{marker}` "
        f"(orchestrator will reject the run as missing_marker)"
    )


# ---------------------------------------------------------------------------
# 10. Authority files exist
# ---------------------------------------------------------------------------

_AUTHORITY_PATHS = [
    DOCS_DIR / "task-spec.md",
    DOCS_DIR / "tacit-knowledge.md",
    DOCS_DIR / "interview-guide.md",
    DOCS_DIR / "stages.md",
    CLAUDE_MD,
    ROOT / "scripts" / "config.yaml",
    ROOT / "scripts" / "init_run.py",
    ROOT / "scripts" / "orchestrate.py",
    ROOT / "scripts" / "run_gate.py",
    ROOT / "scripts" / "detect_toolchain.py",
]


@pytest.mark.parametrize("path", _AUTHORITY_PATHS, ids=lambda p: str(p.relative_to(ROOT)))
def test_authority_path_exists(path):
    assert path.exists(), f"authority file missing: {path.relative_to(ROOT)}"


# ---------------------------------------------------------------------------
# 11. No absolute paths in canonical files
# ---------------------------------------------------------------------------

_ABSOLUTE_PATH_RE = re.compile(r"(?:/home/|/Users/|/opt/|/usr/local/)[A-Za-z0-9_\-./]+")


def _no_absolute_path_targets() -> list[Path]:
    return list(DOCS_DIR.glob("*.md")) + list(PROMPTS_DIR.glob("*.md")) + [CLAUDE_MD]


@pytest.mark.parametrize(
    "path",
    _no_absolute_path_targets(),
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_no_absolute_paths(path):
    text = path.read_text(encoding="utf-8")
    bad: list[str] = []
    for m in _ABSOLUTE_PATH_RE.finditer(text):
        full = m.group(0)
        if full.endswith("/...") or "..." in full:
            continue
        if text[: m.start()].count("```") % 2 == 1:
            # inside fenced code block — exempt (often shell examples)
            continue
        bad.append(full)
    assert not bad, (
        f"absolute paths in {path.relative_to(ROOT)}: {bad} — "
        f"use {{{{HARNESS_ROOT}}}} or relative path"
    )


# ---------------------------------------------------------------------------
# 12. Intervention schema branch coverage
# ---------------------------------------------------------------------------

def test_intervention_schema_total_over_user_toggleable_stages():
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_intervention_schema_for"),
        None,
    )
    assert fn is not None, "_intervention_schema_for not found in orchestrator"
    handled: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            comp = node.comparators[0]
            if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                handled.add(comp.value)
    expected = {"planning", "requirements", "phase-split", "design", "pr-create"}
    missing = expected - handled
    assert not missing, f"_intervention_schema_for missing branches for: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 13. Gate status enum consistency (run_gate.py ↔ orchestrate.py)
# ---------------------------------------------------------------------------

def test_gate_status_enum_partitions_into_passing_and_failing():
    """The four-value status enum must agree across the two files. If a status
    name appears in only one set, gate_is_passing() falls through to the legacy
    `passed` bool — re-introducing the dual-truth bug the enum fixed."""
    rg = _module_constants(RUN_GATE, {"GATE_STATUS_VALUES"})
    orch_consts = _module_constants(
        ORCHESTRATOR, {"GATE_PASSING_STATUSES", "GATE_FAILING_STATUSES"}
    )
    enum_values = rg.get("GATE_STATUS_VALUES")
    passing = orch_consts.get("GATE_PASSING_STATUSES")
    failing = orch_consts.get("GATE_FAILING_STATUSES")
    assert enum_values is not None, "run_gate.py: GATE_STATUS_VALUES not parsed"
    assert passing is not None and failing is not None, (
        "orchestrate.py: GATE_PASSING_STATUSES / GATE_FAILING_STATUSES not parsed"
    )
    enum_set = set(enum_values)
    classified = set(passing) | set(failing)
    extra_classified = classified - enum_set
    unclassified = enum_set - classified
    overlap = set(passing) & set(failing)
    assert not extra_classified, (
        f"orchestrate.py classifies statuses not in run_gate.GATE_STATUS_VALUES: "
        f"{sorted(extra_classified)}"
    )
    assert not unclassified, (
        f"run_gate.GATE_STATUS_VALUES contains statuses neither passing nor failing: "
        f"{sorted(unclassified)}"
    )
    assert not overlap, (
        f"orchestrate.py: status classified as both passing and failing: {sorted(overlap)}"
    )


# ---------------------------------------------------------------------------
# 14. route() must use gate_is_passing(), never raw .get("passed")
# ---------------------------------------------------------------------------

def test_route_does_not_read_passed_bool_directly():
    """The legacy `gd.get("passed")` check is kept only as an internal fallback
    inside gate_is_passing(); production routing branches must go through the
    helper so that `skipped_fail` is never confused with `passed=True`."""
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    route_fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "route"),
        None,
    )
    assert route_fn is not None, "orchestrate.py: route() not found"
    bad: list[int] = []
    for node in ast.walk(route_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and arg.value == "passed":
                    bad.append(node.lineno)
    assert not bad, (
        f"orchestrate.route() reads `.get('passed')` directly at line(s) {bad} — "
        f"must use gate_is_passing() so skipped_fail is treated as a fail"
    )


# ---------------------------------------------------------------------------
# 15. Doc/code consistency (pins fixes from the harness self-review)
# ---------------------------------------------------------------------------

def _docs_and_prompts_files() -> list[Path]:
    return list(DOCS_DIR.glob("*.md")) + list(PROMPTS_DIR.glob("*.md")) + [CLAUDE_MD]


@pytest.mark.parametrize(
    "path",
    _docs_and_prompts_files(),
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_no_legacy_sentinel_in_docs(path):
    """`_default_next_stage` returns None for `pr-create` in pr_mode=manual.
    No doc may describe this as the legacy sentinel string `next-phase-or-delivery`
    — that name is not a real stage and would silently break the documented
    `next` contract if any reader implemented against it."""
    text = path.read_text(encoding="utf-8")
    assert "next-phase-or-delivery" not in text, (
        f"{path.relative_to(ROOT)} still references legacy sentinel "
        f"`next-phase-or-delivery`; orchestrator returns None — describe "
        f"that explicitly"
    )


def test_tacit_knowledge_verdict_section_points_to_verdict_json():
    """The orchestrator parses `phase-{N}/verdict.json` for code-review verdict;
    `review.md` is the human-readable companion. Earlier doc claimed verdict
    lived in review.md, which would mislead anyone tracing the routing path."""
    p = DOCS_DIR / "tacit-knowledge.md"
    text = p.read_text(encoding="utf-8")
    section = re.search(
        r"## 6\..*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL,
    )
    assert section is not None, "tacit-knowledge.md §6 not found"
    body = section.group(0)
    assert "verdict.json" in body, (
        "tacit-knowledge.md §6 must reference verdict.json as the authoritative "
        "verdict source — orchestrator parses verdict.json directly"
    )


def test_tacit_knowledge_gate_json_example_includes_status_field():
    """The gate JSON schema example in §2 must show `status` — that's the
    primary routing field (gate_is_passing reads it first). Earlier example
    showed only `passed: true`, which would steer new contributors toward the
    legacy single-bool model that conflated skipped_fail with success."""
    p = DOCS_DIR / "tacit-knowledge.md"
    text = p.read_text(encoding="utf-8")
    fences = re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
    assert fences, "no ```json fenced block found in tacit-knowledge.md"
    gate_examples = [b for b in fences if '"name"' in b and '"exit_code"' in b]
    assert gate_examples, "no gate-result-shaped JSON example found in §2"
    for body in gate_examples:
        assert '"status"' in body, (
            "gate JSON example must include the `status` field "
            "(passed|failed|skipped_ok|skipped_fail) — it is the primary "
            "routing signal, not the legacy `passed` bool"
        )


def test_lint_test_design_loop_cap_in_config():
    """Pin the new cumulative cap added to fix the missing
    lint-test → design loopback bound. If this key disappears from
    config.yaml, the code falls back to the hardcoded default and the docs
    (tacit-knowledge §4-2 / stages.md P4) silently lose their config tie-in."""
    cfg_path = ROOT / "scripts" / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    assert re.search(r"^\s*lint_test_design_loop\s*:\s*\d+", text, re.MULTILINE), (
        "scripts/config.yaml must declare `lint_test_design_loop` under caps; "
        "this is the cumulative cap for lint-test → design backtracks within "
        "a phase (cap key referenced by run_lint_test_loop)"
    )


def test_lint_test_design_counter_initialized_in_ensure_phase_counters():
    """ensure_phase_counters must seed `lint_test_design` so the increment
    line in run_lint_test_loop never KeyErrors on first cap-hit."""
    consts = _module_constants(ORCHESTRATOR, set())  # parse all asgn
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "ensure_phase_counters"),
        None,
    )
    assert fn is not None, "ensure_phase_counters not found"
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            keys.add(node.value)
    assert "lint_test_design" in keys, (
        "ensure_phase_counters must include `lint_test_design` so the "
        "cumulative cap counter is seeded on phase entry"
    )
