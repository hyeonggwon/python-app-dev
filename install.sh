#!/usr/bin/env bash
# python-app-dev 하네스 설치 스크립트.
#
# 하는 일:
#   1) git core.hooksPath 를 .githooks 로 설정 (validate_harness.py 강제)
#   2) 하네스의 두 skill 을 ~/.claude/skills/ 로 symlink (덮어쓰기 안 함)
#   3) 필수 도구 (uv, gh, claude, python3) 가 PATH 에 있는지 확인
#   4) validator 한 번 돌려서 sanity 확인
#
# 절대 sed 로 절대경로 치환을 하지 않는다 (CLAUDE.md 의 금지 변경).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$ROOT/.claude/skills"
SKILLS_DST="${HOME}/.claude/skills"

color_red()   { printf "\033[31m%s\033[0m\n" "$*"; }
color_green() { printf "\033[32m%s\033[0m\n" "$*"; }
color_yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

# 1) git hooks
if [ -d "$ROOT/.git" ] || git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$ROOT" config core.hooksPath .githooks
  color_green "✓ git core.hooksPath = .githooks"
else
  color_yellow "⚠ $ROOT 가 git repo 가 아니다 — hooks 미설정. 'git init' 후 install.sh 를 다시 실행하라."
fi

# 2) skill symlinks
mkdir -p "$SKILLS_DST"
for skill_dir in "$SKILLS_SRC"/*/; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  target="$SKILLS_DST/$skill_name"
  if [ -L "$target" ]; then
    existing="$(readlink "$target")"
    if [ "$existing" = "$skill_dir" ] || [ "$existing" = "${skill_dir%/}" ]; then
      color_green "✓ skill symlink already correct: $skill_name"
      continue
    fi
    color_yellow "⚠ skill symlink exists at $target → $existing (다른 위치). skip."
    continue
  fi
  if [ -e "$target" ]; then
    color_yellow "⚠ $target already exists (not a symlink). skip — 수동으로 정리하라."
    continue
  fi
  ln -s "${skill_dir%/}" "$target"
  color_green "✓ linked $skill_name → $target"
done

# 3) tool check (warn-only — uv/gh 는 mode 에 따라 옵션)
need_tool() {
  local name="$1"; local hint="$2"
  if command -v "$name" >/dev/null 2>&1; then
    color_green "✓ $name: $(command -v "$name")"
  else
    color_yellow "⚠ $name 미설치 — $hint"
  fi
}
need_tool python3 "python3.10+ 필요"
need_tool claude  "Claude Code CLI 필요 (https://claude.com/claude-code)"
need_tool uv     "신규 프로젝트의 기본 패키지 도구. maintenance 에서는 detect_toolchain 결과 우선."
need_tool gh     "pr_mode=auto 사용 시 필요 (https://cli.github.com)"
need_tool git    "필수"

# 4) validator
color_yellow "→ validate_harness.py 실행..."
if python3 "$ROOT/scripts/validate_harness.py"; then
  color_green "✓ harness 정합성 OK"
else
  color_red "✗ harness 정합성 실패 — 위 메시지 참고하여 수정 후 재실행하라."
  exit 1
fi

cat <<EOF

설치 완료. 메인 세션에서 다음과 같이 사용:

  /orchestrate-python-app-dev

이 skill 이 init_run.py 호출 → deep-interview → stage 디스패치 흐름을 시작한다.

권위 파일 (수정 시 PR 필요):
  - $ROOT/docs/task-spec.md
  - $ROOT/docs/tacit-knowledge.md
  - $ROOT/docs/interview-guide.md
  - $ROOT/docs/stages.md
  - $ROOT/CLAUDE.md  (하네스 유지보수자용)

EOF
