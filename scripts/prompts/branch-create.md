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

1. **Backtrack-safe reuse**: run `git rev-parse --abbrev-ref HEAD`. If HEAD is already on a branch
   matching this phase's expected pattern (`dev_matthew_<ticket>_NN` for the current ticket, or
   `<change_kind>/<keyword>` for ticketless), skip name-allocation and reuse the current branch.
   This prevents NN drift on backtrack — code-review major → design → branch-create reruns must
   stay on the same branch we already pushed commits to.
2. Otherwise determine branch name per rules.
3. Run `git rev-parse --verify --quiet <branch>` — if exists locally, `git switch <branch>`. Else `git switch -c <branch>` (or `git checkout -b <branch>`).
4. Confirm with `git rev-parse --abbrev-ref HEAD`.
5. Write the branch name (one line, no trailing newline) to `{stage_dir}/branch.txt`.

## Constraints

- Do not push in this stage.
- Do not modify any source files.

## Output

```
BRANCH_CREATE_DONE: {stage_dir}/branch.txt
```
