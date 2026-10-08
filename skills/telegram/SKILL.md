---
name: telegram
description: Send a message to your Telegram notifications chat. Use when the user says "/telegram <message>", "send this to Telegram", "notify me on Telegram", "ping me on Telegram when this is done", or otherwise asks for something to be posted to Telegram.
---

# Telegram notifications

Sends a message to your Telegram notifications chat (a group or a DM) via your own Telegram bot.

## Usage

```bash
~/.claude/skills/telegram/send.sh "your message here"
```

Long or multi-line messages are easier to pipe in:

```bash
cat <<'EOF' | ~/.claude/skills/telegram/send.sh
Build finished ✅
All 214 tests passed.
EOF
```

The script prints `sent` on success and exits non-zero with the API error otherwise.

## Writing the message

- Keep it short — this is a phone notification, not a report. Lead with the outcome.
- Messages are parsed as Telegram Markdown (`*bold*`, `_italic_`, `` `code` ``). If parsing fails the script automatically retries as plain text, so unescaped characters degrade rather than error.
- Telegram's hard limit is 4096 characters; the script rejects anything longer. Summarize instead of splitting across messages.
- When the message reports on work you just did, say what actually happened — including failures. Don't send "done ✅" for a run with failing tests.

## Configuration

Credentials live in `~/.claude/telegram.env` (mode 600), not in this skill directory:

| Variable | Meaning |
| --- | --- |
| `TELEGRAM_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Target chat or group id (groups are negative, e.g. `-1001234567890`) |

Point `TELEGRAM_SKILL_ENV` at a different file to send elsewhere.

To find the chat ID of another group, add the bot to it, post a message, then:

```bash
source ~/.claude/telegram.env
curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getUpdates" | grep -o '"chat":{"id":[-0-9]*,"title":"[^"]*"'
```

## Automatic notifications (do not duplicate)

`stop-notify.sh` in this directory is a **Stop hook**, registered globally in
`~/.claude/settings.json`. It already pings Telegram when a long session ends.
**Never send a "done" message yourself** — the hook does it, and a manual one
means the user gets two.

It notifies only when all of these hold:

- the turn took **5+ minutes of actual work** (override with `CLAUDE_TELEGRAM_NOTIFY_THRESHOLD`, in seconds)
- the session is **not** remote-controlled (detected by walking the process tree to the `claude` process and reading its flags — the user is already on their phone for those)
- it hasn't already notified for that turn (state in `~/.claude/.telegram-notify-state/`)

The message carries a one-sentence summary of what the turn accomplished:

```
✅ add-dark-mode
my-app · done after 12 min
Added auto-generated task summaries to Telegram notifications with async hook execution
```

### How the summary is produced

`summarize.py` extracts the turn (the prompt, the files changed, the assistant's
text and tool calls) and asks a headless `claude -p --model haiku` for one
sentence. It is **not** the last assistant text block verbatim: that is whatever
the reply happened to end on — a caveat, a question, a correction — and runs to
thousands of characters.

Two consequences worth knowing:

- **It runs detached.** A Stop hook is synchronous and the model call takes
  10–20s, so inline it would hang the prompt for that long at the end of every
  long turn. The hook does its cheap checks, claims the turn in the dedupe state,
  spawns the summarize-and-send, and returns in well under a second.
- **It re-enters the hooks.** `claude -p` is a real session: it fires `Stop` and
  `SessionStart` like any other. Both hooks therefore bail immediately when
  `CLAUDE_TELEGRAM_SUMMARIZER=1` is set, which `summarize.py` exports for its
  child. Without that guard the summarizer registers itself in the session
  registry, and a slow enough call would notify on its own.

If the summary fails for any reason it is simply omitted and the last prompt is
shown instead — the notification is worth sending without it, and never worth
delaying. Tune with `CLAUDE_TELEGRAM_SUMMARY_MODEL` (default `haiku`) and
`CLAUDE_TELEGRAM_SUMMARY_TIMEOUT` (default 25s).

Work time is measured from the user's last *presence* — a typed prompt **or** an answer to `AskUserQuestion` — not from their last typed prompt. Their answers arrive as tool results, so the naive clock would count time they spent away deciding as work time and fire falsely.

A turn ending on a pending `AskUserQuestion` sends `❓ … needs your input` instead of `✅ … done`, so a session blocked on a question isn't silently missed.

Debug it with `CLAUDE_TELEGRAM_NOTIFY_LOG=/tmp/tg.log`. It exits 0 on every path — a notifier must never block Claude from stopping.

## Notes

- Sending is outward-facing and not reversible. If the user asked for a notification, send it; otherwise confirm the wording before posting.
- The bot must remain a member of the group. If sends start failing with `chat not found`, check it wasn't removed.
- Session titles come from the transcript: `custom-title` (set by `/rename`) preferred, else the auto-generated `ai-title`. Nothing needs renaming — Claude Code titles every session on its own.
