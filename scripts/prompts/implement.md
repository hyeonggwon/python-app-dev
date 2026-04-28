# Stage: implement (phase {N})

## Inputs

1. `{tacit_knowledge_path}` — toolchain, commit message format (§7-2).
2. `{spec_path}` — `jira_ticket`, mode, project info.
3. `{phases_path}` — phase `{N}`'s included requirement ids.
4. `{requirements_path}` — acceptance criteria for those ids.
5. `{stage_dir}/design.md`
6. `{stage_dir}/branch.txt`
7. `{stage_dir}/feedback.md` (if exists) — feedback from `lint-test` self-correct or `code-review` minor loop.
8. `{stage_dir}/review.md` (if a code-review minor loop just sent us back) — concrete issues to address.

Workspace cwd: `{workspace}`.

## Task

Implement the design for this phase as code, with unit tests, in small commits.

### Steps

1. Confirm current branch matches `{stage_dir}/branch.txt`.
2. Read design and the targeted requirements' acceptance_criteria.
3. Create/modify source files under `src/<package>/` (or the project's existing source layout — detected at deep-interview time).
4. Create unit tests under `tests/unit/` covering each requirement's acceptance_criteria.
5. Group changes into logical commits. For each commit:
   - `git add <specific files>`
   - `git commit -m "$(cat <<'EOF'` ... `EOF` ... `)"` with the format from `{tacit_knowledge_path}` §7-2:
     - Ticketed: `<type>(<ticket>): <subject>` + Korean body + footer
     - Ticketless: `<type>: <subject>` + body + footer
     - `<subject>`: 50자 이내, 동사원형, 마침표 X.
6. After committing, run `git log --oneline <base>..HEAD` and capture SHAs.

### Output file

Write `{stage_dir}/implementation.md`:

```markdown
# 구현 요약

## 변경 파일
- `src/...` (added)
- `src/...` (modified)
- `tests/unit/test_...` (added)

## 커밋
| SHA | 타입 | 제목 |
|---|---|---|
| abc1234 | feat | /login API JWT 인증 구현 |
| def5678 | test | login 단위 테스트 추가 |

## 요구사항 매핑
- US-001 → src/auth/login.py + tests/unit/test_login.py

## 알려진 한계
(다음 phase로 미룬 것, 의도적으로 처리하지 않은 케이스)
```

## Constraints

- 의존성 추가는 `uv add <pkg>` (또는 detect된 도구의 등가 명령). pyproject.toml 직접 편집보다 도구를 사용.
- 새 파일은 design의 모듈 구조 §1을 따른다.
- **이 stage에서는 lint/format/type/test를 직접 돌리지 않는다.** 다음 stage `lint-test`가 별도로 처리. (단, 의존성 추가 직후 `uv sync`는 OK.)
- 코드는 한국어 주석 OK, 식별자는 영어 권장.

## Output

```
IMPLEMENT_DONE: {stage_dir}/implementation.md
```
