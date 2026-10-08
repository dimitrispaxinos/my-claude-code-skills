#!/usr/bin/env bash
# Install the skills into ~/.claude/skills and wire up their hooks.
#
# Usage:  ./install.sh [--copy] [--force] [skill ...]
#   (no skill names)  install all skills in ./skills
#   --copy            copy instead of symlink (default: symlink, so `git pull`
#                     updates the installed skills in place)
#   --force           replace an existing skill of the same name
#
# Idempotent: safe to re-run. Needs bash, jq and python3.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$HOME/.claude/skills"
SETTINGS="$HOME/.claude/settings.json"
MODE=link
FORCE=false
WANT=()

for arg in "$@"; do
  case "$arg" in
    --copy) MODE=copy ;;
    --force) FORCE=true ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) WANT+=("$arg") ;;
  esac
done
[[ ${#WANT[@]} -gt 0 ]] || WANT=($(ls "$REPO/skills"))

command -v jq >/dev/null || { echo "error: jq is required (brew install jq / apt install jq)" >&2; exit 1; }

mkdir -p "$SKILLS_DIR"
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"

# Append a hook command to settings.json unless an identical one is present.
add_hook() {
  local event="$1" cmd="$2" timeout="$3" tmp
  if jq -e --arg e "$event" --arg c "$cmd" \
       '[.hooks[$e][]?.hooks[]?.command] | index($c)' "$SETTINGS" >/dev/null; then
    return
  fi
  tmp="$(mktemp)"
  jq --arg e "$event" --arg c "$cmd" --argjson t "$timeout" \
     '.hooks[$e] = ((.hooks[$e] // []) + [{matcher: "", hooks: [{type: "command", command: $c, timeout: $t}]}])' \
     "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  echo "  + $event hook → $cmd"
}

for name in "${WANT[@]}"; do
  src="$REPO/skills/$name"
  dst="$SKILLS_DIR/$name"
  [[ -d "$src" ]] || { echo "error: no skill '$name' in $REPO/skills" >&2; exit 1; }

  if [[ -e "$dst" || -L "$dst" ]]; then
    if [[ "$FORCE" != true ]]; then
      echo "skip $name: $dst exists (use --force to replace)"
      continue
    fi
    rm -rf "$dst"
  fi

  if [[ "$MODE" == link ]]; then ln -s "$src" "$dst"; else cp -R "$src" "$dst"; fi
  echo "installed $name ($MODE)"

  case "$name" in
    session-registry)
      mkdir -p "$HOME/.local/bin"
      ln -sf "$dst/sessions.py" "$HOME/.local/bin/sessions"
      echo "  + ~/.local/bin/sessions (make sure ~/.local/bin is on your PATH)"
      add_hook SessionStart "$dst/register.sh" 10
      ;;
    telegram)
      env="$HOME/.claude/telegram.env"
      if [[ ! -f "$env" ]]; then
        cp "$REPO/telegram.env.example" "$env"
        chmod 600 "$env"
        echo "  + created $env — fill in TELEGRAM_TOKEN and TELEGRAM_CHAT_ID"
      fi
      add_hook Stop "$dst/stop-notify.sh" 30
      ;;
  esac
done

echo "Done. Restart Claude Code sessions to pick up new hooks."
