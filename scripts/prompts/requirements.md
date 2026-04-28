# Stage: requirements

## Inputs

1. `{task_spec_path}`
2. `{tacit_knowledge_path}`
3. `{spec_path}`
4. `{planning_path}` — product brief from prior stage.
5. `{stage_dir}/feedback.md` (if exists) — revise feedback.

## Task

Decompose the product brief into actionable requirements. Output to:

  `{stage_dir}/requirements.md`

The file must start with a YAML front-matter block listing every requirement, then a markdown body that describes them in narrative form.

```markdown
---
requirements:
  - id: US-001
    title: 사용자 로그인
    priority: must            # must | should | could
    kind: functional
    description: |
      사용자로서 이메일/비밀번호로 로그인하여 보호된 자원에 접근하고 싶다.
    acceptance_criteria:
      - Given 유효한 이메일·비밀번호 When POST /login Then HTTP 200 + JWT 발급
      - Given 잘못된 자격 증명 When POST /login Then HTTP 401
  - id: NFR-001
    title: API 응답 시간
    priority: should
    kind: nonfunctional
    description: |
      /login p95 응답 시간이 200ms 이하여야 한다.
    acceptance_criteria:
      - p95 latency < 200ms under 50 RPS load test
---

# 요구사항 상세

## US-001 사용자 로그인
(narrative description, edge cases, dependencies)

## NFR-001 API 응답 시간
(narrative)
```

## Constraints

- ID 형식: 기능 요구사항은 `US-NNN`, 비기능 요구사항은 `NFR-NNN` (3자리 zero-pad).
- 각 항목은 반드시 `id`, `title`, `priority` (`must`/`should`/`could`), `kind` (`functional`/`nonfunctional`), `description`, `acceptance_criteria` 보유.
- `must` priority 요구사항은 다음 phase-split 단계에서 반드시 어떤 phase에 매핑되어야 하므로 — 추측·과잉 분해를 피하고 product brief의 범위에 충실할 것.
- 한국어 본문.

## Output

```
REQUIREMENTS_DONE: {stage_dir}/requirements.md
```
