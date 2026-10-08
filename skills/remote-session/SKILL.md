---
name: remote-session
description: Launch a new Claude Code session in the CURRENT folder with permissions bypassed and remote control enabled, then report the claude.ai/code connect URL. Use when the user says "/remote-session", "start a remote session", "new remote-controlled session here", or wants to drive a session in this directory from claude.ai or their phone.
---

# Remote Session

Starts a fully-detached Claude Code session in the user's current working
directory with `--dangerously-skip-permissions` and `--remote-control`, then
hands back the `https://claude.ai/code/session_...` URL so they can drive it from
claude.ai/code or the Claude mobile app.

## Steps

1. Note the current working directory — the session launches **here**. Do not
   `cd` anywhere. (If the user named a different folder, `cd` there first.)
2. Run the bundled launcher in the **foreground** (do NOT use run_in_background).
   The script backgrounds/detaches the session itself and returns immediately,
   printing `LOGFILE=` and `PID=`:
   ```bash
   bash "<skill-base-dir>/remote-session.sh"
   ```
   Pass an optional session name as the first arg; it defaults to the folder name.
3. Read the `LOGFILE=<path>` and `PID=<pid>` lines from the script's stdout.
4. Wait ~5-8 seconds for the session to boot and register, then extract the
   connect URL from the log:
   ```bash
   grep -aoE 'https://claude\.ai/code/session_[A-Za-z0-9]+' "<logfile>" | head -1
   ```
   If nothing matches yet, wait a couple more seconds and retry (up to ~20s).
5. Report the URL to the user as the primary deliverable, and confirm the session
   is alive: `kill -0 <pid>` (or `pgrep -fl 'remote-control'`).

## Notes

- **Detached, no local window** — the launcher uses `nohup … & disown` so the
  session (a PTY-wrapped `claude` via `script`) is orphaned to launchd and
  survives the terminal closing or the launching process being cleaned up. The
  user controls it entirely through the URL. It keeps running until they
  explicitly stop it (`kill <pid>`).
- **Do not launch it via a background task** — running the script foreground is
  correct; it self-detaches. Backgrounding it through a Claude task would tie the
  session to that task's lifecycle and it would die when the task is cleaned up.
- **Requirements**: Claude Code v2.1.51+, a claude.ai subscription (not an API
  key), and a logged-in `/login` session. Runs on macOS/BSD and Linux/WSL (the
  launcher picks the right `script` syntax via `uname`); not native Windows.
- A freshly launched session may report **MCP servers need authentication**
  (`run /mcp` inside it) — expected; authenticate from the remote UI if needed.
- ⚠️ Bypass-permissions disables safety checks — only launch in trusted repos.
