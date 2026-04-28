# Stages — python-app-dev

## 모드 결정: 모드 B (복잡)

**근거**:
1. **단계 수가 동적**: phase 수가 `phase-split` 산출물에 따라 결정됨(MVP→확장 분할 결과).
2. **단계 간 루프(back-edge)가 정상 경로에 존재**: `code-review` verdict가 `major`이면 `design`으로, `sanity-test` 실패면 `design`으로 돌아감 — 단일 stage 내부 재시도가 아닌 stage 간 back-edge.
3. **조건부 분기**: `mode = new` vs `maintenance`에 따라 첫 번째 phase의 `branch-create`/`implement` 시작 지점이 다름.

## 컨텍스트 규약 — 경로는 유지, 본문은 폐기

메인 세션은 stage script를 호출한 뒤 **산출물 파일 경로만** 다음 stage 호출에 넘긴다. 본문은 working memory에서 즉시 폐기. 사용자 개입 지점에서 짧은 요약이 필요하면 그때만 본문을 읽고 즉시 폐기. 이 규칙은 orchestrating skill 본문에도 박혀 있다.

## Orchestrator 호출 패턴

단일 entrypoint:

```
scripts/orchestrate.py <stage> [--phase N] [--resume]
```

- run-level stage: `--phase` 생략 (`planning`, `requirements`, `phase-split`, `delivery`)
- phase-level stage: `--phase N` 필수
- `--resume`: 사용자 개입 후 메인 세션이 재호출할 때

각 stage 호출은:
1. `state.json` 갱신 (`current_stage`)
2. orchestrator가 프롬프트 토큰 물질화 → `{run_dir}/.prompts/<stage>[--p<N>].md`
3. `claude -p` headless 호출 with stage별 `--allowed-tools`
4. 산출물 검증 (marker 라인 + aux 파일 존재 + verdict shape-check)
5. 결정론적 게이트 실행 (해당하는 stage)
6. 라우팅 결정 (PASS / 자기 루프백 / 다른 stage 루프백 / 사용자 개입 / 에스컬레이션) 후 `state.json` 업데이트하고 종료

## Stage 디렉토리 단일 진실

```python
STAGE_DIRS = {
  "planning":      "",               # run_dir 직속 (planning.md)
  "requirements":  "",
  "phase-split":   "",
  "design":        "phase-{N}",
  "branch-create": "phase-{N}",
  "implement":     "phase-{N}",
  "lint-test":     "phase-{N}",
  "code-review":   "phase-{N}",
  "sanity-test":   "phase-{N}",
  "document":      "phase-{N}",
  "pr-create":     "phase-{N}",
  "pr-publish":    "phase-{N}",
  "delivery":      "",
}
```

모든 경로 계산은 이 맵에서 파생된다. Stage 이름 표기는 kebab-case로 단일화 (short/long-form 혼용 금지).

---

# Run-Level Stages

## R1. `planning`

| 항목 | 값 |
|---|---|
| **이름** | `planning` |
| **Inputs** | `{HARNESS_ROOT}/docs/task-spec.md`, `{HARNESS_ROOT}/docs/tacit-knowledge.md`, `{run_dir}/interview/spec.md`, `{run_dir}/planning/feedback.md` (있으면) |
| **Outputs** | `{run_dir}/planning.md` |
| **프롬프트 스케치** | `interview/spec.md`의 `goal_summary`/`out_of_scope`/(`current_vs_expected`)를 읽고, **목표 / 사용자 가치 / 핵심 가정 / 범위 / 비범위** 5개 섹션으로 구성된 product brief를 한국어로 작성. `mode == maintenance`이면 "현재 동작 vs 기대 동작" 섹션 추가. 마지막 줄 `PLANNING_DONE: {run_dir}/planning.md` |
| **참조 skill/sub-agent** | (없음) |
| **검증 기준** | (a) `planning.md` 존재 (b) 5개 섹션 헤더 모두 존재 (c) marker 라인 |
| **루프백 + cap** | 사용자 개입 ① 토글 시 `feedback`이 적히면 자기 루프백 (cap=2). cap 도달 시 escalation |
| **사용자 개입** | ① `interventions.planning == on`이면 직후. 기대 입력: `approve` / `reject(이유)` / `revise(피드백)` |
| **도구 (`STAGE_TOOLS`)** | `Read, Glob, Grep, WebSearch, WebFetch, Write` |

