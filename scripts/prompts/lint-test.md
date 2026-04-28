# Stage: lint-test (phase {N})

## Inputs

1. `{tacit_knowledge_path}` — gates spec §2.
2. `{stage_dir}/implementation.md`
3. `{stage_dir}/feedback.md` — **always read this**. The orchestrator wrote gate result paths here before invoking you.
4. The gate JSON files referenced from feedback (read each one).

Workspace cwd: `{workspace}`.

## Task

Self-correct lint/format/type/test failures iteratively until all gates pass, or you cannot make progress.

### Iteration protocol

The orchestrator already ran the gates **once** before invoking you. Your job:

1. Read each gate JSON in `{run_dir}/phase-{N}/gates/*.json`. Identify which ones have `passed=false`.
2. For each failing gate, diagnose and fix:
   - **install**: missing dep → `uv add <pkg>`; broken pyproject → fix the toml.
   - **lint** (ruff): run `uv run ruff check --fix` for auto-fixable. For remaining issues, edit code by hand.
   - **format**: run `uv run ruff format`.
   - **types** (mypy): add missing annotations, fix Optional misuse, narrow types.
   - **tests**: read failing test output (in `gates/tests.stdout.txt`); fix the code or the test (be careful — only fix tests when the test was wrong, not when the code is wrong).
   - **coverage**: add unit tests to cover uncovered branches.
3. Stage and commit each round of fixes:
   - Commit message: `chore: lint-test 자가 교정 (round N)` or more specific (`fix: mypy 누락 어노테이션 보강`).
4. After your fixes, **request the orchestrator re-run gates** by appending to `{stage_dir}/lint-test.md` (see Output below) and exiting with the marker line.

The orchestrator will detect your exit, re-run gates, and either re-invoke you (if still failing and cap not reached) or proceed.

## Output file

Append to `{stage_dir}/lint-test.md` after each round (cumulative log):

```markdown
# Lint/Test 자가 교정 로그

## Round 1 (timestamp)
**실패 게이트**: lint, types, tests
**진단 요약**:
- ruff: F401 unused import × 3
- mypy: src/auth/jwt.py:42 missing return annotation
- pytest: test_login_invalid_password 실패 — 401 대신 500 반환

**조치**:
- `uv run ruff check --fix` → 3개 자동 수정
- src/auth/jwt.py:42에 `-> str` 추가
- src/auth/login.py에서 잘못된 예외 처리 수정

**커밋**:
- abc1234 fix: ruff F401 + 누락 어노테이션 보강
- def5678 fix: 잘못된 자격 증명 시 401 반환

## Round 2 (timestamp)
...
```

## Constraints

- Do NOT change design intent. If a fix would meaningfully alter the public API or data model, **stop** and add a `### 설계 영향` block to `lint-test.md` describing the conflict — orchestrator will route to `design`.
- 자가 교정 cap = 5 (orchestrator enforces). cap 도달 시 자동으로 design 루프백.

## Output

```
LINT_TEST_DONE: {stage_dir}/lint-test.md
```
