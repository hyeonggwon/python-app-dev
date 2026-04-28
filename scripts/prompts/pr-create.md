# Stage: pr-create (phase {N})

## Inputs

1. `{tacit_knowledge_path}` — §7-3 PR rules.
2. `{spec_path}` — `pr_mode` (`auto` | `manual`), `pr_base_branch`, `pr_reviewers`, `pr_labels`, `jira_ticket`.
3. `{stage_dir}/design.md`
4. `{stage_dir}/implementation.md`
5. `{stage_dir}/review.md`
6. `{stage_dir}/sanity.md`
7. `{stage_dir}/docs-changes.md`
8. `{stage_dir}/branch.txt`

Workspace cwd: `{workspace}`.

## Task

Compose the PR description and (in `auto` mode) push + open the PR via `gh`.

### Title

Use the same format as the first commit's subject:

- Ticketed: `<type>(<jira-ticket>): <subject>`
- Ticketless: `<type>: <subject>`

`<subject>` 50자 이내. Use the phase title from `phases.md` (or the most representative commit subject).

### Body — write to `{stage_dir}/pr.md`

```markdown
## Summary
- (1~3 bullets, 사용자 가치 위주)

## 변경 사항
- (구현 핵심 변경, 모듈 단위)

## Phase 산출물
- 설계: phase-{N}/design.md
- 구현 요약: phase-{N}/implementation.md
- 코드리뷰 verdict: phase-{N}/verdict.json
- Sanity: phase-{N}/sanity.md
- 문서 변경: phase-{N}/docs-changes.md

## Test plan
- [ ] 단위 테스트 통과 (CI)
- [ ] Sanity 시나리오 통과 — `pytest tests/sanity -m sanity`
- [ ] (필요시) 수동 확인 시나리오

Refs: <jira-ticket>     # 티켓 있을 때만
```

### Push & create (only when `pr_mode == auto`)

1. `git rev-parse --abbrev-ref HEAD` to confirm current branch matches `branch.txt`.
2. `git push -u origin "$(cat {stage_dir}/branch.txt)"`.
3. `gh pr create --title "<title>" --body-file {stage_dir}/pr.md --base "<pr_base_branch>"` plus:
   - `--reviewer alice --reviewer bob` for each in `pr_reviewers`
   - `--label foo --label bar` for each in `pr_labels`
4. Capture the printed PR URL and write it (one line, no trailing newline) to `{stage_dir}/pr-url.txt`.

### Manual mode (`pr_mode == manual`)

- Do NOT push. Do NOT create the PR.
- Just write `{stage_dir}/pr.md` with the same body. The user will push and open the PR themselves.

## Constraints

- Never `git push --force`. Never delete remote branches.
- Never use `gh pr create` flags this prompt didn't specify.
- 한국어 본문.

## Output

```
PR_CREATE_DONE: {stage_dir}/pr.md
```
