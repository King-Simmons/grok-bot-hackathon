#!/usr/bin/env bash
# Point each harness at the single vendored pstack tree, plus the repo verification skill.
# Safe to re-run. Replaces symlinks. Leaves the real verify-lofi-news-radio directory in place.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_SKILLS="$ROOT/vendor/pstack/skills"
VENDOR_AGENTS="$ROOT/vendor/pstack/agents"
VERIFY_NAME="verify-lofi-news-radio"
VERIFY_SRC="$ROOT/.cursor/skills/$VERIFY_NAME"

if [[ ! -d "$VENDOR_SKILLS" ]]; then
  echo "missing $VENDOR_SKILLS" >&2
  exit 1
fi

skill_harnesses=(.cursor/skills .claude/skills .agents/skills .pi/skills)
for rel in "${skill_harnesses[@]}"; do
  dir="$ROOT/$rel"
  mkdir -p "$dir"
  shopt -s nullglob
  for entry in "$dir"/*; do
    [[ -L "$entry" ]] || continue
    target="$(readlink "$entry")"
    case "$target" in
      ../../vendor/pstack/skills/*)
        name="$(basename "$entry")"
        if [[ ! -d "$VENDOR_SKILLS/$name" ]]; then
          rm "$entry"
        fi
        ;;
    esac
  done
  shopt -u nullglob
  for skill in "$VENDOR_SKILLS"/*; do
    [[ -d "$skill" ]] || continue
    name="$(basename "$skill")"
    ln -sfn "../../vendor/pstack/skills/$name" "$dir/$name"
  done
done

if [[ -d "$VERIFY_SRC" && ! -L "$VERIFY_SRC" ]]; then
  for rel in .claude/skills .agents/skills .pi/skills; do
    ln -sfn "../../.cursor/skills/$VERIFY_NAME" "$ROOT/$rel/$VERIFY_NAME"
  done
fi

agent_harnesses=(.cursor/agents .claude/agents)
for rel in "${agent_harnesses[@]}"; do
  dir="$ROOT/$rel"
  mkdir -p "$dir"
  shopt -s nullglob
  for entry in "$dir"/*; do
    [[ -L "$entry" ]] || continue
    target="$(readlink "$entry")"
    case "$target" in
      ../../vendor/pstack/agents/*)
        name="$(basename "$entry")"
        if [[ ! -f "$VENDOR_AGENTS/$name" ]]; then
          rm "$entry"
        fi
        ;;
    esac
  done
  shopt -u nullglob
  for agent in "$VENDOR_AGENTS"/*; do
    [[ -f "$agent" ]] || continue
    name="$(basename "$agent")"
    ln -sfn "../../vendor/pstack/agents/$name" "$dir/$name"
  done
done

echo "linked pstack skills into ${skill_harnesses[*]}"
echo "linked pstack agents into ${agent_harnesses[*]}"
