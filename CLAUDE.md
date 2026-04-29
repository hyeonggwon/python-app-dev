# python-app-dev — 하네스 유지보수 가이드

이 파일은 이 **하네스 자체**를 변경하려는 사람을 위한 것이다. 이 하네스를 *사용해서* 파이썬 프로젝트를 개발하려는 사람은 메인 세션에서 `/orchestrate-python-app-dev` 를 호출하라.

## 권위 파일 위치 (0-1)

다음은 stage 프롬프트가 매 호출마다 권위로 삼는 정본이다. 이 위치를 옮기면 stage 프롬프트의 `Inputs` 섹션과 `scripts/tests/test_invariants.py`의 매핑도 함께 옮겨야 한다.

- `docs/task-spec.md` — 작업 명세
- `docs/tacit-knowledge.md` — 확정된 암묵지 (도구 체인, 자가 교정 정책, 코드리뷰 매핑, git 규칙, 임계치 기본값)
- `docs/interview-guide.md` — 매 run 변수 목록
- `docs/stages.md` — 모드 B 결정 + 모든 stage 정의 + 라우팅 표 + 카운터

## 절대 경로 안 박기

이 레포의 어떤 정본 파일에도 `/home/...`, `/Users/...` 같은 절대 경로가 박혀 있으면 **버그**다 (install 시점에 잘못 치환된 결과). 권위 경로는 두 표기로 분리한다:

- `{{HARNESS_ROOT}}` — skill·docs 정본에. 메인 세션이 skill 로드 직후 `git rev-parse --show-toplevel` 한 줄로 runtime resolve.
- `{run_dir}`, `{run_id}` 등 — stage 프롬프트 정본에. orchestrator가 매 호출 직전 `str.replace`로 `{run_dir}/.prompts/<stage>.md` 에 물질화.

## 무엇을 바꿀 때 어디를 건드려야 하나

| 변경하고 싶은 것 | 건드릴 파일 |
|---|---|
| 새 stage 추가 | `docs/stages.md` + `scripts/prompts/<stage>.md` + `scripts/orchestrate.py`의 `STAGE_TOOLS`/`STAGE_DIRS`/`STAGE_REQUIRED_AUX_OUTPUTS` 매핑 + 카운터 키 + 라우팅 |
| 게이트 추가/제거 | `docs/tacit-knowledge.md §2` + `scripts/run_gate.py` + `scripts/orchestrate.py`의 `lint-test` 라우팅 + 해당 stage 프롬프트의 Inputs |
| Cap 기본값 | `scripts/config.yaml` (per-run override는 `interview/spec.md.caps`) |
| 도구 추가 (uv → poetry로 변경 등) | `docs/tacit-knowledge.md §1` + `scripts/detect_toolchain.py` + 영향받는 stage 프롬프트의 Bash 화이트리스트 + `scripts/orchestrate.py`의 `STAGE_TOOLS` |
| 브랜치 네이밍 | `docs/tacit-knowledge.md §7-1` + `scripts/prompts/branch-create.md` + 해당 도메인 코드 (orchestrator의 다음-NN 결정 로직) |
| 커밋 메시지 형식 | `docs/tacit-knowledge.md §7-2` + `scripts/prompts/{implement,document}.md` |
| 사용자 개입 토글 추가 | `docs/interview-guide.md §3` + `docs/stages.md` 사용자 개입 입력 형태 + `scripts/orchestrate.py`의 개입 분기 |
| 새 sub-agent 활용 | `docs/tacit-knowledge.md §5` + 해당 stage 프롬프트 본문 + STAGE_TOOLS의 `Task` 허용 확인 |

## 검사기

`.githooks/pre-commit`이 모든 커밋 직전에 `python3 -m pytest scripts/tests/` 를 돌린다. 두 종류로 나뉜다.

**`scripts/tests/test_invariants.py`** — 정본 파일 간 cross-file 정합성:

- stage 프롬프트의 Bash 명령 ↔ `STAGE_TOOLS`의 `Bash(...)` 패턴 (shell builtins 제외)
- 프롬프트의 `{token}` ↔ orchestrator의 치환 맵
- 모든 stage가 `STAGE_TOOLS` / `STAGE_PRIMARY_OUTPUT` / `STAGE_OWNED_PATTERNS` / `STAGE_MARKER` 에 등록됨
- 멀티-아웃풋 stage의 aux 파일 ↔ 프롬프트가 그 파일을 만들라고 명시 ↔ `STAGE_OWNED_PATTERNS` 에 포함
- verdict 라벨 enum ↔ 리뷰 stage 프롬프트가 가르치는 라벨
- 게이트 status enum ↔ orchestrate 의 `GATE_PASSING_STATUSES` ∪ `GATE_FAILING_STATUSES`
- `route()` 가 게이트 결과를 `gate_is_passing()` 으로만 읽는지 (raw `.get('passed')` 금지)
- short/long-form stage 디렉토리명 단일화
- 정본 파일에 절대 경로 (`/home/...`, `/Users/...`) 박힘 금지

