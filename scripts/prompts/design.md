# Stage: design (phase {N})

## Inputs

1. `{task_spec_path}`
2. `{tacit_knowledge_path}`
3. `{spec_path}`
4. `{planning_path}`
5. `{requirements_path}`
6. `{phases_path}`
7. (workspace, if it exists) `{workspace}` — read existing source structure with Glob/Grep
8. (prior phase, if N > 1) `{run_dir}/phase-{prev}/design.md`, `implementation.md`
9. `{stage_dir}/feedback.md` (if exists) — feedback from a self-loop revise or backtrack

## Task

Design the implementation for **this phase only** (phase `{N}`). Read `{phases_path}` to identify which requirement ids belong to this phase. Output to:

  `{stage_dir}/design.md`

The file must begin with a YAML front-matter block:

```markdown
---
phase: {N}
verdict: pass
---

# 1. 모듈 구조
(이 phase에서 추가/수정할 모듈, 파일 경로, 책임 분할)

# 2. 공개 인터페이스
(API 엔드포인트 / CLI 명령 / 라이브러리 함수의 시그니처와 의미. 한 줄 한 줄 분명히.)

# 3. 데이터 모델
(이 phase에서 도입/변경하는 도메인 모델, 데이터베이스 스키마, DTO. 변경 사항만.)

# 4. 외부 의존
(새로 추가할 라이브러리, API, 인프라. 사유와 대안 검토.)

# 5. 테스트 전략
(unit 테스트 단위 + sanity 시나리오 후보 1~3개. 각 sanity 시나리오는 "관찰자 시점 골든 패스"를 한 문단으로.)

# 6. 위험·대안
(설계 결정에서 명시적으로 받아들이는 위험과 트레이드오프, 폐기한 대안)
```

## Self-review (mandatory)

After drafting the six sections above, **invoke the architect-reviewer sub-agent** via the Task tool to validate the design. Pass the design content (or the file path) and ask for:

- Whether the module/interface decomposition is coherent and minimal.
- Whether the test strategy covers the requirement acceptance_criteria.
- Whether external dependencies are justified.
- Whether risks are acknowledged.

Use this Task call:

```
subagent_type: voltagent-qa-sec:architect-reviewer
prompt: |
  Review the design at {stage_dir}/design.md against:
  - phase requirements: {requirements_path} (only ids in phases.md phase {N}.included_requirement_ids)
  - existing project structure (use Glob/Read on {workspace} if present)
  - constraints in {tacit_knowledge_path}
  Return: verdict (pass | needs_revision), concrete issues if any.
```

Read the sub-agent's response. Then **update the front-matter `verdict:` field**:

- `pass` if the reviewer approved with no major issues.
- `needs_revision` if there are concrete corrections — also append an `## Architect Review Issues` section listing them, **but do not fix them in this run**. The orchestrator will loop you back with feedback.

## Constraints

- Do NOT write any source code in this stage. This is design.
- 한국어 작성.

## Output

```
DESIGN_DONE: {stage_dir}/design.md
```