## R2. `requirements`

| 항목 | 값 |
|---|---|
| **Inputs** | task-spec, tacit-knowledge, `{run_dir}/interview/spec.md`, `{run_dir}/planning.md`, `{run_dir}/requirements/feedback.md` (있으면) |
| **Outputs** | `{run_dir}/requirements.md` |
| **프롬프트 스케치** | `planning.md` 기반으로 사용자 스토리(`US-001` ...)와 비기능 요구사항(`NFR-001` ...) 도출. 각 항목에 `id`, `title`, `description`, `priority` (`must`/`should`/`could`), `acceptance_criteria` (Given-When-Then) 포함. YAML front-matter로 `{requirements: [...]}` 헤더 + 마크다운 본문. 마지막 줄 `REQUIREMENTS_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) 파일 존재 (b) front-matter `requirements` 배열 ≥ 1 (c) 모든 항목이 `id`/`priority`/`acceptance_criteria` 보유 (d) marker |
| **루프백 + cap** | 사용자 개입 ② 시 자기 루프백 (cap=2) |
| **사용자 개입** | ② `interventions.requirements == on`이면 직후 |
| **도구** | `Read, Glob, Grep, Write` |

## R3. `phase-split`

| 항목 | 값 |
|---|---|
| **Inputs** | task-spec, tacit-knowledge, spec, planning, requirements, `{run_dir}/phase-split/feedback.md` (있으면) |
| **Outputs** | `{run_dir}/phases.md` |
| **프롬프트 스케치** | `tacit-knowledge.md §3` 정책(MVP→확장)에 따라 요구사항을 phase로 분할. 각 phase는 `id`(1, 2, ...), `title`, `goal`, `included_requirement_ids` (US/NFR id 배열), `out_of_phase` 보유. phase 수 ≤ 5. front-matter `{phases: [...]}`. 마지막 줄 `PHASE_SPLIT_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) 파일 존재 (b) `phases` 배열 ≥ 1, ≤ 5 (c) `requirements.md`의 모든 `must` priority 항목이 어떤 phase에 매핑됨 (d) phase id가 1부터 연속 (e) marker |
| **루프백 + cap** | 사용자 개입 ③ 시 자기 루프백 (cap=2) |
| **사용자 개입** | ③ `interventions.phase_split == on`이면 직후 |
| **도구** | `Read, Write` |

---

# Phase-Level Stages (각 phase마다 반복)

## P1. `design`

| 항목 | 값 |
|---|---|
| **Inputs** | task-spec, tacit-knowledge, spec, planning, requirements, phases, (이전 phase가 있으면) `phase-{N-1}/{design,implementation}.md`, (workspace) `src/` 구조, `phase-{N}/design/feedback.md` (재시도면) |
| **Outputs** | `phase-{N}/design.md` (front-matter에 `verdict` 필드: `pass`/`needs_revision`) |
| **프롬프트 스케치** | 해당 phase의 요구사항을 구현하기 위한 (1) 모듈 구조 (2) 공개 API/CLI 시그니처 (3) 데이터 모델 (4) 외부 의존 (5) 테스트 전략 + sanity 시나리오 후보 (6) 위험·대안. 작성 후 `architect-reviewer` sub-agent를 Task로 호출, 그 verdict 결과를 front-matter `verdict`에 박는다. 마지막 줄 `DESIGN_DONE: ...` |
| **참조** | `voltagent-qa-sec:architect-reviewer` (Task 도구로 호출, self-review) |
| **검증** | (a) 파일 존재 (b) 6개 섹션 헤더 (c) front-matter `verdict ∈ {pass, needs_revision}` (d) marker. orchestrator가 `verdict == needs_revision`이면 자기 루프백 |
| **루프백 + cap** | self verdict `needs_revision` → 자기 루프백 (cap=2). 도달 시 escalation |
| **사용자 개입** | ④ `interventions.design_per_phase == on`이면 직후 |
| **도구** | `Read, Glob, Grep, Write, Task` |

