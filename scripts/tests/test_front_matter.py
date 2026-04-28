"""_read_front_matter_field robustness tests.

The design.md prompt teaches `verdict: pass` (unquoted), but YAML legitimately
accepts `verdict: "pass"` and `verdict: 'needs_revision'`. The architect-
reviewer subagent or future prompt revisions may emit quoted values. Without
quote-stripping the routing block escalates `verdict_invalid` for what is
semantically a valid front-matter value.
"""

from __future__ import annotations

from pathlib import Path

import orchestrate


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_unquoted_value(tmp_path):
    p = tmp_path / "design.md"
    _write(p, "---\nverdict: pass\nphase: 1\n---\n# body\n")
    assert orchestrate._read_front_matter_field(p, "verdict") == "pass"


def test_double_quoted_value(tmp_path):
    p = tmp_path / "design.md"
    _write(p, '---\nverdict: "needs_revision"\nphase: 1\n---\n# body\n')
    assert orchestrate._read_front_matter_field(p, "verdict") == "needs_revision"


def test_single_quoted_value(tmp_path):
    p = tmp_path / "design.md"
    _write(p, "---\nverdict: 'pass'\nphase: 1\n---\n# body\n")
    assert orchestrate._read_front_matter_field(p, "verdict") == "pass"


def test_value_with_trailing_comment_strips_to_value(tmp_path):
    p = tmp_path / "design.md"
    _write(p, "---\nverdict: pass            # pass | needs_revision\nphase: 1\n---\n# body\n")
    assert orchestrate._read_front_matter_field(p, "verdict") == "pass"


def test_quoted_value_with_trailing_comment(tmp_path):
    """Both quote-strip and comment-strip must apply."""
    p = tmp_path / "design.md"
    _write(p, '---\nverdict: "pass"   # set by reviewer\nphase: 1\n---\n')
    assert orchestrate._read_front_matter_field(p, "verdict") == "pass"


def test_missing_front_matter_returns_none(tmp_path):
    p = tmp_path / "design.md"
    _write(p, "# no front matter\nbody only\n")
    assert orchestrate._read_front_matter_field(p, "verdict") is None


def test_missing_field_returns_none(tmp_path):
    p = tmp_path / "design.md"
    _write(p, "---\nphase: 1\n---\n# body\n")
    assert orchestrate._read_front_matter_field(p, "verdict") is None


def test_nonexistent_file_returns_none(tmp_path):
    p = tmp_path / "absent.md"
    assert orchestrate._read_front_matter_field(p, "verdict") is None
