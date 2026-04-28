# Stage: document (phase {N})

## Inputs

1. `{tacit_knowledge_path}` — commit message format §7-2.
2. `{requirements_path}`
3. `{stage_dir}/design.md`
4. `{stage_dir}/implementation.md`
5. `{stage_dir}/sanity.md`

Workspace cwd: `{workspace}`.

## Task

Update project documentation to reflect the changes made in this phase, then commit the doc changes.

### What to update

- **README.md**: install/run sections if commands or env vars changed.
- **공개 API/CLI docstrings**: every function/class added or whose signature changed.
- **`docs/`** (if the project has one): user-facing guide updates relevant to this phase.
- **ADR** (`docs/adr/NNNN-<slug>.md`): if the design made a non-trivial decision (chose lib X over Y, picked async over sync, etc.). One ADR per significant decision.
- **CHANGELOG** (if present): add an entry for this phase's user-visible changes.

### Steps

1. Run `git diff <base>..HEAD` to see what changed in this phase.
2. Identify which doc surfaces are affected.
3. Edit the relevant files. Keep changes minimal — don't rewrite untouched docs.
4. Commit:
   - `git add <doc files>`
   - `git commit -m "$(cat <<'EOF'` ... `EOF` ... `)"` with type `docs` (e.g., `docs(MAE1-123): /login API 사용법 추가` or `docs: README install 섹션 갱신`).

### Output

`{stage_dir}/docs-changes.md`:

```markdown
# 문서 변경 (phase {N})

## 변경 파일
- README.md (install 섹션 갱신)
- src/auth/login.py (docstring 추가: login 함수)
- docs/adr/0007-jwt-rs256.md (신규)

## 커밋
| SHA | 제목 |
|---|---|
| abc1234 | docs(MAE1-123): /login API 사용법 추가 |

## 변경 없음 영역
- (의도적으로 그대로 둔 문서가 있다면 명시)
```

If there are genuinely no docs to update, still create `docs-changes.md` and explain why
(예: "phase 가 내부 리팩터링만 다뤘고 사용자 노출 표면이 변하지 않았다"). orchestrator 는
본문 형식을 강제하지 않고 marker 와 파일 존재만 검사한다 — 사람이 PR 리뷰에서 검토한다.

## Constraints

- 한국어 narrative OK.
- 새 docstring은 코드와 같은 언어 정책을 따른다 (영어 식별자 + 한국어/영어 docstring 가능).

## Output

```
DOCUMENT_DONE: {stage_dir}/docs-changes.md
```
