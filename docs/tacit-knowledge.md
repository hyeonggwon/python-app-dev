# Tacit Knowledge — python-app-dev

확정된 결정 사항. 매 run마다 사용자에게 다시 묻지 않는다. 변경하려면 이 파일을 직접 수정하고, 의존하는 stage 프롬프트·검증 게이트·도구 화이트리스트를 함께 업데이트한다.

## 1. 도구 체인

### 1-1. 신규 프로젝트 (`mode = new`)
- 패키징: `uv` (`uv init`, `uv add`, `uv sync`, `uv run`)
- 린트/포맷: `ruff` (단일 도구로 둘 다 처리)
- 타입: `mypy` (기본 `strict = false`, deep-interview에서 토글)
- 테스트: `pytest` + `pytest-cov`
- 의존성 매니페스트: `pyproject.toml` 단일 소스
- 권장 디렉토리: `src/<package>/`, `tests/{unit,sanity}/`

### 1-2. 기존 프로젝트 (`mode = maintenance`) — 자동 감지
다음 우선순위로 도구를 감지하여 그대로 사용한다.

| 영역 | 감지 우선순위 |
|---|---|
| 패키징 | `uv.lock` → `poetry.lock` → `Pipfile.lock` → `requirements*.txt` → `setup.py` |
| 린트 | `[tool.ruff]` in `pyproject.toml` → `.ruff.toml` → `.flake8` → `pylintrc` → 표준 도입 |
| 포맷 | `[tool.ruff]` format → `[tool.black]` → 표준 도입 |
| 타입 | `[tool.mypy]` → `mypy.ini` → `[tool.pyright]` → 표준 도입 |
| 테스트 | `[tool.pytest.ini_options]` → `pytest.ini` → `tox.ini` → 표준 도입 |
| 작업 러너 | `Makefile` → `tasks.py` (invoke) → `noxfile.py` → `tox.ini` → 직접 실행 |

감지 결과는 `interview/spec.md`의 `detected_toolchain` 필드에 기록되고 deep-interview에서 사용자가 검토한다(잘못 감지된 경우 override 가능).

## 2. 검증 게이트 (0-2 결정론 영역)

orchestrator가 subprocess로 직접 실행, 결과를 `phase-{N}/gates/<name>.json`에 기록한다. 모든 게이트 결과 JSON은 다음 공통 스키마를 가진다:

```json
{
  "name": "lint",
  "command": "ruff check src/ tests/",
  "exit_code": 0,
  "status": "passed",
  "passed": true,
  "skipped": false,
  "started_at": "...",
  "finished_at": "...",
  "stdout_path": "phase-1/gates/lint.stdout.txt",
  "stderr_path": "phase-1/gates/lint.stderr.txt",
  "summary": {"errors": 0, "warnings": 3}
}
```

`status` 는 4-값 enum: `passed | failed | skipped_ok | skipped_fail`. orchestrator 의 라우팅은 이 `status` 를 우선 읽는다 (`gate_is_passing()`). `passed` 부울은 호환용 보조 필드로만 유지되며 단독으로 라우팅에 쓰면 `skipped_fail` 을 통과로 오분류한다.

| 게이트 | 명령 | 통과 조건 |
|---|---|---|
| `install` | `uv sync` (또는 감지된 등가 명령) | exit 0 |
| `lint` | `ruff check` | exit 0 |
| `format` | `ruff format --check` | exit 0 |
| `types` | `mypy src/` | exit 0 |
| `tests` | `pytest tests/unit -v --json-report` | 모든 테스트 통과 |
| `coverage` | `pytest --cov=src --cov-report=json` | `total >= threshold` (기본 70%) |
| `sanity` | `pytest tests/sanity -m sanity` | 모든 테스트 통과 |

LLM 리뷰 stage(code-review, design-review)는 이 JSON 파일들을 Inputs로 받아 **판단만** 수행한다. 테스트를 직접 돌리지 않는다.

## 3. Phase 분리 정책

기준: **MVP → 확장 (가치 우선순위)**.

- Phase 1 = 최소 기능으로 사용자 가치 달성하는 단위.
- Phase 2+ = 추가 가치, 비기능 강화, 확장 기능.
- Phase는 5개를 권장 상한으로 한다(많아지면 deep-interview에서 분할/통합 제안).
- 각 Phase는 독립적으로 PR이 가능해야 한다(머지 가능한 단위).

## 4. 자가 교정 정책

### 4-1. 린트/포맷/타입 (`lint-test` stage 내부)
- 자동 수정 가능한 항목은 도구로 처리 (`ruff check --fix`, `ruff format`).
- 수정 후 재검증, 통과할 때까지 반복. cap = **5회**.
- cap 도달 시 → `lint-test` stage 빠져나가 `design` 으로 루프백 (구조적 문제 가능성).

