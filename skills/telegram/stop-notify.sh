#!/usr/bin/env bash
# Claude Code Stop hook — ping Telegram when a long local session finishes.
#
# Fires on every turn end; stays quiet unless ALL of these hold:
#   * the turn involved >= THRESHOLD seconds of actual work
#   * the session is not remote-controlled (you're already on your phone)
#   * we haven't already notified for this same turn
#
# The cheap checks run inline; composing the summary and sending are detached, so
# the hook never delays the session. Generating a summary costs a headless model
# call (~10-20s) and a Stop hook runs synchronously -- inline, that would be a
# visible hang before the prompt comes back.
#
# Reads the hook JSON on stdin. Exits 0 unconditionally: a notifier must never
# block Claude from stopping, so every failure path is silent.

set -uo pipefail

THRESHOLD_SECONDS="${CLAUDE_TELEGRAM_NOTIFY_THRESHOLD:-300}"
BASE="$HOME/.claude/skills/telegram"
SEND="$BASE/send.sh"
SUMMARIZE="$BASE/summarize.py"
STATE_DIR="$HOME/.claude/.telegram-notify-state"
DEBUG_LOG="${CLAUDE_TELEGRAM_NOTIFY_LOG:-}"

log() { [[ -n "$DEBUG_LOG" ]] && echo "$(date -u +%FT%TZ) $*" >>"$DEBUG_LOG"; return 0; }
quit() { log "skip: $*"; exit 0; }

# Our own summarizer runs `claude -p`, which fires this hook again. The guard is
# an explicit marker rather than a timing coincidence: relying on "the
# summarizer is always fast" would make a slow call notify on its own.
[[ -n "${CLAUDE_TELEGRAM_SUMMARIZER:-}" ]] && exit 0

command -v jq >/dev/null 2>&1 || exit 0
[[ -x "$SEND" ]] || exit 0

INPUT="$(cat)"
[[ -n "$INPUT" ]] || exit 0

TRANSCRIPT="$(jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)"
CWD="$(jq -r '.cwd // empty' <<<"$INPUT" 2>/dev/null)"
SESSION_ID="$(jq -r '.session_id // empty' <<<"$INPUT" 2>/dev/null)"
ACTIVE="$(jq -r '.stop_hook_active // false' <<<"$INPUT" 2>/dev/null)"

[[ "$ACTIVE" == "true" ]] && quit "stop_hook_active"
[[ -f "$TRANSCRIPT" ]] || quit "no transcript"

# --- Skip remote-controlled sessions ----------------------------------------
# No env var or hook field exposes this, so walk up the process tree to the
# `claude` process and read the flags it was launched with.
#
# Only the claude process itself is inspected, never the intermediate shells:
# a shell's argv holds the whole command text, so any command that merely
# mentions --remote-control (this hook's own test suite, a grep, an edit to
# this file) would otherwise read as a remote session and silence the notifier.
pid="$PPID"
for _ in 1 2 3 4 5 6 7 8; do
  [[ -n "$pid" && "$pid" != "0" && "$pid" != "1" ]] || break
  args="$(ps -o args= -p "$pid" 2>/dev/null)" || break
  if [[ "$(basename "${args%% *}")" == "claude" ]]; then
    [[ " $args " == *" --remote-control "* ]] && quit "remote-controlled"
    break   # found the session's own process; its flags are authoritative
  fi
  pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
done

# --- When did *my* work on this turn start? ---------------------------------
# Not "when you last typed" — your answers to AskUserQuestion arrive as tool
# results, so that clock would keep running while I wait on you and count your
# away-time as work. Presence = a typed prompt OR an answer to a question.
LAST_PRESENCE="$(jq -r '
  select(.type == "user" and .isSidechain != true)
  | select(
      ((.isMeta != true) and (.message.content | type) == "string")
      or ((.isMeta != true) and (.message.content | type) == "array"
          and any(.message.content[]?; .type == "text"))
      or ((.toolUseResult | type) == "object" and (.toolUseResult | has("answers")))
    )
  | .timestamp // empty
' "$TRANSCRIPT" 2>/dev/null | tail -1)"

[[ -n "$LAST_PRESENCE" ]] || quit "no presence timestamp"

START_EPOCH="$(jq -rn --arg t "$LAST_PRESENCE" '
  $t | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601
