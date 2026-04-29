[한국어](README.md) | **English**

# python-app-dev

> A multi-stage Claude Code harness that drives Python app development from planning to PR while keeping the main session's context clean.

## Initialize

```bash
git clone git@github.com:hyeonggwon/python-app-dev.git
cd python-app-dev
./install.sh
```

What `install.sh` does:
- Sets `git core.hooksPath = .githooks` (enforces pytest before every commit)
- Verifies required tools (`python3 / claude / git`); warns on optional (`uv / gh`)
- Runs `python3 -m pytest scripts/tests/` once for sanity

## Quick Start

In a Claude Code main session:

```
/orchestrate-python-app-dev
```

Flow:
1. **deep-interview** — asks for mode (new/maintenance), 5 user-intervention toggles, caps, PR mode; writes `spec.md`
2. **run-level** — `planning` → `requirements` → `phase-split`
3. **phase-level** (per phase) — `design` → `branch-create` → `implement` → `lint-test` → `code-review` → `sanity-test` → `document` → `pr-create`
4. **delivery** — finalize and surface tacit-knowledge update candidates

## How It Works

**Mode B**: the main session never reads stage bodies. The orchestrator launches each stage as a separate `claude -p` call; the main session only parses the final `ORCHESTRATE_RESULT: {decision, ...}` JSON line to decide what comes next.

| decision | next action |
|---|---|
| `pass` | dispatch the next stage |
| `loopback` | on review verdict or sanity failure, restart an earlier stage (artifacts cleaned automatically) |
| `awaiting_user` | wait for user approval after a stage whose toggle is ON |
| `escalated` | cap exceeded / missing marker / validation failure — halt and request user decision |

Each stage is bounded by `--allowed-tools`; `lint-test` must pass `run_gate.py` thresholds.

## Architecture

```
.
├── CLAUDE.md                  # guide for harness maintainers
├── install.sh
├── docs/                      # source of truth (PR to modify)
│   ├── task-spec.md
│   ├── tacit-knowledge.md     # toolchain / self-correction / threshold policy
│   ├── interview-guide.md     # per-run variables
│   └── stages.md              # 8-field stage definitions + routing
├── .claude/skills/            # two skills loaded by the main session
├── scripts/
│   ├── orchestrate.py         # single entrypoint — runs one stage, prints ORCHESTRATE_RESULT
│   ├── init_run.py            # creates outputs/<run-id>/ + state.json
│   ├── run_gate.py            # lint-test gate evaluator
│   ├── detect_toolchain.py    # auto-detects uv / poetry / pip
│   ├── config.yaml            # default caps / thresholds
│   ├── prompts/               # 12 canonical stage prompts
│   └── tests/                 # pytest — cross-file invariants + unit tests
└── .githooks/pre-commit       # enforces pytest before each commit
```

## Generated files

Every run is isolated under `outputs/<run-id>/`:

```
outputs/<run-id>/
├── state.json                 # single source of truth — only the orchestrator writes
├── interview/spec.md          # deep-interview output
├── effective_thresholds.json  # merged config + spec overrides snapshot
├── planning.md / requirements.md / phases.md
├── phase-N/
│   ├── design.md, branch-create.md, implement.md, sanity-test.md, document.md, pr-create.md
│   ├── lint-test/             # ruff / mypy / pytest result JSONs
│   ├── review.md, verdict.json
│   └── feedback.md            # accumulated on loopback
├── workspace/                 # user project's .git (kept separate)
├── escalation.md / escalation.decision.md  # when triggered
└── delivery.md                # at run end — includes tacit-knowledge update candidates
```

`outputs/` is gitignored from the harness repo. Only `outputs/.index.jsonl` gets one line appended at run end.

## Development

To modify the *harness itself*, `CLAUDE.md` is authoritative. Key rules:

- `docs/` ↔ stage prompts ↔ `STAGE_TOOLS` / `STAGE_DIRS` move as a set — change one and `scripts/tests/test_invariants.py` will block
- Don't bypass pre-commit (`--no-verify` is forbidden)
- No hard-coded absolute paths (`/home/...`) — use `{{HARNESS_ROOT}}` / `{run_dir}` tokens
- Don't auto-edit `tacit-knowledge.md` or caps — only surface candidates in `delivery.md`; humans decide
- Harness git and workspace git are separate — `ensure_workspace_repo()` verifies the workspace

To add a new stage, follow the 4-step checklist in `CLAUDE.md`.

## Requirements

- Python 3.10+
- [Claude Code CLI](https://claude.com/claude-code)
- git
- (optional) `uv` — default package tool for new projects
- (optional) `gh` — required when `pr_mode=auto`
