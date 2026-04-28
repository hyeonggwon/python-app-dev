# Stage: phase-split

## Inputs

1. `{task_spec_path}`
2. `{tacit_knowledge_path}` — see §3 (Phase 분리 정책).
3. `{spec_path}`
4. `{planning_path}`
5. `{requirements_path}`
6. `{stage_dir}/feedback.md` (if exists)

## Task

Split the requirements into ordered phases following **MVP → 확장** policy. Output to:

  `{stage_dir}/phases.md`

Use the following YAML front-matter + markdown body format:

```markdown
---
phases:
  - id: 1
    title: MVP — 인증 코어
    goal: |
      이메일·비밀번호로 로그인하고 보호된 자원에 접근할 수 있다.
    included_requirement_ids: [US-001, NFR-001]
    out_of_phase: |
      - 비밀번호 재설정 (Phase 2)
      - 소셜 로그인 (Phase 3)
  - id: 2
    title: 비밀번호 재설정 흐름
    goal: ...
    included_requirement_ids: [US-002]
    out_of_phase: |
      ...
---

# Phase 분리 근거

## Phase 1 — MVP
(왜 이 묶음이 가치 단위인가. 어떤 가설을 먼저 검증하는가.)

## Phase 2 — ...
```

## Constraints

- Phase 수: **최대 5개**. 더 필요하면 `feedback.md`에 사유 적고 사용자 개입에 의존.
- ID는 1부터 연속 (`1, 2, 3, ...`).
- `requirements.md`의 모든 `priority: must` 항목은 어떤 phase에 **반드시** 매핑되어야 한다.
- 각 phase는 **독립적으로 PR 가능**한 단위여야 한다 (다른 phase의 산출물에 의존하더라도, 의존이 머지된 후 PR 가능한 정도까지는 격리되어야 함).
- 한국어.

## Output

```
PHASE_SPLIT_DONE: {stage_dir}/phases.md
```
