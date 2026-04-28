# Stage: code-review (phase {N})

## Inputs

1. `{tacit_knowledge_path}` — code review verdict classification §6.
2. `{stage_dir}/design.md`
3. `{stage_dir}/implementation.md`
4. `{run_dir}/phase-{N}/gates/*.json` — **read all gate results**. Do NOT re-run any gate.
5. `{thresholds_path}` — effective thresholds for this run.

## Task

Produce a unified code-review verdict by invoking sub-agent reviewers and synthesizing their output. **You write only the verdict — gates are already passed (orchestrator enforced before invoking you).**

### Steps

1. Invoke `voltagent-qa-sec:code-reviewer` via Task with:
   - The implementation diff (use `git diff <base>..HEAD` from cwd `{workspace}`).
   - The acceptance criteria from `{requirements_path}` for ids in this phase.
   - The design at `{stage_dir}/design.md`.
   - Ask for: bugs, design issues, missing edge cases, naming/style, suggested fixes.

2. **If `{security_review}` == "true"** (read from this materialized prompt — see token), additionally invoke `voltagent-qa-sec:security-auditor` with:
   - Same diff context.
   - Specific concerns: input validation, auth/session handling, secret management, injection vectors, dependency CVEs.

3. Synthesize the responses into a single verdict.

### Outputs (both required)

#### `{stage_dir}/review.md` — narrative review

```markdown
# Code Review (phase {N})

## Reviewers
- voltagent-qa-sec:code-reviewer
- voltagent-qa-sec:security-auditor (security_review=true)  # only when applicable

## Findings

### Critical (요구사항 오해, 근본 결함)
(list, or "없음")

### Major (설계 결함, 누락된 케이스)
(list)

### Minor (스타일, 사소한 버그)
(list)

## Sub-agent Raw Outputs
<details>
<summary>code-reviewer</summary>

(paste reviewer response)

</details>
```

#### `{stage_dir}/verdict.json` — machine-readable verdict (orchestrator parses this)

JSON, not YAML — the orchestrator uses stdlib `json.loads` to parse it. Every
issue must be an object with `location` / `description` / `suggestion`.

```json
{
  "verdict": "pass",
  "loop_target": "none",
  "issues_critical": [
    {
      "location": "src/auth/login.py:42",
      "description": "토큰 만료 검증이 누락됨",
      "suggestion": "exp claim 을 검증하거나 PyJWT 의 exp 자동 검증 옵션 사용"
    }
  ],
  "issues_major": [],
  "issues_minor": [],
  "summary": "한 단락. 종합 판단의 근거."
}
```

`verdict` ∈ `{pass, minor, major, critical}`,
`loop_target` ∈ `{none, implement, design, escalation}`.

### Verdict classification (must follow exactly)

| 발견 | verdict | loop_target |
|---|---|---|
| 발견 없음 / 사소한 의견만 | `pass` | `none` |
| Minor만 존재 | `minor` | `implement` |
| Major 존재 (Critical 없음) | `major` | `design` |
| Critical 1개 이상 | `critical` | `escalation` |

`loop_target`은 위 매핑을 정확히 따라야 한다. 다른 값은 orchestrator에서 거부된다.

## Constraints

- Do NOT modify any source code in this stage.
- Do NOT re-run tests/lint/types — read the gate JSONs.
- 한국어 narrative.

## Output

```
CODE_REVIEW_DONE: {stage_dir}/verdict.json
```