## P2. `branch-create`

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge `§7-1`, spec (`jira_ticket`, `change_kind`), phases, design |
| **Outputs** | `phase-{N}/branch.txt` (한 줄: 브랜치명) |
| **프롬프트 스케치** | (a) 원격 브랜치 목록 조회 (b) 티켓 있으면 `dev_matthew_<ticket>_<NN>` 다음 번호 결정 (c) 티켓 없으면 `<change_kind>/<keyword>` (keyword는 design title에서 도출, kebab-case) (d) `git checkout -b <name>` (e) `branch.txt`에 기록. **idempotent**: 이미 같은 이름 브랜치가 있으면 checkout만. 마지막 줄 `BRANCH_CREATE_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) `branch.txt` 존재 (b) 그 이름의 브랜치가 현재 HEAD (c) marker |
| **루프백 + cap** | 실패 시 escalation (자기 재실행 없음 — 결정적 단계라 LLM 재시도가 도움이 되지 않음) |
| **사용자 개입** | (없음) |
| **도구** | `Read, Write, Bash(git checkout:*, git branch:*, git switch:*, git ls-remote:*, git for-each-ref:*, git rev-parse:*)` |

## P3. `implement`

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge, spec, phases, `phase-{N}/{design,branch}.txt`, `phase-{N}/implement/feedback.md` (재시도면), `phase-{N}/review.md` (재시도면) |
| **Outputs** | `phase-{N}/implementation.md` (변경 파일 목록 + 커밋 SHA 목록) + 코드 변경 (`workspace/` 또는 사용자 프로젝트) |
| **프롬프트 스케치** | design.md를 따라 코드와 unit test 작성. 작은 논리 단위로 `git add` + `git commit` 수행 (커밋 메시지는 `tacit-knowledge.md §7-2`). 작성 후 `git log --oneline`로 SHA 목록을 `implementation.md`에 기록. 마지막 줄 `IMPLEMENT_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) `implementation.md` 존재 (b) 파일 안에 ≥ 1 커밋 SHA (c) `git diff <base>..HEAD` 가 비어있지 않음 (d) marker |
| **루프백 + cap** | code-review minor 시 자기 루프백 (cap=3, `code_review_minor__phase_{N}`). install/lint/format/types/tests/coverage 게이트는 후속 `lint-test` stage 의 자기교정 루프(cap=5)에서 처리 — implement 단계에는 자체 게이트 게이트백이 없음 |
| **사용자 개입** | (없음) |
| **도구** | `Read, Write, Edit, Glob, Grep, Bash(uv:*, ruff:*, mypy:*, pytest:*, git add:*, git commit:*, git status, git log:*, git diff:*, git rev-parse:*)` |

## P4. `lint-test`

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge, implementation, `phase-{N}/lint-test/feedback.md` (재시도면) |
| **Outputs** | `phase-{N}/gates/{install,lint,format,types,tests,coverage}.json` + `phase-{N}/lint-test.md` (자가 교정 로그) |
| **프롬프트 스케치** | orchestrator가 먼저 6개 게이트를 실행 → 결과 JSON을 LLM에 input으로 전달 → 실패 게이트 발견 시 LLM이 진단·수정·재커밋 → orchestrator가 다시 게이트 실행. 통과할 때까지 반복(cap 내부). LLM은 `lint-test.md`에 각 시도의 진단·수정 요약을 누적 append. 마지막 줄 `LINT_TEST_DONE: ...` |
| **참조** | (없음) |
| **검증 (orchestrator 직접)** | 모든 게이트 JSON `passed=true`. coverage는 `total >= effective_thresholds.coverage_threshold` |
| **루프백 + cap** | 자기 내부 자가교정 cap=5. 도달 시 → `design` 루프백 (cap=2). 그 후 escalation |
| **사용자 개입** | (없음) |
| **도구** | `Read, Edit, Write, Bash(uv:*, ruff:*, mypy:*, pytest:*, git add:*, git commit:*)` |

