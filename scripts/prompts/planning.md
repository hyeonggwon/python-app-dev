# Stage: planning

You are running the **planning** stage of the python-app-dev harness.

## Inputs (read these in order)

1. `{task_spec_path}` — what this harness does and produces.
2. `{tacit_knowledge_path}` — confirmed conventions (toolchain, git rules, review policy).
3. `{spec_path}` — this run's deep-interview output (mode, goals, ticket, toggles).
4. `{stage_dir}/feedback.md` (read **only if** it exists) — feedback from a prior revise loop.

Do not read source code in this stage. Planning is upstream of any implementation.

## Task

Write a product brief grounded in the user's goal and the harness's scope. Output to:

  `{stage_dir}/planning.md`

The file must contain exactly these top-level sections, in this order:

```markdown
# 목표
(1~2 문장. 무엇을 위해 이 작업을 하는가)

# 사용자 가치
(누가 무엇을 얻는가. 한 단락)

# 핵심 가정
- (검증되지 않았지만 진행을 위해 받아들이는 가정 3~5개)

# 범위
- (이 작업이 실제로 다루는 변경)

# 비범위
- (명시적으로 다루지 않는 것)
```

If `mode == maintenance` (read from `{spec_path}` front-matter), add a sixth section:

```markdown
# 현재 동작 vs 기대 동작
**현재**: ...
**기대**: ...
```

## Constraints

- 한국어로 작성.
- `requirements.md`에 들어갈 사용자 스토리 단위는 **여기서 만들지 않는다**. 이 stage는 다음 stage(`requirements`)가 분해할 단일 product brief를 만든다.
- 추측하지 말 것. 부족한 정보가 있다면 "핵심 가정"에 명시.

## Output

After writing the file, print **exactly one** marker line to stdout as the last line:

```
PLANNING_DONE: {stage_dir}/planning.md
```
