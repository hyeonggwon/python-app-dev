"""Boolean coercion tests for `_truthy` and the call sites that depend on it.

Why this file exists: spec.md is YAML and the deep-interview LLM merges the
same data into `state.json.interview_spec` as JSON. JSON has no on/off
literal, so a careless merge can land ``"planning": "off"`` (literal string)
into the interview_spec. A naive ``if interventions[stage]:`` then sees the
truthy non-empty string "off" and fires the very intervention the user
disabled. Same shape of bug for ``security_review``, ``mypy_strict``,
``pytest_parallel``.

The fix is twofold:

  * ``orchestrate._truthy`` and ``run_gate._truthy`` (mirror) coerce the value
    explicitly — recognized truthy/falsy strings become bool, anything else
    falls back to the caller-provided default rather than guessing.
  * docs/interview-guide.md, docs/tacit-knowledge.md, docs/stages.md, and the
    deep-interview skill now teach ``true``/``false`` as the canonical boolean
    spelling. ``test_doc_boolean_canonicalization`` below pins that — if the
    docs ever drift back to ``on``/``off`` for these toggles, the test catches
    it before a future merge can re-introduce the bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import orchestrate
import run_gate
from scripts.tests._helpers import (  # type: ignore[import-not-found]
    make_run_dir, parse_emit, write_state,
)


ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = ROOT / "docs"
SKILL_DIR = ROOT / ".claude" / "skills"


# ---------------------------------------------------------------------------
# 1. Unit: _truthy in orchestrate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [True, 1, 1.5, -3, "true", "True", "TRUE",
                                   "yes", "YES", "on", "ON", "1", " on ", " TRUE\t"])
def test_orchestrate_truthy_recognizes_truthy(value):
    assert orchestrate._truthy(value) is True


@pytest.mark.parametrize("value", [False, 0, 0.0, "false", "False", "FALSE",
                                   "no", "NO", "off", "OFF", "0", "", "  ", None])
def test_orchestrate_truthy_recognizes_falsy(value):
    assert orchestrate._truthy(value) is False


@pytest.mark.parametrize("value", ["maybe", "definitely-not", "1.0.0", "yesno",
                                    "ʏᴇs", object()])
def test_orchestrate_truthy_unrecognized_returns_default(value):
    """Unknown shapes don't auto-flip to False — they honor the caller's default
    so an unrecognized string never silently disables a guard the user asked
    for. This is what makes ``default=True`` for default-on interventions safe."""
    assert orchestrate._truthy(value, default=True) is True
    assert orchestrate._truthy(value, default=False) is False


def test_orchestrate_truthy_bool_subclass_passthrough():
    """A real bool short-circuits the string path. Important for the case where
    spec.md was written canonically and arrived as bool — we don't want the
    function to second-guess it."""
    assert orchestrate._truthy(True, default=False) is True
    assert orchestrate._truthy(False, default=True) is False


# ---------------------------------------------------------------------------
# 2. Unit: _truthy in run_gate (must mirror orchestrate)
# ---------------------------------------------------------------------------

def test_run_gate_truthy_mirrors_orchestrate():
    """run_gate keeps its own copy (the two scripts are intentionally
    independent). The two implementations must agree on every recognized
    spelling — drift here re-opens the same bug under different gates."""
    cases = [
        # (value, default, expected)
        (True, False, True),
        (False, True, False),
        (1, False, True),
        (0, True, False),
        ("true", False, True),
        ("on", False, True),
        ("yes", False, True),
        ("1", False, True),
        ("false", True, False),
        ("off", True, False),
        ("no", True, False),
        ("0", True, False),
        ("", True, False),
        ("  ", True, False),
        (None, True, True),
        (None, False, False),
        ("maybe", True, True),
        ("maybe", False, False),
    ]
    for value, default, expected in cases:
        orch = orchestrate._truthy(value, default=default)
        rg = run_gate._truthy(value, default=default)
        assert orch == rg == expected, (
            f"divergence for ({value!r}, default={default}): "
            f"orchestrate={orch} run_gate={rg} expected={expected}"
        )


# ---------------------------------------------------------------------------
# 3. Integration: route() respects coerced intervention toggles
# ---------------------------------------------------------------------------

def _state_for_intervention(run_dir, **interventions) -> dict:
    return write_state(
        run_dir,
        interview_spec={"interventions": interventions},
        counters={"total_stages": 0, "planning_revise": 0,
                  "requirements_revise": 0, "phase_split_revise": 0},
    )


def test_planning_intervention_string_off_does_not_fire(tmp_path, capsys):
    """The reported bug: spec.md `planning: off` arrives as the string "off"
    in interview_spec. Pre-fix, `if interventions[stage]:` was truthy on
    "off" → user saw an awaiting_user gate they explicitly disabled.

    Post-fix, _truthy maps "off" → False and routing falls through to the
    default-pass branch."""
    run_dir = make_run_dir(tmp_path)
    state = _state_for_intervention(run_dir, planning="off")

    rc = orchestrate.route("planning", run_dir, None, state, {"caps": {}})

    assert rc == 0
    assert state["status"] != "awaiting_user", (
        "planning intervention with string 'off' must not block as awaiting_user"
    )
    out = parse_emit(capsys.readouterr().out)
    assert out["decision"] == "pass"
    assert out["next"] == "requirements"


def test_planning_intervention_string_on_fires(tmp_path, capsys):
    """Counter-test: string "on" must still fire — _truthy recognizes it."""
    run_dir = make_run_dir(tmp_path)
    state = _state_for_intervention(run_dir, planning="on")

    rc = orchestrate.route("planning", run_dir, None, state, {"caps": {}})

    assert rc == 0
    assert state["status"] == "awaiting_user"
    out = parse_emit(capsys.readouterr().out)
    assert out["decision"] == "awaiting_user"


def test_planning_intervention_bool_false_does_not_fire(tmp_path, capsys):
    """The other half of the contract: a real Python False also disables
    (we didn't break the canonical path while fixing the string path)."""
    run_dir = make_run_dir(tmp_path)
    state = _state_for_intervention(run_dir, planning=False)

    rc = orchestrate.route("planning", run_dir, None, state, {"caps": {}})

    assert rc == 0
    assert state["status"] != "awaiting_user"


def test_planning_intervention_missing_uses_default_true(tmp_path, capsys):
    """When the user didn't provide the key at all, we default to the
    documented per-stage default (True for planning). Coercion must not
    accidentally flip this to False."""
    run_dir = make_run_dir(tmp_path)
    state = write_state(run_dir, interview_spec={"interventions": {}})

    rc = orchestrate.route("planning", run_dir, None, state, {"caps": {}})

    assert rc == 0
    assert state["status"] == "awaiting_user"


def test_design_intervention_string_off_does_not_fire(tmp_path, capsys):
    """design_per_phase defaults to False. A user who explicitly sets it to
    string "off" expects the same behavior as the default — pre-fix, the
    string would fire the gate."""
    run_dir = make_run_dir(tmp_path, phase=1)
    state = _state_for_intervention(run_dir, design_per_phase="off")
    state["counters"]["design_arch_self__phase_1"] = 0
    state["counters"]["design_revise__phase_1"] = 0
    write_state(run_dir, **state)
    # design.md with verdict: pass so route() doesn't try to escalate on a
    # missing front matter — we want the intervention check to be the only
    # thing left between us and the default-pass branch.
    sd = run_dir / "phase-1"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "design.md").write_text("---\nverdict: pass\n---\n# design\n", encoding="utf-8")

    rc = orchestrate.route("design", run_dir, 1, state, {"caps": {}})

    assert rc == 0
    assert state["status"] != "awaiting_user"


