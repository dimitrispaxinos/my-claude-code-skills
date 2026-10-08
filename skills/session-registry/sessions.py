#!/usr/bin/env python3
"""Fleet view of Claude Code sessions on this machine.

Nothing here is a source of truth: the registry (written by the SessionStart
hook) supplies only the pid <-> session_id link, and everything else is derived
live from the process table and the session transcripts. A derived view cannot
go stale, and a session that dies cannot leave a lie behind.

Usage:
    sessions.py                 live sessions, collisions, recently ended
    sessions.py --json          same data, machine-readable
    sessions.py --all           include every transcript, however old
    sessions.py --dead-days N   how far back "recently ended" reaches (default 3)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
REGISTRY_DIR = HOME / ".claude" / "session-registry"
# Claude Code's own per-pid session records. Authoritative where present.
BUILTIN_DIR = HOME / ".claude" / "sessions"
PROJECTS_DIR = HOME / ".claude" / "projects"

# Only the tail of each transcript is parsed. They reach 22MB+ and the fields we
# want (title, last prompt, recent edits) are all appended, so the tail holds
# them. Full-file scans are reserved for the rare small file missing a title.
TAIL_BYTES = 512 * 1024
FULL_SCAN_MAX = 4 * 1024 * 1024

# A collision only matters between sessions editing the same file around the
# same time; overlaps from days ago are history, not risk.
COLLISION_WINDOW_HOURS = 24

IDLE_STALE_DAYS = 3

# A transcript written this recently belongs to a running session, not an ended
# one -- used only where the pid link is missing and liveness must be inferred.
AMBIGUOUS_SECONDS = 600


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def humanize(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h, m = divmod(seconds // 60, 60)
        return f"{h}h{m:02d}m" if m else f"{h}h"
    d, h = divmod(seconds // 3600, 24)
    return f"{d}d{h}h" if h else f"{d}d"


# --------------------------------------------------------------------------
# Process table
# --------------------------------------------------------------------------

def live_claude_processes() -> dict[int, dict]:
    """Every running claude process, keyed by pid.

    Matched on argv[0] only: a shell's argv contains its whole command text, so
    matching anywhere in the line would pick up every `grep claude` and editor.
    """
    out = run(["ps", "-axo", "pid=,ppid=,lstart=,args="])
    procs: dict[int, dict] = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+[\d:]+\s+\d{4})\s+(.*)$", line)
        if not m:
            continue
        pid, ppid, lstart, args = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        exe = os.path.basename(args.split(" ")[0])
        if exe != "claude":
            continue
        procs[pid] = {
            "pid": pid,
            "ppid": ppid,
            "args": args,
            "started_at": lstart,
            "remote": " --remote-control " in f" {args} ",
            "name": remote_name(args),
            "resumed_sid": resumed_session_id(args),
            "cwd": proc_cwd(pid),
        }
    return procs


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=20
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def resumed_session_id(args: str) -> str | None:
    """Session id from a `--resume <uuid>` flag.

    Free and authoritative, and the only link available for a resumed remote
    session: those get no built-in record, so without this a `--resume` of a
    still-running session stays invisible -- exactly the case worth catching.
    """
    m = re.search(r"--resume[= ]([0-9a-fA-F-]{36})", args)
    return m.group(1) if m else None


def remote_name(args: str) -> str | None:
    m = re.search(r"--remote-control\s+(?!--)(.+?)(?:\s+--|\s*$)", args)
    return m.group(1).strip() if m else None


def proc_cwd(pid: int) -> str | None:
    out = run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def remote_log_path(proc: dict) -> str | None:
    """The PTY log this remote session's `script` parent is writing to."""
    if not proc.get("remote"):
        return None
    out = run(["lsof", "-p", str(proc["ppid"]), "-Fn"])
    for line in out.splitlines():
        # -Fn emits one path per line, so filenames containing spaces survive.
        if line.startswith("n") and re.search(r"claude-remote-.*\.log$", line[1:]):
            return line[1:]
    return None


