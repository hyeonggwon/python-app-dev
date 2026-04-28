# Stage: delivery

## Inputs

1. `{task_spec_path}`
2. `{spec_path}`
3. `{planning_path}`, `{requirements_path}`, `{phases_path}`
4. All `{run_dir}/phase-*/{design,implementation,review,sanity,docs-changes,pr,verdict}.{md,yaml}`
5. All `{run_dir}/phase-*/gates/*.json`
6. `{run_dir}/escalation.md` (if it exists)

Workspace cwd: `{workspace}` (read-only here).

## Task

Compose the run-end delivery report. The orchestrator handles `outputs/.index.jsonl` append after this stage exits.

### Output file

`{run_dir}/delivery.md`:

```markdown
---
run_id: <id>
final_status: success | escalated
total_phases: <int>
escalation_triggers: [<list>]   # empty if success
---

# Delivery Report

## Phase 별 요약

### Phase 1 — <title>
- **요구사항**: US-001, NFR-001
- **핵심 결정**: ...
- **PR**: https://github.com/.../pull/123  (or `phase-1/pr.md` for manual mode)
- **gates**: install ✅ lint ✅ types ✅ tests ✅ coverage ✅ (78%) sanity ✅
- **루프**: code-review minor 1회, lint-test self-correct 2회

### Phase 2 — ...

## 전체 통계
- 총 stage 실행: <total_stages>
- 자가 교정 횟수 합계: <int>
- 코드리뷰 verdict 분포: pass <a>, minor <b>, major <c>
- Sanity 실패 → design 루프백: <int>회

## 암묵지 업데이트 후보 (제안만 — tacit-knowledge.md 자동 수정 금지)
- (반복 지적된 패턴이 있으면 나열)
- (자주 cap 도달한 게이트가 있으면 나열)
- (deep-interview 토글의 효과적 조합 관찰)
- 없으면 "없음"

## 다음 작업
- (보류된 phase, 미래 작업의 후보)
```

## Constraints

- 한국어.
- 자동으로 `tacit-knowledge.md`를 수정하지 않는다. 제안만.
- `total_phases`/`final_status`는 `phases.md` 와 escalation 유무로 결정.

## Output

```
DELIVERY_DONE: {run_dir}/delivery.md
```
