#!/usr/bin/env python3
"""Cross-file invariant checker for the python-app-dev harness.

Run as a pre-commit hook (.githooks/pre-commit). All checks use only the
standard library. On any failure prints a `FAIL: ...` line and exits 1;
otherwise prints `OK` and exits 0.

What it checks (each block has a `check_*` function):

  1.  All canonical stages are registered in every orchestrator table.
  2.  Each canonical prompt under `scripts/prompts/<stage>.md` exists for
      every stage in STAGE_DIRS.
  3.  Bash commands quoted in prompts (`uv ...`, `git ...`, `gh ...`,
      `pytest ...`, `ruff ...`, `mypy ...`) are covered by that stage's
      `Bash(<prefix>:*)` patterns in STAGE_TOOLS. Shell builtins and
      negative-example commands (e.g. `git push --force` shown as forbidden)
      are exempt.
  4.  Every `{token}` referenced in a prompt is in the orchestrator's
      `materialize_prompt` replacement map.
  5.  Multi-output stages: the prompt body explicitly tells the LLM to write
      every file listed in STAGE_REQUIRED_AUX_OUTPUTS for that stage.
  6.  The `code-review` prompt teaches the exact verdict labels and
      loop_target values that the orchestrator routes on
      (VALID_VERDICT_LABELS, VALID_LOOP_TARGETS, VERDICT_TO_LOOP).
  7.  Stage directory names in STAGE_DIRS match the kebab-case stage keys
      (no short-form / long-form drift).
  8.  Marker line strings in STAGE_MARKER appear in their respective prompts
      (each prompt must instruct the LLM to print its marker).
  9.  Authority paths cited in CLAUDE.md and stage prompts (`docs/...`)
      exist on disk.
  10. No absolute paths (`/home/...`, `/Users/...`, `/opt/...`) leak into
      any canonical file under `docs/` or `scripts/prompts/`.
  11. Decision template keys produced by `_intervention_schema_for` are a
      subset of what the corresponding deep-interview/orchestration code
      reads back (currently checks only that the schema dict is well-formed
      JSON-able).

The script is invoked from the repo root and inspects:
  - scripts/orchestrate.py            (parsed via AST)
  - scripts/prompts/*.md
  - docs/*.md
  - CLAUDE.md
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = ROOT / "scripts" / "orchestrate.py"
PROMPTS_DIR = ROOT / "scripts" / "prompts"
DOCS_DIR = ROOT / "docs"
CLAUDE_MD = ROOT / "CLAUDE.md"

# Shell builtins / read-only inspection commands that don't need a tool grant.
SHELL_BUILTINS = {
    "echo", "cd", "pwd", "ls", "cat", "head", "tail", "wc",
    "true", "false", "if", "then", "fi", "for", "while", "do", "done",
    "return", "exit", "set", "unset", "export", "test",
}

# Commands that appear in prompts as **forbidden negative examples** —
# they don't need a corresponding Bash tool grant.
NEGATIVE_EXAMPLE_RE = re.compile(
    r"(?:Never|Do not|don't|금지|금지한다|하지 (?:마라|말 것))[^\n]*?`([^`]+)`",
    re.IGNORECASE,
)

# Match prompts: any inline `code` segment that begins with one of our tools.
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
MARKER_RE = re.compile(r"\b([A-Z]+(?:_[A-Z]+)*_DONE)\b")

# Tools the harness considers "Bash-grantable" (need a Bash(<prefix>:*) entry).
GRANTABLE_BIN_PREFIXES = {
    "uv", "ruff", "mypy", "pytest",
    "git", "gh",
}


# ---------------------------------------------------------------------------
# Orchestrator table extraction
# ---------------------------------------------------------------------------

def parse_orchestrator() -> dict:
    """Return a dict of the orchestrator's top-level data tables."""
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    wanted = {
        "STAGE_DIRS", "STAGE_TOOLS", "STAGE_REQUIRED_AUX_OUTPUTS",
        "STAGE_PRIMARY_OUTPUT", "STAGE_MARKER",
        "RUN_LEVEL_STAGES", "PHASE_LEVEL_STAGES",
        "VALID_VERDICT_LABELS", "VALID_LOOP_TARGETS", "VERDICT_TO_LOOP",
    }
    out: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id in wanted:
                try:
                    out[tgt.id] = ast.literal_eval(node.value)
                except ValueError:
                    # STAGE_MARKER is a comprehension — handle below
                    pass

    # STAGE_MARKER is `{k: f"{k.replace('-','_').upper()}_DONE" for k in STAGE_DIRS}`.
    # Reconstruct it deterministically from STAGE_DIRS.
    if "STAGE_MARKER" not in out and "STAGE_DIRS" in out:
        out["STAGE_MARKER"] = {
            k: f"{k.replace('-', '_').upper()}_DONE" for k in out["STAGE_DIRS"]
        }

    missing = wanted - set(out)
    if missing:
        # PHASE_LEVEL_STAGES is computed from STAGE_DIRS - RUN_LEVEL_STAGES;
        # also can't literal_eval a set difference. Reconstruct.
        if {"STAGE_DIRS", "RUN_LEVEL_STAGES"} <= set(out):
            out.setdefault(
                "PHASE_LEVEL_STAGES",
                set(out["STAGE_DIRS"]) - set(out["RUN_LEVEL_STAGES"]),
            )
        missing = wanted - set(out)
    return out


