# remote-session

Starts a new Claude Code session **in the current folder** that you control from
[claude.ai/code](https://claude.ai/code) or the Claude mobile app. It runs in the
background with no terminal window, and keeps running after you close your terminal.

## Usage

Inside Claude Code:

```
/remote-session                 # session named after the folder
/remote-session my-feature      # custom name
```

Claude runs the launcher, waits for the session to start, and replies with its
`https://claude.ai/code/session_…` link.

To run it yourself, without Claude:

```bash
cd ~/code/my-app
~/.claude/skills/remote-session/remote-session.sh my-feature
# LOGFILE=/tmp/claude-remote-my-feature-1234.log
# PID=1235
grep -aoE 'https://claude\.ai/code/session_[A-Za-z0-9]+' /tmp/claude-remote-my-feature-1234.log
```

To stop it: `kill <PID>`.

## How it works

- It runs `claude --dangerously-skip-permissions --remote-control <name>`.
- `claude` needs a terminal for its remote-control mode, so the launcher wraps it
  in `script`, which provides a stand-in terminal. On macOS/BSD it runs
  `script -q /dev/null …`, and on Linux/WSL `script -qfc "…" /dev/null`; it picks
  the right one with `uname`.
- `nohup … & disown` detaches it, so the session doesn't depend on whatever
  started it.
- The session's link appears in the log file a few seconds after launch.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | Instructions Claude follows for `/remote-session` |
| `remote-session.sh` | The launcher |

## Requirements and caveats

- Claude Code v2.1.51 or later, signed in with a claude.ai subscription (not an API key).
- macOS or Linux/WSL. Not native Windows.
- **Permission checks are off** (`--dangerously-skip-permissions`), so only use it
  in repos you trust.
- A new session may say its MCP servers need authentication. Run `/mcp` from the
  remote session if so.
