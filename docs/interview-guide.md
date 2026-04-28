# Interview Guide — python-app-dev

매 run의 deep-interview에서 사용자에게 물어 채워야 하는 변수 목록. 메인 세션이 사용자와 대화하며 `outputs/<run-id>/interview/spec.md`로 기록한다.

이미 `tacit-knowledge.md`에 확정된 사항(도구 체인, 자가 교정 cap, 코드리뷰 verdict 분류 등)은 **묻지 않는다**.

## 0. 진행 원칙

- 한 번에 한두 항목씩 묻고 사용자 답변을 받아 채워나간다.
- 사용자가 모호하게 답하면 구체화 질문을 한 번 더 던진다(추측해서 채우지 않는다).
- 마지막에 채워진 spec을 사용자에게 요약해 "이대로 진행"을 확정받는다.

## 1. 모드 결정

- **`mode`** = `new` | `maintenance`
  - `new`: 신규 프로젝트. workspace는 `outputs/<run-id>/workspace/`
  - `maintenance`: 기존 프로젝트 유지보수
- (`mode = maintenance`일 때만)
  - **`project_path`**: 절대 경로
  - 시스템이 `detect_toolchain.py`를 즉시 실행해 결과를 `detected_toolchain` 필드에 채운다
  - 사용자에게 감지 결과를 보여주고 override할 항목이 있는지 확인
- (`mode = new`일 때만)
  - **`project_name`**: 패키지명 (snake_case 권장)
  - **`project_kind`**: `cli` | `web-server` | `client-lib` | `ai-pipeline` | `data-tool` | `mixed-py-js` | `other` — 시작 템플릿 선정용
  - **`python_version`**: 기본 `3.12`

## 2. 작업 컨텍스트

- **`jira_ticket`**: 문자열 (예: `MAE1-123`) 또는 `null`
  - `null`이면 브랜치명·커밋 메시지가 무티켓 형식으로 전환
- **`change_kind`**: (`maintenance`만) `feature` | `fix` | `refactor` | `perf` | `docs` | `chore` | `test`
- **`goal_summary`**: 1~2문장. 무엇을 해결/구현하려는가
- **`current_vs_expected`**: (`maintenance`이면서 `change_kind in [fix, perf, refactor]`) "현재 동작 vs 기대 동작" 자유 형식
- **`out_of_scope`**: 명시적으로 이 run에서 제외할 것 (선택)

## 3. 사용자 개입 토글

각 지점을 켜면 stage 직후 사용자 confirm을 받고, 끄면 자동 통과한다.

| 키 | 지점 | 기본값 |
|---|---|---|
| `interventions.planning` | 기획 직후 | `on` |
| `interventions.requirements` | 요구사항 직후 | `on` |
| `interventions.phase_split` | Phase 분리 직후 | `on` |
| `interventions.design_per_phase` | 각 phase의 design 직후 | `off` |
| `interventions.pr_per_phase` | 각 phase의 pr-create 직후 | `on` |

사용자가 빠르게 진행하고 싶으면 모두 끄고, 신중히 가고 싶으면 모두 켤 수 있다.

## 4. 검증·리뷰 옵션

- **`coverage_threshold`**: 정수 (0~100), 기본 `70`
- **`mypy_strict`**: bool, 기본 `false`
- **`security_review`**: bool, 기본 `false`
  - on이면 `code-review` stage에서 `security-auditor` sub-agent 추가 호출
- **`pytest_parallel`**: bool, 기본 `false` (true면 `-n auto` 추가, `pytest-xdist` 필요)
- **`sanity_scenarios_per_phase`**: 정수 1~5, 기본 `2`

## 5. PR 출구

- **`pr_mode`**: `auto` | `manual`
  - `auto`: orchestrator가 push + `gh pr create` 직접 실행
  - `manual`: PR 본문만 생성, 사용자가 직접 push
- **`pr_base_branch`**: 기본 `main` (감지된 기본 브랜치 우선)
- **`pr_reviewers`**: GitHub username 배열 또는 `[]`
- **`pr_labels`**: 문자열 배열 또는 `[]`

## 6. 루프 한계 override (선택)

기본은 `tacit-knowledge.md` 정책 그대로. 사용자가 특별히 늘리거나 줄이고 싶을 때만.

- `caps.lint_test_self_correct` (기본 5)
- `caps.code_review_minor_loop` (기본 3)
- `caps.code_review_major_loop` (기본 2)
- `caps.sanity_loop` (기본 2)
- `caps.install_loop` (기본 3)

## 7. spec.md 출력 형식

deep-interview가 끝나면 다음 YAML front-matter + 본문 형식으로 `outputs/<run-id>/interview/spec.md`에 저장.

```markdown
---
run_id: 20260427-153022
mode: new
project_name: my_app
project_kind: web-server
python_version: "3.12"
jira_ticket: MAE1-123
change_kind: null
detected_toolchain: null

goal_summary: |
  사용자가 이메일/비밀번호로 로그인할 수 있는 JWT 기반 인증 API 제공.
out_of_scope: |
  - 소셜 로그인은 차후 phase
  - 비밀번호 재설정 흐름은 별도 작업

interventions:
  planning: on
  requirements: on
  phase_split: on
  design_per_phase: off
  pr_per_phase: on

coverage_threshold: 70
mypy_strict: false
security_review: true
pytest_parallel: false
sanity_scenarios_per_phase: 2

pr_mode: auto
pr_base_branch: main
pr_reviewers: ["alice", "bob"]
pr_labels: ["auth", "phase-1"]

caps: {}
---

# 추가 컨텍스트

(deep-interview 중 사용자가 추가로 준 자유 형식 설명을 여기에 정리)
```
