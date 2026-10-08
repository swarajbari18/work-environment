#!/usr/bin/env python3
"""Phase A measurement report for the work environment (spec section 36).

Reads the ActivityWatch window and AFK buckets from the local server and writes
a Markdown report with the metrics the protocol asks for. No baseline is used.

Usage: report.py [--start ISO8601] [--end ISO8601] [--out FILE]
Defaults: start = contents of ~/work-environment-backup/phase-a-start.txt, end = now.
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict

AW_URL = os.environ.get("WENV_AW_URL", "http://127.0.0.1:5600/api/0")
PHASE_START_FILE = os.path.expanduser("~/work-environment-backup/phase-a-start.txt")

# App names as recorded by the awatcher window watcher (process / WM class, lower-cased).
BROWSERS = {"brave-browser", "firefox", "firefox_firefox", "google-chrome", "chromium",
            "chromium-browser", "vivaldi-stable", "opera", "microsoft-edge", "epiphany"}
WORK_APPS = {"cursor", "code", "antigravity", "gnome-terminal-server", "gnome-terminal",
             "org.gnome.textEditor", "org.gnome.texteditor", "libreoffice-writer", "obsidian"}
ENTERTAINMENT_APPS = {"steam", "spotify"}
ENTERTAINMENT_TITLE = re.compile(
    r"youtube|netflix|twitch|crunchyroll|hulu|prime video|disney\+|reddit|instagram|tiktok|facebook|twitter|9gag|\banime\b",
    re.IGNORECASE)

MAX_EVENT_SECONDS = 120      # a single window event never counts for more than this (suspend, lock)
RUN_GAP_SECONDS = 2          # consecutive events of one app closer than this form one run
WORK_SESSION_GAP = 10        # work-app runs closer than this stay in the same work session
AFK_MIN_SECONDS = 60         # AFK stretches shorter than this are not counted as AFK periods


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


def merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def overlap(t0, t1, intervals):
    return sum(max(0.0, min(t1, b) - max(t0, a)) for a, b in intervals)


def window_intervals(events, start, end):
    """Turn window heartbeats into (t0, t1, app, title) intervals clipped to [start, end]."""
    evs = sorted(events, key=lambda e: parse_ts(e["timestamp"]))
    out = []
    for i, e in enumerate(evs):
        t0 = parse_ts(e["timestamp"])
        follow = parse_ts(evs[i + 1]["timestamp"]) if i + 1 < len(evs) else t0 + MAX_EVENT_SECONDS
        t1 = min(follow, t0 + MAX_EVENT_SECONDS)
        t0, t1 = max(t0, start), min(t1, end)
        if t1 > t0:
            data = e.get("data", {})
            out.append((t0, t1, (data.get("app") or "").lower(), data.get("title") or ""))
    return out


def afk_intervals(events, start, end):
    spans = []
    for e in events:
        if e.get("data", {}).get("status") == "afk":
            t0 = parse_ts(e["timestamp"])
            t1 = t0 + float(e.get("duration", 0.0))
            t0, t1 = max(t0, start), min(t1, end)
            if t1 > t0:
                spans.append((t0, t1))
    return merge(spans)


def compute(window_events, afk_events, start, end):
    """Return the Phase A metrics for [start, end] (epoch seconds)."""
    afk = afk_intervals(afk_events, start, end)
    rows = []
    for t0, t1, app, title in window_intervals(window_events, start, end):
        active = max(0.0, (t1 - t0) - overlap(t0, t1, afk))
        rows.append({"t0": t0, "t1": t1, "app": app, "title": title, "active": active})

    # Merge consecutive rows of the same app into runs.
    runs = []
    for r in rows:
        if runs and runs[-1]["app"] == r["app"] and r["t0"] - runs[-1]["end"] <= RUN_GAP_SECONDS:
            runs[-1]["end"] = r["t1"]
            runs[-1]["active"] += r["active"]
        else:
            runs.append({"app": r["app"], "start": r["t0"], "end": r["t1"], "active": r["active"]})

    def is_entertainment(r):
        if r["app"] in ENTERTAINMENT_APPS:
            return True
        return r["app"] in BROWSERS and bool(ENTERTAINMENT_TITLE.search(r["title"]))

    per_app = defaultdict(float)
    for r in rows:
        per_app[r["app"] or "(no focused window)"] += r["active"]

    launches = sum(
        1 for i, run in enumerate(runs)
        if run["app"] in BROWSERS and (i == 0 or runs[i - 1]["app"] not in BROWSERS)
    )
    switches = sum(1 for i in range(1, len(runs)) if runs[i]["app"] != runs[i - 1]["app"])

    # Work sessions: unbroken stretches of work-app focus, with no non-work app,
    # no long gap and no AFK time inside them.
    sessions, current, last_end = [], 0.0, None
    for run in runs:
        duration = run["end"] - run["start"]
        mostly_active = run["active"] >= 0.5 * duration if duration > 0 else False
        is_work = run["app"] in WORK_APPS and mostly_active
        continues = last_end is not None and run["start"] - last_end <= WORK_SESSION_GAP
        if is_work and continues and current > 0:
            current += run["active"]
        else:
            if current > 0:
                sessions.append(current)
            current = run["active"] if is_work else 0.0
        last_end = run["end"]
    if current > 0:
        sessions.append(current)

    afk_periods = [b - a for a, b in afk if b - a >= AFK_MIN_SECONDS]

    return {
        "window": {"start": iso(start), "end": iso(end)},
        "active_seconds": sum(r["active"] for r in rows),
        "browser_seconds": sum(r["active"] for r in rows if r["app"] in BROWSERS),
        "entertainment_seconds": sum(r["active"] for r in rows if is_entertainment(r)),
        "work_app_seconds": sum(r["active"] for r in rows if r["app"] in WORK_APPS),
        "browser_launches": launches,
        "task_switches": switches,
        "work_sessions_seconds": sessions,
        "afk_periods_count": len(afk_periods),
        "afk_seconds_total": sum(b - a for a, b in afk),
        "top_apps": sorted(per_app.items(), key=lambda kv: -kv[1])[:8],
        "events_used": len(rows),
    }


def fetch_events(bucket, start, end):
    url = f"{AW_URL}/buckets/{urllib.parse.quote(bucket)}/events?start={urllib.parse.quote(iso(start))}&end={urllib.parse.quote(iso(end))}&limit=-1"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.load(resp)


def bucket_names():
    with urllib.request.urlopen(f"{AW_URL}/buckets/", timeout=10) as resp:
        names = list(json.load(resp).keys())
    return ([n for n in names if n.startswith("aw-watcher-window_")],
            [n for n in names if n.startswith("aw-watcher-afk_")])


def hm(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def render(m, title):
    lines = [f"## {title}", ""]
    lines.append(f"Window: {m['window']['start']} to {m['window']['end']} (UTC)  ")
    lines.append(f"Window events used: {m['events_used']}")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Active time (not AFK) | {hm(m['active_seconds'])} |")
    lines.append(f"| Browser time | {hm(m['browser_seconds'])} |")
    lines.append(f"| Entertainment time | {hm(m['entertainment_seconds'])} |")
    lines.append(f"| Work-app time | {hm(m['work_app_seconds'])} |")
    lines.append(f"| Browser launches | {m['browser_launches']} |")
    lines.append(f"| Task switches | {m['task_switches']} |")
    sessions = m["work_sessions_seconds"]
    if sessions:
        lines.append(f"| Work sessions | {len(sessions)} (median {hm(statistics.median(sessions))}, longest {hm(max(sessions))}) |")
    else:
        lines.append("| Work sessions | 0 |")
    lines.append(f"| AFK periods (>= {AFK_MIN_SECONDS}s) | {m['afk_periods_count']} ({hm(m['afk_seconds_total'])} AFK in total) |")
    lines.append("")
    lines.append("Top apps by active time (use this to refine the app lists in report.py):")
    lines.append("")
    for app, secs in m["top_apps"]:
        lines.append(f"- `{app}`: {hm(secs)}")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--out")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if args.start:
        start = parse_ts(args.start)
    elif os.path.exists(PHASE_START_FILE):
        start = parse_ts(open(PHASE_START_FILE).read().strip())
    else:
        start = (now - dt.timedelta(days=1)).timestamp()
    end = parse_ts(args.end) if args.end else now.timestamp()

    window_buckets, afk_buckets = bucket_names()
    window_events, afk_events = [], []
    for b in window_buckets:
        window_events += fetch_events(b, start, end)
    for b in afk_buckets:
        afk_events += fetch_events(b, start, end)

    m = compute(window_events, afk_events, start, end)
    phase_start = dt.datetime.fromtimestamp(start, dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    text = f"# Phase A report\n\nPhase A started: {phase_start}. No baseline (decided 2026-10-08).\n\n"
    text += render(m, "Since Phase A start")
    body = json.dumps(m, indent=2, default=str)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            f.write(text)
            f.write("\n<!-- metrics: " + body.replace("--", "- -") + " -->\n")
    print(text)


if __name__ == "__main__":
    main()