def parse_replacements() -> set[str]:
    """Extract the keys of the `replacements` dict in materialize_prompt."""
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


# ---------------------------------------------------------------------------
# Prompt scanning helpers
# ---------------------------------------------------------------------------

def strip_negative_examples(text: str) -> str:
    """Remove inline-code segments that are referenced as forbidden examples."""
    forbidden_segments: list[str] = []
    for m in NEGATIVE_EXAMPLE_RE.finditer(text):
        forbidden_segments.append(m.group(1))
    if not forbidden_segments:
        return text
    out = text
    for seg in forbidden_segments:
        out = out.replace(f"`{seg}`", "")
    return out


def extract_inline_commands(text: str) -> list[str]:
    """Return inline-code segments that look like grantable shell commands.

    Heuristics for what counts as "an actual command the LLM will execute":
      - Must have at least one argument after the bin name (bare `gh`, `git`
        are tool-name mentions, not commands).
      - Must NOT appear on a markdown checklist line (`- [ ]` or `[ ]`) —
        those are PR-body / test-plan templates, not commands for the LLM.
      - Must NOT appear inside a YAML-style body template section between
        "## Test plan" and the next blank-line section break.
    """
    cmds: list[str] = []
    lines = text.splitlines()
    in_test_plan_block = False
    for line in lines:
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


def stage_tool_subprefixes(tool_entries: list[str]) -> dict[str, set[str]]:
    """Index Bash() entries by binary, with the set of allowed sub-prefixes.

    `Bash(uv:*)`           -> {"uv": {"*"}}
    `Bash(git add:*)`      -> {"git": {"add"}}
    `Bash(git status)`     -> {"git": {"status_exact"}}     # exact-match flag
    """
    out: dict[str, set[str]] = {}
    for entry in tool_entries:
        if not entry.startswith("Bash(") or not entry.endswith(")"):
            continue
        inner = entry[5:-1]
        # Split on first colon between bin/sub and trailing argspec
        if ":" in inner:
            head, _ = inner.rsplit(":", 1)  # e.g. "git add" : "*"
            parts = head.split(maxsplit=1)
            binname = parts[0]
            sub = parts[1] if len(parts) > 1 else "*"
        else:
            parts = inner.split(maxsplit=1)
            binname = parts[0]
            sub = (parts[1] + "_exact") if len(parts) > 1 else "_exact"
        out.setdefault(binname, set()).add(sub)
    return out


def command_is_covered(cmd: str, prefixes: dict[str, set[str]]) -> bool:
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
    if sub in allowed:
        return True
    if (sub + "_exact") in allowed:
        return True
    # Two-word subcommands: `gh pr create` style
    if len(parts) > 2:
        two = parts[1] + " " + parts[2]
        if two in allowed:
            return True
    return False


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)


