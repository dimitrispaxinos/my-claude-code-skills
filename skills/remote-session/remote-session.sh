#!/usr/bin/env bash
set -euo pipefail

# Launch a Claude Code session in the CURRENT working directory with permission
# prompts bypassed and remote control enabled, so it can be driven from
# claude.ai/code or the Claude mobile app.
#
# The session is launched FULLY DETACHED: this script backgrounds it and exits
# immediately, so the session is orphaned to launchd/init and survives the
# terminal closing or the parent process (including a Claude Code background
# task) being cleaned up. It is NOT tied to this script's lifetime.
#
# Usage:  remote-session.sh [session-name]
#   Run it from the folder you want the session to live in. Do NOT cd into the
#   script's own directory first — it intentionally uses $PWD as the repo root.
#   session-name defaults to the current folder's name.
#
# Prints `LOGFILE=<path>` and `PID=<pid>`. The session's connect URL
# (https://claude.ai/code/session_...) appears in that log a few seconds later.
#
# Notes:
#   - claude needs a TTY for its interactive/remote-control UI; `script`
#     allocates a pseudo-terminal so it doesn't fall back to headless --print
#     mode. `script` syntax differs by platform: BSD/macOS takes the command as
#     trailing args (`script -q /dev/null <cmd>`); util-linux (Linux, WSL)
#     takes it as one string via -c (`script -qfc "<cmd>" /dev/null`).

SESSION_NAME="${1:-$(basename "$PWD")}"
LOG="${TMPDIR:-/tmp}/claude-remote-${SESSION_NAME}-$$.log"
: > "$LOG"

# nohup ignores SIGHUP, `&` backgrounds it, `disown` drops it from this shell's
# job table — together the session outlives whatever launched this script.
case "$(uname -s)" in
  Linux)
    # Quote the name so spaces/specials survive the single command string.
    CMD="claude --dangerously-skip-permissions --remote-control $(printf '%q' "$SESSION_NAME")"
    nohup script -qfc "$CMD" /dev/null >>"$LOG" 2>&1 &
    ;;
  Darwin|*BSD)
    nohup script -q /dev/null claude --dangerously-skip-permissions --remote-control "$SESSION_NAME" >>"$LOG" 2>&1 &
    ;;
  *)
    echo "Unsupported platform: $(uname -s) (needs macOS/BSD or Linux/WSL)" >&2
    exit 1
    ;;
esac
CHILD_PID=$!
disown "$CHILD_PID" 2>/dev/null || disown 2>/dev/null || true

echo "LOGFILE=$LOG"
echo "PID=$CHILD_PID"
