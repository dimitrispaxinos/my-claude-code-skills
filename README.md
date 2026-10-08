# my-claude-code-skills

Skills for [Claude Code](https://claude.com/claude-code) that I use every day for
running many sessions at once on one machine.

| Skill | What it does |
| --- | --- |
| [`remote-session`](skills/remote-session/SKILL.md) | `/remote-session [name]` starts a detached Claude Code session **in the current folder**, with remote control on, and gives you the `claude.ai/code/session_…` link so you can drive it from the web or the Claude mobile app. |
| [`session-registry`](skills/session-registry/SKILL.md) | `sessions` lists every live Claude Code session: which one is which, where each one is, which sessions have recently **edited the same file**, what ended recently (with the `--resume` command), and what's stale. Lets sessions avoid overwriting each other's work. |
| [`telegram`](skills/telegram/SKILL.md) | `/telegram <msg>` posts to your Telegram, plus a **Stop hook** that pings you when a turn took 5+ minutes. The ping includes the session title, the repo, how long it took, and a one-sentence summary (written by Haiku) of what got done, or `❓ needs your input` if the session is waiting on a question. |

Each skill folder has its own README with usage, setup and how it works.

## Requirements

- Claude Code (v2.1.51+ for `--remote-control`) and a claude.ai subscription
- macOS or Linux/WSL (not native Windows)
- `bash`, `jq`, `python3`; `lsof` for `sessions`

## Install

```bash
git clone https://github.com/dimitrispaxinos/my-claude-code-skills.git
cd my-claude-code-skills
./install.sh                    # all skills
./install.sh remote-session     # or just some
```

What `install.sh` does:

- **Skills:** symlinks each skill into `~/.claude/skills/`, so a `git pull` updates them. Use `--copy` to copy instead.
- **Existing skills:** skips any skill you already have with the same name; `--force` replaces it.
- **`session-registry`:**
  - links `sessions` into `~/.local/bin`; make sure that's on your `PATH`
  - adds a `SessionStart` hook to `~/.claude/settings.json`
- **`telegram`:**
  - creates `~/.claude/telegram.env` (mode 600) from [`telegram.env.example`](telegram.env.example)
  - adds a `Stop` hook to `~/.claude/settings.json`
- **Re-running** is safe: hooks are only added once.

Restart your Claude Code sessions afterwards so they load the new hooks.

### Telegram setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Send the bot a message, or add it to a group and post something there. Then
   find the chat id:
   ```bash
   source ~/.claude/telegram.env
   curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getUpdates" | grep -o '"chat":{"id":[-0-9]*'
   ```
3. Put both values in `~/.claude/telegram.env`, then test with
   `~/.claude/skills/telegram/send.sh "hello"`.

You can tune the notifier with environment variables:
`CLAUDE_TELEGRAM_NOTIFY_THRESHOLD` (seconds, default 300),
`CLAUDE_TELEGRAM_SUMMARY_MODEL` (default `haiku`), and
`CLAUDE_TELEGRAM_NOTIFY_LOG=/tmp/tg.log` for debugging.

## Notes

- `remote-session` launches Claude with `--dangerously-skip-permissions`, so only
  use it in repos you trust.
- The notifier stays quiet for remote-controlled sessions, because you're already
  on your phone for those.
- `session-registry` and `telegram` know about each other: the summarizer's
  headless `claude -p` call is not counted as a session, and doesn't trigger a
  notification of its own.

## License

MIT