def remote_url(proc: dict) -> str | None:
    """Resolve a remote session's claude.ai URL.

    The URL is only ever printed to the launcher's log, so we ask lsof which log
    this session's `script` parent is writing to. Matching on the log's filename
    instead would be ambiguous -- several sessions share the name
    "my-app" and would resolve to each other's URLs.
    """
    log = remote_log_path(proc)
    if not log:
        return None
    try:
        with open(log, "rb") as fh:
            blob = fh.read(200_000).decode("utf-8", "replace")
        m = re.search(r"https://claude\.ai/code/session_[A-Za-z0-9]+", blob)
        return m.group(0) if m else None
    except OSError:
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------

def read_tail(path: Path, size: int = TAIL_BYTES) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            total = fh.tell()
            fh.seek(max(0, total - size))
            data = fh.read()
        text = data.decode("utf-8", "replace")
        lines = text.split("\n")
        if total > size and lines:
            lines = lines[1:]  # first line is almost certainly truncated
        return lines
    except OSError:
        return []


def scan_transcript(path: Path, want_title: bool = True) -> dict:
    """Pull the human-meaningful facts out of a transcript's tail."""
    info: dict = {
        "title": None, "ai_title": None, "last_prompt": None,
        "last_activity": None, "edits": [], "cwd": None,
    }
    lines = read_tail(path)
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t == "custom-title":
            info["title"] = e.get("customTitle") or info["title"]
        elif t == "ai-title":
            info["ai_title"] = e.get("aiTitle") or info["ai_title"]
        elif t == "last-prompt":
            info["last_prompt"] = e.get("lastPrompt") or info["last_prompt"]
        elif t == "file-history-delta":
            p, ts = e.get("trackingPath"), parse_ts(e.get("timestamp"))
            if p and ts:
                info["edits"].append((p, ts))
        if e.get("cwd"):
            info["cwd"] = e["cwd"]
        ts = parse_ts(e.get("timestamp"))
        if ts and (info["last_activity"] is None or ts > info["last_activity"]):
            info["last_activity"] = ts

    # A session whose current turn is enormous can push the title out of the
    # tail. Worth one full pass, but only for files small enough to be cheap.
    if want_title and not (info["title"] or info["ai_title"]):
        try:
            if path.stat().st_size <= FULL_SCAN_MAX:
                for line in path.read_text("utf-8", "replace").split("\n"):
                    if '"custom-title"' in line or '"ai-title"' in line:
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if e.get("type") == "custom-title":
                            info["title"] = e.get("customTitle") or info["title"]
                        elif e.get("type") == "ai-title":
                            info["ai_title"] = e.get("aiTitle") or info["ai_title"]
        except OSError:
            pass

    if info["last_activity"] is None:
        try:
            info["last_activity"] = datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            )
        except OSError:
            pass
    return info


def all_transcripts() -> dict[str, Path]:
    found: dict[str, Path] = {}
    if not PROJECTS_DIR.is_dir():
        return found
    for p in PROJECTS_DIR.glob("*/*.jsonl"):
        found[p.stem] = p
    return found


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def load_builtin() -> dict[int, dict]:
    """Claude Code's own session records, keyed by pid.

    `~/.claude/sessions/<pid>.json` is written and kept current by claude itself,
    and is authoritative where it exists: it carries the pid -> sessionId link
    plus a live busy/idle status. It does not cover remote-controlled sessions,
    which is the gap register.sh fills.
    """
    entries: dict[int, dict] = {}
    if not BUILTIN_DIR.is_dir():
        return entries
    for f in BUILTIN_DIR.glob("*.json"):
        try:
            e = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        pid = e.get("pid")
        if isinstance(pid, int):
            entries[pid] = e
    return entries


def load_registry() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    if not REGISTRY_DIR.is_dir():
        return entries
    for f in REGISTRY_DIR.glob("*.json"):
        try:
            e = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if e.get("session_id"):
            entries[e["session_id"]] = e
    return entries


def remote_log_mtime(proc: dict) -> datetime | None:
    """Last activity of a remote session, from its PTY log.

    The `script` wrapper streams the session's terminal output to a log, so the
    log stops growing exactly when the session goes quiet. This is the only
    activity signal for a remote session whose transcript we cannot identify.
    """
    log = remote_log_path(proc)
    if not log:
        return None
    try:
        return datetime.fromtimestamp(os.path.getmtime(log), timezone.utc)
    except OSError:
        return None


