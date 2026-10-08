---
name: sessions
description: Show the fleet of Claude Code sessions on this machine — what's live, which session is which, where to return to, which sessions are editing the same files, and what can be reaped. Use when the user says "/sessions", "what sessions are running", "which session was doing X", "am I stepping on another session", or before editing files in a repo where other sessions are live.
---

# Session registry

A fleet view of every Claude Code session on this machine.

```bash
sessions                 # live sessions, collisions, recently ended
sessions --json          # machine-readable
sessions --all           # every transcript, however old
sessions --dead-days 7   # widen the "recently ended" window
```

`sessions` is on PATH (`~/.local/bin/sessions` → `sessions.py`).

## How it works

Live sessions are enumerated from the **process table** — a running process is
the only proof a session is live. Everything else is joined onto that, and
nothing is cached, so the view cannot go stale.

The pid ↔ session_id link comes from the first of these that answers:

1. **`~/.claude/sessions/<pid>.json`** — Claude Code's own record. Authoritative
   where present, and carries a live `busy`/`idle` status. **Does not cover
   remote-controlled sessions.**
2. **`~/.claude/session-registry/<id>.json`** — written by our `SessionStart`
   hook (`register.sh`). Exists only to fill the remote gap above.
3. **`--resume <uuid>`** in the process's own arguments — free, authoritative,
   and the only link for a *resumed remote* session (no built-in record, and the
   hook may predate it).

Everything else is derived at read time:

| Fact | Source |
| --- | --- |
| live / uptime / cwd | process table (`ps`, `lsof`) |
| status | `~/.claude/sessions/<pid>.json` (local only) |
| title | transcript `custom-title` (from `/rename`), else `ai-title` |
| last prompt | transcript `last-prompt` |
| files edited | transcript `file-history-delta.trackingPath` |
| last activity | claude's status clock, else newest transcript timestamp, else remote PTY-log mtime |
| remote URL | `lsof` on the session's `script` parent → its log |

Do not use `pgrep -f '^claude'` to enumerate sessions: it does not return the
calling session's own claude process. `ps -axo args=` matched on argv[0] does.

## Reading the output

- `●` busy (claude's own status) · `●` cyan: active in the last 15 min · `○` idle
- **SAME SESSION, MULTIPLE PROCESSES** — the most serious check. Several live processes are appending to one transcript, almost always a `--resume` of a session that was never actually dead. Neither process knows about the other and their turns interleave. Ranked above file collisions because the conversation itself is being corrupted, not just a file.
- **COLLISIONS** — two *live* sessions edited the *same file* within 24h. Same-repo overlap is deliberately not flagged: when many sessions share one directory it would fire constantly and teach the reader to ignore it.
- **RECENTLY ENDED** — resume with the printed `claude --resume <id>`.
- **CLEANUP** — live but untouched for >3 days. Review before killing; age alone is not staleness, since some sessions are parked deliberately.

## Coordinating with other sessions

Before a substantial edit in a repo where other sessions are live, run `sessions`
and check the COLLISIONS block. If another live session recently touched a file
you are about to change, say so rather than racing it — the other session may
hold unsaved context about that file.

## Known limitation

A session is unidentified only if **all three** links fail: no built-in record
(so: remote-controlled), no hook entry (so: started before the hook existed), and
no `--resume` uuid in its arguments. Those appear as `(unregistered)` with pid,
cwd, name and URL but no title, prompt, or collision data, and gain full detail
on restart. Local sessions are always identified, because claude records them
itself.

For the same reason, a transcript that was written in the last 10 minutes inside
a directory where an unregistered session is running is **withheld** from
RECENTLY ENDED and reported as a count — it probably belongs to that live
session, and resuming a live session would put two processes on one transcript.
Guessing the link from cwd and start time would silently attribute one session's
work to another, so it is not attempted.
