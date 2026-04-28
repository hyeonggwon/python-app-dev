#!/usr/bin/env python3
"""Detect a Python project's toolchain and emit a JSON description.

Used in maintenance mode: deep-interview runs this against the user-supplied
project_path to learn which packaging/lint/format/type/test/runner tools are
already in place. The result is shown to the user (override possible) and stored
in interview/spec.md `detected_toolchain`.

Usage:
  python3 scripts/detect_toolchain.py <project_path>

Output: JSON to stdout describing the detected toolchain.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


def read_toml(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def detect_packaging(root: Path, pyproject: dict | None) -> str:
    if (root / "uv.lock").exists():
        return "uv"
    if (root / "poetry.lock").exists():
        return "poetry"
    if (root / "Pipfile.lock").exists() or (root / "Pipfile").exists():
        return "pipenv"
    if pyproject and "tool" in pyproject:
        tool = pyproject["tool"]
        if "uv" in tool:
            return "uv"
        if "poetry" in tool:
            return "poetry"
        if "hatch" in tool:
            return "hatch"
        if "pdm" in tool:
            return "pdm"
    if (root / "requirements.txt").exists() or list(root.glob("requirements*.txt")):
        return "pip-requirements"
    if (root / "setup.py").exists() or (root / "setup.cfg").exists():
        return "setuptools"
    return "unknown"


def detect_linter(root: Path, pyproject: dict | None) -> str:
    if pyproject and "tool" in pyproject and "ruff" in pyproject["tool"]:
        return "ruff"
    if (root / ".ruff.toml").exists() or (root / "ruff.toml").exists():
        return "ruff"
    if (root / ".flake8").exists() or (
        pyproject and "tool" in pyproject and "flake8" in pyproject["tool"]
    ):
        return "flake8"
    if (root / ".pylintrc").exists() or (root / "pylintrc").exists():
        return "pylint"
    return "none"


def detect_formatter(root: Path, pyproject: dict | None) -> str:
    if pyproject and "tool" in pyproject:
        tool = pyproject["tool"]
        if "ruff" in tool and "format" in tool.get("ruff", {}):
            return "ruff-format"
        if "ruff" in tool:
            return "ruff-format"
        if "black" in tool:
            return "black"
    if (root / ".ruff.toml").exists():
        return "ruff-format"
    if (root / "pyproject.toml").exists():
        return "none"
    return "none"


def detect_typechecker(root: Path, pyproject: dict | None) -> str:
    if pyproject and "tool" in pyproject:
        tool = pyproject["tool"]
        if "mypy" in tool:
            return "mypy"
        if "pyright" in tool:
            return "pyright"
    if (root / "mypy.ini").exists() or (root / ".mypy.ini").exists():
        return "mypy"
    if (root / "pyrightconfig.json").exists():
        return "pyright"
    return "none"


def detect_test(root: Path, pyproject: dict | None) -> str:
    if pyproject and "tool" in pyproject:
        tool = pyproject["tool"]
        if "pytest" in tool:
            return "pytest"
    if (root / "pytest.ini").exists():
        return "pytest"
    if (root / "tox.ini").exists():
        return "tox"
    if (root / "conftest.py").exists() or (
        (root / "tests").is_dir() and any((root / "tests").glob("test_*.py"))
    ):
        return "pytest"
    return "none"


def detect_runner(root: Path) -> str:
    if (root / "Makefile").exists():
        return "make"
    if (root / "tasks.py").exists():
        return "invoke"
    if (root / "noxfile.py").exists():
        return "nox"
    if (root / "tox.ini").exists():
        return "tox"
    return "direct"


def detect_python_version(pyproject: dict | None) -> str | None:
    if not pyproject:
        return None
    for path in (
        ("project", "requires-python"),
        ("tool", "poetry", "dependencies", "python"),
    ):
        cur = pyproject
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                cur = None
                break
            cur = cur[k]
        if isinstance(cur, str):
            return cur
    return None


def detect_default_branch(root: Path) -> str:
    git_head = root / ".git" / "HEAD"
    if not git_head.exists():
        return "main"
    # Crude: check `refs/remotes/origin/HEAD` symbolic ref via the ref file.
    origin_head = root / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    if origin_head.exists():
        try:
            content = origin_head.read_text(encoding="utf-8").strip()
            if content.startswith("ref: refs/remotes/origin/"):
                return content.split("/")[-1]
        except Exception:
            pass
    for candidate in ("main", "master", "develop"):
        if (root / ".git" / "refs" / "heads" / candidate).exists():
            return candidate
    return "main"


def detect(project_path: Path) -> dict:
    pyproject = read_toml(project_path / "pyproject.toml")
    return {
        "project_path": str(project_path),
        "is_git_repo": (project_path / ".git").exists(),
        "default_branch": detect_default_branch(project_path),
        "python_version": detect_python_version(pyproject),
        "packaging": detect_packaging(project_path, pyproject),
        "linter": detect_linter(project_path, pyproject),
        "formatter": detect_formatter(project_path, pyproject),
        "typechecker": detect_typechecker(project_path, pyproject),
        "test": detect_test(project_path, pyproject),
        "runner": detect_runner(project_path),
        "has_pyproject": pyproject is not None,
        "has_pre_commit": (project_path / ".pre-commit-config.yaml").exists(),
        "has_dot_github": (project_path / ".github").exists(),
        "has_dockerfile": (project_path / "Dockerfile").exists(),
        "has_js_assets": (project_path / "package.json").exists(),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage: detect_toolchain.py <project_path>", file=sys.stderr)
        return 2
    project_path = Path(argv[0]).expanduser().resolve()
    if not project_path.is_dir():
        print(f"ERROR: not a directory: {project_path}", file=sys.stderr)
        return 2
    result = detect(project_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
