#!/usr/bin/env bash
# Send a message to your Telegram notifications chat.
# Usage: send.sh "message text"
#        echo "message text" | send.sh
set -euo pipefail

CONFIG="${TELEGRAM_SKILL_ENV:-$HOME/.claude/telegram.env}"

if [[ ! -f "$CONFIG" ]]; then
  echo "error: config not found at $CONFIG" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG"

: "${TELEGRAM_TOKEN:?missing TELEGRAM_TOKEN in $CONFIG}"
: "${TELEGRAM_CHAT_ID:?missing TELEGRAM_CHAT_ID in $CONFIG}"

if [[ $# -gt 0 ]]; then
  TEXT="$*"
else
  TEXT="$(cat)"
fi

if [[ -z "${TEXT//[[:space:]]/}" ]]; then
  echo "error: refusing to send an empty message" >&2
  exit 1
fi

# Telegram caps messages at 4096 characters.
if [[ ${#TEXT} -gt 4096 ]]; then
  echo "error: message is ${#TEXT} chars; Telegram's limit is 4096" >&2
  exit 1
fi

RESPONSE="$(curl -sS --max-time 15 -X POST \
  "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=${TEXT}" \
  -d "parse_mode=Markdown" \
  -d "disable_web_page_preview=true")"

if [[ "$RESPONSE" != *'"ok":true'* ]]; then
  # Markdown parsing is the usual culprit; retry once as plain text.
  RESPONSE="$(curl -sS --max-time 15 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${TEXT}" \
    -d "disable_web_page_preview=true")"
fi

if [[ "$RESPONSE" == *'"ok":true'* ]]; then
  echo "sent"
else
  echo "error: telegram rejected the message" >&2
  echo "$RESPONSE" >&2
  exit 1
fi