def build(dead_days: int, include_all: bool) -> dict:
    registry = load_registry()
    builtin = load_builtin()
    procs = live_claude_processes()
    transcripts = all_transcripts()

    mine_by_pid = {
        e["pid"]: e for e in registry.values() if isinstance(e.get("pid"), int)
    }

    sessions: list[dict] = []

    # Live sessions are enumerated from the process table, not from any file:
    # a process is the only proof a session is running.
    for pid, proc in procs.items():
        b = builtin.get(pid) or {}
        m = mine_by_pid.get(pid) or {}
        sid = b.get("sessionId") or m.get("session_id") or proc.get("resumed_sid")

        info = {"title": None, "ai_title": None, "last_prompt": None,
                "last_activity": None, "edits": [], "cwd": None}
        tpath = transcripts.get(sid) if sid else None
        if not tpath and m.get("transcript_path"):
            p = Path(m["transcript_path"])
            tpath = p if p.exists() else None
        if tpath:
            info = scan_transcript(tpath)

        # Prefer claude's own status clock; fall back to the transcript, then to
        # the remote log. A null statusUpdatedAt means unknown -- treating it as
        # epoch 0 would report the session as idle for twenty thousand days.
        last_activity = info["last_activity"]
        status = b.get("status")
        upd = b.get("statusUpdatedAt")
        if isinstance(upd, (int, float)) and upd > 0:
            ts = datetime.fromtimestamp(upd / 1000, timezone.utc)
            if last_activity is None or ts > last_activity:
                last_activity = ts
        if last_activity is None and proc.get("remote"):
            last_activity = remote_log_mtime(proc)

        sessions.append({
            "session_id": sid,
            "pid": pid,
            "alive": True,
            "registered": bool(sid),
            "source": "builtin" if b else ("hook" if m else None),
            "status": status,
            "cwd": proc.get("cwd") or b.get("cwd") or m.get("cwd"),
            "name": proc.get("name") or b.get("name") or m.get("name"),
            "remote": proc.get("remote", False),
            "url": remote_url(proc),
            "title": info["title"] or info["ai_title"] or b.get("name"),
            "titled_by": "you" if info["title"] else ("auto" if info["ai_title"] else None),
            "last_prompt": info["last_prompt"],
            "last_activity": last_activity,
            "edits": info["edits"],
            "started_at": b.get("procStart") or m.get("started_at") or proc.get("started_at"),
        })

    cutoff = now().timestamp() - dead_days * 86400
    live = [s for s in sessions if s["alive"]]
    live_ids = {s["session_id"] for s in live if s["session_id"]}

    # Ended sessions are read straight from the transcripts, not the registry.
    # A dead session needs no pid link -- the transcript already holds its id,
    # title, cwd and last activity -- and sourcing them from the registry would
    # limit this list to sessions started after the hook was installed, i.e.
    # nothing worth resuming today.
    # An unregistered live session cannot be tied to its transcript, so that
    # transcript is indistinguishable from an ended one. Anything still being
    # written in a directory where such a session is running is therefore
    # withheld rather than offered for `--resume`: resuming a session that is
    # in fact alive puts two processes on one transcript.
    unregistered_cwds = {s["cwd"] for s in live if not s["registered"] and s["cwd"]}

    dead: list[dict] = []
    withheld = 0
    for sid, path in transcripts.items():
        if sid in live_ids:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if not include_all and mtime < cutoff:
            continue  # cheap mtime gate first: parsing 152 transcripts is not free
        info = scan_transcript(path)
        if not info["last_activity"]:
            continue
        if not include_all and info["last_activity"].timestamp() < cutoff:
            continue
        age = now().timestamp() - info["last_activity"].timestamp()
        if age < AMBIGUOUS_SECONDS and info["cwd"] in unregistered_cwds:
            withheld += 1
            continue
        entry = registry.get(sid, {})
        dead.append({
            "session_id": sid,
            "pid": None, "alive": False, "registered": sid in registry,
            "cwd": info["cwd"] or entry.get("cwd"),
            "name": entry.get("name"),
            "remote": entry.get("remote", False),
            "url": None,
            "title": info["title"] or info["ai_title"],
            "titled_by": "you" if info["title"] else ("auto" if info["ai_title"] else None),
            "last_prompt": info["last_prompt"],
            "last_activity": info["last_activity"],
            "edits": info["edits"],
            "started_at": entry.get("started_at"),
        })
    live.sort(key=lambda s: s["last_activity"].timestamp() if s["last_activity"] else 0, reverse=True)
    dead.sort(key=lambda s: s["last_activity"].timestamp() if s["last_activity"] else 0, reverse=True)
    return {
        "live": live,
        "dead": dead,
        "collisions": collisions(live),
        "shared_sessions": shared_sessions(live),
        "unregistered_live": sum(1 for s in live if not s["registered"]),
        "withheld": withheld,
    }


