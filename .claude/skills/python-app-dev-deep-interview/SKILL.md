---
name: python-app-dev-deep-interview
description: python-app-dev 하네스의 매 run 시작 시점 deep-interview. 사용자에게 모드(신규/유지보수), 프로젝트 컨텍스트, 5개 사용자 개입 토글, 검증 옵션, PR 출구 설정을 묻고 spec.md로 정리한다.
---

# Deep-Interview: Python App Dev

이 skill 은 `orchestrate-python-app-dev` 가 `init_run.py` 직후 호출한다. 메인 세션이 사용자와 직접 대화하며 매 run 의 변수를 채운다.

## 0. 진행 원칙

- **이미 확정된 사항(도구 체인, cap, verdict 분류 등)은 묻지 않는다.** 그건 `{{HARNESS_ROOT}}/docs/tacit-knowledge.md` 에 있다.
- 한 번에 한두 항목씩 묻는다 — 한꺼번에 폼처럼 들이밀지 마라.
- 사용자가 모호하게 답하면 추측해서 채우지 말고 한 번 더 구체화 질문을 던진다.
- 사용자가 답할 수 없는 항목은 기본값으로 채우고 그 사실을 명시한다.
- 마지막에 채워진 spec 을 사용자에게 요약해서 "이대로 진행" 을 확정받는다.

## 1. 시작 — 권위 파일 빠르게 살핌

대화 시작 전에 다음을 한 번씩 `Read` 하고 즉시 폐기 (메인 세션 컨텍스트에 본문을 들고 있지 않는다):

- `{{HARNESS_ROOT}}/docs/task-spec.md`
- `{{HARNESS_ROOT}}/docs/interview-guide.md`
- `{{HARNESS_ROOT}}/docs/tacit-knowledge.md` §1 (도구 체인 — 사용자가 detect 결과를 override 할 때 참조)

## 2. 질문 순서 (사용자 친화 순)

다음 순서로 한두 항목씩 묻는다. 각 항목은 `interview-guide.md` 의 정의와 정확히 일치한다.

### 2-1. 모드 결정

> "신규 프로젝트인가요, 기존 프로젝트 유지보수인가요?"

- `new` → workspace 는 `outputs/<run-id>/workspace/` 가 자동 사용됨
- `maintenance` → 기존 경로 필요

### 2-2. 프로젝트 식별 (모드별 분기)

**`new` 일 때:**
- `project_name` (snake_case 권장)
- `project_kind` 8 종 중 선택 (cli/web-server/client-lib/ai-pipeline/data-tool/mixed-py-js/other)
- `python_version` (기본 `3.12`, 필요 시 변경)

**`maintenance` 일 때:**
- `project_path` (절대 경로)
- 즉시 `python3 {{HARNESS_ROOT}}/scripts/detect_toolchain.py "$project_path"` 실행
  - 결과(stdout JSON)를 사용자에게 보여주고 override 할 항목이 있는지 확인
  - 결과는 `state.json.interview_spec.detected_toolchain` 에 머지
  - **새 도구 체인을 임의로 강제하지 않는다.** 감지된 것을 따른다 (예: 프로젝트가 poetry 면 uv 로 강제 마이그레이션 안 함).

### 2-3. 작업 컨텍스트

- `jira_ticket` (없으면 `null` — 무티켓 형식으로 자동 전환)
- `change_kind` (`maintenance` 만): feature/fix/refactor/perf/docs/chore/test
- `goal_summary` (1~2 문장)
- `current_vs_expected` (`maintenance` + change_kind ∈ {fix, perf, refactor})
- `out_of_scope` (선택)

### 2-4. 사용자 개입 토글 (5 개)

> "다음 5 개 지점에서 매번 사용자 확인을 받을지, 자동 통과할지 결정해 주세요. 개별로 켜고 끌 수 있습니다."

| 키 | 지점 | 기본값 |
|---|---|---|
| `interventions.planning` | 기획 직후 | on |
| `interventions.requirements` | 요구사항 직후 | on |
| `interventions.phase_split` | Phase 분리 직후 | on |
| `interventions.design_per_phase` | 각 phase 의 design 직후 | off |
| `interventions.pr_per_phase` | 각 phase 의 pr-create 직후 | on |

빠르게 진행 = 모두 off, 신중히 = 모두 on, 기본 = 위 표 그대로.

### 2-5. 검증·리뷰 옵션

- `coverage_threshold` (기본 70)
- `mypy_strict` (기본 false)
- `security_review` (기본 false; on 이면 code-review 에서 `security-auditor` 추가 호출)
- `pytest_parallel` (기본 false; on 이면 `pytest-xdist` 필요)
- `sanity_scenarios_per_phase` (기본 2)

### 2-6. PR 출구

- `pr_mode`: auto | manual
- `pr_base_branch` (기본 `main`, 감지된 기본 브랜치 우선)
- `pr_reviewers` (배열, 비어있어도 OK)
- `pr_labels` (배열, 비어있어도 OK)

### 2-7. cap override (선택, 기본 권장)

기본은 `config.yaml`. 사용자가 특별히 늘리거나 줄이고 싶을 때만 묻는다. 안 물어도 됨.

## 3. 종결 처리

수집한 변수를 다음 형식으로 `{run_dir}/interview/spec.md` 에 저장:

```markdown
---
run_id: <run_id>
mode: new | maintenance
... (interview-guide.md §7 의 전체 schema)
---

# 추가 컨텍스트

(자유 형식. 사용자가 대화 중 언급한 도메인 지식, 제약, 비기능 요구 등.)
```

저장 후 `state.json.interview_spec` 에도 같은 데이터를 머지 (`json.load → update → json.dump`).

마지막으로 사용자에게 한 번 더 확정:

> "이대로 진행해도 괜찮을까요? 'yes' 라고 답해주시면 첫 stage(planning) 를 시작합니다."

확정 받으면 `orchestrate-python-app-dev` 의 dispatch 흐름으로 복귀해 `planning` 부터 호출.

## 4. 안전선

- spec 본문은 deep-interview 가 끝나면 working memory 에서 폐기 — 다음 stage 가 필요로 할 때는 `interview-guide.md` 의 권위 경로 (`{run_dir}/interview/spec.md`) 로 읽어 들인다.
- 사용자가 명시적으로 "이건 적지 말고 다음 stage 에 알려주지 마" 라고 한 정보는 spec.md 에 넣지 않는다 (예: 임시 자격증명).
- detect_toolchain 의 결과를 사용자가 override 한 항목은 `interview_spec.toolchain_overrides` 에 명시 (`detected` vs `effective` 둘 다 보존).
