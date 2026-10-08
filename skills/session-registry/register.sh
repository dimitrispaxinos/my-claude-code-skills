#!/usr/bin/env bash
# Claude Code SessionStart hook — record the one fact that cannot be derived.
#
# Everything else about a session (title, last prompt, files touched, liveness,
# uptime, remote URL) is readable after the fact from the transcript and the
# process table. The pid <-> session_id link is not: claude appends to its
# transcript and closes it, so no open file descriptor ties the running process
# to its session. This hook captures that link while both facts are in hand.
#
# Writes ~/.claude/session-registry/<session_id>.json. Exits 0 on every path.

set -uo pipefail

REGISTRY_DIR="$HOME/.claude/session-registry"

command -v jq >/dev/null 2>&1 || exit 0

# The telegram notifier summarizes turns with a headless `claude -p`, which fires
# this hook. Those are throwaway processes that live for seconds; registering
# them would fill the fleet view with sessions that are gone before it renders.
[[ -n "${CLAUDE_TELEGRAM_SUMMARIZER:-}" ]] && exit 0

INPUT="$(cat)"
[[ -n "$INPUT" ]] || exit 0

SESSION_ID="$(jq -r '.session_id // empty' <<<"$INPUT" 2>/dev/null)"
CWD="$(jq -r '.cwd // empty' <<<"$INPUT" 2>/dev/null)"
TRANSCRIPT="$(jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)"
SOURCE="$(jq -r '.source // empty' <<<"$INPUT" 2>/dev/null)"

[[ -n "$SESSION_ID" ]] || exit 0

# --- Find this session's own claude process ---------------------------------
# Only argv[0] is trusted: a shell's argv holds its whole command text, so
# matching flags anywhere in the tree would catch any command that merely
# mentions them.
CLAUDE_PID=""
CLAUDE_ARGS=""
pid="$PPID"
for _ in 1 2 3 4 5 6 7 8; do
  [[ -n "$pid" && "$pid" != "0" && "$pid" != "1" ]] || break
  args="$(ps -o args= -p "$pid" 2>/dev/null)" || break
  if [[ "$(basename "${args%% *}")" == "claude" ]]; then
    CLAUDE_PID="$pid"
    CLAUDE_ARGS="$args"
    break
  fi
  pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
done

REMOTE=false
NAME=""
if [[ -n "$CLAUDE_ARGS" ]]; then
  if [[ " $CLAUDE_ARGS " == *" --remote-control "* ]]; then
    REMOTE=true
    # `--remote-control [name]` — the name is optional and may contain spaces,
    # but stops at the next flag.
    NAME="$(sed -E 's/.*--remote-control[[:space:]]+//; s/[[:space:]]+--.*$//' <<<"$CLAUDE_ARGS")"
    [[ "$NAME" == -* ]] && NAME=""
  fi
fi

# Process start time is more truthful than "now": on a `resume` or `compact`
# this hook re-fires long after the session actually began.
STARTED=""
if [[ -n "$CLAUDE_PID" ]]; then
  STARTED="$(ps -o lstart= -p "$CLAUDE_PID" 2>/dev/null | sed -e 's/^ *//' -e 's/ *$//')"
fi

mkdir -p "$REGISTRY_DIR" 2>/dev/null || exit 0

jq -n \
  --arg session_id "$SESSION_ID" \
  --arg pid "${CLAUDE_PID:-}" \
  --arg cwd "$CWD" \
  --arg transcript "$TRANSCRIPT" \
  --arg name "$NAME" \
  --argjson remote "$REMOTE" \
  --arg started "$STARTED" \
  --arg source "$SOURCE" \
  --arg registered_at "$(date -u +%FT%TZ)" \
  '{session_id: $session_id,
    pid: (if $pid == "" then null else ($pid | tonumber) end),
    cwd: $cwd,
    transcript_path: $transcript,
    name: (if $name == "" then null else $name end),
    remote: $remote,
    started_at: (if $started == "" then null else $started end),
    source: $source,
    registered_at: $registered_at}' \
  > "$REGISTRY_DIR/${SESSION_ID}.json" 2>/dev/null

exit 0