def shared_sessions(live: list[dict]) -> list[dict]:
    """Live processes writing to the same transcript.

    Usually a `--resume` of a session that was never actually dead. Both
    processes append to one transcript and neither knows about the other, so
    this outranks a file collision: the conversation itself is being interleaved.
    """
    by_sid: dict[str, list[dict]] = {}
    for s in live:
        if s["session_id"]:
            by_sid.setdefault(s["session_id"], []).append(s)
    out = []
    for sid, group in by_sid.items():
        if len(group) > 1:
            out.append({
                "session_id": sid,
                "title": next((g["title"] for g in group if g["title"]), None),
                "pids": [g["pid"] for g in group],
                "cwd": next((g["cwd"] for g in group if g["cwd"]), None),
            })
    return out


def collisions(live: list[dict]) -> list[dict]:
    """Live sessions that recently edited the same file.

    Same-repo overlap is not a collision here: 16 of these sessions share one
    directory, so flagging that would fire constantly and teach you to ignore
    it. Same file, recently, between two live sessions is the case that
    actually loses work.
    """
    horizon = now().timestamp() - COLLISION_WINDOW_HOURS * 3600
    by_path: dict[str, dict[str, datetime]] = {}
    for s in live:
        label = s["title"] or s["name"] or (f"pid {s['pid']}")
        for path, ts in s["edits"]:
            if ts.timestamp() < horizon:
                continue
            slot = by_path.setdefault(path, {})
            if label not in slot or ts > slot[label]:
                slot[label] = ts
    out = []
    for path, editors in by_path.items():
        if len(editors) > 1:
            out.append({
                "path": path,
                "editors": sorted(
                    ({"session": k, "at": v} for k, v in editors.items()),
                    key=lambda e: e["at"], reverse=True,
                ),
            })
    out.sort(key=lambda c: c["editors"][0]["at"], reverse=True)
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
RED, YELLOW, GREEN, CYAN = "\033[31m", "\033[33m", "\033[32m", "\033[36m"


def c(text: str, *codes: str) -> str:
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + RESET


