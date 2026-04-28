---
name: orchestrate-python-app-dev
description: 파이썬 프로그램 개발(신규/유지보수)용 multi-stage 하네스. 기획→요구사항→Phase분리→(Phase별)설계→브랜치→구현→린트/테스트→코드리뷰→sanity→문서화→PR 흐름을 모드 B로 진행한다. 메인 세션이 stage script를 호출하고 산출물 경로만 다음 stage로 넘긴다.
---

# Orchestrate: Python App Dev (Mode B)

이 skill 은 메인 세션이 **하네스 전체 흐름의 조율자** 역할을 할 때 따르는 절차다. 작업 본문(설계, 구현, 리뷰)은 매 stage 가 별도의 `claude -p` 호출로 처리하고, 메인 세션은 **경로만 다음 호출에 넘긴다**.

## 0. 먼저 — 모드 B 컨텍스트 규약 (위반 시 하네스 의미 상실)

- stage script 를 호출한 직후, 메인 세션은 **산출물 본문을 working memory 에서 즉시 폐기**하고 경로만 보관한다.
- "다음 stage 가 뭘 해야 하나" 를 결정하려고 산출물을 *읽지 마라*. orchestrator 가 인쇄한 `ORCHESTRATE_RESULT: {...}` JSON 한 줄로만 라우팅한다.
- 사용자 개입 지점에서 짧은 요약이 필요할 때만 본문을 **`Read` 한 줄로 발췌하고 즉시 폐기**.
- `outputs/<run-id>/` 아래의 모든 산출물 위치는 `docs/stages.md` 의 8-항목 정의에 박혀 있다. 파일이 어디에 떨어질지 추측하지 말고 그 표를 따른다.

## 1. 진입점

사용자가 `/orchestrate-python-app-dev` 를 호출하면 이 skill 이 로드된다. 다음 순서로 진행:

### 1-1. Harness root 결정

```
HARNESS_ROOT = $(git rev-parse --show-toplevel)
```

skill 본문의 모든 `{{HARNESS_ROOT}}` 는 위 값으로 치환해서 사용한다. 절대 install-time 으로 sed 치환하지 말 것 (CLAUDE.md 의 금지 변경).

### 1-2. Run 디렉토리 초기화

`init_run.py` 가 `outputs/<run-id>/` 와 `state.json` 의 초기 스키마를 만든다.

```bash
python3 {{HARNESS_ROOT}}/scripts/init_run.py
```

stdout 마지막 줄에 `INIT_RUN_DONE: <run_dir>` 가 인쇄된다. 그 경로를 `RUN_DIR` 로 보관.

### 1-3. Deep-interview (메인 세션이 직접 수행)

`python-app-dev-deep-interview` skill 을 참조해 사용자와 대화. 끝나면 `RUN_DIR/interview/spec.md` (YAML front-matter + 자유 본문) 를 작성하고 `state.json.interview_spec` 에 동일 내용을 머지.

deep-interview 종료 조건: 사용자가 "충분히 설명했다" 라고 명시적으로 확정.

## 2. Stage 디스패치 — 단일 패턴

모든 stage 호출은 이 한 줄 패턴이다.

```bash
python3 {{HARNESS_ROOT}}/scripts/orchestrate.py <stage> --run-dir "$RUN_DIR" [--phase <N>]
```

호출 직후 stdout 의 마지막 `ORCHESTRATE_RESULT: { ... }` JSON 한 줄을 파싱해 다음 액션을 결정. **다른 출력은 무시한다.**

### 2-1. ORCHESTRATE_RESULT decision 매트릭스

| `decision` | 의미 | 다음 액션 |
|---|---|---|
| `pass` | 정상 통과 | `next` 필드의 stage 호출 (없으면 다음 phase 또는 `delivery`) |
| `loopback` | stage 가 이전 stage 로 루프백 | `to` 필드의 stage 를 `--phase` 와 함께 재호출. orchestrator 가 이미 feedback.md 와 stage_outputs 를 정리해 둠 |
| `awaiting_user` | 사용자 개입 토글이 ON 인 stage 의 통과 후 승인 대기 | `schema` 필드를 사용자에게 보여주고 응답 받기 → `state.json.user_input` 에 머지 → 같은 stage 를 `--resume` 으로 재호출 |
| `escalated` | cap 초과 / 누락 marker / 검증 실패 | run 정지. `RUN_DIR/escalation.md` 와 함께 사용자에게 트리거 보고. `escalation.decision.md` 작성 받아 처리 |

### 2-2. 정상 시퀀스

Run-level (phase 없음, 순서대로):

1. `planning` → 통과 시 `requirements`
2. `requirements` → 통과 시 `phase-split`
3. `phase-split` → 산출물(`phases.md`) 의 `phases:` 배열 길이 N 을 메인 세션이 한 번 읽어 `state.json.total_phases` 에 박고 즉시 폐기

Phase-level (각 phase 마다 순서대로, `--phase N` 필수):

4. `design`
5. `branch-create`
6. `implement`
7. `lint-test`
8. `code-review`
9. `sanity-test`
10. `document`
11. `pr-create` — PR 본문 (`pr.md`) 만 작성. push/gh 는 하지 않는다.
12. `pr-publish` — `pr_mode==auto` 에서만 실행. `git push` + `gh pr create` 후 `pr-url.txt` 기록. `pr_mode==manual` 이면 orchestrator 가 자동으로 건너뛴다 (사용자가 직접 push).

