# session-registry

`sessions`: a live view of every Claude Code session running on your machine.
It's for when you have many sessions open, often several in the same repo, and need to know:

- which window is which, and what each one is doing
- whether two live sessions **recently edited the same file**, so one is about to
  overwrite the other's work
- what ended recently, with the exact `claude --resume <id>` command to bring it back
- what has been sitting untouched for days and can be closed

## Usage

```bash
sessions                 # live sessions, collisions, recently ended, cleanup
sessions --json          # machine-readable
sessions --all           # every transcript, however old
sessions --dead-days 7   # widen the "recently ended" window (default 3 days)
```

Inside Claude Code you can also just ask "what sessions are running?" or "am I stepping
on another session?". The skill tells Claude to run `sessions`, and to check it before
large edits in a repo where other sessions are live.

### Reading the output

| Marker | Meaning |
| --- | --- |
| `●` | busy (per Claude Code's own status) |
| `●` cyan | active in the last 15 minutes |
| `○` | idle |
| **SAME SESSION, MULTIPLE PROCESSES** | two processes are writing to one transcript, usually a `--resume` of a session that was still running. Fix this first. |
| **COLLISIONS** | two live sessions edited the same file within 24 hours |
| **RECENTLY ENDED** | sessions you can resume, with the command printed |
| **CLEANUP** | live but untouched for more than 3 days. Check before killing. |

## How it works

Nothing is cached, so the view can't go stale.

- **Which sessions are live:** read from the process table each time. A running
  process is the only proof a session is live.
- **Which session each process belongs to:** found from, in order:
  1. Claude Code's own record, `~/.claude/sessions/<pid>.json`
  2. this skill's `SessionStart` hook record, `~/.claude/session-registry/<id>.json`.
     Remote-controlled sessions have no built-in record, so this hook fills the gap.
  3. a `--resume <uuid>` in the process's arguments
- **Everything else** is read from the session transcript: title (from `/rename`,
  else Claude's auto-title), last prompt, files edited and last activity.
- **The remote URL** comes from the launcher's log file, found with `lsof`.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | Tells Claude when and how to use `sessions` |
| `sessions.py` | The `sessions` command (Python 3, standard library only) |
| `register.sh` | `SessionStart` hook that records which process belongs to which session |

## Install

`../../install.sh session-registry` does it all:

- symlinks the skill into `~/.claude/skills`
- links `sessions` into `~/.local/bin`, which needs to be on your `PATH`
- adds the hook to `~/.claude/settings.json`

To set it up by hand, add this to `~/.claude/settings.json`:

```json
"hooks": {
  "SessionStart": [
    { "matcher": "", "hooks": [ { "type": "command", "command": "~/.claude/skills/session-registry/register.sh", "timeout": 10 } ] }
  ]
}
```

Requires `python3`, `jq`, `ps` and `lsof`. Built and used daily on macOS; it uses
standard `ps`/`lsof` flags, so it should work on Linux too.

## Known limitation

Some sessions show as `(unregistered)`, with pid, folder and URL but no title or
collision data. That happens when a remote-controlled session was started before
the hook was installed and wasn't resumed with `--resume`. It gets full details
after a restart.
