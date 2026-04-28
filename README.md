**한국어** | [English](README.en.md)

# python-app-dev

> 파이썬 앱을 신규 개발/유지보수할 때 Claude Code 메인 세션을 멀티-stage 하네스로 구동해 컨텍스트 오염 없이 기획→PR 흐름을 자동 분기시키는 도구.

## Initialize

```bash
git clone git@github.com:hyeonggwon/python-app-dev.git
cd python-app-dev
./install.sh
```

`install.sh` 가 하는 일:
- `git core.hooksPath = .githooks` (`validate_harness.py` 강제)
- `python3 / claude / git` 필수 확인, `uv / gh` 는 옵션 경고
- `validate_harness.py` 1회 실행으로 정합성 확인

## Quick Start

Claude Code 메인 세션에서:

```
/orchestrate-python-app-dev
```

흐름:
1. **deep-interview** — 모드(신규/유지보수), 5개 사용자 개입 토글, cap, PR 모드를 묻고 `spec.md` 작성
2. **run-level** — `planning` → `requirements` → `phase-split`
3. **phase-level** (각 phase 마다) — `design` → `branch-create` → `implement` → `lint-test` → `code-review` → `sanity-test` → `document` → `pr-create`
4. **delivery** — 종결 + 암묵지 업데이트 후보 제안

## How It Works

**모드 B**: 메인 세션은 stage 본문을 *읽지 않는다*. orchestrator 가 stage 를 별도 `claude -p` 로 띄우고, 메인 세션은 마지막 한 줄 `ORCHESTRATE_RESULT: {decision, ...}` JSON 만 보고 라우팅.

| decision | 다음 액션 |
|---|---|
| `pass` | 다음 stage 호출 |
| `loopback` | code-review verdict / sanity 실패 시 이전 stage 재시작 (산출물 자동 정리) |
| `awaiting_user` | 토글이 ON 인 stage 의 통과 후 사용자 승인 대기 |
| `escalated` | cap 초과 / marker 누락 / 검증 실패 → 정지 후 사용자 결정 |

각 stage 는 `--allowed-tools` 로 도구 경계가 강제되고, `lint-test` 는 `run_gate.py` 의 임계치를 통과해야 한다.

## Architecture

```
.
├── CLAUDE.md                  # 하네스 유지보수자용 가이드
├── install.sh
├── docs/                      # 정본 (수정 시 PR)
│   ├── task-spec.md
│   ├── tacit-knowledge.md     # 도구 체인 / 자가 교정 / 임계치 정책
│   ├── interview-guide.md     # 매 run 변수
│   └── stages.md              # stage 8-항목 정의 + 라우팅
├── .claude/skills/            # 메인 세션이 로드할 skill 2종
├── scripts/
│   ├── orchestrate.py         # 단일 진입점 — stage 1회 실행 + ORCHESTRATE_RESULT 출력
│   ├── init_run.py            # outputs/<run-id>/ + state.json 초기화
│   ├── run_gate.py            # lint-test 게이트 평가
│   ├── detect_toolchain.py    # uv / poetry / pip 자동 감지
│   ├── validate_harness.py    # pre-commit 정합성 검사기
│   ├── config.yaml            # cap / 임계치 기본값
│   └── prompts/               # 12 stage 프롬프트 정본
└── .githooks/pre-commit       # validate_harness.py 강제
```

## 생성되는 파일

매 run 은 `outputs/<run-id>/` 아래에 격리:

```
outputs/<run-id>/
├── state.json                 # 단일 권위 상태 — orchestrator 만 수정
├── interview/spec.md          # deep-interview 결과
├── effective_thresholds.json  # config + spec override 머지 스냅샷
├── planning.md / requirements.md / phases.md
├── phase-N/
│   ├── design.md, branch-create.md, implement.md, sanity-test.md, document.md, pr-create.md
│   ├── lint-test/             # ruff / mypy / pytest 결과 JSON
│   ├── review.md, verdict.yaml
│   └── feedback.md            # 루프백 시 누적
├── workspace/                 # 사용자 프로젝트의 .git (분리됨)
├── escalation.md / escalation.decision.md  # 발생 시
└── delivery.md                # 종결 — 암묵지 업데이트 후보 포함
```

`outputs/` 는 `.gitignore` 로 하네스 git 에서 제외. `outputs/.index.jsonl` 만 run 종결 시 한 줄 append.

## Development

이 *하네스 자체* 를 수정한다면 `CLAUDE.md` 가 권위. 핵심:

- `docs/` ↔ stage prompt ↔ `STAGE_TOOLS` / `STAGE_DIRS` 는 한 세트 — 한쪽만 고치면 `validate_harness.py` 가 차단
- pre-commit 우회 금지 (`--no-verify` X)
- 절대경로 (`/home/...`) 박지 마라 — `{{HARNESS_ROOT}}` / `{run_dir}` 토큰만
- `tacit-knowledge.md` / cap 자동 수정 금지 — `delivery.md` 후보 섹션에 제안만, 사람이 결정
- 하네스 git ↔ workspace git 분리 — workspace 는 `ensure_workspace_repo()` 가 검증

새 stage 추가는 `CLAUDE.md` 의 4단계 체크리스트 따라.

## Requirements

- Python 3.10+
- [Claude Code CLI](https://claude.com/claude-code)
- git
- (옵션) `uv` — 신규 프로젝트의 기본 패키지 도구
- (옵션) `gh` — `pr_mode=auto` 사용 시 필요