def test_design_intervention_string_on_fires(tmp_path, capsys):
    """design_per_phase=on (string) must fire — same coercion path."""
    run_dir = make_run_dir(tmp_path, phase=1)
    state = _state_for_intervention(run_dir, design_per_phase="on")
    state["counters"]["design_arch_self__phase_1"] = 0
    state["counters"]["design_revise__phase_1"] = 0
    write_state(run_dir, **state)
    sd = run_dir / "phase-1"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "design.md").write_text("---\nverdict: pass\n---\n# design\n", encoding="utf-8")

    rc = orchestrate.route("design", run_dir, 1, state, {"caps": {}})

    assert rc == 0
    assert state["status"] == "awaiting_user"


# ---------------------------------------------------------------------------
# 4. Integration: merge_effective_thresholds coerces booleans
# ---------------------------------------------------------------------------

def test_merge_effective_thresholds_coerces_string_off_for_security_review():
    """`security_review: "off"` (string) in spec must NOT enable the
    security-auditor sub-agent. Pre-fix, `if spec.get("security_review"):`
    was truthy on the non-empty string, leaking the auditor into runs the
    user explicitly opted out of."""
    state = {
        "interview_spec": {"security_review": "off"},
        "overrides": {},
    }
    eff = orchestrate.merge_effective_thresholds(state)
    # Falsy security_review should not be present in thresholds — the merge
    # only sets the key when the truthy check passes.
    assert eff["thresholds"].get("security_review") is not True


def test_merge_effective_thresholds_coerces_string_on_for_security_review():
    state = {
        "interview_spec": {"security_review": "on"},
        "overrides": {},
    }
    eff = orchestrate.merge_effective_thresholds(state)
    assert eff["thresholds"]["security_review"] is True


def test_merge_effective_thresholds_coerces_string_true_for_mypy_strict():
    state = {
        "interview_spec": {"mypy_strict": "true"},
        "overrides": {},
    }
    eff = orchestrate.merge_effective_thresholds(state)
    assert eff["thresholds"]["mypy_strict"] is True


def test_merge_effective_thresholds_coerces_string_false_for_pytest_parallel():
    state = {
        "interview_spec": {"pytest_parallel": "false"},
        "overrides": {},
    }
    eff = orchestrate.merge_effective_thresholds(state)
    assert eff["thresholds"]["pytest_parallel"] is False


# ---------------------------------------------------------------------------
# 5. Integration: run_gate.make_command honors coerced toggles
# ---------------------------------------------------------------------------