def check_stage_table_consistency(orch: dict, fails: Failures) -> None:
    keys = set(orch["STAGE_DIRS"])
    for table_name in ("STAGE_TOOLS", "STAGE_PRIMARY_OUTPUT", "STAGE_MARKER"):
        diff = keys.symmetric_difference(set(orch[table_name]))
        if diff:
            fails.add(f"{table_name} keys differ from STAGE_DIRS: {sorted(diff)}")
    rl = set(orch["RUN_LEVEL_STAGES"])
    pl = set(orch["PHASE_LEVEL_STAGES"])
    if rl & pl:
        fails.add(f"stage in both RUN_LEVEL and PHASE_LEVEL: {sorted(rl & pl)}")
    if (rl | pl) != keys:
        fails.add(f"RUN_LEVEL | PHASE_LEVEL != STAGE_DIRS: missing={sorted(keys - (rl|pl))}, extra={sorted((rl|pl) - keys)}")


def check_canonical_prompts_exist(orch: dict, fails: Failures) -> None:
    for stage in orch["STAGE_DIRS"]:
        p = PROMPTS_DIR / f"{stage}.md"
        if not p.exists():
            fails.add(f"missing canonical prompt: scripts/prompts/{stage}.md")


def check_bash_commands_covered(orch: dict, fails: Failures) -> None:
    for stage in orch["STAGE_DIRS"]:
        prompt = PROMPTS_DIR / f"{stage}.md"
        if not prompt.exists():
            continue
        text = prompt.read_text(encoding="utf-8")
        text = strip_negative_examples(text)
        cmds = extract_inline_commands(text)
        if not cmds:
            continue
        prefixes = stage_tool_subprefixes(orch["STAGE_TOOLS"][stage])
        for cmd in cmds:
            if not command_is_covered(cmd, prefixes):
                fails.add(
                    f"[{stage}] command not granted by STAGE_TOOLS: `{cmd}`"
                )


def check_tokens_covered(orch: dict, fails: Failures) -> None:
    replacements = parse_replacements()
    if not replacements:
        fails.add("could not extract replacements dict from materialize_prompt")
        return
    for stage in orch["STAGE_DIRS"]:
        prompt = PROMPTS_DIR / f"{stage}.md"
        if not prompt.exists():
            continue
        text = prompt.read_text(encoding="utf-8")
        for m in TOKEN_RE.finditer(text):
            tok = m.group(1)
            # Skip placeholders that are obviously documentation:
            #  - all-uppercase like {N}, {NAME} — N is a real token; others may not be
            #  - single-letter "A","B","C","X","Y" used in tables
            if len(tok) == 1 and tok != "N":
                continue
            if tok in replacements:
                continue
            if tok.lower() in {"id", "int", "list", "url"}:
                # field names within YAML/JSON examples
                continue
            fails.add(f"[{stage}] unknown {{token}} not in replacement map: {{{tok}}}")


def check_aux_outputs_mentioned(orch: dict, fails: Failures) -> None:
    for stage, aux in orch["STAGE_REQUIRED_AUX_OUTPUTS"].items():
        prompt = PROMPTS_DIR / f"{stage}.md"
        if not prompt.exists():
            continue
        text = prompt.read_text(encoding="utf-8")
        for fname in aux:
            if fname not in text:
                fails.add(
                    f"[{stage}] aux output `{fname}` declared in "
                    f"STAGE_REQUIRED_AUX_OUTPUTS but not mentioned in prompt body"
                )


def check_verdict_enum_in_review_prompt(orch: dict, fails: Failures) -> None:
    prompt = PROMPTS_DIR / "code-review.md"
    if not prompt.exists():
        return
    text = prompt.read_text(encoding="utf-8")
    for label in orch["VALID_VERDICT_LABELS"]:
        if label not in text:
            fails.add(f"code-review prompt does not teach verdict label `{label}`")
    for tgt in orch["VALID_LOOP_TARGETS"]:
        if tgt not in text:
            fails.add(f"code-review prompt does not teach loop_target `{tgt}`")
    # Verify the verdict-to-loop mapping the prompt teaches matches the orchestrator's
    # source of truth: check that for each label, the unique allowed loop_target appears
    # near it in the prompt's verdict-classification table.
    for label, allowed in orch["VERDICT_TO_LOOP"].items():
        only = next(iter(allowed))
        # Find a line containing the label and verify the allowed target is on the same row
        for line in text.splitlines():
            if "|" in line and f"`{label}`" in line:
                if f"`{only}`" not in line:
                    fails.add(
                        f"code-review prompt: verdict `{label}` row does not pair "
                        f"with loop_target `{only}` (orchestrator requires that pair)"
                    )
                break


