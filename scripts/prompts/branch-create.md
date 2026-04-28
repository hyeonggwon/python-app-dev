# Stage: branch-create (phase {N})

## Inputs

1. `{tacit_knowledge_path}` — see §7-1 for branch naming.
2. `{spec_path}` — `jira_ticket`, `change_kind`.
3. `{phases_path}` — phase `{N}` title (used as keyword for ticketless branches).
4. `{stage_dir}/design.md`

## Task

Create (or check out) the working git branch for this phase. Idempotent: if the target branch already exists, switch to it instead of creating.

Workspace cwd: `{workspace}`.

### Naming rules

Read `jira_ticket` from `{spec_path}` front-matter:

- **티켓 있음**: `dev_matthew_<jira-ticket>_<NN>` where NN ∈ {01, 02, 03, ...} is the next available 2-digit suffix.
  - To pick NN, query: `git ls-remote --heads origin "dev_matthew_<ticket>_*"` and pick the smallest NN not yet used (start at 01).
  - Also check local: `git for-each-ref --format='%(refname:short)' refs/heads/dev_matthew_<ticket>_*`.
- **티켓 없음** (`jira_ticket: null`):
  - `<change_kind>/<keyword>` where keyword is derived from the phase title in kebab-case (lowercase, ASCII, hyphens). If `change_kind` is not set in spec (new mode), use `feat`.

### Steps

1. **Backtrack-safe reuse (this-phase only)**: if `{stage_dir}/branch.txt` already
   exists, read its single-line value `<existing>` and run
   `git switch <existing>`. Confirm with `git rev-parse --abbrev-ref HEAD` and
   stop — do not allocate a new name, do not touch base. This is the only
   reuse path: it triggers when code-review-major / sanity-fail backtrack
   then re-enters branch-create within the *same* phase. We must stay on the
   branch we already pushed commits to.

2. **Fresh phase — branch off base, never off a previous phase's branch**.
   This is the critical rule: on phase 2's first entry, HEAD is still on
   phase 1's branch. If we created phase 2's branch from there, phase 2's PR
   would include all of phase 1's commits, violating the "each phase
   independently PR-able" policy in tacit-knowledge §3.

   Read `pr_base_branch` from `{spec_path}` (default `main` if absent).
   - `git rev-parse --verify --quiet <base>` to test local existence.
   - If exists: `git switch <base>` (or `git checkout <base>`).
   - If not (e.g., a `mode=new` workspace that was freshly initialized and
     has no commits or branches yet): skip the base switch — the new branch
     will inherit the unborn / current HEAD. Do **not** error.

3. Determine the new branch name per the naming rules above.

4. Create or switch:
   - `git rev-parse --verify --quiet <branch>` — if exists locally,
     `git switch <branch>`.
   - Else `git switch -c <branch>` (or `git checkout -b <branch>`).

5. Confirm with `git rev-parse --abbrev-ref HEAD`.

6. Write the branch name (one line, no trailing newline) to
   `{stage_dir}/branch.txt`.

## Constraints

- Do not push in this stage.
- Do not modify any source files.

## Output

```
BRANCH_CREATE_DONE: {stage_dir}/branch.txt
```