' 2>/dev/null)"
[[ -n "$START_EPOCH" && "$START_EPOCH" != "null" ]] || quit "unparseable timestamp"

ELAPSED=$(( $(date -u +%s) - START_EPOCH ))
log "elapsed=${ELAPSED}s threshold=${THRESHOLD_SECONDS}s"
[[ "$ELAPSED" -ge "$THRESHOLD_SECONDS" ]] || quit "too short (${ELAPSED}s)"

# --- Only notify once per turn ----------------------------------------------
mkdir -p "$STATE_DIR" 2>/dev/null
STATE_FILE="$STATE_DIR/${SESSION_ID:-unknown}"
[[ -f "$STATE_FILE" && "$(cat "$STATE_FILE" 2>/dev/null)" == "$LAST_PRESENCE" ]] \
  && quit "already notified for this turn"

# Claimed before the send, not after: the background job takes ~20s, and a second
# Stop firing in that window would otherwise spawn a duplicate notification.
echo "$LAST_PRESENCE" >"$STATE_FILE" 2>/dev/null

# --- Did the turn end waiting on you, or finished? --------------------------
LAST_ASK="$(jq -r '
  select(.type == "assistant")
  | select(any(.message.content[]?; .type == "tool_use" and .name == "AskUserQuestion"))
  | .timestamp // empty
' "$TRANSCRIPT" 2>/dev/null | tail -1)"

LAST_ANSWER="$(jq -r '
  select(.type == "user" and (.toolUseResult | type) == "object"
         and (.toolUseResult | has("answers")))
  | .timestamp // empty
' "$TRANSCRIPT" 2>/dev/null | tail -1)"

PENDING_QUESTION=false
if [[ -n "$LAST_ASK" ]] && [[ -z "$LAST_ANSWER" || "$LAST_ASK" > "$LAST_ANSWER" ]]; then
  PENDING_QUESTION=true
fi

# --- Where should you come back to? -----------------------------------------
# Claude Code auto-titles every session (ai-title) and stores /rename as
# custom-title. Prefer the name you chose; fall back to the generated one.
title="$(grep '"type":"custom-title"' "$TRANSCRIPT" 2>/dev/null | tail -1 \
         | jq -r '.customTitle // empty' 2>/dev/null)"
[[ -z "$title" ]] && title="$(grep '"type":"ai-title"' "$TRANSCRIPT" 2>/dev/null | tail -1 \
                             | jq -r '.aiTitle // empty' 2>/dev/null)"
[[ -z "$title" ]] && title="Claude session"

repo="$(basename "${CWD:-$PWD}")"

mins=$(( ELAPSED / 60 ))
if (( mins >= 60 )); then
  duration="$(( mins / 60 ))h $(( mins % 60 ))m"
else
  duration="${mins} min"
fi

if [[ "$PENDING_QUESTION" == true ]]; then
  headline="❓ ${title}"
  status="needs your input after ${duration}"
else
  headline="✅ ${title}"
  status="done after ${duration}"
fi

# --- Summarize and send, detached -------------------------------------------
(
  summary=""
  if [[ -x "$SUMMARIZE" ]] && command -v python3 >/dev/null 2>&1; then
    summary="$(python3 "$SUMMARIZE" "$TRANSCRIPT" "$LAST_PRESENCE" 2>/dev/null)"
  fi

  MESSAGE="${headline}
${repo} · ${status}"
  if [[ -n "$summary" ]]; then
    MESSAGE="${MESSAGE}
${summary}"
  else
    # No summary is better than a wrong one, but the last prompt still says
    # which piece of work this was.
    last_prompt="$(grep '"type":"last-prompt"' "$TRANSCRIPT" 2>/dev/null | tail -1 \
                   | jq -r '.lastPrompt // empty' 2>/dev/null | tr '\n' ' ' | tr -s ' ')"
    if [[ ${#last_prompt} -gt 70 ]]; then
      last_prompt="${last_prompt:0:70}…"
    fi
    [[ -n "$last_prompt" ]] && MESSAGE="${MESSAGE}
↳ \"${last_prompt}\""
  fi

  if printf '%s' "$MESSAGE" | "$SEND" >/dev/null 2>&1; then
    log "sent: $headline ($status)${summary:+ | $summary}"
  else
    log "send failed"
    rm -f "$STATE_FILE" 2>/dev/null   # let the next Stop retry
  fi
) >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