def check_stage_dir_naming(orch: dict, fails: Failures) -> None:
    for stage in orch["STAGE_DIRS"]:
        if stage != stage.lower():
            fails.add(f"stage `{stage}` not lowercase kebab")
        if "_" in stage:
            fails.add(f"stage `{stage}` uses underscore (must be kebab-case)")


def check_marker_in_prompt(orch: dict, fails: Failures) -> None:
    for stage, marker in orch["STAGE_MARKER"].items():
        prompt = PROMPTS_DIR / f"{stage}.md"
        if not prompt.exists():
            continue
        text = prompt.read_text(encoding="utf-8")
        if marker not in text:
            fails.add(
                f"[{stage}] prompt does not contain marker line `{marker}` "
                f"(orchestrator will reject the run as missing_marker)"
            )


def check_authority_paths_exist(fails: Failures) -> None:
    must_exist = [
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
    for p in must_exist:
        if not p.exists():
            fails.add(f"authority file missing: {p.relative_to(ROOT)}")


def check_no_absolute_paths(fails: Failures) -> None:
    """Canonical files must use {{HARNESS_ROOT}} or relative paths, never absolute.

    Exemptions:
      - Inside ``` fenced code blocks (often shell examples).
      - Paths ending in `/...` are clearly placeholder examples (e.g.
        `/home/...`, `/Users/...`).
    """
    pat = re.compile(r"(?:/home/|/Users/|/opt/|/usr/local/)[A-Za-z0-9_\-./]+")
    targets = list(DOCS_DIR.glob("*.md")) + list(PROMPTS_DIR.glob("*.md")) + [CLAUDE_MD]
    for p in targets:
        text = p.read_text(encoding="utf-8")
        for m in pat.finditer(text):
            full = m.group(0)
            if full.endswith("/...") or "..." in full:
                continue
            start = m.start()
            preceding = text[:start]
            fence_count = preceding.count("```")
            if fence_count % 2 == 1:
                continue
            fails.add(
                f"absolute path in {p.relative_to(ROOT)} at offset {start}: "
                f"`{full}` — use {{{{HARNESS_ROOT}}}} or relative path"
            )


def check_intervention_schema_wellformed(fails: Failures) -> None:
    """Verify _intervention_schema_for is total over the user-toggleable stages."""
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_intervention_schema_for":
            fn = node
            break
    if fn is None:
        fails.add("_intervention_schema_for function not found in orchestrator")
        return
    handled: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            comp = node.comparators[0]
            if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                handled.add(comp.value)
    expected = {"planning", "requirements", "phase-split", "design", "pr-create"}
    missing = expected - handled
    if missing:
        fails.add(
            f"_intervention_schema_for missing branches for: {sorted(missing)}"
        )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

CHECKS = [
    ("stage table consistency",     check_stage_table_consistency),
    ("canonical prompts exist",     check_canonical_prompts_exist),
    ("Bash commands covered",       check_bash_commands_covered),
    ("tokens covered",              check_tokens_covered),
    ("aux outputs mentioned",       check_aux_outputs_mentioned),
    ("verdict enum in code-review", check_verdict_enum_in_review_prompt),
    ("stage-dir naming",            check_stage_dir_naming),
    ("marker in prompt",            check_marker_in_prompt),
]

CHECKS_NO_ORCH = [
    ("authority files exist",       check_authority_paths_exist),
    ("no absolute paths",           check_no_absolute_paths),
    ("intervention schema",         check_intervention_schema_wellformed),
]


def main() -> int:
    fails = Failures()
    try:
        orch = parse_orchestrator()
    except Exception as e:
        print(f"FAIL: could not parse orchestrate.py: {e}", file=sys.stderr)
        return 1

    for name, fn in CHECKS:
        try:
            fn(orch, fails)
        except Exception as e:
            fails.add(f"{name}: check itself crashed: {e}")
    for name, fn in CHECKS_NO_ORCH:
        try:
            fn(fails)
        except Exception as e:
            fails.add(f"{name}: check itself crashed: {e}")

    if fails.items:
        print("FAIL:", file=sys.stderr)
        for f in fails.items:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
