# Stage: pr-create (phase {N}) — draft only

## Inputs

1. `{tacit_knowledge_path}` — §7-3 PR rules.
2. `{spec_path}` — `pr_mode` (`auto` | `manual`), `pr_base_branch`, `pr_reviewers`, `pr_labels`, `jira_ticket`.
3. `{stage_dir}/design.md`
4. `{stage_dir}/implementation.md`
5. `{stage_dir}/review.md`
6. `{stage_dir}/sanity.md`
7. `{stage_dir}/docs-changes.md`
8. `{stage_dir}/branch.txt`

## Task

**Compose the PR description only.** This stage writes `pr.md` and stops.
The actual `git push` + `gh pr create` happens in the separate `pr-publish`
stage (auto mode) AFTER the user approves this draft via the
`pr_per_phase` intervention. Splitting draft/publish is what makes the
intervention useful — if push happened here and the user then asked to
revise, re-running this stage would try to open a PR that already exists.

In `pr_mode == manual`, the user pushes and opens the PR themselves; the
orchestrator skips `pr-publish` entirely.

### Title

Use the same format as the first commit's subject:

- Ticketed: `<type>(<jira-ticket>): <subject>`
- Ticketless: `<type>: <subject>`

`<subject>` 50자 이내. Use the phase title from `phases.md` (or the most
representative commit subject). Write the title as the first line of
`pr.md` after the body, in a `<!-- pr-title: ... -->` HTML comment so
`pr-publish` can read it without the user having to retype it:

```markdown
<!-- pr-title: feat(MAE1-123): JWT 로그인 추가 -->
```

### Body — write to `{stage_dir}/pr.md`

```markdown
<!-- pr-title: <title> -->

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

## Constraints

- Do NOT run `git push` in this stage.
- Do NOT run `gh pr create` in this stage.
- Both commands are reserved for the separate `pr-publish` stage. The tool
  whitelist for `pr-create` no longer permits them, and any attempt will
  fail with a permission error.
- Do not modify any source files.
- 한국어 본문.

## Output

```
PR_CREATE_DONE: {stage_dir}/pr.md
```