## P5. `code-review`

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge, design, implementation, `phase-{N}/gates/*.json` |
| **Outputs** | `phase-{N}/review.md` + `phase-{N}/verdict.json` (필수 aux) |
| **프롬프트 스케치** | `voltagent-qa-sec:code-reviewer`를 Task로 호출. spec의 `security_review == true`면 `security-auditor`도 호출. 두 verdict를 종합해 `verdict.json` 작성: `{verdict, loop_target, issues_critical, issues_major, issues_minor, summary}`. 게이트 JSON을 입력으로 받지만 **테스트를 직접 돌리지 않는다**. 마지막 줄 `CODE_REVIEW_DONE: ...` |
| **aux output 매핑** | `STAGE_REQUIRED_AUX_OUTPUTS["code-review"] = ["review.md", "verdict.json"]` |
| **참조** | `voltagent-qa-sec:code-reviewer`, `voltagent-qa-sec:security-auditor` (옵션) |
| **검증 (orchestrator)** | (a) `verdict.json` 존재 (b) shape-check: `verdict ∈ {pass, minor, major, critical}`, `loop_target ∈ {none, implement, design, escalation}`, `issues_*`는 list (c) 라벨↔loop_target 정합성: `pass→none`, `minor→implement`, `major→design`, `critical→escalation` (위반 시 escalation) (d) marker (e) 모든 게이트 passed인데 verdict ≠ pass면 진행, 게이트 fail인데 verdict=pass면 escalation (객관 우선) |
| **루프백 + cap** | `minor` → implement (cap=3). `major` → design (cap=2). `critical` → escalation 즉시. cap 별 비교는 `state.counters.code_review_minor__phase_{N}` / `code_review_major__phase_{N}` |
| **사용자 개입** | (없음) |
| **도구** | `Read, Write, Task, Glob, Grep` (Read-only on 검사 대상; Write는 자기 산출물용) |

## P6. `sanity-test`

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge, requirements, design, `phase-{N}/sanity-test/feedback.md` (재시도면) |
| **Outputs** | `phase-{N}/sanity.md` (시나리오 + 실행 결과) + `phase-{N}/gates/sanity.json` |
| **프롬프트 스케치** | (1) `qa-expert` Task로 phase 핵심 골든패스 시나리오 도출 (`spec.sanity_scenarios_per_phase`개) (2) `tests/sanity/` 아래 `pytest` 마커 `sanity`로 통합 테스트 작성 (3) `pytest -m sanity` 실행 (orchestrator가 게이트로) (4) 결과를 `sanity.md`에 기록. 마지막 줄 `SANITY_TEST_DONE: ...` |
| **참조** | `voltagent-qa-sec:qa-expert` |
| **검증 (orchestrator)** | `gates/sanity.json passed=true`. shape-check + marker |
| **루프백 + cap** | fail → `design` (cap=2). 자가 교정하지 않음 |
| **사용자 개입** | (없음) |
| **도구** | `Read, Write, Edit, Task, Bash(uv:*, pytest:*, git add:*, git commit:*)` |

## P7. `document`

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge, requirements, design, implementation, sanity |
| **Outputs** | `phase-{N}/docs-changes.md` + 프로젝트 내 문서 변경 (README, docstring, `docs/`, ADR 등) |
| **프롬프트 스케치** | 변경된 코드·새로 추가된 인터페이스에 따라 (1) README 업데이트 (2) 공개 API docstring (3) 필요시 ADR 추가. 모든 변경 파일은 commit. 마지막 줄 `DOCUMENT_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) `docs-changes.md` 존재 (b) marker. 문서 변경이 없는 phase 라면 `docs-changes.md` 본문에 그 사유를 기록한다 (orchestrator 는 본문 형식을 강제하지 않음 — 사람이 PR 리뷰 시 검토) |
| **루프백 + cap** | 실패 시 escalation (자기 재실행 없음) |
| **사용자 개입** | (없음) |
| **도구** | `Read, Write, Edit, Glob, Grep, Bash(git add:*, git commit:*, git status, git diff:*)` |

## P8. `pr-create` (draft only)

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge, spec, design, implementation, review, sanity, docs-changes, branch.txt |
| **Outputs** | `phase-{N}/pr.md` (제목은 본문 선두의 `<!-- pr-title: ... -->` HTML 코멘트에 박힘) |
| **프롬프트 스케치** | PR 본문만 작성한다. `## Summary`, `## 변경 사항`, `## Phase 산출물`, `## Test plan`, `Refs:` 푸터. **`git push`/`gh pr create` 는 이 stage 에서 실행하지 않는다** — tool whitelist 도 그것을 허용하지 않는다. 실제 push/PR 생성은 사용자 개입 ⑤ 통과 후 `pr-publish` stage 에서 일어난다. 마지막 줄 `PR_CREATE_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) `pr.md` 존재 + 본문 형식 (b) marker |
| **루프백 + cap** | 사용자 개입 ⑤ revise 시 자기 루프백 (cap=2, `pr_create_revise__phase_{N}`). 그 외 실패는 escalation |
| **사용자 개입** | ⑤ `interventions.pr_per_phase == on`이면 **직후** (post-stage 승인). 기대 입력: `approve` / `revise(피드백)`. 승인 후 `pr_mode==auto` 면 `pr-publish` stage 진행, `pr_mode==manual` 이면 `pr-publish` 를 건너뛰고 다음 phase 또는 delivery 로 이동 |
| **도구** | `Read, Write` |