모든 phase 가 끝나면:

13. `delivery` (run-level)

### 2-3. Phase 별 루프 분기

`code-review` 의 `verdict` 에 따라 orchestrator 가 자동으로 분기한다 — 메인 세션은 `ORCHESTRATE_RESULT.decision == "loopback"` 만 따라가면 된다.

| code-review verdict | loopback 대상 | 정리되는 산출물 |
|---|---|---|
| `pass` | (없음) → `sanity-test` | — |
| `minor` | `implement` | implement 이후 모든 phase 산출물 (lint-test/code-review/sanity-test/document/pr-create/pr-publish) |
| `major` | `design` | design 이후 모든 phase 산출물 (branch-create 도 — 같은 브랜치는 유지되지만 새 commit 으로 덮음) |
| `critical` | escalation | — |

`sanity-test` 실패도 `design` 으로 루프백.

### 2-4. 사용자 개입 지점 (5 토글)

`spec.md.interventions` 에 따라 `awaiting_user` 가 발생할 수 있는 stage:

| 토글 키 | stage | 기대 입력 schema |
|---|---|---|
| `planning` | `planning` 후 | `decision: approve|reject|revise`, `feedback: string` |
| `requirements` | `requirements` 후 | `decision`, `feedback`, `add_requirements`, `remove_ids` |
| `phase_split` | `phase-split` 후 | `decision`, `feedback`, `merge_phases`, `split_phase`, `reorder` |
| `design_per_phase` | 각 phase `design` 후 | `decision: approve|revise`, `feedback` |
| `pr_per_phase` | 각 phase `pr-create` 후 | `decision: approve|revise`, `feedback` |

기본값: planning/requirements/phase_split/pr_per_phase 는 ON, design_per_phase 는 OFF.

승인 시 `state.json.user_input` 에 사용자 응답을 머지하고 같은 stage 를 `--resume` 으로 호출 → 통과 처리. revise 면 stage 를 재호출 (orchestrator 가 feedback.md 에 사용자 피드백 prepend).

## 3. Escalation 처리

`decision == "escalated"` 가 오면 즉시 정지. 사용자에게:

```
ESCALATED: <trigger>
   stage: <stage>, phase: <phase>
   detail: see {{HARNESS_ROOT}}/outputs/<run-id>/escalation.md
   please write {{HARNESS_ROOT}}/outputs/<run-id>/escalation.decision.md with:
       decision: continue | abort | resume_from_<stage>
       feedback: |
         <free text>
```

사용자가 decision 파일을 쓰면 메인 세션이 읽어서:

- `continue` — 같은 stage 를 `--resume` 으로 재호출 (cap 카운터는 그대로). cap 이 다시 초과되면 같은 trigger 로 또 escalate.
- `abort` — `state.json.status = "aborted"` 로 마크하고 `delivery` 호출 → 종료.
- `resume_from_<stage>` — 해당 stage 부터 재시작. orchestrator 가 그 사이의 산출물 정리는 하지 않으므로 메인 세션이 사용자 의도를 다시 확인할 것.

## 4. Run 종결

마지막 phase 의 `pr-create` 가 통과하고 (또는 abort 결정), 메인 세션이 `delivery` 를 호출. orchestrator 가 종결 시점에 한 번:

- `outputs/.index.jsonl` 한 줄 append
- 모든 escalation 트리거 / cap 도달 패턴이 `delivery.md` 의 "암묵지 업데이트 후보" 섹션에 **제안만** 으로 들어간다 (자동 수정 금지)

## 5. 권위 파일 (이 skill 도, stage 도 모두 이 정본을 따른다)

- `{{HARNESS_ROOT}}/docs/task-spec.md` — 작업 명세
- `{{HARNESS_ROOT}}/docs/tacit-knowledge.md` — 도구 체인, 자가 교정 정책, 코드리뷰 매핑, git 규칙, 임계치
- `{{HARNESS_ROOT}}/docs/interview-guide.md` — 매 run 에 물을 변수
- `{{HARNESS_ROOT}}/docs/stages.md` — stage 8-항목 정의 + 라우팅 표 + 카운터

이 4 개 외의 정보를 "관행" 으로 가정하지 말고, 이 파일들에 박힌 내용을 따른다. 필요하면 이 4 개를 수정해서 PR (사람이 결정).

## 6. 자가 진단

각 stage 호출 후 다음을 확인 (실패 시 즉시 정지하고 사용자에게 보고):

- stdout 에 `ORCHESTRATE_RESULT: { ... }` 가 한 줄 있다
- `decision` 필드가 4개 enum 중 하나다 (`pass | loopback | awaiting_user | escalated`). abort 는 사용자가 선택하는 escalation decision 이지 orchestrator 가 emit 하는 값이 아니다 — abort 처리 후 호출된 `delivery` 는 `decision: pass, final_status: aborted` 로 응답한다.
- 그 외에는 `state.json` 도 `escalation.md` 도 직접 편집하지 않는다 (orchestrator 만 쓴다)