### 4-2. 테스트
- 실패 테스트 진단 → 코드 수정 → 재실행. cap = **5회**.
- cap 도달 시 → `design` 루프백 (cap=2). 그 cap도 도달 시 → escalation.

### 4-3. sanity test
- 실패 시 자가 교정 **하지 않는다**. 깊은 결함 가능성이 높음.
- 즉시 `design` 루프백, cap = **2회**. cap 도달 시 → escalation.

## 5. 코드리뷰 sub-agent 매핑

stage 내부에서 Task 도구로 호출(headless가 sub-agent 호출 가능 = 모드 B).

| stage | sub-agent | 역할 |
|---|---|---|
| `design` (review 부분) | `voltagent-qa-sec:architect-reviewer` | 설계 적절성·구조 리뷰 |
| `code-review` | `voltagent-qa-sec:code-reviewer` | 코드 품질·버그·디자인 패턴 |
| `code-review` (옵션) | `voltagent-qa-sec:security-auditor` | 보안 민감 프로젝트일 때만 |
| `sanity-test` (시나리오 작성) | `voltagent-qa-sec:qa-expert` | sanity 시나리오 도출 |

`security-auditor` 활성화는 deep-interview의 `security_review = on/off` 토글로 결정.

## 6. 코드리뷰 verdict 분류

`code-review` stage 의 권위 verdict 는 `phase-{N}/verdict.json` 의 `verdict` 필드 (orchestrator 가 `json.loads` 로 직접 파싱). `phase-{N}/review.md` 는 사람이 읽는 본문. 두 파일 모두 `STAGE_REQUIRED_AUX_OUTPUTS["code-review"]` 에 등록되어 있다. 라벨은 다음 4 종:

| verdict | 의미 | 루프백 | cap |
|---|---|---|---|
| `pass` | 통과, 다음 stage로 | (없음) | — |
| `minor` | 스타일/네이밍/사소한 버그 | `implement` | 3 |
| `major` | 설계 결함, 누락된 케이스 처리 | `design` | 2 |
| `critical` | 요구사항 오해, 근본 결함 | escalation | 즉시 |

각 결함마다 `location`, `description`, `suggestion`을 기록한다 (severity 는 issue 자체가 아니라 verdict 라벨로 표현된다 — 즉 위 표의 `pass/minor/major/critical` 한 단계로 묶어서 분류).

## 7. Git 규칙

### 7-1. 브랜치 네이밍
- 티켓 있음: `dev_matthew_<jira-ticket>_<NN>` (예: `dev_matthew_MAE1-123_01`)
- 티켓 없음: `<type>/<keyword>` (예: `feat/jwt-login`, `fix/null-token-bug`)
- `<NN>`은 같은 티켓에서 분리된 PR 순번. 첫 번째 = `01`. orchestrator가 원격 브랜치 목록을 조회해 다음 번호 자동 결정.

### 7-2. 커밋 메시지
티켓 있음:
```
<type>(<jira-ticket>): <subject>

<body (한글, 72자 줄바꿈)>

<footer>
```

티켓 없음(괄호·티켓 생략):
```
<type>: <subject>

<body>

<footer>
```

- `<type>`: feat, fix, docs, style, refactor, perf, test, chore
- `<subject>`: 50자 이내, 마침표 X, 동사원형
- `<body>`: 변경 이유, 주요 변경, 주의사항. 한글 OK
- `<footer>`: `Fixes:`, `Refs:`, `Co-authored-by:`, `Reviewed-by:`, `Signed-off-by:` 등

### 7-3. PR
- 자동 모드: orchestrator가 `git push -u origin <branch>` + `gh pr create --title ... --body-file <path>` 실행
- 수동 모드: `pr.md`만 생성, 사용자가 직접 push/create
- PR 본문에는 phase 산출물 링크(요구사항, 설계, sanity 결과) 포함

## 8. 임계치 기본값 (deep-interview에서 override 가능)

| 항목 | 기본 |
|---|---|
| 커버리지 임계치 | 70% |
| mypy strict | off |
| security_review | off |
| pytest 병렬 (-n auto) | off |
| sanity 시나리오 수 (phase당) | 1~3 |

## 9. 컨텍스트 권위 파일 (0-1)

stage 프롬프트의 Inputs 섹션에 다음을 **항상** 절대경로로 명시한다:

- `{HARNESS_ROOT}/docs/tacit-knowledge.md` — 본 파일
- `{HARNESS_ROOT}/docs/task-spec.md`
- `{run_dir}/interview/spec.md`
- 직전 stage 산출물(stage별로 결정, `stages.md` 참조)

`{HARNESS_ROOT}` 는 메인 세션이 skill 로드 직후 `git rev-parse --show-toplevel` 한 줄로 runtime resolve. orchestrator가 매 호출 직전 stage 프롬프트의 모든 `{...}` 토큰을 물질화하여 `{run_dir}/.prompts/<stage>.md` 로 떨어뜨린다.
