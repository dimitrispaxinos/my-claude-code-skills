#!/usr/bin/env python3
"""One-sentence summary of the turn that just finished.

Called by the Stop hook. Extracts what happened in this turn from the transcript
and asks a small headless model to describe it in one sentence.

The last assistant text block is deliberately NOT used verbatim: it is whatever
the reply happened to end on -- a caveat, a question, a correction -- and runs to
thousands of characters. It describes the tail of a message, not the work.

Prints the sentence on stdout, or nothing at all. Any failure is silent: the
notification is worth sending without a summary, and never worth delaying.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

MODEL = os.environ.get("CLAUDE_TELEGRAM_SUMMARY_MODEL", "haiku")
TIMEOUT = int(os.environ.get("CLAUDE_TELEGRAM_SUMMARY_TIMEOUT", "25"))

# Enough of the turn to characterize it, small enough to stay fast and cheap.
MAX_CONTEXT = 6000
MAX_SENTENCE = 240

PROMPT = """Below is a transcript excerpt of one turn of work that just finished.

Write ONE sentence (max 20 words) describing what was accomplished, for a phone
notification telling the user what their assistant finished while they were away.

Rules:
- State the outcome, not the process. "Added X to Y", not "I looked at files and then".
- If the work failed, was blocked, or is incomplete, SAY SO plainly.
- No preamble, no quotes, no markdown, no trailing period-padding. Just the sentence.

TRANSCRIPT EXCERPT:
"""


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def extract_turn(path: str, since_iso: str) -> str:
    """Everything the assistant did since the user last spoke."""
    since = parse_ts(since_iso)
    if since is None:
        return ""

    prompt = ""
    events: list[str] = []
    files: list[str] = []

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""

    # Walk backwards: the turn is at the tail, and these transcripts reach 20MB+.
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue

        ts = parse_ts(e.get("timestamp"))
        if ts and ts < since:
            break

        t = e.get("type")
        if t == "assistant":
            for block in e.get("message", {}).get("content", []) or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    events.append(block["text"].strip())
                elif block.get("type") == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input") or {}
                    target = inp.get("file_path") or inp.get("path") or ""
                    if target:
                        events.append(f"[{name}: {os.path.basename(target)}]")
                    elif name:
                        events.append(f"[{name}]")
        elif t == "file-history-delta":
            p = e.get("trackingPath")
            if p and p not in files:
                files.append(p)
        elif t == "user" and not e.get("isMeta"):
            content = e.get("message", {}).get("content")
            if isinstance(content, str) and content.strip():
                prompt = content.strip()

    events.reverse()

    parts = []
    if prompt:
        parts.append(f"USER ASKED:\n{prompt[:1200]}")
    if files:
        shown = [os.path.basename(f) for f in files[:12]]
        parts.append(f"FILES CHANGED: {', '.join(shown)}")
    if events:
        parts.append("ASSISTANT DID:\n" + "\n".join(events))

    body = "\n\n".join(parts)
    if len(body) > MAX_CONTEXT:
        # Keep the head (the ask) and the tail (the outcome); the middle is process.
        body = body[: MAX_CONTEXT // 2] + "\n[...]\n" + body[-MAX_CONTEXT // 2 :]
    return body


def summarize(context: str) -> str:
    if not context.strip():
        return ""
    env = dict(os.environ)
    # Marks the child as our own summarizer. Both hooks bail on this, so the
    # summarizer cannot notify, register itself, or summarize recursively.
    env["CLAUDE_TELEGRAM_SUMMARIZER"] = "1"
    try:
        proc = subprocess.run(
            ["claude", "-p", PROMPT + context, "--model", MODEL],
            capture_output=True, text=True, timeout=TIMEOUT,
            env=env, stdin=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    if proc.returncode != 0:
        return ""

    text = " ".join(proc.stdout.split()).strip().strip('"').strip()
    if not text or "\n" in text:
        text = text.split("\n")[0].strip()
    if len(text) > MAX_SENTENCE:
        text = text[:MAX_SENTENCE].rsplit(" ", 1)[0] + "…"
    return text


def main() -> int:
    if os.environ.get("CLAUDE_TELEGRAM_SUMMARIZER"):
        return 0  # never summarize a summarizer
    if len(sys.argv) < 3:
        return 0
    out = summarize(extract_turn(sys.argv[1], sys.argv[2]))
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