## P9. `pr-publish` (auto 모드 전용 — manual 에선 skip)

| 항목 | 값 |
|---|---|
| **Inputs** | tacit-knowledge, spec, branch.txt, pr.md |
| **Outputs** | `phase-{N}/pr-url.txt` |
| **프롬프트 스케치** | (1) `pr.md` 선두의 `<!-- pr-title: ... -->` 코멘트에서 제목 추출 (2) HEAD 가 `branch.txt` 와 일치하는지 확인 (3) `git push -u origin <branch>` (4) `gh pr create --title <title> --body-file pr.md --base <base> [--reviewer ...] [--label ...]` (5) 출력된 PR URL 을 `pr-url.txt` 에 기록. 재시도 (예: backtrack 후 재진입) 로 PR 이 이미 존재하면 `gh pr view <branch> --json url --jq .url` 로 회수해 기록 — **PR 닫고 새로 만들지 않는다**. 마지막 줄 `PR_PUBLISH_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) `pr-url.txt` 존재 + 유효한 GitHub PR URL 정규식 (b) marker |
| **라우팅** | `pr_mode == manual` 이면 orchestrator 가 이 stage 를 호출하지 않는다 (`_default_next_stage` 에서 `pr-create` 다음을 `next-phase-or-delivery` 로 단락) |
| **루프백 + cap** | 자기 재실행 카운터 없음. 실패 시 escalation |
| **사용자 개입** | (없음 — 사용자 검토는 ⑤ 에서 끝) |
| **도구** | `Read, Write, Bash(git push:*, git rev-parse:*, gh pr:*, gh repo:*)` |

---

# Run-End Stage

## E1. `delivery`

| 항목 | 값 |
|---|---|
| **Inputs** | 모든 `phase-*/{design,implementation,review,sanity,docs-changes,pr,verdict}.{md,yaml}`, 모든 `gates/`, `escalation.md` (있으면) |
| **Outputs** | `{run_dir}/delivery.md` + `outputs/.index.jsonl`에 한 줄 append |
| **프롬프트 스케치** | (1) phase별 요약 (요구사항, 핵심 결정, PR URL) (2) 전체 게이트 결과 통계 (3) **암묵지 업데이트 후보** 섹션: 반복 지적된 패턴, 자주 cap 도달한 게이트, deep-interview 토글의 효과적 조합 — **제안만**, 자동 수정 금지 (4) 종결 상태 (`success` / `escalated`). orchestrator가 `outputs/.index.jsonl`에 `{run_id, mode, jira_ticket, project_kind, final_status, total_phases, escalation_triggers: [...]}` **단 한 줄** append. 마지막 줄 `DELIVERY_DONE: ...` |
| **참조** | (없음) |
| **검증** | (a) `delivery.md` 존재 (b) 4개 섹션 헤더 (c) `.index.jsonl`에 본 run의 라인이 정확히 1개 (d) marker |
| **루프백 + cap** | (없음, run 종료) |
| **사용자 개입** | (없음) |
| **도구** | `Read, Write, Glob, Grep, Bash(git log:*, git rev-parse:*)` |

---

# 사용자 개입 입력 형태 (모드 B 사전 고정)

각 개입 지점에서 메인 세션이 사용자에게 받는 입력 스키마:

| 지점 | 형태 |
|---|---|
| ① planning | `decision: approve\|reject\|revise`, `feedback: \|<텍스트>` |
| ② requirements | 위와 동일 + `add_requirements: [{title, priority, ...}]`, `remove_ids: [...]` |
| ③ phase-split | 위 + `merge_phases: [[1,2]]`, `split_phase: 1`, `reorder: [2,1,3]` |
| ④ design (옵션) | `decision: approve\|revise`, `feedback: \|<텍스트>` |
| ⑤ pr-create | `decision: approve\|revise`, `feedback: \|<텍스트>` |

메인 세션은 사용자 답변을 `{run_dir}/state.json` 의 `user_input` 키에 머지(예: `{"decision":"approve"}` 또는 `{"decision":"revise","feedback":"..."}` + 위 표의 stage 별 추가 키)한 뒤 `orchestrate.py <stage> --resume` 으로 같은 stage 를 재호출한다. orchestrator 는 `_handle_resume` 에서 이 키를 소비하고 비운 다음 라우팅한다 (별도 `decision.md` 파일은 쓰지 않는다).

---

# 라우팅 표 (orchestrator 내부)

```python
VALID_VERDICT_LABELS = {"pass", "minor", "major", "critical"}
VALID_LOOP_TARGETS  = {"none", "implement", "design", "escalation"}