def test_make_command_types_with_string_true_adds_strict(tmp_path):
    """`bool("false") == True` in plain Python — so the pre-fix code
    treated *any* non-empty string as mypy --strict, including "false"."""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    cmd = run_gate.make_command(
        "types", toolchain={}, workspace=ws,
        options={"mypy_strict": "true"},
    )
    assert cmd is not None and "--strict" in cmd


def test_make_command_types_with_string_false_omits_strict(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    cmd = run_gate.make_command(
        "types", toolchain={}, workspace=ws,
        options={"mypy_strict": "false"},
    )
    assert cmd is not None and "--strict" not in cmd


def test_make_command_tests_with_string_true_adds_xdist(tmp_path):
    ws = tmp_path / "ws"
    (ws / "tests" / "unit").mkdir(parents=True)
    cmd = run_gate.make_command(
        "tests", toolchain={}, workspace=ws,
        options={"pytest_parallel": "on"},
    )
    assert cmd is not None and "-n" in cmd and "auto" in cmd


def test_make_command_tests_with_string_false_no_xdist(tmp_path):
    ws = tmp_path / "ws"
    (ws / "tests" / "unit").mkdir(parents=True)
    cmd = run_gate.make_command(
        "tests", toolchain={}, workspace=ws,
        options={"pytest_parallel": "off"},
    )
    assert cmd is not None and "-n" not in cmd


# ---------------------------------------------------------------------------
# 6. _parse_simple_yaml accepts the same boolean spellings as _truthy
# ---------------------------------------------------------------------------

def test_parse_simple_yaml_accepts_yaml_1_1_booleans():
    """_parse_simple_yaml is used for config.yaml (controlled, hand-written)
    but it must agree with _truthy on what counts as a boolean — otherwise a
    contributor who writes `pytest_parallel: on` in config.yaml would land
    the literal string "on" in caps/thresholds, which downstream readers
    would then have to coerce themselves (or get bitten)."""
    text = (
        "section:\n"
        "  a: true\n"
        "  b: false\n"
        "  c: on\n"
        "  d: off\n"
        "  e: yes\n"
        "  f: no\n"
        "  g: 1\n"
    )
    parsed = orchestrate._parse_simple_yaml(text)
    section = parsed["section"]
    assert section["a"] is True
    assert section["b"] is False
    assert section["c"] is True
    assert section["d"] is False
    assert section["e"] is True
    assert section["f"] is False
    # `1` should still parse as int — boolean-recognition only kicks in for
    # the canonical spellings, so legit numeric configs aren't clobbered.
    assert section["g"] == 1
    assert isinstance(section["g"], int) and not isinstance(section["g"], bool)


# ---------------------------------------------------------------------------
# 7. Doc canonicalization: no `on`/`off` left for boolean toggles
# ---------------------------------------------------------------------------

def _bool_toggle_keys() -> list[str]:
    """The interview keys whose values must be `true`/`false` in canonical docs.
    `pr_mode` is excluded — it's a string enum (auto/manual), not a boolean."""
    return [
        "interventions.planning",
        "interventions.requirements",
        "interventions.phase_split",
        "interventions.design_per_phase",
        "interventions.pr_per_phase",
        "mypy_strict",
        "security_review",
        "pytest_parallel",
    ]


_DOC_FILES = [
    DOCS_DIR / "interview-guide.md",
    DOCS_DIR / "tacit-knowledge.md",
    DOCS_DIR / "stages.md",
    DOCS_DIR / "task-spec.md",
    SKILL_DIR / "python-app-dev-deep-interview" / "SKILL.md",
]


@pytest.mark.parametrize("doc", _DOC_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_canonical_docs_dont_use_on_off_for_known_bool_toggles(doc):
    """Pin the documentation half of the fix.

    The bug surfaced because `interview-guide.md` showed
    ``interventions.planning: on`` while the orchestrator merged values
    through JSON (which has no on/off literal). Even though `_truthy` now
    coerces strings, the canonical docs must teach `true`/`false` so that
    LLMs reading the example don't reintroduce string-typed booleans into
    `state.interview_spec`. This catches doc drift before code can rely on
    a string-typed boolean and re-open the original hole."""
    text = doc.read_text(encoding="utf-8")
    bad: list[str] = []
    for key in _bool_toggle_keys():
        leaf = key.split(".")[-1]
        # Match `<leaf>: on` / `<leaf>: off` (YAML) and `<leaf> == on` / `== off`
        # (prose-style). Allow trailing comment / whitespace / end-of-line.
        import re
        for pattern in (
            rf"\b{re.escape(leaf)}\s*:\s*(on|off)\b",
            rf"\b{re.escape(leaf)}\s*==\s*(on|off)\b",
        ):
            for m in re.finditer(pattern, text):
                bad.append(f"{m.group(0)!r} (matches {pattern!r})")
    assert not bad, (
        f"{doc.relative_to(ROOT)} still uses on/off for canonical boolean "
        f"toggles: {bad}. Use `true`/`false` — see docs/interview-guide.md §3 "
        f"and the _truthy fix that went with it."
    )