def render(data: dict, dead_days: int) -> str:
    live, dead, colls = data["live"], data["dead"], data["collisions"]
    lines: list[str] = []
    stale = sum(
        1 for s in live
        if s["last_activity"] and (now() - s["last_activity"]).total_seconds() > IDLE_STALE_DAYS * 86400
    )
    header = f"{len(live)} live"
    if stale:
        header += f" · {stale} idle >{IDLE_STALE_DAYS}d"
    if colls:
        header += f" · {len(colls)} collision" + ("s" if len(colls) > 1 else "")
    if data.get("shared_sessions"):
        n = len(data["shared_sessions"])
        header += c(f" · {n} shared session" + ("s" if n > 1 else "") + "!", RED)
    lines.append(c("CLAUDE SESSIONS", BOLD) + c(f"   {header}", DIM))
    lines.append("")

    if not live:
        lines.append(c("  no live sessions", DIM))

    for s in live:
        idle = (now() - s["last_activity"]).total_seconds() if s["last_activity"] else None
        if s.get("status") == "busy":
            dot = c("●", GREEN)
        elif idle is not None and idle < 900:
            dot = c("●", CYAN)
        else:
            dot = c("○", DIM)
        title = s["title"] or s["name"] or c("(unregistered)", DIM)
        if s["titled_by"] == "auto":
            title = c(title, DIM) if False else title
        repo = os.path.basename(s["cwd"]) if s["cwd"] else "?"
        when = f"{humanize(idle)} ago" if idle is not None else "—"
        lines.append(f"{dot} {c(title, BOLD)}  {c(repo, CYAN)}  {c(when, DIM)}")

        kind = "remote" if s["remote"] else "local"
        meta = [kind, f"pid {s['pid']}"]
        if s.get("status"):
            meta.append(s["status"])
        if s["name"] and s["title"] and s["name"] != s["title"]:
            meta.append(f'launched as "{s["name"]}"')
        if not s["registered"]:
            meta.append(c("unidentified — restart to link it to its transcript", YELLOW))
        lines.append(c("    " + " · ".join(meta), DIM))
        if s["url"]:
            lines.append(f"    {c(s['url'], DIM)}")
        if s["last_prompt"]:
            p = " ".join(s["last_prompt"].split())
            if len(p) > 78:
                p = p[:78] + "…"
            lines.append(c(f'    ↳ "{p}"', DIM))
        lines.append("")

    for sh in data.get("shared_sessions", []):
        lines.append(c("⚠  SAME SESSION, MULTIPLE PROCESSES", BOLD, RED))
        lines.append(f"   {c(sh['title'] or sh['session_id'], YELLOW)}")
        lines.append(c(
            f"     pids {', '.join(str(p) for p in sh['pids'])} are all writing to one transcript.\n"
            f"     Almost certainly a --resume of a session that was still running.\n"
            f"     Keep the one you're using and quit the others; their turns are interleaving.", DIM))
        lines.append("")

    if colls:
        lines.append(c("⚠  COLLISIONS", BOLD, RED) + c("   same file, both sessions live, last 24h", DIM))
        for col in colls:
            try:
                rel = os.path.relpath(col["path"], os.getcwd())
                shown = rel if not rel.startswith("../../") else col["path"]
            except ValueError:
                shown = col["path"]
            lines.append(f"   {c(shown, YELLOW)}")
            for e in col["editors"]:
                ago = humanize((now() - e["at"]).total_seconds())
                lines.append(c(f"     {e['session']}  ({ago} ago)", DIM))
        lines.append("")

    if dead:
        lines.append(c("RECENTLY ENDED", BOLD) + c(f"   last {dead_days}d", DIM))
        if data.get("withheld"):
            lines.append(c(
                f"   {data['withheld']} recently-active session(s) withheld: they may belong to the\n"
                f"   unregistered sessions above, and resuming a live session is unsafe.", YELLOW))
        for s in dead[:12]:
            idle = humanize((now() - s["last_activity"]).total_seconds()) if s["last_activity"] else "?"
            title = s["title"] or "(untitled)"
            repo = os.path.basename(s["cwd"]) if s["cwd"] else "?"
            lines.append(f"  {title}  {c(repo, CYAN)}  {c(idle + ' ago', DIM)}")
            lines.append(c(f"     claude --resume {s['session_id']}", DIM))
        lines.append("")

    stale_live = [
        s for s in live
        if s["last_activity"] and (now() - s["last_activity"]).total_seconds() > IDLE_STALE_DAYS * 86400 and s["pid"]
    ]
    if stale_live:
        lines.append(c("CLEANUP", BOLD) + c(f"   live but untouched for >{IDLE_STALE_DAYS}d", DIM))
        for s in stale_live:
            title = s["title"] or s["name"] or f"pid {s['pid']}"
            idle = humanize((now() - s["last_activity"]).total_seconds())
            lines.append(f"  {c(str(s['pid']), YELLOW)}  {title}  {c(idle + ' idle', DIM)}")
        pids = " ".join(str(s["pid"]) for s in stale_live)
        lines.append(c(f"  review, then: kill {pids}", DIM))
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fleet view of Claude Code sessions.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--all", action="store_true", help="include every transcript, however old")
    ap.add_argument("--dead-days", type=int, default=3, help="how far back 'recently ended' reaches")
    args = ap.parse_args()

    data = build(args.dead_days, args.all)

    if args.json:
        def enc(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, tuple):
                return list(o)
            raise TypeError(type(o))
        print(json.dumps(data, default=enc, indent=2))
        return 0

    print(render(data, args.dead_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