VERDICT_TO_LOOP = {
  "pass":     {"none"},
  "minor":    {"implement"},
  "major":    {"design"},
  "critical": {"escalation"},
}
# verdict ∈ VERDICT_TO_LOOP[verdict]에 loop_target가 들어있지 않으면 escalation
```

라우팅 layer가 객관 게이트와 verdict의 모순(객관 fail + verdict pass)을 발견하면 즉시 escalation.

---

# Cap 카운터 (`state.json.counters`)

```json
{
  "lint_test_self_correct__phase_{N}": 0,
  "code_review_minor__phase_{N}": 0,
  "code_review_major__phase_{N}": 0,
  "sanity__phase_{N}": 0,
  "design_arch_self__phase_{N}": 0,
  "design_revise__phase_{N}": 0,
  "pr_create_revise__phase_{N}": 0,
  "planning_revise": 0,
  "requirements_revise": 0,
  "phase_split_revise": 0,
  "total_stages": 0
}
```

(`branch-create`, `implement`, `document`, `pr-create`, `pr-publish` 자기 재실행 카운터는 두지 않는다 — 위 stage 들은 자기 루프백 루트가 없고, 실패 시 곧장 escalation 으로 빠진다. `pr-create` 의 `pr_create_revise` 는 user-revise 누적 카운터로 별도.)

`total_stages`는 runaway cap (전역). 도달 + 마지막 verdict=pass면 정상 종료, 도달 + 비-pass면 escalation.

Backtrack 시 동작:
- **stage 산출물**: target stage 와 그 이후 stage 들의 `STAGE_OWNED_PATTERNS` (LLM 산출물 + 해당 stage 가 소유한 게이트 JSON 포함, 예: lint-test 의 `gates/install.json`, sanity-test 의 `gates/sanity.json`)을 모두 unlink. target 자체도 청소하는 이유: 이전 라운드의 primary 산출물 (예: `verdict: pass` 가 박힌 design.md) 이 그대로 남으면 LLM 이 덮어쓰지 못한 경우 post-stage 라우팅이 그 stale 값으로 단락된다. user-revise 경로도 동일하게 target 의 `STAGE_OWNED_PATTERNS` 를 청소한다 (두 경로 동일 의미).
- **stage_outputs**: target 과 그 이후 stage 의 항목 모두 pop.
- **in-stage 카운터**: target 과 cleared stage 들의 `IN_STAGE_RETRY_COUNTERS` 만 0 으로 리셋 (verdict 누적 카운터 `code_review_minor/major`, user-revise 카운터는 cumulative 유지).
- **verdict_history**: `code-review-*` 가 트리거한 backtrack 일 때만 마지막 엔트리 1개 pop. `lint-test-cap` 이나 `sanity-fail` backtrack 은 verdict_history 를 건드리지 않는다 (그 두 경로는 verdict 를 추가하지 않으므로 pop 하면 직전 phase 의 무관한 verdict 를 잃게 된다).
- **공유 feedback.md**: phase-level stage 들의 `feedback.md` 는 phase 디렉토리 공유라 `STAGE_OWNED_PATTERNS` 의 어느 항목에도 들어있지 않다. 즉 backtrack/revise 청소 후에도 살아남고, 그 직후 새 피드백이 append 된다.
