# Stage: sanity-test (phase {N})

## Inputs

1. `{tacit_knowledge_path}` — §4-3 sanity policy (no self-correct, fail → design loopback).
2. `{requirements_path}` — acceptance_criteria for this phase's ids.
3. `{stage_dir}/design.md` — §5 test strategy & sanity scenario candidates.
4. `{thresholds_path}` — `sanity_scenarios_per_phase` (target count).
5. `{stage_dir}/feedback.md` (if exists) — backtrack feedback.

Workspace cwd: `{workspace}`.

## Task

Author and execute integration/smoke tests for this phase's golden-path user scenarios. Sanity tests run the actual code (server start + request, CLI invocation, etc.).

### Steps

1. **Scenario design**: invoke `voltagent-qa-sec:qa-expert` via Task with:
   - The phase requirements + their acceptance_criteria.
   - The design's §5 test strategy.
   - Ask for `{sanity_scenarios_per_phase}` end-to-end scenarios. Each scenario = a single user-observable flow with explicit setup, action, expected observation.

2. **Author tests**: under `{workspace}/tests/sanity/`, write pytest files with the `sanity` marker:
   ```python
   import pytest

   @pytest.mark.sanity
   def test_login_golden_path_returns_jwt(running_app):
       resp = running_app.post("/login", json={"email": "u@x.com", "password": "pw"})
       assert resp.status_code == 200
       assert resp.json()["token"]
   ```
   - Use real fixtures (DB, app server) where reasonable. **Do not mock the database** for sanity tests.
   - If the project lacks `pyproject.toml` `[tool.pytest.ini_options].markers`, add the `sanity` marker there.
   - Place a `tests/sanity/conftest.py` if shared fixtures are needed.

3. **Run** `pytest tests/sanity -m sanity -v`. Capture stdout.

### Outputs

Write `{stage_dir}/sanity.md`:

```markdown
# Sanity Test (phase {N})

## 시나리오
### 1. <시나리오 제목>
- **설정**: ...
- **행동**: ...
- **관찰**: ...
- **테스트 파일**: tests/sanity/test_login_golden.py::test_login_golden_path_returns_jwt

### 2. ...

## 실행 결과
- 통과: 2/2
- 실행 시간: 4.3s
- 출력 요약 (마지막 20줄)
```

The orchestrator will then run the `sanity` gate (`pytest tests/sanity -m sanity`) as the deterministic check and write `gates/sanity.json`.

## Constraints

- Sanity tests **must touch the real subsystem** (no mocked DB; no mocked HTTP for the SUT). Mocking external paid APIs is OK.
- 자가 교정 금지. 테스트가 실패하면 — 코드를 직접 고치지 말고 — `sanity.md`에 실패 진단을 적고 종료. orchestrator가 design으로 루프백한다.

## Output

```
SANITY_TEST_DONE: {stage_dir}/sanity.md
```
