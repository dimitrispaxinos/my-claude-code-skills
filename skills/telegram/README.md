# telegram

Telegram notifications from Claude Code:

1. **`/telegram <message>`**: Claude posts a message to your Telegram, e.g. "ping me when the deploy finishes".
2. **Automatic "done" pings**: a `Stop` hook messages you when a turn took **5+ minutes**
   of actual work, so you know which window to go back to:

   ```
   ✅ add-dark-mode
   my-app · done after 12 min
   Added a dark theme toggle to settings and fixed contrast on the dashboard cards
   ```

   If the session stopped to ask you something, the first line starts with `❓` and the
   second says `my-app · needs your input after 12 min`.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Message your bot, or add it to a group and post something there. Then get the chat id:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":[-0-9]*'
   ```
3. Run `../../install.sh telegram`. It creates `~/.claude/telegram.env` (mode 600)
   and adds the Stop hook. Fill in the env file:
   ```bash
   TELEGRAM_TOKEN=123456789:AA...
   TELEGRAM_CHAT_ID=-1001234567890
   ```
4. Test it: `~/.claude/skills/telegram/send.sh "hello from Claude"`

The token and chat id live only in `~/.claude/telegram.env`, never in this folder.
To use a different file, set `TELEGRAM_SKILL_ENV=/path/to/file`.

## When it pings

The Stop hook only sends a message when **all** of these are true:

- **The turn took 5+ minutes of work.** The clock runs from your last typed
  prompt *or* your last answer to one of Claude's questions, so time you spent
  away deciding doesn't count as work.
- **The session isn't remote-controlled.** If you're driving it from your phone,
  you're already watching.
- **It hasn't already pinged for this turn.**

The one-sentence summary comes from a headless `claude -p --model haiku` call. It
runs in the background, so Claude Code never waits on it. If summarizing fails,
the message shows your last prompt instead.

## Settings (environment variables)

| Variable | Default | Effect |
| --- | --- | --- |
| `CLAUDE_TELEGRAM_NOTIFY_THRESHOLD` | `300` | Seconds of work before a turn triggers a ping |
| `CLAUDE_TELEGRAM_SUMMARY_MODEL` | `haiku` | Model used for the one-line summary |
| `CLAUDE_TELEGRAM_SUMMARY_TIMEOUT` | `25` | Seconds before giving up on the summary |
| `CLAUDE_TELEGRAM_NOTIFY_LOG` | unset | Path to a debug log of every hook decision |

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | Tells Claude how to send messages, and not to send its own "done" message (the hook does) |
| `send.sh` | Sends a message (argument or stdin). Uses Telegram Markdown, falls back to plain text, enforces the 4096-character limit |
| `stop-notify.sh` | The `Stop` hook |
| `summarize.py` | Writes the one-sentence turn summary |

## Manual hook setup

`install.sh` adds this for you. To add it by hand, put this in `~/.claude/settings.json`:

```json
"hooks": {
  "Stop": [
    { "matcher": "", "hooks": [ { "type": "command", "command": "~/.claude/skills/telegram/stop-notify.sh", "timeout": 30 } ] }
  ]
}
```

Requires `bash`, `curl`, `jq` and `python3`. The hook always exits 0, so a
notifier problem never stops Claude from finishing.
