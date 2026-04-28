# Stage: pr-publish (phase {N}) — push + gh pr create

## Inputs

1. `{tacit_knowledge_path}` — §7-3 PR rules.
2. `{spec_path}` — `pr_mode`, `pr_base_branch`, `pr_reviewers`, `pr_labels`, `jira_ticket`.
3. `{stage_dir}/branch.txt` — branch name (one line).
4. `{stage_dir}/pr.md` — PR body (first non-blank line starts with `<!-- pr-title: ... -->`).

Workspace cwd: `{workspace}`.

## Task

Execute the actual remote operations: push the phase branch and open the
PR via `gh`. This stage runs only when `pr_mode == auto`. The orchestrator
skips this stage entirely in `pr_mode == manual`.

By the time this stage runs, the user has already approved the draft `pr.md`
via the `pr_per_phase` intervention on `pr-create`. There is no second
intervention here — if the user wants to revise the body, they revise on
`pr-create`, which reruns the draft and brings us back here once approved.

### Steps

1. Read `branch.txt` (single line, no trailing newline) → `<branch>`.
2. Read `pr.md` and extract the title from the leading
   `<!-- pr-title: ... -->` HTML comment → `<title>`. If the marker is
   absent, fail loudly (do NOT guess a title — that means `pr-create`
   produced a malformed draft and the user should re-revise).
3. Confirm `git rev-parse --abbrev-ref HEAD` matches `<branch>`. If not,
   abort — we are on the wrong branch and must not push.
4. `git push -u origin "<branch>"`.
5. Build the `gh pr create` command:
   - `gh pr create --title "<title>" --body-file {stage_dir}/pr.md --base "<pr_base_branch>"`
   - Append `--reviewer <user>` for each entry in `pr_reviewers`.
   - Append `--label <label>` for each entry in `pr_labels`.
6. Run it and capture the printed PR URL.
7. Write the URL (one line, no trailing newline) to `{stage_dir}/pr-url.txt`.

### Idempotency

A retry of this stage (e.g., from a future backtrack that clears
`pr-url.txt`) may find that:

- The branch is already pushed → `git push -u` is idempotent, no problem.
- A PR already exists for this branch → `gh pr create` will fail. In that
  case, recover the existing URL via
  `gh pr view "<branch>" --json url --jq .url` and write it to
  `pr-url.txt`. Do NOT close/recreate the existing PR.

## Constraints

- Never `git push --force`. Never delete remote branches.
- Never use `gh pr create` flags this prompt didn't specify.
- Do not modify any source files.
- 한국어 출력.

## Output

```
PR_PUBLISH_DONE: {stage_dir}/pr-url.txt
```
