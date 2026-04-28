# Task Spec — python-app-dev 하네스

## 목적

파이썬 프로그램 개발(신규)과 유지보수(기존 코드베이스)를 동일한 워크플로우 — 기획 → 요구사항 → Phase 분리 → (Phase별) 설계 → 브랜치 → 구현 → 린트/테스트 → 코드리뷰 → sanity → 문서화 → PR — 로 진행하는 제너릭 하네스.

대상 프로그램 종류는 제한 없음(서버, 클라이언트, AI/ML 파이프라인, CLI, 라이브러리 등). 파이썬 프로젝트 안에 JS 자산(프론트엔드, 도구)이 섞여 있어도 동일하게 다룬다.

## 적용 모드

| 모드 | 시작 지점 | 작업 위치 |
|---|---|---|
| `new` (신규 개발) | `outputs/<run-id>/workspace/` 안에 새 프로젝트 생성 | 하네스 임시 디렉토리 |
| `maintenance` (유지보수) | 사용자가 deep-interview에서 절대경로 제공 | 사용자 지정 경로 |

매 run 시작 시 deep-interview에서 모드를 결정한다.

## 표준 도구 체인

| 영역 | 도구 |
|---|---|
| 패키징/환경 | `uv` |
| 린트/포맷 | `ruff` (check + format) |
| 타입 체크 | `mypy` |
| 테스트 | `pytest` (+ `pytest-cov`) |
| sanity test | `pytest -m sanity` (별도 마커, `tests/sanity/`) |
| VCS / PR | `git` + GitHub `gh` CLI |

`maintenance` 모드에서는 프로젝트의 기존 도구 체인을 자동 감지(`pyproject.toml`, `requirements.txt`, `setup.cfg`, `tox.ini`, `Makefile`)해 그대로 사용한다. 감지 실패 시 위 표준을 도입한다(이 결정은 deep-interview에서 사용자 확인을 받는다).

## 산출물 형식 (run별)

run 디렉토리 구조:

```
outputs/<run-id>/
├── interview/spec.md            # deep-interview 결과 (모드, 토글, 임계치 등)
├── planning.md                  # 기획서 (1단계 산출물)
├── requirements.md              # 요구사항 목록 (US/NFR)
├── phases.md                    # Phase 분리안 (MVP → 확장)
├── workspace/                   # (new 모드만) 새 프로젝트 루트
├── phase-1/
│   ├── design.md
│   ├── branch.txt               # 생성된 브랜치명
│   ├── implementation.md        # 구현 변경 요약
│   ├── gates/                   # 결정론적 게이트 결과 JSON
│   │   ├── install.json
│   │   ├── lint.json
│   │   ├── format.json
│   │   ├── types.json
│   │   ├── tests.json
│   │   ├── coverage.json
│   │   └── sanity.json
│   ├── review.md                # 코드리뷰 verdict (LLM)
│   ├── sanity.md                # sanity 시나리오 + 결과
│   ├── docs-changes.md          # 추가/변경된 문서 목록
│   ├── pr.md                    # PR 본문 초안
│   ├── feedback.md              # (실패 시) 다음 루프 입력
│   └── verdict.json             # phase 종합 verdict
├── phase-2/...
├── delivery.md                  # run 종결 보고
└── escalation.md                # (cap 도달 시) 사람 개입 요청
```

run 종결 시 `outputs/.index.jsonl`에 한 줄 append한다(0-5 정책).

## 워크플로우 개요

run-level → phase loop → phase-level의 2단 구조.

```
deep-interview
  → planning              [개입 ① 토글]
  → requirements          [개입 ② 토글]
  → phase-split           [개입 ③ 토글]
  → for each phase:
      design              [개입 ④ 토글]
      → branch-create
      → implement
      → lint-test         (자가 교정 cap=5)
      → code-review       (sub-agent verdict)
      → sanity-test       (게이트 fail → design)
      → document
      → pr-create         [개입 ⑤ 토글]
  → delivery
```

## 사용자 개입 지점 (모두 토글)

| # | 지점 | 사용자 행동 |
|---|---|---|
| ① | planning 직후 | 기획 초안 승인/수정/반려 |
| ② | requirements 직후 | US/NFR 추가·삭제·우선순위 |
| ③ | phase-split 직후 | Phase 순서/범위/MVP 경계 확정 |
| ④ | 각 phase의 design 직후 | 모듈 구조·인터페이스·외부 의존 확정 |
| ⑤ | 각 phase의 pr-create 직전 | PR 본문 검토 + push/create 여부 |

deep-interview에서 각각 on/off 가능. off된 지점은 자동 통과(자동 confirm).

## 비범위 (이 하네스가 안 하는 것)

- 배포(CI/CD 파이프라인 구축, 인프라 프로비저닝)
- 운영 모니터링/알람 설정
- 비-파이썬 단독 프로젝트(순수 JS, Go 등)
- 멀티-레포 동시 작업
- 회사 내부 보안 감사(보안 검토는 옵션 토글로 코드 수준만 본다)