**`scripts/tests/test_handle_resume.py` / `test_routing.py` / `test_run_gate.py` / `test_gate_cross_check.py` / `test_front_matter.py`** — orchestrator·run_gate 의 순수 함수 단위 테스트 (state machine, 라우팅, 게이트 status 매핑, front-matter 파싱).

검사 실패 시 커밋 차단. `--no-verify`로 우회하지 않는다.

## 단계 0 다섯 축의 위치

- **0-1** (컨텍스트): 본 파일 + `docs/`의 권위 파일 + stage 프롬프트의 Inputs 섹션 + orchestrator의 토큰 물질화
- **0-2** (게이트): `scripts/run_gate.py` + `scripts/orchestrate.py`의 머지된 정책 스냅샷(`{run_dir}/effective_thresholds.json`) + verdict shape-check
- **0-3** (도구 경계): `scripts/orchestrate.py`의 `STAGE_TOOLS` 매핑 → 매 `claude -p` 호출에 `--allowed-tools` 추가
- **0-4** (피드백 루프): `scripts/orchestrate.py`의 라우팅 테이블 + `feedback.md` 멀티-라이터 헬퍼 + backtrack 시 산출물 청소 + cap 카운터 + escalation
- **0-5** (자기교정 경계): run 종결 시 `outputs/.index.jsonl` 1라인 append + `escalation.md`의 과거 패턴 블록 + `delivery.md`의 암묵지 업데이트 후보 섹션 (모두 **제안만**, 자동 수정 금지)

다섯 축 중 의도적으로 생략한 것은 없다.

## 이 하네스 고유의 금지 변경

- **자동으로 `tacit-knowledge.md`를 LLM이 수정하지 않는다.** 반복 지적은 `delivery.md`의 후보 섹션에 제안만. 사람이 결정하고 수정.
- **자동으로 cap을 완화하지 않는다.** 사용자가 매 run에 `interview/spec.md.caps`로 override하거나, 영구 변경은 `scripts/config.yaml`을 사람이 수정.
- **`outputs/.index.jsonl`은 run 종결 시 단 한 줄.** 에스컬레이션마다 따로 쓰지 않는다 — 한 run이 여러 번 카운트되면 cross-run 통계가 왜곡된다.
- **`{{HARNESS_ROOT}}` install-time 치환 금지.** 어떤 도구도 SKILL.md나 docs/*.md의 `{{HARNESS_ROOT}}`을 sed로 절대경로로 치환하지 마라. 하네스를 `mv` 한 번에 옮길 수 있어야 한다.
- **하네스 git 과 workspace git 은 항상 분리된다.** 하네스 root 의 `.git` 은 하네스 자체의 버전관리, workspace 의 `.git` 은 사용자 프로젝트의 버전관리. 모든 phase-level stage 는 `cwd=workspace` 로 실행되고, mode=new 의 workspace 는 `ensure_workspace_repo()` 가 자동으로 `git init` 한다. mode=maintenance 의 workspace 가 walk-up 시 하네스 .git 으로 도달하면 `ensure_workspace_repo` 가 거부한다. `outputs/` 는 하네스의 `.gitignore` 에 박혀 있어 하네스 git 에 추적되지 않는다.

## 모드 B 컨텍스트 규약

이 하네스는 모드 B다. **메인 세션은 stage script를 호출한 뒤 산출물 파일 경로만 다음 호출에 넘기고, 본문은 working memory에서 즉시 폐기한다.** 사용자 개입 지점에서 짧은 요약이 필요할 때만 본문을 읽고 즉시 폐기. 이 규칙은 `orchestrate-python-app-dev` skill 본문에도 박혀 있다.

## 새로운 stage를 추가할 때 체크리스트

1. `docs/stages.md` 에 8개 항목 모두 채워서 정의
2. `scripts/prompts/<stage>.md` 작성 (Inputs 섹션, marker 라인, 도구 화이트리스트와 일치하는 Bash 사용)
3. `scripts/orchestrate.py`:
   - `STAGE_DIRS[<stage>]` 추가
   - `STAGE_TOOLS[<stage>]` 추가
   - `STAGE_PRIMARY_OUTPUT[<stage>]` 추가
   - `STAGE_OWNED_PATTERNS[<stage>]` — backtrack 시 unlink할 산출물 glob들. **primary output 도 반드시 포함**해야 backtrack 청소가 동작한다. 누락 시 stale 산출물이 다음 iteration 으로 흘러간다.
   - `STAGE_REQUIRED_AUX_OUTPUTS[<stage>]` (멀티-아웃풋이면)
   - 카운터 키 (필요시)
   - 라우팅 분기
4. `git commit` — pre-commit pytest (`scripts/tests/`) 가 누락된 부분을 잡아준다
